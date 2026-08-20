"""Provisioning a node from the admin panel.

Does what provisioning/provision_node.py does from a terminal, but driven
over an SSH session the head opens itself, so an operator can add a node by
filling in a form instead of having shell access to the head.

The credential lifecycle is the part worth reading. An operator types the
password their hosting provider gave them; that password is used exactly
twice — to install the head's generated key, and to run the bootstrap. It
is then rotated to a value nobody has ever seen, and every later connection
uses the key. So the password an operator typed stops being valid the
moment provisioning succeeds, and the one that replaces it exists only as a
break-glass path.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.node import Inbound, InboundState, Node, NodeStatus
from app.services import crypto, ssh_manager, tiers
from app.services.ssh_manager import SshError

logger = logging.getLogger(__name__)

# Source checkout layout: <repo>/head/app/services/ -> <repo>/provisioning/
DEFAULT_BOOTSTRAP_SCRIPT = Path(__file__).resolve().parents[3] / "provisioning" / "bootstrap_node.sh"
REMOTE_CERT_PATH = "/root/freeskyvpn_head_client_cert.pem"
#: Куда bootstrap_node.sh кладёт сертификаты ноды — должно совпадать с ним.
MARZBAN_NODE_DIR = "/var/lib/marzban-node"


@dataclass
class ProvisionResult:
    node_id: str
    log: list[str]


class ProvisioningError(RuntimeError):
    pass


def bootstrap_script_path() -> Path:
    configured = get_settings().bootstrap_script_path
    return Path(configured) if configured else DEFAULT_BOOTSTRAP_SCRIPT


def _bootstrap_source() -> str:
    path = bootstrap_script_path()
    if not path.is_file():
        raise ProvisioningError(
            f"bootstrap script not found at {path}; set BOOTSTRAP_SCRIPT_PATH"
        )
    return path.read_text()


def provision_node(
    db: Session,
    *,
    host: str,
    country: str,
    ssh_user: str,
    ssh_password: str,
    ssh_port: int = 22,
    uplink_mbit: int = 100,
    capacity: int = 200,
    control_port: int = 62050,
    control_sni: str = "www.microsoft.com",
    control_reality_port: int = 8443,
) -> ProvisionResult:
    settings = get_settings()
    if not crypto.is_configured():
        raise ProvisioningError(
            "SECRETS_KEY is not set — refusing to store node credentials unencrypted"
        )

    cert_path = Path(settings.head_client_cert_path)
    if not cert_path.is_file():
        raise ProvisioningError(
            f"head client certificate missing at {cert_path}; "
            "run provisioning/generate_head_client_cert.sh first"
        )

    log: list[str] = []

    # Повторная попытка для того же адреса продолжает прежнюю запись, а не
    # заводит вторую. Провижининг рассчитан на перезапуск после неудачи —
    # так и написано в документации, — но каждая попытка создавала новую
    # строку: флот тихо наполнялся полупровизиненными дублями, каждый со
    # своим ключом, и все они считались нодами.
    node = db.scalar(select(Node).where(Node.host == host))
    if node is None:
        node = Node(host=host)
        db.add(node)
        log.append("новая нода")
    else:
        log.append(f"нода {host} уже была заведена, продолжаю её")

    node.country = country
    node.ssh_user = ssh_user
    node.ssh_port = ssh_port
    node.control_port = control_port
    node.uplink_mbit = uplink_mbit
    node.capacity = capacity
    # Not yet usable: it has no control-channel inbound and no pinned
    # certificate, so the selector must not hand users to it.
    node.status = NodeStatus.draining
    db.flush()

    try:
        log.append(ssh_manager.check_connectivity(node, password=ssh_password))
        log.append("SSH reachable")

        node.ssh_private_key_enc = ssh_manager.install_key(node, password=ssh_password)
        db.flush()
        log.append("head SSH key installed")

        # From here on the key works, so the operator's password is no longer
        # needed and is not passed again.
        with ssh_manager.connect(node) as client:
            # Asked before bootstrap, while nothing of ours is running yet:
            # afterwards marzban-node holds the control port and the answer
            # would include our own listeners.
            occupied = ssh_manager.listening_ports(client)
            node.occupied_ports = sorted(occupied)
            db.flush()

            clashing = sorted(occupied & set(tiers.all_ports()))
            if clashing:
                log.append(
                    "ports already in use on the node, they will not be handed out: "
                    + ", ".join(str(port) for port in clashing)
                )

            control_port = _free_port(control_port, occupied, CONTROL_PORT_RANGE)
            if control_port != node.control_port:
                log.append(f"control port {node.control_port} was taken, using {control_port}")
                node.control_port = control_port
                db.flush()

            control_reality_port = _free_port(
                control_reality_port, occupied, CONTROL_REALITY_FALLBACKS
            )

            sftp = client.open_sftp()
            try:
                sftp.put(str(cert_path), REMOTE_CERT_PATH)
            finally:
                sftp.close()
            log.append("head client certificate uploaded")

            # The tier port sets come from the head so the tc filters on the
            # node and the ports the head hands out cannot drift apart.
            args = tiers.bootstrap_arguments()
            command = (
                f"bash -s -- {control_port} {control_sni} {control_reality_port} "
                f"{uplink_mbit} {args['paid_ports']} {args['free_ports']} "
                f"{args['paid_range']} {args['free_range']}"
            )
            result = ssh_manager.run(client, command, stdin_data=_bootstrap_source())
            if result.exit_status != 0:
                raise ProvisioningError(
                    f"bootstrap failed (exit {result.exit_status}): "
                    + _meaningful_tail(result.stderr)
                )
            log.append("bootstrap completed")

        payload = _parse_bootstrap_output(result.stdout)
        _apply_bootstrap_payload(db, node, payload)
        log.append(f"registered control-channel inbound on port {payload['control_inbound']['port']}")
        log.append(f"traffic priority applied on a {uplink_mbit} Mbit/s link")

        node.ssh_password_enc = ssh_manager.rotate_password(node)
        node.ssh_password_rotated_at = datetime.now(UTC)
        log.append("SSH password rotated; the password you entered is no longer valid")

        node.status = NodeStatus.active
        db.flush()
        return ProvisionResult(node_id=str(node.id), log=log)

    except (SshError, ProvisioningError, KeyError, ValueError) as exc:
        # The node row is kept in `draining` rather than deleted: it holds the
        # key we may already have installed, and losing that would leave the
        # node with our access and us without a record of it.
        node.status = NodeStatus.draining
        db.flush()
        logger.exception("provisioning failed for %s", host)
        raise ProvisioningError(f"{exc}\n\n" + "\n".join(log)) from exc



#: Куда уходить, если marzban-node не может занять свой порт по умолчанию.
CONTROL_PORT_RANGE = tuple(range(62050, 62100))

#: То же для Reality-инбаунда канала управления. Обычные HTTPS-порты — он
#: должен выглядеть как ещё один клиентский, а не как канал управления.
CONTROL_REALITY_FALLBACKS = (8443, 2096, 2087, 2083, 2053, 443)



#: Строка полосы прогресса curl: только числа, проценты, размеры и время.
_PROGRESS_LINE = re.compile(r"^[\d\s.:%kKMGmhs+-]*$")


def _meaningful_tail(output: str, lines: int = 12) -> str:
    """Последние осмысленные строки вывода bootstrap.

    Установщик Xray качает архив с полосой прогресса, и она пишет сотни
    строк из одних цифр. Обрезание по символам оставляло от ошибки ровно
    их: настоящее «unzip: command not found» уходило за границу, а на
    экран попадали проценты и скорости.

    Полоса выбрасывается целиком, потому что она никогда ничего не
    объясняет; если после фильтра не осталось ничего, возвращается хвост
    как есть — пустое сообщение хуже неудобного.
    """
    kept = [
        line.rstrip()
        for line in output.splitlines()
        if line.strip() and not _PROGRESS_LINE.match(line.strip())
    ]
    if not kept:
        return output.strip()[-600:]
    return "\n".join(kept[-lines:])


def _free_port(preferred: int, occupied: set[int], candidates) -> int:
    """The preferred port if nothing holds it, otherwise the first that is free.

    Falls back to the preferred port when every candidate is taken: the
    bootstrap then fails on a bind with a message naming the port, which is
    a clearer outcome than provisioning a node onto a port chosen by
    desperation.
    """
    if preferred not in occupied:
        return preferred
    for candidate in candidates:
        if candidate not in occupied:
            return candidate
    return preferred


def _parse_bootstrap_output(stdout: str) -> dict:
    lines = [line for line in stdout.strip().splitlines() if line.strip()]
    if not lines:
        raise ProvisioningError("bootstrap produced no output")
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ProvisioningError(f"could not parse bootstrap output: {lines[-1][:300]}") from exc


def _apply_bootstrap_payload(db: Session, node: Node, payload: dict) -> None:
    node.tls_cert_pem = base64.b64decode(payload["tls_cert_b64"]).decode()
    # The node reports the address it actually reaches the internet from,
    # which can differ from what the operator typed (NAT, a hostname).
    node.host = payload.get("host") or node.host

    inbound = payload["control_inbound"]
    db.add(
        Inbound(
            node_id=node.id,
            port=inbound["port"],
            sni=inbound["sni"],
            transport="reality-vision",
            reality_private_key=inbound["reality_private_key"],
            reality_public_key=inbound["reality_public_key"],
            reality_short_id=inbound["reality_short_id"],
            is_control_channel=True,
            control_client_uuid=inbound["control_client_uuid"],
        )
    )
    db.flush()


def diagnose_node(db: Session, node: Node) -> list[str]:
    """Посмотреть на ноду глазами головы и сказать, что с ней не так.

    Управляющий канал отвечает «Connection refused» — это значит, что пакет
    до ноды дошёл, а на порту никто не слушает. Дальше вариантов немного, и
    все они видны с самой ноды: контейнер не запущен, запущен и падает, или
    слушает не тот порт. Через SSH голова туда попадает — ключ у неё уже
    есть, — так что спрашивать человека «посмотрите на ноде» незачем.
    """
    report: list[str] = []
    with ssh_manager.connect(node) as client:
        status = ssh_manager.run(client, "docker inspect -f '{{.State.Status}}' marzban-node")
        state = status.stdout.strip()
        if status.exit_status != 0 or not state:
            report.append("контейнера marzban-node на ноде нет")
            images = ssh_manager.run(client, "docker images -q gozargah/marzban-node")
            if not images.stdout.strip():
                report.append("образ тоже не скачан — bootstrap не дошёл до запуска")
            report.append("Поднять заново: /usr/local/sbin/freeskyvpn-start-node.sh")
        else:
            report.append(f"контейнер marzban-node: {state}")
            if state != "running":
                exit_code = ssh_manager.run(
                    client, "docker inspect -f '{{.State.ExitCode}}' marzban-node"
                )
                report.append(f"код выхода: {exit_code.stdout.strip()}")

        listening = ssh_manager.listening_ports(client)
        if node.control_port in listening:
            report.append(f"управляющий порт {node.control_port} слушается")
        else:
            report.append(f"управляющий порт {node.control_port} НЕ слушается")

        certs = ssh_manager.run(client, f"ls -1 {MARZBAN_NODE_DIR} 2>/dev/null")
        present = set(certs.stdout.split())
        missing = {"ssl_cert.pem", "ssl_key.pem", "ssl_client_cert.pem"} - present
        if missing:
            report.append("не хватает файлов сертификатов: " + ", ".join(sorted(missing)))

        logs = ssh_manager.run(client, "docker logs --tail 20 marzban-node 2>&1")
        tail = [line for line in logs.stdout.splitlines() if line.strip()]
        if tail:
            report.append("последнее из лога контейнера:")
            report.extend(f"    {line}" for line in tail[-12:])

    report.append("")
    report.append(f"канал управления сейчас: {node.channel_state.value}")
    was = node.channel_state
    try:
        from app.node_manager.channel import call_node
        from app.services.certs import bundle_for

        call_node(db, node, bundle_for(node), lambda client: client.status())
    except Exception as exc:  # noqa: BLE001 - показываем любую причину как есть
        report.append(f"голова НЕ достучалась: {type(exc).__name__}: {exc}")
    else:
        report.append("голова достучалась — нода отвечает")
    if node.channel_state != was:
        report.append(f"состояние канала: {was.value} → {node.channel_state.value}")

    return report


def rescan_ports(db: Session, node: Node) -> tuple[list[int], list[int]]:
    """Ask an already-provisioned node what is holding its ports now.

    Provisioning reads this once, before anything of ours is running. By
    then the answer is clean; afterwards it is not, because our own Xray is
    listening on every inbound we handed out. So this subtracts what we know
    is ours before storing — otherwise a rescan would slowly convince the
    head that its own ports are unavailable and push every new inbound onto
    the fallback range.

    Returns (что занято чужим, что из наших предпочтительных портов задето).
    """
    with ssh_manager.connect(node) as client:
        seen = ssh_manager.listening_ports(client)

    ours = {
        port
        for (port,) in db.execute(
            select(Inbound.port).where(
                Inbound.node_id == node.id, Inbound.state != InboundState.dead
            )
        ).all()
    }
    ours.add(node.control_port)

    foreign = sorted(seen - ours)
    node.occupied_ports = foreign
    db.flush()
    return foreign, sorted(set(foreign) & set(tiers.all_ports()))


def rotate_node_password(db: Session, node: Node) -> None:
    node.ssh_password_enc = ssh_manager.rotate_password(node)
    node.ssh_password_rotated_at = datetime.now(UTC)
    db.flush()

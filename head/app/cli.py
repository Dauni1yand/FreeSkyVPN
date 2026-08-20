"""Operator commands that must work before anyone can log in.

    python -m app.cli create-admin <username> [password]
    python -m app.cli generate-key
    python -m app.cli add-node <host> <country> <ssh-password> [опции]
    python -m app.cli check-node <host> [--ssh-port N]
    python -m app.cli list-nodes [--json]
    python -m app.cli status [--json]
    python -m app.cli node-capacity <id> <N>
    python -m app.cli node-status <id> <active|draining>
    python -m app.cli node-delete <id>
    python -m app.cli grant <user-id> [минут]
    python -m app.cli egress-url

`create-admin` is the bootstrap: the panel has no sign-up, so the first
operator has to be made from a shell on the head. It also resets an
existing operator's password, which is the recovery path when someone is
locked out.

`add-node` is the same for the fleet. The web panel can do it too and
needs no Telegram to — but reaching a panel bound to loopback means an SSH
tunnel and a browser, and on a server being set up over SSH that is a
detour. Everything the form does, this does.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import secrets
import socket
import sys
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.config import get_settings
from app.db.models.node import Assignment, Inbound, Node, NodeStatus
from app.db.models.user import User, UserStatus
from app.db.session import SessionLocal
from app.services import access, egress, provisioning
from app.services.admin_auth import ensure_admin
from app.services.config_selector import NoCapacityError, assign_config
from app.services.ssh_manager import SshError


def create_admin(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m app.cli create-admin <username> [password]", file=sys.stderr)
        return 2

    username = argv[0]
    password = argv[1] if len(argv) > 1 else secrets.token_urlsafe(18)
    generated = len(argv) < 2

    with SessionLocal() as db:
        ensure_admin(db, username, password)
        db.commit()

    print(f"admin '{username}' ready")
    if generated:
        print(f"password: {password}")
        print("Store it now — it is not recoverable, only resettable by re-running this command.")
    return 0


def generate_key(_argv: list[str]) -> int:
    """A key for SECRETS_KEY / HEAD_SECRET_KEY."""
    print(secrets.token_urlsafe(48))
    return 0


def add_node(argv: list[str]) -> int:
    """Provision a node without a browser.

    Takes the same fields as the panel's form. The SSH password is used
    once, to install the head's key and run the bootstrap; the node's
    password is replaced with a random one before this returns, so the one
    typed here stops working — which is the point.
    """
    parser = argparse.ArgumentParser(prog="add-node", description=add_node.__doc__)
    parser.add_argument("host", help="IP или имя ноды")
    parser.add_argument("country", help="метка страны, пользователям не видна")
    parser.add_argument("ssh_password", help="пароль root от хостера, используется один раз")
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--uplink-mbit", type=int, default=100)
    parser.add_argument("--capacity", type=int, default=200)
    parser.add_argument("--control-sni", default="www.microsoft.com")
    args = parser.parse_args(argv)

    print(f"подключаю {args.host} — это 1–3 минуты", flush=True)
    with SessionLocal() as db:
        try:
            result = provisioning.provision_node(
                db,
                host=args.host.strip(),
                country=args.country.strip(),
                ssh_user=args.ssh_user.strip(),
                ssh_password=args.ssh_password,
                ssh_port=args.ssh_port,
                uplink_mbit=args.uplink_mbit,
                capacity=args.capacity,
                control_sni=args.control_sni.strip(),
            )
        except (provisioning.ProvisioningError, SshError) as exc:
            # Committed rather than rolled back: the half-provisioned row and
            # its failure log are how the next attempt knows where it stopped.
            db.commit()
            print(f"\nне удалось подключить {args.host}: {exc}", file=sys.stderr)
            return 1
        db.commit()

    for line in result.log:
        print(f"  · {line}")
    print(f"\nнода {args.host} подключена")
    print("Пароль на ноде сменён на случайный — введённый больше не действует.")
    return 0


#: Порты, на которые хостеры чаще всего переносят ssh с 22-го.
ALTERNATE_SSH_PORTS = (2222, 22222, 2200, 2022, 222)


def _probe(host: str, port: int, timeout: float) -> tuple[str, str]:
    """One TCP connection. Returns (исход, подробность)."""
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            # sshd представляется первым. Баннер отличает «порт открыт» от
            # «порт открыт, но за ним не ssh» — второе встречается, когда
            # хостер вешает на 22 свою заглушку.
            banner = sock.recv(128).decode(errors="replace").strip()
        return ("ssh" if banner.startswith("SSH-") else "open", banner)
    except TimeoutError:
        return ("filtered", "")
    except ConnectionRefusedError:
        return ("refused", "")
    except OSError as exc:
        return ("error", str(exc))


def _head_public_ip() -> str | None:
    """The address a node's firewall would need to allow.

    Not the same as the server's own idea of its address behind NAT, which
    is what people paste into a whitelist when the rule then does nothing.
    """
    import httpx

    for service in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with httpx.Client(timeout=8.0) as client:
                text = client.get(service).text.strip()
            if text and len(text) <= 45:
                return text
        except httpx.HTTPError:
            continue
    return None


def check_node(argv: list[str]) -> int:
    """Can the head open a TCP connection to a node's SSH port?

    Run before add-node, or after it fails. Provisioning cannot start until
    this works, and the answer separates the situations that look identical
    from inside a failed install: nothing came back at all, something came
    back and said no, or something answered that was not ssh.

    On a silent failure it keeps going rather than stopping at "no": it
    tries the ports hosters commonly move ssh to, and prints the address a
    firewall would have to allow. Both are the next question anyway, and
    asking them here saves a round trip through a support ticket.
    """
    parser = argparse.ArgumentParser(prog="check-node", description=check_node.__doc__)
    parser.add_argument("host")
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--no-scan",
        action="store_true",
        help="не искать ssh на других портах, если основной молчит",
    )
    args = parser.parse_args(argv)

    print(f"стучусь в {args.host}:{args.ssh_port} с головы…", flush=True)
    started = time.monotonic()
    outcome, detail = _probe(args.host, args.ssh_port, args.timeout)
    elapsed = (time.monotonic() - started) * 1000

    if outcome == "ssh":
        print(f"порт открыт, {elapsed:.0f} мс")
        print(f"за ним ssh: {detail}")
        print("\nСеть в порядке — можно запускать add-node.")
        return 0

    if outcome == "open":
        print(f"порт открыт, {elapsed:.0f} мс")
        print(f"но приветствие не похоже на ssh: {detail[:60]!r}", file=sys.stderr)
        print("Обычно это заглушка хостера. Настоящий ssh ищите на другом порту.", file=sys.stderr)
        return 1

    if outcome == "refused":
        print(
            f"\nсоединение отклонено — хост жив, но на {args.ssh_port} никто не слушает.\n"
            "Либо sshd на другом порту, либо он не запущен.",
            file=sys.stderr,
        )
    elif outcome == "filtered":
        print(
            f"\nне отвечает за {args.timeout:g} с — пакеты отбрасываются молча.\n"
            "До sshd дело не дошло, пароль ни при чём.",
            file=sys.stderr,
        )
    else:
        print(f"\nне достучаться: {detail}", file=sys.stderr)
        return 1

    if not args.no_scan:
        print("\nищу ssh на других портах…", file=sys.stderr)
        found = []
        for port in ALTERNATE_SSH_PORTS:
            state, banner = _probe(args.host, port, timeout=3.0)
            if state == "ssh":
                found.append((port, banner))
                print(f"  {port}: ssh — {banner}", file=sys.stderr)
            elif state == "open":
                print(f"  {port}: открыт, но не ssh", file=sys.stderr)
        if found:
            port = found[0][0]
            print(
                f"\nssh нашёлся на {port}. Добавляйте с этим портом:\n"
                f"  python -m app.cli add-node {args.host} <страна> '<пароль>' --ssh-port {port}",
                file=sys.stderr,
            )
            return 1
        print("  ни на одном из обычных портов ssh нет", file=sys.stderr)

    if outcome == "filtered":
        ip = _head_public_ip()
        print(
            "\nОстаются две причины, и обе на стороне ноды:\n"
            "  1. Сервер не запущен или ещё не развёрнут — проверьте в панели хостера.\n"
            "  2. Его фаервол не пропускает эту голову. Разрешить нужно адрес:",
            file=sys.stderr,
        )
        print(f"       {ip or '<не удалось определить>'}", file=sys.stderr)
        print(
            "     Часть заграничных хостеров режет российские диапазоны целиком —\n"
            "     тогда с вашего домашнего компьютера ssh зайдёт, а с головы нет.\n"
            "     Проверить: ssh root@" + args.host + " со своей машины.",
            file=sys.stderr,
        )
    return 1


def list_nodes(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="list-nodes")
    parser.add_argument("--json", action="store_true", help="машинно-читаемый вывод для меню")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        nodes = db.scalars(select(Node).order_by(Node.country, Node.host)).all()
        if args.json:
            print(json.dumps([_node_row(db, n) for n in nodes], ensure_ascii=False))
            return 0
        if not nodes:
            print("нод нет")
            return 0
        width = max(len(n.host) for n in nodes)
        for node in nodes:
            print(
                f"{node.host:<{width}}  {node.country:<14} "
                f"{node.status.value:<10} канал:{node.channel_state.value:<9} "
                f"ёмкость:{node.capacity:<5} {node.uplink_mbit or '?'} Мбит"
            )
    return 0



def _live_users(db, node: Node) -> int:
    """People currently placed on this node."""
    return db.scalar(
        select(func.count())
        .select_from(Assignment)
        .join(Inbound, Inbound.id == Assignment.inbound_id)
        .where(Inbound.node_id == node.id, Assignment.released_at.is_(None))
    ) or 0


def _node_row(db, node: Node) -> dict:
    return {
        "id": str(node.id),
        "host": node.host,
        "country": node.country,
        "status": node.status.value,
        "channel": node.channel_state.value,
        "capacity": node.capacity,
        "uplink_mbit": node.uplink_mbit,
        "users": _live_users(db, node),
    }


def _find_node(db, wanted: str) -> Node | None:
    """Resolve a node by id or by host, so a menu and a human agree.

    The menu passes ids; a person typing the command reaches for the
    address they already know. Accepting both costs one query.
    """
    node = None
    with contextlib.suppress(ValueError):
        node = db.get(Node, uuid.UUID(wanted))
    if node is None:
        node = db.scalar(select(Node).where(Node.host == wanted))
    return node


def status(argv: list[str]) -> int:
    """One screen answering "is anything wrong right now"."""
    parser = argparse.ArgumentParser(prog="status")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        nodes = db.scalars(select(Node)).all()
        now = datetime.now(UTC)
        data = {
            "nodes_total": len(nodes),
            "nodes_active": sum(1 for n in nodes if n.status == NodeStatus.active),
            "nodes_isolated": sum(1 for n in nodes if n.channel_state.value == "isolated"),
            "capacity": sum(n.capacity for n in nodes if n.status == NodeStatus.active),
            "users_total": db.scalar(select(func.count()).select_from(User)) or 0,
            "users_banned": db.scalar(
                select(func.count()).select_from(User).where(User.status == UserStatus.banned)
            ) or 0,
            "users_online": db.scalar(
                select(func.count())
                .select_from(User)
                .where(User.access_expires_at.isnot(None), User.access_expires_at > now)
            ) or 0,
            "assignments_live": db.scalar(
                select(func.count())
                .select_from(Assignment)
                .where(Assignment.released_at.is_(None))
            ) or 0,
        }

    if args.json:
        print(json.dumps(data, ensure_ascii=False))
        return 0

    print(f"Ноды        {data['nodes_active']} в работе из {data['nodes_total']}"
          + (f", изолировано {data['nodes_isolated']}" if data["nodes_isolated"] else ""))
    print(f"Ёмкость     {data['capacity']} мест, занято {data['assignments_live']}")
    print(f"Пользователи {data['users_total']}, со временем сейчас {data['users_online']}"
          + (f", заблокировано {data['users_banned']}" if data["users_banned"] else ""))
    return 0


def node_capacity(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="node-capacity")
    parser.add_argument("node")
    parser.add_argument("capacity", type=int)
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        node = _find_node(db, args.node)
        if node is None:
            print("нода не найдена", file=sys.stderr)
            return 1
        previous = node.capacity
        node.capacity = max(1, args.capacity)
        db.commit()
        ratio = get_settings().free_admission_ratio
        print(f"ёмкость {node.host}: {previous} → {node.capacity}")
        print(
            f"Запасной доступ перестанет приниматься на {int(node.capacity * ratio)}, "
            "остальное держится для тех, кто посмотрел рекламу."
        )
    return 0


def node_status(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="node-status")
    parser.add_argument("node")
    parser.add_argument("state", choices=[s.value for s in NodeStatus])
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        node = _find_node(db, args.node)
        if node is None:
            print("нода не найдена", file=sys.stderr)
            return 1
        node.status = NodeStatus(args.state)
        db.commit()
        if node.status == NodeStatus.draining:
            print(f"{node.host} выведена из ротации: новых не получит, текущие продолжают работать")
        else:
            print(f"{node.host} снова в ротации")
    return 0


def node_delete(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="node-delete")
    parser.add_argument("node")
    parser.add_argument("--yes", action="store_true", help="не спрашивать подтверждения")
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        node = _find_node(db, args.node)
        if node is None:
            print("нода не найдена", file=sys.stderr)
            return 1
        stranded = _live_users(db, node)
        host = node.host
        if not args.yes:
            # Спрашиваем именно про людей, а не про запись в таблице: удаление
            # ноды с живыми пользователями оставит их без связи до следующего
            # нажатия «подключиться», и это стоит знать заранее.
            warning = f" Сейчас на ней {stranded} чел." if stranded else ""
            answer = input(f"Удалить {host}?{warning} [y/N]: ").strip().lower()
            if answer not in ("y", "yes"):
                print("отменено")
                return 1
        db.delete(node)
        db.commit()
    print(f"{host} удалена" + (f", осталось без связи {stranded} чел." if stranded else ""))
    return 0


def grant(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="grant")
    parser.add_argument("user_id")
    parser.add_argument("minutes", type=int, nargs="?", default=60)
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        try:
            user = db.get(User, uuid.UUID(args.user_id))
        except ValueError:
            print("это не похоже на user_id", file=sys.stderr)
            return 1
        if user is None:
            print("пользователь не найден", file=sys.stderr)
            return 1
        state = access.grant_manual(db, user, args.minutes, by="cli")
        db.commit()
        print(f"выдано {args.minutes} мин; всего осталось {state.seconds_remaining // 60} мин")
    return 0


def egress_url(_argv: list[str]) -> int:
    """Print a vless:// link for the egress proxy.

    Only needed to pin the proxy to a fixed server. Left to itself the
    proxy asks the head over the API and re-asks when its node stops
    working, which is what survives a node being blocked.
    """
    with SessionLocal() as db:
        user = egress.get_or_create(db)
        try:
            config = assign_config(db, user)
        except NoCapacityError as exc:
            print(f"нет свободной ноды: {exc}", file=sys.stderr)
            print("Добавьте ноду и повторите: python -m app.cli add-node …", file=sys.stderr)
            return 1
        db.commit()

    print(config.vless_url)
    print(f"\nНода: {config.node_country}", file=sys.stderr)
    return 0


COMMANDS = {
    "create-admin": create_admin,
    "generate-key": generate_key,
    "add-node": add_node,
    "check-node": check_node,
    "list-nodes": list_nodes,
    "status": status,
    "node-capacity": node_capacity,
    "node-status": node_status,
    "node-delete": node_delete,
    "grant": grant,
    "egress-url": egress_url,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: python -m app.cli {{{'|'.join(COMMANDS)}}} [args]", file=sys.stderr)
        return 2
    return COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())

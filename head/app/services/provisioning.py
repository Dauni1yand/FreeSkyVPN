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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.node import Inbound, Node, NodeStatus, NodeTier
from app.services import crypto, ssh_manager
from app.services.ssh_manager import SshError

logger = logging.getLogger(__name__)

# Source checkout layout: <repo>/head/app/services/ -> <repo>/provisioning/
DEFAULT_BOOTSTRAP_SCRIPT = Path(__file__).resolve().parents[3] / "provisioning" / "bootstrap_node.sh"
REMOTE_CERT_PATH = "/root/freeskyvpn_head_client_cert.pem"


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
    tier: NodeTier = NodeTier.free,
    shaped_mbit: int = 10,
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
    node = Node(
        host=host,
        country=country,
        ssh_user=ssh_user,
        ssh_port=ssh_port,
        control_port=control_port,
        tier=tier,
        shaped_mbit=shaped_mbit if tier == NodeTier.free else None,
        # Not yet usable: it has no control-channel inbound and no pinned
        # certificate, so the selector must not hand users to it.
        status=NodeStatus.draining,
    )
    db.add(node)
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
            sftp = client.open_sftp()
            try:
                sftp.put(str(cert_path), REMOTE_CERT_PATH)
            finally:
                sftp.close()
            log.append("head client certificate uploaded")

            command = (
                f"bash -s -- {control_port} {control_sni} {control_reality_port} "
                f"{tier.value} {shaped_mbit}"
            )
            result = ssh_manager.run(client, command, stdin_data=_bootstrap_source())
            if result.exit_status != 0:
                raise ProvisioningError(
                    f"bootstrap failed (exit {result.exit_status}): {result.stderr.strip()[-1500:]}"
                )
            log.append("bootstrap completed")

        payload = _parse_bootstrap_output(result.stdout)
        _apply_bootstrap_payload(db, node, payload)
        log.append(f"registered control-channel inbound on port {payload['control_inbound']['port']}")

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


def rotate_node_password(db: Session, node: Node) -> None:
    node.ssh_password_enc = ssh_manager.rotate_password(node)
    node.ssh_password_rotated_at = datetime.now(UTC)
    db.flush()

"""SSH to a node: provisioning, key installation, password rotation.

The architecture keeps SSH out of the request path (all routine control is
REST — see app/node_manager), but three things genuinely need a shell on
the node: first-time provisioning, installing the head's SSH key, and
rotating the node's password. The admin panel drives all three.

Credential handling follows one rule: a password is a bootstrap mechanism,
not an access mechanism. The first successful connection installs a
generated keypair and rotates the password to something nobody has ever
typed, so from then on access is by key and the stored password exists only
as a break-glass path. This is why "add a node with a password" and "the
password is no longer the way in" are not in conflict.
"""

from __future__ import annotations

import io
import logging
import secrets
import string
from contextlib import contextmanager
from dataclasses import dataclass

import paramiko

from app.db.models.node import Node
from app.services import crypto

logger = logging.getLogger(__name__)

# Ambiguity-free alphabet: these end up in break-glass procedures that a
# human may have to read aloud or retype under pressure.
_PASSWORD_ALPHABET = "".join(
    c for c in string.ascii_letters + string.digits if c not in "0OoIl1"
) + "!@%^_-+="


class SshError(RuntimeError):
    """Could not connect to, or run something on, a node."""


@dataclass
class CommandResult:
    exit_status: int
    stdout: str
    stderr: str

    def check(self, what: str) -> CommandResult:
        if self.exit_status != 0:
            raise SshError(f"{what} failed (exit {self.exit_status}): {self.stderr.strip() or self.stdout.strip()}")
        return self


def generate_password(length: int = 32) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


@contextmanager
def connect(node: Node, *, password: str | None = None, timeout: float = 20.0):
    """Open an SSH session to `node`.

    Prefers the stored key and falls back to the stored password, so a node
    that has been hardened keeps working and one that has not been
    provisioned yet is still reachable. `password` overrides both, which is
    what the very first connection to a brand new node uses.
    """
    client = paramiko.SSHClient()
    # A brand-new node has no known host key and there is nothing to compare
    # against on first contact. Its identity is pinned afterwards by the
    # marzban-node certificate captured during provisioning, which is what
    # every subsequent control call verifies.
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    pkey = None
    if password is None and node.ssh_private_key_enc:
        key_material = crypto.decrypt(node.ssh_private_key_enc)
        pkey = paramiko.Ed25519Key.from_private_key(io.StringIO(key_material))

    if password is None and pkey is None:
        if not node.ssh_password_enc:
            raise SshError(f"node {node.id} has no stored SSH credentials")
        password = crypto.decrypt(node.ssh_password_enc)

    try:
        client.connect(
            hostname=node.host,
            port=node.ssh_port,
            username=node.ssh_user,
            password=password,
            pkey=pkey,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
        )
    except Exception as exc:
        raise SshError(f"cannot reach {node.ssh_user}@{node.host}:{node.ssh_port}: {exc}") from exc

    try:
        yield client
    finally:
        client.close()


def run(client: paramiko.SSHClient, command: str, *, stdin_data: str | None = None) -> CommandResult:
    stdin, stdout, stderr = client.exec_command(command, timeout=300)
    if stdin_data is not None:
        stdin.write(stdin_data)
        stdin.channel.shutdown_write()
    out = stdout.read().decode(errors="replace")
    err = stderr.read().decode(errors="replace")
    return CommandResult(exit_status=stdout.channel.recv_exit_status(), stdout=out, stderr=err)


def generate_keypair() -> tuple[str, str]:
    """A fresh Ed25519 keypair: (private PEM, public authorized_keys line)."""
    key = paramiko.Ed25519Key.generate()
    buffer = io.StringIO()
    key.write_private_key(buffer)
    public_line = f"{key.get_name()} {key.get_base64()} freeskyvpn-head"
    return buffer.getvalue(), public_line


def install_key(node: Node, *, password: str) -> str:
    """Install a generated key on the node. Returns the encrypted private key.

    Called during provisioning while the password still works. After this
    the head no longer needs the password for ordinary access.
    """
    private_pem, public_line = generate_keypair()

    with connect(node, password=password) as client:
        run(
            client,
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh && "
            f"grep -qxF '{public_line}' ~/.ssh/authorized_keys 2>/dev/null || "
            f"echo '{public_line}' >> ~/.ssh/authorized_keys && "
            "chmod 600 ~/.ssh/authorized_keys",
        ).check("installing the head's SSH key")

    return crypto.encrypt(private_pem)


def rotate_password(node: Node, *, new_password: str | None = None) -> str:
    """Set a new SSH password on the node. Returns it encrypted.

    Uses `chpasswd` over an existing session, so the connection that
    performs the change is authenticated by whatever already works — the key
    if one is installed, the old password otherwise.
    """
    new_password = new_password or generate_password()

    with connect(node) as client:
        result = run(client, "chpasswd", stdin_data=f"{node.ssh_user}:{new_password}\n")
        if result.exit_status != 0:
            # Some images ship chpasswd outside PATH for non-login shells.
            result = run(client, "/usr/sbin/chpasswd", stdin_data=f"{node.ssh_user}:{new_password}\n")
        result.check("rotating the SSH password")

    logger.info("rotated SSH password for node %s", node.id)
    return crypto.encrypt(new_password)


def check_connectivity(node: Node, *, password: str | None = None) -> str:
    """Confirm we can log in and report what we found. Used before provisioning."""
    with connect(node, password=password) as client:
        result = run(client, "cat /etc/os-release 2>/dev/null | head -2; uname -sr").check("probing the node")
    return result.stdout.strip()

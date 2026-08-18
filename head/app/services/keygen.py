"""Reality keypair / UUID generation on the head.

During bootstrap keys are generated on the node itself (bootstrap_node.sh),
but every inbound created *afterwards* is minted here, because the head is
the party that decides a new one is needed. That is no weaker: the head
already necessarily holds every inbound's private key, since marzban-node
takes whole Xray configs and the private key is part of one (see
node_manager/config_render.py).

Output format differs across Xray releases and both are handled:

    Xray 1.8.x        Xray 26.x
    ----------        ---------
    Private key: ...  PrivateKey: ...
    Public key: ...   Password (PublicKey): ...
                      Hash32: ...
"""

from __future__ import annotations

import re
import secrets
import shutil
import subprocess
import uuid
from dataclasses import dataclass

from app.config import get_settings

_PRIVATE_RE = re.compile(r"^\s*private\s*key\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_PUBLIC_RE = re.compile(
    r"^\s*(?:password\s*\(\s*publickey\s*\)|public\s*key)\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE
)


@dataclass(frozen=True)
class RealityKeypair:
    private_key: str
    public_key: str


def parse_x25519_output(output: str) -> RealityKeypair:
    private_match = _PRIVATE_RE.search(output)
    public_match = _PUBLIC_RE.search(output)
    if not private_match or not public_match:
        raise ValueError(f"could not parse `xray x25519` output: {output!r}")
    return RealityKeypair(private_key=private_match.group(1), public_key=public_match.group(1))


def generate_reality_keypair() -> RealityKeypair:
    binary = _xray_binary()
    result = subprocess.run([binary, "x25519"], capture_output=True, text=True, check=True, timeout=10)
    return parse_x25519_output(result.stdout)


def generate_short_id() -> str:
    """Reality shortId: an even-length hex string, at most 16 chars."""
    return secrets.token_hex(4)


def generate_client_uuid() -> str:
    return str(uuid.uuid4())


def _xray_binary() -> str:
    configured = get_settings().xray_client_binary_path
    return shutil.which(configured) or configured

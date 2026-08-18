"""Fallback path for when the head's *direct* connection to a node's control
API is blocked — the scenario this module exists for is RF-side DPI/TSPU
interfering with the head server's own outbound traffic, since the head (not
the nodes) is the party sitting behind censorship.

Rather than invent a second circumvention technique, the head runs a local
Xray client speaking the exact same VLESS+Reality+XTLS-Vision protocol
already serving paying users, and dials the node's *dedicated*
control-channel inbound (Inbound.is_control_channel=True) with it. That
gives a local SOCKS5 proxy; the REST control calls are then routed through
it unchanged. From the censor's vantage point this outbound connection is
indistinguishable from an ordinary customer using the VPN — it doesn't get
a weaker disguise just because it's carrying control traffic.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings


@dataclass(frozen=True)
class RealityTunnelParams:
    node_host: str
    node_port: int
    sni: str
    public_key: str
    short_id: str
    client_uuid: str


def _free_local_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class RealityTunnel:
    """One local `xray run` client process, tunnelling to a single node.

    Kept warm (not torn down after each call) since a node stuck on the
    fallback path will need it again on the very next control call, and
    spinning up a fresh Reality handshake per call would be needlessly slow
    and needlessly noisy for something trying to look like ordinary traffic.
    """

    def __init__(self, params: RealityTunnelParams):
        self._params = params
        self._process: subprocess.Popen | None = None
        self._config_path: Path | None = None
        self.local_socks_port: int | None = None

    def start(self) -> int:
        if self.is_running:
            assert self.local_socks_port is not None
            return self.local_socks_port

        settings = get_settings()
        binary = shutil.which(settings.xray_client_binary_path) or settings.xray_client_binary_path
        self.local_socks_port = _free_local_port()

        config = {
            "log": {"loglevel": "warning"},
            "inbounds": [
                {
                    "listen": "127.0.0.1",
                    "port": self.local_socks_port,
                    "protocol": "socks",
                    "settings": {"udp": False},
                }
            ],
            "outbounds": [
                {
                    "protocol": "vless",
                    "settings": {
                        "vnext": [
                            {
                                "address": self._params.node_host,
                                "port": self._params.node_port,
                                "users": [
                                    {
                                        "id": self._params.client_uuid,
                                        "flow": "xtls-rprx-vision",
                                        "encryption": "none",
                                    }
                                ],
                            }
                        ]
                    },
                    "streamSettings": {
                        "network": "tcp",
                        "security": "reality",
                        "realitySettings": {
                            "serverName": self._params.sni,
                            "publicKey": self._params.public_key,
                            "shortId": self._params.short_id,
                            "fingerprint": "chrome",
                        },
                    },
                }
            ],
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as fd:
            json.dump(config, fd)
            self._config_path = Path(fd.name)

        self._process = subprocess.Popen(
            [binary, "run", "-c", str(self._config_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return self.local_socks_port

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            self._process = None
        if self._config_path is not None:
            self._config_path.unlink(missing_ok=True)
            self._config_path = None
        self.local_socks_port = None

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

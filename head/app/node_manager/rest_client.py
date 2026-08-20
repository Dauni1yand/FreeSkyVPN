"""Client for a node's control API, as implemented by Gozargah/Marzban-node's
`rest_service.py` (SERVICE_PROTOCOL=rest — the project's default, and the one
this codebase standardises on instead of the rpyc alternative).

The wire contract, confirmed against the upstream source, is coarser than a
typical CRUD API:

    POST /connect   -> claims exclusive control, returns a session_id.
                        A second caller connecting evicts whoever held the
                        session before ("Core control access was taken away
                        from previous client"). That eviction-on-connect
                        behaviour is exactly what makes switching between
                        the direct and tunnelled paths in channel.py safe
                        without any explicit handoff.
    POST /ping       -> keepalive for the current session.
    POST /start       -> boots Xray with a full config blob (stdin).
    POST /restart     -> stop + start with a new config blob.
    POST /stop        -> stops Xray.
    POST /            -> status (connected / started / core_version).

There is no per-user or per-inbound RPC — every change is "here is the
entire desired Xray config, run it" (see config_render.py). Xray's own
HandlerService gRPC API (which marzban-node wires up internally on
XRAY_API_PORT for the currently-connected peer) allows incremental
add/remove without a restart; worth adopting once per-change restarts
become a real bottleneck, not needed at MVP scale.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import httpx


@dataclass
class NodeStatusResponse:
    connected: bool
    started: bool
    core_version: str | None
    session_id: uuid.UUID | None = None



def _check(response: httpx.Response) -> None:
    """raise_for_status, но с телом ответа в сообщении.

    marzban-node отвечает на неудачный /start кодом 503 и телом, в котором
    написано, почему Xray не поднялся, — это единственное место, где
    причина вообще называется. Штатный raise_for_status тело выбрасывает,
    и до головы доезжало «Server error '503 Service Unavailable'», годное
    только чтобы понять, что что-то не так.
    """
    if response.is_success:
        return

    detail = " ".join(response.text.split())[:600]
    message = (
        f"{response.request.method} {response.request.url.path} → "
        f"{response.status_code}" + (f": {detail}" if detail else "")
    )
    raise httpx.HTTPStatusError(message, request=response.request, response=response)


class NodeRestClient:
    """Speaks the marzban-node REST contract over a caller-supplied httpx.Client.

    This class only knows *what* to send; it has no opinion on *how* the
    bytes reach the node. The caller decides that by configuring the
    httpx.Client — a direct connection, or one routed through a local SOCKS
    proxy (see reality_tunnel.py) — which is what lets channel.py reuse this
    exact class for both the primary and fallback paths.
    """

    def __init__(self, client: httpx.Client, timeout_s: float = 3.0):
        self._client = client
        self._timeout_s = timeout_s
        self._session_id: uuid.UUID | None = None

    def connect(self) -> NodeStatusResponse:
        resp = self._client.post("/connect", timeout=self._timeout_s)
        _check(resp)
        data = resp.json()
        self._session_id = uuid.UUID(str(data["session_id"]))
        return NodeStatusResponse(
            connected=data["connected"],
            started=data["started"],
            core_version=data.get("core_version"),
            session_id=self._session_id,
        )

    def ping(self) -> None:
        self._require_session()
        resp = self._client.post("/ping", json={"session_id": str(self._session_id)}, timeout=self._timeout_s)
        _check(resp)

    def status(self) -> NodeStatusResponse:
        resp = self._client.post("/", timeout=self._timeout_s)
        _check(resp)
        data = resp.json()
        return NodeStatusResponse(connected=data["connected"], started=data["started"], core_version=data.get("core_version"))

    def push_config(self, xray_config_json: str) -> NodeStatusResponse:
        """Apply a full Xray config: boots Xray if it isn't running, restarts it otherwise."""
        self._require_session()
        current = self.status()
        path = "/restart" if current.started else "/start"
        resp = self._client.post(
            path,
            json={"session_id": str(self._session_id), "config": xray_config_json},
            timeout=max(self._timeout_s, 10.0),  # xray boot can legitimately take a couple of seconds
        )
        _check(resp)
        data = resp.json()
        return NodeStatusResponse(connected=data["connected"], started=data["started"], core_version=data.get("core_version"))

    def stop(self) -> None:
        self._require_session()
        resp = self._client.post("/stop", json={"session_id": str(self._session_id)}, timeout=self._timeout_s)
        _check(resp)

    def _require_session(self) -> None:
        if self._session_id is None:
            raise RuntimeError("connect() must succeed before calling this method")

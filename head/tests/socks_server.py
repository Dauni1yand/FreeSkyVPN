"""A minimal SOCKS5 server for testing the hand-written client.

Speaks only what the client speaks — no-auth CONNECT — and actually relays
bytes, so a TLS handshake can be driven through it end to end. Without a
real relay the client's framing would go unverified, which is the part most
likely to be wrong in a protocol implemented by hand.
"""

from __future__ import annotations

import socket
import struct
import threading
from contextlib import contextmanager


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("client closed mid-handshake")
        buf += chunk
    return buf


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while data := src.recv(65536):
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (src, dst):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _handle(
    client: socket.socket,
    force_reply: int | None,
    upstream_port: int | None,
    upstream_host: str | None = None,
    record: list | None = None,
) -> None:
    try:
        version, nmethods = _recv_exact(client, 2)
        _recv_exact(client, nmethods)
        client.sendall(bytes([version, 0x00]))  # no auth

        _ver, _cmd, _rsv, atyp = _recv_exact(client, 4)
        if atyp == 0x03:
            length = _recv_exact(client, 1)[0]
            host = _recv_exact(client, length).decode()
        elif atyp == 0x01:
            host = socket.inet_ntoa(_recv_exact(client, 4))
        else:
            client.sendall(b"\x05\x08\x00\x01" + b"\x00" * 6)
            return
        port = struct.unpack("!H", _recv_exact(client, 2))[0]
        if record is not None:
            record.append((host, port))

        if force_reply is not None:
            client.sendall(bytes([0x05, force_reply, 0x00, 0x01]) + b"\x00" * 6)
            return

        target_port = upstream_port if upstream_port is not None else port
        target_host = upstream_host if upstream_host is not None else host
        upstream = socket.create_connection((target_host, target_port), timeout=5)
        client.sendall(b"\x05\x00\x00\x01" + b"\x00" * 6)

        threading.Thread(target=_pump, args=(client, upstream), daemon=True).start()
        _pump(upstream, client)
    except OSError:
        pass
    finally:
        client.close()


@contextmanager
def socks5_server(
    force_reply: int | None = None,
    upstream_port: int | None = None,
    upstream_host: str | None = None,
    record: list | None = None,
):
    """Yields the port of a running SOCKS5 proxy.

    `force_reply` makes every CONNECT fail with that reply code, for testing
    the client's error handling.

    `upstream_port` redirects connections to a fixed port regardless of the
    requested one, so a probe aimed at "some-domain:443" can be served by a
    test listener. It leaves the *host* alone, which is enough when the
    request names something that resolves locally.

    `upstream_host` redirects that too. Needed when the client hardcodes a
    real destination it cannot be talked out of — api.telegram.org, say —
    and the test has no way to make that name resolve.

    `record` collects every (host, port) the proxy was asked to reach. That
    is what proves a client actually went through the proxy, without the
    test having to satisfy whatever the client does next.
    """
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    port = listener.getsockname()[1]
    stop = threading.Event()

    def serve() -> None:
        listener.settimeout(0.3)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(
                target=_handle,
                args=(conn, force_reply, upstream_port, upstream_host, record),
                daemon=True,
            ).start()

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        stop.set()
        thread.join(timeout=2)
        listener.close()

"""Minimal SOCKS5 CONNECT client.

Exists so an SNI probe can be made *from a node's network position* rather
than from the head's: the head sits in RF, but the host a Reality inbound
forwards to is contacted by the node, so the head's own view of a domain's
reachability and latency is the wrong measurement. `RealityTunnel` already
gives a local SOCKS5 proxy that egresses at the node (it is what carries
control calls when the direct path is blocked), and this turns that into an
ordinary socket the ssl module can wrap.

Only the no-auth CONNECT path of RFC 1928 is implemented — that is all the
locally-bound, single-purpose tunnel needs, and a fuller client would be
more surface than the job calls for.
"""

from __future__ import annotations

import socket
import struct

SOCKS_VERSION = 0x05
NO_AUTH = 0x00
CMD_CONNECT = 0x01
ATYP_IPV4 = 0x01
ATYP_DOMAIN = 0x03
ATYP_IPV6 = 0x04

_REPLY_ERRORS = {
    0x01: "general SOCKS server failure",
    0x02: "connection not allowed by ruleset",
    0x03: "network unreachable",
    0x04: "host unreachable",
    0x05: "connection refused",
    0x06: "TTL expired",
    0x07: "command not supported",
    0x08: "address type not supported",
}


class Socks5Error(RuntimeError):
    pass


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise Socks5Error("proxy closed the connection mid-handshake")
        buf += chunk
    return buf


def socks5_connect(
    proxy_host: str, proxy_port: int, dest_host: str, dest_port: int, timeout: float = 10.0
) -> socket.socket:
    """Open a TCP connection to dest through a SOCKS5 proxy.

    Returns the connected socket with the tunnel established, ready to be
    wrapped in TLS by the caller.
    """
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        sock.sendall(bytes([SOCKS_VERSION, 1, NO_AUTH]))
        version, method = _recv_exact(sock, 2)
        if version != SOCKS_VERSION:
            raise Socks5Error(f"unexpected SOCKS version {version}")
        if method != NO_AUTH:
            raise Socks5Error(f"proxy demands unsupported auth method {method}")

        host_bytes = dest_host.encode("idna")
        if len(host_bytes) > 255:
            raise Socks5Error("destination hostname too long for SOCKS5")
        request = (
            bytes([SOCKS_VERSION, CMD_CONNECT, 0x00, ATYP_DOMAIN, len(host_bytes)])
            + host_bytes
            + struct.pack("!H", dest_port)
        )
        sock.sendall(request)

        version, reply, _reserved, atyp = _recv_exact(sock, 4)
        if version != SOCKS_VERSION:
            raise Socks5Error(f"unexpected SOCKS version {version} in reply")
        if reply != 0x00:
            raise Socks5Error(_REPLY_ERRORS.get(reply, f"SOCKS error {reply}"))

        # The bound address is not useful here, but it has to be drained so
        # the socket is positioned at the start of the tunnelled stream.
        if atyp == ATYP_IPV4:
            _recv_exact(sock, 4)
        elif atyp == ATYP_IPV6:
            _recv_exact(sock, 16)
        elif atyp == ATYP_DOMAIN:
            length = _recv_exact(sock, 1)[0]
            _recv_exact(sock, length)
        else:
            raise Socks5Error(f"unsupported address type {atyp} in reply")
        _recv_exact(sock, 2)  # bound port

        return sock
    except Exception:
        sock.close()
        raise

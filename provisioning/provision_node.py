#!/usr/bin/env python3
"""Head-side driver for onboarding one new node — the only script that ever
opens an SSH connection to a node (see bootstrap_node.sh's header comment).

    python provision_node.py --host 203.0.113.10 --country nl \
        --client-cert /etc/freeskyvpn/head_client_cert.pem \
        --api-url http://localhost:8000

What it does, in order:
  1. scp's the head's mTLS client certificate to the node
  2. runs bootstrap_node.sh on the node over ssh (installs Docker, xray-core,
     generates the node's Reality keypair, starts marzban-node)
  3. parses the JSON line bootstrap_node.sh prints on success
  4. POSTs it to the head's own API to create the `nodes` + control-channel
     `inbounds` rows (app/api/routers/nodes.py)

From this point on nothing here is used again for that node — all further
management goes through app/node_manager (REST, direct or Reality-tunnelled).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

BOOTSTRAP_SCRIPT = Path(__file__).parent / "bootstrap_node.sh"
REMOTE_CERT_PATH = "/root/freeskyvpn_head_client_cert.pem"


def run_bootstrap(host: str, ssh_user: str, client_cert: Path, control_port: int, sni: str, reality_port: int) -> dict:
    print(f"[provision] copying head client cert to {host}", file=sys.stderr)
    subprocess.run(
        ["scp", "-o", "StrictHostKeyChecking=accept-new", str(client_cert), f"{ssh_user}@{host}:{REMOTE_CERT_PATH}"],
        check=True,
    )

    print(f"[provision] running bootstrap_node.sh on {host}", file=sys.stderr)
    result = subprocess.run(
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=accept-new",
            f"{ssh_user}@{host}",
            "bash -s --",
            str(control_port),
            sni,
            str(reality_port),
        ],
        input=BOOTSTRAP_SCRIPT.read_text(),
        text=True,
        capture_output=True,
        check=True,
    )
    print(result.stderr, file=sys.stderr)  # bootstrap_node.sh logs to stderr

    # bootstrap_node.sh's last stdout line is the JSON registration payload
    last_line = next(line for line in reversed(result.stdout.strip().splitlines()) if line.strip())
    return json.loads(last_line)


def register_with_head(api_url: str, payload: dict, country: str) -> dict:
    import httpx

    payload = {**payload, "country": country}
    resp = httpx.post(f"{api_url.rstrip('/')}/api/v1/nodes/register", json=payload, timeout=10.0)
    resp.raise_for_status()
    return resp.json()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="node's public IP or hostname")
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument("--country", required=True, help="e.g. nl, de — shown to admins, not to users")
    parser.add_argument("--client-cert", required=True, type=Path, help="head's mTLS client certificate")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--control-port", type=int, default=62050)
    parser.add_argument("--control-sni", default="www.microsoft.com")
    parser.add_argument("--control-reality-port", type=int, default=8443)
    args = parser.parse_args()

    payload = run_bootstrap(
        args.host, args.ssh_user, args.client_cert, args.control_port, args.control_sni, args.control_reality_port
    )
    node = register_with_head(args.api_url, payload, args.country)
    print(f"[provision] registered node {node['id']} ({node['host']}, {node['country']})")


if __name__ == "__main__":
    main()

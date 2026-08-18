"""Resolves the mTLS material `node_manager.channel.call_node` needs.

Each marzban-node generates its own self-signed certificate on first boot,
so there is no shared CA to trust — the head pins each node's individual
certificate instead, captured during provisioning and stored on the node
row. httpx wants a filesystem path for `verify=`, so the stored PEM is
materialised into a cache directory on first use.
"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings
from app.db.models.node import Node
from app.node_manager.channel import NodeCertBundle
from app.node_manager.exceptions import NodeNotProvisionedError


def bundle_for(node: Node) -> NodeCertBundle:
    if not node.tls_cert_pem:
        raise NodeNotProvisionedError(
            f"node {node.id} has no stored TLS certificate; re-run provisioning/provision_node.py for it"
        )

    settings = get_settings()
    cache_dir = Path(settings.node_cert_cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    cert_path = cache_dir / f"{node.id}.pem"
    # rewrite when the stored PEM changes (node re-provisioned with a new cert)
    if not cert_path.exists() or cert_path.read_text() != node.tls_cert_pem:
        cert_path.write_text(node.tls_cert_pem)

    return NodeCertBundle(
        ca_cert=str(cert_path),
        client_cert=settings.head_client_cert_path,
        client_key=settings.head_client_key_path,
    )

#!/usr/bin/env bash
#
# Runs ON a fresh foreign node, invoked over SSH by provision_node.py — this
# is the one and only place SSH is used (see the blueprint: "SSH один раз,
# дальше — API"). Everything after this script finishes talks to the node
# exclusively over marzban-node's REST control API (app/node_manager).
#
# What it does:
#   1. installs Docker (if missing) and the official Xray-core binary
#      (needed locally just to run `xray x25519`/`xray uuid` for keygen)
#   2. generates ONE Reality keypair + short id + client uuid for this
#      node's dedicated control-channel inbound (see Inbound.is_control_channel
#      in app/db/models/node.py) — the inbound the head tunnels through when
#      its direct connection is blocked
#   3. writes the head's mTLS client certificate (passed in as $1) into
#      marzban-node's SSL_CLIENT_CERT_FILE so it trusts the head
#   4. starts marzban-node itself via Docker, in REST mode
#   5. prints a single JSON line with everything provision_node.py needs to
#      call POST /api/v1/nodes/register on the head
#
# Usage (driven remotely, see provisioning/provision_node.py):
#   ssh root@<node> 'bash -s' -- <control_port> < bootstrap_node.sh
# with the head's client cert piped in via a side channel (scp'd to
# /root/freeskyvpn_head_client_cert.pem beforehand by provision_node.py).

set -euo pipefail

CONTROL_PORT="${1:-62050}"
CONTROL_SNI="${2:-www.microsoft.com}"
CONTROL_PORT_REALITY="${3:-8443}"
HEAD_CLIENT_CERT_PATH="/root/freeskyvpn_head_client_cert.pem"
MARZBAN_NODE_DIR="/var/lib/marzban-node"

log() { echo "[bootstrap] $*" >&2; }

if [[ ! -f "$HEAD_CLIENT_CERT_PATH" ]]; then
    echo "missing $HEAD_CLIENT_CERT_PATH — provision_node.py must scp it here before running this script" >&2
    exit 1
fi

log "installing docker (if missing)"
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
fi

log "installing xray-core (for local keygen only — the node's own copy ships inside the marzban-node image)"
if ! command -v xray &>/dev/null; then
    curl -fsSL https://github.com/Gozargah/Marzban-scripts/raw/master/install_latest_xray.sh | bash
fi

log "generating Reality keypair for the control-channel inbound"
KEYPAIR_OUTPUT="$(xray x25519)"
PRIVATE_KEY="$(echo "$KEYPAIR_OUTPUT" | awk -F': ' '/Private/{print $2}')"
PUBLIC_KEY="$(echo "$KEYPAIR_OUTPUT" | awk -F': ' '/Public/{print $2}')"
SHORT_ID="$(openssl rand -hex 8)"
CONTROL_CLIENT_UUID="$(xray uuid)"

log "provisioning marzban-node ($MARZBAN_NODE_DIR)"
mkdir -p "$MARZBAN_NODE_DIR"
cp "$HEAD_CLIENT_CERT_PATH" "$MARZBAN_NODE_DIR/ssl_client_cert.pem"

docker rm -f marzban-node &>/dev/null || true
docker run -d \
    --name marzban-node \
    --restart always \
    --network host \
    -e SSL_CERT_FILE="$MARZBAN_NODE_DIR/ssl_cert.pem" \
    -e SSL_KEY_FILE="$MARZBAN_NODE_DIR/ssl_key.pem" \
    -e SSL_CLIENT_CERT_FILE="$MARZBAN_NODE_DIR/ssl_client_cert.pem" \
    -e SERVICE_PROTOCOL="rest" \
    -e SERVICE_PORT="$CONTROL_PORT" \
    -v "$MARZBAN_NODE_DIR:$MARZBAN_NODE_DIR" \
    gozargah/marzban-node:latest

log "opening firewall for control + control-channel Reality ports"
if command -v ufw &>/dev/null; then
    ufw allow "$CONTROL_PORT"/tcp || true
    ufw allow "$CONTROL_PORT_REALITY"/tcp || true
fi

NODE_HOST="$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')"

log "done — registration payload follows on stdout"
cat <<JSON
{"host": "$NODE_HOST", "control_port": $CONTROL_PORT, "control_inbound": {"port": $CONTROL_PORT_REALITY, "sni": "$CONTROL_SNI", "reality_private_key": "$PRIVATE_KEY", "reality_public_key": "$PUBLIC_KEY", "reality_short_id": "$SHORT_ID", "control_client_uuid": "$CONTROL_CLIENT_UUID"}}
JSON

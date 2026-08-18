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
NODE_TIER="${4:-free}"          # free | paid
SHAPED_MBIT="${5:-10}"          # only applied when NODE_TIER=free
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

# Speed shaping. Xray-core has no per-user bandwidth limit (the `speedLimit`
# policy field seen in various guides is silently ignored — measured, not
# assumed), so the free/paid split is delivered by putting the two tiers on
# different nodes and shaping the free ones here, once. Nothing has to run on
# the node again afterwards, which keeps SSH to provisioning only.
#
# This is an interface-wide cap with fair queueing between flows, not a
# guaranteed per-user rate: free users on the node share `SHAPED_MBIT`, and
# fq_codel keeps one heavy user from starving the rest. That is the honest
# description of what this delivers.
if [[ "$NODE_TIER" == "free" ]]; then
    IFACE="$(ip route show default | awk '/default/ {print $5; exit}')"
    if [[ -z "$IFACE" ]]; then
        echo "could not determine the default interface; skipping shaping" >&2
    else
        log "shaping $IFACE to ${SHAPED_MBIT}mbit (free tier)"
        tc qdisc del dev "$IFACE" root 2>/dev/null || true
        tc qdisc add dev "$IFACE" root handle 1: htb default 10
        tc class add dev "$IFACE" parent 1: classid 1:10 \
            htb rate "${SHAPED_MBIT}mbit" ceil "${SHAPED_MBIT}mbit"
        tc qdisc add dev "$IFACE" parent 1:10 handle 10: fq_codel
        # tc is not persistent across reboots; re-apply on boot.
        cat > /etc/systemd/system/freeskyvpn-shaping.service <<UNIT
[Unit]
Description=FreeSkyVPN free-tier traffic shaping
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/bash -c 'tc qdisc del dev $IFACE root 2>/dev/null; \\
  tc qdisc add dev $IFACE root handle 1: htb default 10 && \\
  tc class add dev $IFACE parent 1: classid 1:10 htb rate ${SHAPED_MBIT}mbit ceil ${SHAPED_MBIT}mbit && \\
  tc qdisc add dev $IFACE parent 1:10 handle 10: fq_codel'

[Install]
WantedBy=multi-user.target
UNIT
        systemctl daemon-reload && systemctl enable --now freeskyvpn-shaping.service || \
            echo "could not install the shaping unit; shaping will not survive a reboot" >&2
    fi
else
    log "paid tier: leaving bandwidth unshaped"
fi

# marzban-node writes its self-signed cert on first start. The head pins this
# exact certificate as the only one it will accept from this node (there is no
# shared CA), so provisioning is not finished until we have it.
log "waiting for marzban-node to generate its TLS certificate"
for _ in $(seq 1 30); do
    [[ -s "$MARZBAN_NODE_DIR/ssl_cert.pem" ]] && break
    sleep 1
done
if [[ ! -s "$MARZBAN_NODE_DIR/ssl_cert.pem" ]]; then
    echo "marzban-node did not produce $MARZBAN_NODE_DIR/ssl_cert.pem; check 'docker logs marzban-node'" >&2
    exit 1
fi
# base64 so the PEM's newlines survive the single-line JSON below
TLS_CERT_B64="$(base64 -w0 < "$MARZBAN_NODE_DIR/ssl_cert.pem")"

NODE_HOST="$(curl -fsS https://api.ipify.org || hostname -I | awk '{print $1}')"

log "done — registration payload follows on stdout"
cat <<JSON
{"host": "$NODE_HOST", "control_port": $CONTROL_PORT, "tier": "$NODE_TIER", "shaped_mbit": $( [[ "$NODE_TIER" == "free" ]] && echo "$SHAPED_MBIT" || echo null ), "tls_cert_b64": "$TLS_CERT_B64", "control_inbound": {"port": $CONTROL_PORT_REALITY, "sni": "$CONTROL_SNI", "reality_private_key": "$PRIVATE_KEY", "reality_public_key": "$PUBLIC_KEY", "reality_short_id": "$SHORT_ID", "control_client_uuid": "$CONTROL_CLIENT_UUID"}}
JSON

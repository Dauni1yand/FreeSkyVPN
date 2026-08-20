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
UPLINK_MBIT="${4:-100}"         # link capacity to shape within
PAID_PORTS="${5:-443,2053,2087}" # served first when the link is contended
FREE_PORTS="${6:-8443,2083,2096}"
PAID_RANGE="${7:-20000-39999}"   # fallback ports, same priority as PAID_PORTS
FREE_RANGE="${8:-40000-59999}"
HEAD_CLIENT_CERT_PATH="/root/freeskyvpn_head_client_cert.pem"
MARZBAN_NODE_DIR="/var/lib/marzban-node"

log() { echo "[bootstrap] $*" >&2; }

if [[ ! -f "$HEAD_CLIENT_CERT_PATH" ]]; then
    echo "missing $HEAD_CLIENT_CERT_PATH — provision_node.py must scp it here before running this script" >&2
    exit 1
fi

# Проверяется явно, а не подразумевается. Минимальный облачный образ
# Ubuntu приезжает без unzip, а установщик Xray распаковывает им архив —
# и падает строкой «unzip: command not found» после того, как двадцать
# мегабайт уже скачаны, в середине полосы прогресса, где её не видно.
# Сначала снести прежнее, потом ставить.
#
# Нода задумана одноразовой: всё ценное живёт на голове, а здесь только то,
# что голова же и положила. Поэтому повторная установка не «дополняет»
# прежнюю, а начинает с чистого листа — иначе остатки предыдущей попытки
# (контейнер со старым сертификатом, чужой tc, скрипт запуска с другими
# флагами) продолжают действовать и объясняют потом самые непонятные отказы.
log "cleaning up anything left from a previous install"
docker rm -f marzban-node >/dev/null 2>&1 || true
rm -rf "$MARZBAN_NODE_DIR"
rm -f /usr/local/sbin/freeskyvpn-start-node.sh
rm -f "$HEAD_CLIENT_CERT_PATH.old"
# tc снимается со всех внешних интерфейсов: имя могло смениться вместе с
# образом, а прежняя дисциплина пережила бы переустановку и продолжила
# резать трафик по портам, которых уже нет.
for iface in $(ip -o link show 2>/dev/null | awk -F': ' '$2 !~ /^(lo|docker|veth|br-)/ {print $2}'); do
    tc qdisc del dev "$iface" root >/dev/null 2>&1 || true
done

log "checking prerequisites"
MISSING_PACKAGES=()
for tool in curl unzip openssl tc; do
    command -v "$tool" &>/dev/null && continue
    case "$tool" in
        tc) MISSING_PACKAGES+=("iproute2") ;;   # им же настраивается приоритет трафика
        *)  MISSING_PACKAGES+=("$tool") ;;
    esac
done

if (( ${#MISSING_PACKAGES[@]} )); then
    log "installing: ${MISSING_PACKAGES[*]}"
    if ! command -v apt-get &>/dev/null; then
        echo "нет apt-get, а не хватает: ${MISSING_PACKAGES[*]}" >&2
        echo "Поставьте эти пакеты на ноде вручную и повторите." >&2
        exit 1
    fi
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    if ! apt-get install -y -qq "${MISSING_PACKAGES[@]}"; then
        echo "не удалось установить: ${MISSING_PACKAGES[*]}" >&2
        exit 1
    fi
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

# The container's launch parameters are written to a script on the node rather
# than run inline, because updating Xray later means recreating this container
# with exactly these settings (see provisioning/update_node.sh). Keeping one
# copy on the node removes the chance of an update quietly recreating it with
# different flags.
cat > /usr/local/sbin/freeskyvpn-start-node.sh <<SCRIPT
#!/usr/bin/env bash
set -e
docker rm -f marzban-node >/dev/null 2>&1 || true
docker run -d \\
    --name marzban-node \\
    --restart always \\
    --network host \\
    -e SSL_CERT_FILE="$MARZBAN_NODE_DIR/ssl_cert.pem" \\
    -e SSL_KEY_FILE="$MARZBAN_NODE_DIR/ssl_key.pem" \\
    -e SSL_CLIENT_CERT_FILE="$MARZBAN_NODE_DIR/ssl_client_cert.pem" \\
    -e SERVICE_PROTOCOL="rest" \\
    -e SERVICE_PORT="$CONTROL_PORT" \\
    -v "$MARZBAN_NODE_DIR:$MARZBAN_NODE_DIR" \\
    gozargah/marzban-node:latest
SCRIPT
chmod +x /usr/local/sbin/freeskyvpn-start-node.sh
/usr/local/sbin/freeskyvpn-start-node.sh

log "opening firewall for control + control-channel Reality ports"
if command -v ufw &>/dev/null; then
    ufw allow "$CONTROL_PORT"/tcp || true
    ufw allow "$CONTROL_PORT_REALITY"/tcp || true
fi

# Traffic priority. Xray-core has no per-user bandwidth control (measured,
# not assumed: the `speedLimit` policy field is silently ignored, and
# `sendThrough` has no effect), so the only handle on a user's traffic that
# the node can act on is the port they connect to. Each tier owns a fixed set
# of ports — passed in from the head so the two cannot drift — and tc gives
# those sets different priority.
#
# Both classes may burst to the full link, so nothing is wasted when only one
# tier is active. The difference appears exactly when the link is contended:
# htb serves the higher-priority class first, so paying users get the
# bandwidth and free users get what is left.
#
# Written as a standalone script and then executed, rather than run inline
# and duplicated into a unit file: one copy of the rules means the boot-time
# state cannot drift from what was applied now.
IFACE="$(ip route show default | awk '/default/ {print $5; exit}')"
if [[ -z "$IFACE" ]]; then
    echo "could not determine the default interface; skipping traffic priority" >&2
else
    log "shaping $IFACE at ${UPLINK_MBIT}mbit, paid traffic served first"
    PAID_RATE=$((UPLINK_MBIT * 70 / 100))
    FREE_RATE=$((UPLINK_MBIT - PAID_RATE))

    {
        echo '#!/usr/bin/env bash'
        echo 'set -e'
        echo "IFACE=$IFACE"
        echo 'tc qdisc del dev "$IFACE" root 2>/dev/null || true'
        echo 'tc qdisc add dev "$IFACE" root handle 1: htb default 20'
        echo "tc class add dev \"\$IFACE\" parent 1: classid 1:1 htb rate ${UPLINK_MBIT}mbit ceil ${UPLINK_MBIT}mbit"
        # Guaranteed shares differ; both ceil at the full link, so the split
        # only bites while the two tiers actually compete.
        echo "tc class add dev \"\$IFACE\" parent 1:1 classid 1:10 htb rate ${PAID_RATE}mbit ceil ${UPLINK_MBIT}mbit prio 0"
        echo "tc class add dev \"\$IFACE\" parent 1:1 classid 1:20 htb rate ${FREE_RATE}mbit ceil ${UPLINK_MBIT}mbit prio 1"
        # fq_codel inside each class keeps one heavy user from starving peers
        # in the same tier.
        echo 'tc qdisc add dev "$IFACE" parent 1:10 handle 10: fq_codel'
        echo 'tc qdisc add dev "$IFACE" parent 1:20 handle 20: fq_codel'

        # Match on source port: for traffic leaving the node towards a client,
        # the source port is the inbound the client connected to.
        for port in ${PAID_PORTS//,/ }; do
            echo "tc filter add dev \"\$IFACE\" parent 1: protocol ip prio 1 u32 match ip sport $port 0xffff flowid 1:10"
        done
        for port in ${FREE_PORTS//,/ }; do
            echo "tc filter add dev \"\$IFACE\" parent 1: protocol ip prio 2 u32 match ip sport $port 0xffff flowid 1:20"
        done

        # Fallback ranges, used once a tier's preferred ports are all taken.
        # cls_basic is not present on every kernel, so a failure here is a
        # warning rather than fatal: the preferred ports still get priority.
        echo "tc filter add dev \"\$IFACE\" parent 1: protocol ip prio 3 basic match \"cmp(u16 at 0 layer transport gt $(( ${PAID_RANGE%-*} - 1 )))\" and \"cmp(u16 at 0 layer transport lt $(( ${PAID_RANGE#*-} + 1 )))\" flowid 1:10 2>/dev/null || echo 'note: cls_basic missing, paid fallback ports unprioritised' >&2"
        echo "tc filter add dev \"\$IFACE\" parent 1: protocol ip prio 3 basic match \"cmp(u16 at 0 layer transport gt $(( ${FREE_RANGE%-*} - 1 )))\" and \"cmp(u16 at 0 layer transport lt $(( ${FREE_RANGE#*-} + 1 )))\" flowid 1:20 2>/dev/null || true"
    } > /usr/local/sbin/freeskyvpn-shaping.sh

    chmod +x /usr/local/sbin/freeskyvpn-shaping.sh
    /usr/local/sbin/freeskyvpn-shaping.sh || echo "shaping failed to apply; check 'tc -s qdisc'" >&2

    # tc state is lost on reboot; re-apply from the same script.
    cat > /etc/systemd/system/freeskyvpn-shaping.service <<UNIT
[Unit]
Description=FreeSkyVPN traffic priority (paid served before free)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/freeskyvpn-shaping.sh

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload && systemctl enable freeskyvpn-shaping.service || \
        echo "could not install the shaping unit; priority will not survive a reboot" >&2
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
{"host": "$NODE_HOST", "control_port": $CONTROL_PORT, "uplink_mbit": $UPLINK_MBIT, "tls_cert_b64": "$TLS_CERT_B64", "control_inbound": {"port": $CONTROL_PORT_REALITY, "sni": "$CONTROL_SNI", "reality_private_key": "$PRIVATE_KEY", "reality_public_key": "$PUBLIC_KEY", "reality_short_id": "$SHORT_ID", "control_client_uuid": "$CONTROL_CLIENT_UUID"}}
JSON

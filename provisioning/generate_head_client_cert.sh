#!/usr/bin/env bash
# Generates the head's own mTLS client identity, once. Every node gets a
# copy of the resulting .pem (see bootstrap_node.sh) so it can verify the
# head as the only party allowed to control it — this is what
# SSL_CLIENT_CERT_FILE checks on the node side.
#
# Usage: ./generate_head_client_cert.sh /etc/freeskyvpn/
set -euo pipefail

OUT_DIR="${1:-.}"
mkdir -p "$OUT_DIR"

openssl req -x509 -newkey rsa:4096 -nodes \
    -keyout "$OUT_DIR/head_client_key.pem" \
    -out "$OUT_DIR/head_client_cert.pem" \
    -days 3650 \
    -subj "/CN=freeskyvpn-head"

chmod 600 "$OUT_DIR/head_client_key.pem"
echo "wrote $OUT_DIR/head_client_cert.pem and $OUT_DIR/head_client_key.pem"
echo "keep head_client_key.pem private — only head_client_cert.pem is copied to nodes"

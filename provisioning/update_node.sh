#!/usr/bin/env bash
#
# Updates Xray on a node, by pulling a newer marzban-node image and recreating
# the container from the launch script bootstrap left behind.
#
# Run over SSH by the head (app/services/xray_updates.py), never by hand in
# normal operation — the head only does it after an operator approves the
# update in Telegram or the admin panel.
#
# Deliberately reuses /usr/local/sbin/freeskyvpn-start-node.sh rather than
# restating `docker run`: an update that recreated the container with slightly
# different flags than provisioning used would be a very quiet way to break a
# node.
#
# Prints one JSON line describing the outcome.

set -uo pipefail

START_SCRIPT=/usr/local/sbin/freeskyvpn-start-node.sh
IMAGE="gozargah/marzban-node:latest"

emit() { printf '{"ok": %s, "before": "%s", "after": "%s", "error": "%s"}\n' "$1" "$2" "$3" "$4"; }

# Error text ends up inside the JSON above, so anything that would break the
# parse on the head has to go: quotes and backslashes are the two that a
# docker error message realistically contains (image refs and paths).
clean() { printf '%s' "$1" | tail -1 | tr -d '"\\' | cut -c1-300; }

xray_version() {
    # The version Xray reports from inside the running container. Empty if the
    # container is not up, which the caller treats as "unknown", not "old".
    docker exec marzban-node xray version 2>/dev/null \
        | awk 'NR==1{print $2; exit}'
}

if [[ ! -x $START_SCRIPT ]]; then
    emit false "" "" "no $START_SCRIPT on this node — it was provisioned before updates were supported, re-provision it"
    exit 1
fi

BEFORE="$(xray_version)"

if ! PULL_OUTPUT="$(docker pull "$IMAGE" 2>&1)"; then
    emit false "$BEFORE" "$BEFORE" "docker pull failed: $(clean "$PULL_OUTPUT")"
    exit 1
fi

# Recreating drops every current connection on this node. That is why the
# update needs approval rather than happening on a timer.
if ! RESTART_OUTPUT="$("$START_SCRIPT" 2>&1)"; then
    emit false "$BEFORE" "" "restart failed: $(clean "$RESTART_OUTPUT")"
    exit 1
fi

# Xray needs a moment before it answers; poll rather than sleep-and-hope.
AFTER=""
for _ in $(seq 1 20); do
    AFTER="$(xray_version)"
    [[ -n $AFTER ]] && break
    sleep 1
done

if [[ -z $AFTER ]]; then
    emit false "$BEFORE" "" "container did not come back after the update — check 'docker logs marzban-node' on the node"
    exit 1
fi

emit true "$BEFORE" "$AFTER" ""

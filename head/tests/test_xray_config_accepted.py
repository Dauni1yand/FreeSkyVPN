"""Validates rendered configs against a real Xray binary.

Everything else in the suite checks that render_node_config produces the
JSON *we* expect. This checks the thing that actually matters: that Xray
accepts it. A config the head is happy with but the core rejects would take
a node's Xray down on push, so it is worth catching here rather than in
production.

Skipped when no Xray binary is available — point XRAY_TEST_BINARY at one
(or install it at the configured xray_client_binary_path) to run it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

from app.config import get_settings
from app.db.models.node import Assignment
from app.node_manager.config_render import render_node_config
from tests.factories import make_inbound, make_node

# Reality rejects a config whose private key is not a valid x25519 key, so the
# placeholder keys the other tests use will not do here.
REAL_PRIVATE_KEY = "sEzIv3v8wmMLQPvtib565KCJdqdcni__ZhC4GQBJ20M"
REAL_PUBLIC_KEY = "VSiS_f1mjGzIlac8cP5lLh-RNQDBALGEjVVMoHaemjI"


def _xray_binary() -> str | None:
    for candidate in (os.environ.get("XRAY_TEST_BINARY"), get_settings().xray_client_binary_path, "xray"):
        if candidate and (found := shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)):
            return found
    return None


def _assert_xray_accepts(config_json: str) -> None:
    binary = _xray_binary()
    if binary is None:
        pytest.skip("no xray binary available; set XRAY_TEST_BINARY to run this test")

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
        fh.write(config_json)
        path = fh.name

    try:
        # `xray run` validates the config before binding; a rejected config
        # exits immediately with the reason, an accepted one keeps running
        # until the timeout kills it.
        result = subprocess.run(
            [binary, "run", "-c", path], capture_output=True, text=True, timeout=5, check=False
        )
        output = result.stdout + result.stderr
    except subprocess.TimeoutExpired as expired:
        output = (expired.stdout or b"").decode() + (expired.stderr or b"").decode()
    finally:
        Path(path).unlink(missing_ok=True)

    assert "Failed to start" not in output, f"xray rejected the rendered config:\n{output}"


@pytest.mark.parametrize("transport", ["reality-vision", "reality-grpc", "reality-xhttp"])
def test_rendered_config_is_accepted_by_xray(db, transport):
    node = make_node(db)
    inbound = make_inbound(
        db,
        node,
        port=18443,
        transport=transport,
        reality_private_key=REAL_PRIVATE_KEY,
        reality_public_key=REAL_PUBLIC_KEY,
    )
    inbound.assignments = [Assignment(user_id=uuid.uuid4(), xray_uuid=str(uuid.uuid4()))]

    _assert_xray_accepts(render_node_config([inbound]))


def test_rendered_control_channel_config_is_accepted_by_xray(db):
    node = make_node(db)
    inbound = make_inbound(
        db,
        node,
        port=18444,
        is_control_channel=True,
        control_client_uuid=str(uuid.uuid4()),
        reality_private_key=REAL_PRIVATE_KEY,
        reality_public_key=REAL_PUBLIC_KEY,
    )

    config = render_node_config([inbound])
    assert json.loads(config)["inbounds"][0]["settings"]["clients"][0]["id"] == inbound.control_client_uuid
    _assert_xray_accepts(config)

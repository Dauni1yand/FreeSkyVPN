"""Что голова сообщает, когда до ноды не достучались обоими путями.

Прямой путь и туннель отказывают по разным причинам, и полезная почти
всегда у первого. Настоящий случай: нода отвечала «503: Failed to start
core» — то есть приняла вызов и не смогла поднять Xray, — а туннель падал
с «Connection refused», потому что локальный SOCKS не поднялся. В ошибку
попадала последняя, то есть ровно та, что ничего не объясняет.

Со стороны это выглядело неразрешимо: в логе ноды виден 503 на /start, а
голова уверяет, что соединение отвергнуто. Два несовместимых факта про
одну и ту же попытку.
"""

from __future__ import annotations

import pytest

from app.db.models.node import NodeChannelState
from app.node_manager import channel
from app.node_manager.exceptions import NodeUnreachableError
from tests.factories import make_node


@pytest.fixture
def node(db):
    created = make_node(db)
    db.commit()
    return created


def _fail_with(monkeypatch, *, direct: Exception | None, tunnel: Exception | None):
    """Оба пути отказывают заданными ошибками."""

    class _Client:
        def __init__(self, exc):
            self._exc = exc

        def __enter__(self):
            raise self._exc

        def __exit__(self, *_a):
            return False

        def close(self):
            pass

    monkeypatch.setattr(
        channel, "_direct_client", lambda *_a: _Client(direct) if direct else None
    )
    monkeypatch.setattr(
        channel, "_tunnelled_client", lambda *_a: _Client(tunnel) if tunnel else None
    )


def test_the_direct_answer_is_not_replaced_by_the_tunnel_failure(db, node, monkeypatch):
    """Регрессия. Ответ ноды объясняет всё, отказ туннеля — ничего."""
    _fail_with(
        monkeypatch,
        direct=RuntimeError("POST /start → 503: Failed to start core: bad port"),
        tunnel=ConnectionRefusedError("[Errno 111] Connection refused"),
    )

    with pytest.raises(NodeUnreachableError) as excinfo:
        channel.call_node(db, node, None, lambda rest: rest.status())

    message = str(excinfo.value)
    assert "Failed to start core" in message, "ответ ноды обязан дойти"
    assert "Connection refused" in message, "вторая причина тоже нужна"


def test_each_failure_says_which_path_it_came_from(db, node, monkeypatch):
    """Без этого две причины в одной строке нечитаемы."""
    _fail_with(
        monkeypatch,
        direct=RuntimeError("отказ ноды"),
        tunnel=RuntimeError("отказ туннеля"),
    )

    with pytest.raises(NodeUnreachableError) as excinfo:
        channel.call_node(db, node, None, lambda rest: rest.status())

    message = str(excinfo.value)
    assert "напрямую: отказ ноды" in message
    assert "через туннель: отказ туннеля" in message


def test_one_path_failing_alone_is_reported_alone(db, node, monkeypatch):
    """Когда туннеля нет вовсе, лишней строки быть не должно."""
    _fail_with(monkeypatch, direct=RuntimeError("только прямой"), tunnel=None)

    with pytest.raises(NodeUnreachableError) as excinfo:
        channel.call_node(db, node, None, lambda rest: rest.status())

    message = str(excinfo.value)
    assert "напрямую: только прямой" in message
    assert "через туннель" not in message


def test_the_recorded_transition_carries_the_same_detail(db, node, monkeypatch):
    """Причина уходит и в историю состояний — там она нужна так же."""
    from app.config import get_settings

    node.consecutive_fallback_fails = get_settings().node_channel_fallback_fails_before_isolated
    db.commit()

    _fail_with(
        monkeypatch,
        direct=RuntimeError("503: Failed to start core"),
        tunnel=RuntimeError("туннель не поднялся"),
    )

    with pytest.raises(NodeUnreachableError):
        channel.call_node(db, node, None, lambda rest: rest.status())

    assert node.channel_state == NodeChannelState.isolated

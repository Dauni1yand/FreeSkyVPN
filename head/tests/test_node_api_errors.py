"""Что доезжает до головы, когда нода отказывает.

marzban-node отвечает на неудачный /start кодом 503 и телом, в котором
написано, почему Xray не поднялся. Это единственное место, где причина
вообще называется: в логе ноды остаётся только строка доступа с кодом.

Штатный raise_for_status тело выбрасывает, и до головы доезжало «Server
error '503 Service Unavailable' for url ...» — этого хватает, чтобы понять,
что что-то не так, и ни на что больше. Дальше строка попадает в
NoCapacityError, оттуда в лог egress, и её читает человек.
"""

from __future__ import annotations

import httpx
import pytest

from app.node_manager.rest_client import NodeRestClient


def client_returning(status_code: int, body: str, *, path_seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if path_seen is not None:
            path_seen.append(request.url.path)
        if request.url.path == "/connect":
            return httpx.Response(
                200, json={"connected": True, "started": False, "session_id": str(_SESSION)}
            )
        if request.url.path == "/":
            return httpx.Response(200, json={"connected": True, "started": False})
        return httpx.Response(status_code, text=body)

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://node")


_SESSION = "cb455d8a-42b2-4bde-91b4-c7270a20250c"


def test_the_nodes_reason_reaches_the_head():
    """Регрессия: причина 503 терялась по дороге."""
    rest = NodeRestClient(
        client_returning(503, '{"detail":"Failed to start core: invalid inbound port"}')
    )
    rest.connect()

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        rest.push_config("{}")

    assert "Failed to start core" in str(excinfo.value)
    assert "invalid inbound port" in str(excinfo.value)


def test_the_status_code_and_path_are_still_named():
    """Тело без кода двусмысленно: 503 и 401 лечатся по-разному."""
    rest = NodeRestClient(client_returning(503, '{"detail":"нет ядра"}'))
    rest.connect()

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        rest.push_config("{}")

    message = str(excinfo.value)
    assert "503" in message
    assert "/start" in message


def test_an_empty_body_does_not_produce_a_dangling_colon():
    rest = NodeRestClient(client_returning(500, ""))
    rest.connect()

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        rest.push_config("{}")

    assert str(excinfo.value).rstrip().endswith("500")


def test_a_huge_body_is_trimmed():
    """Нода может вернуть весь трейсбек; в сообщение он не помещается."""
    rest = NodeRestClient(client_returning(503, "x" * 5000))
    rest.connect()

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        rest.push_config("{}")

    assert len(str(excinfo.value)) < 800


def test_a_multiline_body_becomes_one_line():
    """Сообщение уходит одной строкой в лог; перевод строки его рвёт."""
    rest = NodeRestClient(client_returning(503, "первая строка\nвторая строка"))
    rest.connect()

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        rest.push_config("{}")

    assert "\n" not in str(excinfo.value)
    assert "первая строка вторая строка" in str(excinfo.value)


def test_a_successful_call_still_returns_normally():
    """Проверка ошибок не должна мешать штатному пути."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/connect":
            return httpx.Response(
                200, json={"connected": True, "started": False, "session_id": str(_SESSION)}
            )
        return httpx.Response(
            200, json={"connected": True, "started": True, "core_version": "1.8.24"}
        )

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="https://node") as raw:
        rest = NodeRestClient(raw)
        rest.connect()
        result = rest.push_config("{}")

    assert result.started
    assert result.core_version == "1.8.24"


def test_a_running_core_is_restarted_rather_than_started():
    """Ветка, по которой пойдёт нода с уже поднятым Xray."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/connect":
            return httpx.Response(
                200, json={"connected": True, "started": True, "session_id": str(_SESSION)}
            )
        if request.url.path == "/":
            return httpx.Response(200, json={"connected": True, "started": True})
        return httpx.Response(200, json={"connected": True, "started": True})

    with httpx.Client(transport=httpx.MockTransport(handler), base_url="https://node") as raw:
        rest = NodeRestClient(raw)
        rest.connect()
        rest.push_config("{}")

    assert "/restart" in seen
    assert "/start" not in seen

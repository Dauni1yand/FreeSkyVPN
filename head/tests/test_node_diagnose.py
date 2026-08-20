"""Что показывает диагностика ноды.

Управляющий канал отвечает «Connection refused»: пакет до ноды дошёл, на
порту никто не слушает. Само по себе это ничего не объясняет, а вариантов
всего несколько, и все видны с самой ноды. Ключ у головы уже есть, так что
она может посмотреть сама — вместо того чтобы просить человека сходить
туда руками.

Проверяется, что каждое состояние читается по-разному: «нет контейнера»,
«контейнер падает» и «всё на месте» лечатся не одинаково, а исходная
ошибка для всех трёх выглядела одной строкой.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import provisioning
from app.services.ssh_manager import CommandResult

NODE = SimpleNamespace(host="10.0.0.1", control_port=62050, ssh_user="root", ssh_port=22)


@pytest.fixture
def on_node(monkeypatch):
    """Отвечает за ноду: команда → (вывод, код возврата)."""

    def build(responses: dict[str, tuple[str, int]], listening: set[int]):
        def run(_client, command, **_kwargs):
            for needle, (out, code) in responses.items():
                if needle in command:
                    return CommandResult(stdout=out, stderr="", exit_status=code)
            return CommandResult(stdout="", stderr="", exit_status=1)

        connect = MagicMock()
        connect.return_value.__enter__ = lambda _self: MagicMock()
        connect.return_value.__exit__ = lambda *_a: False
        monkeypatch.setattr(provisioning.ssh_manager, "connect", connect)
        monkeypatch.setattr(provisioning.ssh_manager, "run", run)
        monkeypatch.setattr(
            provisioning.ssh_manager, "listening_ports", lambda _client: listening
        )
        return provisioning.diagnose_node(NODE)

    return build


ALL_CERTS = "ssl_cert.pem ssl_key.pem ssl_client_cert.pem"


def test_a_missing_container_is_named_as_such(on_node):
    report = "\n".join(
        on_node(
            {
                "docker inspect -f '{{.State.Status}}'": ("", 1),
                "docker images -q": ("", 0),
                "ls -1": ("", 0),
                "docker logs": ("", 1),
            },
            set(),
        )
    )
    assert "контейнера marzban-node на ноде нет" in report
    assert "образ тоже не скачан" in report
    assert "freeskyvpn-start-node.sh" in report, "нужно сказать, чем поднять"


def test_a_crashing_container_shows_its_exit_code_and_log(on_node):
    """«exited» без причины не лечится; причина — в логе контейнера."""
    report = "\n".join(
        on_node(
            {
                "docker inspect -f '{{.State.Status}}'": ("exited", 0),
                "docker inspect -f '{{.State.ExitCode}}'": ("1", 0),
                "ls -1": ("ssl_cert.pem ssl_key.pem", 0),
                "docker logs": ("FileNotFoundError: ssl_client_cert.pem", 0),
            },
            set(),
        )
    )
    assert "exited" in report
    assert "код выхода: 1" in report
    assert "FileNotFoundError" in report


def test_a_missing_certificate_is_pointed_out(on_node):
    """marzban-node без клиентского сертификата стартует и сразу падает."""
    report = "\n".join(
        on_node(
            {
                "docker inspect -f '{{.State.Status}}'": ("exited", 0),
                "docker inspect -f '{{.State.ExitCode}}'": ("1", 0),
                "ls -1": ("ssl_cert.pem ssl_key.pem", 0),
                "docker logs": ("", 0),
            },
            set(),
        )
    )
    assert "ssl_client_cert.pem" in report


def test_the_control_port_is_reported_either_way(on_node):
    healthy = "\n".join(
        on_node(
            {
                "docker inspect -f '{{.State.Status}}'": ("running", 0),
                "ls -1": (ALL_CERTS, 0),
                "docker logs": ("uvicorn running", 0),
            },
            {22, 62050},
        )
    )
    assert "62050 слушается" in healthy

    silent = "\n".join(
        on_node(
            {
                "docker inspect -f '{{.State.Status}}'": ("running", 0),
                "ls -1": (ALL_CERTS, 0),
                "docker logs": ("", 0),
            },
            {22},
        )
    )
    assert "62050 НЕ слушается" in silent


def test_a_healthy_node_says_nothing_alarming(on_node):
    """Иначе диагностика перестанет что-либо значить."""
    report = "\n".join(
        on_node(
            {
                "docker inspect -f '{{.State.Status}}'": ("running", 0),
                "ls -1": (ALL_CERTS, 0),
                "docker logs": ("uvicorn running on 0.0.0.0:62050", 0),
            },
            {22, 62050},
        )
    )
    assert "НЕ слушается" not in report
    assert "не хватает" not in report
    assert "нет" not in report.split("последнее из лога")[0]

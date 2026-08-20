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

from app.db.models.node import NodeChannelState
from app.services import provisioning
from app.services.ssh_manager import CommandResult

NODE = SimpleNamespace(
    host="10.0.0.1",
    control_port=62050,
    ssh_user="root",
    ssh_port=22,
    channel_state=NodeChannelState.isolated,
)


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
        monkeypatch.setattr(
            "app.node_manager.channel.call_node",
            lambda *_a, **_kw: None,
        )
        monkeypatch.setattr("app.services.certs.bundle_for", lambda _n: None)
        return provisioning.diagnose_node(None, NODE)

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


# --- повторное добавление -------------------------------------------------


def test_retrying_a_failed_node_continues_the_same_row(db, monkeypatch, tmp_path):
    """Провижининг рассчитан на перезапуск после неудачи.

    Но каждая попытка заводила новую строку: флот наполнялся
    полупровизиненными дублями одного адреса, каждый со своим ключом, и все
    они считались нодами — в том числе в сообщении «нет подходящей ноды».
    """
    from sqlalchemy import select

    from app.db.models.node import Node
    from app.services import crypto
    from app.services import provisioning as prov

    cert = tmp_path / "head_client_cert.pem"
    cert.write_text("cert", encoding="utf-8")
    monkeypatch.setattr(crypto, "is_configured", lambda: True)
    monkeypatch.setattr(
        prov, "get_settings", lambda: SimpleNamespace(head_client_cert_path=str(cert))
    )
    monkeypatch.setattr(
        prov.ssh_manager,
        "check_connectivity",
        lambda *_a, **_kw: (_ for _ in ()).throw(prov.SshError("нода молчит")),
    )

    for _ in range(3):
        with pytest.raises(prov.ProvisioningError):
            prov.provision_node(
                db,
                host="203.0.113.77",
                country="nl",
                ssh_user="root",
                ssh_password="x",
            )
        db.commit()

    rows = db.scalars(select(Node).where(Node.host == "203.0.113.77")).all()
    assert len(rows) == 1, f"после трёх попыток строк должно быть одна, а не {len(rows)}"


# --- взгляд со стороны головы --------------------------------------------


def test_a_healthy_looking_node_still_reports_whether_the_head_reaches_it(monkeypatch):
    """Нода может быть безупречна со своей стороны и недостижима для головы.

    Именно так и выглядела реальная поломка: контейнер работает, порт
    слушается, лог чистый — а канал управления изолирован. Отчёт, который
    заканчивался на «всё хорошо», оставлял человека там же, откуда начал.
    """

    def run(_client, command, **_kwargs):
        if "State.Status" in command:
            return CommandResult(stdout="running", stderr="", exit_status=0)
        if "ls -1" in command:
            return CommandResult(stdout=ALL_CERTS, stderr="", exit_status=0)
        return CommandResult(stdout="", stderr="", exit_status=0)

    connect = MagicMock()
    connect.return_value.__enter__ = lambda _self: MagicMock()
    connect.return_value.__exit__ = lambda *_a: False
    monkeypatch.setattr(provisioning.ssh_manager, "connect", connect)
    monkeypatch.setattr(provisioning.ssh_manager, "run", run)
    monkeypatch.setattr(provisioning.ssh_manager, "listening_ports", lambda _c: {62050})
    monkeypatch.setattr("app.services.certs.bundle_for", lambda _n: None)

    def refuse(*_args, **_kwargs):
        raise ConnectionRefusedError("[Errno 111] Connection refused")

    monkeypatch.setattr("app.node_manager.channel.call_node", refuse)

    report = "\n".join(provisioning.diagnose_node(None, NODE))
    assert "голова НЕ достучалась" in report
    assert "Connection refused" in report


def test_a_reachable_node_says_so_plainly(monkeypatch):
    def run(_client, command, **_kwargs):
        if "State.Status" in command:
            return CommandResult(stdout="running", stderr="", exit_status=0)
        if "ls -1" in command:
            return CommandResult(stdout=ALL_CERTS, stderr="", exit_status=0)
        return CommandResult(stdout="", stderr="", exit_status=0)

    connect = MagicMock()
    connect.return_value.__enter__ = lambda _self: MagicMock()
    connect.return_value.__exit__ = lambda *_a: False
    monkeypatch.setattr(provisioning.ssh_manager, "connect", connect)
    monkeypatch.setattr(provisioning.ssh_manager, "run", run)
    monkeypatch.setattr(provisioning.ssh_manager, "listening_ports", lambda _c: {62050})
    monkeypatch.setattr("app.services.certs.bundle_for", lambda _n: None)
    monkeypatch.setattr("app.node_manager.channel.call_node", lambda *_a, **_kw: None)

    report = "\n".join(provisioning.diagnose_node(None, NODE))
    assert "голова достучалась" in report


def test_the_diagnosis_reports_a_recovery_it_caused(monkeypatch):
    """Успешный вызов снимает изоляцию — об этом стоит сказать прямо,
    иначе человек пойдёт чинить то, что уже починилось."""
    from app.db.models.node import NodeChannelState

    node = SimpleNamespace(
        host="10.0.0.1",
        control_port=62050,
        ssh_user="root",
        ssh_port=22,
        channel_state=NodeChannelState.isolated,
    )

    def run(_client, command, **_kwargs):
        if "State.Status" in command:
            return CommandResult(stdout="running", stderr="", exit_status=0)
        if "ls -1" in command:
            return CommandResult(stdout=ALL_CERTS, stderr="", exit_status=0)
        return CommandResult(stdout="", stderr="", exit_status=0)

    def recover(_db, target, _certs, _fn):
        target.channel_state = NodeChannelState.active

    connect = MagicMock()
    connect.return_value.__enter__ = lambda _self: MagicMock()
    connect.return_value.__exit__ = lambda *_a: False
    monkeypatch.setattr(provisioning.ssh_manager, "connect", connect)
    monkeypatch.setattr(provisioning.ssh_manager, "run", run)
    monkeypatch.setattr(provisioning.ssh_manager, "listening_ports", lambda _c: {62050})
    monkeypatch.setattr("app.services.certs.bundle_for", lambda _n: None)
    monkeypatch.setattr("app.node_manager.channel.call_node", recover)

    report = "\n".join(provisioning.diagnose_node(None, node))
    assert "isolated → active" in report

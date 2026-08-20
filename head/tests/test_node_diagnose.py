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


# --- сертификат головы на ноде -------------------------------------------


def _cert_scenario(monkeypatch, tmp_path, *, remote_fingerprint, local_pem=None):
    """Диагностика при заданном сертификате на ноде и на голове."""
    from app.services import provisioning as prov

    local = tmp_path / "head_client_cert.pem"
    local.write_text(local_pem or _SELF_SIGNED, encoding="utf-8")
    monkeypatch.setattr(
        prov, "get_settings", lambda: SimpleNamespace(head_client_cert_path=str(local))
    )

    def run(_client, command, **_kwargs):
        if "State.Status" in command:
            return CommandResult(stdout="running", stderr="", exit_status=0)
        if "ls -1" in command:
            return CommandResult(stdout=ALL_CERTS, stderr="", exit_status=0)
        if "fingerprint" in command:
            return CommandResult(
                stdout=f"sha256 Fingerprint={remote_fingerprint}", stderr="", exit_status=0
            )
        return CommandResult(stdout="", stderr="", exit_status=0)

    connect = MagicMock()
    connect.return_value.__enter__ = lambda _self: MagicMock()
    connect.return_value.__exit__ = lambda *_a: False
    monkeypatch.setattr(prov.ssh_manager, "connect", connect)
    monkeypatch.setattr(prov.ssh_manager, "run", run)
    monkeypatch.setattr(prov.ssh_manager, "listening_ports", lambda _c: {62050})
    monkeypatch.setattr("app.services.certs.bundle_for", lambda _n: None)
    monkeypatch.setattr("app.node_manager.channel.call_node", lambda *_a, **_kw: None)
    return "\n".join(prov.diagnose_node(None, NODE))


def _fingerprint_of(pem: str) -> str:
    import hashlib
    import ssl as ssl_module

    digest = hashlib.sha256(ssl_module.PEM_cert_to_DER_cert(pem)).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def test_a_matching_head_certificate_is_not_mentioned(monkeypatch, tmp_path):
    """Совпадение — это норма, о норме диагностика молчит."""
    report = _cert_scenario(
        monkeypatch, tmp_path, remote_fingerprint=_fingerprint_of(_SELF_SIGNED)
    )
    assert "ДРУГОЙ клиентский сертификат" not in report


def test_a_stale_head_certificate_on_the_node_is_called_out(monkeypatch, tmp_path):
    """Пересоздали secrets/ — и все прежние ноды перестают узнавать голову.

    Со стороны головы это выглядит обрывом соединения без объяснений, и
    догадаться про сертификат неоткуда.
    """
    report = _cert_scenario(
        monkeypatch,
        tmp_path,
        remote_fingerprint="AA:BB:CC:DD" + ":00" * 28,
    )
    assert "ДРУГОЙ клиентский сертификат" in report
    assert "add-node" in report, "нужно сказать, чем лечится"


def test_the_node_log_is_read_after_the_attempt(monkeypatch, tmp_path):
    """Наш конец видит только обрыв; почему нода его закрыла — знает нода.

    Лог, прочитанный до попытки, этой попытки не содержит.
    """
    from app.services import provisioning as prov

    order: list[str] = []

    def run(_client, command, **_kwargs):
        if "docker logs" in command:
            order.append("logs")
            return CommandResult(stdout="ssl error from client", stderr="", exit_status=0)
        if "State.Status" in command:
            return CommandResult(stdout="running", stderr="", exit_status=0)
        if "ls -1" in command:
            return CommandResult(stdout=ALL_CERTS, stderr="", exit_status=0)
        return CommandResult(stdout="", stderr="", exit_status=0)

    def failing_call(*_args, **_kwargs):
        order.append("call")
        raise ConnectionResetError("[Errno 104] Connection reset by peer")

    connect = MagicMock()
    connect.return_value.__enter__ = lambda _self: MagicMock()
    connect.return_value.__exit__ = lambda *_a: False
    monkeypatch.setattr(prov.ssh_manager, "connect", connect)
    monkeypatch.setattr(prov.ssh_manager, "run", run)
    monkeypatch.setattr(prov.ssh_manager, "listening_ports", lambda _c: {62050})
    monkeypatch.setattr("app.services.certs.bundle_for", lambda _n: None)
    monkeypatch.setattr("app.node_manager.channel.call_node", failing_call)

    report = "\n".join(prov.diagnose_node(None, NODE))

    assert order.index("call") < order.index("logs"), "лог должен читаться после попытки"
    assert "лог ноды после этой попытки" in report
    assert "ssl error from client" in report


_SELF_SIGNED = """-----BEGIN CERTIFICATE-----
MIIBhTCCASugAwIBAgIUP0Q1qKzq9wMBCTMYqR8YrKzZ3AkwCgYIKoZIzj0EAwIw
FDESMBAGA1UEAwwJdGVzdC1jZXJ0MB4XDTI0MDEwMTAwMDAwMFoXDTM0MDEwMTAw
MDAwMFowFDESMBAGA1UEAwwJdGVzdC1jZXJ0MFkwEwYHKoZIzj0CAQYIKoZIzj0D
AQcDQgAEa8mBl0pDwbmXwCQmOQMVLmwuNL9CzXHkzTQmtdCTnJdSKQmB1cVN0Vqd
0YQKQmJ0V0nGDkfMxLJcBLQmVLQmB6NTMFEwHQYDVR0OBBYEFPQmVLQmB6nJdSKQ
mB1cVN0VqdMB8GA1UdIwQYMBaAFPQmVLQmB6nJdSKQmB1cVN0VqdMA8GA1UdEwEB
/wQFMAMBAf8wCgYIKoZIzj0EAwIDSAAwRQIhAPQmVLQmB6nJdSKQmB1cVN0VqdQm
VLQmB6nJdSKQmB1cAiA0VqdQmVLQmB6nJdSKQmB1cVN0VqdQmVLQmB6nJdSKQmA==
-----END CERTIFICATE-----
"""


# --- настоящая выдача конфига ---------------------------------------------


def _push_scenario(monkeypatch, *, push, pushing):
    """Диагностика с успешным status() и заданным поведением push_config."""
    from app.services import provisioning as prov

    def run(_client, command, **_kwargs):
        if "State.Status" in command:
            return CommandResult(stdout="running", stderr="", exit_status=0)
        if "ls -1" in command:
            return CommandResult(stdout=ALL_CERTS, stderr="", exit_status=0)
        if "fingerprint" in command:
            return CommandResult(stdout="", stderr="", exit_status=1)
        return CommandResult(stdout="", stderr="", exit_status=0)

    connect = MagicMock()
    connect.return_value.__enter__ = lambda _self: MagicMock()
    connect.return_value.__exit__ = lambda *_a: False
    monkeypatch.setattr(prov.ssh_manager, "connect", connect)
    monkeypatch.setattr(prov.ssh_manager, "run", run)
    monkeypatch.setattr(prov.ssh_manager, "listening_ports", lambda _c: {62050})
    monkeypatch.setattr("app.services.certs.bundle_for", lambda _n: None)
    monkeypatch.setattr("app.node_manager.channel.call_node", lambda *_a, **_kw: None)
    monkeypatch.setattr("app.services.node_sync.push_node_config", pushing)
    return "\n".join(prov.diagnose_node(None, NODE, push=push))


def test_without_the_flag_the_report_admits_what_it_did_not_check(monkeypatch):
    """Опрос состояния проходит и там, где выдача конфига не проходит.

    Так и выглядела поломка: канал «active», а пользователи без конфигов.
    Отчёт не должен выглядеть зелёным, проверив самый дешёвый вызов.
    """
    called = []
    report = _push_scenario(
        monkeypatch, push=False, pushing=lambda *_a: called.append("push")
    )
    assert not called, "без флага ничего перезапускать нельзя"
    assert "--push" in report
    assert "только опрос состояния" in report


def test_the_flag_surfaces_why_the_node_refuses_a_config(monkeypatch):
    def refuse(*_args, **_kwargs):
        raise RuntimeError("POST /start → 503: Failed to start core: bad port")

    report = _push_scenario(monkeypatch, push=True, pushing=refuse)
    assert "выдача конфига НЕ прошла" in report
    assert "Failed to start core" in report


def test_a_working_push_is_stated_plainly(monkeypatch):
    report = _push_scenario(monkeypatch, push=True, pushing=lambda *_a: None)
    assert "выдача конфига прошла" in report

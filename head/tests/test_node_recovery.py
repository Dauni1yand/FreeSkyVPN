"""Возврат ноды в строй после того, как её признали недоступной.

Изоляция задумывалась как временное состояние: `call_node` снимает её, как
только нода ответит. Но выбиралка исключает изолированные ноды из
кандидатов — значит обращаться к ним больше некому, и состояние, которое
снимается только успешным вызовом, не получает ни одного вызова.

Единственным выходом была проверка обновлений Xray: она ходит по всем
активным нодам раз в двенадцать часов и заодно задевает изолированные.
То есть выздоровевшая нода возвращалась в работу в среднем через шесть
часов, случайно и молча.
"""

from __future__ import annotations

import pytest

from app.db.models.node import NodeChannelState, NodeStatus
from app.services import scheduler
from app.services.config_selector import eligible_nodes
from app.services.tiers import Tier
from tests.factories import make_inbound, make_node, seed_snis


@pytest.fixture
def isolated_node(db):
    seed_snis(db)
    node = make_node(db)
    make_inbound(db, node)
    node.channel_state = NodeChannelState.isolated
    db.commit()
    return node


def test_an_isolated_node_is_not_offered_to_users(db, isolated_node):
    """Исходное поведение, ради которого изоляция и существует."""
    assert eligible_nodes(db, tier=Tier.full) == []


def test_nothing_in_the_user_path_would_ever_call_it(db, isolated_node):
    """Из чего и следует, что нужен отдельный проход.

    Тест фиксирует именно это рассуждение: раз нода не попадает в
    кандидаты, ни одно подключение её не затронет, и сама она не оживёт.
    """
    assert isolated_node not in eligible_nodes(db)
    assert isolated_node not in eligible_nodes(db, tier=Tier.grace)


def test_the_recovery_pass_picks_exactly_the_unhealthy_ones(db, monkeypatch, isolated_node):
    healthy = make_node(db, host="203.0.113.99")
    make_inbound(db, healthy, port=2053)
    draining = make_node(db, host="203.0.113.98")
    draining.status = NodeStatus.draining
    draining.channel_state = NodeChannelState.isolated
    db.commit()

    called: list[str] = []

    def fake_call(_db, node, _certs, _fn):
        called.append(node.host)

    monkeypatch.setattr(scheduler, "SessionLocal", lambda: _NoCloseSession(db))
    monkeypatch.setattr("app.node_manager.channel.call_node", fake_call)
    monkeypatch.setattr("app.services.certs.bundle_for", lambda _n: None)

    scheduler.run_node_recovery()

    assert isolated_node.host in called, "изолированную ноду нужно пробовать"
    assert healthy.host not in called, "здоровую дёргать незачем"
    assert draining.host not in called, "выведенную из ротации возвращать не просили"


def test_a_node_that_answers_comes_back_into_rotation(db, monkeypatch, isolated_node):
    """Собственно выздоровление: после успешного вызова нода снова кандидат."""

    def fake_call(_db, node, _certs, _fn):
        node.channel_state = NodeChannelState.active
        node.consecutive_fallback_fails = 0

    monkeypatch.setattr(scheduler, "SessionLocal", lambda: _NoCloseSession(db))
    monkeypatch.setattr("app.node_manager.channel.call_node", fake_call)
    monkeypatch.setattr("app.services.certs.bundle_for", lambda _n: None)

    scheduler.run_node_recovery()

    assert eligible_nodes(db, tier=Tier.full) != []


def test_a_node_that_stays_down_does_not_break_the_pass(db, monkeypatch, isolated_node):
    """Проход идёт по всем нодам; одна упавшая не должна отменять остальные."""
    other = make_node(db, host="203.0.113.97")
    other.channel_state = NodeChannelState.isolated
    db.commit()

    seen: list[str] = []

    def fake_call(_db, node, _certs, _fn):
        seen.append(node.host)
        raise RuntimeError("всё ещё недоступна")

    monkeypatch.setattr(scheduler, "SessionLocal", lambda: _NoCloseSession(db))
    monkeypatch.setattr("app.node_manager.channel.call_node", fake_call)
    monkeypatch.setattr("app.services.certs.bundle_for", lambda _n: None)

    scheduler.run_node_recovery()
    assert len(seen) == 2


class _NoCloseSession:
    """Отдаёт тестовую сессию и не закрывает её на выходе из `with`."""

    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *_exc):
        return False

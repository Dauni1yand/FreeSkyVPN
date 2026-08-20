"""Почему подходящей ноды нет.

«no node is currently accepting full-class users» звучало одинаково и когда
нод нет вовсе, и когда они есть, но недоступны, и когда просто заполнены.
Лечится это тремя разными способами, а строка была одна — по ней нельзя
было выбрать даже направление. Она же уходит в лог контейнера egress, где
её читает человек.
"""

from __future__ import annotations

from app.db.models.node import NodeChannelState, NodeStatus
from app.services.config_selector import _why_nothing_eligible
from app.services.tiers import Tier
from tests.factories import make_node


def test_an_empty_fleet_says_so(db):
    assert "не зарегистрировано" in _why_nothing_eligible(db, Tier.full)


def test_an_unreachable_node_is_named_as_unreachable(db):
    node = make_node(db)
    node.channel_state = NodeChannelState.isolated
    db.commit()

    reason = _why_nothing_eligible(db, Tier.full)
    assert "недоступны по управляющему каналу" in reason
    assert node.host in reason, "нужно назвать адрес, а не только количество"
    assert "не зарегистрировано" not in reason, "нода есть, дело не в этом"


def test_a_drained_node_is_distinguished_from_an_unreachable_one(db):
    """Одну чинят, другую возвращают в ротацию одним нажатием."""
    node = make_node(db)
    node.status = NodeStatus.draining
    db.commit()

    reason = _why_nothing_eligible(db, Tier.full)
    assert "выведены из ротации" in reason
    assert "недоступны" not in reason


def test_a_full_fleet_says_it_is_full(db):
    make_node(db)
    db.commit()
    assert "заполнены" in _why_nothing_eligible(db, Tier.full)


def test_the_grace_ceiling_is_explained_rather_than_left_as_a_number(db):
    """Запасной доступ упирается в 80% намеренно, и это стоит сказать."""
    make_node(db)
    db.commit()

    reason = _why_nothing_eligible(db, Tier.grace)
    assert "80%" in reason
    assert "посмотрел рекламу" in reason


def test_a_mixed_fleet_accounts_for_every_node(db):
    unreachable = make_node(db, host="203.0.113.1")
    unreachable.channel_state = NodeChannelState.isolated
    drained = make_node(db, host="203.0.113.2")
    drained.status = NodeStatus.draining
    make_node(db, host="203.0.113.3")
    db.commit()

    reason = _why_nothing_eligible(db, Tier.full)
    assert "всего нод 3" in reason
    # Адреса, а не счётчики: на флоте из одной ноды «1 недоступна» не
    # говорит, какая именно, а на большом — куда идти смотреть.
    assert "203.0.113.1" in reason
    assert "203.0.113.2" in reason
    assert "1 заполнены" in reason

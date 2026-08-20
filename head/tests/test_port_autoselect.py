"""Порты, занятые на ноде кем-то другим.

Голова выбирает порт инбаунда из короткого списка обычных HTTPS-портов.
Если у хостера на 8443 висит панель, а на 443 — веб-сервер, Xray не сможет
занять выданный порт, и пользователь получит конфиг, который никуда не
подключается. Сигнала при этом нет никакого, кроме нажатия «не работает»
через какое-то время.

Провижининг спрашивает ноду один раз — пока у него ещё есть shell — и
записывает ответ. Дальше это просто порты, которые никогда не предлагаются.
"""

from __future__ import annotations

import pytest

from app.db.models.node import InboundState
from app.services import provisioning, tiers
from app.services.inbound_factory import pick_port
from app.services.ssh_manager import _parse_listening
from app.services.tiers import Tier
from tests.factories import make_inbound, make_node, seed_snis

# --- чтение занятых портов -----------------------------------------------


def test_ss_output_is_understood():
    output = (
        "LISTEN 0      4096   0.0.0.0:22        0.0.0.0:*\n"
        "LISTEN 0      511    *:443             *:*\n"
        "LISTEN 0      4096   [::]:8443         [::]:*\n"
    )
    assert _parse_listening(output) == {22, 443, 8443}


def test_netstat_output_is_understood():
    """Запасной инструмент: ss есть не в каждом минимальном образе."""
    output = (
        "Proto Recv-Q Send-Q Local Address           Foreign Address         State\n"
        "tcp        0      0 0.0.0.0:22              0.0.0.0:*               LISTEN\n"
        "tcp6       0      0 :::2053                 :::*                    LISTEN\n"
    )
    assert _parse_listening(output) == {22, 2053}


def test_a_loopback_listener_counts_as_taken():
    """Занять 0.0.0.0:P, когда держат 127.0.0.1:P, всё равно не выйдет."""
    assert 33060 in _parse_listening("LISTEN 0 70 127.0.0.1:33060 0.0.0.0:*")


def test_nonsense_yields_nothing_rather_than_garbage():
    """Пустой ответ означает «не узнали» и не должен ничего запрещать."""
    assert _parse_listening("command not found\n") == set()


# --- выбор порта ----------------------------------------------------------


def test_a_port_the_hoster_holds_is_never_handed_out(db):
    """Регрессия. Без этого 8443 выдавался бы поверх чужой панели."""
    seed_snis(db)
    node = make_node(db)
    node.occupied_ports = list(tiers.ports_for(Tier.grace))
    db.commit()

    port = pick_port(db, node, Tier.grace)
    assert port not in tiers.ports_for(Tier.grace)
    low, high = tiers.fallback_range_for(Tier.grace)
    assert low <= port <= high, "должен уйти в запасной диапазон своего класса"


def test_only_the_busy_ones_are_skipped(db):
    seed_snis(db)
    node = make_node(db)
    preferred = tiers.ports_for(Tier.full)
    node.occupied_ports = [preferred[0]]
    db.commit()

    assert pick_port(db, node, Tier.full) == preferred[1]


def test_a_node_never_probed_behaves_as_before(db):
    """NULL — это «не смотрели», а не «ничего не занято».

    Ноды, добавленные до появления проверки, не должны менять поведение
    из-за неё.
    """
    seed_snis(db)
    node = make_node(db)
    assert node.occupied_ports is None
    db.commit()

    assert pick_port(db, node, Tier.full) == tiers.ports_for(Tier.full)[0]


def test_our_own_live_inbound_still_blocks_its_port(db):
    """Проверка чужих портов не должна отменить учёт своих."""
    seed_snis(db)
    node = make_node(db)
    preferred = tiers.ports_for(Tier.full)
    make_inbound(db, node, port=preferred[0], state=InboundState.active)
    node.occupied_ports = []
    db.commit()

    assert pick_port(db, node, Tier.full) != preferred[0]


# --- порты канала управления ---------------------------------------------


def test_the_control_port_moves_when_something_holds_it():
    assert provisioning._free_port(62050, {62050}, provisioning.CONTROL_PORT_RANGE) == 62051


def test_the_control_port_stays_put_when_it_is_free():
    assert provisioning._free_port(62050, {443, 8443}, provisioning.CONTROL_PORT_RANGE) == 62050


def test_the_control_reality_port_falls_back_to_another_https_port():
    """Он должен выглядеть как обычный клиентский инбаунд, а не как канал."""
    chosen = provisioning._free_port(8443, {8443}, provisioning.CONTROL_REALITY_FALLBACKS)
    assert chosen in provisioning.CONTROL_REALITY_FALLBACKS
    assert chosen != 8443


def test_everything_taken_keeps_the_preferred_port(db):
    """Пусть bootstrap упадёт на bind с внятным номером порта,
    а не встанет на порт, выбранный от безысходности."""
    everything = set(provisioning.CONTROL_PORT_RANGE)
    assert provisioning._free_port(62050, everything, provisioning.CONTROL_PORT_RANGE) == 62050


@pytest.mark.parametrize("tier", list(Tier))
def test_all_ports_covers_both_classes(tier):
    assert set(tiers.ports_for(tier)) <= set(tiers.all_ports())

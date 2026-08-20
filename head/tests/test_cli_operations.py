"""Команды, на которых стоит меню по ssh.

menu.py — тонкая оболочка: он разбирает JSON этих команд и по нему рисует
списки и выбирает, к чему применить действие. То есть ключи в этом JSON —
контракт между двумя файлами, которые ничто больше не связывает.
Переименуют ключ — меню молча покажет пустой список, и ошибка всплывёт
только на сервере.

Поэтому здесь закрепляется форма вывода, а не только то, что команда
отработала.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import cli
from app.config import get_settings
from app.db import models  # noqa: F401 - registers tables
from app.db.base import Base
from app.db.models.node import Node, NodeStatus
from app.db.models.user import User
from tests.factories import make_assignment, make_inbound, make_node, make_user


@pytest.fixture
def session_factory(monkeypatch):
    monkeypatch.setenv("HEAD_SECRET_KEY", "x")
    monkeypatch.setenv("ADMIN_API_TOKEN", "y")
    get_settings.cache_clear()
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, future=True)
    monkeypatch.setattr(cli, "SessionLocal", factory)
    yield factory
    get_settings.cache_clear()


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


def _json(capsys) -> object:
    return json.loads(capsys.readouterr().out)


# --- контракт с меню -----------------------------------------------------


def test_status_json_carries_every_field_the_menu_prints(db, capsys):
    make_node(db)
    make_user(db)
    db.commit()

    assert cli.status(["--json"]) == 0
    data = _json(capsys)
    assert set(data) == {
        "nodes_total",
        "nodes_active",
        "nodes_isolated",
        "capacity",
        "users_total",
        "users_banned",
        "users_online",
        "assignments_live",
    }


def test_list_nodes_json_carries_every_field_the_menu_prints(db, capsys):
    make_node(db)
    db.commit()

    assert cli.list_nodes(["--json"]) == 0
    rows = _json(capsys)
    assert len(rows) == 1
    assert set(rows[0]) == {
        "id",
        "host",
        "country",
        "status",
        "channel",
        "capacity",
        "uplink_mbit",
        "users",
        "occupied_ports",
    }


def test_an_empty_fleet_is_an_empty_list_not_an_error(db, capsys):
    """Меню рисует список из этого вывода; None или текст его сломают."""
    assert cli.list_nodes(["--json"]) == 0
    assert _json(capsys) == []


def test_occupancy_counts_only_live_assignments(db, capsys):
    node = make_node(db)
    inbound = make_inbound(db, node)
    user, other = make_user(db), make_user(db)
    make_assignment(db, user, inbound)
    released = make_assignment(db, other, inbound)
    db.flush()
    released.released_at = __import__("datetime").datetime.now(__import__("datetime").UTC)
    db.commit()

    cli.list_nodes(["--json"])
    assert _json(capsys)[0]["users"] == 1


# --- изменения -----------------------------------------------------------


def test_capacity_can_be_set_by_host_not_only_by_id(db, capsys):
    """Меню передаёт id, человек за клавиатурой — адрес, который помнит."""
    node = make_node(db)
    db.commit()

    assert cli.node_capacity([node.host, "42"]) == 0
    db.expire_all()
    assert db.get(Node, node.id).capacity == 42


def test_capacity_never_drops_to_zero(db):
    """Ноль означал бы ноду, которая есть, но никого не принимает —
    для этого существует draining, и он честнее."""
    node = make_node(db)
    db.commit()

    cli.node_capacity([str(node.id), "0"])
    db.expire_all()
    assert db.get(Node, node.id).capacity == 1


def test_draining_takes_a_node_out_of_rotation(db):
    node = make_node(db)
    db.commit()

    assert cli.node_status([node.host, "draining"]) == 0
    db.expire_all()
    assert db.get(Node, node.id).status == NodeStatus.draining


def test_delete_needs_confirmation_unless_told_otherwise(db, monkeypatch):
    node = make_node(db)
    host = node.host
    db.commit()

    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    assert cli.node_delete([host]) == 1
    # Запрашиваем заново, а не через прежний объект: удаление происходит в
    # другой сессии, и обновление устаревшего экземпляра упало бы само.
    db.expunge_all()
    assert db.scalar(select(Node).where(Node.host == host)) is not None

    assert cli.node_delete([host, "--yes"]) == 0
    db.expunge_all()
    assert db.scalar(select(Node).where(Node.host == host)) is None


def test_granting_time_puts_an_account_online(db, capsys):
    user = make_user(db)
    db.commit()

    assert cli.grant([str(user.id), "30"]) == 0
    db.expire_all()
    assert db.get(User, user.id).access_expires_at is not None
    assert "30" in capsys.readouterr().out


def test_unknown_targets_fail_rather_than_pretending(db, capsys):
    assert cli.node_capacity(["1.2.3.4", "10"]) == 1
    assert cli.node_status(["1.2.3.4", "active"]) == 1
    assert cli.grant(["not-a-uuid", "10"]) == 1
    assert cli.grant(["11111111-2222-3333-4444-555555555555", "10"]) == 1


def test_a_node_row_survives_a_missing_uplink(db, capsys):
    """uplink_mbit необязателен; меню печатает его в каждой строке."""
    node = make_node(db)
    node.uplink_mbit = None
    db.commit()

    cli.list_nodes(["--json"])
    assert _json(capsys)[0]["uplink_mbit"] is None


def test_occupied_ports_reach_the_menu(db, capsys):
    """Меню предупреждает о чужих портах — значит должно их видеть."""
    node = make_node(db)
    node.occupied_ports = [443, 8443]
    db.commit()

    cli.list_nodes(["--json"])
    assert _json(capsys)[0]["occupied_ports"] == [443, 8443]


def test_a_node_with_no_users_reports_zero_not_null(db, capsys):
    make_node(db)
    db.commit()
    cli.list_nodes(["--json"])
    assert _json(capsys)[0]["users"] == 0


def test_status_counts_only_unexpired_access(db, capsys):
    from datetime import UTC, datetime, timedelta

    online, expired = make_user(db), make_user(db)
    online.access_expires_at = datetime.now(UTC) + timedelta(hours=1)
    expired.access_expires_at = datetime.now(UTC) - timedelta(hours=1)
    db.commit()

    cli.status(["--json"])
    assert _json(capsys)["users_online"] == 1


def test_isolated_nodes_are_counted_because_the_menu_colours_them(db, capsys):
    from app.db.models.node import NodeChannelState

    node = make_node(db)
    node.channel_state = NodeChannelState.isolated
    db.commit()

    cli.status(["--json"])
    assert _json(capsys)["nodes_isolated"] == 1

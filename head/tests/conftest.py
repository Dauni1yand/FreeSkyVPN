import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import models  # noqa: F401 - registers all tables on Base.metadata
from app.db.base import Base
from app.services import keygen


@pytest.fixture
def db() -> Session:
    """An in-memory SQLite database with the full schema applied.

    Good enough to validate models and query logic without a real
    Postgres; UUID/Enum/JSON-specific behaviour still needs the real
    thing, exercised separately once a dev Postgres is available.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(autouse=True)
def fake_keygen(monkeypatch):
    """Avoid shelling out to the xray binary, which tests should not require."""
    counter = {"n": 0}

    def fake_keypair():
        counter["n"] += 1
        return keygen.RealityKeypair(private_key=f"priv{counter['n']}", public_key=f"pub{counter['n']}")

    monkeypatch.setattr(keygen, "generate_reality_keypair", fake_keypair)


@pytest.fixture
def pushes(monkeypatch):
    """Records pushes to nodes instead of performing them.

    Returns the list of nodes pushed to, so tests can assert that a change
    actually reached the node rather than only the database.
    """
    pushed = []

    def fake_push(db, node):
        pushed.append(node)

    monkeypatch.setattr("app.services.config_selector.push_node_config", fake_push)
    return pushed

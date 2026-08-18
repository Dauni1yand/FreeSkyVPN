import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import models  # noqa: F401 - registers all tables on Base.metadata
from app.db.base import Base


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

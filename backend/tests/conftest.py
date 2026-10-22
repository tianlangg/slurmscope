import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://slurmscope:test@localhost:5432/slurmscope_test"
)
test_database_url = os.environ["DATABASE_URL"]
if not test_database_url.rsplit("/", 1)[-1].split("?", 1)[0].endswith("_test"):
    raise RuntimeError(
        "Refusing to run integration tests: DATABASE_URL must name a database ending in _test"
    )


@pytest.fixture()
def client():
    from slurmscope.db import models  # noqa: F401
    from slurmscope.db.database import Base, engine
    from slurmscope.main import app

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as test_client:
        yield test_client
    Base.metadata.drop_all(engine)


@pytest.fixture()
def db_session():
    from slurmscope.db.database import SessionLocal

    with SessionLocal() as session:
        yield session

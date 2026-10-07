from __future__ import annotations

import os
from collections.abc import Iterator
from decimal import Decimal
from uuid import uuid4

import pytest

from unfold.provider import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        api_key="test-secret",
        model="deepseek-flash",
        base_url="https://api.deepseek.com",
        max_cost=Decimal("5"),
        input_rate=Decimal("2"),
        output_rate=Decimal("8"),
    )


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--require-postgres", action="store_true", default=False)


@pytest.fixture
def database(request: pytest.FixtureRequest) -> Iterator[object]:
    url = os.environ.get("UNFOLD_TEST_DATABASE_URL")
    if not url:
        if request.config.getoption("--require-postgres"):
            pytest.fail("UNFOLD_TEST_DATABASE_URL is required for database acceptance")
        pytest.skip("set UNFOLD_TEST_DATABASE_URL to run PostgreSQL integration tests")
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from sqlalchemy.schema import CreateSchema, DropSchema

    from unfold.app.config import AppSettings
    from unfold.app.db import Database
    from unfold.app.migrate import migrate

    schema = "test_" + uuid4().hex
    control = create_engine(url)
    with control.begin() as connection:
        connection.execute(CreateSchema(schema))
    isolated_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
    db = Database(AppSettings(database_url=isolated_url.render_as_string(hide_password=False)))
    try:
        migrate(db)
        yield db
    finally:
        db.close()
        with control.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        control.dispose()

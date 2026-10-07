from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from unfold.app.config import AppSettings


class Database:
    def __init__(self, settings: AppSettings):
        self.engine: Engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=8,
            max_overflow=8,
        )
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self) -> Iterator[Session]:
        # Each request, heartbeat and worker transaction has its own session.
        with self.sessions.begin() as session:
            yield session

    def close(self) -> None:
        self.engine.dispose()

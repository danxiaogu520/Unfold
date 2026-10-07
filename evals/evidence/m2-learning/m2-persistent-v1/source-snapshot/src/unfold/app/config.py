from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, cast

from sqlalchemy.engine import make_url

from unfold.provider import GenerationError


@dataclass(frozen=True)
class AppSettings:
    database_url: str = field(repr=False)
    model_mode: Literal["fake", "deepseek"] = "fake"
    data_directory: Path = Path(".data")
    lease_seconds: float = 30.0
    worker_concurrency: int = 2
    queue_capacity: int = 32
    max_attempts: int = 3
    event_retention_seconds: int = 604800
    host: str = "127.0.0.1"
    port: int = 8000

    def __post_init__(self) -> None:
        try:
            url = make_url(self.database_url)
        except Exception as exc:
            raise GenerationError("configuration", "invalid database URL") from exc
        if url.drivername != "postgresql+psycopg":
            raise GenerationError("configuration", "database URL must use postgresql+psycopg")
        if self.model_mode not in ("fake", "deepseek"):
            raise GenerationError("configuration", "model mode must be fake or deepseek")
        if self.lease_seconds <= 0 or min(self.worker_concurrency, self.queue_capacity) < 1:
            raise GenerationError("configuration", "invalid execution limits")
        if self.host != "127.0.0.1":
            raise GenerationError(
                "configuration", "this release supports local loopback access only"
            )

    @classmethod
    def from_env(cls) -> AppSettings:
        database = os.environ.get("UNFOLD_DATABASE_URL", "").strip()
        if not database:
            raise GenerationError("configuration", "missing UNFOLD_DATABASE_URL")
        mode = os.environ.get("UNFOLD_MODEL_MODE", "fake")
        if mode not in ("fake", "deepseek"):
            raise GenerationError("configuration", "invalid UNFOLD_MODEL_MODE")
        return cls(
            database_url=database,
            model_mode=cast(Literal["fake", "deepseek"], mode),
            data_directory=Path(os.environ.get("UNFOLD_DATA_DIRECTORY", ".data")),
        )


def load_app_env(path: Path) -> None:
    allowed = {"UNFOLD_DATABASE_URL", "UNFOLD_MODEL_MODE", "UNFOLD_DATA_DIRECTORY"}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in allowed:
            raise GenerationError("configuration", "unsupported application environment setting")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        os.environ.setdefault(key, value)

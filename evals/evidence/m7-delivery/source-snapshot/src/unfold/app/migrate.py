from pathlib import Path

from alembic import command
from alembic.config import Config

from unfold.app.db import Database


def migration_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
    return config


def migrate(database: Database, revision: str = "head", *, downgrade: bool = False) -> None:
    config = migration_config()
    with database.engine.begin() as connection:
        # Serialize migrators without introducing an application queue dependency.
        from sqlalchemy import text

        connection.execute(text("SELECT pg_advisory_xact_lock(180000)"))
        config.attributes["connection"] = connection
        if downgrade:
            command.downgrade(config, revision)
        else:
            command.upgrade(config, revision)

from alembic import context

from unfold.app.config import AppSettings
from unfold.app.db import Database
from unfold.app.models import Base

connection = context.config.attributes.get("connection")
if context.is_offline_mode():
    context.configure(
        dialect_name="postgresql",
        target_metadata=Base.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
elif connection is not None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    database = Database(AppSettings.from_env())
    try:
        with database.engine.begin() as connection:
            context.configure(
                connection=connection, target_metadata=Base.metadata, compare_type=True
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        database.close()

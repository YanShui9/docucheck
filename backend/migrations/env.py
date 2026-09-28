"""Alembic runner; credentials stay in the local environment or ignored .env."""

from alembic import context
from sqlalchemy import create_engine, pool

from backend.app.db.connection import database_url
from backend.app.db.models import metadata

config = context.config


def run_migrations_offline() -> None:
    url = database_url(config.get_main_option("sqlalchemy.url"))
    context.configure(url=str(url), target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(config.get_main_option("sqlalchemy.url")),
                           poolclass=pool.NullPool, hide_parameters=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

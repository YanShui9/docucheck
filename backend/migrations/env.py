"""Alembic runner; credentials stay in the local environment or ignored .env."""

import os
from pathlib import Path

from alembic import context
from dotenv import dotenv_values
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import URL

from backend.app.db.models import metadata

config = context.config
ROOT = Path(__file__).resolve().parents[2]


def _database_url() -> str | URL:
    explicit = config.get_main_option("sqlalchemy.url") or os.getenv("DOCUCHECK_DATABASE_URL")
    if explicit:
        return explicit
    password = os.getenv("DOCUCHECK_MYSQL_PASSWORD")
    if not password:
        password = dotenv_values(ROOT / ".env").get("DOCUCHECK_MYSQL_PASSWORD")
    if not password:
        raise RuntimeError("database_password_missing")
    return URL.create("mysql+pymysql", username="docucheck", password=password,
                      host="127.0.0.1", port=3307, database="docucheck")


def run_migrations_offline() -> None:
    url = _database_url()
    context.configure(url=str(url), target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool, hide_parameters=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""Local database connection settings shared by HTTP and migrations."""

import os
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[3]


def database_url(override: str | URL | None = None) -> str | URL:
    if override:
        return override
    explicit = os.getenv("DOCUCHECK_DATABASE_URL")
    if explicit:
        return explicit
    password = os.getenv("DOCUCHECK_MYSQL_PASSWORD")
    if not password:
        password = dotenv_values(ROOT / ".env").get("DOCUCHECK_MYSQL_PASSWORD")
    if not password:
        raise RuntimeError("database_password_missing")
    return URL.create("mysql+pymysql", username="docucheck", password=password,
                      host="127.0.0.1", port=3307, database="docucheck")

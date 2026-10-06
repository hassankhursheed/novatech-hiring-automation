"""A real, throwaway PostgreSQL for the integration tests (testcontainers).

Each test session starts its own PostgreSQL 17 container, creates the three roles exactly as production does
(infra/postgres/init), applies every migration in order (the `-- migrate:up` sections, like dbmate) and the NovaTech
configuration seed. Tests connect as the least-privilege runtime role where the code under test does, roll back
their transaction, and the container is removed at the end: no test ever touches a working database.

To run against an existing database instead (for example a CI service container), set DATABASE_URL_TEST
(backend_app) and DATABASE_URL_OWNER_TEST (novatech_owner). Without Docker and without those variables the
integration tests are skipped, unless REQUIRE_DB_TESTS=1 (CI), in which case they fail.
"""

import os
import secrets
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

POSTGRES_IMAGE = "postgres:17-alpine"  # same image as docker-compose.yml


@dataclass(frozen=True)
class DatabaseUrls:
    app: str  # backend_app: SELECT + EXECUTE api.* only
    owner: str  # novatech_owner: migrations; lets a test move clocks inside its rolled-back transaction


def _db_dir() -> Path:
    candidates = [Path(p) for p in [os.environ.get("NOVATECH_DB_DIR", "")] if p]
    candidates += [Path(__file__).resolve().parents[3] / "db", Path("/db")]
    for candidate in candidates:
        if (candidate / "migrations").is_dir():
            return candidate
    raise RuntimeError("db/migrations not found; set NOVATECH_DB_DIR")


def _up_section(text: str) -> str:
    return text.split("-- migrate:up", 1)[-1].split("-- migrate:down", 1)[0]


def _with_credentials(url: str, user: str, password: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    netloc = f"{quote(user)}:{quote(password)}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def provision(admin_url: str) -> DatabaseUrls:
    """Roles, migrations and seed on an empty database, as infra/postgres/init + dbmate + seed do."""
    db_dir = _db_dir()
    passwords = {role: secrets.token_urlsafe(18) for role in ("novatech_owner", "n8n_app", "backend_app")}
    with psycopg.connect(admin_url, autocommit=True) as conn:
        dbname = conn.info.dbname
        for role, password in passwords.items():
            create = sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}")
            conn.execute(create.format(sql.Identifier(role), sql.Literal(password)))
        conn.execute(sql.SQL("ALTER DATABASE {} OWNER TO novatech_owner").format(sql.Identifier(dbname)))
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(dbname)))
        conn.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO novatech_owner, n8n_app, backend_app").format(
                sql.Identifier(dbname)
            )
        )
        for role in ("n8n_app", "backend_app"):
            conn.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '30s'").format(sql.Identifier(role)))

    owner_url = _with_credentials(admin_url, "novatech_owner", passwords["novatech_owner"])
    with psycopg.connect(owner_url) as conn:
        conn.execute("SET TIME ZONE 'UTC'")
        for migration in sorted((db_dir / "migrations").glob("*.sql")):
            conn.execute(_up_section(migration.read_text(encoding="utf-8")))  # type: ignore[call-overload]
        for seed in sorted((db_dir / "seed").glob("0*.sql")):
            conn.execute(seed.read_text(encoding="utf-8"))  # type: ignore[call-overload]
        conn.commit()
    return DatabaseUrls(app=_with_credentials(admin_url, "backend_app", passwords["backend_app"]), owner=owner_url)


@pytest.fixture(scope="session")
def database_urls() -> Iterator[DatabaseUrls]:
    app_url, owner_url = os.environ.get("DATABASE_URL_TEST"), os.environ.get("DATABASE_URL_OWNER_TEST")
    if app_url and owner_url:
        yield DatabaseUrls(app=app_url, owner=owner_url)
        return
    try:
        try:
            from testcontainers.community.postgres import PostgresContainer
        except ImportError:  # testcontainers < 4.14
            from testcontainers.postgres import PostgresContainer

        container = PostgresContainer(
            POSTGRES_IMAGE, username="postgres", password=secrets.token_urlsafe(18), dbname="novatech", driver=None
        )
        container.with_env("TZ", "UTC")
        container.start()
    except Exception as exc:  # no Docker available
        if os.environ.get("REQUIRE_DB_TESTS") == "1":
            raise
        pytest.skip(f"no test database: Docker is not available for testcontainers ({type(exc).__name__})")
    try:
        yield provision(container.get_connection_url())
    finally:
        container.stop()

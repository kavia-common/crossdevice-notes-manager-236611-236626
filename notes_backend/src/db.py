import os
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence

import psycopg
from psycopg.rows import dict_row


def _required_env(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(
            f"Missing required environment variable {name}. "
            "It must be provided via the container .env."
        )
    return val


def _build_conninfo() -> str:
    """
    Build a PostgreSQL connection string from the DB container contract env vars.

    Contract:
      POSTGRES_URL, POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB, POSTGRES_PORT
    """
    host = _required_env("POSTGRES_URL")
    user = _required_env("POSTGRES_USER")
    password = _required_env("POSTGRES_PASSWORD")
    db = _required_env("POSTGRES_DB")
    port = _required_env("POSTGRES_PORT")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


# PUBLIC_INTERFACE
@contextmanager
def get_db() -> Iterator[psycopg.Connection]:
    """Yield a psycopg connection (dict rows) with automatic commit/rollback."""
    conninfo = _build_conninfo()
    with psycopg.connect(conninfo, row_factory=dict_row) as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


# PUBLIC_INTERFACE
def fetch_one(conn: psycopg.Connection, sql: str, params: Optional[Sequence[Any]] = None) -> Optional[Dict[str, Any]]:
    """Fetch a single row as a dict, or None."""
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row  # type: ignore[return-value]


# PUBLIC_INTERFACE
def fetch_all(conn: psycopg.Connection, sql: str, params: Optional[Sequence[Any]] = None) -> List[Dict[str, Any]]:
    """Fetch all rows as a list of dicts."""
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        rows = cur.fetchall()
        return list(rows)  # type: ignore[arg-type]


# PUBLIC_INTERFACE
def execute(conn: psycopg.Connection, sql: str, params: Optional[Sequence[Any]] = None) -> int:
    """Execute a statement and return affected row count."""
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.rowcount


# PUBLIC_INTERFACE
def execute_returning_one(
    conn: psycopg.Connection, sql: str, params: Optional[Sequence[Any]] = None
) -> Dict[str, Any]:
    """Execute a RETURNING query and return the single resulting row dict."""
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        if not row:
            raise RuntimeError("Expected a row to be returned, got none.")
        return row  # type: ignore[return-value]

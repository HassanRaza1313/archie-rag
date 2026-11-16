"""Databases, read-only: each row a query returns becomes a document, and its columns stay as structured fields.

Account: a SQLAlchemy URL (``postgresql+psycopg://reader:...@host/db``, ``mysql+pymysql://...``,
``sqlite:////data/app.db``; other databases with their driver installed). Location: one SELECT (or WITH ... SELECT)
statement. The first column is the row's id; a column named title, name or subject titles the document.

Read-only three ways: only a single SELECT is accepted, the session is set read-only where the database allows it
(PostgreSQL, MySQL, SQLite), and every transaction is rolled back. Give the account a read-only database user too.
Needs the ``connectors`` extra (SQLAlchemy, psycopg, PyMySQL).
"""

from __future__ import annotations

import datetime as dt
import decimal
import re
from functools import partial
from pathlib import PurePath
from typing import Any

from ragkit.connectors import (
    ACCOUNT_KINDS,
    CONNECTORS,
    Account,
    AccountKind,
    Cache,
    Connector,
    ConnectorError,
    Item,
    version_of,
)

TITLE_COLUMNS = ("title", "name", "subject", "summary", "heading")
FIELD_MAX_CHARS = 200  # longer text goes in the document body, not in the structured fields
TIMEOUT_S = 120
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)


def read_only_query(sql: str) -> str:
    """The query if it is one SELECT (or WITH ... SELECT) statement; anything else is refused."""
    bare = _COMMENTS.sub(" ", sql).strip().rstrip(";").strip()
    if not bare:
        raise ConnectorError("enter a SELECT query, e.g. SELECT id, title, body FROM articles")
    if ";" in bare:
        raise ConnectorError("use a single SELECT statement")
    if not re.match(r"(select|with)\b", bare, re.IGNORECASE):
        raise ConnectorError("only SELECT queries can be used: Archie never changes your database")
    if re.search(r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call|exec|execute|"
                 r"into)\b", bare, re.IGNORECASE):  # fmt: skip
        raise ConnectorError("the query may only read: remove statements that write or change the database")
    return bare


def _engine(account: Account) -> Any:
    try:
        from sqlalchemy import create_engine, event
        from sqlalchemy.engine import make_url
    except ImportError as exc:
        raise ConnectorError("databases need the 'connectors' extra: pip install 'ragkit[connectors]'") from exc
    try:
        url = make_url(account.secret.get("url", ""))
    except Exception as exc:
        raise ConnectorError("the database address isn't a valid URL (e.g. postgresql+psycopg://...)") from exc
    backend = url.get_backend_name()
    if backend == "sqlite":
        if url.database in (None, "", ":memory:"):
            raise ConnectorError("give the path of the SQLite file")
        url = url.set(database=f"file:{url.database}?mode=ro", query={**url.query, "uri": "true"})
    engine = create_engine(url, pool_pre_ping=True)

    @event.listens_for(engine, "begin")
    def _read_only(connection: Any) -> None:
        if backend == "postgresql":
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = {TIMEOUT_S * 1000}")
        elif backend in ("mysql", "mariadb"):
            connection.exec_driver_sql("SET SESSION TRANSACTION READ ONLY")

    return engine


def _value(value: Any) -> Any:
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, bytes | bytearray | memoryview):
        return None  # binary columns aren't text
    return value


def row_document(columns: list[str], row: tuple[Any, ...]) -> tuple[str, str, dict[str, Any]]:
    """(id, markdown, fields) for one row: short values become fields, long text becomes the body."""
    values = {column: _value(value) for column, value in zip(columns, row, strict=True)}
    row_id = str(values[columns[0]])
    title_column = next((c for c in columns if c.lower() in TITLE_COLUMNS and values[c]), None)
    title = str(values[title_column]) if title_column else row_id
    fields: dict[str, Any] = {}
    lines, body = [f"# {title}", ""], []
    for column, value in values.items():
        if value is None or value == "":
            continue
        if isinstance(value, str) and (len(value) > FIELD_MAX_CHARS or "\n" in value):
            body += [f"## {column}", "", value, ""]
        else:
            fields[column] = value
            lines.append(f"- **{column}**: {value}")
    return row_id, "\n".join([*lines, "", *body]).strip() + "\n", fields


def check(account: Account) -> str:
    engine = _engine(account)
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
            connection.rollback()
    except ConnectorError:
        raise
    except Exception as exc:
        raise ConnectorError(f"couldn't connect: {type(exc).__name__}: {str(exc).splitlines()[0]}") from exc
    finally:
        engine.dispose()
    return f"connected to {engine.dialect.name}"


def fetch(account: Account, location: str, cache: Cache, limit: int) -> list[Item]:
    from sqlalchemy import text

    query = read_only_query(location)
    engine = _engine(account)
    url = engine.url
    database = (url.database or "").removeprefix("file:").split("?")[0]
    # Citations show this address: a SQLite file by its name only, so the server's folders stay private.
    base = (
        f"sqlite://{PurePath(database).name}"
        if url.get_backend_name() == "sqlite"
        else f"{url.get_backend_name()}://{url.host or ''}/{database}"
    )
    items: list[Item] = []
    try:
        with engine.connect() as connection:
            result = connection.execution_options(stream_results=True).execute(text(query))
            columns = list(result.keys())
            for row in result:
                row_id, markdown, fields = row_document(columns, tuple(row))
                data = markdown.encode()
                items.append(cache.keep(f"{base}/{row_id}", version_of(markdown), ".md", partial(bytes, data), fields))
                if len(items) >= limit:
                    break
            connection.rollback()
    except ConnectorError:
        raise
    except Exception as exc:
        raise ConnectorError(f"the query failed: {type(exc).__name__}: {str(exc).splitlines()[0]}") from exc
    finally:
        engine.dispose()
    return items


ACCOUNT_KINDS["database"] = AccountKind(
    "Database (read-only)",
    settings=(),
    secrets=(("url", "Database address, e.g. postgresql+psycopg://reader:password@host:5432/db"),),
    check=check,
)
CONNECTORS["database"] = Connector("database", "A SELECT query; the first column is each row's id", fetch)

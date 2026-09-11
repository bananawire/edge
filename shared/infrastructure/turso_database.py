"""TursoDatabase — peewee.Database backed by the official libsql package.

This is the production replacement for ``peewee.SqliteDatabase``. The edge
service talks to a remote Turso (libSQL) database when ``EDGE_TURSO_URL`` is
configured, and falls back to a local libSQL file (via the same client) for
unit tests and offline development.

Peewee does not ship a ``playhouse.libsql`` backend, so we subclass
``peewee.Database`` directly and route ``_connect``/``execute_sql``/the
transaction hooks through the DB-API 2.0 ``libsql`` client. The libsql client
itself is SQLite-compatible, so all schema- and SQL-level features we rely on
(``PRAGMA table_info``, ``sqlite_master``, ``ALTER TABLE ADD COLUMN``,
``BEGIN``/``COMMIT``/``ROLLBACK``) work identically against remote Turso or
against a local file.

SQLite-specific transaction lock modifiers (``IMMEDIATE``, ``EXCLUSIVE``) are
intentionally dropped: Turso serializes writes server-side via libsql, so the
client-side locking is neither needed nor supported by the remote protocol.
Application code that needs strict read-then-write atomicity must rely on a
conditional UPDATE rather than ``BEGIN IMMEDIATE``.
"""

from __future__ import annotations

import datetime
import logging
import sqlite3 as _stdlib_sqlite3
from typing import Any, Iterable, Optional

import libsql
import peewee

logger = logging.getLogger(__name__)


def _adapt_param(value: Any) -> Any:
    """Convert Python values into types libsql's parameter binder accepts.

    libsql accepts ints, floats, strings, bytes, and ``None``. It does NOT
    auto-adapt ``datetime``/``date``/``time`` instances the way the stdlib
    ``sqlite3`` module does (peewee registers adapters against ``sqlite3`` at
    import time, but those do not propagate to libsql). We mirror the
    stdlib adapters here so any field type Peewee passes through works
    transparently.
    """
    if value is None or isinstance(value, (bool, int, float, str, bytes)):
        return value
    if isinstance(value, datetime.datetime):
        # Match stdlib sqlite3 adapter: ``isoformat(' ')``.
        return value.isoformat(" ")
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, datetime.time):
        return value.isoformat()
    return value


def _adapt_params(params: Optional[Iterable[Any]]) -> tuple:
    """Adapt a Peewee-provided parameter tuple to a libsql-compatible one."""
    if params is None:
        return ()
    if isinstance(params, dict):
        return {key: _adapt_param(value) for key, value in params.items()}
    return tuple(_adapt_param(p) for p in params)


# libsql raises plain ``ValueError`` for every database-level failure
# (constraint violation, missing table, syntax error). The message text is
# what SQLite emits, so we mirror peewee's translation policy: any
# "constraint failed" message becomes ``IntegrityError``; anything else
# surfaces as ``OperationalError`` (most likely "no such table/column" or
# "syntax error" reported by the embedded SQLite parser).
_CONSTRAINT_KEYWORDS = ("constraint failed", "UNIQUE", "NOT NULL", "FOREIGN KEY", "CHECK")


def _translate_libsql_error(exc: ValueError) -> Exception:
    """Map a libsql ``ValueError`` to the matching peewee exception class."""
    message = str(exc)
    if any(keyword in message for keyword in _CONSTRAINT_KEYWORDS):
        return peewee.IntegrityError(exc)
    return peewee.OperationalError(exc)


def _is_remote_turso_url(url: str) -> bool:
    """Return True when ``url`` points at a remote libsql:// Turso instance."""
    return url.startswith("libsql://") or url.startswith("https://")


class _LibsqlCursorAdapter:
    """Tiny adapter exposing the cursor attributes Peewee expects.

    ``libsql.Cursor`` is already DB-API 2.0 compliant; this wrapper exists
    only to normalise attribute access (the upstream ``cursor`` is a builtin
    type and does not expose every Python attribute reliably across versions).
    """

    __slots__ = ("_cursor",)

    def __init__(self, cursor) -> None:
        self._cursor = cursor

    def execute(self, sql: str, params: Optional[Iterable[Any]] = None):
        adapted = _adapt_params(params)
        if not adapted:
            return self._cursor.execute(sql)
        return self._cursor.execute(sql, adapted)

    def executemany(self, sql: str, params: Iterable[Iterable[Any]]):
        adapted = [_adapt_params(p) for p in params]
        return self._cursor.executemany(sql, adapted)

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def fetchmany(self, size: None = None):
        if size is None:
            return self._cursor.fetchmany()
        return self._cursor.fetchmany(size)

    def close(self) -> None:
        close = getattr(self._cursor, "close", None)
        if callable(close):
            close()

    @property
    def description(self):
        return self._cursor.description

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def arraysize(self):
        return getattr(self._cursor, "arraysize", 1)

    @arraysize.setter
    def arraysize(self, value: int) -> None:
        try:
            self._cursor.arraysize = value
        except (AttributeError, TypeError):
            pass


class TursoDatabase(peewee.Database):
    """Peewee ``Database`` implementation that delegates to ``libsql``.

    Parameters mirror the official libsql quickstart
    (https://docs.turso.tech/sdk/python/quickstart):

    - ``database``: either a ``libsql://...`` URL (remote Turso) or a local
      file path / ``":memory:"`` for offline mode.
    - ``auth_token``: JWT for the remote Turso database. Ignored when
      ``database`` is a local path or in-memory target.
    """

    # Peewee's SQLite-derived field/operation mapping. Turso/libSQL is wire-
    # compatible with SQLite, so the SQLite mapping works unchanged.
    field_types = {
        "BIGAUTO": peewee.FIELD.AUTO,
        "BIGINT": peewee.FIELD.INT,
        "BOOL": peewee.FIELD.INT,
        "DOUBLE": peewee.FIELD.FLOAT,
        "SMALLINT": peewee.FIELD.INT,
        "UUID": peewee.FIELD.TEXT,
    }
    operations = {
        "LIKE": "GLOB",
        "ILIKE": "LIKE",
    }
    index_schema_prefix = True
    limit_max = -1
    truncate_table = False
    # peewee treats ``server_version`` as a SQLite-style 3-tuple. libsql
    # exposes ``sqlite_version_info`` which we can compare directly.
    server_version = libsql.sqlite_version_info

    def __init__(
        self,
        database: str,
        auth_token: str = "",
        **kwargs: Any,
    ) -> None:
        self._auth_token = auth_token or ""
        super().__init__(database, **kwargs)

    # --- connection lifecycle -------------------------------------------

    def _connect(self):
        """Open a libsql connection. Peewee owns the returned object."""
        # ``isolation_level=None`` puts the driver in autocommit mode, exactly as
        # peewee's own SqliteDatabase does. Without it libsql opens an implicit
        # transaction on the first write and every statement issued outside an
        # explicit ``atomic()`` block is silently lost when the connection
        # closes. Explicit transactions still work: ``begin()`` issues BEGIN.
        try:
            conn = libsql.connect(
                self.database,
                auth_token=self._auth_token,
                isolation_level=None,
            )
        except TypeError:
            # Older libsql builds may not accept these keywords; fall back to
            # positional connect for local files only and commit eagerly.
            if self._auth_token and _is_remote_turso_url(self.database):
                raise
            conn = libsql.connect(self.database)
            conn.isolation_level = None
        return conn

    def _set_server_version(self, conn) -> None:
        """Peewee expects a SQLite 3-tuple. libsql ships that natively."""
        self.server_version = libsql.sqlite_version_info

    def _initialize_connection(self, conn) -> None:
        """No-op: libsql defaults match the schema-generation needs."""
        return None

    def _close(self, conn) -> None:
        try:
            conn.close()
        except Exception:  # pragma: no cover - defensive cleanup
            logger.exception("Failed to close libsql connection cleanly")

    # --- SQL execution ---------------------------------------------------

    def execute_sql(self, sql, params=None, commit=None):
        """Run ``sql`` against the live libsql connection and return a cursor.

        libsql surfaces every database error as ``ValueError`` regardless of
        the underlying cause (constraint violation, missing table, syntax
        error). Peewee's DB-API ``__exception_wrapper__`` only translates
        exceptions whose class name is in its ``EXCEPTIONS`` mapping; because
        ``ValueError`` is not, the wrapper is a no-op for libsql. We
        translate the libsql ``ValueError`` here into the appropriate
        peewee exception so application/repo code that catches
        ``IntegrityError`` / ``OperationalError`` keeps working unchanged.
        """
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug((sql, params))
        cursor = self.cursor()
        adapter = cursor if isinstance(cursor, _LibsqlCursorAdapter) else _LibsqlCursorAdapter(cursor)
        try:
            adapter.execute(sql, params)
        except ValueError as exc:
            raise _translate_libsql_error(exc) from exc
        return cursor

    def cursor(self, named_cursor=None):
        """Return a cursor Peewee can drive.

        ``named_cursor`` is accepted for API compatibility; libsql cursors are
        always tuple-based.
        """
        return _LibsqlCursorAdapter(super().cursor())

    # --- transactions ---------------------------------------------------

    def begin(self, lock_type=None):
        """Begin a transaction.

        ``lock_type`` (e.g. ``"IMMEDIATE"``, ``"EXCLUSIVE"``) is intentionally
        ignored. libsql serializes writes server-side and the SQLite-specific
        modifier is not part of the Turso protocol. Application code that
        needs read-then-write atomicity must use a conditional UPDATE.

        libsql rejects ``BEGIN`` when the connection is already in an
        implicit transaction (e.g. after the first ``INSERT`` of a unit of
        work). Peewee always calls ``begin`` at the top of ``atomic()``, so
        we detect the already-open transaction state and skip the SQL.
        """
        if lock_type:
            logger.debug(
                "Dropping SQLite-specific BEGIN %s lock modifier; "
                "Turso serializes writes server-side.",
                lock_type,
            )
        conn = self.connection()
        if getattr(conn, "in_transaction", False):
            return
        self.execute_sql("BEGIN")

    def commit(self):
        self.execute_sql("COMMIT")

    def rollback(self):
        self.execute_sql("ROLLBACK")

    # --- introspection --------------------------------------------------

    def get_tables(self, schema=None):
        """Return all user tables in the ``main`` schema (or ``schema``)."""
        sql = (
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        if schema:
            sql = (
                "SELECT name FROM \"%s\".sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name" % schema
            )
        return [row[0] for row in self.execute_sql(sql).fetchall()]

    def get_columns(self, table, schema=None):
        """Return ``ColumnMetadata`` instances for ``table``."""
        schema_prefix = '"%s".' % schema if schema else ""
        cursor = self.execute_sql("PRAGMA %stable_info(\"%s\")" % (schema_prefix, table))
        rows = cursor.fetchall()
        # PRAGMA table_info returns: cid, name, type, notnull, dflt_value, pk
        return [
            peewee.ColumnMetadata(
                name=row[1],
                data_type=row[2],
                null=not bool(row[3]),
                primary_key=bool(row[5]),
                table=table,
                default=row[4],
            )
            for row in rows
        ]

    # --- defaults -------------------------------------------------------

    def last_insert_id(self, cursor, query_type=None):
        return cursor.lastrowid

    def rows_affected(self, cursor):
        return cursor.rowcount

    # --- upsert / ON CONFLICT ------------------------------------------

    # Turso exposes the same ON CONFLICT ... DO UPDATE syntax that
    # SQLite >= 3.24 supports, so we mirror ``peewee.SqliteDatabase``'s
    # behavior here. Without these overrides peewee raises
    # ``NotImplementedError`` for ``Model.insert(...).on_conflict(...)``.

    def conflict_statement(self, on_conflict, query):
        action = on_conflict._action.lower() if on_conflict._action else ""
        if action and action not in ("nothing", "update"):
            return peewee.SQL("INSERT OR %s" % on_conflict._action.upper())

    def conflict_update(self, oc, query):
        if self.server_version < (3, 24, 0) and any(
            (oc._preserve, oc._update, oc._where, oc._conflict_target, oc._conflict_constraint)
        ):
            raise ValueError(
                "SQLite does not support specifying which values to preserve or update."
            )

        action = oc._action.lower() if oc._action else ""
        if action and action not in ("nothing", "update", ""):
            return

        if action == "nothing":
            return peewee.SQL("ON CONFLICT DO NOTHING")

        if not oc._update and not oc._preserve:
            raise ValueError(
                "If you are not performing any updates (or preserving any "
                "INSERTed values), then the conflict resolution action should "
                "be set to \"NOTHING\"."
            )
        if oc._conflict_constraint:
            raise ValueError(
                "SQLite does not support specifying named constraints for "
                "conflict resolution."
            )
        if not oc._conflict_target:
            raise ValueError(
                "SQLite requires that a conflict target be specified when "
                "doing an upsert."
            )

        return self._build_on_conflict_update(oc, query)


# Re-export sqlite3.Binary for parity with playhouse.sqlite_ext. Peewee
# imports ``Binary`` via the database class on some code paths.
Binary = _stdlib_sqlite3.Binary


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "smoke.db")
        db = TursoDatabase(path)
        try:
            db.connect(reuse_if_open=True)
            db.execute_sql("CREATE TABLE smoke (id INTEGER PRIMARY KEY, value TEXT)")
            with db.atomic():
                db.execute_sql("INSERT INTO smoke (value) VALUES (?)", ("hello",))
                db.execute_sql("INSERT INTO smoke (value) VALUES (?)", ("world",))
            rows = db.execute_sql("SELECT id, value FROM smoke ORDER BY id").fetchall()
            assert rows == [(1, "hello"), (2, "world")], rows
            columns = [c.name for c in db.get_columns("smoke")]
            assert columns == ["id", "value"], columns
            print("turso_database smoke test passed:", rows)
        finally:
            db.close()
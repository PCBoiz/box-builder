"""Storage. SQLite for a local/Docker run, Postgres (Neon) when DATABASE_URL is a postgres URL.

The UNIQUE index on box_items.object_id is the hard guarantee that an object can never sit in two boxes.
Every write transaction also takes one global lock (SQLite: BEGIN IMMEDIATE, Postgres: advisory lock), so
"check it is free, then take it" can never interleave with another user."""
import contextlib
import os
import re
import sqlite3
import threading


class IntegrityError(Exception):
    """A UNIQUE / FOREIGN KEY rule refused the write (same meaning on SQLite and Postgres)."""


class Row:
    """Row that reads by position or by column name, like sqlite3.Row (used for Postgres)."""
    __slots__ = ("_k", "_v")

    def __init__(self, keys, values):
        self._k, self._v = tuple(keys), tuple(values)

    def __getitem__(self, i):
        return self._v[i] if isinstance(i, (int, slice)) else self._v[self._k.index(i)]

    def __iter__(self):
        return iter(self._v)

    def __len__(self):
        return len(self._v)

    def keys(self):
        return list(self._k)


def is_postgres(target: str) -> bool:
    return target.startswith(("postgres://", "postgresql://"))


def _to_pg(sql: str) -> str:
    """'?' placeholders -> '%s' (outside quoted text only); literal '%' doubled for psycopg."""
    out, in_quote = [], False
    for ch in sql:
        if ch == "'":
            in_quote = not in_quote
        if ch == "%":
            out.append("%%")
        elif ch == "?" and not in_quote:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


class _Cur:
    def __init__(self, cur):
        self._c = cur

    def fetchone(self):
        return self._c.fetchone()

    def fetchall(self):
        return self._c.fetchall()

    def __iter__(self):
        return iter(self._c.fetchall())

    @property
    def rowcount(self):
        return self._c.rowcount


class Conn:
    def __init__(self, raw, pg: bool, pool=None):
        self.raw, self.pg, self._pool = raw, pg, pool
        if pg:
            import psycopg
            self._integrity = psycopg.errors.IntegrityError
        else:
            self._integrity = sqlite3.IntegrityError

    def execute(self, sql: str, params=()):
        if self.pg:
            sql = _to_pg(sql)
        try:
            return _Cur(self.raw.execute(sql, params))
        except self._integrity as e:
            raise IntegrityError(str(e)) from e

    def executemany(self, sql: str, rows) -> None:
        """Many rows in one go. On Postgres the rows travel in one pipeline instead of one network round trip each,
        which matters when the database is hosted far away (thousands of single INSERTs took longer than the sync tool waits)."""
        rows = list(rows)
        if not rows:
            return
        try:
            if self.pg:
                with self.raw.cursor() as cur:
                    cur.executemany(_to_pg(sql), rows)
            else:
                self.raw.executemany(sql, rows)
        except self._integrity as e:
            raise IntegrityError(str(e)) from e

    def close(self):
        if self.raw is None:                       # closing twice is harmless
            return
        raw, self.raw = self.raw, None
        if self._pool is not None:
            self._pool.putconn(raw)
        else:
            raw.close()


# ----------------------------------------------------------------------------- connecting
_pools: dict = {}
_pools_lock = threading.Lock()


def _pg_row_factory(cursor):
    keys = [c.name for c in cursor.description] if cursor.description else []
    # A database created as SQL_ASCII hands text back as bytes; Neon is UTF8, but decode defensively anyway.
    return lambda values: Row(keys, [v.decode("utf-8") if isinstance(v, (bytes, bytearray)) else v for v in values])


def _pool(url: str):
    with _pools_lock:
        if url not in _pools:
            from psycopg_pool import ConnectionPool
            _pools[url] = ConnectionPool(
                url, min_size=1, max_size=int(os.getenv("DB_POOL_MAX", "10")), open=True, timeout=30,
                check=ConnectionPool.check_connection,       # Neon closes idle connections; reconnect quietly
                kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": _pg_row_factory})
        return _pools[url]


def close_pools() -> None:
    with _pools_lock:
        pools, _pools_copy = list(_pools.values()), _pools.clear()
    for pool in pools:
        try:
            pool.close()
        except Exception:
            pass


def connect(target: str) -> Conn:
    if is_postgres(target):
        pool = _pool(target)
        return Conn(pool.getconn(), True, pool)
    conn = sqlite3.connect(target, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return Conn(conn, False)


def _schema(pg: bool) -> list[str]:
    pk = "BIGSERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"
    real = "DOUBLE PRECISION" if pg else "REAL"
    return [
        "CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v BIGINT NOT NULL)",
        "INSERT INTO meta(k, v) VALUES ('rev', 0) ON CONFLICT (k) DO NOTHING",
        f"""CREATE TABLE IF NOT EXISTS boxes(
              id          {pk},
              module      TEXT NOT NULL,
              label       TEXT NOT NULL,
              theme       TEXT NOT NULL DEFAULT '',
              categories  TEXT NOT NULL DEFAULT '[]',
              code        TEXT NOT NULL DEFAULT '',
              anchor      TEXT NOT NULL DEFAULT '',
              count       INTEGER NOT NULL,
              status      TEXT NOT NULL,
              message     TEXT NOT NULL DEFAULT '',
              owner       TEXT NOT NULL DEFAULT '',
              created_at  {real} NOT NULL,
              updated_at  {real} NOT NULL)""",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_box_name ON boxes(module, label)",
        """CREATE TABLE IF NOT EXISTS box_items(
              box_id     BIGINT NOT NULL REFERENCES boxes(id) ON DELETE CASCADE,
              pos        INTEGER NOT NULL,
              object_id  TEXT NOT NULL,
              qty        INTEGER NOT NULL DEFAULT 1,
              locked     INTEGER NOT NULL DEFAULT 0,
              source     TEXT NOT NULL DEFAULT 'llm',
              PRIMARY KEY(box_id, pos))""",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_object_once ON box_items(object_id)",
        "CREATE TABLE IF NOT EXISTS catalog_snapshot(k TEXT PRIMARY KEY, v TEXT NOT NULL)",
        # which approved boxes already exist in Scenario Manager (written back by the userscript after a confirmed create)
        f"""CREATE TABLE IF NOT EXISTS sm_push(
              box_id     BIGINT PRIMARY KEY REFERENCES boxes(id) ON DELETE CASCADE,
              sm_box_id  TEXT NOT NULL,
              pushed_at  {real} NOT NULL)""",
        # added_at: when the boxes that used this object were made (epoch seconds); 0 = unknown, which never expires
        f"""CREATE TABLE IF NOT EXISTS external_used(
              object_id TEXT PRIMARY KEY,
              note      TEXT NOT NULL DEFAULT '',
              added_at  {real} NOT NULL DEFAULT 0)""",
        # what Scenario Manager says its boxes hold, as last read by the userscript (read only, active boxes only, no people)
        f"""CREATE TABLE IF NOT EXISTS sm_boxes(
              sm_box_id     TEXT PRIMARY KEY,
              title         TEXT NOT NULL DEFAULT '',
              status        TEXT NOT NULL DEFAULT '',
              status_label  TEXT NOT NULL DEFAULT '',
              created_at    TEXT NOT NULL DEFAULT '',
              seen_at       {real} NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS sm_box_items(
              sm_box_id  TEXT NOT NULL REFERENCES sm_boxes(sm_box_id) ON DELETE CASCADE,
              object_id  TEXT NOT NULL,
              qty        INTEGER NOT NULL DEFAULT 1,
              PRIMARY KEY(sm_box_id, object_id))""",
        "CREATE INDEX IF NOT EXISTS ix_sm_box_items_object ON sm_box_items(object_id)",
        # on/off switches for the automations (only rows that were ever changed; defaults live in app/automation.py)
        f"""CREATE TABLE IF NOT EXISTS automations(
              k          TEXT PRIMARY KEY,
              enabled    INTEGER NOT NULL,
              config     TEXT NOT NULL DEFAULT '{{}}',
              updated_at {real} NOT NULL)""",
        f"""CREATE TABLE IF NOT EXISTS automation_log(
              id    {pk},
              at    {real} NOT NULL,
              kind  TEXT NOT NULL,
              msg   TEXT NOT NULL)""",
    ]


# columns added after the first release: older databases get them on start
_ADD_COLUMNS = [("boxes", "code", "TEXT NOT NULL DEFAULT ''"), ("boxes", "anchor", "TEXT NOT NULL DEFAULT ''"),
                ("external_used", "added_at", "{real} NOT NULL DEFAULT 0")]


def init(target: str) -> None:
    pg = is_postgres(target)
    if not pg:
        d = os.path.dirname(target)
        if d:
            os.makedirs(d, exist_ok=True)
    conn = connect(target)
    try:
        if not pg:
            conn.execute("PRAGMA journal_mode=WAL")
        for stmt in _schema(pg):
            conn.execute(stmt)
        for table, col, ddl in _ADD_COLUMNS:
            ddl = ddl.replace("{real}", "DOUBLE PRECISION" if pg else "REAL")
            if pg:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {ddl}")
            elif col not in [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    finally:
        conn.close()


@contextlib.contextmanager
def tx(conn: Conn):
    """One writer at a time. Bumps the global revision so open pages know to refresh."""
    conn.execute("BEGIN" if conn.pg else "BEGIN IMMEDIATE")
    try:
        if conn.pg:
            conn.execute("SELECT pg_advisory_xact_lock(727001)")
        yield conn
        conn.execute("UPDATE meta SET v = v + 1 WHERE k = 'rev'")
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


def rev(conn: Conn) -> int:
    return conn.execute("SELECT v FROM meta WHERE k='rev'").fetchone()[0]

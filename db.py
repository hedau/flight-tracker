"""Saving and loading routes and prices.

Two kinds of database are supported with the same code:
- On your Mac (no DATABASE_URL set): a local SQLite file, flights.db.
- In the cloud (DATABASE_URL set): a PostgreSQL database such as Neon.

Queries are written with "?" placeholders; for PostgreSQL they are
rewritten to "%s", which is what the psycopg library expects.
"""

import os
import sqlite3
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SQLITE_PATH = os.environ.get(
    "SQLITE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "flights.db")
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS routes (
    id          {id_column},
    origin      TEXT NOT NULL,
    destination TEXT NOT NULL,
    trip_type   TEXT NOT NULL,
    depart_date TEXT NOT NULL,
    return_date TEXT,
    airlines    TEXT,
    airline_names TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prices (
    id         {id_column},
    route_id   INTEGER NOT NULL REFERENCES routes(id) ON DELETE CASCADE,
    checked_on TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    price      INTEGER,
    airline    TEXT,
    source     TEXT NOT NULL,
    error      TEXT,
    UNIQUE (route_id, checked_on)
);
"""


def using_postgres():
    return bool(DATABASE_URL)


@contextmanager
def connect():
    """Open a connection, commit if everything worked, and always close it."""
    if using_postgres():
        import psycopg  # only needed in the cloud
        from psycopg.rows import dict_row

        conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    else:
        conn = sqlite3.connect(SQLITE_PATH)
        conn.row_factory = lambda cursor, row: {
            col[0]: row[i] for i, col in enumerate(cursor.description)
        }
        conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _sql(query):
    return query.replace("?", "%s") if using_postgres() else query


def query(sql, params=()):
    with connect() as conn:
        return conn.execute(_sql(sql), params).fetchall()


def execute(sql, params=()):
    """Run a statement; returns the first row if it has RETURNING, else None."""
    with connect() as conn:
        cursor = conn.execute(_sql(sql), params)
        return cursor.fetchone() if cursor.description else None


def init():
    id_column = (
        "INTEGER GENERATED ALWAYS AS IDENTITY PRIMARY KEY"
        if using_postgres()
        else "INTEGER PRIMARY KEY AUTOINCREMENT"
    )
    with connect() as conn:
        for statement in SCHEMA.format(id_column=id_column).split(";"):
            if statement.strip():
                conn.execute(statement)
        # Databases created before these columns existed need them added.
        for column in ("airlines", "airline_names"):
            if using_postgres():
                conn.execute("ALTER TABLE routes ADD COLUMN IF NOT EXISTS %s TEXT" % column)
            elif column not in {c["name"] for c in conn.execute("PRAGMA table_info(routes)")}:
                conn.execute("ALTER TABLE routes ADD COLUMN %s TEXT" % column)


# --- Routes -----------------------------------------------------------------

def add_route(origin, destination, trip_type, depart_date, return_date,
              airlines, airline_names, created_at):
    """airlines is a comma-separated list of airline codes like "DL,UA", or None
    for any airline; airline_names holds their names, like "Delta,United"."""
    row = execute(
        "INSERT INTO routes (origin, destination, trip_type, depart_date, return_date,"
        " airlines, airline_names, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (origin, destination, trip_type, depart_date, return_date, airlines, airline_names, created_at),
    )
    return row["id"]


def get_route(route_id):
    rows = query("SELECT * FROM routes WHERE id = ?", (route_id,))
    return rows[0] if rows else None


def list_routes():
    return query("SELECT * FROM routes ORDER BY active DESC, depart_date, id")


def active_routes():
    return query("SELECT * FROM routes WHERE active = 1 ORDER BY id")


def deactivate_route(route_id):
    execute("UPDATE routes SET active = 0 WHERE id = ?", (route_id,))


def delete_route(route_id):
    execute("DELETE FROM prices WHERE route_id = ?", (route_id,))
    execute("DELETE FROM routes WHERE id = ?", (route_id,))


# --- Prices -----------------------------------------------------------------

def has_price_for(route_id, checked_on):
    rows = query(
        "SELECT 1 FROM prices WHERE route_id = ? AND checked_on = ? AND price IS NOT NULL",
        (route_id, checked_on),
    )
    return bool(rows)


def save_price(route_id, checked_on, checked_at, price, airline, source, error):
    """Store one price per route per day. Checking again the same day replaces it."""
    execute(
        "INSERT INTO prices (route_id, checked_on, checked_at, price, airline, source, error)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (route_id, checked_on) DO UPDATE SET"
        " checked_at = excluded.checked_at, price = excluded.price,"
        " airline = excluded.airline, source = excluded.source, error = excluded.error",
        (route_id, checked_on, checked_at, price, airline, source, error),
    )


def prices_for(route_id):
    return query(
        "SELECT checked_on, checked_at, price, airline, source, error"
        " FROM prices WHERE route_id = ? ORDER BY checked_on",
        (route_id,),
    )

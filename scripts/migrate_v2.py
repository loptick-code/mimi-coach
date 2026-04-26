"""Ajoute les colonnes feedback / validation / pace_adjustment a la DB existante.

Idempotent : on detecte les colonnes manquantes et on les ajoute.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from app.db import engine, init_db


def _add_column_if_missing(conn, table: str, column: str, type_sql: str, default_sql: str | None = None) -> None:
    inspector = inspect(conn)
    cols = {c["name"] for c in inspector.get_columns(table)}
    if column in cols:
        print(f"  - {table}.{column} : deja present")
        return
    suffix = f" DEFAULT {default_sql}" if default_sql else ""
    sql = f'ALTER TABLE "{table}" ADD COLUMN "{column}" {type_sql}{suffix}'
    conn.exec_driver_sql(sql)
    print(f"  + {table}.{column} ({type_sql}) ajoute")


def main() -> None:
    init_db()
    print("=== Migration v2 : feedback + adaptation auto ===")
    with engine.begin() as conn:
        _add_column_if_missing(conn, "user", "pace_adjustment_pct", "FLOAT", "0")
        _add_column_if_missing(conn, "session", "feedback", "VARCHAR")
        _add_column_if_missing(conn, "session", "validated_at", "DATETIME")
        _add_column_if_missing(conn, "session", "pace_adjustment_at_validation", "FLOAT")
    print("OK")


if __name__ == "__main__":
    main()

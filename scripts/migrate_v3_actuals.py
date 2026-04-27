"""Ajoute les colonnes actual_* sur Session pour stocker le realise depuis Garmin.

Idempotent : detecte les colonnes manquantes et les ajoute.
"""

from __future__ import annotations

from sqlalchemy import inspect

from app.db import engine, init_db


def _add_column_if_missing(conn, table: str, column: str, type_sql: str) -> None:
    inspector = inspect(conn)
    cols = {c["name"] for c in inspector.get_columns(table)}
    if column in cols:
        print(f"  - {table}.{column} : deja present")
        return
    sql = f'ALTER TABLE "{table}" ADD COLUMN "{column}" {type_sql}'
    conn.exec_driver_sql(sql)
    print(f"  + {table}.{column} ({type_sql}) ajoute")


def main() -> None:
    init_db()
    print("=== Migration v3 : realise Garmin sur les seances ===")
    with engine.begin() as conn:
        _add_column_if_missing(conn, "session", "garmin_activity_id", "BIGINT")
        _add_column_if_missing(conn, "session", "actual_duration_sec", "INTEGER")
        _add_column_if_missing(conn, "session", "actual_distance_m", "FLOAT")
    print("OK")


if __name__ == "__main__":
    main()

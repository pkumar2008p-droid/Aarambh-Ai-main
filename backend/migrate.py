"""Apply the AARAMBH PostgreSQL schema once (safe to rerun).

Run from the repository root after setting DATABASE_URL to the Render
EXTERNAL Database URL on your local machine, or an internal URL on Render.
The script never prints the connection URL or credentials.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg


def main() -> int:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("ERROR: DATABASE_URL is not set.")
        return 2
    if database_url.startswith("postgres://"):
        database_url = "postgresql://" + database_url[len("postgres://"): ]

    schema_path = Path(__file__).with_name("schema.sql")
    schema = schema_path.read_text(encoding="utf-8")
    # schema.sql contains plain idempotent DDL statements (no procedural blocks).
    statements = [part.strip() for part in schema.split(";") if part.strip()]
    try:
        with psycopg.connect(
            database_url,
            connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "15")),
            sslmode=os.getenv("DB_SSLMODE", "require"),
        ) as conn:
            with conn.cursor() as cur:
                for number, statement in enumerate(statements, start=1):
                    try:
                        cur.execute(statement)
                    except Exception as exc:
                        raise RuntimeError(
                            f"SQL statement {number} failed ({type(exc).__name__})."
                        ) from None
        print(f"SUCCESS: applied {len(statements)} schema statements.")
        return 0
    except Exception as exc:
        print(f"ERROR: PostgreSQL schema setup failed: {exc}")
        print("Confirm the database is Available and DATABASE_URL is correct.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

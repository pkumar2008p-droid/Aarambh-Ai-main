"""AARAMBH core services backed by PostgreSQL (no Supabase dependency).

Expected environment variables:
    DATABASE_URL  Render PostgreSQL connection string
    DATA_KEY      Fernet key used to encrypt patient/private fields
    PEPPER        High-entropy server-side HMAC secret

This module intentionally keeps the small ``db.table(...).execute()`` interface
used by the existing app, so the application routes do not need a database API
rewrite.  The implementation below executes parameterized PostgreSQL queries
through psycopg 3.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import psycopg
from cryptography.fernet import Fernet, InvalidToken
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

log = logging.getLogger("aarambh")
IST = ZoneInfo("Asia/Kolkata")
DEV = os.getenv("APP_ENV", "production").strip().lower() == "development"


def need(name: str) -> str:
    """Read a required secret/config value and fail with a clear message."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing environment variable: {name}")
    return value


DATABASE_URL = need("DATABASE_URL")
# Render and some PostgreSQL providers still supply the legacy postgres:// URL.
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://") :]

try:
    _fernet = Fernet(need("DATA_KEY").encode("ascii"))
except (ValueError, UnicodeEncodeError) as exc:
    raise RuntimeError(
        "DATA_KEY must be a valid Fernet key. Generate one with "
        "Fernet.generate_key().decode()."
    ) from exc

_PEPPER = need("PEPPER").encode("utf-8")


@contextmanager
def _connection():
    """Open one short-lived DB connection per operation; always close it."""
    with psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
        connect_timeout=int(os.getenv("DB_CONNECT_TIMEOUT", "10")),
    ) as conn:
        yield conn


# ---------- minimal, safe PostgREST-style query interface ----------
# Names passed to SQL are identifiers, never values.  Values always use
# psycopg placeholders.  Restrict identifiers to ordinary PostgreSQL names.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_JSON_COLUMNS = {"features", "docs_ready", "barriers", "metrics"}
_DATE_COLUMNS = {"due", "at", "resched_date", "completed_on"}
_TIME_COLUMNS = {"created_at", "last_seen", "expires_at", "captured_at", "updated_at", "locked_until", "trained_at"}


def _ident(value: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid database identifier: {value!r}")
    return '"' + value + '"'


def _adapt_value(column: str, value: Any) -> Any:
    if column in _JSON_COLUMNS and isinstance(value, (dict, list)):
        return Jsonb(value)
    if isinstance(value, str) and column in _DATE_COLUMNS:
        return date.fromisoformat(value[:10])
    if isinstance(value, str) and column in _TIME_COLUMNS:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return value


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return JSON-friendly values for Flask responses."""
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, (datetime, date)):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


class QueryResult:
    def __init__(self, data: list[dict[str, Any]] | None = None, count: int | None = None):
        self.data = data or []
        self.count = count


class PostgresQuery:
    def __init__(self, table_name: str):
        self.table_name = _ident(table_name)
        self.operation = "select"
        self.columns = "*"
        self.values: dict[str, Any] | list[dict[str, Any]] | None = None
        self.filters: list[tuple[str, str, Any]] = []
        self.ordering: list[tuple[str, bool]] = []
        self.row_limit: int | None = None
        self.row_offset = 0
        self.count_mode: str | None = None
        self.conflict_columns: list[str] = []

    def select(self, columns: str = "*", count: str | None = None, **_: Any) -> "PostgresQuery":
        selected = columns.strip()
        if selected != "*":
            names = [part.strip() for part in selected.split(",") if part.strip()]
            if not names:
                raise ValueError("select() requires at least one column")
            self.columns = ", ".join(_ident(name) for name in names)
        else:
            self.columns = "*"
        if count not in (None, "exact"):
            raise ValueError("Only count='exact' is supported")
        self.count_mode = count
        self.operation = "select"
        return self

    def insert(self, values: dict[str, Any] | list[dict[str, Any]], **_: Any) -> "PostgresQuery":
        self.operation = "insert"
        self.values = values
        return self

    def update(self, values: dict[str, Any], **_: Any) -> "PostgresQuery":
        self.operation = "update"
        self.values = values
        return self

    def delete(self, **_: Any) -> "PostgresQuery":
        self.operation = "delete"
        return self

    def upsert(
        self,
        values: dict[str, Any] | list[dict[str, Any]],
        on_conflict: str | None = None,
        **_: Any,
    ) -> "PostgresQuery":
        self.operation = "upsert"
        self.values = values
        if on_conflict:
            self.conflict_columns = [_ident(c.strip()) for c in on_conflict.split(",") if c.strip()]
        return self

    def _filter(self, column: str, operator: str, value: Any) -> "PostgresQuery":
        self.filters.append((_ident(column), operator, value))
        return self

    def eq(self, column: str, value: Any) -> "PostgresQuery":
        if value is None:
            self.filters.append((_ident(column), "IS NULL", None))
            return self
        return self._filter(column, "=", value)

    def neq(self, column: str, value: Any) -> "PostgresQuery":
        if value is None:
            self.filters.append((_ident(column), "IS NOT NULL", None))
            return self
        return self._filter(column, "<>", value)

    def gt(self, column: str, value: Any) -> "PostgresQuery":
        return self._filter(column, ">", value)

    def gte(self, column: str, value: Any) -> "PostgresQuery":
        return self._filter(column, ">=", value)

    def lt(self, column: str, value: Any) -> "PostgresQuery":
        return self._filter(column, "<", value)

    def lte(self, column: str, value: Any) -> "PostgresQuery":
        return self._filter(column, "<=", value)

    def in_(self, column: str, values: Iterable[Any]) -> "PostgresQuery":
        self.filters.append((_ident(column), "IN", [_adapt_value(column, v) for v in values]))
        return self

    def order(self, column: str, desc: bool = False, **_: Any) -> "PostgresQuery":
        self.ordering.append((_ident(column), bool(desc)))
        return self

    def limit(self, value: int, **_: Any) -> "PostgresQuery":
        value = int(value)
        if value < 0:
            raise ValueError("limit must be non-negative")
        self.row_limit = value
        return self

    def range(self, start: int, end: int, **_: Any) -> "PostgresQuery":
        start, end = int(start), int(end)
        if start < 0 or end < start:
            raise ValueError("Invalid range")
        self.row_offset = start
        self.row_limit = end - start + 1
        return self

    def _where_sql(self) -> tuple[str, list[Any]]:
        if not self.filters:
            return "", []
        clauses: list[str] = []
        params: list[Any] = []
        for column, operator, value in self.filters:
            if operator in ("IS NULL", "IS NOT NULL"):
                clauses.append(f"{column} {operator}")
            elif operator == "IN":
                if not value:
                    clauses.append("FALSE")
                else:
                    clauses.append(f"{column} = ANY(%s)")
                    params.append(value)
            else:
                clauses.append(f"{column} {operator} %s")
                params.append(_adapt_value(column.strip('"'), value))
        return " WHERE " + " AND ".join(clauses), params

    def _return_rows(self, cursor: Any) -> list[dict[str, Any]]:
        return [_normalise_row(row) for row in cursor.fetchall()]

    def _write_rows(self, rows: list[dict[str, Any]], upsert: bool = False) -> QueryResult:
        if not rows:
            return QueryResult([])
        columns = list(rows[0].keys())
        if not columns:
            raise ValueError("Cannot insert an empty row")
        if any(list(row.keys()) != columns for row in rows):
            raise ValueError("All rows in a bulk insert must have the same columns in the same order")

        quoted_columns = ", ".join(_ident(col) for col in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        all_params = [
            _adapt_value(column, row[column])
            for row in rows
            for column in columns
        ]
        values_sql = ", ".join(
            "(" + placeholders + ")" for _ in rows
        )
        sql = f"INSERT INTO {self.table_name} ({quoted_columns}) VALUES {values_sql}"

        if upsert:
            if not self.conflict_columns:
                raise ValueError("upsert() requires on_conflict='column_name'")
            conflict_sql = ", ".join(self.conflict_columns)
            update_columns = [col for col in columns if _ident(col) not in self.conflict_columns]
            if update_columns:
                updates = ", ".join(
                    f'{_ident(col)} = EXCLUDED.{_ident(col)}' for col in update_columns
                )
                sql += f" ON CONFLICT ({conflict_sql}) DO UPDATE SET {updates}"
            else:
                sql += f" ON CONFLICT ({conflict_sql}) DO NOTHING"
        sql += " RETURNING *"

        with _connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, all_params)
                return QueryResult(self._return_rows(cur))

    def execute(self) -> QueryResult:
        where_sql, where_params = self._where_sql()

        if self.operation == "select":
            count_value = None
            with _connection() as conn:
                with conn.cursor() as cur:
                    if self.count_mode == "exact":
                        cur.execute(
                            f"SELECT COUNT(*) AS total FROM {self.table_name}{where_sql}",
                            where_params,
                        )
                        count_value = cur.fetchone()["total"]

                    sql = f"SELECT {self.columns} FROM {self.table_name}{where_sql}"
                    if self.ordering:
                        sql += " ORDER BY " + ", ".join(
                            f"{column} {'DESC' if desc else 'ASC'}"
                            for column, desc in self.ordering
                        )
                    params = list(where_params)
                    if self.row_limit is not None:
                        sql += " LIMIT %s"
                        params.append(self.row_limit)
                    if self.row_offset:
                        sql += " OFFSET %s"
                        params.append(self.row_offset)
                    cur.execute(sql, params)
                    return QueryResult(self._return_rows(cur), count_value)

        if self.operation in ("insert", "upsert"):
            if self.values is None:
                raise ValueError("No values supplied")
            rows = self.values if isinstance(self.values, list) else [self.values]
            return self._write_rows(rows, upsert=self.operation == "upsert")

        if self.operation == "update":
            if not isinstance(self.values, dict) or not self.values:
                raise ValueError("update() requires a non-empty dictionary")
            if not self.filters:
                raise ValueError("Refusing to update every row: add a filter")
            assignments = ", ".join(f"{_ident(col)} = %s" for col in self.values)
            params = [_adapt_value(col, val) for col, val in self.values.items()]
            sql = f"UPDATE {self.table_name} SET {assignments}{where_sql} RETURNING *"
            with _connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, params + where_params)
                    return QueryResult(self._return_rows(cur))

        if self.operation == "delete":
            if not self.filters:
                raise ValueError("Refusing to delete every row: add a filter")
            sql = f"DELETE FROM {self.table_name}{where_sql} RETURNING *"
            with _connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, where_params)
                    return QueryResult(self._return_rows(cur))

        raise ValueError(f"Unsupported database operation: {self.operation}")


class PostgresClient:
    """Small database client exposing table queries used by the application."""

    def table(self, name: str) -> PostgresQuery:
        return PostgresQuery(name)


db = PostgresClient()


def db_ping() -> bool:
    """Return True only when PostgreSQL is reachable and the app schema exists."""
    required_tables = {
        "patients", "staff", "sessions", "audit_logs", "followups", "contacts",
        "tasks", "rx", "otps", "risk_snapshots", "training_samples", "ml_models",
    }
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            if cur.fetchone()["ok"] != 1:
                return False
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = ANY(%s)",
                (list(required_tables),),
            )
            found = {row["table_name"] for row in cur.fetchall()}
            return required_tables.issubset(found)


# ---------- time ----------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    return datetime.now(IST).date()


def iso(value: date | datetime) -> str:
    return value.isoformat()


def ts(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------- crypto ----------
def enc(obj: Any) -> str:
    payload = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _fernet.encrypt(payload).decode("ascii")


def dec(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        value = json.loads(_fernet.decrypt(token.encode("ascii")).decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except (InvalidToken, AttributeError, TypeError, ValueError, UnicodeError):
        return {}


def hmac_hex(value: str) -> str:
    return hmac.new(_PEPPER, str(value).encode("utf-8"), hashlib.sha256).hexdigest()


def sha(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


# ---------- validation ----------
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f<>]")
_PH = re.compile(r"^[6-9]\d{9}$")
_TM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def clean(value: Any, max_length: int = 200) -> str:
    if value is None:
        return ""
    return _CTRL.sub("", str(value)).strip()[:max_length]


def phone_ok(value: Any) -> str | None:
    phone = clean(value, 10)
    return phone if _PH.fullmatch(phone) else None


def time_ok(value: Any) -> str | None:
    value = clean(value, 5)
    return value if _TM.fullmatch(value) else None


def date_ok(value: Any) -> date | None:
    try:
        return date.fromisoformat(clean(value, 10))
    except (TypeError, ValueError):
        return None


# ---------- database helpers ----------
def paged(make):
    """Fetch all rows in pages of 1,000 (PostgreSQL has no PostgREST row cap)."""
    output: list[dict[str, Any]] = []
    offset = 0
    while True:
        rows = make().range(offset, offset + 999).execute().data
        output.extend(rows)
        if len(rows) < 1000:
            return output
        offset += 1000


def by_ids(table: str, column: str, ids: Iterable[Any]) -> list[dict[str, Any]]:
    identifiers = list(ids)
    output: list[dict[str, Any]] = []
    for start in range(0, len(identifiers), 80):
        batch = identifiers[start : start + 80]
        output.extend(
            paged(lambda b=batch: db.table(table).select("*").in_(column, b))
        )
    return output


def one(table: str, id_: Any) -> dict[str, Any] | None:
    rows = (
        db.table(table)
        .select("*")
        .eq("id", id_)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


# ---------- sessions (opaque token; only its hash is stored) ----------
COOKIE = "aarambh_sid" if DEV else "__Host-aarambh"
IDLE = timedelta(minutes=30)
ABSOLUTE = {"patient": timedelta(hours=2), "staff": timedelta(hours=8)}


def create_session(kind: str, subject: str, ip: str) -> str:
    if kind not in ABSOLUTE:
        raise ValueError("Unsupported session kind")
    token = secrets.token_urlsafe(48)
    current = now_utc()
    db.table("sessions").delete().lt("expires_at", iso(current)).execute()
    db.table("sessions").insert(
        {
            "token_hash": sha(token),
            "kind": kind,
            "subject": subject,
            "ip_hash": hmac_hex(ip or ""),
            "created_at": iso(current),
            "last_seen": iso(current),
            "expires_at": iso(current + ABSOLUTE[kind]),
        }
    ).execute()
    return token


def get_session(token: str | None) -> dict[str, Any] | None:
    if not token or len(token) > 120:
        return None
    rows = (
        db.table("sessions")
        .select("*")
        .eq("token_hash", sha(token))
        .limit(1)
        .execute()
        .data
    )
    if not rows:
        return None

    session = rows[0]
    current = now_utc()
    if current > ts(session["expires_at"]) or current - ts(session["last_seen"]) > IDLE:
        db.table("sessions").delete().eq("id", session["id"]).execute()
        return None

    if current - ts(session["last_seen"]) > timedelta(seconds=60):
        db.table("sessions").update({"last_seen": iso(current)}).eq("id", session["id"]).execute()
    return session


def drop_session(token: str | None) -> None:
    if token:
        db.table("sessions").delete().eq("token_hash", sha(token)).execute()


# ---------- audit trail ----------
def audit(actor: str, role: str, action: str, entity: str = "", entity_id: str = "", ip: str = "") -> None:
    try:
        db.table("audit_logs").insert(
            {
                "actor": actor,
                "role": role,
                "action": action,
                "entity": entity,
                "entity_id": str(entity_id),
                "ip": ip,
            }
        ).execute()
    except Exception:
        # Audit failure should be visible in server logs, but must not expose
        # database details to the HTTP response.
        log.exception("audit failed")

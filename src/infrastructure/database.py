from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any, Generator

DEFAULT_DB_PATH = ".runtime/sabor-da-maria.sqlite3"
WORKSPACE_ID = "dona_maria"

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace (
    id TEXT PRIMARY KEY,
    initial_budget TEXT NOT NULL DEFAULT '80.00',
    committed_budget TEXT NOT NULL DEFAULT '0.00',
    pantry_json TEXT,
    initialized_at TEXT
);

CREATE TABLE IF NOT EXISTS kitchen_capabilities (
    workspace_id TEXT NOT NULL,
    capability_type TEXT NOT NULL,
    name TEXT NOT NULL,
    available INTEGER,
    detail TEXT,
    PRIMARY KEY (workspace_id, capability_type, name)
);

CREATE TABLE IF NOT EXISTS recipes (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL DEFAULT 'dona_maria',
    name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    servings INTEGER NOT NULL,
    recipe_json TEXT NOT NULL,
    interested INTEGER,
    feedback TEXT,
    purchase_plan_json TEXT,
    cmv_json TEXT,
    pricing_json TEXT,
    selected_price_label TEXT,
    accepted INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingredient_matches (
    recipe_id TEXT NOT NULL,
    recipe_ingredient TEXT NOT NULL,
    pantry_ingredient TEXT,
    status TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    reasoning TEXT,
    PRIMARY KEY (recipe_id, recipe_ingredient)
);

CREATE TABLE IF NOT EXISTS market_quotes (
    quote_id TEXT PRIMARY KEY,
    recipe_id TEXT NOT NULL,
    ingredient TEXT NOT NULL,
    package_quantity TEXT NOT NULL,
    unit TEXT NOT NULL,
    package_price TEXT NOT NULL,
    source_url TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory_reservations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL DEFAULT 'dona_maria',
    recipe_id TEXT NOT NULL,
    pantry_ingredient TEXT NOT NULL,
    quantity TEXT NOT NULL,
    unit TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS committed_purchases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workspace_id TEXT NOT NULL DEFAULT 'dona_maria',
    recipe_id TEXT NOT NULL,
    purchase_id TEXT NOT NULL,
    ingredient TEXT NOT NULL,
    purchased_quantity TEXT NOT NULL,
    unit TEXT NOT NULL,
    cash_outlay TEXT NOT NULL,
    quantity_used TEXT NOT NULL,
    quantity_remaining TEXT NOT NULL
);
"""


class _DecimalEncoder(json.JSONEncoder):
    def default(self, obj: object) -> Any:
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def _dumps(obj: Any) -> str:
    return json.dumps(obj, cls=_DecimalEncoder, ensure_ascii=False)


class Database:
    """Thin SQLite wrapper for Sabor da Maria workflow state."""

    def __init__(self, db_path: str | Path | None = None):
        resolved = db_path or os.environ.get("SABOR_DB_PATH", DEFAULT_DB_PATH)
        self._path = Path(resolved)
        if str(self._path) != ":memory:":
            self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path),
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        columns = [
            row[1]
            for row in self._conn.execute(
                "PRAGMA table_info(kitchen_capabilities)"
            )
        ]
        if "detail" not in columns:
            self._conn.execute(
                "ALTER TABLE kitchen_capabilities ADD COLUMN detail TEXT"
            )

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        cursor = self._conn.cursor()
        try:
            cursor.execute("BEGIN")
            yield cursor
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

    def execute(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def commit(self) -> None:
        self._conn.commit()

    def fetchone(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> sqlite3.Row | None:
        self._conn.commit()
        return self._conn.execute(sql, params).fetchone()

    def fetchall(
        self,
        sql: str,
        params: tuple[Any, ...] = (),
    ) -> list[sqlite3.Row]:
        self._conn.commit()
        return self._conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self._conn.close()

    @staticmethod
    def dumps(obj: Any) -> str:
        return _dumps(obj)

    @staticmethod
    def loads(s: str) -> Any:
        return json.loads(s)

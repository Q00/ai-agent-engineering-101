"""Shared memory for the shop and the buyer: a Neon (Postgres) database.

The kit starts every hook (market_buyer, restock, review) as a fresh process, so anything an agent should
remember across orders lives here, not in the process. DATABASE_URL comes from .env (never committed).

Queries go over Neon's SQL-over-HTTPS endpoint (port 443), not the Postgres wire protocol: some networks
(the campus Wi-Fi among them) block port 5432. Placeholders are Postgres-style: $1, $2, ...
"""
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

HERE = Path(__file__).resolve().parent

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS settles (
        seq BIGINT PRIMARY KEY, ts DOUBLE PRECISION, shop_url TEXT, buyer TEXT, sku TEXT, name TEXT,
        category TEXT, kind TEXT, list_price INT, amount INT, grade TEXT, claim TEXT, claim_honest BOOLEAN,
        warranty_hours DOUBLE PRECISION, unit TEXT)""",
    "CREATE INDEX IF NOT EXISTS settles_sku ON settles (sku)",
    "CREATE INDEX IF NOT EXISTS settles_shop ON settles (shop_url)",
    "CREATE INDEX IF NOT EXISTS settles_ts ON settles (ts)",
    """CREATE TABLE IF NOT EXISTS broken (
        seq BIGINT PRIMARY KEY, ts DOUBLE PRECISION, shop_url TEXT, buyer TEXT, sku TEXT, unit TEXT,
        hours_after_sale DOUBLE PRECISION, warranty_hours DOUBLE PRECISION, refunded INT)""",
    "CREATE INDEX IF NOT EXISTS broken_unit ON broken (unit)",
    "CREATE TABLE IF NOT EXISTS shops (url TEXT PRIMARY KEY, name TEXT, seen DOUBLE PRECISION)",
    """CREATE TABLE IF NOT EXISTS forum (
        id BIGINT PRIMARY KEY, ts DOUBLE PRECISION, author TEXT, role TEXT, thread BIGINT, text TEXT,
        likes INT, dislikes INT, score DOUBLE PRECISION)""",
    "CREATE TABLE IF NOT EXISTS offers_seen (ts DOUBLE PRECISION, shop TEXT, sku TEXT, name TEXT, qty INT, category TEXT)",
    """CREATE TABLE IF NOT EXISTS trajectory (
        id BIGSERIAL PRIMARY KEY, ts DOUBLE PRECISION, role TEXT, decision TEXT, context JSONB, outcome JSONB)""",
    "CREATE TABLE IF NOT EXISTS policy (key TEXT PRIMARY KEY, value JSONB, updated DOUBLE PRECISION, reason TEXT)",
    "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT)",
]


def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url and (HERE / ".env").exists():
        for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith("DATABASE_URL="):
                url = line.split("=", 1)[1].strip().strip("'\"")
    if not url:
        raise SystemExit("DATABASE_URL is not set (put it in .env next to db.py)")
    return url


class DB:
    def __init__(self, timeout=20):
        url = database_url()
        self.endpoint = f"https://{urlparse(url).hostname}/sql"
        self.c = httpx.Client(timeout=timeout, headers={"Neon-Connection-String": url})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.c.close()

    def _post(self, body):
        r = self.c.post(self.endpoint, json=body)
        if r.status_code != 200:
            raise RuntimeError(f"neon {r.status_code}: {r.text[:300]}")
        return r.json()

    def q(self, sql, *params) -> list[dict]:
        return self._post({"query": sql, "params": list(params)})["rows"]

    def batch(self, statements) -> None:
        """Several (sql, params) in one round trip, as one transaction."""
        statements = list(statements)
        if statements:
            self._post({"queries": [{"query": s, "params": list(p)} for s, p in statements]})

    def init(self) -> None:
        self.batch((s, ()) for s in SCHEMA)

    def get_state(self, key, default=None):
        rows = self.q("SELECT value FROM state WHERE key = $1", key)
        return rows[0]["value"] if rows else default

    def set_state(self, key, value) -> None:
        self.q("INSERT INTO state (key, value) VALUES ($1, $2) "
               "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", key, str(value))

    def get_policy(self, key, default):
        rows = self.q("SELECT value FROM policy WHERE key = $1", key)
        return rows[0]["value"] if rows else default

    def set_policy(self, key, value, reason, now) -> None:
        self.q("INSERT INTO policy (key, value, updated, reason) VALUES ($1, $2::jsonb, $3, $4) "
               "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated = EXCLUDED.updated, "
               "reason = EXCLUDED.reason", key, json.dumps(value, ensure_ascii=False), now, reason)

    def log_trajectory(self, ts, role, decision, context, outcome=None) -> None:
        self.q("INSERT INTO trajectory (ts, role, decision, context, outcome) VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)",
               ts, role, decision, json.dumps(context, ensure_ascii=False),
               None if outcome is None else json.dumps(outcome, ensure_ascii=False))


if __name__ == "__main__":
    with DB() as d:
        d.init()
        print(json.dumps({t: d.q(f"SELECT count(*) AS n FROM {t}")[0]["n"]
                          for t in ("settles", "broken", "shops", "forum", "trajectory", "policy")}))

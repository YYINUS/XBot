"""SQLite persistence: seen items, drafts, posts, metrics, key-value state."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Draft, Item, normalize_title, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    norm_title TEXT,
    data TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    used INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS items_seen ON items(first_seen);
CREATE INDEX IF NOT EXISTS items_title ON items(norm_title);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    posted_at TEXT,
    data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS drafts_status ON drafts(status);

CREATE TABLE IF NOT EXISTS metrics (
    post_id TEXT PRIMARY KEY,
    draft_id INTEGER,
    fetched_at TEXT,
    data TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---------------- items ----------------
    def add_items(self, items: list[Item]) -> list[Item]:
        """Insert unseen items; returns only the ones that were new (by id and by title)."""
        fresh: list[Item] = []
        now = _iso(utcnow())
        for it in items:
            norm = normalize_title(it.title)
            dup = self.conn.execute(
                "SELECT 1 FROM items WHERE id=? OR (norm_title=? AND norm_title!='')", (it.id, norm)
            ).fetchone()
            if dup:
                continue
            self.conn.execute(
                "INSERT INTO items(id, norm_title, data, first_seen) VALUES (?,?,?,?)",
                (it.id, norm, it.model_dump_json(), now),
            )
            fresh.append(it)
        self.conn.commit()
        return fresh

    def unused_items(self, max_age_hours: int, limit: int) -> list[Item]:
        since = _iso(utcnow() - timedelta(hours=max_age_hours))
        rows = self.conn.execute(
            "SELECT data FROM items WHERE used=0 AND first_seen>=? ORDER BY first_seen DESC LIMIT ?",
            (since, limit),
        ).fetchall()
        return [Item.model_validate_json(r["data"]) for r in rows]

    def get_items(self, ids: list[str]) -> list[Item]:
        if not ids:
            return []
        q = ",".join("?" * len(ids))
        rows = self.conn.execute(f"SELECT data FROM items WHERE id IN ({q})", ids).fetchall()
        return [Item.model_validate_json(r["data"]) for r in rows]

    def mark_used(self, ids: list[str]) -> None:
        self.conn.executemany("UPDATE items SET used=1 WHERE id=?", [(i,) for i in ids])
        self.conn.commit()

    def recent_titles(self, hours: int = 72, limit: int = 150) -> list[str]:
        since = _iso(utcnow() - timedelta(hours=hours))
        rows = self.conn.execute(
            "SELECT data FROM items WHERE first_seen>=? ORDER BY first_seen DESC LIMIT ?", (since, limit)
        ).fetchall()
        return [json.loads(r["data"])["title"] for r in rows]

    # ---------------- drafts ----------------
    def save_draft(self, d: Draft) -> Draft:
        data = d.model_dump_json(exclude={"id"})
        posted = _iso(d.posted_at) if d.posted_at else None
        if d.id is None:
            cur = self.conn.execute(
                "INSERT INTO drafts(status, created_at, posted_at, data) VALUES (?,?,?,?)",
                (d.status, _iso(d.created_at), posted, data),
            )
            d.id = cur.lastrowid
        else:
            self.conn.execute(
                "UPDATE drafts SET status=?, posted_at=?, data=? WHERE id=?", (d.status, posted, data, d.id)
            )
        self.conn.commit()
        return d

    def _row_to_draft(self, row) -> Draft:
        d = Draft.model_validate_json(row["data"])
        d.id = row["id"]
        d.status = row["status"]
        return d

    def get_draft(self, draft_id: int) -> Draft | None:
        row = self.conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        return self._row_to_draft(row) if row else None

    def drafts(self, status: str | None = None, limit: int = 50) -> list[Draft]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM drafts WHERE status=? ORDER BY id ASC LIMIT ?", (status, limit)
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM drafts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_draft(r) for r in rows]

    def posted_since(self, since: datetime) -> list[Draft]:
        rows = self.conn.execute(
            "SELECT * FROM drafts WHERE status='posted' AND posted_at>=? ORDER BY posted_at DESC",
            (_iso(since),),
        ).fetchall()
        return [self._row_to_draft(r) for r in rows]

    def recent_drafts(self, days: int = 7, limit: int = 40) -> list[Draft]:
        since = _iso(utcnow() - timedelta(days=days))
        rows = self.conn.execute(
            "SELECT * FROM drafts WHERE created_at>=? AND status IN ('posted','approved','pending') "
            "ORDER BY id DESC LIMIT ?",
            (since, limit),
        ).fetchall()
        return [self._row_to_draft(r) for r in rows]

    # ---------------- metrics ----------------
    def save_metrics(self, post_id: str, draft_id: int | None, data: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO metrics(post_id, draft_id, fetched_at, data) VALUES (?,?,?,?)",
            (post_id, draft_id, _iso(utcnow()), json.dumps(data)),
        )
        self.conn.commit()

    def metrics_by_draft(self) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for r in self.conn.execute("SELECT draft_id, data FROM metrics").fetchall():
            if r["draft_id"] is None:
                continue
            m = json.loads(r["data"])
            agg = out.setdefault(r["draft_id"], {})
            for k, v in m.items():
                if isinstance(v, (int, float)):
                    agg[k] = agg.get(k, 0) + v
        return out

    # ---------------- kv ----------------
    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?,?)", (key, value))
        self.conn.commit()

    def due(self, key: str, every_minutes: int, now: datetime | None = None) -> bool:
        now = now or utcnow()
        last = self.get(f"last_run:{key}")
        if not last:
            return True
        return now - datetime.fromisoformat(last) >= timedelta(minutes=every_minutes)

    def mark_ran(self, key: str, now: datetime | None = None) -> None:
        self.set(f"last_run:{key}", _iso(now or utcnow()))

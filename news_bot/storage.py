"""資料儲存層。

SupabaseStore：正式環境，透過 Supabase 的 PostgREST API 寫入（免費方案 500MB，
                純文字新聞一年約數十 MB，足夠使用多年）。
SQLiteStore  ：本機測試或內網伺服器離線使用，介面相同。
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .config import ROOT, settings
from .models import NewsItem

TW = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(TW).isoformat()


class SupabaseStore:
    def __init__(self, url: str | None = None, key: str | None = None):
        self.base = f"{url or settings.supabase_url}/rest/v1"
        key = key or settings.supabase_service_key
        if not self.base.startswith("http") or not key:
            raise RuntimeError("未設定 SUPABASE_URL / SUPABASE_SERVICE_KEY")
        self.s = requests.Session()
        self.s.headers.update({
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        })

    def _req(self, method: str, path: str, **kw):
        r = self.s.request(method, f"{self.base}/{path}", timeout=30, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase {method} {path} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else None

    def insert_new(self, items: list[NewsItem]) -> int:
        if not items:
            return 0
        rows = self._req(
            "POST", "news?on_conflict=url_hash",
            headers={"Prefer": "resolution=ignore-duplicates,return=representation"},
            data=json.dumps([i.to_row() for i in items], ensure_ascii=False).encode(),
        )
        return len(rows or [])

    def pending(self, limit: int = 200) -> list[dict]:
        return self._req("GET", "news", params={
            "select": "id,title,summary,url,source,published_at,topics,sentiment,title_key,score",
            "pushed_at": "is.null",
            "order": "published_at.asc",
            "limit": str(limit),
        }) or []

    def recent_pushed_keys(self, hours: int = 48) -> list[tuple[int, str]]:
        since = (datetime.now(TW) - timedelta(hours=hours)).isoformat()
        rows = self._req("GET", "news", params={
            "select": "id,title_key",
            "pushed_at": "not.is.null",
            "dup_of": "is.null",
            "published_at": f"gte.{since}",
            "limit": "2000",
        }) or []
        return [(r["id"], r["title_key"]) for r in rows]

    def last_push_time(self) -> datetime | None:
        rows = self._req("GET", "news", params={
            "select": "pushed_at", "pushed_at": "not.is.null", "dup_of": "is.null",
            "order": "pushed_at.desc", "limit": "1"}) or []
        return datetime.fromisoformat(rows[0]["pushed_at"]) if rows else None

    def mark_pushed(self, ids: list[int]) -> None:
        if ids:
            self._req("PATCH", f"news?id=in.({','.join(map(str, ids))})",
                      data=json.dumps({"pushed_at": _now()}))

    def mark_dup(self, id_: int, dup_of: int) -> None:
        self._req("PATCH", f"news?id=eq.{id_}",
                  data=json.dumps({"pushed_at": _now(), "dup_of": dup_of}))

    def log_run(self, stats: dict) -> None:
        try:
            self._req("POST", "run_logs", data=json.dumps(stats, ensure_ascii=False).encode())
        except Exception:  # noqa: BLE001 - 執行紀錄失敗不影響主流程
            pass


class SQLiteStore:
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS news (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url_hash TEXT UNIQUE NOT NULL, url TEXT, title TEXT, summary TEXT,
        source TEXT, published_at TEXT, score INTEGER, topics TEXT,
        matched_keywords TEXT, sentiment TEXT, title_key TEXT,
        pushed_at TEXT, dup_of INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS run_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """

    def __init__(self, path: str | Path | None = None):
        self.db = sqlite3.connect(str(path or ROOT / "news.db"))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(self.SCHEMA)

    def insert_new(self, items: list[NewsItem]) -> int:
        n = 0
        for i in items:
            r = i.to_row()
            r["topics"] = json.dumps(r["topics"], ensure_ascii=False)
            r["matched_keywords"] = json.dumps(r["matched_keywords"], ensure_ascii=False)
            cur = self.db.execute(
                f"INSERT OR IGNORE INTO news ({','.join(r)}) VALUES ({','.join('?' * len(r))})",
                list(r.values()))
            n += cur.rowcount
        self.db.commit()
        return n

    def pending(self, limit: int = 200) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM news WHERE pushed_at IS NULL ORDER BY published_at LIMIT ?", (limit,))
        out = []
        for row in rows:
            d = dict(row)
            d["topics"] = json.loads(d["topics"] or "[]")
            out.append(d)
        return out

    def recent_pushed_keys(self, hours: int = 48) -> list[tuple[int, str]]:
        since = (datetime.now(TW) - timedelta(hours=hours)).isoformat()
        rows = self.db.execute(
            "SELECT id, title_key FROM news WHERE pushed_at IS NOT NULL AND dup_of IS NULL "
            "AND published_at >= ?", (since,))
        return [(r[0], r[1]) for r in rows]

    def last_push_time(self) -> datetime | None:
        r = self.db.execute(
            "SELECT MAX(pushed_at) FROM news WHERE dup_of IS NULL").fetchone()[0]
        return datetime.fromisoformat(r) if r else None

    def mark_pushed(self, ids: list[int]) -> None:
        self.db.executemany("UPDATE news SET pushed_at=? WHERE id=?", [(_now(), i) for i in ids])
        self.db.commit()

    def mark_dup(self, id_: int, dup_of: int) -> None:
        self.db.execute("UPDATE news SET pushed_at=?, dup_of=? WHERE id=?", (_now(), dup_of, id_))
        self.db.commit()

    def log_run(self, stats: dict) -> None:
        self.db.execute("INSERT INTO run_logs (data) VALUES (?)",
                        (json.dumps(stats, ensure_ascii=False),))
        self.db.commit()


def get_store():
    if settings.supabase_url and settings.supabase_service_key:
        return SupabaseStore()
    return SQLiteStore()

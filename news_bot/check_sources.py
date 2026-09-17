"""檢查 config/sources.yaml 每個來源是否可用。

    python -m news_bot.check_sources            # 表格輸出
    python -m news_bot.check_sources --strict   # 有任何來源失效時 exit 1

在 GitHub Actions 執行時會同時寫入 Job Summary（$GITHUB_STEP_SUMMARY）。
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote_plus

import feedparser
import requests

from .config import load_yaml, settings
from .fetcher import GOOGLE_NEWS, UA


def _check(name: str, url: str) -> dict:
    r = {"name": name, "url": url, "status": "", "entries": 0, "latest": "", "ok": False}
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=settings.http_timeout)
        r["status"] = str(resp.status_code)
        if resp.ok:
            feed = feedparser.parse(resp.content)
            r["entries"] = len(feed.entries)
            if feed.entries:
                r["latest"] = (feed.entries[0].get("title") or "")[:30]
            r["ok"] = r["entries"] > 0
            if not r["ok"]:
                r["status"] += " 無項目"
    except Exception as e:  # noqa: BLE001
        r["status"] = type(e).__name__
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    cfg = load_yaml("sources.yaml")
    targets = [(s["name"], s["url"]) for s in cfg.get("rss") or []]
    g = cfg.get("google_news") or {}
    targets += [(f"Google新聞:{q}", GOOGLE_NEWS.format(q=quote_plus(q), when=g.get("when", "1d")))
                for q in g.get("queries") or []]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda t: _check(*t), targets))

    lines = ["| 結果 | 來源 | HTTP | 則數 | 最新標題 | URL |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {'✅' if r['ok'] else '❌'} | {r['name']} | {r['status']} | "
                     f"{r['entries']} | {r['latest'].replace('|', '/')} | {r['url']} |")
    bad = [r for r in results if not r["ok"]]
    lines.append(f"\n共 {len(results)} 個來源，失效 {len(bad)} 個")
    report = "\n".join(lines)
    print(report)

    summary = os.getenv("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("## 新聞來源檢查\n\n" + report + "\n")

    return 1 if (a.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())

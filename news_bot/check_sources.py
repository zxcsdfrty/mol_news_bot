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
from .fetcher import GOOGLE_NEWS, UA, clean_text


def usable_entries(entries) -> int:
    """實際抓得進來的則數：_fetch_rss 會丟掉缺標題或缺連結的項目。"""
    return sum(1 for e in entries
               if clean_text(e.get("title")) and (e.get("link") or "").strip())


def _check(name: str, url: str) -> dict:
    r = {"name": name, "url": url, "status": "", "entries": 0, "usable": 0,
         "latest": "", "note": "", "ok": False}
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=settings.http_timeout)
        r["status"] = str(resp.status_code)
        if resp.ok:
            feed = feedparser.parse(resp.content)
            r["entries"] = len(feed.entries)
            # 以「實際可用」而非「解析得到」判定，否則像聯合新聞網那樣回 20 則
            # 卻每則都沒有標題的情況會被誤報為正常。
            r["usable"] = usable_entries(feed.entries)
            if feed.entries:
                r["latest"] = clean_text(feed.entries[0].get("title"))[:30]
            if getattr(feed, "bozo", 0) and getattr(feed, "bozo_exception", None):
                r["note"] = type(feed.bozo_exception).__name__
            elif r["entries"] and not r["usable"]:
                # 列出第一則實際有哪些欄位，用以分辨「沒有 title 欄位」
                # 與「有 title 但內容是空的」
                keys = ",".join(sorted(feed.entries[0].keys()))[:90]
                r["note"] = f"項目缺標題或連結；欄位: {keys}"
            r["ok"] = r["usable"] > 0
            if not r["ok"]:
                r["status"] += " 無可用項目"
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
    # candidates：來源失效時用來測試替代網址。只有這裡會檢查，fetch_all 不會抓，
    # 確認可用後再搬進 rss。
    targets += [(f"候選:{s['name']}", s["url"]) for s in cfg.get("candidates") or []]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda t: _check(*t), targets))

    lines = ["| 結果 | 來源 | HTTP | 解析 | 可用 | 備註 | 最新標題 | URL |",
             "|---|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {'✅' if r['ok'] else '❌'} | {r['name']} | {r['status']} | "
                     f"{r['entries']} | {r['usable']} | {r['note']} | "
                     f"{r['latest'].replace('|', '/')} | {r['url']} |")
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

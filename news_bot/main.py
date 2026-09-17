"""主流程：抓取 → 關聯判斷 → 去重 → 入庫 → 推播。

用法：
    python -m news_bot.main              # 正式執行（排程每 30 分鐘呼叫一次）
    python -m news_bot.main --dry-run    # 不發 Telegram，印出訊息內容
    python -m news_bot.main --no-push    # 只收錄不推播（例如補抓歷史資料）
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .classifier import Classifier
from .config import settings
from .dedup import dedupe_batch, similar
from .fetcher import enrich_summary, fetch_all, resolve_all
from .notifier import push
from .storage import get_store

log = logging.getLogger("news_bot")
TW = timezone(timedelta(hours=8))


def in_quiet_hours(now: datetime | None = None, spec: str | None = None) -> bool:
    spec = settings.quiet_hours if spec is None else spec
    if not spec:
        return False
    start, end = (int(x) for x in spec.split("-"))
    h = (now or datetime.now(TW)).hour
    return (start <= h < end) if start < end else (h >= start or h < end)


def run(dry_run: bool = False, no_push: bool = False, store=None) -> dict:
    t0 = time.time()
    store = store or get_store()
    clf = Classifier()

    items = fetch_all()
    relevant = [i for i in items if clf.apply(i)]
    # 先還原 Google 轉址再去重：原文網址一致時，他台重複收錄可直接以 URL 去重
    relevant = resolve_all(relevant)
    unique = dedupe_batch(relevant)

    # 摘要過短者嘗試補抓原文描述，補完後重新計分（分數只會更高）
    short = [i for i in unique if len(i.summary) < 30]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(enrich_summary, short))
    for i in short:
        clf.apply(i)

    inserted = store.insert_new(unique)
    stats = {"fetched": len(items), "relevant": len(relevant),
             "unique": len(unique), "inserted": inserted, "pushed": 0, "dups": 0}

    last = store.last_push_time()
    too_soon = (last is not None and not dry_run and
                datetime.now(TW) - last < timedelta(minutes=settings.min_push_interval))
    if no_push or in_quiet_hours() or too_soon:
        log.info("本次不推播（no_push=%s, quiet=%s, too_soon=%s）",
                 no_push, in_quiet_hours(), too_soon)
    else:
        pending = store.pending()
        recent = store.recent_pushed_keys()
        to_send: list[dict] = []
        for row in pending:
            match = next((rid for rid, key in recent if similar(row["title_key"], key)), None)
            if match is None:
                match = next((r["id"] for r in to_send
                              if similar(row["title_key"], r["title_key"])), None)
            if match is not None:
                store.mark_dup(row["id"], match)   # 同一事件他台報導：入庫但不重複推播
                stats["dups"] += 1
            else:
                to_send.append(row)

        # 高分在前；單次上限避免洗版，超出的留待下一輪
        to_send.sort(key=lambda r: (-r.get("score", 0), r["published_at"]))
        batch = to_send[: settings.max_items_per_push]
        sent_ids = push(batch, dry_run=dry_run)
        if not dry_run:
            store.mark_pushed([i for i in sent_ids if i is not None])
        stats["pushed"] = len(sent_ids)

    stats["seconds"] = round(time.time() - t0, 1)
    if not dry_run:
        store.log_run(stats)
    log.info("完成 %s", stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="勞動部新聞收錄與 Telegram 推播")
    p.add_argument("--dry-run", action="store_true", help="不送 Telegram、不標記已推播")
    p.add_argument("--no-push", action="store_true", help="只收錄不推播")
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        run(dry_run=a.dry_run, no_push=a.no_push)
    except Exception:  # noqa: BLE001
        log.exception("執行失敗")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

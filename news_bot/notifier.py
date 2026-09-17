"""Telegram 推播。使用 HTML parse mode，單則訊息上限 4096 字，超過自動分段。"""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from .config import settings

log = logging.getLogger(__name__)
TW = timezone(timedelta(hours=8))
LIMIT = 4000  # 保留一點緩衝
SENTIMENT_ICON = {"負面": "🔴", "正面": "🟢", "中性": "⚪"}


def _fmt_time(v) -> str:
    try:
        dt = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
        return dt.astimezone(TW).strftime("%m/%d %H:%M")
    except ValueError:
        return ""


def format_item(n: int, row: dict, summary_len: int = 110) -> str:
    e = html.escape
    topics = row.get("topics") or []
    tag = f"【{e(topics[0])}】" if topics else ""
    summary = (row.get("summary") or "").strip()
    if len(summary) > summary_len:
        summary = summary[:summary_len].rstrip() + "…"
    icon = SENTIMENT_ICON.get(row.get("sentiment", "中性"), "⚪")
    lines = [
        f"<b>{n}. {tag}{e(row['title'])}</b>",
        f"{icon} {e(row.get('source', ''))}｜{_fmt_time(row.get('published_at'))}",
    ]
    if summary:
        lines.append(e(summary))
    lines.append(f'🔗 <a href="{e(row["url"], quote=True)}">閱讀全文</a>')
    return "\n".join(lines)


def build_messages(rows: list[dict]) -> list[tuple[str, list]]:
    """回傳 [(訊息文字, 該訊息包含的 row id 清單), ...]，以便只標記成功送出的新聞。"""
    now = datetime.now(TW).strftime("%Y/%m/%d %H:%M")
    header = f"📰 <b>勞動部相關新聞快報</b>（{now}）共 {len(rows)} 則\n"
    footer = (f'\n\n🔎 <a href="{html.escape(settings.web_url, quote=True)}">歷史新聞查詢</a>'
              if settings.web_url else "")

    msgs: list[tuple[str, list]] = []
    cur, ids = header, []
    for idx, row in enumerate(rows, 1):
        block = "\n" + format_item(idx, row) + "\n"
        if len(cur) + len(block) > LIMIT and ids:
            msgs.append((cur.rstrip(), ids))
            cur, ids = "📰 （續）\n", []
        cur += block
        ids.append(row.get("id"))
    if len(cur) + len(footer) <= LIMIT:
        cur = cur.rstrip() + footer
    msgs.append((cur.strip(), ids))
    return msgs


def send(text: str, retries: int = 3) -> bool:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(retries):
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 429:  # 觸發 Telegram 流量限制
                wait = r.json().get("parameters", {}).get("retry_after", 5)
                log.warning("Telegram 429，%s 秒後重試", wait)
                time.sleep(wait + 1)
                continue
            if r.ok:
                return True
            log.error("Telegram 發送失敗 %s: %s", r.status_code, r.text[:300])
        except requests.RequestException as ex:
            log.error("Telegram 連線錯誤: %s", ex)
        time.sleep(2 * (attempt + 1))
    return False


def push(rows: list[dict], dry_run: bool = False) -> list:
    """推播並回傳「成功送出」的 row id 清單。"""
    if not rows:
        return []
    sent: list = []
    for text, ids in build_messages(rows):
        if dry_run or not settings.telegram_bot_token:
            print("=" * 60 + "\n" + text)
            sent.extend(ids)
            continue
        if send(text):
            sent.extend(ids)
        time.sleep(1.1)  # 群組每分鐘 20 則上限，保守間隔
    return sent

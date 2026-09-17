"""Telegram 推播。訊息格式比照原委外廠商：一則新聞一封訊息。

    【新聞通報】
    標題 (來源)
    https://example.com/news/123
    　摘要內容……

網址以裸字串呈現，讓 Telegram 自動產生預覽卡片（廠商格式的一部分），
因此不可設定 disable_web_page_preview。

議題分類與輿情傾向仍會寫入資料庫供查詢網頁篩選，只是不放進推播訊息。
"""
from __future__ import annotations

import html
import logging
import time

import requests

from .config import settings

log = logging.getLogger(__name__)
LIMIT = 4000          # Telegram 單則訊息上限 4096 字，保留緩衝
SUMMARY_LIMIT = 300   # 摘要字數上限
SEND_INTERVAL = 3.5   # 群組每分鐘 20 則上限；一則一封訊息須放慢速度


def format_item(row: dict, summary_len: int = SUMMARY_LIMIT) -> str:
    e = html.escape
    source = (row.get("source") or "").strip()
    suffix = f" ({e(source)})" if source else ""
    summary = (row.get("summary") or "").strip()
    if summary_len <= 0:
        summary = ""
    elif len(summary) > summary_len:
        summary = summary[:summary_len].rstrip() + "…"
    lines = [
        "【新聞通報】",
        f"<b>{e(row['title'])}{suffix}</b>",
        e(row["url"]),
    ]
    if summary:
        lines.append(f"　{e(summary)}")  # 全形空格縮排，比照廠商格式
    return "\n".join(lines)


def build_messages(rows: list[dict]) -> list[tuple[str, list]]:
    """回傳 [(訊息文字, 該訊息包含的 row id 清單), ...]。

    廠商格式為一則新聞一封訊息，故每個 id 清單固定只有一個元素；
    回傳型別維持不變，讓 push() 仍可只標記成功送出的新聞。
    """
    msgs: list[tuple[str, list]] = []
    for row in rows:
        text = format_item(row)
        if len(text) > LIMIT:
            # 極端過長時縮短摘要後重組，避免直接截斷破壞 HTML 標籤或跳脫序列
            text = format_item(row, summary_len=SUMMARY_LIMIT - (len(text) - LIMIT) - 1)
        msgs.append((text, [row.get("id")]))
    return msgs


def _redact(text: str) -> str:
    """遮蔽日誌中的 Bot Token。

    requests 的例外訊息會帶出完整請求網址，其中含有 Token；而公開 repo 的
    Actions 執行紀錄任何人都看得到，故寫入日誌前一律先遮蔽。
    """
    token = settings.telegram_bot_token
    return text.replace(token, "***") if token else text


def send(text: str, retries: int = 3) -> bool:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        # 不停用網頁預覽：裸網址的預覽卡片是廠商格式的一部分
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
            log.error("Telegram 發送失敗 %s: %s", r.status_code, _redact(r.text[:300]))
        except requests.RequestException as ex:
            log.error("Telegram 連線錯誤: %s", _redact(str(ex)))
        time.sleep(2 * (attempt + 1))
    return False


def push(rows: list[dict], dry_run: bool = False) -> list:
    """推播並回傳「成功送出」的 row id 清單。"""
    if not rows:
        return []
    sent: list = []
    msgs = build_messages(rows)
    for idx, (text, ids) in enumerate(msgs):
        if dry_run or not settings.telegram_bot_token:
            print("=" * 60 + "\n" + text)
            sent.extend(ids)
            continue
        if send(text):
            sent.extend(ids)
        if idx < len(msgs) - 1:
            time.sleep(SEND_INTERVAL)
    return sent

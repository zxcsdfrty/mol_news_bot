"""從各媒體 RSS 與 Google 新聞 RSS 抓取新聞。"""
from __future__ import annotations

import html
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus, urlsplit

import feedparser
import requests

from .config import load_yaml, settings
from .models import NewsItem

log = logging.getLogger(__name__)

TW = timezone(timedelta(hours=8))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 MOL-NewsBot/1.0")
GOOGLE_NEWS = ("https://news.google.com/rss/search?q={q}+when:{when}"
               "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
GOOGLE_HOST = "news.google.com"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def is_google_url(url: str) -> bool:
    return GOOGLE_HOST in urlsplit(url).netloc


def excluded_sources() -> list[str]:
    """sources.yaml 的 exclude_sources：非新聞媒體（社群貼文等）一律不收。"""
    return [s.lower() for s in (load_yaml("sources.yaml").get("exclude_sources") or [])]


def is_excluded(source: str, url: str, excludes: list[str] | None = None) -> bool:
    """來源名稱或網址網域命中排除清單即視為非新聞來源。

    網域採「完全相同或為其子網域」比對，不可用單純的包含比對，
    否則 x.com 會誤傷 xxx.com.tw 之類的正常網域。

    來源名稱方面，Google 新聞有時標成 facebook.com、有時標成 Facebook，
    故同時比對完整網域與去掉 TLD 的品牌名；品牌名過短者（如 x.com 的 x）
    不納入名稱比對，避免命中任意媒體名稱。
    """
    if excludes is None:
        excludes = excluded_sources()
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    name = (source or "").lower()
    for x in excludes:
        if host == x or host.endswith("." + x):
            return True
        brand = x.split(".")[0]
        if x in name or (len(brand) >= 4 and brand in name):
            return True
    return False


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(_TAG_RE.sub("", s))
    return _WS_RE.sub(" ", s).strip()


def _parse_time(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime(*t[:6], tzinfo=timezone.utc).astimezone(TW)
    return datetime.now(TW)


def _get(url: str) -> bytes | None:
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=settings.http_timeout)
        r.raise_for_status()
        return r.content
    except Exception as e:  # noqa: BLE001 - 單一來源失敗不影響整體
        log.warning("抓取失敗 %s: %s", url, e)
        return None


def _fetch_rss(name: str, url: str) -> list[NewsItem]:
    raw = _get(url)
    if raw is None:
        return []
    feed = feedparser.parse(raw)
    items = []
    for e in feed.entries:
        title = clean_text(e.get("title"))
        link = e.get("link", "").strip()
        if not title or not link:
            continue
        summary = clean_text(e.get("summary") or e.get("description"))
        items.append(NewsItem(title=title, url=link, source=name,
                              published_at=_parse_time(e), summary=summary))
    log.info("RSS %-8s %3d 則  %s", name, len(items), url)
    return items


def _fetch_google(query: str, when: str) -> list[NewsItem]:
    url = GOOGLE_NEWS.format(q=quote_plus(query), when=when)
    raw = _get(url)
    if raw is None:
        return []
    feed = feedparser.parse(raw)
    excludes = excluded_sources()
    items = []
    for e in feed.entries:
        title = clean_text(e.get("title"))
        source = ""
        src = e.get("source")
        if src:
            source = src.get("title", "")
        # Google 新聞偶爾會收錄臉書等社群貼文，於此先行排除
        if is_excluded(source, e.get("link", ""), excludes):
            continue
        # Google 新聞標題格式為「標題 - 媒體名」，把尾巴去掉
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].strip()
        elif " - " in title:
            title, source = title.rsplit(" - ", 1)
        # Google 的 description 只是標題+媒體的 HTML，不當摘要使用
        items.append(NewsItem(title=title, url=e.get("link", ""),
                              source=source or "Google新聞",
                              published_at=_parse_time(e), summary=""))
    log.info("Google新聞 [%s] %d 則", query, len(items))
    return items


def fetch_all() -> list[NewsItem]:
    cfg = load_yaml("sources.yaml")
    jobs = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for s in cfg.get("rss", []) or []:
            jobs.append(pool.submit(_fetch_rss, s["name"], s["url"]))
        g = cfg.get("google_news") or {}
        for q in g.get("queries", []) or []:
            jobs.append(pool.submit(_fetch_google, q, g.get("when", "1d")))
        results = [item for j in jobs for item in j.result()]

    cutoff = datetime.now(TW) - timedelta(hours=settings.max_age_hours)
    fresh = [i for i in results if i.published_at >= cutoff]
    log.info("共抓取 %d 則，%d 小時內 %d 則", len(results), settings.max_age_hours, len(fresh))
    return fresh


_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:og:description|description)["\'][^>]*>', re.I)
_CONTENT_RE = re.compile(r'content=["\']([^"\']*)["\']', re.I)


def enrich_summary(item: NewsItem, min_len: int = 30) -> None:
    """摘要太短時，嘗試到原文抓 og:description 補上（Google 新聞轉址連結略過）。"""
    if len(item.summary) >= min_len or "news.google.com" in item.url:
        return
    raw = _get(item.url)
    if not raw:
        return
    text = raw[:200_000].decode("utf-8", errors="ignore")
    m = _META_RE.search(text)
    if m:
        c = _CONTENT_RE.search(m.group(0))
        if c:
            item.summary = clean_text(c.group(1))

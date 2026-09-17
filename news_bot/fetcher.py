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


def _yaml_list(key: str) -> list[str]:
    return [str(s) for s in (load_yaml("sources.yaml").get(key) or [])]


def _rss_names() -> set[str]:
    return {s["name"] for s in (load_yaml("sources.yaml").get("rss") or []) if s.get("name")}


def source_tier(source: str) -> int:
    """0＝設定檔中的官方 RSS 媒體、1＝其他原始媒體、2＝聚合轉載平台。

    同一事件常被聚合平台重複轉載，數量遠多於原始媒體，若不分級會把
    中央社、自由時報等原始報導整個擠掉（2026-09-17 實際推播的 30 則
    無一來自官方 RSS）。分級只影響「留哪一則」與排序，不會排除任何新聞。
    """
    name = (source or "").lower()
    if source in _rss_names():
        return 0
    if any(a.lower() in name for a in _yaml_list("aggregators")):
        return 2
    return 1


# 聚合平台常把原始媒體名接在標題尾端，例如「…嚴正駁斥 | 民視新聞網」。
_TAIL_RE = re.compile(r"\s*[|｜]\s*([^|｜]{1,12})\s*$")
# 判定尾端字串是否為媒體名（而非「產業」「生活」這類分類名）
_MEDIA_RE = re.compile(r"(報|網|社|視|台|刊|聞|傳媒|電視)$|^[A-Za-z][A-Za-z0-9.\- ]{2,19}$")


def split_outlet(title: str, source: str) -> tuple[str, str]:
    """把標題尾端的媒體名拆出來，回傳 (清理後標題, 來源)。

    尾端字串看起來像媒體名，且目前來源是聚合平台時才改用它；否則只清標題。
    """
    m = _TAIL_RE.search(title)
    if not m:
        return title, source
    tail = m.group(1).strip()
    cleaned = title[: m.start()].strip()
    if not cleaned:
        return title, source
    if _MEDIA_RE.search(tail) and source_tier(source) == 2:
        return cleaned, tail
    return cleaned, source


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
        # 聚合平台會把原始媒體名接在標題尾端，取回來當作真正的來源
        title, source = split_outlet(title, source)
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

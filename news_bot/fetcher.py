"""從各媒體 RSS 與 Google 新聞 RSS 抓取新聞。"""
from __future__ import annotations

import base64
import binascii
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


# 允許出現在網址中的字元；遇到其他位元組即停止，避免把 protobuf 的二進位雜訊一併擷取
_URL_IN_BLOB = re.compile(rb"https?://[A-Za-z0-9\-._~:/?#\[\]@!$&'()*+,;=%]{12,}")
_AU_RE = re.compile(r'data-n-au=["\']([^"\']+)["\']', re.I)
_CANONICAL_RE = re.compile(
    r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', re.I)
_REFRESH_RE = re.compile(r'url=(https?://[^"\'>\s]+)', re.I)


def _sane_url(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.scheme in ("http", "https") and "." in parts.netloc
            and GOOGLE_HOST not in parts.netloc and len(url) < 500)


def _follow_google_url(link: str) -> str:
    """實際連線跟隨轉址取得原文網址；失敗時回傳空字串。"""
    try:
        r = requests.get(link, headers={"User-Agent": UA},
                         timeout=settings.http_timeout, allow_redirects=True)
    except requests.RequestException as e:  # 單則還原失敗不影響其他新聞
        log.warning("Google 轉址還原失敗: %s", e)
        return ""
    if _sane_url(r.url):
        return r.url
    # 轉址頁改以 JavaScript 導向時，從 HTML 內找原文網址
    text = r.content[:100_000].decode("utf-8", errors="ignore")
    for pat in (_AU_RE, _CANONICAL_RE, _REFRESH_RE):
        m = pat.search(text)
        if m:
            cand = html.unescape(m.group(1))
            if _sane_url(cand):
                return cand
    return ""


def _decode_google_url(link: str) -> str:
    """離線解出轉址網址中夾帶的原文網址；解不出時回傳空字串。

    舊式 CBMi... 轉址的 base64 內容含有原文網址，新式的則沒有，
    因此僅作為 _follow_google_url() 連線失敗時的備援。
    """
    m = re.search(r"/articles/([A-Za-z0-9_-]{16,})", link)
    if not m:
        return ""
    seg = m.group(1)
    try:
        raw = base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))
    except (ValueError, binascii.Error):
        return ""
    for cand in _URL_IN_BLOB.findall(raw):
        url = cand.decode("ascii", "ignore")
        if _sane_url(url):
            return url
    return ""


def resolve_google_url(item: NewsItem) -> None:
    """把 Google 新聞的轉址連結換成原文網址。

    廠商格式會把網址直接顯示在訊息中，轉址網址長達數百字元且預覽卡片
    會指向 Google 而非原媒體。還原後亦可正常補抓摘要（enrich_summary
    原本會略過 Google 連結）。兩種方式都還原不出時維持原連結。
    """
    if not is_google_url(item.url):
        return
    for candidate in (_follow_google_url(item.url), _decode_google_url(item.url)):
        if candidate:
            item.url = candidate
            return
    log.info("無法還原原文網址，維持 Google 連結：%s", item.title[:40])


def resolve_all(items: list[NewsItem]) -> list[NewsItem]:
    """還原 Google 轉址連結，並濾掉還原後才看得出來的非新聞來源。"""
    google = [i for i in items if is_google_url(i.url)]
    if google:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(resolve_google_url, google))
    excludes = excluded_sources()
    kept = [i for i in items if not is_excluded(i.source, i.url, excludes)]
    if len(kept) != len(items):
        log.info("排除非新聞來源 %d 則", len(items) - len(kept))
    return kept


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

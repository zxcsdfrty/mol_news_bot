"""標題正規化與相似度去重（同一則新聞常被多家媒體 / Google 新聞重複收錄）。"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .models import NewsItem

# 去掉括號標籤（【獨家】、〔記者…〕、(影)、[圖] 等）、標點與空白
_BRACKET_RE = re.compile(r"[【\[〔〈(（][^】\]〕〉)）]{0,12}[】\]〕〉)）]")
_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)

SIMILARITY = 0.72   # 字元序列相似度（處理少量增刪字）
BIGRAM_DICE = 0.6   # 二字詞重疊度（處理詞序對調，如「基本工資調漲 勞動部宣布」）


def title_key(title: str) -> str:
    t = _BRACKET_RE.sub("", title)
    return _PUNCT_RE.sub("", t).lower()


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)}


def similar(a: str, b: str, threshold: float = SIMILARITY) -> bool:
    if not a or not b:
        return False
    if a == b or (min(len(a), len(b)) >= 10 and (a in b or b in a)):
        return True
    ba, bb = _bigrams(a), _bigrams(b)
    if ba and bb and 2 * len(ba & bb) / (len(ba) + len(bb)) >= BIGRAM_DICE:
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _quality(i: NewsItem) -> tuple:
    """同一新聞留哪一則：非 Google 轉址 > 有摘要 > 分數高。"""
    return ("news.google.com" not in i.url, len(i.summary) > 20, i.score)


def dedupe_batch(items: list[NewsItem]) -> list[NewsItem]:
    for i in items:
        i.title_key = title_key(i.title)
    by_url: dict[str, NewsItem] = {}
    for i in items:
        if i.url_hash not in by_url or _quality(i) > _quality(by_url[i.url_hash]):
            by_url[i.url_hash] = i

    kept: list[NewsItem] = []
    for i in sorted(by_url.values(), key=_quality, reverse=True):
        if not any(similar(i.title_key, k.title_key) for k in kept):
            kept.append(i)
    return kept

"""標題正規化與相似度去重（同一則新聞常被多家媒體 / Google 新聞重複收錄）。"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache

from .config import load_yaml
from .models import NewsItem

# 去掉括號標籤（【獨家】、〔記者…〕、(影)、[圖] 等）、標點與空白
_BRACKET_RE = re.compile(r"[【\[〔〈(（][^】\]〕〉)）]{0,12}[】\]〕〉)）]")
_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)

SIMILARITY = 0.72   # 字元序列相似度（處理少量增刪字）
BIGRAM_DICE = 0.6   # 二字詞重疊度（處理詞序對調，如「基本工資調漲 勞動部宣布」）
# 去掉機關名後的二字詞重疊係數。各家改寫幅度大時（同一則婚假新聞十餘種寫法），
# Dice 會因標題長短差異被拉低，改用重疊係數並排除每則都有的「勞動部」等詞。
# 調高會放過重複、調低會把不同事件併在一起（併掉者仍會入庫，只是不推播）。
ORG_OVERLAP = 0.30
ORG_MIN_BIGRAMS = 6  # 去掉機關名後太短者不適用重疊係數，避免零星字元湊巧命中


def title_key(title: str) -> str:
    t = _BRACKET_RE.sub("", title)
    return _PUNCT_RE.sub("", t).lower()


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)}


@lru_cache(maxsize=1)
def _org_keys() -> tuple[str, ...]:
    """機關與首長名稱，取自 keywords.yaml 的 core（首長異動時一併生效）。

    這些詞幾乎每則都出現，比對相似度前先移除，否則「勞動部」會讓所有
    標題都有基本相似度，真正區別事件的詞反而被稀釋。
    """
    core = (load_yaml("keywords.yaml").get("core") or {}).get("keywords") or []
    keys = {title_key(k) for k in core if k}
    # 長詞先移除，避免「勞動部長」被「勞動部」截掉後殘留「長」字
    return tuple(sorted((k for k in keys if k), key=len, reverse=True))


@lru_cache(maxsize=4096)
def _strip_orgs(key: str) -> str:
    for org in _org_keys():
        key = key.replace(org, "")
    return key


def similar(a: str, b: str, threshold: float = SIMILARITY) -> bool:
    if not a or not b:
        return False
    if a == b or (min(len(a), len(b)) >= 10 and (a in b or b in a)):
        return True
    ba, bb = _bigrams(a), _bigrams(b)
    if ba and bb and 2 * len(ba & bb) / (len(ba) + len(bb)) >= BIGRAM_DICE:
        return True
    sa, sb = _bigrams(_strip_orgs(a)), _bigrams(_strip_orgs(b))
    if (min(len(sa), len(sb)) >= ORG_MIN_BIGRAMS
            and len(sa & sb) / min(len(sa), len(sb)) >= ORG_OVERLAP):
        return True
    return SequenceMatcher(None, a, b).ratio() >= threshold


def _quality(i: NewsItem) -> tuple:
    """同一新聞留哪一則：非 Google 轉址 > 原始媒體（非聚合平台）> 有摘要 > 分數高。"""
    from .fetcher import source_tier  # 延後匯入，避免模組載入順序相依
    return ("news.google.com" not in i.url, -source_tier(i.source),
            len(i.summary) > 20, i.score)


def dedupe_batch(items: list[NewsItem]) -> list[NewsItem]:
    for i in items:
        i.title_key = title_key(i.title)
    by_url: dict[str, NewsItem] = {}
    for i in items:
        if i.url_hash not in by_url or _quality(i) > _quality(by_url[i.url_hash]):
            by_url[i.url_hash] = i

    # 單一連結分群：只跟「已保留的代表」比對，會漏掉需要透過中間寫法才串得起來
    # 的同一事件（A 與 C 不像，但兩者都像 B）。改為與群內任一則相符即併入同群，
    # 並在新的一則同時連到多群時把它們合併。
    clusters: list[list[NewsItem]] = []
    for item in sorted(by_url.values(), key=_quality, reverse=True):
        hits = [c for c in clusters
                if any(similar(item.title_key, m.title_key) for m in c)]
        if not hits:
            clusters.append([item])
            continue
        merged = [item]
        for c in hits:
            merged.extend(c)
            clusters.remove(c)
        clusters.append(merged)
    return [max(c, key=_quality) for c in clusters]

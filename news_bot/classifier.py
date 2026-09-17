"""關鍵字加權計分 → 判斷是否與勞動部相關、議題分類、輿情傾向。

規則：
1. 每個關鍵字只計一次；出現在標題時權重 × title_multiplier。
2. 排除詞（例：美國勞動部）會把對應的機關關鍵字命中扣回，
   但若同一篇在排除詞以外的地方仍出現該機關關鍵字，則照常計分。
3. 沒有命中任何「機關」關鍵字時，至少要命中 2 個不同的議題關鍵字才可能入選，
   避免單一泛用詞（如「時薪」）造成誤判。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .config import load_yaml
from .models import NewsItem


@dataclass
class Result:
    score: int = 0
    relevant: bool = False
    topics: list[str] = field(default_factory=list)
    matched: list[str] = field(default_factory=list)
    sentiment: str = "中性"


class Classifier:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or load_yaml("keywords.yaml")
        self.threshold = int(cfg.get("threshold", 8))
        self.title_mul = int(cfg.get("title_multiplier", 2))
        core = cfg.get("core", {})
        self.core_weight = int(core.get("weight", 10))
        self.core_keywords = list(core.get("keywords", []))
        self.excludes = list(cfg.get("exclude", []) or [])
        self.topics = {
            name: (int(t.get("weight", 3)), list(t.get("keywords", [])))
            for name, t in (cfg.get("topics") or {}).items()
        }
        s = cfg.get("sentiment") or {}
        self.neg = list(s.get("negative", []))
        self.pos = list(s.get("positive", []))

    @staticmethod
    def _count_outside(text: str, kw: str, patterns: list[str]) -> int:
        """計算 kw 出現在 patterns 以外位置的次數。"""
        masked = text
        for p in patterns:
            masked = masked.replace(p, "□" * len(p))
        return masked.count(kw)

    def _hit(self, kw: str, title: str, body: str, excl: list[str]) -> int:
        """回傳 0（未命中）、1（僅內文）、2（標題命中）。"""
        if excl:
            in_title = self._count_outside(title, kw, excl) > 0
            in_body = self._count_outside(body, kw, excl) > 0
        else:
            in_title, in_body = kw in title, kw in body
        return 2 if in_title else (1 if in_body else 0)

    def classify(self, title: str, summary: str = "") -> Result:
        r = Result()
        core_hit = False

        for kw in self.core_keywords:
            excl = [e["pattern"] for e in self.excludes if e.get("cancels") == kw]
            h = self._hit(kw, title, summary, excl)
            if h:
                core_hit = True
                r.matched.append(kw)
                r.score += self.core_weight * (self.title_mul if h == 2 else 1)

        topic_kw_count = 0
        for name, (w, kws) in self.topics.items():
            topic_score = 0
            for kw in kws:
                h = self._hit(kw, title, summary, [])
                if h:
                    topic_kw_count += 1
                    r.matched.append(kw)
                    topic_score += w * (self.title_mul if h == 2 else 1)
            if topic_score:
                r.score += topic_score
                r.topics.append((name, topic_score))

        # 議題依分數排序，只保留名稱
        r.topics = [n for n, _ in sorted(r.topics, key=lambda x: -x[1])]
        # 相同字串去重但保留順序（例：勞保 / 勞保年金 兩者都會列）
        r.matched = list(dict.fromkeys(r.matched))

        r.relevant = r.score >= self.threshold and (core_hit or topic_kw_count >= 2)

        text = f"{title} {summary}"
        neg = sum(1 for k in self.neg if k in text)
        pos = sum(1 for k in self.pos if k in text)
        r.sentiment = "負面" if neg > pos else ("正面" if pos > neg else "中性")
        return r

    def apply(self, item: NewsItem) -> bool:
        r = self.classify(item.title, item.summary)
        item.score, item.topics = r.score, r.topics
        item.matched_keywords, item.sentiment = r.matched, r.sentiment
        return r.relevant

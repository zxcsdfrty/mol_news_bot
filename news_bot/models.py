from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published_at: datetime
    summary: str = ""
    # 以下由分類器填入
    score: int = 0
    topics: list[str] = field(default_factory=list)
    matched_keywords: list[str] = field(default_factory=list)
    sentiment: str = "中性"
    # 以下由去重流程填入
    title_key: str = ""

    @property
    def url_hash(self) -> str:
        return hashlib.sha256(self.url.encode("utf-8")).hexdigest()

    def to_row(self) -> dict:
        return {
            "url_hash": self.url_hash,
            "url": self.url,
            "title": self.title,
            "summary": self.summary,
            "source": self.source,
            "published_at": self.published_at.isoformat(),
            "score": self.score,
            "topics": self.topics,
            "matched_keywords": self.matched_keywords,
            "sentiment": self.sentiment,
            "title_key": self.title_key,
        }

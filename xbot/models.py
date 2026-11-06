"""Data objects passed between collectors, agents, the store and the publisher."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

ItemKind = Literal["news", "data", "research", "dev", "governance", "x_post", "web"]
PostFormat = Literal["single", "thread", "quote", "reply"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def item_id_for(url: str | None, title: str) -> str:
    """Stable id: canonical URL when available, else normalized title."""
    if url:
        key = re.sub(r"[?#].*$", "", url.strip().lower()).rstrip("/")
        key = re.sub(r"^https?://(www\.)?", "", key)
    else:
        key = normalize_title(title)
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def normalize_title(title: str) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", title.lower())
    return re.sub(r"\s+", " ", t).strip()


class Item(BaseModel):
    """One candidate piece of content found by a collector or the scout."""

    id: str = ""
    source: str
    kind: ItemKind = "news"
    title: str
    url: str | None = None
    summary: str = ""
    published_at: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    weight: float = 1.0
    author: str | None = None
    x_post_id: str | None = None  # for quote / reply targets
    metrics: dict = Field(default_factory=dict)

    def model_post_init(self, __context) -> None:  # noqa: D401
        if not self.id:
            self.id = item_id_for(self.url, self.title)

    def as_prompt(self, max_summary: int = 900) -> str:
        parts = [
            f'<item id="{self.id}" kind="{self.kind}" source="{self.source}"'
            + (f' published="{self.published_at:%Y-%m-%d %H:%M}Z"' if self.published_at else "")
            + (f' weight="{self.weight}"' if self.weight != 1.0 else "")
            + ">",
            f"title: {self.title}",
        ]
        if self.url:
            parts.append(f"url: {self.url}")
        if self.author:
            parts.append(f"author: {self.author}")
        if self.tags:
            parts.append(f"tags: {', '.join(self.tags)}")
        if self.metrics:
            parts.append(f"metrics: {self.metrics}")
        if self.summary:
            parts.append(f"content: {self.summary[:max_summary]}")
        parts.append("</item>")
        return "\n".join(parts)


# ---------- agent outputs (validated against the strict tool schemas) ----------

class Finding(BaseModel):
    title: str
    url: str
    source: str
    summary: str
    theme: str
    published: str = ""


class Pick(BaseModel):
    item_ids: list[str]
    topic: str
    angle: str
    format: PostFormat
    theme: str
    score: int
    why: str


class DraftVariant(BaseModel):
    posts: list[str]
    hook_style: str = ""


class Review(BaseModel):
    verdict: Literal["approve", "reject"]
    best_variant: int
    final_posts: list[str]
    score: int
    issues: list[str]
    disclosure_added: bool = False


class Draft(BaseModel):
    """A post (or thread) waiting for approval / publishing."""

    id: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    status: Literal["pending", "approved", "rejected", "posted", "failed", "draft"] = "pending"
    format: PostFormat = "single"
    posts: list[str]
    topic: str = ""
    angle: str = ""
    theme: str = ""
    item_ids: list[str] = Field(default_factory=list)
    source_url: str | None = None
    target_post_id: str | None = None  # quote/reply target
    curator_score: int = 0
    critic_score: int = 0
    issues: list[str] = Field(default_factory=list)
    posted_ids: list[str] = Field(default_factory=list)
    posted_at: datetime | None = None
    error: str | None = None
    telegram_message_id: int | None = None

"""RSS/Atom feeds (news sites, research blogs, Discourse governance forums)."""
from __future__ import annotations

import html
import logging
import re
from calendar import timegm
from datetime import datetime, timedelta, timezone

import feedparser

from ..models import Item
from .http import get

log = logging.getLogger("xbot.sources.rss")
_TAG = re.compile(r"<[^>]+>")


def clean(text: str) -> str:
    text = html.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _kind(tags: list[str]) -> str:
    if "research" in tags:
        return "research"
    if "dev" in tags:
        return "dev"
    if "governance" in tags or "forum" in tags:
        return "governance"
    return "news"


def parse_feed(content: bytes, feed: dict, max_age_hours: int) -> list[Item]:
    parsed = feedparser.parse(content)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    tags = list(feed.get("tags", []))
    out: list[Item] = []
    for e in parsed.entries[:40]:
        ts = e.get("published_parsed") or e.get("updated_parsed")
        published = datetime.fromtimestamp(timegm(ts), tz=timezone.utc) if ts else None
        if published and published < cutoff:
            continue
        summary = clean(e.get("summary", "") or "")
        if not summary and e.get("content"):
            summary = clean(e["content"][0].get("value", ""))
        title = clean(e.get("title", ""))
        if not title:
            continue
        out.append(Item(
            source=feed["name"],
            kind=_kind(tags),
            title=title,
            url=e.get("link"),
            summary=summary[:1500],
            published_at=published,
            tags=tags,
            weight=float(feed.get("weight", 1.0)),
            author=e.get("author"),
        ))
    return out


def collect(feeds: list[dict], max_age_hours: int) -> list[Item]:
    items: list[Item] = []
    for feed in feeds:
        try:
            r = get(feed["url"], timeout=25)
            items.extend(parse_feed(r.content, feed, max_age_hours))
        except Exception as e:
            log.warning("feed %s failed: %s", feed.get("name"), e)
    return items

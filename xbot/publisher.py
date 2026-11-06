"""Posting rules (windows, caps, spacing) and the act of publishing a draft to X."""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import PostingCfg, Settings
from .models import Draft, utcnow
from .store import Store
from .textutil import weighted_len

log = logging.getLogger("xbot.publisher")


def _parse_window(w: str) -> tuple[time, time]:
    a, b = w.split("-")
    return time.fromisoformat(a.strip()), time.fromisoformat(b.strip())


def in_window(local: datetime, windows: list[str]) -> bool:
    t = local.time()
    for w in windows:
        a, b = _parse_window(w)
        if (a <= t <= b) if a <= b else (t >= a or t <= b):
            return True
    return False


def can_post_now(store: Store, s: Settings, now: datetime | None = None) -> tuple[bool, str]:
    now = now or utcnow()
    tz = ZoneInfo(s.timezone)
    local = now.astimezone(tz)
    if store.get("paused") == "1":
        return False, "paused"
    if not in_window(local, s.posting.windows):
        return False, f"outside posting windows ({local:%H:%M} {s.timezone})"
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    today = store.posted_since(day_start.astimezone(ZoneInfo("UTC")))
    if len(today) >= s.posting.max_posts_per_day:
        return False, f"daily cap reached ({len(today)})"
    last = store.posted_since(now - timedelta(minutes=s.posting.min_minutes_between_posts))
    if last:
        return False, "too soon after last post"
    return True, "ok"


def plan_posts(d: Draft, p: PostingCfg) -> tuple[list[str], str | None]:
    """Returns (posts to publish in order, optional source-link self-reply)."""
    posts = list(d.posts)
    link_reply = None
    if d.source_url and d.format in ("single", "thread", "quote"):
        if p.link_strategy == "inline":
            candidate = f"{posts[-1]}\n\n{d.source_url}"
            if weighted_len(candidate) <= p.max_post_chars:
                posts[-1] = candidate
            else:
                link_reply = f"source: {d.source_url}"
        elif p.link_strategy == "reply":
            link_reply = f"source: {d.source_url}"
    return posts, link_reply


def publish(d: Draft, x, s: Settings, dry_run: bool = False) -> Draft:
    posts, link_reply = plan_posts(d, s.posting)
    if dry_run or x is None:
        log.info("[dry-run] would post draft #%s (%s):\n%s%s", d.id, d.format, "\n---\n".join(posts),
                 f"\n+ self-reply: {link_reply}" if link_reply else "")
        return d
    try:
        if d.format == "reply":
            ids = [x.post(posts[0], reply_to=d.target_post_id)]
        elif d.format == "quote":
            ids = x.post_thread(posts, quote=d.target_post_id)
        else:
            ids = x.post_thread(posts)
        if link_reply:
            try:
                ids.append(x.post(link_reply, reply_to=ids[-1]))
            except Exception as e:  # the main post is already live; don't mark the whole draft failed
                log.warning("link reply failed for draft #%s: %s", d.id, e)
        d.posted_ids = ids
        d.status = "posted"
        d.posted_at = utcnow()
        log.info("posted draft #%s -> https://x.com/i/status/%s", d.id, ids[0])
    except Exception as e:
        partial = getattr(e, "ids", None)
        if partial:  # part of a thread is live: record it so it is never re-posted from scratch
            d.posted_ids = list(partial)
            d.posted_at = utcnow()
        d.status = "failed"
        d.error = str(e)[:500]
        log.error("posting draft #%s failed: %s", d.id, e)
    return d

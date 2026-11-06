"""GitHub releases for core crypto repos -> 'dev' items."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from ..models import Item
from .http import get
from .rss import clean

log = logging.getLogger("xbot.sources.github")


def collect(repos: list[str], max_age_hours: int) -> list[Item]:
    headers = {"Accept": "application/vnd.github+json"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(max_age_hours, 72))
    items: list[Item] = []
    for repo in repos:
        try:
            rels = get(f"https://api.github.com/repos/{repo}/releases?per_page=5", headers=headers).json()
        except Exception as e:
            log.warning("github %s failed: %s", repo, e)
            continue
        for rel in rels:
            if rel.get("prerelease") or rel.get("draft") or not rel.get("published_at"):
                continue
            published = datetime.fromisoformat(rel["published_at"].replace("Z", "+00:00"))
            if published < cutoff:
                continue
            body = clean(rel.get("body") or "")
            items.append(Item(
                source=f"GitHub {repo}",
                kind="dev",
                title=f"{repo} released {rel.get('name') or rel.get('tag_name')}",
                url=rel.get("html_url"),
                summary=body[:1500],
                published_at=published,
                tags=["dev"],
                weight=1.0,
            ))
    return items

"""Collectors: each returns a list[Item]. Failures are logged, never fatal."""
from __future__ import annotations

import logging

from ..config import Config
from ..models import Item
from . import coingecko, defillama, github, rss

log = logging.getLogger("xbot.sources")


def collect_all(cfg: Config, x_client=None) -> list[Item]:
    src = cfg.sources
    items: list[Item] = []
    jobs = [
        ("rss", lambda: rss.collect(src.get("rss", []), cfg.settings.pipeline.item_max_age_hours)),
        ("defillama", lambda: defillama.collect(src.get("defillama", {}))),
        ("coingecko", lambda: coingecko.collect(src.get("coingecko", {}))),
        ("github", lambda: github.collect(src.get("github_releases", []), cfg.settings.pipeline.item_max_age_hours)),
    ]
    if x_client is not None and (src.get("x_watch") or {}).get("enabled"):
        jobs.append(("x_watch", lambda: x_client.watch_items(src["x_watch"], cfg.settings.pipeline.item_max_age_hours)))
    for name, job in jobs:
        try:
            got = job()
            log.info("collector %-10s %3d items", name, len(got))
            items.extend(got)
        except Exception as e:  # one broken source must not stop the run
            log.warning("collector %s failed: %s", name, e)
    return items

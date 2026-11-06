"""CoinGecko trending (free endpoint) -> one 'data' item summarizing what's trending."""
from __future__ import annotations

from datetime import datetime, timezone

from ..models import Item, item_id_for
from .http import get


def collect(cfg: dict) -> list[Item]:
    if not cfg.get("trending", True):
        return []
    d = get("https://api.coingecko.com/api/v3/search/trending").json()
    coins = [c["item"] for c in d.get("coins", [])][:10]
    cats = [c.get("name") for c in d.get("categories", [])][:6]
    if not coins:
        return []
    lines = []
    for c in coins:
        data = c.get("data") or {}
        chg = (data.get("price_change_percentage_24h") or {}).get("usd")
        chg_s = f"{chg:+.1f}% 24h" if isinstance(chg, (int, float)) else ""
        lines.append(f"{c['name']} (${c['symbol']}, mcap rank {c.get('market_cap_rank')}) {chg_s}".strip())
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:00")
    title = f"CoinGecko trending coins {today} UTC"
    return [Item(
        id=item_id_for(None, title),
        source="CoinGecko trending",
        kind="data",
        title=title,
        url="https://www.coingecko.com/en/highlights/trending-crypto",
        summary="Trending coins: " + "; ".join(lines) + (f". Trending categories: {', '.join(cats)}" if cats else ""),
        published_at=datetime.now(timezone.utc),
        tags=["markets"],
        weight=0.5,
    )]

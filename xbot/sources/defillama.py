"""DefiLlama (free API): TVL movers, RWA yield board, stablecoin supply moves -> 'data' items.

Data items are generated at most once per day per subject (the id includes the date), so the
curator sees fresh numbers daily without the same item reappearing every run.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from ..models import Item, item_id_for
from .http import get

log = logging.getLogger("xbot.sources.defillama")


def usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= div:
            return f"${x / div:.2f}{suf}"
    return f"${x:.0f}"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _data_item(title: str, summary: str, url: str, tags: list[str], weight: float = 1.0, metrics=None) -> Item:
    return Item(
        id=item_id_for(None, title),
        source="DefiLlama",
        kind="data",
        title=title,
        url=url,
        summary=summary,
        published_at=datetime.now(timezone.utc),
        tags=tags,
        weight=weight,
        metrics=metrics or {},
    )


def tvl_movers(protocols: list[dict], cfg: dict) -> list[Item]:
    cats = set(cfg.get("categories", []))
    min_tvl = float(cfg.get("min_tvl_usd", 5e7))
    thr = float(cfg.get("tvl_move_pct_7d", 15))
    movers = [
        p for p in protocols
        if p.get("category") in cats and (p.get("tvl") or 0) >= min_tvl
        and isinstance(p.get("change_7d"), (int, float)) and abs(p["change_7d"]) >= thr
    ]
    movers.sort(key=lambda p: -abs(p["change_7d"]) * (p.get("tvl") or 0) ** 0.5)
    out = []
    for p in movers[:8]:
        cat = p["category"]
        tag = "rwa" if cat == "RWA" else "depin" if cat == "DePIN" else "defi"
        title = f"{p['name']} ({cat}) TVL {p['change_7d']:+.1f}% over 7d to {usd(p.get('tvl'))} [{_today()}]"
        chains = ", ".join((p.get("chains") or [])[:5])
        summary = (
            f"{p['name']} ({cat}) TVL is {usd(p.get('tvl'))}, {p['change_7d']:+.1f}% over 7 days"
            + (f" and {p['change_1d']:+.1f}% over 24h" if isinstance(p.get("change_1d"), (int, float)) else "")
            + f". Chains: {chains}."
            + (f" X: @{p['twitter']}." if p.get("twitter") else "")
        )
        out.append(_data_item(title, summary, f"https://defillama.com/protocol/{p.get('slug')}", [tag, "defi"],
                              1.1, {"tvl": p.get("tvl"), "change_7d": round(p["change_7d"], 2)}))
    return out


def rwa_yield_board(protocols: list[dict], pools: list[dict], cfg: dict) -> list[Item]:
    if not cfg.get("enabled", True):
        return []
    cat: dict[str, str] = {}
    for p in protocols:
        cat[p.get("slug", "")] = p.get("category")
        parent = (p.get("parentProtocol") or "").replace("parent#", "")
        if parent:
            cat.setdefault(parent, p.get("category"))
    min_tvl = float(cfg.get("min_tvl_usd", 2e7))
    rwa = [x for x in pools if cat.get(x.get("project")) == "RWA" and (x.get("tvlUsd") or 0) >= min_tvl
           and isinstance(x.get("apy"), (int, float))]
    if not rwa:
        return []
    rwa.sort(key=lambda x: -x["tvlUsd"])
    top = rwa[: int(cfg.get("top_n", 12))]
    total = sum(x["tvlUsd"] for x in rwa)
    wavg = sum(x["apy"] * x["tvlUsd"] for x in rwa) / total
    lines = [
        f"{x['project']} {x['symbol']} on {x['chain']}: APY {x['apy']:.2f}%"
        + (f" (30d avg {x['apyMean30d']:.2f}%)" if isinstance(x.get("apyMean30d"), (int, float)) else "")
        + f", TVL {usd(x['tvlUsd'])}"
        for x in top
    ]
    movers = [x for x in rwa if isinstance(x.get("apyPct7D"), (int, float)) and abs(x["apyPct7D"]) >= 1.0]
    mover_lines = [f"{x['project']} {x['symbol']} ({x['chain']}) APY {x['apyPct7D']:+.2f}pp over 7d to {x['apy']:.2f}%"
                   for x in sorted(movers, key=lambda x: -abs(x["apyPct7D"]))[:5]]
    title = f"RWA yield board {_today()}: {len(rwa)} pools, {usd(total)} tracked, TVL-weighted APY {wavg:.2f}%"
    summary = "Top RWA pools by TVL: " + "; ".join(lines) + "."
    if mover_lines:
        summary += " Biggest APY moves: " + "; ".join(mover_lines) + "."
    return [_data_item(title, summary, "https://defillama.com/yields?category=RWA", ["rwa_yield", "rwa"], 1.3,
                       {"pools": len(rwa), "tvl": round(total), "wavg_apy": round(wavg, 3)})]


def stablecoin_moves(assets: list[dict], cfg: dict) -> list[Item]:
    if not cfg.get("enabled", True):
        return []
    thr = float(cfg.get("move_pct_7d", 5))
    out = []
    rows = []
    for a in assets:
        now = (a.get("circulating") or {}).get(a.get("pegType", "peggedUSD"))
        prev = (a.get("circulatingPrevWeek") or {}).get(a.get("pegType", "peggedUSD"))
        if not now or not prev or now < 2e8:
            continue
        chg = (now - prev) / prev * 100
        if abs(chg) >= thr:
            rows.append((a, now, chg))
    rows.sort(key=lambda r: -abs(r[2]) * r[1] ** 0.5)
    for a, now, chg in rows[:5]:
        title = f"Stablecoin {a['symbol']} supply {chg:+.1f}% over 7d to {usd(now)} [{_today()}]"
        summary = (f"{a['name']} ({a['symbol']}, {a.get('pegMechanism')}) circulating supply is {usd(now)}, "
                   f"{chg:+.1f}% week over week.")
        out.append(_data_item(title, summary, "https://defillama.com/stablecoins", ["defi", "stablecoins"], 1.0,
                              {"supply": round(now), "change_7d_pct": round(chg, 2)}))
    return out


def collect(cfg: dict) -> list[Item]:
    if not cfg.get("enabled", True):
        return []
    items: list[Item] = []
    protocols = get("https://api.llama.fi/protocols", timeout=60).json()
    items += tvl_movers(protocols, cfg)
    if (cfg.get("rwa_yields") or {}).get("enabled", True):
        try:
            pools = get("https://yields.llama.fi/pools", timeout=90).json()["data"]
            items += rwa_yield_board(protocols, pools, cfg.get("rwa_yields") or {})
        except Exception as e:
            log.warning("yields failed: %s", e)
    if (cfg.get("stablecoins") or {}).get("enabled", True):
        try:
            assets = get("https://stablecoins.llama.fi/stablecoins", timeout=60).json()["peggedAssets"]
            items += stablecoin_moves(assets, cfg.get("stablecoins") or {})
        except Exception as e:
            log.warning("stablecoins failed: %s", e)
    return items

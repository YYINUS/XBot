"""Cheap, no-LLM pre-ranking so the curator sees the most promising items first."""
from __future__ import annotations

import math
import re

from .models import Item, utcnow

THEME_KEYWORDS = {
    "rwa": r"\brwa\b|tokeni[sz]|treasur(y|ies)|money.market|\bbuidl\b|\bbenji\b|\busyc\b|\bustb\b|\bousg\b|"
           r"private credit|real.world asset|\bondo\b|centrifuge|maple|securitiz|bond|fund on",
    "rwa_yield": r"\byield|\bapy\b|\bapr\b|interest|coupon|t-bill|basis",
    "defi": r"\bdefi\b|lending|borrow|stablecoin|\bcdp\b|vault|liquidity|\bdex\b|\bamm\b|aave|morpho|pendle|"
            r"ethena|\bsky\b|maker|uniswap|curve|restak|eigen|lido",
    "depin": r"\bdepin\b|helium|hivemapper|render|io\.net|akash|grass|filecoin|arweave|physical infra|"
             r"\bgpu\b|compute network|wireless",
    "protocol": r"protocol|governance|proposal|tokenomics|\bmev\b|upgrade|mainnet|hard ?fork|rollup|\bl2\b|"
                r"sequencer|validator|consensus|bridge",
    "dev": r"\beip-?\d+|client|release|\bv\d+\.\d+|reth|geth|agave|firedancer|foundry|solidity|\bevm\b|"
           r"\bsvm\b|zk|prover|devnet|testnet|audit|vulnerab|bug|exploit|post.mortem",
}
_PATTERNS = {k: re.compile(v, re.I) for k, v in THEME_KEYWORDS.items()}
LOW_SIGNAL = re.compile(r"price prediction|top \d+ (coins|altcoins)|sponsored|press release|\bwhale alert\b|"
                        r"^live updates", re.I)


def relevance(item: Item) -> float:
    text = f"{item.title} {item.summary[:600]}"
    hits = sum(1 for p in _PATTERNS.values() if p.search(text))
    base = 0.35 + min(hits, 3) * 0.35
    if LOW_SIGNAL.search(item.title):
        base *= 0.3
    if item.kind in ("data", "research", "dev", "web", "governance"):
        base += 0.2
    return base


def rank(items: list[Item], limit: int) -> list[Item]:
    now = utcnow()

    def score(it: Item) -> float:
        age_h = (now - it.published_at).total_seconds() / 3600 if it.published_at else 12
        recency = math.exp(-max(age_h, 0) / 36)
        return relevance(it) * it.weight * (0.4 + 0.6 * recency)

    ranked = sorted(items, key=score, reverse=True)
    # keep source diversity: at most ~1/4 of the slots from one source
    cap = max(4, limit // 4)
    per_src: dict[str, int] = {}
    out = []
    for it in ranked:
        if per_src.get(it.source, 0) >= cap:
            continue
        per_src[it.source] = per_src.get(it.source, 0) + 1
        out.append(it)
        if len(out) >= limit:
            break
    return out

"""X-specific text checks: weighted length, links, hashtags, emojis, banned phrases."""
from __future__ import annotations

import re

URL_RE = re.compile(r"https?://\S+")
HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F2FF\U0001F900-\U0001F9FF⭐⬆⬇]"
)
URL_WEIGHT = 23


def _char_weight(ch: str) -> int:
    cp = ord(ch)
    # twitter-text v3: these ranges weigh 1, everything else (CJK, emoji, ...) weighs 2
    if cp <= 4351 or 8192 <= cp <= 8205 or 8208 <= cp <= 8223 or 8242 <= cp <= 8247:
        return 1
    return 2


def weighted_len(text: str) -> int:
    total = 0
    last = 0
    for m in URL_RE.finditer(text):
        total += sum(_char_weight(c) for c in text[last:m.start()]) + URL_WEIGHT
        last = m.end()
    total += sum(_char_weight(c) for c in text[last:])
    return total


def lint_post(text: str, *, max_chars: int, banned: list[str], max_hashtags: int, max_emojis: int,
              allow_links: bool = False) -> list[str]:
    issues = []
    n = weighted_len(text)
    if n > max_chars:
        issues.append(f"too long: {n}/{max_chars}")
    if not text.strip():
        issues.append("empty post")
    if not allow_links and URL_RE.search(text):
        issues.append("contains a link")
    if len(HASHTAG_RE.findall(text)) > max_hashtags:
        issues.append("too many hashtags")
    if len(EMOJI_RE.findall(text)) > max_emojis:
        issues.append("too many emojis")
    low = text.lower()
    for phrase in banned:
        p = phrase.lower()
        hit = (p in low) if not p.isalnum() else re.search(rf"(?<!\w){re.escape(p)}(?!\w)", low)
        if hit:
            issues.append(f"banned phrase: {phrase}")
    return issues

"""Prompt loading/rendering. Uses {name} placeholders replaced literally (safe with JSON/braces)."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .config import PROMPTS_DIR, Config

_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")


def render(template: str, **values) -> str:
    def sub(m: re.Match) -> str:
        key = m.group(1)
        return str(values[key]) if key in values else m.group(0)

    return _PLACEHOLDER.sub(sub, template)


def load(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text()


def _list(xs: list[str]) -> str:
    return ", ".join(xs) if xs else "(none)"


def system_prompt(cfg: Config, role: str, **role_values) -> str:
    """core rules + voice guide + role prompt. Kept stable within a day so prompt caching hits."""
    s = cfg.settings
    common = dict(
        handle=s.handle,
        affiliations=_list(s.compliance.affiliations),
        avoid_tokens=_list(s.compliance.avoid_tokens),
        banned_phrases=_list(s.compliance.banned_phrases),
        max_chars=s.posting.max_post_chars,
        max_hashtags=s.compliance.max_hashtags,
        max_emojis=s.compliance.max_emojis,
        link_strategy=s.posting.link_strategy,
        max_age_hours=s.pipeline.item_max_age_hours,
        today=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        language=s.language,
    )
    core = render(load("core"), persona=cfg.persona(), **common)
    lang_note = {
        "zh": "\n\nWrite all posts in Simplified Chinese (keep tickers, protocol names and technical terms in English).",
        "bilingual": "\n\nFollow the voice guide on when to write in English vs Chinese; default to English.",
    }.get(s.language, "")
    role_text = render(load(role), **common, **role_values)
    return f"{core}{lang_note}\n\n{role_text}"

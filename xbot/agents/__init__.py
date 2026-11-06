"""The agent team. Each agent = one role prompt + one strict submit tool."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .. import prompts
from ..config import Config
from ..llm import LLM, WEB_SEARCH_TOOL, submit_tool
from ..models import DraftVariant, Finding, Item, Pick, Review


def obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or list(props), "additionalProperties": False}


STR = {"type": "string"}
INT = {"type": "integer"}
BOOL = {"type": "boolean"}


def arr(items: dict) -> dict:
    return {"type": "array", "items": items}


THEMES = ["rwa", "rwa_yield", "defi", "depin", "protocol", "dev", "research", "markets", "regulation", "other"]
THEME = {"type": "string", "enum": THEMES}
FORMAT = {"type": "string", "enum": ["single", "thread", "quote", "reply"]}

# ---------------------------------------------------------------- Scout
SCOUT_TOOL = submit_tool(
    "submit_findings",
    "Submit the notable findings from your web research.",
    obj({"findings": arr(obj({
        "title": STR, "url": STR, "source": STR, "summary": STR, "theme": THEME,
        "published": {"type": "string", "description": "publication date if known, else empty"},
    }))}),
)


def scout(cfg: Config, llm: LLM, known_titles: list[str]) -> list[Item]:
    sc = cfg.sources.get("scout") or {}
    system = prompts.system_prompt(
        cfg, "scout",
        lookback_hours=cfg.settings.pipeline.item_max_age_hours,
        focus="\n".join(f"- {f}" for f in sc.get("focus", [])),
        known="(see user message)",
        max_searches=sc.get("max_searches", 10),
    )
    user = (
        f"Current time: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.\n"
        "Already known stories (skip these):\n<known>\n" + "\n".join(f"- {t}" for t in known_titles[:120])
        + "\n</known>\nFind what's new and notable."
    )
    tool = dict(WEB_SEARCH_TOOL, max_uses=int(sc.get("max_searches", 10)))
    data = llm.structured(agent="scout", cfg=cfg.settings.model_for("scout"), system=system, user=user,
                          tool=SCOUT_TOOL, server_tools=[tool], max_turns=12)
    items = []
    for f in data.get("findings", []):
        f = Finding(**f)
        items.append(Item(source=f"Scout: {f.source}", kind="web", title=f.title, url=f.url, summary=f.summary,
                          tags=[f.theme], weight=1.1, published_at=datetime.now(timezone.utc)))
    return items


# ---------------------------------------------------------------- Curator
CURATOR_TOOL = submit_tool(
    "submit_picks",
    "Submit the items worth posting, each with an angle and format. May be empty.",
    obj({
        "picks": arr(obj({
            "item_ids": arr(STR), "topic": STR, "angle": STR, "format": FORMAT,
            "theme": THEME, "score": INT, "why": STR,
        })),
        "skipped_note": {"type": "string", "description": "one line on why the rest were skipped"},
    }),
)


def curate(cfg: Config, llm: LLM, items: list[Item], recent_posts: list[str], performance: str,
           allow_replies: bool) -> list[Pick]:
    p = cfg.settings.pipeline
    thread_note = "" if cfg.settings.posting.allow_threads else " (threads are DISABLED: never choose thread)"
    system = prompts.system_prompt(
        cfg, "curator", recent_posts="(see user message)", performance="(see user message)",
        picks=p.picks_per_run, min_score=p.min_score, thread_note=thread_note,
    )
    user = (
        "<recent_posts>\n" + ("\n".join(f"- {t}" for t in recent_posts) or "(none yet)") + "\n</recent_posts>\n\n"
        "<performance>\n" + (performance or "(no data yet)") + "\n</performance>\n\n"
        + ("" if allow_replies else "Quote/reply formats are disabled this run.\n\n")
        + f"Candidate items ({len(items)}):\n\n" + "\n\n".join(i.as_prompt() for i in items)
    )
    data = llm.structured(agent="curator", cfg=cfg.settings.model_for("curator"), system=system, user=user,
                          tool=CURATOR_TOOL)
    valid = {i.id: i for i in items}
    picks = []
    for raw in data.get("picks", []):
        pk = Pick(**raw)
        pk.item_ids = [i for i in pk.item_ids if i in valid]
        if not pk.item_ids or pk.score < p.min_score:
            continue
        has_x = any(valid[i].kind == "x_post" for i in pk.item_ids)
        if pk.format in ("quote", "reply") and (not has_x or not allow_replies):
            pk.format = "single"
        if pk.format == "thread" and not cfg.settings.posting.allow_threads:
            pk.format = "single"
        picks.append(pk)
    picks.sort(key=lambda x: -x.score)
    return picks[: p.picks_per_run]


# ---------------------------------------------------------------- Writer / Replier
DRAFTS_TOOL = submit_tool(
    "submit_drafts",
    "Submit the draft variants. Each variant is a list of posts (1 for single/quote/reply, several for a thread).",
    obj({"variants": arr(obj({"posts": arr(STR), "hook_style": STR}))}),
)


def _exemplars(cfg: Config, n: int = 8) -> str:
    if not cfg.exemplars_path.exists():
        return ""
    try:
        ex = json.loads(cfg.exemplars_path.read_text())[:n]
    except Exception:
        return ""
    return "\n".join(f"<example>{e['text'] if isinstance(e, dict) else e}</example>" for e in ex)


def write(cfg: Config, llm: LLM, pick: Pick, items: list[Item], feedback: str = "") -> list[DraftVariant]:
    s = cfg.settings
    role = "replier" if pick.format in ("quote", "reply") else "writer"
    thread_rule = (f"only if the format is thread; 2-{s.posting.max_thread_len} posts"
                   if s.posting.allow_threads else "threads are disabled; always 1 post")
    system = prompts.system_prompt(cfg, role, variants=s.pipeline.variants_per_pick, thread_rule=thread_rule)
    ex = _exemplars(cfg)
    user = (
        (f"Real posts by the owner, for style reference only:\n{ex}\n\n" if ex else "")
        + f"Pick from the curator:\n- topic: {pick.topic}\n- angle: {pick.angle}\n- format: {pick.format}\n"
        f"- theme: {pick.theme}\n\nSource items:\n\n" + "\n\n".join(i.as_prompt(2500) for i in items)
        + (f"\n\nFeedback on a previous attempt (fix these):\n{feedback}" if feedback else "")
    )
    data = llm.structured(agent=role, cfg=s.model_for(role), system=system, user=user, tool=DRAFTS_TOOL)
    out = []
    for v in data.get("variants", []):
        v = DraftVariant(**v)
        v.posts = [p.strip() for p in v.posts if p.strip()]
        if pick.format != "thread":
            v.posts = v.posts[:1]
        else:
            v.posts = v.posts[: s.posting.max_thread_len]
        if v.posts:
            out.append(v)
    return out


# ---------------------------------------------------------------- Critic
REVIEW_TOOL = submit_tool(
    "submit_review",
    "Submit your review of the draft variants.",
    obj({
        "verdict": {"type": "string", "enum": ["approve", "reject"]},
        "best_variant": INT,
        "final_posts": arr(STR),
        "score": INT,
        "issues": arr(STR),
        "disclosure_added": BOOL,
    }),
)


def critique(cfg: Config, llm: LLM, pick: Pick, items: list[Item], variants: list[DraftVariant],
             lint: dict[int, list[str]]) -> Review:
    system = prompts.system_prompt(cfg, "critic")
    vs = []
    for i, v in enumerate(variants):
        posts = "\n".join(f"  <post n={j + 1}>{p}</post>" for j, p in enumerate(v.posts))
        auto = "; ".join(lint.get(i, [])) or "none"
        vs.append(f'<variant index="{i}" automated_lint="{auto}">\n{posts}\n</variant>')
    user = (
        f"Format: {pick.format}\nAngle: {pick.angle}\n\nSource items:\n\n"
        + "\n\n".join(i.as_prompt(2500) for i in items) + "\n\nDraft variants:\n\n" + "\n\n".join(vs)
    )
    data = llm.structured(agent="critic", cfg=cfg.settings.model_for("critic"), system=system, user=user,
                          tool=REVIEW_TOOL)
    r = Review(**data)
    if not (0 <= r.best_variant < len(variants)):
        r.best_variant = 0
    if not r.final_posts:
        r.final_posts = variants[r.best_variant].posts
    return r


# ---------------------------------------------------------------- Profiler
PROFILE_TOOL = submit_tool(
    "submit_profile",
    "Submit the voice guide and supporting analysis.",
    obj({
        "voice_guide_markdown": {"type": "string", "description": "Complete markdown voice guide, same section "
                                 "structure as: Who I am on X / Core themes / Tone / Format habits / Avoid / "
                                 "What performs best"},
        "language": {"type": "string", "enum": ["en", "zh", "bilingual"]},
        "exemplar_post_ids": arr(STR),
        "top_themes": arr(STR),
        "suggested_watch_accounts": arr(STR),
        "notes_for_owner": STR,
    }),
)


def profile(cfg: Config, llm: LLM, posts: list[dict]) -> dict:
    system = prompts.system_prompt(cfg, "profiler")
    rows = []
    for p in posts:
        text = p["text"].replace("\n", " / ")
        rows.append(f'<post id="{p["id"]}" type="{p["type"]}" date="{p.get("created_at", "")}" '
                    f'likes="{p["likes"]}" reposts="{p["reposts"]}" replies="{p.get("replies", 0)}" '
                    f'impressions="{p.get("impressions", 0)}">{text}</post>')
    user = (f"The owner's posts ({len(rows)}), most recent first. The current voice guide in the system prompt is "
            "only a seed; replace it with what the data shows.\n\n" + "\n".join(rows))
    return llm.structured(agent="profiler", cfg=cfg.settings.model_for("profiler"), system=system, user=user,
                          tool=PROFILE_TOOL)

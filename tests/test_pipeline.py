from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import FakeLLM, FakeX
from xbot import prompts
from xbot.llm import LLM, LLMError, RefusalError
from xbot.models import Draft, Item
from xbot.pipeline import Bot
from xbot.publisher import can_post_now, in_window, plan_posts
from xbot.store import Store
from xbot.telegram import Action, Telegram


def _items():
    now = datetime.now(timezone.utc)
    return [
        Item(source="DefiLlama", kind="data", title="RWA yield board: TVL-weighted APY 4.78%",
             url="https://defillama.com/yields?category=RWA", summary="BUIDL 3.85%, USDY 3.75%", published_at=now),
        Item(source="X @rwa_xyz", kind="x_post", title="@rwa_xyz: tokenized treasuries hit $10B",
             url="https://x.com/rwa_xyz/status/555", summary="tokenized treasuries hit $10B", x_post_id="555",
             published_at=now),
    ]


def _llm(verdict="approve", posts=None):
    def picks(user):
        import re
        found = dict((k, i) for i, k in re.findall(r'<item id="([0-9a-f]+)" kind="(\w+)"', user))
        first = next(iter(found.values()))
        data_id, x_id = found.get("data", first), found.get("x_post", first)
        return {"picks": [
            {"item_ids": [data_id], "topic": "RWA yields", "angle": "yields compressing toward T-bills",
             "format": "single", "theme": "rwa_yield", "score": 82, "why": "data"},
            {"item_ids": [x_id], "topic": "treasuries $10B", "angle": "who uses them as collateral",
             "format": "quote", "theme": "rwa", "score": 75, "why": "x"},
            {"item_ids": ["bogus"], "topic": "x", "angle": "x", "format": "single", "theme": "other",
             "score": 99, "why": "invalid id is dropped"},
        ], "skipped_note": ""}
    return FakeLLM({
        "submit_picks": picks,
        "submit_drafts": {"variants": [{"posts": ["RWA yields: 4.78% TVL-weighted. the spread to T-bills is the story."],
                                        "hook_style": "number"},
                                       {"posts": ["variant b"], "hook_style": "contrast"}]},
        "submit_review": {"verdict": verdict, "best_variant": 0,
                          "final_posts": posts or ["RWA yields: 4.78% TVL-weighted. the spread to T-bills is the story."],
                          "score": 85, "issues": [], "disclosure_added": False},
    })


def test_all_prompts_render_without_leftover_placeholders(cfg):
    for role in ("scout", "curator", "writer", "critic", "replier", "profiler"):
        text = prompts.system_prompt(cfg, role, lookback_hours=48, focus="- x", known="k", max_searches=5,
                                     recent_posts="r", performance="p", picks=3, min_score=65, thread_note="",
                                     variants=2, thread_rule="t")
        import re
        left = set(re.findall(r"\{([a-z_]+)\}", text))
        assert not left, (role, left)
        assert "@0xYYcn" in text or "0xYYcn" in text


def test_end_to_end_approve_mode(cfg):
    store = Store(cfg.db_path)
    store.add_items(_items())
    x = FakeX()
    bot = Bot(cfg, store=store, llm=_llm(), x=x)
    drafts = bot.run_curate_and_write()
    assert [d.format for d in drafts] == ["single", "quote"]
    assert all(d.status == "pending" for d in drafts)
    assert drafts[1].target_post_id == "555"
    assert store.unused_items(48, 10) == []  # used items are not re-curated

    assert bot.run_publish() is None  # nothing approved yet
    assert "approved" in bot.apply_action(Action("approve", drafts[0].id))
    d = bot.run_publish()
    assert d.status == "posted"
    # link_strategy=reply: main post + self-reply with the source link
    assert x.posted[0]["text"].startswith("RWA yields")
    assert x.posted[1]["reply_to"] == "1001" and "defillama.com" in x.posted[1]["text"]

    # spacing rule: a second approved draft must wait
    bot.apply_action(Action("approve", drafts[1].id))
    assert bot.run_publish() is None
    assert bot.run_metrics() == 1
    assert "eng=" in bot.performance_summary()


def test_auto_mode_and_reply_always_needs_approval(cfg):
    cfg.settings.mode = "auto"
    bot = Bot(cfg, store=Store(cfg.db_path), llm=_llm(), x=FakeX())
    d1 = bot.queue(Draft(format="single", posts=["a"], critic_score=90))
    d2 = bot.queue(Draft(format="reply", posts=["b"], critic_score=95, target_post_id="1"))
    d3 = bot.queue(Draft(format="single", posts=["c"], critic_score=60))
    assert (d1.status, d2.status, d3.status) == ("approved", "pending", "pending")


def test_critic_reject_drops_pick_and_lint_blocks_bad_final(cfg):
    store = Store(cfg.db_path)
    store.add_items(_items()[:1])
    bot = Bot(cfg, store=store, llm=_llm(verdict="reject"), x=FakeX())
    assert bot.run_curate_and_write() == []
    store.add_items([Item(source="S", title="new thing", url="https://s.com/1")])
    bot = Bot(cfg, store=store, llm=_llm(posts=["this is NFA 🚀🚀"]), x=FakeX())
    assert bot.run_curate_and_write() == []


def test_edit_and_partial_thread_guard(cfg):
    bot = Bot(cfg, store=Store(cfg.db_path), llm=_llm(), x=FakeX())
    d = bot.store.save_draft(Draft(posts=["x"], status="pending"))
    assert "updated" in bot.apply_action(Action("edit", d.id, "first\n---\nsecond"))
    assert bot.store.get_draft(d.id).posts == ["first", "second"]
    p = bot.store.save_draft(Draft(posts=["a", "b"], status="failed", posted_ids=["9"]))
    assert "partly posted" in bot.apply_action(Action("approve", p.id))


def test_posting_rules(cfg):
    s = cfg.settings
    s.posting.windows = ["08:00-11:00", "22:00-02:00"]
    assert in_window(datetime(2026, 1, 1, 9, 0), s.posting.windows)
    assert in_window(datetime(2026, 1, 1, 1, 0), s.posting.windows)
    assert not in_window(datetime(2026, 1, 1, 12, 0), s.posting.windows)
    s.posting.windows = ["00:00-23:59"]
    store = Store(cfg.db_path)
    store.set("paused", "1")
    assert can_post_now(store, s) == (False, "paused")
    store.set("paused", "0")
    s.posting.max_posts_per_day = 1
    store.save_draft(Draft(posts=["a"], status="posted", posted_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    ok, why = can_post_now(store, s)
    assert not ok


def test_plan_posts_link_strategies(cfg):
    p = cfg.settings.posting
    d = Draft(posts=["hello"], source_url="https://src.com/a")
    p.link_strategy = "inline"
    assert plan_posts(d, p) == (["hello\n\nhttps://src.com/a"], None)
    p.link_strategy = "reply"
    assert plan_posts(d, p) == (["hello"], "source: https://src.com/a")
    p.link_strategy = "none"
    assert plan_posts(d, p) == (["hello"], None)
    assert plan_posts(Draft(posts=["r"], format="reply", source_url="https://s"), p)[1] is None


def test_telegram_parse_only_trusts_owner_chat():
    tg = Telegram("t", "42")
    tg._call = lambda *a, **k: {}
    ups = [
        {"update_id": 1, "callback_query": {"id": "c", "data": "approve:7", "message": {"chat": {"id": 42}}}},
        {"update_id": 2, "callback_query": {"id": "c", "data": "approve:8", "message": {"chat": {"id": 666}}}},
        {"update_id": 3, "message": {"chat": {"id": 42}, "text": "new text", "reply_to_message": {"message_id": 99}}},
        {"update_id": 4, "message": {"chat": {"id": 42}, "text": "/pause"}},
    ]
    actions, off = tg.parse(ups, {99: 5})
    assert [(a.kind, a.draft_id) for a in actions] == [("approve", 7), ("edit", 5), ("pause", None)]
    assert off == 5


# ---------------- LLM wrapper loop against a fake Anthropic client ----------------
def _block(**kw):
    return SimpleNamespace(**kw)


class _FakeMessages:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def create(self, **kw):
        self.requests.append(kw)
        return self.responses.pop(0)


def _resp(stop, content):
    return SimpleNamespace(stop_reason=stop, content=content, stop_details=None,
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0, server_tool_use=None))


def _client(responses):
    msgs = _FakeMessages(responses)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def test_llm_structured_handles_pause_and_nudge(cfg):
    tool = {"name": "submit_x", "input_schema": {}}
    client, msgs = _client([
        _resp("pause_turn", [_block(type="server_tool_use", id="s1", name="web_search", input={})]),
        _resp("end_turn", [_block(type="text", text="here is my answer")]),
        _resp("tool_use", [_block(type="tool_use", id="t1", name="submit_x", input={"ok": True})]),
    ])
    llm = LLM(client=client)
    out = llm.structured(agent="t", cfg=cfg.settings.model_for("writer"), system="s", user="u", tool=tool)
    assert out == {"ok": True}
    req = msgs.requests[-1]
    assert req["fallbacks"] == "default" and req["betas"] == ["server-side-fallback-2026-07-01"]
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert req["tool_choice"] == {"type": "auto"}
    assert req["model"] == "claude-opus-5-5"
    assert llm.usage.calls == 3


def test_llm_refusal_raises(cfg):
    client, _ = _client([_resp("refusal", [])])
    with pytest.raises(RefusalError):
        LLM(client=client).structured(agent="t", cfg=cfg.settings.model_for("writer"), system="s", user="u",
                                      tool={"name": "submit_x"})
    client, _ = _client([_resp("end_turn", [_block(type="text", text="no")])] * 3)
    with pytest.raises(LLMError):
        LLM(client=client).structured(agent="t", cfg=cfg.settings.model_for("writer"), system="s", user="u",
                                      tool={"name": "submit_x"})

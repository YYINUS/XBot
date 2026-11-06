import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    cdir = tmp_path / "config"
    shutil.copytree(ROOT / "config", cdir)
    monkeypatch.setenv("XBOT_STATE_DIR", str(tmp_path / "state"))
    for k in ("TELEGRAM_BOT_TOKEN", "X_API_KEY", "X_BEARER_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    from xbot.config import Config
    c = Config(cdir)
    c.settings.posting.windows = ["00:00-23:59"]
    return c


class FakeLLM:
    """Stands in for xbot.llm.LLM: returns canned structured outputs per tool."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls = []
        self.usage = SimpleNamespace(calls=0, summary=lambda: "fake")

    def structured(self, *, agent, cfg, system, user, tool, server_tools=None, max_turns=10):
        self.calls.append({"agent": agent, "tool": tool["name"], "system": system, "user": user})
        r = self.responses[tool["name"]]
        return r(user) if callable(r) else r


class FakeX:
    def __init__(self):
        self.posted = []
        self.can_write = True

    def post(self, text, reply_to=None, quote=None):
        self.posted.append({"text": text, "reply_to": reply_to, "quote": quote})
        return str(1000 + len(self.posted))

    def post_thread(self, posts, reply_to=None, quote=None):
        ids, prev = [], reply_to
        for i, p in enumerate(posts):
            prev = self.post(p, reply_to=prev, quote=quote if i == 0 else None)
            ids.append(prev)
        return ids

    def metrics(self, ids):
        return {i: {"like_count": 10, "retweet_count": 2, "reply_count": 1, "quote_count": 0,
                    "impression_count": 900} for i in ids}

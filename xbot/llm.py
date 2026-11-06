"""Claude API wrapper used by every agent.

Each agent call is: a cached system prompt (core rules + voice guide + role prompt), one user
message with the volatile material, optional server tools (web search), and ONE strict
"submit_*" tool the model must call with its final answer. The tool input is the structured
result, validated by the API against the schema (strict: true).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import anthropic

from .config import AgentModel

log = logging.getLogger("xbot.llm")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}

# USD per 1M tokens (input, output) - used only for the cost estimate printed in logs.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}


class LLMError(RuntimeError):
    pass


class RefusalError(LLMError):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    web_searches: int = 0
    calls: int = 0
    by_agent: dict[str, float] = field(default_factory=dict)

    def add(self, agent: str, model: str, u: Any) -> None:
        self.calls += 1
        it = getattr(u, "input_tokens", 0) or 0
        ot = getattr(u, "output_tokens", 0) or 0
        cr = getattr(u, "cache_read_input_tokens", 0) or 0
        cw = getattr(u, "cache_creation_input_tokens", 0) or 0
        stu = getattr(u, "server_tool_use", None)
        ws = (getattr(stu, "web_search_requests", 0) or 0) if stu else 0
        self.input_tokens += it
        self.output_tokens += ot
        self.cache_read += cr
        self.cache_write += cw
        self.web_searches += ws
        pin, pout = PRICES.get(model, (4.0, 20.0))
        cost = (it * pin + cw * pin * 1.25 + cr * pin * 0.1 + ot * pout) / 1e6 + ws * 0.01
        self.by_agent[agent] = self.by_agent.get(agent, 0.0) + cost

    @property
    def est_cost(self) -> float:
        return sum(self.by_agent.values())

    def summary(self) -> str:
        parts = ", ".join(f"{k}=${v:.3f}" for k, v in sorted(self.by_agent.items()))
        return (
            f"{self.calls} Claude calls, in={self.input_tokens} out={self.output_tokens} "
            f"cache_read={self.cache_read} web_searches={self.web_searches}; est ${self.est_cost:.3f} ({parts})"
        )


def submit_tool(name: str, description: str, schema: dict) -> dict:
    return {"name": name, "description": description, "strict": True, "input_schema": schema}


class LLM:
    """Thin wrapper around client.beta.messages.create for 'structured answer via tool' calls."""

    def __init__(self, use_fallbacks: bool = True, client: anthropic.Anthropic | None = None):
        self.client = client or anthropic.Anthropic()
        self.use_fallbacks = use_fallbacks
        self.usage = Usage()

    def structured(
        self,
        *,
        agent: str,
        cfg: AgentModel,
        system: str,
        user: str,
        tool: dict,
        server_tools: list[dict] | None = None,
        max_turns: int = 10,
    ) -> dict:
        tools = list(server_tools or []) + [tool]
        messages: list[dict] = [{"role": "user", "content": user}]
        kwargs: dict[str, Any] = dict(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            tool_choice={"type": "auto"},
            output_config={"effort": cfg.effort},
        )
        if self.use_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"

        nudges = 0
        for _turn in range(max_turns):
            try:
                resp = self.client.beta.messages.create(messages=messages, **kwargs)
            except anthropic.BadRequestError as e:
                raise LLMError(f"{agent}: bad request: {e.message}") from e
            except anthropic.AuthenticationError as e:
                raise LLMError("Invalid ANTHROPIC_API_KEY") from e
            except TypeError as e:
                if "authentication" in str(e).lower():
                    raise LLMError("No Claude credentials: set ANTHROPIC_API_KEY in .env or repo secrets") from e
                raise
            # RateLimit / 5xx / connection errors are retried by the SDK (max_retries=2) and then raise.

            self.usage.add(agent, cfg.model, resp.usage)

            if resp.stop_reason == "refusal":
                cat = getattr(getattr(resp, "stop_details", None), "category", None)
                raise RefusalError(f"{agent}: model declined (category={cat})")

            submit = next(
                (b for b in resp.content if b.type == "tool_use" and b.name == tool["name"]), None
            )
            if submit is not None:
                data = submit.input if isinstance(submit.input, dict) else json.loads(submit.input)
                log.debug("%s -> %s", agent, json.dumps(data)[:400])
                return data

            messages.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "pause_turn":
                continue  # server tool (web search) paused a long turn; resend to continue

            other_tools = [b for b in resp.content if b.type == "tool_use"]
            if other_tools:
                messages.append({
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": b.id, "is_error": True,
                         "content": f"Unknown tool {b.name}. Call {tool['name']} with your answer."}
                        for b in other_tools
                    ],
                })
                continue

            if resp.stop_reason == "max_tokens":
                raise LLMError(f"{agent}: hit max_tokens before submitting; raise max_tokens in settings.yaml")

            nudges += 1
            if nudges > 2:
                break
            messages.append({
                "role": "user",
                "content": f"Now call the `{tool['name']}` tool with your final answer. Do not reply in plain text.",
            })
        raise LLMError(f"{agent}: model never called {tool['name']}")

"""Telegram approval inbox.

Each draft arrives as a message with buttons: Post / Regenerate / Reject.
To edit a draft, reply to its message with the new text (separate thread posts with a line '---').
Commands: /status, /pause, /resume, /queue
Only messages from TELEGRAM_CHAT_ID are accepted.
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass

import requests

from .config import Config
from .models import Draft
from .textutil import weighted_len

log = logging.getLogger("xbot.telegram")


@dataclass
class Action:
    kind: str  # approve | reject | regen | edit | status | pause | resume | queue
    draft_id: int | None = None
    text: str | None = None


class Telegram:
    def __init__(self, token: str, chat_id: str | None):
        self.base = f"https://api.telegram.org/bot{token}"
        self.chat_id = str(chat_id) if chat_id else None

    @classmethod
    def from_config(cls, cfg: Config) -> "Telegram | None":
        token = cfg.env("TELEGRAM_BOT_TOKEN")
        if not cfg.settings.telegram.enabled or not token:
            return None
        return cls(token, cfg.env("TELEGRAM_CHAT_ID"))

    def _call(self, method: str, **params) -> dict:
        r = requests.post(f"{self.base}/{method}", json=params, timeout=30)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"telegram {method} failed: {data.get('description')}")
        return data["result"]

    # ---------------- outgoing ----------------
    def send(self, text: str, **kw) -> int | None:
        if not self.chat_id:
            log.warning("TELEGRAM_CHAT_ID not set; run `xbot telegram-setup`")
            return None
        res = self._call("sendMessage", chat_id=self.chat_id, text=text[:4000], parse_mode="HTML",
                         disable_web_page_preview=True, **kw)
        return res["message_id"]

    @staticmethod
    def format_draft(d: Draft, max_chars: int) -> str:
        head = (f"<b>Draft #{d.id}</b> · {d.format} · {d.theme} · curator {d.curator_score} · critic {d.critic_score}\n"
                f"<i>{html.escape(d.angle)}</i>\n")
        if d.target_post_id:
            head += f"↪ target: https://x.com/i/status/{d.target_post_id}\n"
        body = []
        for i, p in enumerate(d.posts):
            n = f"[{i + 1}/{len(d.posts)}] " if len(d.posts) > 1 else ""
            body.append(f"{n}{html.escape(p)}\n<code>({weighted_len(p)}/{max_chars})</code>")
        tail = ""
        if d.source_url:
            tail += f"\nsource: {html.escape(d.source_url)}"
        if d.issues:
            tail += "\n⚠️ " + html.escape("; ".join(d.issues[:4]))
        return head + "\n" + "\n\n".join(body) + "\n" + tail

    def send_draft(self, d: Draft, max_chars: int) -> int | None:
        kb = {"inline_keyboard": [[
            {"text": "✅ Post", "callback_data": f"approve:{d.id}"},
            {"text": "🔁 Regenerate", "callback_data": f"regen:{d.id}"},
            {"text": "❌ Reject", "callback_data": f"reject:{d.id}"},
        ]]}
        return self.send(self.format_draft(d, max_chars), reply_markup=kb)

    def mark(self, message_id: int | None, note: str) -> None:
        if not message_id or not self.chat_id:
            return
        try:
            self._call("editMessageReplyMarkup", chat_id=self.chat_id, message_id=message_id,
                       reply_markup={"inline_keyboard": []})
            self._call("sendMessage", chat_id=self.chat_id, text=note, reply_to_message_id=message_id)
        except Exception as e:
            log.debug("mark failed: %s", e)

    # ---------------- incoming ----------------
    def updates(self, offset: int | None) -> list[dict]:
        params = {"timeout": 0, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", **params)

    def parse(self, updates: list[dict], msg_to_draft: dict[int, int]) -> tuple[list[Action], int | None]:
        actions: list[Action] = []
        next_offset = None
        for u in updates:
            next_offset = u["update_id"] + 1
            if "callback_query" in u:
                cq = u["callback_query"]
                if str(cq.get("message", {}).get("chat", {}).get("id")) != self.chat_id:
                    continue
                try:
                    self._call("answerCallbackQuery", callback_query_id=cq["id"], text="got it")
                except Exception:
                    pass
                kind, _, did = (cq.get("data") or "").partition(":")
                if kind in ("approve", "reject", "regen") and did.isdigit():
                    actions.append(Action(kind, int(did)))
                continue
            msg = u.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != self.chat_id:
                continue
            text = (msg.get("text") or "").strip()
            if text.startswith("/"):
                cmd = text.split()[0][1:].split("@")[0]
                if cmd in ("status", "pause", "resume", "queue"):
                    actions.append(Action(cmd))
                elif cmd in ("approve", "reject", "regen") and len(text.split()) > 1 and text.split()[1].isdigit():
                    actions.append(Action(cmd, int(text.split()[1])))
                continue
            replied = (msg.get("reply_to_message") or {}).get("message_id")
            if replied and replied in msg_to_draft and text:
                actions.append(Action("edit", msg_to_draft[replied], text))
        return actions, next_offset

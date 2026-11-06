"""X (Twitter) API v2 access: posting, reading your own posts, watch-list timelines, metrics."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config
from .models import Item

log = logging.getLogger("xbot.x")


class XNotConfigured(RuntimeError):
    pass


class PartialPost(RuntimeError):
    """A thread failed midway; `ids` are the posts that did go live."""

    def __init__(self, ids: list[str], cause: Exception):
        super().__init__(f"thread failed after {len(ids)} posts: {cause}")
        self.ids = ids


class XClient:
    def __init__(self, cfg: Config):
        import tweepy  # imported lazily so the rest of the bot works without it

        self.tweepy = tweepy
        keys = {k: cfg.env(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET")}
        self.can_write = all(keys.values())
        self.bearer = cfg.env("X_BEARER_TOKEN")
        if not self.can_write and not self.bearer:
            raise XNotConfigured("No X API credentials in environment (.env)")
        self.client = tweepy.Client(
            bearer_token=self.bearer,
            consumer_key=keys["X_API_KEY"],
            consumer_secret=keys["X_API_SECRET"],
            access_token=keys["X_ACCESS_TOKEN"],
            access_token_secret=keys["X_ACCESS_TOKEN_SECRET"],
            wait_on_rate_limit=False,
        )
        self._user_auth = self.can_write  # prefer user context when we have it
        self._me: dict | None = None

    # ---------------- writing ----------------
    def post(self, text: str, reply_to: str | None = None, quote: str | None = None) -> str:
        if not self.can_write:
            raise XNotConfigured("Posting needs X_API_KEY/SECRET + X_ACCESS_TOKEN/SECRET with Read+Write")
        resp = self.client.create_tweet(text=text, in_reply_to_tweet_id=reply_to, quote_tweet_id=quote)
        return str(resp.data["id"])

    def post_thread(self, posts: list[str], reply_to: str | None = None, quote: str | None = None) -> list[str]:
        ids: list[str] = []
        prev = reply_to
        for i, text in enumerate(posts):
            try:
                pid = self.post(text, reply_to=prev, quote=quote if i == 0 else None)
            except Exception as e:
                if ids:
                    raise PartialPost(ids, e) from e
                raise
            ids.append(pid)
            prev = pid
        return ids

    # ---------------- reading ----------------
    def me(self) -> dict:
        if self._me is None:
            r = self.client.get_me(user_auth=True)
            self._me = {"id": str(r.data.id), "username": r.data.username}
        return self._me

    def user_id(self, username: str) -> str:
        r = self.client.get_user(username=username, user_auth=self._user_auth)
        if r.data is None:
            raise ValueError(f"unknown X user {username}")
        return str(r.data.id)

    def own_posts(self, username: str | None = None, limit: int = 400) -> list[dict]:
        uid = self.user_id(username) if username else self.me()["id"]
        out = []
        for t in self.tweepy.Paginator(
            self.client.get_users_tweets, uid, max_results=100,
            tweet_fields=["created_at", "public_metrics", "referenced_tweets", "lang"],
            user_auth=self._user_auth,
        ).flatten(limit=limit):
            m = t.public_metrics or {}
            refs = [r.type for r in (t.referenced_tweets or [])]
            out.append({
                "id": str(t.id), "text": t.text, "created_at": str(t.created_at), "lang": t.lang,
                "likes": m.get("like_count", 0), "reposts": m.get("retweet_count", 0),
                "replies": m.get("reply_count", 0), "quotes": m.get("quote_count", 0),
                "impressions": m.get("impression_count", 0),
                "type": "reply" if "replied_to" in refs else "quote" if "quoted" in refs
                        else "repost" if "retweeted" in refs else "post",
            })
        return out

    def watch_items(self, cfg: dict, max_age_hours: int) -> list[Item]:
        since = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        min_likes = int(cfg.get("min_likes", 0))
        items: list[Item] = []
        for username in cfg.get("accounts", []):
            try:
                uid = self.user_id(username)
                r = self.client.get_users_tweets(
                    uid, max_results=max(5, int(cfg.get("max_posts_per_account", 5))),
                    exclude=["replies", "retweets"], start_time=since,
                    tweet_fields=["created_at", "public_metrics"], user_auth=self._user_auth,
                )
            except Exception as e:
                log.warning("x_watch %s failed: %s", username, e)
                continue
            for t in r.data or []:
                m = t.public_metrics or {}
                if m.get("like_count", 0) < min_likes:
                    continue
                items.append(Item(
                    source=f"X @{username}", kind="x_post",
                    title=f"@{username}: {t.text[:120]}",
                    url=f"https://x.com/{username}/status/{t.id}",
                    summary=t.text, published_at=t.created_at, author=f"@{username}",
                    x_post_id=str(t.id), tags=["x"],
                    metrics={"likes": m.get("like_count", 0), "reposts": m.get("retweet_count", 0)},
                ))
        return items

    def metrics(self, ids: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(ids), 100):
            r = self.client.get_tweets(ids[i:i + 100], tweet_fields=["public_metrics"], user_auth=self._user_auth)
            for t in r.data or []:
                out[str(t.id)] = dict(t.public_metrics or {})
        return out


def load_archive(path: str | Path) -> list[dict]:
    """Parse tweets.js from the free X data export (Settings -> Your account -> Download an archive)."""
    raw = Path(path).read_text(encoding="utf-8")
    raw = re.sub(r"^\s*window\.YTD\.[\w.]+\s*=\s*", "", raw)
    data = json.loads(raw)
    out = []
    for row in data:
        t = row.get("tweet", row)
        text = t.get("full_text") or t.get("text") or ""
        if text.startswith("RT @"):
            kind = "repost"
        elif t.get("in_reply_to_status_id_str"):
            kind = "reply"
        elif t.get("quoted_status_id_str") or t.get("is_quote_status") in (True, "true"):
            kind = "quote"
        else:
            kind = "post"
        out.append({
            "id": t.get("id_str") or t.get("id"), "text": text, "created_at": t.get("created_at"),
            "lang": t.get("lang"), "likes": int(t.get("favorite_count", 0) or 0),
            "reposts": int(t.get("retweet_count", 0) or 0), "replies": 0, "quotes": 0, "impressions": 0,
            "type": kind,
        })
    return out

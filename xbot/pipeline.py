"""Orchestration: collect -> curate -> write -> critique -> queue -> approve -> publish."""
from __future__ import annotations

import logging
from datetime import timedelta

from . import agents
from .config import Config
from .llm import LLM, LLMError
from .models import Draft, Item, Pick, utcnow
from .publisher import can_post_now, publish
from .ranking import rank
from .sources import collect_all
from .store import Store
from .telegram import Action, Telegram
from .textutil import lint_post

log = logging.getLogger("xbot.pipeline")


class Bot:
    def __init__(self, cfg: Config, store: Store | None = None, llm: LLM | None = None, x=None,
                 telegram: Telegram | None = None, dry_run: bool = False):
        self.cfg = cfg
        self.s = cfg.settings
        self.store = store or Store(cfg.db_path)
        self._llm = llm
        self.x = x
        self.tg = telegram
        self.dry_run = dry_run

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = LLM(use_fallbacks=self.s.use_fallbacks)
        return self._llm

    # ------------------------------------------------------------ helpers
    def _lint(self, posts: list[str], fmt: str) -> list[str]:
        c = self.s.compliance
        issues = []
        for i, p in enumerate(posts):
            for issue in lint_post(p, max_chars=self.s.posting.max_post_chars, banned=c.banned_phrases,
                                   max_hashtags=c.max_hashtags, max_emojis=c.max_emojis):
                issues.append(f"post {i + 1}: {issue}" if len(posts) > 1 else issue)
        low = " ".join(posts).lower()
        for tok in c.avoid_tokens:
            if tok.lower() in low:
                issues.append(f"mentions avoided token {tok}")
        if fmt != "thread" and len(posts) > 1:
            issues.append("multiple posts for a non-thread format")
        return issues

    def recent_post_summaries(self) -> list[str]:
        return [f"[{d.created_at:%m-%d}] ({d.theme}) {d.topic}: {d.posts[0][:140]}" for d in self.store.recent_drafts(7)]

    def performance_summary(self) -> str:
        m = self.store.metrics_by_draft()
        if not m:
            return ""
        scored = []
        for did, mm in m.items():
            d = self.store.get_draft(did)
            if not d:
                continue
            eng = mm.get("like_count", 0) + 2 * mm.get("retweet_count", 0) + 2 * mm.get("reply_count", 0) \
                + 3 * mm.get("quote_count", 0)
            scored.append((eng, mm.get("impression_count", 0), d))
        scored.sort(key=lambda x: -x[0])
        fmt = lambda e, imp, d: f"- eng={e} impressions={imp} [{d.theme}/{d.format}] {d.posts[0][:120]}"  # noqa: E731
        lines = ["Best:"] + [fmt(*x) for x in scored[:4]]
        if len(scored) > 6:
            lines += ["Worst:"] + [fmt(*x) for x in scored[-3:]]
        return "\n".join(lines)

    # ------------------------------------------------------------ stages
    def run_scout(self) -> list[Item]:
        if not (self.cfg.sources.get("scout") or {}).get("enabled", True):
            return []
        try:
            found = agents.scout(self.cfg, self.llm, self.store.recent_titles())
        except LLMError as e:
            log.warning("scout failed: %s", e)
            return []
        fresh = self.store.add_items(found)
        log.info("scout: %d findings, %d new", len(found), len(fresh))
        return fresh

    def run_collect(self) -> list[Item]:
        items = collect_all(self.cfg, self.x)
        fresh = self.store.add_items(items)
        log.info("collected %d items, %d new", len(items), len(fresh))
        return fresh

    def draft_pick(self, pick: Pick, items: list[Item]) -> Draft | None:
        target = next((i for i in items if i.kind == "x_post" and i.x_post_id), None)
        feedback = ""
        review = None
        variants = []
        for attempt in range(2):
            variants = agents.write(self.cfg, self.llm, pick, items, feedback)
            if not variants:
                log.info("writer returned nothing for %r", pick.topic)
                return None
            lint = {i: self._lint(v.posts, pick.format) for i, v in enumerate(variants)}
            review = agents.critique(self.cfg, self.llm, pick, items, variants, lint)
            final_issues = self._lint(review.final_posts, pick.format)
            if review.verdict == "approve" and not final_issues:
                break
            feedback = "; ".join(review.issues + final_issues) or "critic rejected the drafts"
            log.info("attempt %d for %r not approved: %s", attempt + 1, pick.topic, feedback)
        assert review is not None
        final_issues = self._lint(review.final_posts, pick.format)
        if review.verdict != "approve" or final_issues:
            log.info("dropping pick %r (critic=%s, issues=%s)", pick.topic, review.verdict, final_issues)
            return None
        src = next((i.url for i in items if i.url and i.kind != "x_post"), None)
        return Draft(
            format=pick.format, posts=review.final_posts, topic=pick.topic, angle=pick.angle, theme=pick.theme,
            item_ids=pick.item_ids, source_url=src, target_post_id=target.x_post_id if target else None,
            curator_score=pick.score, critic_score=review.score, issues=review.issues,
        )

    def queue(self, d: Draft) -> Draft:
        mode = self.s.mode
        if mode == "draft":
            d.status = "draft"
        elif mode == "auto" and d.format != "reply" and d.critic_score >= self.s.pipeline.auto_post_min_score:
            d.status = "approved"
        else:
            d.status = "pending"
        self.store.save_draft(d)
        if d.status == "pending" and self.tg:
            try:
                d.telegram_message_id = self.tg.send_draft(d, self.s.posting.max_post_chars)
                self.store.save_draft(d)
            except Exception as e:
                log.warning("telegram send failed: %s", e)
        log.info("draft #%s queued as %s: %s", d.id, d.status, d.posts[0][:100])
        return d

    def run_curate_and_write(self) -> list[Draft]:
        p = self.s.pipeline
        items = rank(self.store.unused_items(p.item_max_age_hours, 1000), p.max_items_to_curator)
        if not items:
            log.info("no unused items to curate")
            return []
        pending = len(self.store.drafts("pending", 100)) + len(self.store.drafts("approved", 100))
        if pending >= self.s.posting.max_posts_per_day * 2:
            log.info("queue already has %d drafts awaiting approval/posting; skipping drafting", pending)
            return []
        allow_replies = self.s.replies.enabled
        picks = agents.curate(self.cfg, self.llm, items, self.recent_post_summaries(), self.performance_summary(),
                              allow_replies)
        log.info("curator picked %d of %d items", len(picks), len(items))
        by_id = {i.id: i for i in items}
        drafts: list[Draft] = []
        replies = 0
        for pk in picks:
            if pk.format in ("quote", "reply"):
                if replies >= self.s.replies.max_per_run:
                    continue
                replies += 1
            try:
                d = self.draft_pick(pk, [by_id[i] for i in pk.item_ids])
            except LLMError as e:
                log.warning("drafting %r failed: %s", pk.topic, e)
                continue
            self.store.mark_used(pk.item_ids)
            if d:
                drafts.append(self.queue(d))
        return drafts

    def run_publish(self, force: bool = False) -> Draft | None:
        approved = self.store.drafts("approved", 20)
        if not approved:
            return None
        ok, why = (True, "forced") if force else can_post_now(self.store, self.s)
        if not ok:
            log.info("not posting now: %s", why)
            return None
        d = approved[0]
        if self.x is None and not self.dry_run:
            log.warning("approved draft #%s waiting: X credentials not configured", d.id)
            return None
        d = publish(d, self.x, self.s, dry_run=self.dry_run)
        if not self.dry_run:
            self.store.save_draft(d)
            if self.tg and d.status in ("posted", "failed"):
                note = (f"✅ posted: https://x.com/i/status/{d.posted_ids[0]}" if d.status == "posted"
                        else f"⚠️ posting failed: {d.error}")
                self.tg.mark(d.telegram_message_id, note)
        return d

    def run_metrics(self) -> int:
        if self.x is None:
            return 0
        posted = self.store.posted_since(utcnow() - timedelta(days=7))
        ids = {pid: d.id for d in posted for pid in d.posted_ids[:1]}
        if not ids:
            return 0
        try:
            m = self.x.metrics(list(ids))
        except Exception as e:
            log.warning("metrics fetch failed (needs X read access): %s", e)
            return 0
        for pid, data in m.items():
            self.store.save_metrics(pid, ids.get(pid), data)
        return len(m)

    # ------------------------------------------------------------ approvals
    def apply_action(self, a: Action) -> str:
        if a.kind == "pause":
            self.store.set("paused", "1")
            return "⏸ publishing paused"
        if a.kind == "resume":
            self.store.set("paused", "0")
            return "▶️ publishing resumed"
        if a.kind in ("status", "queue"):
            return self.status_text()
        d = self.store.get_draft(a.draft_id) if a.draft_id else None
        if not d:
            return f"draft #{a.draft_id} not found"
        if d.status not in ("pending", "draft", "approved", "failed"):
            return f"draft #{d.id} is already {d.status}"
        if d.posted_ids and a.kind in ("approve", "edit"):
            return f"draft #{d.id} was partly posted ({len(d.posted_ids)} posts live); finish it manually on X"
        if a.kind == "approve":
            d.status = "approved"
            self.store.save_draft(d)
            return f"👍 #{d.id} approved, will post at the next open slot"
        if a.kind == "reject":
            d.status = "rejected"
            self.store.save_draft(d)
            return f"🗑 #{d.id} rejected"
        if a.kind == "edit":
            posts = [p.strip() for p in (a.text or "").split("\n---\n") if p.strip()]
            issues = self._lint(posts, "thread" if len(posts) > 1 else d.format)
            if issues:
                return f"edit not saved: {'; '.join(issues)}"
            d.posts = posts
            d.status = "approved"
            self.store.save_draft(d)
            return f"✏️ #{d.id} updated and approved"
        if a.kind == "regen":
            items = self.store.get_items(d.item_ids)
            pick = Pick(item_ids=d.item_ids, topic=d.topic, angle=d.angle, format=d.format, theme=d.theme,
                        score=d.curator_score, why="regenerate")
            d.status = "rejected"
            self.store.save_draft(d)
            try:
                nd = self.draft_pick(pick, items)
            except LLMError as e:
                return f"regen failed: {e}"
            if not nd:
                return "critic rejected all regenerated versions"
            nd.status = "pending"
            self.queue(nd)
            return f"🔁 regenerated as #{nd.id}"
        return "unknown action"

    def process_telegram(self) -> int:
        if not self.tg:
            return 0
        off = self.store.get("telegram_offset")
        try:
            updates = self.tg.updates(int(off) if off else None)
        except Exception as e:
            log.warning("telegram poll failed: %s", e)
            return 0
        msg_map = {d.telegram_message_id: d.id for d in self.store.drafts(None, 200) if d.telegram_message_id}
        actions, next_off = self.tg.parse(updates, msg_map)
        if next_off is not None:
            self.store.set("telegram_offset", str(next_off))
        for a in actions:
            reply = self.apply_action(a)
            log.info("telegram %s %s -> %s", a.kind, a.draft_id or "", reply)
            try:
                self.tg.send(reply)
            except Exception:
                pass
        return len(actions)

    def status_text(self) -> str:
        counts = {s: len(self.store.drafts(s, 500)) for s in ("pending", "approved", "posted", "rejected", "failed")}
        ok, why = can_post_now(self.store, self.s)
        return (f"mode={self.s.mode} paused={self.store.get('paused') == '1'} | "
                + " ".join(f"{k}={v}" for k, v in counts.items()) + f" | can post now: {ok} ({why})")

    # ------------------------------------------------------------ scheduler entrypoint
    def tick(self, force_collect: bool = False) -> None:
        """Run whatever is due. Safe to call every few minutes from cron / GitHub Actions."""
        self.process_telegram()
        now = utcnow()
        c = self.s.cadence
        if self.store.due("scout", c.scout, now):
            self.run_scout()
            self.store.mark_ran("scout", now)
        if force_collect or self.store.due("collect", c.collect, now):
            self.run_collect()
            try:
                self.run_curate_and_write()
            except LLMError as e:
                log.error("curate/write failed: %s", e)
            self.store.mark_ran("collect", now)
        self.run_publish()
        if self.store.due("metrics", c.metrics, now):
            n = self.run_metrics()
            log.info("metrics updated for %d posts", n)
            self.store.mark_ran("metrics", now)
        if self._llm is not None and self._llm.usage.calls:
            log.info("usage: %s", self._llm.usage.summary())

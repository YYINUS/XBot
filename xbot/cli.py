"""Command line: `python -m xbot <command>` (or `xbot <command>` after `pip install -e .`)."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from .config import Config

log = logging.getLogger("xbot")


def _x(cfg: Config, required: bool = False):
    from .x_client import XClient, XNotConfigured

    try:
        return XClient(cfg)
    except XNotConfigured as e:
        if required:
            sys.exit(f"X API not configured: {e}")
        log.info("X API not configured; posting/reading X disabled")
        return None
    except ImportError:
        if required:
            sys.exit("tweepy not installed: pip install -r requirements.txt")
        return None


def _bot(cfg: Config, args, need_x: bool = False):
    from .pipeline import Bot
    from .telegram import Telegram

    return Bot(cfg, x=_x(cfg, need_x), telegram=Telegram.from_config(cfg), dry_run=getattr(args, "dry_run", False))


def _print_draft(d) -> None:
    print(f"\n#{d.id} [{d.status}] {d.format} · {d.theme} · curator {d.curator_score} · critic {d.critic_score}")
    print(f"   angle: {d.angle}")
    for i, p in enumerate(d.posts):
        print(f"   ({i + 1}) {p}")
    if d.source_url:
        print(f"   source: {d.source_url}")
    if d.target_post_id:
        print(f"   target: https://x.com/i/status/{d.target_post_id}")
    if d.error:
        print(f"   error: {d.error}")


# ---------------------------------------------------------------- commands
def cmd_doctor(cfg: Config, args) -> None:
    ok = lambda b: "OK " if b else "-- "  # noqa: E731
    env = cfg.env
    print("Config dir:", cfg.config_dir)
    print("State DB:  ", cfg.db_path)
    print(ok(env("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY (required for all agents)")
    writes = all(env(k) for k in ("X_API_KEY", "X_API_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET"))
    print(ok(writes), "X OAuth1 user keys (required to post)")
    print(ok(env("X_BEARER_TOKEN")), "X_BEARER_TOKEN (reading watch-list / metrics)")
    print(ok(env("TELEGRAM_BOT_TOKEN")), "TELEGRAM_BOT_TOKEN (approval inbox)")
    print(ok(env("TELEGRAM_CHAT_ID")), "TELEGRAM_CHAT_ID")
    print(ok(cfg.exemplars_path.exists()), "config/exemplars.json (created by `xbot profile`)")
    seed = "SEED PERSONA" in cfg.persona()
    print(ok(not seed), "config/persona.md learned from your posts" + (" (still the seed; run `xbot profile`)" if seed else ""))
    print(f"mode={cfg.settings.mode} language={cfg.settings.language} tz={cfg.settings.timezone}")
    if args.live:
        if env("ANTHROPIC_API_KEY"):
            import anthropic
            try:
                m = anthropic.Anthropic().models.retrieve(cfg.settings.model_for("writer").model)
                print("OK  Claude API reachable:", m.id)
            except Exception as e:
                print("ERR Claude API:", e)
        x = _x(cfg)
        if x and x.can_write:
            try:
                print("OK  X auth as @%s" % x.me()["username"])
            except Exception as e:
                print("ERR X auth:", e)


def cmd_collect(cfg: Config, args) -> None:
    from .sources import collect_all
    from .store import Store

    items = collect_all(cfg, _x(cfg))
    fresh = Store(cfg.db_path).add_items(items) if not args.no_store else items
    print(f"{len(items)} items collected, {len(fresh)} new")
    for it in fresh[: args.show]:
        print(f"- [{it.kind}/{it.source}] {it.title}")


def cmd_scout(cfg: Config, args) -> None:
    bot = _bot(cfg, args)
    fresh = bot.run_scout()
    for it in fresh:
        print(f"- {it.title}\n  {it.url}\n  {it.summary[:200]}")
    print(bot.llm.usage.summary())


def cmd_run(cfg: Config, args) -> None:
    """One full drafting cycle now (scout optional), no waiting for cadence."""
    bot = _bot(cfg, args)
    bot.process_telegram()
    if args.scout:
        bot.run_scout()
    if not args.skip_collect:
        bot.run_collect()
    drafts = bot.run_curate_and_write()
    for d in drafts:
        _print_draft(d)
    if not drafts:
        print("No drafts this cycle (nothing cleared the curator/critic bar).")
    if bot._llm:
        print("\n" + bot.llm.usage.summary())


def cmd_tick(cfg: Config, args) -> None:
    _bot(cfg, args).tick(force_collect=args.force_collect)


def cmd_daemon(cfg: Config, args) -> None:
    bot = _bot(cfg, args)
    log.info("daemon started: tick every %ss", args.interval)
    while True:
        try:
            bot.tick()
        except Exception as e:  # keep the daemon alive
            log.exception("tick failed: %s", e)
        time.sleep(args.interval)


def cmd_drafts(cfg: Config, args) -> None:
    from .store import Store

    for d in Store(cfg.db_path).drafts(args.status, args.limit):
        _print_draft(d)


def cmd_review(cfg: Config, args) -> None:
    """Interactive terminal approval (alternative to Telegram)."""
    from .telegram import Action

    bot = _bot(cfg, args)
    pending = bot.store.drafts("pending", 50)
    if not pending:
        print("Nothing pending.")
        return
    for d in pending:
        _print_draft(d)
        choice = input("   [a]pprove / [r]eject / [e]dit / [g]regenerate / [s]kip / [q]uit > ").strip().lower()
        if choice == "q":
            break
        if choice == "a":
            print("  ", bot.apply_action(Action("approve", d.id)))
        elif choice == "r":
            print("  ", bot.apply_action(Action("reject", d.id)))
        elif choice == "g":
            print("  ", bot.apply_action(Action("regen", d.id)))
        elif choice == "e":
            print("   Enter new text. For a thread, separate posts with a line containing only ---. End with an empty line.")
            lines = []
            while (line := input()) != "":
                lines.append(line)
            print("  ", bot.apply_action(Action("edit", d.id, "\n".join(lines))))


def _simple_action(kind: str):
    def run(cfg: Config, args) -> None:
        from .telegram import Action

        print(_bot(cfg, args).apply_action(Action(kind, args.id, getattr(args, "text", None))))
    return run


def cmd_publish(cfg: Config, args) -> None:
    bot = _bot(cfg, args, need_x=not args.dry_run)
    d = bot.run_publish(force=args.force)
    print(bot.status_text() if d is None else f"draft #{d.id}: {d.status}")


def cmd_status(cfg: Config, args) -> None:
    print(_bot(cfg, args).status_text())


def cmd_profile(cfg: Config, args) -> None:
    """Learn the owner's voice from their own posts -> config/persona.md + config/exemplars.json."""
    from . import agents
    from .llm import LLM
    from .x_client import load_archive

    if args.archive:
        posts = load_archive(args.archive)
        source = f"archive {args.archive}"
    else:
        x = _x(cfg, required=True)
        posts = x.own_posts(args.username or cfg.settings.handle, limit=args.limit)
        source = "X API"
    posts = [p for p in posts if p["type"] != "repost" and p["text"].strip()]
    posts.sort(key=lambda p: str(p.get("created_at")), reverse=True)
    posts = posts[: args.limit]
    if len(posts) < 20:
        sys.exit(f"Only {len(posts)} usable posts from {source}; need at least 20.")
    print(f"Profiling {len(posts)} posts from {source} ...")
    llm = LLM(use_fallbacks=cfg.settings.use_fallbacks)
    result = agents.profile(cfg, llm, posts)
    by_id = {p["id"]: p for p in posts}
    exemplars = [{"id": i, "text": by_id[i]["text"], "likes": by_id[i]["likes"]}
                 for i in result.get("exemplar_post_ids", []) if i in by_id]
    if not exemplars:  # fall back to top posts by engagement
        top = sorted((p for p in posts if p["type"] == "post"), key=lambda p: -(p["likes"] + 2 * p["reposts"]))[:10]
        exemplars = [{"id": p["id"], "text": p["text"], "likes": p["likes"]} for p in top]
    if cfg.persona_path.exists():
        cfg.persona_path.with_suffix(".seed.md").write_text(cfg.persona())
    header = f"<!-- Generated by `xbot profile` from {len(posts)} posts ({source}). Edit freely. -->\n\n"
    cfg.persona_path.write_text(header + result["voice_guide_markdown"].strip() + "\n")
    cfg.exemplars_path.write_text(json.dumps(exemplars, ensure_ascii=False, indent=2))
    print(f"Wrote {cfg.persona_path} and {cfg.exemplars_path} ({len(exemplars)} exemplars)")
    print("Suggested language setting:", result.get("language"))
    print("Top themes:", ", ".join(result.get("top_themes", [])))
    print("Suggested watch accounts:", ", ".join(result.get("suggested_watch_accounts", [])))
    print("\nNotes:", result.get("notes_for_owner", ""))
    print("\n" + llm.usage.summary())


def cmd_telegram_setup(cfg: Config, args) -> None:
    from .telegram import Telegram

    token = cfg.env("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Set TELEGRAM_BOT_TOKEN in .env first (create a bot with @BotFather).")
    tg = Telegram(token, None)
    ups = tg.updates(None)
    chats = {(u.get("message") or {}).get("chat", {}).get("id"): (u.get("message") or {}).get("chat", {})
             for u in ups if u.get("message")}
    if not chats:
        sys.exit("No messages found. Open your bot in Telegram, send it 'hi', then rerun this command.")
    for cid, chat in chats.items():
        print(f"TELEGRAM_CHAT_ID={cid}   # {chat.get('username') or chat.get('title')}")


def cmd_metrics(cfg: Config, args) -> None:
    bot = _bot(cfg, args)
    print(f"updated metrics for {bot.run_metrics()} posts")
    print(bot.performance_summary() or "(no metrics yet)")


def cmd_post_test(cfg: Config, args) -> None:
    if not args.yes:
        sys.exit("This posts publicly to X. Re-run with --yes to confirm.")
    x = _x(cfg, required=True)
    print("posted:", f"https://x.com/i/status/{x.post(args.text)}")


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="xbot", description="Agentic crypto research -> X posting bot")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="never post to X; print what would be posted")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("doctor", help="check configuration and credentials")
    s.add_argument("--live", action="store_true", help="also call the Claude and X APIs")
    s.set_defaults(fn=cmd_doctor)

    s = sub.add_parser("profile", help="learn your voice from your X posts")
    s.add_argument("--archive", help="path to tweets.js from your X data export (no API needed)")
    s.add_argument("--username", help="defaults to settings.handle")
    s.add_argument("--limit", type=int, default=400)
    s.set_defaults(fn=cmd_profile)

    s = sub.add_parser("collect", help="run collectors only (no Claude calls)")
    s.add_argument("--show", type=int, default=30)
    s.add_argument("--no-store", action="store_true")
    s.set_defaults(fn=cmd_collect)

    sub.add_parser("scout", help="run the web-search scout now").set_defaults(fn=cmd_scout)

    s = sub.add_parser("run", help="one drafting cycle now: collect -> curate -> write -> critique -> queue")
    s.add_argument("--scout", action="store_true", help="run the web scout first")
    s.add_argument("--skip-collect", action="store_true")
    s.set_defaults(fn=cmd_run)

    s = sub.add_parser("tick", help="scheduler entrypoint: do whatever is due (use from cron / Actions)")
    s.add_argument("--force-collect", action="store_true")
    s.set_defaults(fn=cmd_tick)

    s = sub.add_parser("daemon", help="run tick in a loop (for a VPS)")
    s.add_argument("--interval", type=int, default=300)
    s.set_defaults(fn=cmd_daemon)

    s = sub.add_parser("drafts", help="list drafts")
    s.add_argument("--status", choices=["pending", "approved", "posted", "rejected", "failed", "draft"])
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_drafts)

    sub.add_parser("review", help="approve/reject/edit pending drafts in the terminal").set_defaults(fn=cmd_review)
    for kind in ("approve", "reject", "regen"):
        s = sub.add_parser(kind, help=f"{kind} a draft by id")
        s.add_argument("id", type=int)
        s.set_defaults(fn=_simple_action(kind))
    s = sub.add_parser("edit", help="replace a draft's text and approve it")
    s.add_argument("id", type=int)
    s.add_argument("text", help="new text; separate thread posts with a line '---'")
    s.set_defaults(fn=_simple_action("edit"))

    s = sub.add_parser("publish", help="post the next approved draft if a slot is open")
    s.add_argument("--force", action="store_true", help="ignore windows/caps")
    s.set_defaults(fn=cmd_publish)

    sub.add_parser("status", help="queue + posting status").set_defaults(fn=cmd_status)
    sub.add_parser("metrics", help="refresh engagement metrics").set_defaults(fn=cmd_metrics)
    sub.add_parser("telegram-setup", help="print your TELEGRAM_CHAT_ID").set_defaults(fn=cmd_telegram_setup)

    s = sub.add_parser("post-test", help="post one manual tweet to verify X write access")
    s.add_argument("text")
    s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_post_test)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    from .llm import LLMError

    try:
        args.fn(Config(), args)
    except LLMError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()

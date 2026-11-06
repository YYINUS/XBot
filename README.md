# XBot: an agent team that researches crypto and posts to X as @0xYYcn

XBot finds interesting news, onchain data, research and dev updates in **RWA / RWA yield, DeFi,
DePIN, protocol design and crypto development**. It decides what's worth saying, drafts posts in
your voice, fact-checks them, and sends them to you for one-tap approval before posting to X.

> **What you need to provide:** see **[SETUP.md](SETUP.md)**.

## How it works

```
        ┌──────────── Collectors (no LLM, free) ────────────┐
        │ RSS: The Block, CoinDesk, DL News, Blockworks,    │
        │      The Defiant, Bankless, Vitalik, EF blog,     │
        │      ethresear.ch, Eth Magicians, gov forums...   │
        │ DefiLlama: RWA yield board, TVL movers            │──┐
        │      (RWA/DePIN/lending/CDP/restaking...),        │  │
        │      stablecoin supply moves                      │  │
        │ GitHub releases: geth, reth, agave, foundry...    │  │
        │ CoinGecko trending · X watch-list (optional)      │  │
        └───────────────────────────────────────────────────┘  │
        ┌── Scout agent (Claude + web search) ──────────────┐  │
        │ hunts primary sources the feeds miss              │──┤
        └───────────────────────────────────────────────────┘  ▼
                                                   SQLite store (dedupe)
                                                            │ pre-rank (keywords, recency, source weight)
                                                            ▼
        Curator agent: scores 0-100, merges duplicates, picks ≤3, sets angle + format
                                                            ▼
        Writer agent (or Reply agent for quote/reply): N variants in your voice
                                                            ▼
        Critic agent: fact-check vs sources, compliance, voice, value → approve/fix/reject
        + hard lint in code: length (X-weighted), links, hashtags, emojis, banned phrases
                                                            ▼
        Queue → Telegram: [✅ Post] [🔁 Regenerate] [❌ Reject], or reply to edit
                                                            ▼
        Publisher: posting windows, daily cap, spacing; source link as self-reply
                                                            ▼
        Metrics: engagement feeds back into the Curator's choices
```

The Profiler agent learns your voice from your own X posts and writes `config/persona.md` and
`config/exemplars.json`. Every other agent reads those on every run.

### Prompt design (in `prompts/`)
- `core.md` is shared by all agents. It sets the pipeline context, accuracy-over-speed rules, a
  defence against prompt injection (feed and web content is treated as untrusted data), no financial
  advice, affiliation disclosure, and your voice guide.
- Each role prompt states the role's job, its quality bar and one `submit_*` tool. The tool is
  **strict** (schema-validated), so the output is always structured.
- The Curator is told that "zero picks is valid". Its job is to say no. The Critic is told that "a
  missed error costs your reputation; a rejected draft costs nothing".
- Every number in a post must come from the source items. The Critic checks this, and the Writer
  is retried once with the Critic's feedback.
- The system prompt stays stable within a day, so Claude's prompt cache hits on repeated calls.

### Safety rails
- Default `mode: approve`. Nothing is posted without your tap. Replies to other people's posts
  **always** need approval, even in `auto` mode, in line with X's automation rules on unsolicited replies.
- Telegram commands are accepted only from your `TELEGRAM_CHAT_ID`. Use `/pause`, `/resume` and `/status`.
- Exploit/hack claims need confirmation. No price targets or buy/sell calls. Banned hype phrases.
- If a thread fails halfway, the posts that went live are recorded and the draft is never re-posted from scratch.

## Quick start (local)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in keys (see SETUP.md)
python -m xbot doctor --live
```

```bash
python -m xbot collect                                   # try the free collectors (no keys needed)
python -m xbot profile --archive ~/Downloads/twitter-archive/data/tweets.js   # learn your voice
python -m xbot --dry-run run --scout                     # one full cycle, prints drafts, posts nothing
python -m xbot review                                    # approve / edit / reject in the terminal
python -m xbot publish                                   # post the next approved draft (respects windows)
```

## Commands

| command | what it does |
|---|---|
| `doctor [--live]` | check keys/config; `--live` calls Claude + X |
| `profile [--archive tweets.js]` | learn voice from your posts → `config/persona.md`, `config/exemplars.json` |
| `collect` | run collectors only (free) |
| `scout` | run the web-search scout now |
| `run [--scout]` | one drafting cycle now |
| `tick` | scheduler entrypoint: Telegram approvals → whatever's due → post if slot open |
| `daemon --interval 300` | `tick` forever (VPS / Docker) |
| `drafts [--status pending]`, `review`, `approve ID`, `reject ID`, `regen ID`, `edit ID "text"` | manage the queue |
| `publish [--force]` | post the next approved draft |
| `status`, `metrics` | queue / posting / engagement |
| `telegram-setup` | print your Telegram chat id |
| `post-test "hello" --yes` | verify X write access with one manual post |

Global flags: `--dry-run` (never posts), `-v` (debug logs).

## Running it 24/7

**GitHub Actions (no server):** `.github/workflows/xbot.yml` runs `xbot tick` every 30 min and
keeps state in the Actions cache. Add the secrets from SETUP.md and set the repository variable
`XBOT_ENABLED=true`. Approvals made in Telegram take effect on the next run. Each run takes about
1 minute; a private repo uses roughly 1,500 of its 2,000 free minutes a month.

**VPS / home server (faster approvals):**
```bash
docker build -t xbot .
docker run -d --name xbot --env-file .env -v xbot-data:/data --restart unless-stopped xbot
```

## Tuning
- `config/settings.yaml`: mode, posting windows, daily cap, per-agent model/effort, cadence, link strategy, compliance lists.
- `config/sources.yaml`: feeds, GitHub repos, DefiLlama thresholds, X watch-list, scout focus areas.
- `config/persona.md`: your voice. Edit freely; the profiler keeps a `.seed.md` backup.
- `prompts/*.md`: the agent prompts.

**Cost.** Every run logs its Claude usage and an estimated cost. At the defaults (all agents on
Claude Opus 5.5, 8 collect cycles and 3 scout runs a day), expect very roughly **$2–5/day**. To
cut it, move `scout` and `curator` to `claude-sonnet-5-5`, raise `cadence.collect`, or lower
`pipeline.max_items_to_curator`.

## Tests
```bash
pip install pytest && python -m pytest -q
```

# What I need from you

Everything is built and tested with fake API clients. To go live, I need the items below.
**Must-have** items are blockers. Everything else has a sensible default.

## Must-have

### 1. Anthropic API key (powers all agents)
- console.anthropic.com → API Keys → create key → `ANTHROPIC_API_KEY`
- Add some credit. Default settings cost very roughly $2–5/day; the logs print the real cost.

### 2. X API credentials for @0xYYcn (needed to post)
1. Go to developer.x.com and sign in **as @0xYYcn**. Create a Project and an App.
2. App → *User authentication settings*: enable OAuth 1.0a, set permissions to **Read and write**,
   type *Web App / Automated App or Bot*. Callback URL and website can be `https://x.com/0xYYcn`.
3. App → *Keys and tokens*: copy the **API Key + Secret**, then generate an **Access Token + Secret**.
   Generate the access token *after* setting Read+write, otherwise it is read-only. Also copy the **Bearer Token**.
4. Put them in `.env` (local) or GitHub repo secrets: `X_API_KEY`, `X_API_SECRET`, `X_ACCESS_TOKEN`,
   `X_ACCESS_TOKEN_SECRET`, `X_BEARER_TOKEN`.
5. **Tell me which X API access level/plan you have.** X changes its tiers and pricing often. Posting
   needs write access; reading your timeline (for `profile`), the watch-list and metrics needs read
   access, which may cost extra. If you only have write access, the bot still works: use the archive
   route in step 4 below for your voice, and leave `x_watch` off.
6. Recommended: in X settings, add the "Automated" account label pointing to your own handle.
   It's transparent and keeps you clearly within X's automation rules.

### 3. Telegram bot (your approval inbox)
1. In Telegram, message **@BotFather** → `/newbot` → copy the token → `TELEGRAM_BOT_TOKEN`
2. Open your new bot and send it "hi"
3. Run `python -m xbot telegram-setup` → it prints `TELEGRAM_CHAT_ID=...`
   (or send me the token and I'll do it)

If you'd rather not use Telegram, use `python -m xbot review` in the terminal.

### 4. Your voice: one of these
- **A. X data archive (free, best):** x.com → Settings → Your account → *Download an archive of your data*.
  It takes about a day to arrive. Unzip it and give me `data/tweets.js`, or run
  `python -m xbot profile --archive path/to/tweets.js` yourself.
- **B. X API read access:** I'll run `python -m xbot profile` (or the *xbot-profile* GitHub workflow).
- **C. Paste 20-50 of your best posts** and I'll build the voice guide from them.

I could not read x.com/0xYYcn from here because X requires a login. `config/persona.md` is
currently a **seed** built from the interests you described, and it will be replaced by your real voice.

## Decisions I need from you (defaults in brackets)

| question | default | where |
|---|---|---|
| Language: English, Chinese, or both? | English | `settings.yaml: language` |
| Timezone for posting windows | America/New_York | `settings.yaml: timezone` |
| Posting mode: draft / approve / auto | **approve** | `settings.yaml: mode` |
| Max posts per day, and posting windows | 6/day; 08:00-11:30, 12:00-15:00, 18:30-23:00 | `settings.yaml: posting` |
| X Premium? (long posts) | no, 280 chars | `posting.max_post_chars` |
| Source link inline, as self-reply, or none | self-reply | `posting.link_strategy` |
| **Affiliations** (projects you work for / advise / hold materially) for disclosure | none | `compliance.affiliations` |
| Tokens/projects to never mention | none | `compliance.avoid_tokens` |
| Accounts to watch for quote/reply opportunities (needs X read) | suggested list, off | `sources.yaml: x_watch` |
| Extra sources you read daily (newsletters, Substacks, Discords, dashboards) | the current list | `sources.yaml` |
| Where to run: GitHub Actions or a VPS/Docker | GitHub Actions | see README |
| Model budget: all Opus 5.5, or Sonnet 5.5 for scout/curator | all Opus 5.5 | `settings.yaml: models` |

## Going live, step by step
1. `cp .env.example .env` and fill it in, then run `python -m xbot doctor --live` until everything shows OK.
2. Run `python -m xbot profile --archive .../tweets.js`, then read `config/persona.md` and tweak it.
3. Run `python -m xbot --dry-run run --scout` and look at the drafts. Tell me what feels off and I'll tune the prompts.
4. Run `python -m xbot post-test "testing" --yes` to verify write access (delete the post after).
5. Turn on scheduling: add GitHub secrets (`ANTHROPIC_API_KEY`, `X_*`, `TELEGRAM_*`) and set the
   repo variable `XBOT_ENABLED=true`. Or run the Docker container.
6. Approve drafts in Telegram for a week or two. Once the drafts look consistently good, consider
   `mode: auto` with `auto_post_min_score: 85`.

## Known limits / notes
- GitHub release tracking returned 403 from my sandbox (network policy); it will work from GitHub Actions or your machine.
- DefiLlama's DePIN category has little TVL or revenue data. DePIN coverage mostly comes from news, the scout, and the watch-list.
- Some feeds may break over time. Failures are logged and skipped, never fatal.

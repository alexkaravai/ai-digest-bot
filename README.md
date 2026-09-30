# AI digest bot

Daily AI news digest in Telegram. A GitHub Actions cron fetches feeds, drops anything already sent, has Claude pick and summarize the top items, and posts one message around 8:00am Los Angeles time.

```
feeds → skip seen → Claude ranks + summarizes (1 call) → Telegram → commit state/seen.json
```

## Setup (about 10 minutes)

1. **Create the bot.** In Telegram, message [@BotFather](https://t.me/BotFather), send `/newbot`, follow the prompts. Copy the token.
2. **Get your chat ID.** Send your new bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser. Copy `result[0].message.chat.id`.
3. **Get an Anthropic API key** at [console.anthropic.com](https://console.anthropic.com).
4. **Create a private GitHub repo** and push this folder to it.
   Private matters: GitHub disables scheduled workflows on inactive public repos, and it keeps your feed choices to yourself.
5. **Add secrets.** Repo → Settings → Secrets and variables → Actions → New repository secret:
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `ANTHROPIC_API_KEY`
6. **Dry run.** Actions → AI digest → Run workflow, leave "dry_run" checked. The formatted message prints in the log.
7. **Real run.** Run it again with "dry_run" unchecked. You should get the digest in Telegram, and a `state: YYYY-MM-DD` commit appears in the repo.

After that it runs on its own every morning.

## How it behaves

- **Schedule:** GitHub starts scheduled runs late (4 to 5 hours in the first week), so the workflow fires hourly from 09:17 to 22:17 UTC. The script sends on the first run that lands at or after 8am LA time and nothing was sent yet today; every other run exits in seconds. Later runs double as retries if a send fails.
- **Always sends something.** Slow day: "Quiet day, nothing worth your time." Claude down: the 10 newest headlines as links. Broken feeds are listed at the bottom. No message means the workflow itself failed, so check the Actions tab (GitHub also emails you on failures).
- **Dedupe:** every item shown to Claude is marked seen for 14 days, so rejected items don't come back tomorrow.
- **Links are checked:** any item whose URL isn't one of the fetched candidates is dropped, so a made-up link can't reach you.
- A manual "Run workflow" skips the time check. A real manual run counts as today's digest.

## Customizing

| Change | Where |
|---|---|
| Add or remove feeds | `sources.yaml` |
| Ranking rules, tone, categories | `prompts/rank.md` |
| Your interests ("more X, less Y") | `preferences.md`, injected into the prompt each run |
| Send time / timezone / model | env vars `DIGEST_HOUR`, `DIGEST_TZ`, `CLAUDE_MODEL` in the workflow |

If you change `DIGEST_HOUR`, check the hourly cron range still covers it in both PST and PDT.

## Run locally

```bash
pip install -r requirements.txt
ANTHROPIC_API_KEY=sk-... python -m digest --dry-run --force
pytest   # needs: pip install pytest
```

`--dry-run` prints instead of sending and doesn't touch state. `--force` skips the time and once-per-day checks.

## Sources (verified Sep 27, 2026)

| Source | Feed | Note |
|---|---|---|
| Techmeme | techmeme.com/feed.xml | Only the latest 15 headlines, so TechCrunch and The Verge back it up |
| TechCrunch AI, The Verge AI | category RSS | |
| Anthropic news + research | community mirror (github.com/Olshansk/rss-feeds) | Anthropic has no official feed. Mirror can lag a few hours |
| OpenAI, Google DeepMind, METR, Simon Willison | official RSS | |
| Alignment Forum | official RSS | Safety essays and research |
| Hugging Face Daily Papers | JSON API | Top 8 by upvotes, none on weekends |
| Hacker News | Algolia API | ≥100 points, AI keywords in title |
| arXiv | API | cs.AI/LG/CL, safety keywords (reward hacking, scheming, sandbagging, ...) |

Dropped: Anthropic alignment blog (no feed). If a feed breaks, it shows up as "Unavailable today" in the digest.

## Cost

About 150 input tokens per candidate. A typical weekday is 50 to 100 candidates, so roughly 10 to 17k input and 1.5k output tokens per day. At $3 / $15 per million tokens that's about $0.05 to $0.08 a day, around $2 a month. Check current pricing for the model you use. GitHub Actions minutes are free at this usage (about 1 minute per run).

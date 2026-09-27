import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import telegram
from .rank import rank
from .sources import fetch_all
from .state import State

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo(os.getenv("DIGEST_TZ", "America/Los_Angeles"))
SEND_HOUR = int(os.getenv("DIGEST_HOUR", "8"))
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")
MAX_CANDIDATES = 120


def main():
    ap = argparse.ArgumentParser(prog="digest")
    ap.add_argument("--dry-run", action="store_true", help="print the message, don't send or write state")
    ap.add_argument("--force", action="store_true", help="skip the send-time and once-per-day checks")
    args = ap.parse_args()

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(TZ)
    today = now_local.date()
    state = State(ROOT / "state" / "seen.json")

    if not args.force:
        if now_local.hour < SEND_HOUR:
            print(f"[skip] {now_local:%H:%M} {TZ} is before {SEND_HOUR}:00")
            return
        if state.last_sent_date == today.isoformat():
            print(f"[skip] already sent today ({today})")
            return

    sources = yaml.safe_load((ROOT / "sources.yaml").read_text())["sources"]
    fetched, failed = fetch_all(sources, now_utc)

    unique = {}
    for c in fetched:
        if c.url and not state.is_seen(c.url):
            unique.setdefault(c.url, c)
    candidates = sorted(unique.values(), key=lambda c: c.published, reverse=True)[:MAX_CANDIDATES]
    print(f"[info] fetched={len(fetched)} new={len(unique)} candidates={len(candidates)} failed={failed}")

    if len(failed) == len(sources):
        blocks = [telegram.header(today), "⚠️ Every source failed. Check the workflow logs."]
    elif not candidates:
        blocks = telegram.format_digest({"quiet_day": True}, today, failed)
    else:
        preferences_file = ROOT / "preferences.md"
        preferences = preferences_file.read_text() if preferences_file.exists() else ""
        try:
            if not os.getenv("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY not set")
            digest = rank(candidates, ROOT / "prompts" / "rank.md", preferences, MODEL)
            blocks = telegram.format_digest(digest, today, failed)
        except Exception as ex:
            print(f"[error] {ex}")
            blocks = telegram.format_fallback(candidates, today, failed)

    messages = telegram.split_messages(blocks)

    if args.dry_run:
        print("\n----- message -----\n".join([""] + messages))
        return

    token, chat_id = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    for m in messages:
        telegram.send(token, chat_id, m)

    state.mark_seen([c.url for c in candidates], today)
    state.prune(today)
    state.last_sent_date = today.isoformat()
    state.save()
    print(f"[done] sent {len(messages)} message(s)")


if __name__ == "__main__":
    sys.exit(main())

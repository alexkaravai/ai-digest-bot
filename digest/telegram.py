import time
from datetime import date
from html import escape

import requests

LIMIT = 4096
SECTIONS = [
    ("launches", "🚀 Launches"),
    ("safety", "⚠️ Safety & unexpected behavior"),
    ("tools", "🛠 Design & dev tools"),
    ("research", "📄 Research"),
    ("industry", "💼 Industry"),
]


def _e(s) -> str:
    return escape(str(s or ""), quote=True)


def header(today: date) -> str:
    return f"<b>AI digest · {today:%a, %b} {today.day}</b>"


def format_item(item) -> str:
    lines = [f'<a href="{_e(item["url"])}">{_e(item["title"])}</a> · {_e(item["source"])}', _e(item["summary"])]
    if item.get("why_it_matters"):
        lines.append(f"<i>Why it matters:</i> {_e(item['why_it_matters'])}")
    return "\n".join(lines)


def footer(failed_sources) -> list[str]:
    return [f"<i>Unavailable today: {_e(', '.join(failed_sources))}</i>"] if failed_sources else []


def format_digest(digest, today: date, failed_sources=()) -> list[str]:
    if digest["quiet_day"]:
        return [header(today), "Quiet day, nothing worth your time."] + footer(failed_sources)

    blocks = [header(today), "<b>Top story</b>\n" + format_item(digest["top_story"])]
    for key, title in SECTIONS:
        items = [i for i in digest["items"] if i["category"] == key]
        for n, item in enumerate(items):
            # keep a section header glued to its first item so a split never orphans it
            blocks.append((f"<b>{title}</b>\n" if n == 0 else "") + format_item(item))
    return blocks + footer(failed_sources)


def format_fallback(candidates, today: date, failed_sources=()) -> list[str]:
    newest = sorted(candidates, key=lambda c: c.published, reverse=True)[:10]
    lines = [f'• <a href="{_e(c.url)}">{_e(c.title)}</a> · {_e(c.source)}' for c in newest]
    return [header(today), "<b>Summaries unavailable today</b>"] + (lines or ["No new items found."]) + footer(failed_sources)


def split_messages(blocks, limit: int = LIMIT) -> list[str]:
    messages, current = [], ""
    for block in blocks:
        if len(block) > limit:
            block = block[: limit - 1] + "…"  # a single block this long never happens in practice
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit:
            messages.append(current)
            current = block
        else:
            current = candidate
    if current:
        messages.append(current)
    return messages


def send(token: str, chat_id: str, text: str):
    for attempt in range(3):
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            },
            timeout=20,
        )
        if r.status_code == 429:
            time.sleep(r.json().get("parameters", {}).get("retry_after", 5))
            continue
        if not r.ok:
            raise RuntimeError(f"Telegram error {r.status_code}: {r.text}")
        return
    raise RuntimeError("Telegram rate limit, gave up")

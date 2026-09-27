import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import feedparser
import requests

USER_AGENT = "ai-digest-bot/1.0 (personal Telegram digest)"
TIMEOUT = 20
DEFAULT_WINDOW_HOURS = 36


@dataclass
class Candidate:
    source: str
    title: str
    url: str
    published: datetime
    text: str = ""
    extra: dict = field(default_factory=dict)


def clean_text(raw: str, limit: int = 500) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = re.sub(r"\s+", " ", html.unescape(text)).strip()
    return text[:limit]


def _get(url, **kwargs):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, **kwargs)
    r.raise_for_status()
    return r


def _cutoff(src, now):
    return now - timedelta(hours=src.get("window_hours", DEFAULT_WINDOW_HOURS))


def _entry_time(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime.fromtimestamp(time.mktime(t), tz=timezone.utc) if t else None


def fetch_rss(src, now):
    feed = feedparser.parse(_get(src["url"]).content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"unparseable feed: {feed.bozo_exception}")
    out = []
    for e in feed.entries:
        published = _entry_time(e)
        if not published or published < _cutoff(src, now):
            continue
        summary = e.get("summary", "")
        url = e.get("link", "")
        if src.get("link_from_summary"):
            m = re.search(r'href="(https?://[^"]+)"', summary)
            if m and "techmeme.com" not in m.group(1):
                url = html.unescape(m.group(1))
        out.append(Candidate(src["name"], clean_text(e.get("title", ""), 200), url, published, clean_text(summary)))
    return out


def fetch_hn(src, now):
    since = int(_cutoff(src, now).timestamp())
    r = _get("https://hn.algolia.com/api/v1/search_by_date", params={
        "tags": "story",
        "numericFilters": f"points>={src.get('min_points', 100)},created_at_i>{since}",
        "hitsPerPage": 200,
    })
    pattern = re.compile(r"\b(" + "|".join(re.escape(k) for k in src["keywords"]) + r")\b", re.I)
    out = []
    for h in r.json()["hits"]:
        if not pattern.search(h.get("title", "")):
            continue
        discussion = f"https://news.ycombinator.com/item?id={h['objectID']}"
        out.append(Candidate(
            src["name"], h["title"], h.get("url") or discussion,
            datetime.fromtimestamp(h["created_at_i"], tz=timezone.utc),
            f"Hacker News: {h.get('points', 0)} points, {h.get('num_comments', 0)} comments.",
            {"discussion": discussion},
        ))
    return out


def fetch_arxiv(src, now):
    cats = " OR ".join(f"cat:{c}" for c in src["categories"])
    phrases = " OR ".join(f'abs:"{p}"' for p in src["phrases"])
    r = _get("https://export.arxiv.org/api/query", params={
        "search_query": f"({cats}) AND ({phrases})",
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": src.get("max_results", 40),
    })
    feed = feedparser.parse(r.content)
    out = []
    for e in feed.entries:
        published = _entry_time(e)
        if not published or published < _cutoff(src, now):
            continue
        url = re.sub(r"v\d+$", "", e.link)
        out.append(Candidate(src["name"], clean_text(e.title, 200), url, published, clean_text(e.summary)))
    return out


def fetch_hf_papers(src, now):
    papers = _get("https://huggingface.co/api/daily_papers", params={"limit": 100}).json()
    cutoff = _cutoff(src, now)
    fresh = []
    for p in papers:
        stamp = p["paper"].get("submittedOnDailyAt") or p.get("publishedAt")
        published = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if published >= cutoff:
            fresh.append((p, published))
    fresh.sort(key=lambda x: x[0]["paper"].get("upvotes", 0), reverse=True)
    return [
        Candidate(
            src["name"], clean_text(p["title"], 200), f"https://huggingface.co/papers/{p['paper']['id']}",
            published, f"{p['paper'].get('upvotes', 0)} upvotes. " + clean_text(p["paper"].get("summary", "")),
        )
        for p, published in fresh[: src.get("top_n", 8)]
    ]


FETCHERS = {"rss": fetch_rss, "hn_algolia": fetch_hn, "arxiv": fetch_arxiv, "hf_papers": fetch_hf_papers}


def fetch_all(sources, now):
    items, failed = [], []
    for src in sources:
        try:
            got = FETCHERS[src["type"]](src, now)
            print(f"[info] {src['name']}: {len(got)}")
            items.extend(got)
        except Exception as ex:
            print(f"[warn] {src['name']} failed: {ex}")
            failed.append(src["name"])
    return items, failed

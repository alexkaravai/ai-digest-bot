import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

RETENTION_DAYS = 14
TRACKING_PARAMS = ("utm_", "ref", "fbclid", "gclid", "mc_", "source")


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(TRACKING_PARAMS)]
    host = parts.netloc.lower().removeprefix("www.")
    return urlunsplit(("https", host, parts.path.rstrip("/"), urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode()).hexdigest()[:16]


class State:
    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text()) if path.exists() else {}
        self.last_sent_date = data.get("last_sent_date")
        self.seen = data.get("seen", {})

    def is_seen(self, url: str) -> bool:
        return url_hash(url) in self.seen

    def mark_seen(self, urls, today: date):
        for u in urls:
            self.seen.setdefault(url_hash(u), today.isoformat())

    def prune(self, today: date):
        cutoff = (today - timedelta(days=RETENTION_DAYS)).isoformat()
        self.seen = {h: d for h, d in self.seen.items() if d >= cutoff}

    def save(self):
        data = {"last_sent_date": self.last_sent_date, "seen": dict(sorted(self.seen.items()))}
        self.path.write_text(json.dumps(data, indent=1) + "\n")

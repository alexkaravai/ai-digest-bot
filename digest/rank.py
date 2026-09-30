import json
from pathlib import Path

import anthropic

CATEGORIES = ["launches", "safety", "tools", "research", "industry"]
MAX_ITEMS = 9

# Strict tool use: the API guarantees output matches this schema. Strict mode doesn't support
# unions, nulls or length limits, so "no why_it_matters" is "" and counts are enforced in validate().
ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_ids": {"type": "array", "items": {"type": "integer"}},
        "url": {"type": "string"},
        "title": {"type": "string"},
        "source": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "summary": {"type": "string"},
        "why_it_matters": {"type": "string", "description": "Empty string when not needed."},
    },
    "required": ["candidate_ids", "url", "title", "source", "category", "summary", "why_it_matters"],
    "additionalProperties": False,
}
REQUIRED_FROM_MODEL = ["candidate_ids", "category", "summary"]

TOOL = {
    "name": "submit_digest",
    "description": "Submit today's selected digest items.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "quiet_day": {"type": "boolean"},
            "top_story": {**ITEM_SCHEMA, "description": "Ignored when quiet_day is true."},
            "items": {"type": "array", "items": ITEM_SCHEMA, "description": f"At most {MAX_ITEMS}."},
        },
        "required": ["quiet_day", "top_story", "items"],
        "additionalProperties": False,
    },
}


CATEGORY_ALIASES = {
    "security": "safety", "alignment": "safety", "risk": "safety",
    "launch": "launches", "product": "launches", "products": "launches", "models": "launches",
    "tool": "tools", "devtools": "tools", "design": "tools",
    "paper": "research", "papers": "research", "science": "research",
    "business": "industry", "policy": "industry", "regulation": "industry", "funding": "industry",
}


class InvalidDigest(Exception):
    pass


def normalize_category(raw) -> str:
    cat = str(raw or "").strip().lower()
    cat = CATEGORY_ALIASES.get(cat, cat)
    if cat not in CATEGORIES:
        print(f"[warn] unknown category {raw!r}, using industry")
        cat = "industry"
    return cat


def build_user_message(candidates) -> str:
    lines = []
    for i, c in enumerate(candidates):
        lines.append(
            f"[{i}] source: {c.source} | published: {c.published:%Y-%m-%d %H:%M} UTC\n"
            f"title: {c.title}\nurl: {c.url}\ntext: {c.text or '(none)'}"
        )
    return "Candidates:\n\n" + "\n\n".join(lines)


def _decode(value):
    # the model sometimes returns a nested object or array as a JSON-encoded string
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            raise InvalidDigest(f"undecodable value: {value[:80]!r}")
    return value


def validate(result: dict, candidates) -> dict:
    if result.get("quiet_day") is True:
        return {"quiet_day": True, "top_story": None, "items": []}
    urls = {c.url for c in candidates}
    n = len(candidates)
    raw_top = _decode(result.get("top_story"))
    raw_items = _decode(result.get("items")) or []
    if not isinstance(raw_items, list):
        raise InvalidDigest(f"items is {type(raw_items).__name__}, not a list")

    def check(item):
        item = _decode(item)
        if not item:
            return None
        if not isinstance(item, dict):
            print(f"[warn] dropped non-object item: {item!r:.80}")
            return None
        missing = [k for k in REQUIRED_FROM_MODEL if not item.get(k)]
        if missing:
            print(f"[warn] dropped item missing {missing}")
            return None
        item["category"] = normalize_category(item["category"])
        ids = item["candidate_ids"]
        if not isinstance(ids, list) or any(not isinstance(i, int) or not (0 <= i < n) for i in ids):
            print(f"[warn] dropped item with bad candidate_ids {ids!r}")
            return None
        # fill gaps from the fetched feed item instead of dropping the story
        first = candidates[ids[0]]
        if not item.get("url"):
            item["url"] = first.url
        if not item.get("title"):
            item["title"] = first.title
        if not item.get("source"):
            item["source"] = first.source
        item["why_it_matters"] = item.get("why_it_matters") or None
        if item["url"] not in urls:
            print(f"[warn] unknown url {item['url']}, using the candidate's")
            item["url"] = first.url
        return item

    top = check(raw_top)
    items = [i for i in (check(x) for x in raw_items[:MAX_ITEMS]) if i]
    if not top and not items and (raw_top or raw_items):
        raise InvalidDigest("model returned items but none were valid")
    if not top and items:
        top, items = items[0], items[1:]

    # the model sometimes repeats the top story (or another item) in the list
    used_urls = {top["url"]} if top else set()
    used_ids = set(top["candidate_ids"]) if top else set()
    unique = []
    for i in items:
        if i["url"] in used_urls or used_ids & set(i["candidate_ids"]):
            continue
        used_urls.add(i["url"])
        used_ids.update(i["candidate_ids"])
        unique.append(i)
    items = unique
    quiet = bool(result.get("quiet_day")) or not top
    return {"quiet_day": quiet, "top_story": None if quiet else top, "items": [] if quiet else items}


def rank(candidates, prompt_path: Path, preferences: str, model: str, client=None) -> dict:
    client = client or anthropic.Anthropic()
    system = prompt_path.read_text().replace("{preferences}", preferences.strip() or "(none yet)")
    last_error = None
    for attempt in range(2):
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=4000,
                system=system,
                tools=[TOOL],
                tool_choice={"type": "tool", "name": "submit_digest"},
                messages=[{"role": "user", "content": build_user_message(candidates)}],
            )
            block = next(b for b in resp.content if b.type == "tool_use")
            result = block.input if isinstance(block.input, dict) else json.loads(block.input)
            print(f"[info] claude usage: in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
            return validate(result, candidates)
        except (InvalidDigest, StopIteration, json.JSONDecodeError, anthropic.APIError) as ex:
            last_error = ex
            print(f"[warn] rank attempt {attempt + 1} failed: {ex!r}")
    raise RuntimeError(f"ranking failed: {last_error!r}")

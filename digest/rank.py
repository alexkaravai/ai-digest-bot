import json
from pathlib import Path

import anthropic

CATEGORIES = ["launches", "safety", "tools", "research", "industry"]
MAX_ITEMS = 9

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "candidate_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 1},
        "url": {"type": "string"},
        "title": {"type": "string"},
        "source": {"type": "string"},
        "category": {"type": "string", "enum": CATEGORIES},
        "summary": {"type": "string"},
        "why_it_matters": {"type": ["string", "null"]},
    },
    "required": ["candidate_ids", "url", "title", "source", "category", "summary", "why_it_matters"],
}

TOOL = {
    "name": "submit_digest",
    "description": "Submit today's selected digest items.",
    "input_schema": {
        "type": "object",
        "properties": {
            "quiet_day": {"type": "boolean"},
            "top_story": {"anyOf": [ITEM_SCHEMA, {"type": "null"}]},
            "items": {"type": "array", "items": ITEM_SCHEMA, "maxItems": MAX_ITEMS},
        },
        "required": ["quiet_day", "top_story", "items"],
    },
}


class InvalidDigest(Exception):
    pass


def build_user_message(candidates) -> str:
    lines = []
    for i, c in enumerate(candidates):
        lines.append(
            f"[{i}] source: {c.source} | published: {c.published:%Y-%m-%d %H:%M} UTC\n"
            f"title: {c.title}\nurl: {c.url}\ntext: {c.text or '(none)'}"
        )
    return "Candidates:\n\n" + "\n\n".join(lines)


def validate(result: dict, candidates) -> dict:
    urls = {c.url for c in candidates}
    n = len(candidates)

    def check(item):
        if not item:
            return None
        missing = [k for k in ITEM_SCHEMA["required"] if k not in item]
        if missing:
            raise InvalidDigest(f"item missing {missing}")
        if item["category"] not in CATEGORIES:
            raise InvalidDigest(f"bad category {item['category']}")
        if not item["candidate_ids"] or any(not (0 <= i < n) for i in item["candidate_ids"]):
            raise InvalidDigest(f"bad candidate_ids {item['candidate_ids']}")
        if item["url"] not in urls:
            print(f"[warn] dropped item with unknown url: {item['url']}")
            return None
        return item

    top = check(result.get("top_story"))
    items = [i for i in (check(x) for x in result.get("items", [])[:MAX_ITEMS]) if i]
    if not top and items:
        top, items = items[0], items[1:]
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

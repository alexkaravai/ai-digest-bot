import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from digest import telegram
from digest.rank import InvalidDigest, rank, validate
from digest.sources import Candidate, clean_text
from digest.state import State, normalize_url

NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)
CANDS = [
    Candidate("OpenAI", "GPT & <friends>", "https://openai.com/a", NOW, "text"),
    Candidate("METR", "Eval", "https://metr.org/b", NOW, "text"),
]


def item(url="https://openai.com/a", category="launches", ids=(0,), why=None):
    return {"candidate_ids": list(ids), "url": url, "title": "T & <b>", "source": "S",
            "category": category, "summary": "Sum < 5 & > 2", "why_it_matters": why}


def test_validate_drops_unknown_url_and_promotes_top():
    out = validate({"quiet_day": False, "top_story": item(url="https://fake.com"),
                    "items": [item(url="https://metr.org/b", category="safety", ids=[1])]}, CANDS)
    assert out["top_story"]["url"] == "https://metr.org/b"
    assert out["items"] == []


def test_validate_rejects_when_nothing_valid():
    with pytest.raises(InvalidDigest):
        validate({"quiet_day": False, "top_story": item(ids=[99]), "items": []}, CANDS)


def test_validate_drops_one_bad_item_keeps_rest():
    out = validate({"quiet_day": False, "top_story": item(),
                    "items": [item(url="https://metr.org/b", ids=[99]), {"oops": 1}]}, CANDS)
    assert out["top_story"]["url"] == "https://openai.com/a" and out["items"] == []


def test_validate_decodes_json_string_fields():
    # regression: Sep 30 run got items as a JSON string and fell back to headlines
    other = item(url="https://metr.org/b", category="safety", ids=[1])
    out = validate({"quiet_day": False, "top_story": json.dumps(item()), "items": json.dumps([other])}, CANDS)
    assert out["top_story"]["url"] == "https://openai.com/a"
    assert [i["url"] for i in out["items"]] == ["https://metr.org/b"]


def test_validate_quiet_when_empty():
    assert validate({"quiet_day": False, "top_story": None, "items": []}, CANDS)["quiet_day"]


def test_format_escapes_everything():
    blocks = telegram.format_digest({"quiet_day": False, "top_story": item(why="W & x"), "items": []}, date(2026, 9, 27))
    text = "\n".join(blocks)
    assert "T &amp; &lt;b&gt;" in text and "Sum &lt; 5 &amp; &gt; 2" in text and "W &amp; x" in text
    assert blocks[0] == "<b>AI digest · Sun, Sep 27</b>"


def test_why_it_matters_optional():
    assert "Why it matters" not in telegram.format_item(item(why=None))


def test_split_respects_limit_and_blocks():
    blocks = ["x" * 1500] * 5
    msgs = telegram.split_messages(blocks)
    assert all(len(m) <= 4096 for m in msgs) and len(msgs) == 3
    assert sum(m.count("x" * 1500) for m in msgs) == 5


def test_split_ignores_markup_length():
    long_link = '<a href="https://example.com/' + "q" * 3000 + '">Short title</a> · Src'
    msgs = telegram.split_messages([long_link, long_link, "tail"])
    assert len(msgs) == 1


def test_validate_dedupes_top_story_and_maps_category():
    dup = item(category="security")
    other = item(url="https://metr.org/b", category="Security", ids=[1])
    out = validate({"quiet_day": False, "top_story": item(), "items": [dup, other]}, CANDS)
    assert [i["url"] for i in out["items"]] == ["https://metr.org/b"]
    assert out["items"][0]["category"] == "safety"


def test_validate_unknown_category_falls_back():
    out = validate({"quiet_day": False, "top_story": item(category="weird"), "items": []}, CANDS)
    assert out["top_story"]["category"] == "industry"


def test_section_header_glued_to_first_item():
    digest = {"quiet_day": False, "top_story": item(),
              "items": [item(url="https://metr.org/b", category="safety", ids=[1])]}
    blocks = telegram.format_digest(digest, date(2026, 9, 27))
    assert blocks[2].startswith("<b>⚠️ Safety")
    assert 'href="https://metr.org/b"' in blocks[2]


def test_fallback_escapes_titles():
    text = "\n".join(telegram.format_fallback(CANDS, date(2026, 9, 27), ["arXiv"]))
    assert "GPT &amp; &lt;friends&gt;" in text and "Unavailable today: arXiv" in text


def test_normalize_url_strips_tracking():
    assert normalize_url("https://www.X.com/a/?utm_source=t&id=3#top") == "https://x.com/a?id=3"


def test_state_roundtrip_and_prune(tmp_path):
    s = State(tmp_path / "seen.json")
    s.mark_seen(["https://a.com/1"], date(2026, 9, 1))
    s.mark_seen(["https://a.com/2"], date(2026, 9, 27))
    s.prune(date(2026, 9, 27))
    s.last_sent_date = "2026-09-27"
    s.save()
    s2 = State(tmp_path / "seen.json")
    assert s2.is_seen("https://a.com/2?utm_medium=x") and not s2.is_seen("https://a.com/1")


def test_clean_text():
    assert clean_text("<p>A &amp; <b>B</b></p>\n\n C") == "A & B C"


class FakeClient:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        assert kwargs["tool_choice"]["name"] == "submit_digest"
        assert "(none yet)" in kwargs["system"]
        return SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", input=self.results.pop(0))],
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )


def test_rank_retries_once_on_invalid(tmp_path):
    prompt = tmp_path / "rank.md"
    prompt.write_text("prefs: {preferences}")
    bad = {"quiet_day": False, "top_story": item(ids=[99]), "items": []}
    good = {"quiet_day": False, "top_story": item(), "items": []}
    client = FakeClient([bad, good])
    out = rank(CANDS, prompt, "", "m", client=client)
    assert client.calls == 2 and out["top_story"]["url"] == "https://openai.com/a"

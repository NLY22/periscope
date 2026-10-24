"""Platform rows must not discard the text their collector captured.

`platform_lookup` hardcoded `"body": ""` for every platform row, whatever the
collector returned. v2ex is the case that makes it visible: sov2ex returns up to
1,200 characters of post content, which was parked in `text` while `body` stayed
empty.

Three things read `body` and were therefore wrong:
  - the renderer (`正文: 0 字` on a post that had 1,200 characters);
  - `runstore.write`, which stored body="" into the run shard;
  - anything that later counts "how many results carried body text".

The threshold matches `core.search.MIN_BODY` (400), which is the project's
existing definition of "has a body rather than a snippet".
"""

from __future__ import annotations

import unittest.mock as mock

import pytest

import topic
from collectors import collector, fanout


def _run_platform_lookup(rows, monkeypatch, speed="fast"):
    """Drive `platform_lookup` with a stub collector."""

    def fake_collect(topic_text, **kw):
        st = {"elapsed": 0.1, "total": len(rows), "per": [], "sources": 1,
              "failed": [], "over_budget": [], "budget": 0.0,
              "attempted": 1, "skipped_browser": []}
        return list(rows), st

    monkeypatch.setattr(fanout, "collect_topic", fake_collect)
    return topic.platform_lookup("t", speed=speed, budget=10)


def test_a_long_platform_text_becomes_the_body(monkeypatch):
    """A 1,200-character post has a body; the row must say so."""
    rows = [{
        "title": "一条 v2ex 帖子", "url": "https://v2ex.com/t/1",
        "source": "v2ex_search", "text": "长" * 1200,
    }]
    out, _st = _run_platform_lookup(rows, monkeypatch)
    assert len(out) == 1
    assert len(out[0]["body"]) == 1200, (
        "the collector's text was thrown away instead of becoming the body")
    assert out[0]["text"] == "长" * 1200


def test_a_short_platform_text_stays_a_snippet(monkeypatch):
    """Below the 400-character line the text is a teaser, not a body."""
    rows = [{
        "title": "短帖", "url": "https://v2ex.com/t/2",
        "source": "v2ex_search", "text": "短" * 120,
    }]
    out, _st = _run_platform_lookup(rows, monkeypatch)
    assert out[0]["body"] == ""
    assert out[0]["text"] == "短" * 120, "the snippet must still be available"


def test_the_boundary_is_exactly_the_project_threshold(monkeypatch):
    """399 is a snippet, 400 is a body - matching `core.search.MIN_BODY`."""
    from core import search as research

    assert research.MIN_BODY == 400

    rows = [
        {"title": "399", "url": "https://x/399", "source": "v2ex_search",
         "text": "x" * 399},
        {"title": "400", "url": "https://x/400", "source": "v2ex_search",
         "text": "x" * 400},
    ]
    out, _st = _run_platform_lookup(rows, monkeypatch)
    by_title = {r["title"]: r for r in out}
    assert by_title["399"]["body"] == ""
    assert len(by_title["400"]["body"]) == 400


def test_a_row_with_no_text_gets_no_body(monkeypatch):
    """Absent stays absent: no invented body."""
    rows = [{"title": "无正文", "url": "https://x/none", "source": "weibo_search"}]
    out, _st = _run_platform_lookup(rows, monkeypatch)
    assert out[0]["body"] == ""
    assert out[0]["text"] == ""


def test_the_body_reaches_the_run_shard(monkeypatch, scratch_path):
    """The shard is what an ingest reads later; a lost body is lost twice."""
    from datetime import datetime

    from core import paths, runstore

    rows = [{
        "title": "带正文的帖子", "url": "https://v2ex.com/t/3",
        "source": "v2ex_search", "text": "正" * 800,
    }]
    out, _st = _run_platform_lookup(rows, monkeypatch)

    base = scratch_path / "topics"
    base.mkdir()
    with mock.patch.object(paths, "TOPIC_DIR", base):
        path = runstore.write("t", out, created_at=datetime(2026, 10, 19, 12, 0, 0))
    stored = runstore.read(path)["rows"][0]
    assert len(stored.get("body") or "") == 800, (
        "the run shard lost the body, so a later ingest cannot recover it")

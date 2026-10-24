"""Run shards: written on every topic query, readable without the network.

P0's contract is narrow and worth pinning exactly: a query must leave a file
behind, that file must contain the raw rows of all three layers, and it must be
readable back with no network and no database.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from core import runstore


@pytest.fixture
def topic_dir(scratch_path):
    base = scratch_path / "topics"
    base.mkdir()
    return base


def _rows():
    return [
        {"title": "API 行", "url": "https://a.example/x", "date": "2026-10-18",
         "body": "正文", "_origin": "api"},
        {"title": "本地行", "url": "https://b.example/y", "date": "2026-10-15",
         "_origin": "local"},
        {"title": "微博行", "url": "https://weibo.com/detail/R1", "date": "2026-10-19",
         "_origin": "platform", "source": "weibo_search",
         "metrics": {"reposts": 0, "comments": 2, "likes": 7}},
    ]


def _write(topic_dir, topic="人形机器人", rows=None, stamp=None):
    import unittest.mock as mock

    with mock.patch.object(runstore.paths, "TOPIC_DIR", topic_dir):
        return runstore.write(
            topic, rows if rows is not None else _rows(),
            argv=["--topic", topic], elapsed=5.9,
            stats={"api": 1, "local": 1, "platform": 1},
            notes=["note"], created_at=stamp or datetime(2026, 10, 19, 13, 20, 0),
        )


def test_write_lands_in_the_topic_dir(topic_dir):
    path = _write(topic_dir)
    assert path.exists()
    assert path.parent == topic_dir
    assert path.name.startswith("20261019-132000")


def test_round_trip_keeps_the_layers_and_the_metadata(topic_dir):
    path = _write(topic_dir)
    payload = runstore.read(path)
    assert payload["version"] == runstore.VERSION
    assert payload["topic"] == "人形机器人"
    assert payload["stats"]["platform"] == 1
    assert [r["_layer"] for r in payload["rows"]] == ["api", "local", "platform"]


def test_engagement_survives_and_is_not_invented(topic_dir):
    path = _write(topic_dir)
    rows = runstore.read(path)["rows"]
    with_metrics = [r for r in rows if r.get("metrics")]
    assert len(with_metrics) == 1, "only the row that measured engagement keeps it"
    assert with_metrics[0]["metrics"]["likes"] == 7


def test_empty_metrics_are_dropped_not_stored(topic_dir):
    rows = _rows() + [{"title": "无数据", "url": "https://c.example/z",
                       "_origin": "platform", "metrics": {}}]
    path = _write(topic_dir, rows=rows)
    stored = runstore.read(path)["rows"]
    assert "metrics" not in stored[-1], "an empty dict would read as zero engagement"


def test_secrets_do_not_reach_the_file(topic_dir):
    rows = _rows() + [{"title": "带 cookie", "url": "https://d.example/w",
                       "_origin": "platform", "cookies": {"SUB": "secret"}}]
    path = _write(topic_dir, rows=rows)
    assert "secret" not in path.read_text(encoding="utf-8")


def test_latest_picks_the_newest_file(topic_dir):
    import unittest.mock as mock

    _write(topic_dir, stamp=datetime(2026, 10, 19, 10, 0, 0))
    second = _write(topic_dir, stamp=datetime(2026, 10, 19, 12, 0, 0))
    with mock.patch.object(runstore.paths, "TOPIC_DIR", topic_dir):
        assert runstore.latest() == second
        assert runstore.find("人形机器人") == second
        assert runstore.find("不存在的话题") is None


def test_latest_is_none_on_an_empty_dir(topic_dir):
    import unittest.mock as mock

    with mock.patch.object(runstore.paths, "TOPIC_DIR", topic_dir):
        assert runstore.latest() is None


def test_write_is_atomic(topic_dir):
    """A crash mid-write must not leave a half file that `latest()` would pick."""
    _write(topic_dir)
    leftovers = list(topic_dir.glob("*.tmp"))
    assert not leftovers, "temporary file survived the write"


def test_slug_keeps_cjk_and_drops_path_separators(topic_dir):
    path = _write(topic_dir, topic="人形机器人/安全")
    assert "/" not in path.name
    assert "人形机器人" in path.name

"""What the collector makes printable, pinned at the string boundary.

Every case here came from data: `_clean` is the one function every scraper's
title passes through, and each form it used to miss reached the screen. The
repository's own run shards held 60 rows whose title still read
`\\u003cem\\u003e人形机器人\\u003c/em\\u003e`.

The assertions are on `_clean` and on `extract_rows`, not on a live request:
the cleaning rule is what regressed, and it is decidable without the network.
"""

from __future__ import annotations

import pytest

from collectors import collector


# --------------------------------------------------------------- what must go
@pytest.mark.parametrize("raw, expected", [
    # Real tags: always worked, kept as the regression baseline.
    ("有温度的<em>人形机器人</em>来了", "有温度的 人形机器人 来了"),
    # Escaped tags *inside* the JSON string: the defect. 头条's search response
    # carries the highlight markup this way, so the text is still escaped after
    # json.loads.
    (r"有温度的\u003cem\u003e人形机器人\u003c/em\u003e来了",
     "有温度的 人形机器人 来了"),
    (r"\u003cem\u003e人形机器人\u003c/em\u003e-视频", "人形机器人 -视频"),
    (r"\u003cb\u003ebold\u003c/b\u003e text", "bold text"),
    # Entities were never decoded either.
    ("A &amp; B &#39;quoted&#39;", "A & B 'quoted'"),
    ('他说&quot;你好&quot;', '他说"你好"'),
    # Both at once.
    ("&lt;b&gt;粗&lt;/b&gt; A &amp; B", "<b>粗</b> A & B"),
])
def test_markup_never_reaches_the_screen(raw: str, expected: str) -> None:
    assert collector._clean(raw) == expected


# ------------------------------------------------------- what must be preserved
@pytest.mark.parametrize("raw", [
    "人形机器人走上世界舞台",
    "固态电池 🔋 2030",
    "https://a.com/x?y=1&z=2",
    r"path\to\nfile",          # a literal backslash is not an escape
    r"line1\nline2",           # only \uXXXX is decoded; \n is left alone
])
def test_ordinary_text_is_untouched(raw: str) -> None:
    assert collector._clean(raw) == raw


def test_cjk_survives_the_escape_decode() -> None:
    """The first attempt used `unicode_escape`, which decodes as latin-1.

    A title that already contains CJK came back as mojibake, and the round-trip
    used to repair it fails on any character outside latin-1. Decoding only
    `\\uXXXX` has neither problem - this is the test that would have caught it.
    """
    raw = r"\u003cem\u003e中文标题\u003c/em\u003e test"
    assert collector._clean(raw) == "中文标题 test"


def test_an_entity_is_unescaped_once_not_twice() -> None:
    """`&amp;amp;` is an author writing the text "&amp;", not "&".

    One unescape pass is the rule. Two would silently rewrite a published title
    that legitimately contains an entity.
    """
    assert collector._clean("A &amp;amp; B") == "A &amp; B"


def test_a_lone_surrogate_half_is_left_alone() -> None:
    """`\\ud800` cannot be encoded; decoding it would raise inside a scraper."""
    raw = r"title \ud800 tail"
    assert "\\ud800" in collector._clean(raw)


def test_empty_and_non_string_inputs() -> None:
    assert collector._clean(None) == ""
    assert collector._clean("") == ""
    assert collector._clean({"a": "1"}) == '{"a": "1"}'


# ------------------------------------------------------------ the wired path
def test_extract_rows_applies_the_cleaning_to_a_real_payload_shape() -> None:
    """The toutiao search payload, reduced to the field the regex reads.

    Asserted through `extract_rows` rather than `_clean` alone, because the bug
    was only visible end to end: the function was correct for the shapes it
    handled, and the source's own escaped payload was not one of them.
    """
    by_key = {s.key: s for s in collector.CATALOG}
    src = by_key["toutiao_search"]
    body = '{"data":[{"title":"\\u003cem\\u003e人形机器人\\u003c/em\\u003e-视频"}]}'
    rows = collector.extract_rows(src, body, "application/json", base_url=src.url)
    assert rows, "the pattern stopped matching the payload shape it was written for"
    assert rows[0]["title"] == "人形机器人 -视频"
    assert "\\u003c" not in rows[0]["title"]

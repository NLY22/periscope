"""B's merge layer: the three real duplicate shapes, plus the score bounds.

The cases come from the refactor's field notes. They are asserted through
`merge.merge`, not through `key_of` equality: the bucket logic deliberately
matches a title that is a prefix of another (`...新机遇` vs `...新机遇经济`),
so comparing raw keys would fail on the exact case the code is meant to fix.
"""

from __future__ import annotations

from core import merge


def _merged(a: str, b: str):
    rows = [
        {"title": a, "url": "https://example.com/a", "author": "甲"},
        {"title": b, "url": "https://example.com/b", "author": "乙"},
    ]
    return merge.merge([], rows)


def test_site_suffix_and_self_repeat_collapse():
    assert len(_merged(
        "环球人物年度盛典分论坛聚焦钠电新机遇__经济网_国家一类新闻网站",
        "环球人物年度盛典分论坛聚焦钠电新机遇",
    )) == 1


def test_self_repeated_title_collapses():
    assert len(_merged(
        "钠离子电池加速从实验室走向应用场 - 钠离子电池加速从实验室走向应用场",
        "钠离子电池加速从实验室走向应用场",
    )) == 1


def test_prefix_modifier_collapses():
    assert len(_merged(
        "可以凌空飞踢！中国人形机器人走上世界舞台",
        "中国人形机器人走上世界舞台",
    )) == 1


def test_different_titles_stay_apart():
    assert len(_merged("完全不同的两个标题甲", "完全不同的两个标题乙")) == 2


def test_merged_row_counts_distinct_authors_as_publishers():
    rows = _merged("同一条新闻的标题", "同一条新闻的标题")
    assert len(rows) == 1
    assert rows[0]["_independent"] == 2
    assert rows[0]["_score"] <= 1.0


def test_score_is_bounded():
    for row in (
        {"trust": 0.0, "date": "2026-10-19", "_origin": "platform"},
        {"trust": 1.0, "date": "2026-10-19", "_origin": "local"},
        {"authority": 4, "rerank": 3, "date": "2026-10-19"},
    ):
        assert 0.0 <= merge.score_of(row) <= 1.0


# ------------------------------------------------------- entity normalisation
def test_an_escaped_ampersand_does_not_block_a_merge():
    """`&amp;` and `&` are the same publisher writing the same word.

    Republication sites escape `&`; the original does not. Before the fix the
    fingerprints were `a&ampb...` and `a&b...`, so one story counted twice -
    and it counted as *two independent sources*, which inflates the
    independence bonus. That is the worst shape of this bug: the duplicate is
    not merely visible, it is scored as corroboration.
    """
    assert len(_merged(
        "A &amp; B 公司发布新产品",
        "A & B 公司发布新产品",
    )) == 1


def test_entity_unescaping_happens_once():
    """`&amp;amp;` is the author writing the text `&amp;`."""
    a = merge.norm_text("X &amp;amp; Y")
    b = merge.norm_text("X &amp; Y")
    assert a != b, "a second unescape pass would erase the distinction"


def test_other_entities_normalise_too():
    """Every entity form, not just `&`.

    The titles are deliberately long: `key_of` falls back to URL identity when a
    normalised title is under six characters, so a short fixture would pass or
    fail for a reason unrelated to entity handling.
    """
    for entity, plain in (("&quot;", '"'), ("&#39;", "'"), ("&lt;", "<")):
        assert len(_merged(
            "某公司发布新产品%s引起广泛讨论" % entity,
            "某公司发布新产品%s引起广泛讨论" % plain,
        )) == 1


def test_unescaping_does_not_create_a_false_merge():
    """Bite proof: cleaning entities must not make different titles collide."""
    assert len(_merged("A &amp; B 发布芯片", "A &amp; B 宣布裁员")) == 2

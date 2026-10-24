"""The two halves of "the term is in the title" being a weak signal.

Background, measured on this repository's corpus:

    rows where `title` is at least as long as `content`   7,616
    of those, `title` longer than 120 characters          6,695
    by source                                            twitter 6,499, 掘金 990

Those collectors store the *post text* in `title`. `core/search.py` gives a
bonus when the query term appears in the title, on the theory that a headline
naming the subject is stronger evidence than a body mentioning it. With a
paragraph in the title field, that bonus fires for any post that mentions the
term anywhere - so a stock-market recap whose last clause says 固态电池 ranked
6th and 7th for a battery-technology query.

Two independent repairs, both pinned here:

1. `tools/ingest_all._split_title` - separate the headline from the body at
   ingest time, so the field means what the ranking thinks it means.
2. `core/search._title_leads_with` - require the term near the *front* of the
   title, so rows already in the corpus (and any future feed that has the same
   shape) cannot collect the bonus from a late mention.

Either alone would help; together they mean neither has to be perfect.
"""

from __future__ import annotations

import importlib

import pytest

from core import search as research

ingest_all = importlib.import_module("tools.ingest_all")


# ------------------------------------------------------------ the ingest split
@pytest.mark.parametrize("raw, expect_title_start, expect_body_start", [
    # Chinese: sentence end is the split point. Length matters - the rule keys on
    # `MAX_TITLE_CHARS` (120), and the rows this exists for measured 148-153
    # characters. A short fixture would pass for the wrong reason.
    ("超导、可控核聚变概念直接冲上涨幅榜前列。血制品、民航也跟涨，"
     "但最亮眼的还是超导和核聚变 —— 融发涨停，同宇涨超 10%，多只个股封板，"
     "市场情绪明显回暖，资金持续流入该板块，后续值得关注，建议持续跟踪资金动向"
     "与政策落地节奏，等待更明确的右侧信号再考虑加仓。",
     "超导、可控核聚变概念直接冲上涨幅榜前列",
     "血制品"),
    # Chinese: no sentence end before the cap, clause separator is used instead.
    ("下周热点板块预判 目前市场尚未形成系统性反转信号，下周大盘仍然会震荡加剧，"
     "热点板块轮动中需紧扣政策落地+业绩验证两条主线，固态电池、机器人等题材可继续关注，"
     "但要控制仓位，不宜追高，等待回调再做打算，同时留意成交量能否持续放大，"
     "以及北向资金的流向是否配合。",
     "下周热点板块预判 目前市场尚未形成系统性反转信号",
     "下周大盘"),
])
def test_a_body_in_the_title_field_is_split(raw, expect_title_start,
                                            expect_body_start):
    title, body = ingest_all._split_title(raw, "")
    assert title.startswith(expect_title_start), title
    assert body.startswith(expect_body_start), body
    assert len(title) < len(raw), "the title was not shortened at all"
    assert len(body) > 0, "the rest of the text was discarded instead of moved"


def test_english_prose_without_a_terminator_still_splits():
    """English posts often run two sentences together with no punctuation."""
    raw = ("Today, we are releasing Bonsai 2 2B, 4B, and 9B. Based on Qwen3.5, "
           "these models cover a wide range of deployment targets and the "
           "smallest one runs on a laptop, which is the interesting part.")
    title, body = ingest_all._split_title(raw, "")
    assert len(title) < len(raw)
    assert body, "the remainder must be kept as body text"


def test_a_short_title_is_left_alone():
    """The rule keys on length, so an ordinary headline is untouched."""
    title, body = ingest_all._split_title("固态电池真量产了！", "")
    assert title == "固态电池真量产了！"
    assert body == ""


def test_a_title_with_a_real_body_is_left_alone():
    """When the collector already filled `text`, the title is a title."""
    title, body = ingest_all._split_title("一个足够长但仍是标题的文字" * 12,
                                          "正文在这里")
    assert title.startswith("一个足够长")
    assert body == "正文在这里"


def test_the_split_never_loses_text():
    """Every character of the original must survive in title or body."""
    raw = ("十大券商策略：A股处于底部区域，配置上建议关注三条主线，"
           "一是以人工智能为代表的科技成长，二是以固态电池为代表的新能源，"
           "三是高股息红利资产，短期市场或维持震荡格局，建议均衡配置。")
    title, body = ingest_all._split_title(raw, "")
    joined = (title + body).replace(" ", "")
    assert joined.replace(",", "").replace("，", "") == \
        raw.replace(" ", "").replace(",", "").replace("，", ""), (
        "the split dropped or duplicated characters")


# ------------------------------------------------------- the ranking lead check
def test_a_term_late_in_the_title_does_not_earn_the_bonus():
    """The exact shape that let a stock recap into the results."""
    title = research._norm(
        "本周主板跌幅榜来了！一批个股大跌超20%，要警惕一个信号 本周主板跌幅榜"
        "整理好了，看着还是挺有感触的。力勤资源、固态电池、天齐锂业都在里面")
    assert not research._title_leads_with(title, ["固态电池"])


def test_a_term_at_the_front_of_the_title_earns_it():
    title = research._norm("固态电池真量产了！全国首条GWh级产线投产")
    assert research._title_leads_with(title, ["固态电池"])


def test_a_short_headline_keeps_the_bonus():
    """A normal 20-character headline puts the term well inside 60%."""
    for title, term in (
        ("科学家发现核聚变捷径 或大幅降低点火能耗", "核聚变"),
        ("韩国放宽工厂机器人安全限制", "机器人"),
        ("DeepSeek 出现服务故障", "DeepSeek"),
    ):
        assert research._title_leads_with(research._norm(title), [term]), title


def test_the_lead_rule_is_not_vacuous():
    """A term absent from the title must never pass, wherever the cut falls."""
    assert not research._title_leads_with(
        research._norm("完全无关的一条内容，只是在正文里提到过"), ["固态电池"])
    assert not research._title_leads_with("", ["固态电池"])
    assert not research._title_leads_with("标题", [])


def test_a_title_that_is_its_own_body_still_passes_when_it_leads():
    """The rule must not punish a long post that *opens* on the subject.

    Some of these are genuine - a first-person post whose opening sentence is
    about the topic. Demoting them would be the over-correction.
    """
    title = research._norm(
        "固态电池真量产了！全国首条GWh级全固态电池量产线在广州南沙投产，"
        "巨湾技研通过4GWh储能项目验证了工艺一致性，这是一个值得记录的节点")
    assert research._title_leads_with(title, ["固态电池"])

"""检索质量的验收测试。

这些用例来自实测：每一条都对应一次真实的失败。
它们跑在项目自己的语料库上，语料不存在时整体跳过 —— 检索质量测试需要
真实数据才能判断，用空库或假数据测出来的结论没有意义。

覆盖的失败类型：
  - `AI 芯片` 返回 0     -> trigram 对 2 字词无能
  - `不存在的词` 返回 3 条 -> trigram 子串误命中
  - `AI` 第 1 名是向量库文档 -> 兜底按长度排序
  - `DeepSeek` 被单一来源霸屏 -> 缺少多样性约束
  - 平台内容日期全是抓取当天 -> UGC 侧硬编码 now()
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core import paths

CORPUS = paths.CORPUS

pytestmark = pytest.mark.skipif(
    not CORPUS.exists(),
    reason="需要真实语料库才能评估检索质量（%s）" % CORPUS,
)


@pytest.fixture(scope="module")
def db():
    """检索逻辑现在在 core/search.py（原根目录的 research.py）。"""
    from core import search as research

    conn = research.connect()
    yield research, conn
    conn.close()


# --------------------------------------------------------------- 基本召回
def test_single_entity_finds_itself(db):
    """专有名词查询，第 1 名必须真的提到它。"""
    research, conn = db
    rows, _ = research.search(conn, "DeepSeek", 5)
    assert rows, "DeepSeek 应该有结果"
    blob = (rows[0]["title"] or "") + (rows[0]["snippet"] or "")
    assert "DeepSeek" in blob


def test_two_char_chinese_term(db):
    """2 字中文词：trigram 查不到，必须走 LIKE 兜底。

    `芯片` 曾经返回 0 条，因为 trigram 最少要 3 个字符。
    """
    research, conn = db
    rows, _ = research.search(conn, "芯片", 5)
    assert rows, "2 字词「芯片」必须有结果"
    hit = any("芯片" in ((r["title"] or "") + (r["snippet"] or "")) for r in rows)
    assert hit, "返回值里必须真的含「芯片」"


def test_two_char_latin_term(db):
    """2 字符英文（AI / VR）同样要走兜底，且第 1 名必须相关。"""
    research, conn = db
    rows, _ = research.search(conn, "AI", 5)
    assert rows
    blob = (rows[0]["title"] or "") + (rows[0]["snippet"] or "")
    assert "AI" in blob or "ai" in blob.lower()


def test_multi_word_short_terms_require_both(db):
    """多个短词必须同时命中，不能任一命中。

    `AI 面试` 曾经第 1 名是 iPhone 新闻，因为只要求含 `AI`。
    """
    research, conn = db
    rows, note = research.search(conn, "AI 面试", 5)
    assert rows, "AI 面试 应该有结果"
    blob = ((rows[0]["title"] or "") + (rows[0]["snippet"] or "")).lower()
    assert "面试" in blob, "第 1 名必须含「面试」，不能只有 AI"


# --------------------------------------------------------------- 假阳性
def test_nonexistent_term_returns_empty(db):
    """查不到的东西必须返回空，不能给一堆不相干的。

    trigram 是子串匹配，`xyz123` 的三元组会撞上别的英文词。
    """
    research, conn = db
    rows, _ = research.search(conn, "不存在的词xyz123", 5)
    assert rows == [], "不存在的词不该有结果，实际返回 %d 条" % len(rows)


def test_every_result_contains_a_query_term(db):
    """结果必须真的含查询词，这是相对于「有结果」更基本的要求。"""
    research, conn = db
    for topic in ("人形机器人", "固态电池", "核聚变"):
        rows, _ = research.search(conn, topic, 8)
        for r in rows:
            blob = (r["title"] or "") + (r["snippet"] or "")
            assert topic in blob or any(
                part in blob for part in topic.split()
            ), "「%s」的结果里出现了不含该词的条目: %s" % (topic, r["title"])


# --------------------------------------------------------------- 排序偏好
def test_prefers_items_with_body(db):
    """带正文的结果必须排在最前 —— 只有标题的条目答不了「说了什么」。

    断言的是「第一名带正文」而不是「前 10 条里 N 条带正文」，因为后者
    会被两个合理的设计压制：
      1. 来源多样性软惩罚（同一编辑部连出多篇深度稿时会被分散）
      2. 语料本身的正文覆盖率只有 15%，短条目客观存在

    实测分数分布验证了正确性：带正文的条目拿到 0.79–0.90，
    只有标题的条目 0.58–0.60，中间有明显断层。
    """
    research, conn = db
    rows, _ = research.search(conn, "人形机器人", 10)
    assert rows, "应该有结果"
    assert (rows[0]["clen"] or 0) >= 400, (
        "第 1 名只有 %d 字正文，排序偏向短文本" % (rows[0]["clen"] or 0))
    with_body = sum(1 for r in rows if (r["clen"] or 0) >= 400)
    assert with_body >= 3, (
        "前 10 条里只有 %d 条带正文" % with_body)


def test_source_diversity(db):
    """前 10 条不该被单一来源霸屏。

    实测 `DeepSeek` 曾经 8/10 来自 qbitai.com。
    """
    research, conn = db
    for topic in ("DeepSeek", "人形机器人", "英伟达"):
        rows, _ = research.search(conn, topic, 10)
        pubs = {(r["publisher"] or "?")[:40] for r in rows}
        assert len(pubs) >= 5, (
            "「%s」前 10 条只来自 %d 个来源，过于集中" % (topic, len(pubs)))


def test_time_spread(db):
    """前 10 条应当跨越多个月份，不是全同一天。

    平台内容曾经全是抓取当天，因为 UGC 侧硬编码 now()。
    """
    research, conn = db
    rows, _ = research.search(conn, "人形机器人", 10)
    dates = {(r["published_at"] or "")[:10] for r in rows if r["published_at"]}
    assert len(dates) >= 3, "前 10 条只有 %d 个不同日期，时间维度塌缩了" % len(dates)


# --------------------------------------------------------------- 混淆
def test_similar_topics_not_confused(db):
    """检索 A 的结果不该被 B 占据。"""
    research, conn = db
    pairs = [
        ("人形机器人", "工业机器人"),
        ("大模型", "小模型"),
        ("固态电池", "锂电池"),
    ]
    for a, b in pairs:
        rows, _ = research.search(conn, a, 10)
        only_a = only_b = 0
        for r in rows:
            blob = (r["title"] or "") + " " + (r["snippet"] or "")
            ha, hb = a in blob, b in blob
            if ha and not hb:
                only_a += 1
            elif hb and not ha:
                only_b += 1
        assert only_b <= only_a, (
            "检索「%s」时，讲「%s」的条目(%d)多于讲「%s」的(%d)"
            % (a, b, only_b, a, only_a))


# --------------------------------------------------------------- 长尾
def test_long_tail_topics(db):
    """冷门话题也该有相关结果。"""
    research, conn = db
    tails = ["光刻机", "碳化硅", "Chiplet", "存算一体"]
    found = 0
    for q in tails:
        rows, _ = research.search(conn, q, 5)
        if rows and any(
            q in ((r["title"] or "") + (r["snippet"] or "")) for r in rows
        ):
            found += 1
    assert found >= 3, "4 个长尾词里只有 %d 个有相关结果" % found


# --------------------------------------------------------------- 数据卫生
def test_no_absurdly_long_bodies(db):
    """正文不该出现整站/整本数据集。

    曾有一条 148 万字的行，含几乎所有关键词，任何查询都命中它。
    """
    conn = db[1]
    worst = conn.execute(
        "SELECT MAX(LENGTH(COALESCE(content,''))) FROM items").fetchone()[0]
    assert worst <= 200000, "最长正文 %d 字，说明抓取把整站内容存了进来" % worst


def test_titles_are_titles(db):
    """标题字段不该塞进整段 JSON。"""
    conn = db[1]
    worst = conn.execute(
        "SELECT MAX(LENGTH(COALESCE(title,''))) FROM items").fetchone()[0]
    assert worst <= 1000, "最长标题 %d 字，采集阶段把响应体当成了标题" % worst


def test_no_title_carries_raw_markup(db):
    """展示用的标题不能带未解码的标记。

    三种形态，都在本仓库的库里出现过：

    - `\\u003cem\\u003e大模型\\u003c/em\\u003e`：头条把高亮标记**转义在
      JSON 字符串内部**返回，`json.loads` 之后仍然是字面量反斜杠，
      标题清洗没处理这种形态（实测库里 114 条）。
    - `&amp;`：实体没解码（实测库里 167 条）。
    - `<em>`：真标签，一直有处理，保留用于回归。

    这不只是观感问题。排序里「标题命中」是一个正交加分项，而
    `"\\u003cem\\u003e大模型\\u003c/em\\u003e"` 里确实含 `大模型`，
    所以它照样拿分 —— 真正被破坏的是那批**同一条内容**因为清洗后
    指纹不同而算成两条，进而被当成「多家独立报道」。
    """
    conn = db[1]
    offenders = []
    for pattern, label in (
        (r"%\u003c%", "escaped tag"),
        (r"%&amp;%", "entity"),
        (r"%<em>%", "raw tag"),
        (r"%</em>%", "raw closing tag"),
    ):
        row = conn.execute(
            "SELECT COUNT(*) FROM items WHERE title LIKE ?", (pattern,)
        ).fetchone()
        if row[0]:
            offenders.append("%s: %d rows" % (label, row[0]))
    assert not offenders, (
        "corpus titles still carry raw markup (re-run "
        "tools/clean_titles.py --apply): %s" % "; ".join(offenders)
    )

"""Tests for the persistent evidence corpus (SimHash + SQLite store)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.corpus.simhash import (
    cluster_pairs,
    feature_hash64,
    fingerprint,
    hamming,
    simhash,
    tokenize,
)
from src.corpus.store import Corpus, _as_signed64
from src.models import ContentItem, SourceType

NOW = datetime(2026, 9, 24, 12, 0, 0, tzinfo=timezone.utc)


def make_item(
    idx: str,
    title: str = "A title",
    content: str = "Some body text about testing.",
    source: SourceType = SourceType.V2EX,
) -> ContentItem:
    return ContentItem(
        id=f"corpus-test:{idx}",
        source_type=source,
        title=title,
        url=f"https://example.com/{idx}",
        content=content,
        author="tester",
        published_at=NOW,
        fetched_at=NOW,
        metadata={"score": 1, "tags": ["a", "b"], "when": NOW},
    )


# ------------------------------------------------------------------ simhash
def test_feature_hash_is_stable_and_64bit() -> None:
    a = feature_hash64("hello")
    assert a == feature_hash64("hello")
    assert 0 <= a < 2**64
    assert a != feature_hash64("world")


def test_tokenize_mixed_scripts() -> None:
    feats = tokenize("Rust 语言 very nice, rust-lang!")
    assert feats["rust"] == 1
    assert feats["rust-lang"] == 1  # hyphenated tokens kept whole
    assert "语言" in feats  # CJK bigram
    assert sum(feats.values()) >= 4


def test_identical_text_zero_distance_small_edit_low_unrelated_far() -> None:
    base = "Bilibili 宣布将支持 8K 视频上传，创作者表示欢迎。" * 4
    copy = "Bilibili 宣布将支持 8K 视频上传,创作者表示欢迎。" * 4  # comma variant
    unrelated = "Quarterly earnings beat analyst expectations across every segment."

    fp_base = fingerprint(base)
    assert hamming(fp_base, fingerprint(base)) == 0
    assert hamming(fp_base, fingerprint(copy)) <= 6
    assert hamming(fp_base, fingerprint(unrelated)) > 10


def test_simhash_empty_features_is_zero() -> None:
    assert simhash({}) == 0


def test_cluster_pairs_groups_near_dups_only() -> None:
    a = fingerprint("deepseek releases new open weights model for coding agents " * 3)
    same = fingerprint("deepseek releases new open weights model for coding agents! " * 3)
    other = fingerprint("国内油价上调，车主加油成本明显增加，物流行业受到影响。" * 3)
    pairs = {frozenset(p) for p in cluster_pairs([("a", a), ("b", same), ("c", other)], max_distance=6)}
    assert frozenset({"a", "b"}) in pairs  # case/punctuation variant clusters
    assert frozenset({"a", "c"}) not in pairs
    assert frozenset({"b", "c"}) not in pairs


# --------------------------------------------------------------------- store
@pytest.fixture()
def corpus(tmp_path):
    c = Corpus(tmp_path / "corpus" / "evidence.db")
    yield c
    c.close()


def test_add_and_dedup_items(corpus: Corpus) -> None:
    items = [make_item("1"), make_item("2", title="Rust 1.83 released", content="rust changelog details")]
    assert corpus.add_items(items) == 2
    # re-adding same ids is a no-op (append-only dedup by natural id)
    assert corpus.add_items(items) == 0
    assert corpus.stats()["items"] == 2


def test_run_accounting(corpus: Corpus) -> None:
    since = NOW - timedelta(hours=24)
    run_id = corpus.begin_run(since)
    new = corpus.add_items([make_item("r1"), make_item("r2")], run_id=run_id)
    corpus.finish_run(run_id, items_new=new, items_total_seen=3)
    stats = corpus.stats()
    assert stats["runs"] == 1
    assert stats["items"] == 2
    row = corpus._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    assert row["items_new"] == 2
    assert row["items_total_seen"] == 3
    assert row["finished_at"] is not None


def test_fts_search_english_and_chinese(corpus: Corpus) -> None:
    run_id = corpus.begin_run(NOW - timedelta(hours=1))
    corpus.add_items(
        [
            make_item("en", title="Rust async runtime comparison", content="tokio vs async-std benchmarks"),
            make_item("zh", title="B站开放 8K 视频", content="创作者表示上传速度明显提升"),
            make_item("other", title="Garden ideas", content="tomatoes and basil"),
        ],
        run_id=run_id,
    )
    hits = corpus.search("async runtime")  # trigram tokenizer: ≥3 chars per term
    assert hits and hits[0]["title"].startswith("Rust")
    zh = corpus.search("创作者")  # CJK: trigram path
    assert zh and zh[0]["id"] == "corpus-test:zh"
    # 2-char CJK words fall back to LIKE scan (trigram needs ≥3 chars)
    zh2 = corpus.search("视频")
    assert zh2 and zh2[0]["id"] == "corpus-test:zh"
    # metadata JSON round-trips with datetime serialised
    assert zh[0]["metadata"]["when"].startswith("2026-09-24")
    assert corpus.search("no-such-token-xyz") == []


def test_search_short_cjk_term_falls_back_to_like(corpus: Corpus) -> None:
    # 2-char Chinese words (工作/注册) can't be matched by the trigram tokenizer
    corpus.add_items(
        [
            make_item("job", title="老婆不想换工作", content="想换个轻松一点的岗位"),
            make_item("reg", title="Muse 注册新方法", content="免费领取十亿 token"),
        ]
    )
    hits = corpus.search("工作")
    assert [h["id"] for h in hits] == ["corpus-test:job"]
    reg = corpus.search("注册")
    assert any(h["id"] == "corpus-test:reg" for h in reg)


def test_search_sanitises_fts_operators(corpus: Corpus) -> None:
    corpus.add_items([make_item("en", title="rust runtime", content="async tokio benchmark")])
    # a bare NOT/OR/hyphen must be treated as literal text, not FTS syntax
    assert corpus.search("no-such-token") == []
    assert corpus.search("NOT") == []
    assert corpus.search("runtime OR nonexistentterm") == []


def test_recent_filters_by_source(corpus: Corpus) -> None:
    corpus.add_items(
        [
            make_item("v1", source=SourceType.V2EX),
            make_item("b1", source=SourceType.BILIBILI),
        ]
    )
    assert {i["id"] for i in corpus.recent()} == {"corpus-test:v1", "corpus-test:b1"}
    assert [i["id"] for i in corpus.recent(source_type="bilibili")] == ["corpus-test:b1"]


def test_clusters_group_syndicated_copy(corpus: Corpus) -> None:
    # ~article-length bodies, as stored in practice (short docs swing wider)
    original = (
        "OpenAI 今天正式发布了新一代模型，上下文窗口扩展到两百万 token。"
        "发布会上演示了模型在复杂代码库上的表现，称其可以独立完成大型工程任务。"
        "开发者社区反响热烈，多位工程师在论坛发帖表示长文本能力将改变工作流。"
    )
    syndicated = original.replace("反响热烈", "反响强烈")  # tiny reword
    unrelated = (
        "Discourse 官方论坛宣布升级反垃圾机制，引入新的信任等级与自动化审核规则。"
        "社区经理表示此举旨在提高高质量讨论占比，减少垃圾账号带来的干扰。"
        "多位版主分享了新版本下的 moderation 数据与社区治理经验。"
    )
    corpus.add_items(
        [
            make_item("o", title="新闻", content=original),
            make_item("s", title="转载", content=syndicated),
            make_item("u", title="论坛", content=unrelated),
        ]
    )
    changed = corpus.recompute_clusters(max_distance=6)
    assert changed >= 1
    rows = {r["id"]: r["cluster_id"] for r in corpus.recent()}
    assert rows["corpus-test:o"] == rows["corpus-test:s"]
    assert rows["corpus-test:o"] != rows["corpus-test:u"]
    assert corpus.stats()["clusters"] == 2


def test_fingerprint_column_stays_signed_int64(corpus: Corpus) -> None:
    assert _as_signed64(2**64 - 1) == -1  # worst case unsigned value
    assert _as_signed64(5) == 5
    corpus.add_items([make_item("fp")])
    stored = corpus._conn.execute("SELECT fingerprint FROM items").fetchone()[0]
    assert isinstance(stored, int)
    assert -2**63 <= stored <= 2**63 - 1


def test_corpus_reopens_idempotent(tmp_path) -> None:
    path = tmp_path / "c.db"
    c1 = Corpus(path)
    c1.add_items([make_item("x")])
    c1.close()
    c2 = Corpus(path)  # schema creation must be IF NOT EXISTS-safe
    assert c2.stats()["items"] == 1
    c2.close()

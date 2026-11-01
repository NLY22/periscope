"""Tests for deep search enhancements: cross-lingual bridge, hydration metadata, and authority rerank."""
from __future__ import annotations

import pytest

from periscope.search.query import enhance_query, translate_to_en, ENGLISH_SOURCES
from periscope.search.merge import score_of
from periscope.search.hydrate import _extract_meta


def test_cross_lingual_translation_terms():
    # 测试机器人与具身智能
    en1 = translate_to_en("人形机器人灵巧手最新进展")
    assert "humanoid robot" in en1
    assert "dexterous hand" in en1

    # 测试大模型与智能体
    en2 = translate_to_en("大模型智能体评测与对齐")
    assert "large language model" in en2
    assert "AI agent" in en2
    assert "benchmark" in en2

    # 测试系统级 Rust 术语
    en3 = translate_to_en("Rust生命周期与内存安全")
    assert "Rust" in en3
    assert "lifetime" in en3
    assert "memory safety" in en3


def test_merge_score_boosts_depth_and_academic_domains():
    base_row = {"title": "普通新闻", "url": "https://news.example.com/1", "authority": 2, "trust": 0.5}
    base_score = score_of(base_row)

    # 包含长正文
    long_body_row = {
        "title": "深度分析文章",
        "url": "https://news.example.com/2",
        "authority": 2,
        "trust": 0.5,
        "body": "a" * 1200,
    }
    long_score = score_of(long_body_row)
    assert long_score > base_score

    # 包含权威学术域名
    academic_row = {
        "title": "ArXiv 学术论文",
        "url": "https://arxiv.org/abs/2610.12345",
        "authority": 2,
        "trust": 0.5,
        "body": "a" * 1200,
    }
    academic_score = score_of(academic_row)
    assert academic_score > long_score


def test_hydrate_extracts_metadata():
    html_sample = """
    <html>
      <head>
        <meta property="article:published_time" content="2026-10-28T14:30:00Z" />
        <meta name="author" content="Tech Author" />
      </head>
      <body><p>Sample content</p></body>
    </html>
    """
    meta = _extract_meta(html_sample)
    assert meta.get("date") == "2026-10-28"
    assert meta.get("author") == "Tech Author"

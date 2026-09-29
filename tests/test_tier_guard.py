"""P0 acceptance: crowd text must never reach the claimable layer.

The first test in here fails on `main` — that is the point. reddit,
hackernews and twitter all append "--- Top Comments ---", which is not one of
the five Chinese markers corpus/sections.py looks for, so their comments are
being graded as if the author wrote them.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.analysis.claims import Claim, ClaimAnalyzer, ClaimStore
from src.corpus.sections import claimable_of
from src.corpus.store import Corpus
from src.models import DiscourseConfig, HackerNewsConfig, Section, SourceType
from src.scrapers.hackernews import HackerNewsScraper

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
STORY_ID = 999


def _hn_handler(with_text: bool = False):
    story = {
        "id": STORY_ID,
        "type": "story",
        "by": "submitter",
        "time": int(NOW.timestamp()),
        "title": "Show HN: a new parser",
        "url": "https://example.com/parser",
        "score": 400,
        "descendants": 3,
        "kids": [11, 12, 13],
    }
    if with_text:
        story.pop("url")
        story["title"] = "Ask HN: which parser"
        story["text"] = "I need a parser that survives malformed input."
    comments = {
        11: {"id": 11, "by": "stranger_a", "text": "the benchmark is rigged"},
        12: {"id": 12, "by": "stranger_b", "text": "no it isnt, here is data"},
        13: {"id": 13, "by": "stranger_c", "text": "<p>deleted account spam</p>"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/topstories.json"):
            return httpx.Response(200, json=[STORY_ID])
        if "/item/" in path:
            raw = int(path.rsplit("/", 1)[1].split(".")[0])
            if raw == STORY_ID:
                return httpx.Response(200, json=story)
            return httpx.Response(200, json=comments[raw])
        raise AssertionError(f"unexpected {request.url}")

    return handler


def _fetch_hn_item(with_text: bool = False):
    client = httpx.AsyncClient(transport=httpx.MockTransport(_hn_handler(with_text)))
    scraper = HackerNewsScraper(HackerNewsConfig(enabled=True, min_score=1), client)
    items = asyncio.run(scraper.fetch(NOW.replace(hour=0)))
    asyncio.run(client.aclose())
    assert len(items) == 1
    return items[0]


def test_hn_link_post_comments_are_not_claimable() -> None:
    item = _fetch_hn_item()
    claimable = claimable_of(item)
    assert "rigged" not in claimable
    assert "stranger_a" not in claimable
    assert claimable == ""          # a link post has no author-written body


def test_hn_comments_become_community_sections_with_locators() -> None:
    item = _fetch_hn_item()
    assert [s.tier for s in item.sections] == ["community"] * 3
    assert [s.locator for s in item.sections] == ["#11", "#12", "#13"]
    assert [s.author for s in item.sections] == ["stranger_a", "stranger_b", "stranger_c"]


def test_hn_comments_stay_visible_in_content_for_the_panel() -> None:
    item = _fetch_hn_item()
    assert "the benchmark is rigged" in item.content
    assert "- @stranger_a:" in item.content


def test_hn_text_post_keeps_the_author_body_claimable() -> None:
    item = _fetch_hn_item(with_text=True)
    assert claimable_of(item) == "I need a parser that survives malformed input."
    assert item.sections[0].tier == "primary"


@pytest.fixture()
def corpus(tmp_path: Path) -> Corpus:
    return Corpus(tmp_path / "corpus.db")


def test_hn_comments_never_enter_claim_fts(corpus: Corpus) -> None:
    corpus.add_items([_fetch_hn_item()])
    assert corpus.search("rigged", limit=5, tier="claimable") == []
    assert corpus.search("rigged", limit=5) != []      # panel keeps full recall


def test_hn_comments_do_not_inflate_independent_sources(corpus: Corpus) -> None:
    item = _fetch_hn_item()
    corpus.add_items([item])
    analyzer = ClaimAnalyzer(
        store=ClaimStore(corpus), corpus=corpus, client=None, claimable_only=True
    )
    claim = Claim(id="claim:hn:1", item_id=item.id, text="the benchmark is rigged")
    analyzer.store.upsert_claims([claim])
    analyzer.link_evidence(claim)
    analyzer.store.recompute_independence()
    stored = analyzer.store.get_claim(claim.id)
    linked = corpus._conn.execute(
        "SELECT COUNT(*) FROM claim_evidence WHERE claim_id=?", (claim.id,)
    ).fetchone()[0]
    assert linked <= 1                      # the origin item only, no crowd votes
    assert stored is not None and stored.independent_sources <= 1


# ------------------------------------------------------------------- discourse
def _discourse_item():
    from src.scrapers.discourse import DiscourseScraper

    latest = {"topic_list": {"topics": [{
        "id": 7, "fancy_title": "为什么我的构建失败了", "slug": "why-build-fails",
        "created_at": "2026-09-29T09:00:00.000Z", "posts_count": 3,
        "views": 250, "like_count": 12, "tags": ["help"], "excerpt": "列表页摘要",
    }]}}
    thread = {"post_stream": {"posts": [
        {"username": "asker", "post_number": 1,
         "cooked": "<p>编译器报 <code>error[E0502]</code></p>"},
        {"username": "helper", "post_number": 2, "cooked": "<p>借用冲突，试试 clone</p>"},
        {"username": "asker", "post_number": 3, "cooked": "<p>解决了，谢谢</p>"},
    ]}}

    async def _get(url, params=None, **kwargs):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = latest if url.endswith("/latest.json") else thread
        return response

    client = AsyncMock()
    client.get.side_effect = _get
    config = DiscourseConfig(enabled=True, sites=[{"base_url": "https://forum.test"}],
                             fetch_replies=5)
    items = asyncio.run(DiscourseScraper(config, client).fetch(NOW.replace(hour=0)))
    assert len(items) == 1
    return items[0]


def test_discourse_floors_become_community_sections_with_post_number_locators() -> None:
    item = _discourse_item()
    assert [s.tier for s in item.sections] == ["primary", "community", "community"]
    assert [s.locator for s in item.sections] == ["#1", "#2", "#3"]
    assert [s.author for s in item.sections] == ["asker", "helper", "asker"]


def test_discourse_floor_text_is_not_claimable() -> None:
    claimable = claimable_of(_discourse_item())
    assert "error[E0502]" in claimable
    assert "借用冲突" not in claimable


# ------------------------------------------------- no scraper may emit markers
def test_no_scraper_emits_a_tier_marker_any_more() -> None:
    """Guard: a seventh emitter must not be able to grow quietly.

    Tiering by string convention is what let three sources leak. This test
    fails the moment any scraper concatenates a marker again — Chinese or
    English — instead of declaring a Section.
    """
    import pathlib
    import re

    forbidden = re.compile(r"【[^】]{2,12}】|--- Top Comments ---")
    offenders = []
    for path in sorted(pathlib.Path("src/scrapers").glob("*.py")):
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            stripped = line.strip()
            # Comments and docstring lines may discuss the old markers; only
            # code that concatenates one into a body can reintroduce the leak.
            if stripped.startswith(("#", '"', "'")):
                continue
            if forbidden.search(line):
                offenders.append(f"{path}:{number}: {stripped[:80]}")
    assert offenders == []


# --------------------------------------------------------------------- reddit
def _reddit_item():
    """Call the parsing seam directly: reddit's fetch path tries old.reddit.com
    for the listing first, and driving that transport adds nothing to a tiering
    assertion."""
    from src.models import RedditConfig
    from src.scrapers.reddit import RedditScraper

    post = {
        "id": "abc123", "name": "t3_abc123", "title": "A claim about parsers",
        "selftext": "The author asserts the parser is linear time.",
        "is_self": True, "author": "op_user", "score": 90, "num_comments": 2,
        "created_utc": int(NOW.timestamp()), "subreddit": "python",
        "permalink": "/r/python/comments/abc123/a_claim_about_parsers/",
        "url": "https://example.com/post",
    }
    comments = [
        {"id": "c1", "author": "stranger_a", "score": 42,
         "body": "totally fake, never happened"},
        {"id": "c2", "author": "stranger_b", "score": 7, "body": "source is a lie"},
    ]

    scraper = RedditScraper(RedditConfig(enabled=True, fetch_comments=2), None)
    return scraper._parse_post(post, comments, "subreddit")


def test_reddit_comments_are_not_claimable() -> None:
    claimable = claimable_of(_reddit_item())
    assert "The author asserts" in claimable
    assert "never happened" not in claimable
    assert "stranger_a" not in claimable


def test_reddit_comments_become_community_sections_keeping_their_score() -> None:
    community = [s for s in _reddit_item().sections if s.tier == "community"]
    assert {s.author for s in community} == {"stranger_a", "stranger_b"}
    assert all(s.locator and s.locator.startswith("#") for s in community)
    assert {s.meta.get("score") for s in community} == {42, 7}


def test_reddit_no_longer_emits_the_english_marker() -> None:
    assert "--- Top Comments ---" not in (_reddit_item().content or "")


# -------------------------------------------------------------------- twitter
def _tweet(content="the author's own tweet"):
    from src.models import ContentItem

    return ContentItem(
        id="twitter:tweet:42", source_type=SourceType.TWITTER, title="t",
        url="https://twitter.com/x/status/42", content=content, author="x",
        published_at=NOW, fetched_at=NOW, metadata={},
    )


def test_twitter_replies_are_not_claimable() -> None:
    from src.scrapers.twitter import TwitterScraper

    item = _tweet()
    sections = [Section(tier="community", text="reply text", author="alice",
                        locator="@alice/1", meta={"likes": 5})]
    assert TwitterScraper.append_discussion_sections(item, sections) is True
    assert claimable_of(item) == "the author's own tweet"
    assert "reply text" in item.content

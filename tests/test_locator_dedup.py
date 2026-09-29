"""Dedup keys work for URL locators and for non-URL locators alike."""

from datetime import datetime, timezone

from src.corpus.sections import claimable_of
from src.models import ContentItem, Section, SourceType
from src.orchestrator import HorizonOrchestrator, _deduplication_item_key

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def make(idx: str, **overrides) -> ContentItem:
    base = dict(
        id=f"d:{idx}",
        source_type=SourceType.RSS,
        title="t",
        url=f"https://example.com/{idx}",
        content="body",
        published_at=NOW,
        fetched_at=NOW,
    )
    base.update(overrides)
    return ContentItem(**base)


def bare_orchestrator() -> HorizonOrchestrator:
    """No __init__: the merge helper touches no instance state."""
    return HorizonOrchestrator.__new__(HorizonOrchestrator)


def test_url_locators_keep_the_existing_normalisation() -> None:
    a = make("a", url="HTTPS://Example.COM:443/story/?utm_source=x")
    b = make("b", url="https://example.com/story")
    assert _deduplication_item_key(a) == _deduplication_item_key(b)


def test_non_url_locators_compare_by_exact_value() -> None:
    a = make("a", url=None, locator="tieba:p/123")
    b = make("b", url=None, locator="tieba:p/124")
    c = make("c", url=None, locator="tieba:p/123")
    assert _deduplication_item_key(a) != _deduplication_item_key(b)
    assert _deduplication_item_key(a) == _deduplication_item_key(c)


def test_non_url_and_url_keys_never_collide() -> None:
    assert _deduplication_item_key(make("a", url=None, locator="x")) != \
        _deduplication_item_key(make("b", url="https://example.com/x"))


def test_merging_section_bearing_items_keeps_sections_and_content_in_sync() -> None:
    primary = make("p", url="https://example.com/story", sections=[
        Section(tier="primary", text="author of the first copy"),
    ])
    duplicate = make(
        "q", url="https://example.com/story", source_type=SourceType.HACKERNEWS,
        sections=[
            Section(tier="primary", text="author of the second copy"),
            Section(tier="community", text="a stranger", author="s"),
        ],
    )
    merged = bare_orchestrator().merge_cross_source_duplicates([primary, duplicate])
    assert len(merged) == 1
    out = merged[0]
    assert len(out.sections) == 3
    assert "- @s: a stranger" in out.content
    assert out.metadata["merged_sources"] == ["rss", "hackernews"]


def test_merging_items_without_sections_keeps_the_old_string_concatenation() -> None:
    primary = make("p", url="https://example.com/story", content="the richer primary content")
    duplicate = make("q", url="https://example.com/story",
                     source_type=SourceType.HACKERNEWS, content="short")
    merged = bare_orchestrator().merge_cross_source_duplicates([primary, duplicate])
    assert "--- From hackernews ---" in merged[0].content
    assert merged[0].sections == []


def test_merged_community_text_never_reaches_claimable() -> None:
    primary = make("p", url="https://example.com/story",
                   sections=[Section(tier="primary", text="author words")])
    duplicate = make("q", url="https://example.com/story",
                     source_type=SourceType.HACKERNEWS,
                     sections=[Section(tier="community", text="stranger words", author="s")])
    out = bare_orchestrator().merge_cross_source_duplicates([primary, duplicate])[0]
    assert claimable_of(out) == "author words"


LONG_AUTHOR = "the author's long original body text, which wins the richest-content merge"


def test_merging_a_marker_item_into_a_section_item_tiers_the_incoming_text() -> None:
    primary = make("p", url="https://example.com/story",
                   sections=[Section(tier="primary", text=LONG_AUTHOR)])
    duplicate = make("q", url="https://example.com/story",
                     source_type=SourceType.HACKERNEWS,
                     content="brief\n\n【评论区 Top】\ncrowd")
    out = bare_orchestrator().merge_cross_source_duplicates([primary, duplicate])[0]
    assert claimable_of(out) == f"{LONG_AUTHOR}\n\nbrief"
    assert "crowd" not in claimable_of(out)
    assert all(s.provenance == "legacy_marker" for s in out.sections[1:])


def test_merging_cannot_rescue_an_unreadable_marker_from_the_legacy_path() -> None:
    """Documents the limit that Tasks 10-12 exist to remove.

    `split_sections` knows five Chinese markers only, so an item concatenated
    with "--- Top Comments ---" comes back as one primary block no matter how
    carefully it is merged. Tiering has to be declared by the scraper; no
    amount of downstream string handling can recover it.
    """
    primary = make("p", url="https://example.com/story",
                   sections=[Section(tier="primary", text=LONG_AUTHOR)])
    duplicate = make("q", url="https://example.com/story",
                     source_type=SourceType.HACKERNEWS,
                     content="brief\n\n--- Top Comments ---\ncrowd")
    out = bare_orchestrator().merge_cross_source_duplicates([primary, duplicate])[0]
    assert len(out.sections) == 2
    assert "crowd" in claimable_of(out)

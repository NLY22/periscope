"""One entry point decides what is author-written, whichever path produced it."""

from datetime import datetime, timezone

from src.corpus.sections import (
    MarkerSection,
    claimable_from_sections,
    claimable_of,
    marker_sections_to_model,
    split_sections,
)
from src.models import ContentItem, Section, SourceType

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def make(content=None, sections=None) -> ContentItem:
    return ContentItem(
        id="d:1",
        source_type=SourceType.V2EX,
        title="t",
        url="https://e.com/1",
        content=content,
        sections=sections or [],
        published_at=NOW,
        fetched_at=NOW,
    )


def test_sections_win_over_the_marker_path() -> None:
    item = make(sections=[
        Section(tier="primary", text="the author says X"),
        Section(tier="community", text="a stranger says Y", author="s"),
    ])
    assert claimable_of(item) == "the author says X"


def test_items_without_sections_fall_back_to_markers() -> None:
    item = make(content="author body\n\n【评论区 Top】\ncrowd body")
    assert claimable_of(item) == "author body"


def test_unasserted_primary_sections_are_not_claimable() -> None:
    item = make(sections=[
        Section(tier="primary", text="caption of an image", provenance="vlm", asserted=False),
        Section(tier="primary", text="the author's own words"),
    ])
    assert claimable_of(item) == "the author's own words"


def test_claimable_from_sections_joins_in_document_order() -> None:
    assert claimable_from_sections([
        Section(tier="community", text="c"),
        Section(tier="primary", text="p1"),
        Section(tier="primary", text="p2"),
    ]) == "p1\n\np2"


def test_marker_conversion_tags_tiers_and_flags_provenance() -> None:
    converted = marker_sections_to_model("author body\n\n【评论区 Top】\ncrowd body")
    assert [(s.tier, s.provenance) for s in converted] == [
        ("primary", "legacy_marker"),
        ("community", "legacy_marker"),
    ]
    assert converted[1].locator == "【评论区 Top】"


def test_marker_conversion_of_empty_content_is_empty() -> None:
    assert marker_sections_to_model(None) == []
    assert marker_sections_to_model("   ") == []


def test_legacy_dataclass_keeps_its_marker_attribute() -> None:
    sections = split_sections("a\n\n【楼层讨论】\nb")
    assert isinstance(sections[1], MarkerSection)
    assert sections[1].marker == "【楼层讨论】"

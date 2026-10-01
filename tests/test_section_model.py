"""ContentItem carries typed sections; identity and time are normalised once."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.models import ContentItem, Section, SourceType, sections_to_content

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)


def make(**overrides):
    base = dict(
        id="t:1",
        source_type=SourceType.DISCOURSE,
        title="Why does my build fail",
        url="https://forum.test/t/7",
        published_at=NOW,
        fetched_at=NOW,
    )
    base.update(overrides)
    return ContentItem(**base)


def test_sections_default_to_empty_and_content_is_untouched() -> None:
    item = make(content="author body")
    assert item.sections == []
    assert item.content == "author body"


def test_locator_defaults_to_the_url() -> None:
    assert make().locator == "https://forum.test/t/7"


def test_locator_may_be_a_non_url_string() -> None:
    item = make(url=None, locator="tieba:p/123")
    assert item.locator == "tieba:p/123"
    assert item.url is None


def test_item_with_neither_url_nor_locator_is_rejected() -> None:
    with pytest.raises(ValidationError):
        make(url=None)


def test_content_is_recomputed_from_sections() -> None:
    item = make(sections=[
        Section(tier="primary", text="author body"),
        Section(tier="community", text="borrow conflict", author="helper", locator="#2"),
    ])
    assert item.content == "author body\n\n- @helper: borrow conflict"


def test_sections_without_author_render_bare_text() -> None:
    assert sections_to_content([Section(tier="community", text="anon reply")]) == "anon reply"


def test_blank_sections_are_dropped_from_content() -> None:
    assert sections_to_content([Section(tier="primary", text="  ")]) == ""


def test_missing_published_at_falls_back_to_fetch_time_and_flags_time_basis() -> None:
    item = make(published_at=None, fetched_at=NOW)
    assert item.published_at == NOW
    assert item.time_basis == "unknown"


def test_explicit_time_basis_is_preserved() -> None:
    assert make(published_at=None, time_basis="crawled").time_basis == "crawled"


def test_citation_url_prefers_url_then_locator() -> None:
    assert make().citation_url == "https://forum.test/t/7"
    assert make(url=None, locator="xhs:note:abc").citation_url == "xhs:note:abc"


def test_rebuild_content_picks_up_in_place_section_changes() -> None:
    item = make(sections=[Section(tier="primary", text="one")])
    item.sections.append(Section(tier="community", text="two", author="b"))
    assert item.content == "one"  # plain assignment does not revalidate
    item.rebuild_content()
    assert item.content == "one\n\n- @b: two"


def test_extra_fields_are_still_forbidden() -> None:
    with pytest.raises(ValidationError):
        make(nonsense=1)


def test_section_rejects_an_unknown_tier() -> None:
    with pytest.raises(ValidationError):
        Section(tier="crowd", text="x")

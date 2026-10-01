from src.processing.content import select_content, split_content


def test_split_content_separates_appended_comments() -> None:
    parts = split_content("Article body\n\n--- Top Comments ---\n\nUseful reply")

    assert parts.main == "Article body"
    assert parts.comments == "Useful reply"


def test_prefix_sampling_preserves_existing_behavior() -> None:
    content = "0123456789" * 100

    assert select_content(content, 500, "prefix") == content[:500]


def test_head_middle_tail_sampling_preserves_article_ending() -> None:
    content = "A" * 1000 + "MIDDLE-MARKER" + "B" * 1000 + "ENDING-MARKER"

    selected = select_content(content, 600, "head-middle-tail")

    assert len(selected) <= 600
    assert selected.startswith("[Opening excerpt]")
    assert "MIDDLE-MARKER" in selected
    assert selected.endswith("ENDING-MARKER")


def _item(**overrides):
    from datetime import datetime, timezone

    from src.models import ContentItem, SourceType

    now = datetime(2026, 9, 29, tzinfo=timezone.utc)
    base = dict(
        id="c:1", source_type=SourceType.HACKERNEWS, title="t",
        url="https://e.com/1", published_at=now, fetched_at=now,
    )
    base.update(overrides)
    return ContentItem(**base)


def test_split_item_content_uses_sections_when_present() -> None:
    from src.models import Section
    from src.processing.content import split_item_content

    item = _item(sections=[
        Section(tier="primary", text="Article body"),
        Section(tier="community", text="Useful reply", author="stranger"),
    ])
    parts = split_item_content(item)
    assert parts.main == "Article body"
    assert parts.comments == "- @stranger: Useful reply"


def test_split_item_content_falls_back_to_the_marker_path() -> None:
    from src.processing.content import split_item_content

    item = _item(
        id="c:2", url="https://e.com/2",
        content="Article body\n\n--- Top Comments ---\n\nLegacy reply",
    )
    parts = split_item_content(item)
    assert parts.main == "Article body"
    assert parts.comments == "Legacy reply"


def test_split_item_content_of_a_comment_only_item_has_no_main() -> None:
    """The HN link post: nothing the author wrote, so nothing to summarise."""
    from src.models import Section
    from src.processing.content import split_item_content

    item = _item(
        id="c:3", url="https://e.com/3",
        sections=[Section(tier="community", text="stranger talk", author="s")],
    )
    assert split_item_content(item).main == ""

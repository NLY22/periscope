"""The source registry is the single place a source is declared.

Guard test A: SOURCE_SPECS <-> SourceType <-> SourcesConfig.
Guard test B: SOURCE_SPECS <-> SCRAPER_BINDINGS, plus the end-to-end proof
that the registry-driven fetch loop reaches every source under its label.
"""

import pytest

from src.models import SOURCE_REGISTRY, SOURCE_SPECS, SourcesConfig, SourceType

SPEC_KEYS = {spec.key for spec in SOURCE_SPECS}

ALL_ENABLED_SOURCES = {
    "github": [{"type": "user_events", "username": "alice"}],
    "hackernews": {"enabled": True},
    "rss": [{"name": "Feed", "url": "https://example.com/feed"}],
    "reddit": {"enabled": True, "subreddits": [{"subreddit": "python"}]},
    "telegram": {"enabled": True, "channels": [{"channel": "updates"}]},
    "twitter": {"enabled": True, "users": ["openai"]},
    "openbb": {"enabled": True, "watchlists": [{"name": "tech", "symbols": ["NVDA"]}]},
    "ossinsight": {"enabled": True},
    "gdelt": {"enabled": True},
    "google_news": {"enabled": True},
    "bilibili": {"enabled": True},
    "v2ex": {"enabled": True},
    "discourse": {"enabled": True, "sites": [{"base_url": "https://forum.test"}]},
    "youtube": {"enabled": True, "channels": [{"name": "c", "channel_id": "UCx"}]},
}


def all_enabled_config():
    from src.models import Config

    return Config.model_validate({
        "ai": {"provider": "openai", "model": "test", "api_key_env": "KEY"},
        "sources": ALL_ENABLED_SOURCES,
    })


def unwrap_model(annotation):
    """`Optional[X]` / `List[X]` -> X, so field introspection has a target."""
    for arg in getattr(annotation, "__args__", ()) or ():
        if arg is not type(None):
            return unwrap_model(arg)
    return annotation


def test_every_source_type_has_exactly_one_spec() -> None:
    assert SPEC_KEYS == {member.value for member in SourceType}
    assert len(SPEC_KEYS) == len(SOURCE_SPECS)


def test_source_registry_is_derived_and_unchanged() -> None:
    assert set(SOURCE_REGISTRY) == SPEC_KEYS
    for spec in SOURCE_SPECS:
        definition = SOURCE_REGISTRY[spec.key]
        assert definition.config_field == spec.config_field
        assert definition.config_is_list == spec.config_is_list
        assert definition.item_fields == spec.item_fields


def test_every_spec_points_at_a_real_config_field() -> None:
    fields = set(SourcesConfig.model_fields)
    assert sorted(s.config_field for s in SOURCE_SPECS if s.config_field not in fields) == []


def test_every_config_field_is_claimed_by_a_spec() -> None:
    orphan = sorted(set(SourcesConfig.model_fields) - {s.config_field for s in SOURCE_SPECS})
    assert orphan == []


def test_list_specs_match_their_config_annotation() -> None:
    for spec in SOURCE_SPECS:
        annotation = SourcesConfig.model_fields[spec.config_field].annotation
        assert spec.config_is_list == ("List" in str(annotation)), spec.key


@pytest.mark.parametrize("spec", SOURCE_SPECS, ids=lambda s: s.key)
def test_spec_values_are_in_range(spec) -> None:
    assert 0.0 <= spec.credibility_prior <= 1.0
    assert spec.kind in {"official", "forum", "ugc_social", "aggregator", "search_engine"}
    assert spec.time_basis_default in {"published", "crawled", "unknown"}
    assert spec.label.strip() == spec.label
    if spec.rate_limit is not None:
        assert spec.rate_limit.requests >= 1
        assert spec.rate_limit.per_seconds > 0
        assert 0.0 <= spec.rate_limit.jitter < 1.0


def test_item_fields_exist_on_their_config_model() -> None:
    for spec in SOURCE_SPECS:
        if not spec.item_fields:
            continue
        model = unwrap_model(SourcesConfig.model_fields[spec.config_field].annotation)
        for field_name in spec.item_fields:
            assert field_name in model.model_fields, (spec.key, field_name)


def test_labels_match_the_names_the_fetch_report_already_uses() -> None:
    assert {spec.label for spec in SOURCE_SPECS} == {
        "GitHub", "Hacker News", "RSS Feeds", "Reddit", "Telegram", "Twitter",
        "OpenBB", "OSS Insight", "GDELT", "Google News", "Bilibili", "V2EX",
        "Discourse", "YouTube",
    }


# ------------------------------------------------------------- guard test B
def test_every_spec_has_a_scraper_binding() -> None:
    from src.sources.registry import SCRAPER_BINDINGS

    assert set(SCRAPER_BINDINGS) == SPEC_KEYS


def test_every_binding_builds_with_a_real_config() -> None:
    import asyncio

    import httpx

    from src.scrapers.base import BaseScraper
    from src.sources.registry import SCRAPER_BINDINGS, BuildContext

    sources = all_enabled_config().sources

    async def build_every_one():
        client = httpx.AsyncClient()
        try:
            return [
                SCRAPER_BINDINGS[spec.key](
                    spec, getattr(sources, spec.config_field), client, BuildContext()
                )
                for spec in SOURCE_SPECS
            ]
        finally:
            await client.aclose()

    built = asyncio.run(build_every_one())
    assert len(built) == len(SOURCE_SPECS)
    for spec, scraper in zip(SOURCE_SPECS, built):
        assert isinstance(scraper, BaseScraper), spec.key


def test_build_context_hands_the_throttle_to_the_scraper() -> None:
    import httpx

    from src.models import RateLimit
    from src.scrapers.throttle import Throttle
    from src.sources.registry import SCRAPER_BINDINGS, BuildContext

    spec = next(s for s in SOURCE_SPECS if s.key == "hackernews")
    throttle = Throttle(default=RateLimit(requests=1, per_seconds=2.0, jitter=0.0))
    scraper = SCRAPER_BINDINGS["hackernews"](
        spec, all_enabled_config().sources.hackernews, httpx.AsyncClient(),
        BuildContext(throttle=throttle),
    )
    assert scraper.throttle is throttle


def test_build_throttle_maps_declared_rate_limits_onto_hosts() -> None:
    from src.sources.registry import build_throttle

    throttle = build_throttle(SOURCE_SPECS)
    assert throttle.limit_for("https://api.bilibili.com/x").per_seconds == 2.0
    assert throttle.limit_for("https://api.github.com/") is None  # github has no limit


def test_is_enabled_matches_the_old_hardcoded_predicates() -> None:
    from src.sources.registry import is_enabled

    by_key = {spec.key: spec for spec in SOURCE_SPECS}
    config = SourcesConfig()
    assert is_enabled([], by_key["github"]) is False          # empty list
    assert is_enabled([object()], by_key["github"]) is True    # non-empty list
    assert is_enabled(config.hackernews, by_key["hackernews"]) is True
    assert is_enabled(None, by_key["twitter"]) is False
    assert is_enabled(config.reddit, by_key["reddit"]) is True


def test_importing_the_sources_package_does_not_pull_in_every_scraper() -> None:
    """`src/sources/__init__.py` must stay import-light."""
    import pathlib

    init = pathlib.Path("src/sources/__init__.py").read_text(encoding="utf-8")
    assert "from .registry" not in init
    assert "import registry" not in init


def test_fetch_all_sources_walks_every_enabled_source(monkeypatch) -> None:
    """The one end-to-end proof that the registry loop replaced all 14 ifs.

    Every scraper's fetch is allowed to fail — `_fetch_with_progress` swallows
    exceptions by design — so this asserts the loop *reached* each source under
    its declared label, not that any source returned data.
    """
    import asyncio
    from datetime import datetime, timezone

    import httpx
    from rich.console import Console

    from src.models import Config
    from src.orchestrator import Orchestrator

    config = Config.model_validate({
        "ai": {"provider": "openai", "model": "test", "api_key_env": "KEY"},
        "sources": {
            "github": [{"type": "user_events", "username": "alice"}],
            "hackernews": {"enabled": True},
            "rss": [{"name": "Feed", "url": "https://example.com/feed"}],
            "reddit": {"enabled": True, "subreddits": [{"subreddit": "python"}]},
            "telegram": {"enabled": True, "channels": [{"channel": "updates"}]},
            "twitter": {"enabled": True, "users": ["openai"]},
            "openbb": {"enabled": True, "watchlists": [{"name": "tech", "symbols": ["NVDA"]}]},
            "ossinsight": {"enabled": True},
            "gdelt": {"enabled": True},
            "google_news": {"enabled": True},
            "bilibili": {"enabled": True},
            "v2ex": {"enabled": True},
            "discourse": {"enabled": True, "sites": [{"base_url": "https://forum.test"}]},
            "youtube": {"enabled": True, "channels": [{"name": "c", "channel_id": "UCx"}]},
        },
    })
    config.corpus.enabled = False     # keep persist_to_corpus a no-op
    config.analysis.enabled = False

    orch = Orchestrator.__new__(Orchestrator)
    orch.config = config
    orch.console = Console(record=True, quiet=True)
    orch.icons = {"fetch": "*", "detail": "-"}
    orch.last_fetch_report = None
    orch._corpus = None

    real_client = httpx.AsyncClient

    def offline_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(200, json={})
        )
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", offline_client)

    reached = []
    original = Orchestrator._fetch_with_progress

    async def recording(self, name, scraper, since):
        reached.append(name)
        return await original(self, name, scraper, since)

    monkeypatch.setattr(Orchestrator, "_fetch_with_progress", recording)

    asyncio.run(orch.fetch_all_sources(datetime(2026, 9, 29, tzinfo=timezone.utc)))

    assert sorted(reached) == sorted(spec.label for spec in SOURCE_SPECS)

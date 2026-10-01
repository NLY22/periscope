"""Which scraper serves which declared source.

Lives outside `models.py` because `models.py` may not import scrapers (they
all import models). Two sources do not fit a `key -> class` map, which is why
the values are factories:

  rss      needs a third argument, an ExtractorRegistry built from config
  twitter  picks between two classes on `cfg.mode`, and the Playwright one
           takes no http client at all
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Optional

import httpx

from ..models import SOURCE_SPECS, RateLimit, SourceSpec
from ..scrapers.auth import AuthProvider
from ..scrapers.base import BaseScraper
from ..scrapers.bilibili import BilibiliScraper
from ..scrapers.discourse import DiscourseScraper
from ..scrapers.gdelt import GDELTScraper
from ..scrapers.github import GitHubScraper
from ..scrapers.google_news import GoogleNewsScraper
from ..scrapers.hackernews import HackerNewsScraper
from ..scrapers.openbb import OpenBBScraper
from ..scrapers.ossinsight import OSSInsightScraper
from ..scrapers.reddit import RedditScraper
from ..scrapers.rss import RSSScraper
from ..scrapers.telegram import TelegramScraper
from ..scrapers.throttle import Throttle
from ..scrapers.twitter import TwitterScraper
from ..scrapers.twitter_playwright import TwitterPlaywrightScraper
from ..scrapers.v2ex import V2EXScraper
from ..scrapers.youtube import YouTubeScraper


@dataclass(frozen=True)
class BuildContext:
    """Everything a factory may need beyond the source's own config."""

    extractors: Dict[str, Any] = field(default_factory=dict)
    throttle: Optional[Throttle] = None
    auth: Dict[str, AuthProvider] = field(default_factory=dict)


ScraperFactory = Callable[
    [SourceSpec, Any, Optional[httpx.AsyncClient], BuildContext], Optional[BaseScraper]
]


def _wire(scraper: BaseScraper, spec: SourceSpec, ctx: BuildContext) -> BaseScraper:
    """Hand the shared plumbing to a freshly built scraper.

    Done by attribute rather than constructor argument because the thirteen
    existing subclasses each forward `super().__init__` positionally with their
    own normalised config; making them all accept two more keyword arguments
    would touch every scraper for no behavioural gain. `BaseScraper` already
    defaults these attributes, so replacing them here is the whole wiring.
    """
    if ctx.throttle is not None:
        scraper.throttle = ctx.throttle
    auth = ctx.auth.get(spec.key)
    if auth is not None:
        scraper.auth = auth
    return scraper


def simple(cls: type) -> ScraperFactory:
    """Adapt the common `Cls(config, client)` constructor to a factory."""

    def build(spec, config, client, ctx):
        return _wire(cls(config, client), spec, ctx)

    return build


def _build_rss(spec, config, client, ctx):
    from ..extractors import ExtractorRegistry

    return _wire(RSSScraper(config, client, ExtractorRegistry(ctx.extractors)), spec, ctx)


def _build_twitter(spec, config, client, ctx):
    if getattr(config, "mode", "apify") == "playwright":
        # The Playwright scraper drives a browser instead of the shared
        # client and reads cookies from its own file, so it takes no wiring.
        return TwitterPlaywrightScraper(config)
    return _wire(TwitterScraper(config, client), spec, ctx)


SCRAPER_BINDINGS: Dict[str, ScraperFactory] = {
    "github": simple(GitHubScraper),
    "hackernews": simple(HackerNewsScraper),
    "rss": _build_rss,
    "reddit": simple(RedditScraper),
    "telegram": simple(TelegramScraper),
    "twitter": _build_twitter,
    "openbb": simple(OpenBBScraper),
    "ossinsight": simple(OSSInsightScraper),
    "gdelt": simple(GDELTScraper),
    "google_news": simple(GoogleNewsScraper),
    "bilibili": simple(BilibiliScraper),
    "v2ex": simple(V2EXScraper),
    "discourse": simple(DiscourseScraper),
    "youtube": simple(YouTubeScraper),
}

# Hosts each source actually talks to, so a declared RateLimit can be applied
# per host. Discourse is empty on purpose: its host comes from per-site
# config and is not known until the config is read.
API_HOSTS: Dict[str, tuple] = {
    "github": ("api.github.com",),
    "hackernews": ("hacker-news.firebaseio.com",),
    "reddit": ("oauth.reddit.com", "www.reddit.com", "old.reddit.com"),
    "telegram": ("t.me",),
    "bilibili": ("api.bilibili.com", "www.bilibili.com"),
    "v2ex": ("www.v2ex.com", "global.v2ex.co"),
    "youtube": ("www.youtube.com",),
    "discourse": (),
}


def is_enabled(source_config: Any, spec: SourceSpec) -> bool:
    """The predicate the fourteen hardcoded `if`s used to spell out."""
    if source_config is None:
        return False
    if spec.config_is_list:
        return bool(source_config)
    return bool(getattr(source_config, "enabled", False))


def build_throttle(specs: Iterable[SourceSpec] = SOURCE_SPECS) -> Throttle:
    """One throttle whose per-host limits come from the declared specs."""
    limits: Dict[str, RateLimit] = {}
    for spec in specs:
        if spec.rate_limit is None:
            continue
        for host in API_HOSTS.get(spec.key, ()):
            limits[host] = spec.rate_limit
    return Throttle(default=None, limits=limits)

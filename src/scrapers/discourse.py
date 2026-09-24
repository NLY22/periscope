"""Discourse scraper for any Discourse-powered forum.

Discourse is the open-source engine behind hundreds of large technical
communities (users.rust-lang.org, community.nodebb.org is NodeBB, but
home-assistant, jetson, godot, discourse.meta and many more run on
Discourse). Every instance exposes the same key-less JSON API:

  GET {base}/latest.json          -> topic list
  GET {base}/t/{topic_id}.json    -> post stream (the actual discussion)

Configured with a list of sites, each optionally filtered by tags and
capped by volume, this scraper turns every fresh topic into one
ContentItem whose content is the first post plus the top follow-up
replies — the part no search engine indexes usefully.
"""

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

from ..models import ContentItem, SourceType
from .base import BaseScraper

_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(\.\d+)?Z?$")


def _parse_discourse_time(raw: str) -> Optional[datetime]:
    match = _ISO_RE.match(raw or "")
    if not match:
        return None
    try:
        dt = datetime.strptime(match.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


def _html_to_text(raw: str, limit: int) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)
    if len(text) > limit:
        text = text[:limit] + "…"
    return text


def _field(obj: Any, key: str, default: Any = None) -> Any:
    """Read a config field from either a dict or a pydantic model."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class DiscourseScraper(BaseScraper):
    """Fetch fresh topics and reply threads across Discourse instances."""

    def __init__(self, config: Any, http_client: httpx.AsyncClient):
        super().__init__(
            config if isinstance(config, dict) else {}, http_client
        )
        get = (
            config.get
            if isinstance(config, dict)
            else lambda key, default=None: getattr(config, key, default)
        )
        self.enabled = get("enabled", True)
        self.sites: List[Dict[str, Any]] = list(get("sites", []) or [])
        self.max_topics_per_site = get("max_topics_per_site", 10)
        self.fetch_replies = get("fetch_replies", 5)
        self.post_chars = get("post_chars", 1200)

    async def fetch(self, since: datetime) -> List[ContentItem]:
        if not self.enabled or not self.sites:
            return []
        results = await asyncio.gather(
            *[self._fetch_site(site, since) for site in self.sites],
            return_exceptions=True,
        )
        items: List[ContentItem] = []
        for result in results:
            if isinstance(result, list):
                items.extend(result)
        return items

    async def _fetch_site(
        self, site: Any, since: datetime
    ) -> List[ContentItem]:
        base = str(_field(site, "base_url", "") or "").rstrip("/")
        if not base:
            return []
        if not _field(site, "enabled", True):
            return []
        name = _field(site, "name") or base.split("//")[-1].split("/")[0]
        tags = set(_field(site, "tags") or [])

        listing = await self._get_json(f"{base}/latest.json")
        if not isinstance(listing, dict):
            return []
        topics = listing.get("topic_list", {}).get("topics", [])
        selected = []
        for topic in topics:
            if topic.get("archived") or topic.get("closed"):
                continue
            created = _parse_discourse_time(topic.get("created_at", ""))
            if created is None or created < since:
                continue
            if tags and tags.isdisjoint(set(topic.get("tags") or [])):
                continue
            selected.append((topic, created))
            if len(selected) >= int(
                _field(site, "max_topics") or self.max_topics_per_site
            ):
                break

        results = await asyncio.gather(
            *[
                self._build_topic(base, name, topic, created)
                for topic, created in selected
            ],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, ContentItem)]

    async def _build_topic(
        self,
        base: str,
        site_name: str,
        topic: Dict[str, Any],
        created: datetime,
    ) -> Optional[ContentItem]:
        topic_id = int(topic["id"])
        thread = await self._get_json(f"{base}/t/{topic_id}.json")
        title = (topic.get("fancy_title") or topic.get("title") or "").strip()
        if not isinstance(thread, dict):
            excerpt = (topic.get("excerpt") or "").strip()
            content = _html_to_text(excerpt, self.post_chars)
            author = (topic.get("posters") or [{}])[0].get("description") or ""
        else:
            posts = thread.get("post_stream", {}).get("posts", [])
            first = posts[0] if posts else {}
            author = first.get("username", "")
            content = _html_to_text(first.get("cooked", ""), self.post_chars)
            replies = posts[1 : 1 + self.fetch_replies]
            if replies:
                lines = []
                for post in replies:
                    text = _html_to_text(post.get("cooked", ""), 400)
                    if text:
                        lines.append(f"- @{post.get('username', '')}: {text}")
                if lines:
                    content = (content + "\n\n【楼层讨论】\n" + "\n".join(lines)).strip()

        return ContentItem(
            id=self._generate_id("discourse", site_name, str(topic_id)),
            source_type=SourceType.DISCOURSE,
            title=title,
            url=f"{base}/t/{topic_id}",
            content=content,
            author=author or None,
            published_at=created,
            metadata={
                "site": site_name,
                "slug": topic.get("slug"),
                "posts": topic.get("posts_count", 0),
                "views": topic.get("views", 0),
                "likes": topic.get("like_count", 0),
                "tags": topic.get("tags") or [],
                "topic_id": topic_id,
            },
        )

    async def _get_json(self, url: str) -> Any:
        try:
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

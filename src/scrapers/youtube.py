"""YouTube scraper via the OFFICIAL public channel feeds.

No API key, no signing, no page scraping: every YouTube channel publishes
an atom feed at https://www.youtube.com/feeds/videos.xml?channel_id=UC...
(the same endpoint the YouTube Data API documents). Requests are one GET
per channel per run — inherently polite, cacheable, and ToS-clean.

What the feed gives us: title, link, publish time, author, media description,
view/rating counts, and the thumbnail. That is already "streaming media as
text" for the corpus: the description is the creator's own summary of the
video, which is what claim extraction and research actually consume.

Transcript extraction (subtitle tracks) is deliberately NOT done here:
unauthenticated caption access in the wild relies on undocumented endpoints;
Periscope's policy is official surfaces only. Videos worth deeper reading
keep their link, and the Bilibili layer already demonstrates the
transcript path where the platform offers a documented API.
"""

import logging
import re
from datetime import datetime, timezone
from typing import List, Optional
from xml.etree import ElementTree

import httpx

from ..models import ContentItem, SourceType, YouTubeConfig
from .base import BaseScraper

logger = logging.getLogger(__name__)

_ATOM = "{http://www.w3.org/2005/Atom}"
_MEDIA = "{http://search.yahoo.com/mrss/}"
_YT = "{http://www.youtube.com/xmlns/feeds}"
_FEED_PATH = "/feeds/videos.xml"


class YouTubeScraper(BaseScraper):
    """Fetch recent uploads from configured YouTube channels."""

    def __init__(self, config: YouTubeConfig, http_client: httpx.AsyncClient):
        super().__init__({"enabled": config.enabled}, http_client)
        self.config = config

    async def fetch(self, since: datetime) -> List[ContentItem]:
        if not self.config.enabled:
            return []
        items: List[ContentItem] = []
        for entry in self.config.channels:
            if not entry.enabled:
                continue
            feed_url = self._feed_url(entry)
            if not feed_url:
                continue
            try:
                items.extend(await self._fetch_feed(entry.name, feed_url, since))
            except Exception as exc:  # one dead channel must not sink the run
                logger.warning("YouTube feed %s failed: %s", entry.name, exc)
        return items

    @staticmethod
    def _feed_url(entry) -> Optional[str]:
        channel_id = (getattr(entry, "channel_id", "") or "").strip()
        if channel_id:
            sep = "&" if "?" in _FEED_PATH else "?"
            return f"https://www.youtube.com{_FEED_PATH}{sep}channel_id={channel_id}"
        handle = (getattr(entry, "handle", "") or "").strip().lstrip("@")
        if handle:
            # curated channel IDs are also exposed under @handle/streams,
            # but the uploads playlist atom endpoint needs the id itself;
            # accept a directly-configured feed URL as the escape hatch.
            feed = (getattr(entry, "feed_url", "") or "").strip()
            return feed or None
        return (getattr(entry, "feed_url", "") or "").strip() or None

    async def _fetch_feed(
        self, name: str, feed_url: str, since: datetime
    ) -> List[ContentItem]:
        response = await self.client.get(
            feed_url, headers={"User-Agent": "Periscope/0.1 (personal reader; contact: local)"}
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)

        author_name = ""
        author_el = root.find(f"{_ATOM}author/{_ATOM}name")
        if author_el is not None and author_el.text:
            author_name = author_el.text.strip()

        channel_id = ""
        cid_el = root.find(f"{_YT}channelId")
        if cid_el is not None and cid_el.text:
            channel_id = cid_el.text.strip()

        items: List[ContentItem] = []
        for entry in root.findall(f"{_ATOM}entry"):
            published = _parse_dt(_text(entry, f"{_ATOM}published"))
            if published is None or published < since:
                continue
            video_id = (_text(entry, f"{_YT}videoId") or "").strip()
            link_el = entry.find(f"{_ATOM}link")
            link = (link_el.get("href") if link_el is not None else "") or ""
            if not video_id and link:
                m = re.search(r"[?&]v=([\w-]{11})", link)
                video_id = m.group(1) if m else ""
            if not video_id:
                continue

            title = _text(entry, f"{_ATOM}title") or "Untitled"
            group = entry.find(f"{_MEDIA}group")
            description = ""
            thumb = ""
            views: Optional[int] = None
            if group is not None:
                description = _text(group, f"{_MEDIA}description") or ""
                thumb_el = group.find(f"{_MEDIA}thumbnail")
                if thumb_el is not None:
                    thumb = thumb_el.get("url") or ""
                stats = group.find(f"{_MEDIA}community/{_MEDIA}statistics")
                if stats is not None:
                    try:
                        views = int(stats.get("views", ""))
                    except ValueError:
                        views = None

            content = description.strip()
            if views is not None:
                content = f"播放量: {views}\n\n{content}".strip()

            items.append(
                ContentItem(
                    id=self._generate_id("youtube", channel_id or name, video_id),
                    source_type=SourceType.YOUTUBE,
                    title=title,
                    url=link or f"https://www.youtube.com/watch?v={video_id}",
                    author=author_name or name,
                    published_at=published,
                    content=content,
                    profile=self.config.profile,
                    metadata={
                        "channel": author_name or name,
                        "channel_id": channel_id,
                        "video_id": video_id,
                        "thumbnail": thumb,
                        "views": views,
                        "feed_url": feed_url,
                    },
                )
            )
        return items[: self.config.max_videos]


def _text(el, path) -> str:
    child = el.find(path)
    return (child.text or "").strip() if child is not None else ""


def _parse_dt(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None

"""V2EX scraper: hot topics, node topics and reply threads.

Uses the official (key-less) V2EX API. `base_url` is configurable because
the www.v2ex.com endpoint may be unreachable from some networks while the
global mirror works; pick whichever the deployment can reach.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ..models import ContentItem, Section, SourceType
from .base import BaseScraper


class V2EXScraper(BaseScraper):
    """Fetch V2EX topics (hot + selected nodes) with top replies."""

    def __init__(self, config: Any, http_client: httpx.AsyncClient):
        super().__init__(config if isinstance(config, dict) else {}, http_client)
        get = (
            config.get
            if isinstance(config, dict)
            else lambda key, default=None: getattr(config, key, default)
        )
        self.base_url = str(get("base_url", "https://global.v2ex.co")).rstrip("/")
        self.enabled = get("enabled", True)
        self.nodes: List[str] = list(get("nodes", []) or [])
        self.fetch_hot = get("fetch_hot", True)
        self.fetch_replies = get("fetch_replies", 10)
        self.expand_topics = get("expand_topics", 15)
        self.category = get("category")
        self.profile = get("profile")

    async def fetch(self, since: datetime) -> List[ContentItem]:
        if not self.enabled:
            return []

        raw: Dict[int, Dict[str, Any]] = {}
        tasks: List[Any] = []
        if self.fetch_hot:
            tasks.append(self._get_json("/api/topics/hot.json"))
        for node in self.nodes:
            tasks.append(self._get_json("/api/topics/show.json", {"node_name": node}))
        for payload in await asyncio.gather(*tasks, return_exceptions=True):
            if isinstance(payload, Exception) or not isinstance(payload, list):
                continue
            for topic in payload:
                tid = topic.get("id")
                if tid is not None:
                    raw.setdefault(int(tid), topic)

        fresh = [
            topic
            for topic in raw.values()
            if self._created(topic) is not None and self._created(topic) >= since
        ]
        fresh.sort(key=lambda t: t.get("replies", 0), reverse=True)
        expand = fresh[: self.expand_topics]

        results = await asyncio.gather(
            *[self._build_item(topic) for topic in expand], return_exceptions=True
        )
        items: List[ContentItem] = []
        for result in results:
            if isinstance(result, ContentItem):
                items.append(result)
        return items

    @staticmethod
    def _created(topic: Dict[str, Any]) -> Optional[datetime]:
        ts = topic.get("created")
        if not ts:
            return None
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)

    async def _build_item(self, topic: Dict[str, Any]) -> Optional[ContentItem]:
        tid = int(topic["id"])
        created = self._created(topic)
        if created is None:
            return None
        member = topic.get("member") or {}
        node = topic.get("node") or {}
        member = topic.get("member") or {}
        node = topic.get("node") or {}
        author = member.get("username")
        body = (topic.get("content") or "").strip()
        # Plain `content` is often empty for API results; fall back to
        # rendered text stripped of tags would need bs4 — keep raw title+node.
        sections: List[Section] = []
        if body:
            sections.append(Section(tier="primary", text=body, author=author))
        item = ContentItem(
            id=self._generate_id("v2ex", "topic", str(tid)),
            source_type=SourceType.V2EX,
            title=(topic.get("title") or "").strip(),
            url=topic.get("url") or f"{self.base_url}/t/{tid}",
            author=author,
            published_at=created,
            sections=sections,
            metadata={
                "node": node.get("title"),
                "node_slug": node.get("slug"),
                "replies": topic.get("replies", 0),
                "last_reply_by": topic.get("last_reply_by"),
                "topic_id": tid,
            },
            profile=self.profile,
        )
        if self.fetch_replies > 0:
            for reply in await self._fetch_replies(tid):
                item.sections.append(Section(
                    tier="community",
                    text=reply["text"],
                    author=reply["user"] or None,
                    locator=f"#{reply['id']}" if reply.get("id") else None,
                ))
            item.rebuild_content()
        return item

    async def _fetch_replies(self, topic_id: int) -> List[Dict[str, str]]:
        payload = await self._get_json(
            "/api/replies/show.json",
            {"topic_id": topic_id, "from": 0, "to": self.fetch_replies},
        )
        if not isinstance(payload, list):
            return []
        out = []
        for reply in payload[: self.fetch_replies]:
            content = " ".join((reply.get("content") or "").split())
            user = ((reply.get("member") or {}).get("username")) or ""
            if content:
                out.append(
                    {
                        "id": reply.get("id"),
                        "user": user,
                        "text": content if len(content) <= 300 else content[:300] + "…",
                    }
                )
        return out

    async def _get_json(self, path: str, params: Optional[dict] = None) -> Any:
        try:
            response = await self.client.get(
                f"{self.base_url}{path}", params=params
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            return None

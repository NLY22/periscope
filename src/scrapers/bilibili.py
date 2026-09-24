"""Bilibili scraper: popular videos and their top comments.

Streams are treated as first-class sources: each video becomes a
ContentItem, and the comment section (评论区) — the most information-dense
community signal on the platform — is attached as discussion content.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ..models import ContentItem, SourceType
from .base import BaseScraper

API_POPULAR = "https://api.bilibili.com/x/web-interface/popular"
API_COMMENTS = "https://api.bilibili.com/x/v2/reply"
API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_PLAYER = "https://api.bilibili.com/x/player/v2"
VIDEO_URL = "https://www.bilibili.com/video/{bvid}"


class BilibiliScraper(BaseScraper):
    """Fetch popular videos and top comments from Bilibili."""

    def __init__(self, config: Dict[str, Any], http_client: httpx.AsyncClient):
        super().__init__(config, http_client)
        get = config.get if isinstance(config, dict) else lambda key, default=None: getattr(config, key, default)
        self.enabled = get("enabled", True)
        self.max_videos = get("max_videos", 20)
        self.min_views = get("min_views", 0)
        self.fetch_comments = get("fetch_comments", 8)
        self.transcript_chars = get("transcript_chars", 0)

    async def fetch(self, since: datetime) -> List[ContentItem]:
        if not self.enabled:
            return []

        try:
            response = await self.client.get(API_POPULAR, params={"ps": 20, "pn": 1})
            payload = self._check_response(response)
        except Exception:
            return []

        items: List[ContentItem] = []
        records = []
        for rec in payload.get("data", {}).get("list", []):
            if rec.get("reason_type"):  # banner/ads slots
                continue
            records.append(rec)
            if len(records) >= self.max_videos:
                break

        tasks = [self._build_item(rec, since) for rec in records]
        built = await asyncio.gather(*tasks, return_exceptions=True)
        for result in built:
            if isinstance(result, ContentItem):
                items.append(result)
        return items

    async def _build_item(
        self, rec: Dict[str, Any], since: datetime
    ) -> Optional[ContentItem]:
        stat = rec.get("stat", {})
        views = stat.get("view", 0)
        if views < self.min_views:
            return None
        bvid = rec.get("bvid", "")
        if not bvid:
            return None
        published_at = datetime.fromtimestamp(rec.get("pubdate", 0), tz=timezone.utc)
        if published_at < since:
            return None

        owner = rec.get("owner", {})
        item = ContentItem(
            id=self._generate_id("bilibili", "video", bvid),
            source_type=SourceType.BILIBILI,
            title=rec.get("title", "").strip(),
            url=VIDEO_URL.format(bvid=bvid),
            content=self._clean_desc(rec.get("desc", "")),
            author=owner.get("name"),
            published_at=published_at,
            metadata={
                "aid": rec.get("aid"),
                "cid": stat.get("cid") or rec.get("cid"),
                "bvid": bvid,
                "tname": rec.get("tname"),
                "views": views,
                "likes": stat.get("like", 0),
                "coins": stat.get("coin", 0),
                "favorites": stat.get("favorite", 0),
                "danmaku": stat.get("danmaku", 0),
                "reply_count": stat.get("reply", 0),
                "duration_sec": rec.get("duration"),
                "up_mid": owner.get("mid"),
            },
        )

        if self.transcript_chars > 0:
            transcript = await self._fetch_transcript(rec.get("aid"), bvid)
            if transcript:
                item.content = (item.content or "").strip()
                item.content = (
                    item.content + "\n\n【视频字幕节选】\n" + transcript
                ).strip()
                item.metadata["has_transcript"] = True

        if self.fetch_comments > 0:
            comments = await self._fetch_comments(rec.get("aid"), bvid)
            if comments:
                item.content = (item.content or "").strip()
                block = "\n\n【评论区 Top】\n" + "\n".join(
                    f"- @{c['user']}: {c['text']}" for c in comments
                )
                item.content = (item.content + block).strip()
        return item

    async def _fetch_transcript(self, aid: Any, bvid: str) -> str:
        """Inline creator-authored CC subtitles, if the video has any.

        Chain: view -> first page cid -> player/v2 subtitle list -> subtitle
        JSON body. AI-generated subtitles (ai_status) exist too but require
        a login cookie, so they are skipped politely here. Any failure just
        means "no transcript for this video" — never an error.
        """
        if not bvid:
            return ""
        try:
            view = self._check_response(await self.client.get(API_VIEW, params={"bvid": bvid}))
            data = view.get("data", {}) or {}
            cid = (data.get("pages") or [{}])[0].get("cid")
            aid = data.get("aid") or aid
            if not cid or not aid:
                return ""
            player = self._check_response(
                await self.client.get(API_PLAYER, params={"aid": aid, "cid": cid})
            )
            subtitles = (
                ((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
            )
            # Human tracks first; AI tracks (lan "ai-*") need a login cookie
            # to fetch, so skip them when a creator track exists.
            human = [
                s
                for s in subtitles
                if not (s.get("ai_status") or (s.get("lan") or "").startswith("ai"))
            ]
            track = human[0] if human else None
            if track is None:
                return ""
            url = track.get("subtitle_url", "")
            if url.startswith("//"):
                url = "https:" + url
            if not url.startswith("http"):
                return ""
            response = await self.client.get(url, follow_redirects=True)
            response.raise_for_status()
            body = response.json().get("body") or []
            text = " ".join((line.get("content") or "").strip() for line in body)
            return self._truncate(" ".join(text.split()), self.transcript_chars)
        except Exception:
            return ""

    async def _fetch_comments(self, oid: Any, bvid: str) -> List[Dict[str, str]]:
        if not oid:
            return []
        try:
            response = await self.client.get(
                API_COMMENTS,
                params={"type": 1, "oid": oid, "sort": 2, "pn": 1, "ps": self.fetch_comments},
            )
            payload = self._check_response(response)
        except Exception:
            return []
        replies = payload.get("data", {}).get("replies") or []
        out = []
        for reply in replies[: self.fetch_comments]:
            content = (reply.get("content", {}) or {}).get("message", "").strip()
            user = (reply.get("member", {}) or {}).get("uname", "")
            if content:
                out.append({"user": user, "text": self._truncate(content, 300)})
        return out

    @staticmethod
    def _check_response(response: httpx.Response) -> Dict[str, Any]:
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"bilibili api error: {payload.get('code')} {payload.get('message')}")
        return payload

    @staticmethod
    def _clean_desc(desc: str) -> str:
        return BilibiliScraper._truncate(" ".join((desc or "").split()), 2000)

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        return text if len(text) <= limit else text[:limit] + "…"

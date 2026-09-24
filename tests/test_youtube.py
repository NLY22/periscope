"""YouTube scraper tests — official channel atom feeds only."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from src.models import YouTubeChannelConfig, YouTubeConfig
from src.scrapers.youtube import YouTubeScraper

SINCE = datetime(2026, 9, 20, tzinfo=timezone.utc)

_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xmlns/feeds"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <id>channel:UCabc123</id>
  <title>科技观察站</title>
  <author><name>科技观察站</name></author>
  <yt:channelId>UCabc123</yt:channelId>
  <entry>
    <title>开源大模型的最新进展</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=vidAAA00001"/>
    <author><name>科技观察站</name></author>
    <published>2026-09-22T10:00:00+00:00</published>
    <yt:videoId>vidAAA00001</yt:videoId>
    <media:group>
      <media:description>本期讨论 开源权重 与闭源模型的差距。</media:description>
      <media:thumbnail url="https://i.ytimg.com/vi/vidAAA00001/hq.jpg"/>
      <media:community>
        <media:statistics views="12345"/>
      </media:community>
    </media:group>
  </entry>
  <entry>
    <title>太旧的视频</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=vidOLD0002"/>
    <published>2026-09-01T10:00:00+00:00</published>
    <yt:videoId>vidOLD0002</yt:videoId>
    <media:group><media:description>旧内容</media:description></media:group>
  </entry>
  <entry>
    <title>缺少视频编号</title>
    <published>2026-09-23T10:00:00+00:00</published>
  </entry>
</feed>
"""


def _client(feed_body: str | None = _FEED, error: Exception | None = None) -> AsyncMock:
    async def _get(url, **kwargs):
        if error is not None:
            raise error
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.content = (feed_body or "").encode("utf-8")
        return response

    client = AsyncMock()
    client.get.side_effect = _get
    return client


def _config(*channels: YouTubeChannelConfig, **kwargs) -> YouTubeConfig:
    kwargs.setdefault("enabled", True)
    return YouTubeConfig(channels=list(channels), **kwargs)


def test_parses_recent_entry_and_skips_stale_and_invalid() -> None:
    scraper = YouTubeScraper(
        _config(YouTubeChannelConfig(name="科技观察站", channel_id="UCabc123")),
        _client(),
    )
    items = asyncio.run(scraper.fetch(SINCE))

    assert len(items) == 1
    item = items[0]
    assert item.id == "youtube:UCabc123:vidAAA00001"
    assert item.source_type.value == "youtube"
    assert item.title == "开源大模型的最新进展"
    assert str(item.url) == "https://www.youtube.com/watch?v=vidAAA00001"
    assert item.author == "科技观察站"
    # description is the "streaming -> text" payload the corpus stores
    assert "开源权重" in (item.content or "")
    assert item.content.startswith("播放量: 12345")
    assert item.metadata["views"] == 12345
    assert item.metadata["channel_id"] == "UCabc123"
    assert item.metadata["thumbnail"].endswith("hq.jpg")


def test_requests_the_official_feed_url_once_per_channel() -> None:
    client = _client()
    scraper = YouTubeScraper(
        _config(
            YouTubeChannelConfig(name="A", channel_id="UCaaaa"),
            YouTubeChannelConfig(name="B", channel_id="UCbbbb"),
        ),
        client,
    )
    asyncio.run(scraper.fetch(SINCE))

    urls = [c.args[0] for c in client.get.await_args_list]
    assert urls == [
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaa",
        "https://www.youtube.com/feeds/videos.xml?channel_id=UCbbbb",
    ]


def test_explicit_feed_url_is_honoured_and_channel_id_only_missing_label() -> None:
    client = _client()
    scraper = YouTubeScraper(
        _config(
            YouTubeChannelConfig(
                name="自定义", feed_url="https://example.com/my/videos.xml"
            )
        ),
        client,
    )
    asyncio.run(scraper.fetch(SINCE))
    assert client.get.await_args.args[0] == "https://example.com/my/videos.xml"


def test_disabled_and_entry_disabled() -> None:
    off = YouTubeScraper(
        _config(
            YouTubeChannelConfig(name="A", channel_id="UCa"), enabled=False
        ),
        _client(),
    )
    assert asyncio.run(off.fetch(SINCE)) == []

    skipped = YouTubeScraper(
        _config(YouTubeChannelConfig(name="A", channel_id="UCa", enabled=False)),
        _client(),
    )
    assert asyncio.run(skipped.fetch(SINCE)) == []


def test_channel_without_identifier_is_skipped() -> None:
    client = _client()
    scraper = YouTubeScraper(
        _config(YouTubeChannelConfig(name="无名", handle="@onlyahandle")), client
    )
    assert asyncio.run(scraper.fetch(SINCE)) == []
    assert client.get.await_args_list == []


def test_one_broken_channel_does_not_sink_the_run() -> None:
    calls = {"n": 0}

    async def _get(url, **kwargs):
        calls["n"] += 1
        if "UCbad" in url:
            raise RuntimeError("404 not found")
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.content = _FEED.encode("utf-8")
        return response

    client = AsyncMock()
    client.get.side_effect = _get
    scraper = YouTubeScraper(
        _config(
            YouTubeChannelConfig(name="坏", channel_id="UCbad"),
            YouTubeChannelConfig(name="好", channel_id="UCgood"),
        ),
        client,
    )
    items = asyncio.run(scraper.fetch(SINCE))
    assert calls["n"] == 2
    assert len(items) == 1


def test_malformed_xml_yields_empty() -> None:
    scraper = YouTubeScraper(
        _config(YouTubeChannelConfig(name="A", channel_id="UCa")),
        _client("<not-xml"),
    )
    assert asyncio.run(scraper.fetch(SINCE)) == []


def test_max_videos_caps_per_channel() -> None:
    scraper = YouTubeScraper(
        _config(YouTubeChannelConfig(name="A", channel_id="UCa"), max_videos=0),
        _client(),
    )
    assert asyncio.run(scraper.fetch(SINCE)) == []

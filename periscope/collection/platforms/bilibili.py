"""Turn a B站 search hit into a readable, sourced item.

Why this is a separate module from `collectors/collector.py`: the collector's
job is to *find* things cheaply - one request per page, twenty rows at a time.
This job is the opposite: a handful of URLs, several requests each, and the
result is prose (a subtitle track, a comment thread) rather than a row.

Why it is not `src/scrapers/bilibili.py`: that one builds a full `ContentItem`
for the daily digest, which brings the digest's tiering and trust decisions
along with it. Here the output is plain data, and the ingest step decides what
it means.

The four endpoints, and what each buys:

    /x/web-interface/view       cid + authoritative counters
    /x/player/v2                subtitle track list for that cid
    <subtitle_url>              the actual lines
    /x/v2/reply                 top comments (optional, off by default)

Measured cost: ~0.4s for view, ~0.4s for player, ~0.3s for the subtitle body.
So five videos enriched concurrently is about one second, which is why this can
be offered as `--enrich` without breaking the "fast" contract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import asyncio

import httpx

from periscope.runtime import paths


ROOT = paths.ROOT

API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_PLAYER = "https://api.bilibili.com/x/player/v2"
API_REPLY = "https://api.bilibili.com/x/v2/reply"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com/",
}

# A subtitle track longer than this is cheap to keep but slow to index; the
# median lecture-length video measured 6,800 characters.
MAX_TRANSCRIPT_CHARS = 20000
MAX_COMMENTS = 10


class SubtitleUnavailable(RuntimeError):
    """No usable track, with the reason in the message.

    A distinct exception because "the endpoint returned nothing" and "the video
    has no subtitles" are different facts, and only the second one is about the
    video. See `_subtitle_track` for what was measured.
    """


@dataclass
class Enriched:
    """What one video yielded.

    Every field is optional and independently meaningful: a video can have
    counters but no subtitle, and the caller must be able to tell that apart
    from "enrichment failed".
    """

    bvid: str = ""
    aid: Optional[int] = None
    cid: Optional[int] = None
    metrics: Dict[str, int] = field(default_factory=dict)
    transcript_lang: str = ""
    transcript_method: str = "cc"
    segments: List[Dict[str, Any]] = field(default_factory=list)
    text: str = ""
    comments: List[Dict[str, Any]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    # True when the subtitle endpoint answered but gave no track list, which on
    # an anonymous client means "locked", not "absent".
    subtitle_locked: bool = False

    @property
    def has_transcript(self) -> bool:
        return bool(self.text)

    def as_patch(self) -> Dict[str, Any]:
        """Fields to merge into the run row."""
        out: Dict[str, Any] = {}
        if self.metrics:
            out["metrics"] = self.metrics
        ids = {k: v for k, v in
               (("bvid", self.bvid), ("aid", self.aid), ("cid", self.cid)) if v}
        if ids:
            out["ids"] = ids
        if self.has_transcript:
            out["transcript"] = {
                "lang": self.transcript_lang,
                "method": self.transcript_method,
                "segments": self.segments,
                "text": self.text,
            }
        if self.comments:
            out["comments"] = self.comments
        return out


def _num(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _counters(stat: Dict[str, Any]) -> Dict[str, int]:
    """The counters B站 reports, with its own names kept.

    Absent stays absent: `view` returns `play/like/favorite/coin/danmaku/reply/
    share`, but a video with comments disabled still reports 0 - which is a
    measurement, so 0 is kept and missing keys are dropped.
    """
    mapping = {
        "play": "play", "like": "like", "favorite": "favorites",
        "coin": "coin", "danmaku": "danmaku", "reply": "reply",
        "share": "share", "view": "play",
    }
    out: Dict[str, int] = {}
    for key, name in mapping.items():
        value = _num(stat.get(key))
        if value is not None:
            out.setdefault(name, value)
    return out


async def _json(cli: httpx.AsyncClient, url: str, params: Dict[str, Any]) -> Dict:
    response = await cli.get(url, params=params, headers=HEADERS,
                             follow_redirects=True)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") not in (0, None):
        raise RuntimeError("code=%s %s" % (payload.get("code"),
                                           payload.get("message", ""))[:80])
    return payload


async def _subtitle_track(cli: httpx.AsyncClient, aid: int, cid: int):
    """First human-authored subtitle track, else None.

    AI tracks (`ai_status`, or `lan` starting with `ai`) are deliberately not
    used: they need a login cookie to fetch, and an ASR artifact presented as
    the creator's words is exactly the confusion the `method` column exists to
    prevent. They are still recorded as unavailable rather than as empty.

    **Measured 2026-10-19, anonymous access**: `player/v2` returns
    `need_login_subtitle: false` and an empty `subtitles` list for every video
    probed - 80 search hits plus a video whose title advertises "中英双语字幕".
    Both `/x/player/v2` and `/x/player/wbi/v2` behave the same. So on an
    unauthenticated client this endpoint currently yields no tracks at all,
    and an empty list here does NOT mean "the video has no subtitles".

    That is why the caller reports `subtitle-locked-or-absent` rather than
    `no-subtitle`: the two are different facts and only one of them is about
    the video. Supplying a `SESSDATA` cookie is the only way to tell them
    apart, and that is a user decision, not something this module should do
    silently.
    """
    payload = await _json(cli, API_PLAYER, {"aid": aid, "cid": cid})
    data = payload.get("data") or {}
    subtitles = ((data.get("subtitle") or {}).get("subtitles") or [])
    if not subtitles:
        # Record why it might be empty, so the caller can say something true.
        if data.get("need_login_subtitle"):
            raise SubtitleUnavailable("need-login")
        raise SubtitleUnavailable("empty-track-list")
    human = [s for s in subtitles
             if not (s.get("ai_status")
                     or str(s.get("lan") or "").startswith("ai"))]
    return human[0] if human else None


async def _subtitle_body(cli: httpx.AsyncClient, track: Dict[str, Any]):
    url = str(track.get("subtitle_url") or "")
    if url.startswith("//"):
        url = "https:" + url
    if not url.startswith("http"):
        return [], ""
    response = await cli.get(url, headers=HEADERS, follow_redirects=True)
    response.raise_for_status()
    body = (response.json() or {}).get("body") or []
    segments = [
        {"start": line.get("from"), "end": line.get("to"),
         "text": (line.get("content") or "").strip()}
        for line in body if (line.get("content") or "").strip()
    ]
    text = " ".join(seg["text"] for seg in segments)
    if len(text) > MAX_TRANSCRIPT_CHARS:
        text = text[:MAX_TRANSCRIPT_CHARS]
    return segments, text


async def _comments(cli: httpx.AsyncClient, aid: int, limit: int):
    try:
        payload = await _json(cli, API_REPLY, {
            "type": 1, "oid": aid, "sort": 2, "pn": 1, "ps": max(limit, 1),
        })
    except Exception:
        return []
    out = []
    stack = [(reply, out, None) for reply in reversed(((payload.get("data") or {}).get("replies") or [])[:max(limit, 0)])]
    while stack:
        reply, target, inherited_parent = stack.pop()
        if not isinstance(reply, dict):
            continue
        content, member = reply.get("content"), reply.get("member")
        raw_text = content.get("message") if isinstance(content, dict) else ""
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        children = reply.get("replies")
        if not text and not children:
            continue
        author = member.get("uname") if isinstance(member, dict) else ""
        node = {"user": author if isinstance(author, str) else "", "text": text,
                "likes": _num(reply.get("like")) or 0, "rpid": reply.get("rpid")}
        parent = reply.get("parent") or inherited_parent
        if parent:
            node["parent_id"] = parent
        if reply.get("ctime"):
            node["created_at"] = reply["ctime"]
        target.append(node)
        if isinstance(children, list) and children:
            node["replies"] = []
            stack.extend((child, node["replies"], node["rpid"]) for child in reversed(children))
    return out


async def enrich(
    bvid: str,
    *,
    with_comments: bool = False,
    comment_limit: int = MAX_COMMENTS,
    timeout: float = 20.0,
) -> Enriched:
    """Fetch counters, cid and (if present) the creator's subtitle track."""
    result = Enriched(bvid=bvid)
    if not bvid:
        result.errors.append("no-bvid")
        return result

    async with httpx.AsyncClient(timeout=timeout) as cli:
        # 1. view: cid + authoritative counters. Without cid nothing else works,
        # so a failure here ends the attempt rather than cascading.
        try:
            view = await _json(cli, API_VIEW, {"bvid": bvid})
        except Exception as exc:
            result.errors.append("view:%s" % type(exc).__name__)
            return result

        data = view.get("data") or {}
        result.aid = _num(data.get("aid"))
        pages = data.get("pages") or []
        result.cid = _num((pages[0] if pages else {}).get("cid"))
        result.metrics = _counters(data.get("stat") or {})

        # 2. subtitle list + body. Each step can fail on its own: a video with
        # no subtitles is the common case, not an error.
        if result.aid and result.cid:
            track = None
            try:
                track = await _subtitle_track(cli, result.aid, result.cid)
            except SubtitleUnavailable as exc:
                result.errors.append("subtitle:%s" % exc)
                result.subtitle_locked = True
            except Exception as exc:
                result.errors.append("player:%s" % type(exc).__name__)
            if track:
                result.transcript_lang = str(track.get("lan") or "")
                try:
                    segments, text = await _subtitle_body(cli, track)
                    result.segments = segments
                    result.text = text
                except Exception as exc:
                    result.errors.append("subtitle:%s" % type(exc).__name__)
            elif not result.errors or not result.errors[-1].startswith("subtitle:"):
                result.errors.append("subtitle:no-human-track")

        # 3. comments, only when asked. They are a lead, never the author's
        # words, so callers that grade evidence should leave this off.
        if with_comments and result.aid:
            result.comments = await _comments(cli, result.aid, comment_limit)

    return result


async def enrich_many(
    bvids: List[str],
    *,
    concurrency: int = 4,
    with_comments: bool = False,
    timeout: float = 20.0,
) -> Dict[str, Enriched]:
    """Enrich several videos, bounded concurrency, per-video failure isolated.

    `gather` without `return_exceptions` re-raises the first failure and drops
    every other result, which would turn one bad video id into "enrichment
    failed" for the whole query. A row that could not be enriched keeps its
    original data and gains an `errors` entry instead.
    """
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(bvid: str) -> tuple[str, Enriched]:
        async with sem:
            try:
                return bvid, await enrich(bvid, with_comments=with_comments,
                                          timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                failed = Enriched(bvid=bvid)
                failed.errors.append("enrich:%s" % type(exc).__name__)
                return bvid, failed

    results = await asyncio.gather(*(one(b) for b in bvids))
    return dict(results)

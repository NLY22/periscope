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
import time

import httpx

from periscope.runtime import paths


ROOT = paths.ROOT

API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_PLAYER = "https://api.bilibili.com/x/player/v2"
API_REPLY = "https://api.bilibili.com/x/v2/reply"
API_REPLY_THREAD = "https://api.bilibili.com/x/v2/reply/reply"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Referer": "https://www.bilibili.com/",
}

# A subtitle track longer than this is cheap to keep but slow to index; the
# median lecture-length video measured 6,800 characters.
MAX_TRANSCRIPT_CHARS = 20000
MAX_COMMENTS = 60
MAX_COMMENT_PAGES = 3
MAX_REPLY_THREADS = 5


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
    discussion_coverage: Dict[str, Any] = field(default_factory=dict)
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
        if self.discussion_coverage:
            out["discussion_coverage"] = self.discussion_coverage
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


async def _comments(cli: httpx.AsyncClient, aid: int, limit: int,
                    deadline: float | None = None):
    """Fetch a bounded slice of top comments and their available reply threads.

    The legacy anonymous endpoint has returned inconsistent page counts in
    live probes. We follow its page number only while it returns rows and keep
    any mismatch in `truncated_by`; an empty later page never implies complete
    coverage when the API still reports more comments.
    """
    limit = max(0, int(limit))
    coverage = {"status": "complete", "fetched_comments": 0,
                "reported_comments": 0, "fetched_roots": 0,
                "fetched_replies": 0, "truncated_by": [], "errors": []}
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    thread_roots = 0
    if limit == 0:
        coverage["truncated_by"].append("comment_limit")
        coverage["status"] = "partial"
        return out, coverage

    async def request(url: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if deadline is None:
            return await _json(cli, url, params)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            if "time_budget" not in coverage["truncated_by"]:
                coverage["truncated_by"].append("time_budget")
            return None
        try:
            return await asyncio.wait_for(_json(cli, url, params), timeout=remaining)
        except asyncio.TimeoutError:
            if "time_budget" not in coverage["truncated_by"]:
                coverage["truncated_by"].append("time_budget")
            return None

    async def capture(reply: Dict[str, Any], target: List[Dict[str, Any]],
                      inherited_parent: Any = None, *, allow_thread: bool = True) -> None:
        nonlocal thread_roots
        if not isinstance(reply, dict) or coverage["fetched_comments"] >= limit:
            return
        rpid = reply.get("rpid")
        identity = str(rpid) if rpid is not None else ""
        if identity and identity in seen:
            return
        if identity:
            seen.add(identity)
        content, member = reply.get("content"), reply.get("member")
        raw_text = content.get("message") if isinstance(content, dict) else ""
        text = raw_text.strip() if isinstance(raw_text, str) else ""
        children = reply.get("replies")
        if not text and not children:
            return
        author = member.get("uname") if isinstance(member, dict) else ""
        node = {"user": author if isinstance(author, str) else "", "text": text,
                "likes": _num(reply.get("like")) or 0, "rpid": rpid}
        parent = reply.get("parent") or inherited_parent
        if parent:
            node["parent_id"] = parent
        if reply.get("ctime"):
            node["created_at"] = reply["ctime"]
        target.append(node)
        coverage["fetched_comments"] += 1

        inline = children if isinstance(children, list) else []
        node["replies"] = []
        for child in inline:
            if coverage["fetched_comments"] >= limit:
                coverage["truncated_by"].append("comment_limit")
                break
            before = coverage["fetched_comments"]
            await capture(child, node["replies"], rpid, allow_thread=False)
            if coverage["fetched_comments"] > before:
                coverage["fetched_replies"] += 1

        reported_children = _num(reply.get("rcount")) or 0
        if (allow_thread and rpid is not None and reported_children > len(inline)
                and thread_roots < MAX_REPLY_THREADS
                and coverage["fetched_comments"] < limit):
            thread_roots += 1
            fetched_thread = 0
            for page in range(1, MAX_COMMENT_PAGES + 1):
                remaining = limit - coverage["fetched_comments"]
                if remaining <= 0:
                    coverage["truncated_by"].append("comment_limit")
                    break
                try:
                    thread_payload = await request(API_REPLY_THREAD, {
                        "type": 1, "oid": aid, "root": rpid,
                        "pn": page, "ps": min(20, remaining),
                    })
                except Exception as exc:  # keep captured root and inline replies
                    coverage["errors"].append("replies:%s" % type(exc).__name__)
                    break
                if thread_payload is None:
                    break
                thread_data = thread_payload.get("data") or {}
                thread_rows = thread_data.get("replies") or []
                reported = _num((thread_data.get("page") or {}).get("count"))
                if not thread_rows:
                    if reported_children > fetched_thread:
                        coverage["truncated_by"].append("reply_page_empty")
                    break
                before = coverage["fetched_comments"]
                for child in thread_rows:
                    if coverage["fetched_comments"] >= limit:
                        coverage["truncated_by"].append("comment_limit")
                        break
                    await capture(child, node["replies"], rpid, allow_thread=False)
                delta = coverage["fetched_comments"] - before
                fetched_thread += delta
                coverage["fetched_replies"] += delta
                if reported is None or fetched_thread >= max(0, reported - len(inline)):
                    break
                if page == MAX_COMMENT_PAGES:
                    coverage["truncated_by"].append("reply_page_limit")
        elif (allow_thread and rpid is not None
              and reported_children > len(inline)
              and thread_roots >= MAX_REPLY_THREADS):
            coverage["truncated_by"].append("reply_thread_limit")
        elif (allow_thread and reported_children > len(inline)
              and coverage["fetched_comments"] >= limit):
            coverage["truncated_by"].append("comment_limit")

        if not node["replies"]:
            node.pop("replies")

    reported_roots: Optional[int] = None
    page = 1
    while page <= MAX_COMMENT_PAGES and coverage["fetched_comments"] < limit:
        try:
            page_size = min(20, limit - coverage["fetched_comments"])
            payload = await request(API_REPLY, {
                "type": 1, "oid": aid, "sort": 2,
                "pn": page, "ps": page_size,
            })
        except Exception as exc:
            coverage["errors"].append("comments:%s" % type(exc).__name__)
            break
        if payload is None:
            break
        data = payload.get("data") or {}
        rows = data.get("replies") or []
        reported = _num((data.get("page") or {}).get("count"))
        if reported is not None:
            reported_roots = max(reported_roots or 0, reported)
            coverage["reported_comments"] = max(coverage["reported_comments"], reported)
        if not rows:
            if reported_roots is not None and coverage["fetched_roots"] < reported_roots:
                coverage["truncated_by"].append("comment_page_empty")
            break
        for reply in rows:
            if coverage["fetched_comments"] >= limit:
                coverage["truncated_by"].append("comment_limit")
                break
            before = coverage["fetched_comments"]
            await capture(reply, out)
            if coverage["fetched_comments"] > before:
                coverage["fetched_roots"] += 1
        if reported_roots is not None and coverage["fetched_roots"] >= reported_roots:
            break
        if reported_roots is None and len(rows) < page_size:
            break
        if page == MAX_COMMENT_PAGES:
            if reported_roots is None or coverage["fetched_roots"] < reported_roots:
                coverage["truncated_by"].append("comment_page_limit")
            break
        page += 1

    if (coverage["fetched_comments"] >= limit
            and (reported_roots is None
                 or coverage["fetched_roots"] < reported_roots)):
        coverage["truncated_by"].append("comment_limit")
    coverage["truncated_by"] = list(dict.fromkeys(coverage["truncated_by"]))
    coverage["status"] = "partial" if coverage["truncated_by"] or coverage["errors"] else "complete"
    return out, coverage


async def enrich(
    bvid: str,
    *,
    with_comments: bool = False,
    comment_limit: int = MAX_COMMENTS,
    timeout: float = 20.0,
    budget_deadline: float | None = None,
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
            if budget_deadline is not None and budget_deadline <= time.monotonic():
                result.discussion_coverage = {
                    "status": "partial", "fetched_comments": 0,
                    "reported_comments": 0, "fetched_roots": 0,
                    "fetched_replies": 0, "truncated_by": ["time_budget"],
                    "errors": [],
                }
                return result
            result.comments, result.discussion_coverage = await _comments(
                cli, result.aid, comment_limit, deadline=budget_deadline)

    return result


async def enrich_many(
    bvids: List[str],
    *,
    concurrency: int = 4,
    with_comments: bool = False,
    timeout: float = 20.0,
    budget_seconds: float | None = None,
) -> Dict[str, Enriched]:
    """Enrich several videos, bounded concurrency, per-video failure isolated.

    `gather` without `return_exceptions` re-raises the first failure and drops
    every other result, which would turn one bad video id into "enrichment
    failed" for the whole query. A row that could not be enriched keeps its
    original data and gains an `errors` entry instead.
    """
    sem = asyncio.Semaphore(max(1, concurrency))
    deadline = (None if budget_seconds is None else
                time.monotonic() + max(0.0, budget_seconds))

    async def one(bvid: str) -> tuple[str, Enriched]:
        async with sem:
            try:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    skipped = Enriched(bvid=bvid)
                    if with_comments:
                        skipped.discussion_coverage = {
                            "status": "partial", "fetched_comments": 0,
                            "reported_comments": 0, "fetched_roots": 0,
                            "fetched_replies": 0,
                            "truncated_by": ["time_budget"], "errors": [],
                        }
                    return bvid, skipped
                return bvid, await enrich(bvid, with_comments=with_comments,
                                          timeout=(min(timeout, remaining)
                                                   if remaining is not None else timeout),
                                          budget_deadline=deadline)
            except Exception as exc:  # noqa: BLE001
                failed = Enriched(bvid=bvid)
                failed.errors.append("enrich:%s" % type(exc).__name__)
                return bvid, failed

    results = await asyncio.gather(*(one(b) for b in bvids))
    return dict(results)

"""Reachability probes for sources Periscope might add but has not (spec §6).

Why a harness instead of a notebook of curl output: the design rule is
*"先做判别实验，后写代码"*, and the previous results live in the spec as prose.
Prose cannot be re-run when a platform changes, which is exactly what happened
to 百度贴吧 — the S2 conclusion (floors unreachable, list reachable) came from
one afternoon of manual requests. Here the same judgement is a function, so
"blocked" is always re-checkable and never inherited from memory.

Two properties that matter:

- **Injected transport.** Every probe takes an `httpx.AsyncClient`, so the
  verdicts are testable offline with `MockTransport`. Nothing in this module
  reaches the network by itself, and `scripts/spike_sources.py` refuses live
  requests unless `--online` is passed explicitly.
- **A verdict is not an exception.** A timeout, a 403 or a signed-API refusal
  all come back as a `Probe` with the evidence attached, because the whole
  point is to record what actually happened rather than what we expected.

The judgements are the spec's own:

  pass             the author's own text can be parsed out of the response
  list_only        a topic/note list is reachable but no body text is
  blocked_captcha  an interstitial (安全验证 / seccaptcha / BIOC) answered instead
  signed_required  the endpoint answers but demands a request signature
  blocked_auth     a login/permission wall, not a captcha
  error            transport failure, non-2xx without an identifiable wall
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import httpx

VERDICTS = (
    "pass", "list_only", "blocked_captcha", "signed_required",
    "blocked_auth", "error",
)

_CAPTCHA_MARKERS = ("百度安全验证", "seccaptcha", "BIOC_OPTIONS", "验证码", "captcha", "滑块")
_AUTH_MARKERS = ("阅读权限", "请登录", "请先登录", "登录后查看", "登录之后", "sign in",
                 "log in to", "提示信息", "permission to view")

# Floor/reply structure: what distinguishes "we can read the discussion" from
# "we can read the titles". Tieba puts floors in `.lzl_con` / `p_content`,
# Discourse-style JSON in `post_stream`, XHS notes in `noteDesc` / `desc`.
_BODY_MARKERS = (
    "noteDesc", '"desc"', "lzl_con", "p_content", "post_stream",
    "replyCount", "videoInfo",
)

_HTML_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


@dataclass
class Probe:
    """One request's outcome, with enough evidence attached to argue with."""

    source: str
    step: int
    url: str
    verdict: str
    status: Optional[int] = None
    bytes: int = 0
    title: str = ""
    markers: List[str] = field(default_factory=list)
    body_found: bool = False
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def ok(self) -> bool:
        """Only `pass` means "we could actually get the content"."""
        return self.verdict == "pass"


def _markers_in(text: str) -> List[str]:
    lowered = text.lower()
    found = [m for m in _CAPTCHA_MARKERS if m.lower() in lowered]
    found += [m for m in _AUTH_MARKERS if m.lower() in lowered]
    found += [m for m in _BODY_MARKERS if m.lower() in lowered]
    return found


def _has_body_text(text: str) -> bool:
    """Is there author text to parse, as opposed to a shell or a title list?"""
    if any(marker in text for marker in ("noteDesc", '"desc"', "p_content", "post_stream")):
        return True
    match = re.search(r"(?:noteDesc|desc)\s*[:=]\s*\"([^\"]{20,})", text)
    return bool(match)


def classify(
    source: str,
    step: int,
    url: str,
    *,
    status: Optional[int] = None,
    text: str = "",
    error: Optional[str] = None,
) -> Probe:
    """Turn one response into a verdict, preferring the most specific reading.

    Order matters: a 200 page can still be a captcha, and a JSON body with an
    `error_code` is a signature refusal rather than a parse failure.
    """
    markers = _markers_in(text)
    title_match = _HTML_TITLE.search(text)
    title = (title_match.group(1).strip()[:80] if title_match else "")
    body_found = _has_body_text(text)

    if error is not None:
        return Probe(source, step, url, "error", status, len(text), title, markers,
                     False, f"transport failed: {error}")

    if any(m in markers for m in _CAPTCHA_MARKERS) and not body_found:
        return Probe(source, step, url, "blocked_captcha", status, len(text), title,
                     markers, body_found, "an interstitial answered instead of content")

    # A permission wall can arrive as HTTP 200 (that is how the Discuz case
    # showed up in spec §14.4), so this check is not tied to a status code.
    if any(m in markers for m in _AUTH_MARKERS) and not body_found:
        return Probe(source, step, url, "blocked_auth", status, len(text), title,
                     markers, body_found, "login or permission wall")

    if status is not None and 200 <= status < 300:
        code = re.search(r'"error_code"\s*:\s*"?(\d+)"?', text)
        if code and not body_found:
            return Probe(source, step, url, "signed_required", status, len(text), title,
                         markers, body_found,
                         f"endpoint answered with error_code={code.group(1)}; "
                         "getting past it needs request signing, which §11 excludes")
        if body_found:
            return Probe(source, step, url, "pass", status, len(text), title, markers,
                         True, "author text is parseable from this response")
        if any(m in markers for m in ("replyCount", "videoInfo")) or "data-field" in text:
            return Probe(source, step, url, "list_only", status, len(text), title,
                         markers, False,
                         "titles/authors are reachable but the body or floors are not")
        return Probe(source, step, url, "list_only", status, len(text), title,
                     markers, False, "200 with no parseable author text")

    return Probe(source, step, url, "error", status, len(text), title, markers,
                 body_found, f"unexpected HTTP {status}")


async def _get(client: httpx.AsyncClient, url: str, source: str, step: int) -> Probe:
    try:
        response = await client.get(url)
    except Exception as exc:                      # any transport failure is a result
        return classify(source, step, url, error=f"{type(exc).__name__}: {exc}")
    return classify(source, step, url, status=response.status_code,
                    text=response.text if len(response.content) <= 4_000_000 else "")


async def probe_tieba(client: httpx.AsyncClient, keyword: str) -> List[Probe]:
    """S2: the routes measured in spec §14.2, in the same order.

    The mobile and JSON paths are included because they gave different answers
    (403 vs a signed-API refusal), and that difference is the whole
    conclusion — a probe that only checked the PC URL would have said
    "unreachable" and lost the fact that the list page is reachable.
    """
    from urllib.parse import quote

    kw = quote(keyword)
    tid = "10037465698"
    routes = [
        (1, f"https://tieba.baidu.com/f?kw={kw}&pn=0"),
        (1, f"https://tieba.baidu.com/mo/q/m?kw={kw}&lp=5024"),
        (1, f"https://tieba.baidu.com/p/{tid}"),
        (1, f"https://tieba.baidu.com/f/good?kw={kw}"),
        (1, f"http://c.tieba.baidu.com/c/f/frs/page?kw={kw}&pn=1&rn=10"),
    ]
    return [await _get(client, url, "tieba", step) for step, url in routes]


async def probe_xiaohongshu(
    client: httpx.AsyncClient, note_url: str, keyword: str = ""
) -> List[Probe]:
    """S1 step 1: unauthenticated fetch of one note page.

    The spec predicts it will fail; predicting is not measuring, so this records
    status, byte count, page title and which markers appeared. `keyword` adds a
    search-page probe, which is how you find a note id without a logged-in
    session — that, not a guessed URL, is the honest starting point.
    """
    from urllib.parse import quote

    probes = []
    if keyword:
        probes.append(await _get(
            client, f"https://www.xiaohongshu.com/search_result?keyword={quote(keyword)}",
            "xiaohongshu", 1))
    probes.append(await _get(client, note_url, "xiaohongshu", 1))
    return probes


def summarise(probes: List[Probe]) -> Dict[str, Any]:
    """One verdict per source, plus the per-route evidence."""
    by_source: Dict[str, List[Probe]] = {}
    for probe in probes:
        by_source.setdefault(probe.source, []).append(probe)
    out: Dict[str, Any] = {}
    for source, rows in by_source.items():
        if any(r.verdict == "pass" for r in rows):
            judgement = "pass"
        elif any(r.verdict == "list_only" for r in rows):
            judgement = "list_only"
        elif any(r.verdict == "signed_required" for r in rows):
            judgement = "signed_required"
        elif any(r.verdict == "blocked_auth" for r in rows):
            judgement = "blocked_auth"
        elif any(r.verdict == "blocked_captcha" for r in rows):
            judgement = "blocked_captcha"
        else:
            judgement = "error"
        out[source] = {
            "judgement": judgement,
            "floors_or_body_reachable": any(r.verdict == "pass" for r in rows),
            "routes": [r.to_dict() for r in rows],
        }
    return out


def to_json(probes: List[Probe]) -> str:
    return json.dumps(summarise(probes), ensure_ascii=False, indent=2) + "\n"

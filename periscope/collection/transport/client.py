"""HTTP collection, pagination and per-source request policy."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Any, Dict, List, Optional
import asyncio
import json
import re
import sys
import time
import xml.etree.ElementTree as ET

import httpx

from ..catalog.queries import AI_TERMS, CN_TERMS, ARXIV_QUERIES, RECENT_MONTHS, month_windows
from ..models import Source, FetchStats
from .parsing import (
    MAX_TITLE_CHARS, _dig, _decode_escapes, _clean, _rss_items,
    _looks_like_href, _regex_items, _html_items, extract_rows, _num, _extra_fields,
)
from ..sources import CATALOG
from periscope.runtime import paths


ROOT = paths.ROOT

def force_stdout() -> None:
    """UTF-8 stdout for the CLI only.

    This used to rebuild `sys.stdout` from its `.buffer`. That detaches whatever
    owned the stream before (pytest's capture, a redirect), and the process then
    dies at shutdown with "I/O operation on closed file". `reconfigure` changes
    the encoding of the existing stream instead, so it composes with a captor.
    See `core/console.py` for the full account.
    """
    try:
        from periscope.runtime.console import force_utf8_output

        force_utf8_output()
    except Exception:  # noqa: BLE001 - a collector import must not depend on core
        pass

UA = {"User-Agent": (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}

RATE_LIMIT_STATUS = frozenset({412, 429, 503, 403})
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_BACKOFF = 4.0


def _page_url(source: Source, page_value: Any, query: str,
              window: "tuple[int, int] | None" = None) -> str:
    """Fill the placeholders in a source URL.

    Two naming rules, and mixing them up is what broke things twice:

    - `{per_page}` and `{page}` are *role* placeholders. Every source may use
      them regardless of the API's own names; `per_page_param` and `page_param`
      say what to rename them to on the wire.
    - A source whose paging parameter is neither `page` nor `per_page` (arXiv
      uses `start`, npm uses `from`) names it directly in the URL, and
      `page_param` then holds that same literal name - so the rename below is a
      no-op for it.

    `{after}` / `{before}` are unix seconds for date-bounded sources, and are
    left as-is when no window is supplied so a source without a date bound is
    unaffected.
    """
    url = source.url
    if source.queries and source.query_param:
        url = url.replace("{%s}" % source.query_param, quote(query, safe=""))
    if window is not None:
        url = url.replace("{after}", str(window[0]))
        url = url.replace("{before}", str(window[1]))
        # Some APIs want a date, not an epoch. Both spellings are offered so a
        # source can use whichever its endpoint documents, and the unused
        # placeholder simply never appears in that source's URL.
        after_iso = datetime.fromtimestamp(window[0], timezone.utc).strftime("%Y-%m-%d")
        before_iso = datetime.fromtimestamp(window[1], timezone.utc).strftime("%Y-%m-%d")
        url = url.replace("{after_iso}", after_iso)
        url = url.replace("{before_iso}", before_iso)
    url = url.replace("{page}", str(page_value))
    url = url.replace("{cursor}", str(page_value))
    url = url.replace("{per_page}", str(source.per_page))
    # arXiv and npm spell the paging parameter themselves (`{offset}`, `{from}`)
    # and record that spelling in page_param; fill it here so those sources do
    # not depend on the `{page}` role name at all.
    if source.page_param and source.page_param not in ("page", "cursor"):
        url = url.replace("{%s}" % source.page_param, str(page_value))
    return _rename_params(url, source)


def _cookie_dir() -> Path:
    """Where the exported session files live.

    This used to be a literal `Path(r"D:\\work\\periscope\\data\\cookies")`,
    which is this checkout's path and nobody else's: a clone anywhere else
    silently collected anonymously, so the sources that need a session reported
    "0 items" instead of "no cookie file". `core.paths` is the one place that
    knows, and it also applies the `PERISCOPE_COOKIE_DIR` override.
    """
    try:
        from periscope.runtime import paths

        return paths.COOKIES
    except Exception:  # noqa: BLE001 - collector must import standalone
        return ROOT / "data" / "cookies"


COOKIE_DIR = _cookie_dir()


def _load_cookies(name: str) -> Dict[str, str]:
    """Cookies for a source, from the exported session files.

    Accepts both shapes the export tools have produced: a list of
    `{name, value, ...}` objects (what Playwright's context.cookies() returns)
    and a flat `{name: value}` map. A missing file yields no cookies rather than
    an error - some endpoints answer anonymously, and the ones that do not will
    report HTTP 401/412 in the source's own row.
    """
    if not name:
        return {}
    path = COOKIE_DIR / name
    if not path.exists():
        path = COOKIE_DIR / ("%s.json" % name)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    items = raw if isinstance(raw, list) else raw.get("cookies", raw)
    if isinstance(items, dict):
        return {k: v for k, v in items.items() if isinstance(v, str)}
    out = {}
    for c in items:
        if isinstance(c, dict) and c.get("name"):
            out[c["name"]] = c.get("value", "")
    return out


def _date_windows(source: Source) -> "list[tuple[int, int] | None]":
    """The (after, before) pairs a source should be walked over.

    Returns `[None]` for a source with no date bound, so the caller's loop is the
    same either way. Windows are contiguous and non-overlapping, newest first, so
    a partial run still leaves a coherent recent slice rather than gaps.
    """
    if not source.window_days or not source.windows:
        return [None]
    now = int(datetime.now(timezone.utc).timestamp())
    span = source.window_days * 86400
    return [(now - (i + 1) * span, now - i * span) for i in range(source.windows)]


def _rename_params(url: str, source: Source) -> str:
    """Rewrite the role placeholders' query keys to the API's own names."""
    for role, actual in (("per_page", source.per_page_param),
                         ("page", source.page_param)):
        if actual and actual != role:
            url = re.sub(r"([?&])%s=" % role, r"\1%s=" % actual, url)
    return url


def _keep_mentioning(rows: List[Dict[str, Any]], query: str,
                     stats: "FetchStats") -> List[Dict[str, Any]]:
    """Drop rows whose title does not contain `query` as a phrase.

    Whitespace is ignored on both sides, because search endpoints insert it for
    highlighting: CSDN returns `固态 电池 深度报告` for the query `固态电池`.
    Comparing raw strings would reject the relevant rows and keep nothing.

    A whitespace-free comparison is safe for the languages this project handles:
    Chinese has no inter-word spaces, so removing them cannot join two distinct
    words, and an English query like `AI chip` is compared as `aichip` - which
    still only matches titles that contain those words adjacently in some form.

    Counted on the stats object rather than silently dropped: a source whose
    rows are all filtered out must be distinguishable from a source that
    returned nothing, or the next investigation starts from a false premise.
    """
    want = re.sub(r"\s+", "", query or "").lower()
    if not want:
        return rows
    kept = []
    for row in rows:
        title = re.sub(r"\s+", "", row.get("title") or "").lower()
        if want in title:
            kept.append(row)
    dropped = len(rows) - len(kept)
    if dropped:
        stats.filtered += dropped
    return kept


async def collect(client: httpx.AsyncClient, source: Source,
                  since: Optional[datetime] = None, *, on_progress=None) -> tuple:
    """Walk one source to its depth limit. Returns (rows, stats).

    RSS/Atom feeds are fetched once unless the source sets `paginate`. A blog
    feed has no pagination contract, so a second request returns the same
    document - the earlier run showed `pages=2, items=30` on thirty-item feeds,
    which is one page counted twice. arXiv is the exception: its Atom endpoint is
    a search API with a working `start` offset, so it declares `paginate=True`
    and keeps its 500 items.
    """
    stats = FetchStats(source.key, source.name, source.kind)
    started = time.monotonic()
    rows: List[Dict[str, Any]] = []
    seen = set()
    last_requested = None

    page_cap = source.max_pages if (source.paginate or source.fmt != "rss") else 1

    queries = source.queries or ("",)
    for query in queries:
        for window in _date_windows(source):
            cursor = ""
            for page_i in range(page_cap):
                if source.cursor_path:
                    value = cursor or "*"
                else:
                    value = source.page_start + page_i * source.page_step
                url = _page_url(source, value, query, window)
                headers = dict(source.headers) if source.headers else None
                cookies = _load_cookies(source.cookies_file)

                # Retry on the statuses that mean "slow down", not "wrong
                # request". Without this a rate limit silently truncates a
                # source: one run returned 307 arXiv items where the previous
                # run had 12,854, and nothing in the row said the difference was
                # throttling rather than the source running dry.
                r = None
                for attempt in range(RATE_LIMIT_RETRIES + 1):
                    try:
                        if last_requested is not None:
                            await asyncio.sleep(max(0, source.min_interval - (time.monotonic() - last_requested)))
                        last_requested = time.monotonic()
                        stats.requests += 1
                        if source.post_body:
                            # Some search endpoints reject GET outright (掘金
                            # returns 200 with no results for GET, which reads
                            # like an empty corpus rather than a wrong method).
                            r = await client.post(
                                url, timeout=30, headers=headers, cookies=cookies,
                                json=json.loads(source.post_body))
                        else:
                            r = await client.get(
                                url, timeout=30, headers=headers, cookies=cookies)
                    except Exception as exc:
                        stats.error = "%s: %s" % (type(exc).__name__, str(exc)[:50])
                        break
                    if r.status_code not in RATE_LIMIT_STATUS:
                        break
                    if attempt < RATE_LIMIT_RETRIES:
                        wait = RATE_LIMIT_BACKOFF * (2 ** attempt)
                        stats.throttled += 1
                        await asyncio.sleep(wait)
                        continue
                    stats.error = "HTTP %d (throttled)" % r.status_code
                    break
                if r is None or r.status_code != 200:
                    if r is not None and not stats.error:
                        stats.error = "HTTP %d" % r.status_code
                    break

                # A successful transport can still be a login/challenge page
                # or a JSON error envelope. Do not report these as no matches.
                if source.fmt == "json":
                    try:
                        payload = json.loads(r.text)
                    except ValueError:
                        stats.error = "invalid_json_response"
                        break
                    container = _dig(payload, source.items_path) if source.items_path else payload
                    if not isinstance(container, (list, dict)):
                        stats.error = "unexpected_items_schema"
                        break
                elif source.fmt == "rss":
                    try:
                        root = ET.fromstring(r.text)
                    except ET.ParseError:
                        stats.error = "invalid_feed_response"
                        break
                    if root.tag.rsplit("}", 1)[-1].lower() not in ("rss", "feed", "rdf"):
                        stats.error = "unexpected_feed_schema"
                        break

                got = extract_rows(source, r.text, r.headers.get("content-type", ""),
                                   base_url=str(r.url))
                # Drop rows that do not mention the query phrase. Only sources
                # that declare it, because for most endpoints the API already
                # guarantees relevance and a local filter would only cost rows
                # whose title paraphrases the query (CSDN rewrites 固态电池 as
                # `固态 电池` with highlight spaces, which is why the comparison
                # ignores whitespace).
                if source.require_query_in_title and query:
                    got = _keep_mentioning(got, query, stats)
                # Stamp the origin on every row: ingestion needs to know which
                # source (and therefore which credibility prior) an item came
                # from, and the extractor knows nothing about its caller.
                for row in got:
                    row["source"] = source.key
                    row["kind"] = source.kind
                fresh = 0
                for row in got:
                    key = row["url"] or row["title"]
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    rows.append(row)
                    fresh += 1
                stats.pages += 1
                stats.items = len(rows)
                if on_progress is not None:
                    on_progress(rows, stats)

                if source.cursor_path:
                    nxt = _dig(r.json() if r.text.lstrip()[:1] in "{[" else {},
                               source.cursor_path)
                    if not nxt or nxt == cursor:
                        break
                    cursor = nxt
                if fresh == 0:
                    break

    stats.items = len(rows)
    stats.seconds = time.monotonic() - started
    return rows, stats


async def main():
    only = sys.argv[1:] or None
    # The browser-backed platforms are not fetched here: they need Chrome and a
    # session, which is scripts/collect_ugc.py's job. Counting them as failures
    # in this pass would understate the catalog.
    wanted = [s for s in CATALOG
              if not s.needs_browser and (not only or s.key in only)]
    skipped = [s for s in CATALOG if s.needs_browser]
    print("sources in catalog: %d   http now: %d   browser-only: %d"
          % (len(CATALOG), len(wanted), len(skipped)))
    print("=" * 92)

    # Per-host serialisation. GitHub's search API allows 10 requests/minute
    # unauthenticated, and HN throttles too; running six of their sources at once
    # got 403/400 on every one, while the same URLs answered 200 when sent alone.
    # One lock per host costs a little wall time and buys working requests.
    host_locks: Dict[str, asyncio.Lock] = {}
    host_last: Dict[str, float] = {}

    def lock_for(url: str) -> asyncio.Lock:
        host = httpx.URL(url).host or "?"
        if host not in host_locks:
            host_locks[host] = asyncio.Lock()
        return host_locks[host]

    # minimum spacing between two requests to the same host
    HOST_SPACING = {
        "api.github.com": 6.5,      # 10/min, kept well under
        "hn.algolia.com": 4.0,      # search_by_date throttles harder than /search
        # arXiv is the one that proved this table matters. A run at 3.0s spacing
        # with 10 sources in flight returned 307 items where an earlier run had
        # 12,854; a moment later the same endpoint answered three requests in a
        # row. So the limiter is per-connection, not per-IP: holding several
        # requests open against it counts against you. 6s keeps it comfortable.
        "export.arxiv.org": 6.0,
        "api.stackexchange.com": 1.0,
        "api.openalex.org": 1.0,
        "www.federalregister.gov": 1.0,
        # The Chinese platform search endpoints throttle aggressively: B站
        # answers 412 partway through a run even with a Referer, and 知乎
        # tolerates a steady pace but not bursts.
        "api.bilibili.com": 5.0,
        "www.zhihu.com": 1.5,
        "api.juejin.cn": 2.0,
        "so.csdn.net": 2.0,
    }

    sem = asyncio.Semaphore(10)

    async def guarded(s):
        lock = lock_for(s.url)
        spacing = HOST_SPACING.get(httpx.URL(s.url).host or "", 0.0)
        async with sem:
            async with lock:
                if spacing:
                    wait = spacing - (time.monotonic() - host_last.get(
                        httpx.URL(s.url).host or "", 0.0))
                    if wait > 0:
                        await asyncio.sleep(wait)
                async with httpx.AsyncClient(follow_redirects=True,
                                             headers=UA) as c:
                    result = await collect(c, s)
                host = httpx.URL(s.url).host or ""
                host_last[host] = time.monotonic()
                return result

    results = await asyncio.gather(*[guarded(s) for s in wanted])

    all_rows = []
    print("%-20s %-26s %-11s %5s %5s %6s %4s  %s"
          % ("key", "name", "kind", "req", "pages", "items", "thrt", "note"))
    print("-" * 92)
    for (rows, st) in sorted(results, key=lambda x: -x[1].items):
        all_rows.extend(rows)
        print("%-20s %-26s %-11s %5d %5d %6d %4d  %s"
              % (st.key, st.name[:26], st.kind, st.requests, st.pages,
                 st.items, st.throttled, st.error[:24]))
    print("-" * 92)
    tot_items = sum(st.items for _, st in results)
    tot_req = sum(st.requests for _, st in results)
    ok = sum(1 for _, st in results if st.items)
    print("sources with items: %d/%d   requests: %d   items: %d"
          % (ok, len(results), tot_req, tot_items))
    print("(browser-only platforms not counted here: %d)" % len(skipped))

    # Persist the rows so ingestion is a separate, repeatable step rather than
    # something that only exists while this process is alive.
    #
    # A filtered run writes to its own file. Writing `collector.py hn_ai` over
    # `http_items.json` replaced a 20,054-row full collection with the 31,080
    # rows of one source - the numbers looked better and the dataset was gone.
    # Only an unfiltered run owns the canonical filename.
    if only:
        out = paths.COLLECT_DIR / ("http_items.%s.json" % "-".join(sorted(only))[:40])
    else:
        out = paths.COLLECT_DIR / "http_items.json"
    out.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print("wrote %d rows -> %s" % (len(all_rows), out.name))

    from collections import Counter
    print("by kind:", dict(Counter(st.kind for _, st in results if st.items)))
    return all_rows


if __name__ == "__main__":
    force_stdout()
    asyncio.run(main())

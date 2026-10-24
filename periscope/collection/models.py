"""HTTP source descriptions and collection statistics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class Source:
    """One HTTP endpoint, and how to walk it."""

    key: str
    name: str
    url: str
    kind: str                      # official | benchmark | academic | media | community | jobs | gov | finance
    fmt: str = "json"              # json | rss | html
    # where the items live in a JSON payload, dot path; empty means "the root list"
    items_path: str = ""
    # field mapping - each is a dot path within one item
    title: str = "title"
    url_field: str = "url"
    # Fallback dot paths, tried in order when the primary field comes back
    # empty. 知乎 is the reason this exists: `search_v3` returns the display
    # title under `highlight.title` (search-term wrapped) and the plain one
    # under `object.title`, and which of the two is populated depends on the
    # result type - `general` mixes answers, articles and topics. Picking one
    # path silently dropped rows; this tries both.
    title_fallback: tuple = ()
    url_fallback: tuple = ()
    # A targeted regex for `fmt="html"` sources, with the title in group 1 and
    # an optional href in group 2. `_html_items` is a coarse fallback that takes
    # every heading and anchor on the page; on a real search results page that
    # is mostly navigation and footer. 虎扑, 头条 and 公众号 each publish their
    # results in one repeating block, so one regex each beats a generic sweep.
    html_pattern: str = ""
    date_field: str = ""
    author_field: str = ""
    text_field: str = ""           # body text, if the API returns it
    # pagination
    page_param: str = ""           # e.g. "page" or "offset"
    page_start: int = 1
    page_step: int = 1
    per_page_param: str = ""
    per_page: int = 100
    max_pages: int = 5
    # Some Atom endpoints are real search APIs with an offset parameter (arXiv
    # is), while a blog feed is one document with no pagination contract. Both
    # parse as RSS, so the difference has to be declared rather than inferred:
    # guessing "rss means one page" cost arXiv 400 items on the first attempt.
    paginate: bool = False
    cursor_path: str = ""          # e.g. meta.next_cursor, for cursor APIs
    cursor_param: str = ""
    # for search endpoints
    queries: tuple = ()
    query_param: str = ""
    # Date-bounded backfill. When `window_days` is set the collector walks
    # backwards from now in windows of that size, filling `{after}` and `{before}`
    # with unix seconds. Paging deepens one relevance ranking; a date bound
    # reaches a different slice - what was being said in March, which today's
    # ranking has long since buried.
    window_days: int = 0
    windows: int = 0
    # Per-request headers. Needed because a missing `Referer` is what separates
    # a 412 from a 200 on B站's search API - the platform was written off as
    # "needs a browser" when a one-line header was the whole problem.
    headers: tuple = ()
    # A cookie file to send with every request for this source. Stored outside
    # the repository, so a missing file means an unauthenticated request rather
    # than a crash - some endpoints work anonymously, some do not.
    cookies_file: str = ""
    # POST body template, for endpoints that refuse GET (掘金 search).
    post_body: str = ""
    # pacing
    min_interval: float = 0.4
    # A platform whose content is rendered by JavaScript cannot be read with a
    # plain GET - a probe returned byte-identical shells for two different 抖音
    # URLs. Those sources are collected by the browser pass instead, and
    # `profile` names the persistent Chrome profile holding the session, if any.
    needs_browser: bool = False
    profile: str = ""
    # Engagement fields on a search hit, as dot paths into one item. Kept as
    # raw numbers with their original names (`play`, `digg_count`, ...) rather
    # than normalised here: what a "view" means differs per platform, and that
    # mapping belongs to the analysis layer, not to the collector. Declaring
    # them per source is what makes "collect the signal" possible without a
    # second request - the search response already carries it.
    metrics: tuple = ()
    # Extra ids worth keeping for a follow-up fetch (B站 `cid` is not in search
    # results, but `aid`/`bvid` are, and they are what `player/v2` needs).
    id_fields: tuple = ()
    # Drop rows whose title does not contain the query phrase (whitespace
    # ignored). For endpoints that treat a query as bag-of-words rather than a
    # phrase, the *result count* looks healthy while the content is unrelated.
    #
    # sov2ex is the case that forced this: 「固态电池」 returned 511 hits and the
    # first five were 油车换电车 / MacBook 二手 / 三星手机 - matching on 固态 and
    # 电池 separately. Quoting the query returns 0, so the phrase filter has to
    # happen on our side. Without it, 20 unrelated rows entered every topic run
    # with a platform trust prior attached, which is worse than collecting
    # nothing: they are scored as evidence.
    require_query_in_title: bool = False


@dataclass
class FetchStats:
    key: str
    name: str
    kind: str
    requests: int = 0
    items: int = 0
    pages: int = 0
    seconds: float = 0.0
    error: str = ""
    # How many times the source asked us to slow down and we did. Reported so a
    # short yield can be read as throttling rather than as an empty source.
    throttled: int = 0
    # Rows the source returned but that did not mention the query phrase.
    # Separate from `items` so "the endpoint answered with unrelated content"
    # is visible instead of looking like an empty source.
    filtered: int = 0

"""Adapters for local, API and platform topic lookup."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

from .models import SPEED_FAST, SPEED_NORMAL, SPEED_DEEP, FAST_SOURCES, BROWSER_SCRIPTS, BROWSER_MODULES
from periscope.runtime import paths
from periscope.search import api as dsapi, local as corpus_search


ROOT = paths.ROOT

def local_lookup(topic: str, limit: int) -> tuple[list[dict], int]:
    """查本地语料，返回 (行, 命中总数)。"""
    if not Path(corpus_search.CORPUS).is_file():
        return [], 0
    db = corpus_search.connect()
    terms = corpus_search.terms_of(topic)
    match, _ = corpus_search.build_match(
        [t for t in terms if len(t) >= corpus_search.MIN_GRAM])
    total = 0
    if match:
        try:
            total = db.execute(
                "SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH ?",
                (match,)).fetchone()[0]
        except Exception:  # noqa: BLE001
            total = 0
    rows, _ = corpus_search.search(db, topic, limit)
    # The ranking reads a 400-char snippet on purpose (it runs over hundreds of
    # candidates). The rows that survive are the ones the reader actually sees,
    # so their real body is fetched here - otherwise a 22,680-character stored
    # article reached the output as 400 characters with nothing saying so.
    rows = corpus_search.hydrate_bodies(db, rows)
    db.close()

    out = []
    for r in rows:
        r = dict(r)
        body = r["body"] if "body" in r.keys() else ""
        out.append({
            "title": r["title"] or "",
            "url": r["url"] or "",
            "author": r["publisher"] or "",
            "publisher": r["publisher"] or "",
            "date": (r["published_at"] or "")[:10],
            "body": body if (r["clen"] or 0) >= 400 else "",
            "text": body or r["snippet"] or "",
            "trust": r["trust"] or 0.5,
            "source": r.get("source_key") or r["source_type"] or "",
            "kind": r.get("source_kind") or "",
            **{key: r[key] for key in ("provenance", "metrics", "ids", "published_text", "body_source_url", "sources", "document") if r.get(key)},
            "_origin": "local",
        })
    return out, total


def crawl_lookup(topic: str, seeds: tuple[str, ...], *, max_pages=12, depth=2, budget=30, form_fields=()):
    from periscope.collection.crawling.engine import crawl_topic
    return crawl_topic(topic, seeds, max_pages=max_pages, depth=depth, budget=budget, form_fields=form_fields)


def api_lookup(topic: str, count: int, depth: int = 1) -> tuple[list[dict], float, str]:
    """Query the search API, optionally through several phrasings.

    `depth=1` is a single query and behaves exactly as before.
    """
    if depth <= 1:
        r = dsapi.search_topic(topic, count=count)
    else:
        r = dsapi.search_topic_wide(topic, count=count, depth=depth)
    if not r.ok:
        return [], r.elapsed, r.error
    rows = r.rows()
    for x in rows:
        x["_origin"] = "api"
        # 去掉 ISO 时间的时分秒；API 返回 `2026-01-19T16:00:00+00:00`，
        # 展示时只需要日期
        d = (x.get("date") or "").strip()
        if d:
            m = re.match(r"(\d{4}-\d{2}-\d{2})", d)
            x["date"] = m.group(1) if m else d[:10]
    return rows, r.elapsed, ""


def _platform_trust(source_key: str, kind: str = "") -> float:
    """The credibility prior for one platform row, from the project's own table.

    This used to be two constants - `0.62` for every HTTP source and `0.58` for
    every browser source - regardless of which platform the row came from. The
    project already carries a per-family table (`src.corpus.trust.SOURCE_PRIORS`)
    and the ingest path uses it, so the live topic query was scoring against a
    different scale than the corpus it reads back.

    The flat value was not neutral, it was flattering. Measured against the
    table, `0.62` is **+0.27** for bilibili (prior 0.35) and **+0.17** for the
    seven V2EX-family sources - 知乎, CSDN, 掘金, V2EX, 百度知道 all land there.
    It is **-0.08** for rss. Combined with the freshness bonus, a same-day
    虎扑 reply (0.62 + 0.12 = 0.74) outranked 人民网 at authority 4 (0.667),
    which is the inversion this project's trust model exists to prevent.

    The lookup itself lives in `collectors/credibility.py`, not here: `topic.py`
    may not import `src` at all (`tests/test_b_import_boundary.py`), while
    `collectors/` is allowed to borrow that kernel. Delegating keeps the boundary
    intact and the number honest, instead of relaxing the rule to let one import
    through.
    """
    try:
        from periscope.collection import credibility

        return credibility.prior_for(source_key, kind)
    except Exception:  # noqa: BLE001 - a trust lookup must not break collection
        return 0.5


def platform_lookup(topic: str, speed: str = SPEED_NORMAL,
                    budget: float = 30.0, *, coverage: str = "focused",
                    sources: tuple[str, ...] = (), kinds: tuple[str, ...] = (),
                    sources_file: str = "") -> tuple[list[dict], dict]:
    """跑平台采集，返回 (行, 统计)。

    用并发采集器（`collectors/fanout.py`）：实测串行 74.8 秒的 13 个源
    并发后 26.1 秒，快速模式（1 页、去掉 B站）只要 4.7 秒。
    """
    stat: dict = {"ran": [], "failed": [], "elapsed": 0.0, "mode": speed}
    t0 = time.monotonic()
    rows: list[dict] = []

    plan = None
    # ---------- HTTP 平台源 ----------
    try:
        from periscope.collection.strategies import focused as fanout

        # `budget` is the caller's wall-clock ceiling, and it used to reach only
        # the browser subprocesses below - so on the default fast path the flag
        # was accepted and did nothing. Passing it here makes it mean what its
        # help text says. A slow source that misses the budget is skipped and
        # reported, not treated as a failure: "did not finish" is not "refused".
        if coverage != "focused" or sources or kinds or sources_file:
            from periscope.collection.strategies import broad
            from periscope.collection.registry import SourceRegistry

            registry = SourceRegistry.from_file(sources_file) if sources_file else SourceRegistry()
            plan = registry.plan(coverage, sources=sources, kinds=kinds)
            http_budget = min(budget / 2, 30) if budget and speed == SPEED_DEEP else budget
            got, st = broad.collect_topic(topic, plan, pages=1 if speed == SPEED_FAST else 2 if speed == SPEED_NORMAL else 3,
                                          timeout=15 if speed == SPEED_FAST else 30, budget=http_budget)
            stat["source_reports"] = st["source_reports"]
        elif speed == SPEED_FAST:
            got, st = fanout.collect_topic(
                topic, sources=FAST_SOURCES, pages=1, timeout=min(20, budget) if budget else 20,
                budget=budget)
        elif speed == SPEED_NORMAL:
            got, st = fanout.collect_topic(
                topic, pages=2, timeout=min(30, budget) if budget else 30,
                budget=budget)
        else:
            got, st = fanout.collect_topic(
                topic, pages=3, timeout=min(45, budget) if budget else 45,
                budget=budget)

        for r in got:
            # `body` used to be hardcoded "" here, which threw away text the
            # collector *had*: v2ex returns up to 1,200 characters of post
            # content, and it was parked in `text` while the row claimed no body.
            # The renderer reads `body` (so the screen said "正文: 0 字"), the
            # run shard stored body="" (so a later ingest saw no body), and the
            # only place the text survived was a field nothing displayed.
            #
            # The 400 threshold is the project's existing definition of "has a
            # body" (core/search.MIN_BODY). Below it the text is still kept in
            # `text`, which is where a snippet belongs.
            text = r.get("text") or ""
            rows.append({
                "title": r.get("title") or "",
                "url": r.get("url") or "",
                "author": r.get("author") or r.get("source") or "",
                "date": _fmt_date(r.get("date")),
                "body": text if len(text) >= 400 else "",
                "text": text,
                "trust": _platform_trust(r.get("source") or "", r.get("kind") or ""),
                "source": r.get("source") or "",
                "kind": r.get("kind") or "community",
                **({"provenance": r["provenance"]} if r.get("provenance") else {}),
                **({"published_text": r["published_text"]} if r.get("published_text") else {}),
                **{key: r[key] for key in ("body_source_url", "discussion_url") if r.get(key)},
                "_origin": "platform",
                # Carried through only when the collector actually captured
                # them; an empty dict is dropped rather than stored as "no
                # engagement", which is a different claim from "not measured".
                **({"metrics": r["metrics"]} if r.get("metrics") else {}),
                **({"ids": r["ids"]} if r.get("ids") else {}),
                **{key: r[key] for key in ("comments", "replies", "answers") if r.get(key)},
            })
        stat["ran"].extend("%s:%d" % (k, n) for k, n, _, _ in st["per"] if n)
        stat["failed"].extend(st.get("failed") or [])
        if st.get("over_budget"):
            stat["over_budget"] = list(st["over_budget"])
    except Exception as exc:  # noqa: BLE001
        stat["failed"].append("fanout:%s" % type(exc).__name__)

    if speed == SPEED_DEEP and (plan is not None or not (coverage != "focused" or sources or kinds or sources_file)):
        from periscope.collection.strategies import browser as browser_search

        got, reports = browser_search.collect(topic, BROWSER_SCRIPTS, BROWSER_MODULES,
                                               started=t0, budget=budget, plan=plan)
        for row in got:
            text = row.get("text") or row.get("title") or ""
            rows.append({**row, "author": row.get("author") or row.get("platform_name") or "",
                         "date": _fmt_date(row.get("date")), "text": text,
                         "body": text if len(text) >= 400 else "",
                         "trust": _platform_trust(row["source"]), "_origin": "platform"})
        # Replace HTTP skip records with the actual browser outcome.
        observed = {report["key"] for report in reports}
        stat["source_reports"] = [report for report in stat.get("source_reports", [])
                                  if report["key"] not in observed] + reports
        stat["ran"].extend("%s:%d" % (report["key"], report["rows"]) for report in reports if report["rows"])
        stat["failed"].extend(report["key"] + ":" + report["error"] for report in reports
                              if report["status"] == "failed" or report["status"] == "partial" and report["error"] != "browser_deadline")
        stat.setdefault("over_budget", []).extend(report["key"] for report in reports if report["error"] == "browser_deadline")

    stat["elapsed"] = time.monotonic() - t0
    return rows, stat


def _fmt_date(raw) -> str:
    if raw is None or raw == "":
        return ""
    t = str(raw).strip()
    if re.fullmatch(r"\d{9,13}", t):
        try:
            import datetime as dt

            v = int(t)
            if v > 10 ** 11:
                v //= 1000
            return dt.datetime.fromtimestamp(
                v, dt.timezone.utc).strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            return ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        return m.group(0)
    try:
        from email.utils import parsedate_to_datetime

        d = parsedate_to_datetime(t)
        if d:
            return d.strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        pass
    return ""

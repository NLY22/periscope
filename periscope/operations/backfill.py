"""Select eligible article URLs and cache recovered full text for later ingestion."""

from __future__ import annotations

from pathlib import Path
import argparse
import asyncio
import hashlib
import json
import sqlite3
import sys
import time

import httpx

from periscope.runtime import paths
from periscope.runtime.console import force_utf8_output
from src.corpus.store import Corpus
from src.extractors.trafilatura import TrafilaturaExtractor
from src.models import TrafilaturaExtractorConfig
from .fulltext import recover_body, mark_fulltext_failure


ROOT = paths.ROOT


CORPUS = paths.CORPUS

# Hosts and paths that serve data, not prose. Fetching an npm registry document
# or a GitHub API blob and asking an article extractor for its body wastes a
# request and returns nothing, so they are excluded by pattern rather than by an
# allowlist that would need constant maintenance.
MACHINE_PATTERNS = (
    "api.zhihu.com", "api.github.com", "api.bilibili.com", "api.openalex.org",
    "export.arxiv.org", "arxiv.org/abs", "doi.org/", "registry.npmjs.org",
    "www.npmjs.com", "pypi.org", "spdx.org", "openrouter.ai",
    "github.com/advisories", "raw.githubusercontent.com",
    "hn.algolia.com", "api.stackexchange.com", "huggingface.co/",
    "www.bilibili.com/video", "b23.tv", "www.youtube.com",
    "twitter.com", "x.com/", "federalregister.gov/api",
    "search.bilibili.com", "api.juejin.cn",
    # Catalogue and store pages: these are listings, not articles. The first dry
    # run picked twelve 知乎 ebook SKUs, because "shortest content first" selects
    # exactly the machine-generated product pages.
    "/kvip/", "/sku/", "/product/", "/products/", "/store/", "/shop/",
    "/category/", "/tag/", "/tags/", "/search", "?q=",
    "/login", "/signup", "/pricing", "/download", "/playlist",
    # Hosts measured to refuse or to serve no article text. openai.com answers
    # 403 to a non-browser client; oschina's news index and cnbeta's list pages
    # parse to nothing. Retrying them at scale wasted 23 of 80 attempts, so they
    # are excluded rather than silently failing one request at a time.
    "openai.com/index", "www.oschina.net/news/list", "www.oschina.net/news/rss",
    # Google News hands out redirect URLs, not article URLs:
    #   https://news.google.com/rss/articles/CBMi...?oc=5
    # Fetching one answers HTTP 503 every single time. Measured: a 400-item run
    # spent its entire budget on these and recovered 0, because the ranking put
    # `google_news` first and every candidate was a redirect. The 34,368
    # Google News rows need their real target resolved first (the base64 segment
    # decodes to it), which is a separate pass - not something this fetcher can
    # do by requesting the redirect.
    "news.google.com/rss/articles",
    # PDF。Trafilatura 只解析 HTML，PDF 每次都会抛异常。实测队首被
    # anthropic.com 的系统卡 PDF 占满，8 批运行几乎全部空转。
    ".pdf",
    # 站点首页（见 BARE_HOSTS）与搜狗跳转链接。
    # 搜狗 `/link?url=` 要过一次 JS 跳转才到真实页面，直接抓只有 404。
    "weixin.sogou.com/link?",
)

# Whole hosts that never yield article text - the path-level patterns above miss
# them because the URL shape is ordinary. Learned from the measurement, and kept
# short so a host that merely blocks one page is not written off entirely.
DEAD_HOSTS = (
    "openai.com", "www.oschina.net",
)

MIN_ARTICLE_CHARS = 400
# Below this the item already has enough to work with; above it, fetching is not
# the bottleneck.
SHORT_THRESHOLD = 200

# Types whose "missing body" is not missing text at all. Measured body rate for
# each of these after 3,300 fetches was 0.0%, and the reason is structural, not
# fixable by retrying:
#   twitter      the tweet itself IS the text - it is already in `title`
#   bilibili     a video page carries a description, not an article
#   youtube      same
#   google_news  the URL is a redirect; fetching it answers 503 every time
#   reddit       answers 403 to a non-API client
# Fetching them would spend the whole budget on guaranteed empties.
NO_BODY_TYPES = frozenset({"twitter", "bilibili", "youtube", "google_news", "reddit"})

# 一篇真正的文章很少超过这个长度。超过说明提取器把整站内容吞了进来 ——
# 实测抓取成功的 9,942 条里，最长一条 1,480,601 字，前十条都在 30 万字以上，
# 内容是 API 文档或整本论文集，不是"某篇文章"。这类行会污染检索：
# 它们含有几乎所有关键词，于是任何查询都能匹配到它们。
#
# 中位数是 5,672 字，所以 60,000 字的上限对正常文章没有任何影响。
MAX_ARTICLE_CHARS = 60000


# 只有「站点首页」才排除，不是整个 host。
# `https://stackoverflow.com` 没有正文，但 `https://stackoverflow.com/questions/123`
# 有 —— 用 `in` 判断会把后者一起排除，那是 6 万个真实问答页。
BARE_HOSTS = frozenset({
    "https://stackoverflow.com",
    "https://agents.stackoverflow.com",
    "http://stackoverflow.com",
    "http://agents.stackoverflow.com",
})


def is_machine(url: str) -> bool:
    low = (url or "").lower()
    if any(p in low for p in DEAD_HOSTS):
        return True
    # 精确匹配站点首页（忽略尾部斜杠）
    stripped = low.rstrip("/")
    if stripped in BARE_HOSTS:
        return True
    return any(p in low for p in MACHINE_PATTERNS)


def normalize_url(url: str) -> str:
    """去掉追踪参数，还原成「干净的文章地址」。

    CSDN 的搜索结果链接带一长串 `ops_request_misc=elastic_search_misc&
    request_id=...&utm_medium=...`，带着这些参数请求会稳定返回 HTTP 521
    （反爬）。去掉之后是 `https://blog.csdn.net/{user}/article/details/{id}`，
    正常返回 200 和完整正文。

    实测：40 条带追踪参数的 CSDN 链接，0 条成功；去掉参数后抓取正常。
    """
    if not url:
        return url
    try:
        from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

        p = urlparse(url)
        if not p.query:
            return url
        keep = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                if not (k.startswith("utm_") or k in (
                    "ops_request_misc", "request_id", "biz_id", "spm",
                    "utm_term", "utm_source", "utm_medium", "utm_campaign",
                    "share_token", "share_source", "from_source",
                    "xsec_token", "xsec_source", "channel_type",
                    "vd_source", "scene", "srcid", "dist_request_id",
                ))]
        return urlunparse(p._replace(query=urlencode(keep)))
    except Exception:  # noqa: BLE001
        return url


def report(conn) -> None:
    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    short = conn.execute(
        "SELECT COUNT(*) FROM items WHERE LENGTH(COALESCE(claimable, content,'')) < ?",
        (SHORT_THRESHOLD,)).fetchone()[0]
    have = conn.execute(
        "SELECT COUNT(*) FROM items WHERE LENGTH(COALESCE(content,'')) >= ?",
        (MIN_ARTICLE_CHARS,)).fetchone()[0]
    print("=" * 80)
    print("full-text coverage")
    print("=" * 80)
    print("  items          %7d" % total)
    print("  body >=800ch   %7d (%.1f%%)" % (have, 100.0 * have / total))
    print("  summary-only   %7d (%.1f%%)" % (short, 100.0 * short / total))
    try:
        done = conn.execute(
            "SELECT COUNT(*) FROM items WHERE metadata_json LIKE '%\"fulltext\"%'"
        ).fetchone()[0]
        tried = conn.execute(
            "SELECT COUNT(*) FROM items WHERE metadata_json LIKE '%\"fulltext_failed\"%'"
        ).fetchone()[0]
        print("  fetched ok     %7d" % done)
        print("  fetched failed %7d" % tried)
    except sqlite3.OperationalError:
        pass

    # Per-type breakdown. The headline "88% summary-only" is misleading: for
    # twitter the tweet text *is* the body and for bilibili/youtube the page is
    # a video, so those rows can never have a body. Reporting the rate per type
    # shows where the real gap is instead of one number that implies a single
    # fixable problem.
    print()
    print("  by source type (items / with body / rate / fetchable?)")
    try:
        rows = conn.execute("""
            SELECT source_type,
                   COUNT(*) n,
                   SUM(CASE WHEN LENGTH(COALESCE(content,'')) >= ? THEN 1 ELSE 0 END) f
            FROM items GROUP BY 1 ORDER BY n DESC
        """, (MIN_ARTICLE_CHARS,)).fetchall()
        for r in rows:
            st, n, f = r[0], r[1], r[2]
            mark = "no" if st in NO_BODY_TYPES else "yes"
            print("    %-14s %7d  %7d  %5.1f%%  %s"
                  % (st, n, f, 100.0 * f / n, mark))
    except sqlite3.OperationalError:
        pass


def candidates(conn, limit: int, hosts: tuple = ()):
    """Items that need a body and can plausibly supply one.

    Ordered by trust, not by shortness. "Shortest first" was the obvious choice
    and the wrong one: the shortest bodies in this corpus are machine-generated
    listing pages, so the pass spent its budget on 知乎 ebook SKUs and npm
    registry documents. Trust already encodes "this is a real publication", and
    the machine-endpoint filter removes the rest.

    Ordered *within* a source-type priority as well, because trust alone put
    every github.com repository first (a repo page scores high and its README
    is technically "body text"). Measured: a 50-item run spent all 50 on GitHub
    READMEs at 15,390 chars average - real text, but the wrong 50. News is the
    material that answers "what was said", so it is fetched first.
    """
    # Lower number = fetched earlier.
    #
    # The split is not "important vs unimportant", it is **fetchable vs not**.
    # Measured coverage per type after ~3,300 fetches:
    #
    #   rss          58.7%   real articles            -> worth fetching
    #   gdelt        54.1%   news                      -> worth fetching
    #   hackernews   27.5%   links to external posts   -> worth fetching
    #   v2ex          4.2%   topic pages have a body   -> worth fetching
    #   google_news   2.4%   redirects, 503 every time -> do not fetch
    #   bilibili      0.0%   video page, no article    -> nothing to fetch
    #   twitter       0.0%   the tweet *is* the text   -> nothing to fetch
    #   youtube       0.0%   video page, no article    -> nothing to fetch
    #   reddit        0.0%   answers 403 to non-API    -> nothing to fetch
    #
    # Putting the unfetchable types first is what made three consecutive
    # 8,000-item batches recover nothing: the queue was full of links that
    # cannot yield text, so the budget went to guaranteed failures.
    type_rank = {
        "rss": 0,
        "gdelt": 1,
        "hackernews": 2,
        "v2ex": 3,
        "openbb": 4,
        # --- below here: measured to give no article text. Kept at the end so
        # a run that reaches them has already covered everything useful.
        "github": 8,        # README exists but is not "what was said"
        "reddit": 9,
        "twitter": 10,
        "bilibili": 11,
        "youtube": 12,
        "google_news": 13,
    }
    # The rank has to be applied *inside* the query, not to the returned rows.
    # Sorting in Python after `LIMIT ?` only reorders whatever survived the
    # limit - and since github.com URLs dominate the high-trust head of the
    # table, every batch was 100% GitHub READMEs no matter what the Python sort
    # said. Measured: a --limit 5 dry run returned five GitHub repos.
    case_sql = " ".join(
        "WHEN '%s' THEN %d" % (k, v) for k, v in type_rank.items())
    sql = f"""
        SELECT id, url, title, source_type, publisher, trust,
               LENGTH(COALESCE(claimable, content, '')) n
        FROM items
        WHERE url LIKE 'http%'
          AND LENGTH(COALESCE(content, '')) < {MIN_ARTICLE_CHARS}
          AND (metadata_json IS NULL
               OR (metadata_json NOT LIKE '%"fulltext"%'
                   AND metadata_json NOT LIKE '%"fulltext_failed"%'))
        ORDER BY CASE source_type {case_sql} ELSE 99 END, trust DESC
        LIMIT ?
    """
    rows = conn.execute(sql, (limit * 20,)).fetchall()
    out = []
    for r in rows:
        if r["source_type"] in NO_BODY_TYPES:
            continue
        if is_machine(r["url"]):
            continue
        if hosts and not any(h in r["url"] for h in hosts):
            continue
        out.append(r)
        if len(out) >= limit:
            break

    # The first page of the ranked query can be entirely machine endpoints -
    # one whole page was `oschina.net/news/*` release notes plus an OpenAI model
    # index, all filtered out by `is_machine`, which made `--limit 8` return
    # zero candidates and the run silently do nothing. So keep pulling pages,
    # with an offset, until the quota is met or the table is exhausted.
    if len(out) < limit:
        offset = len(rows)
        while len(out) < limit:
            page = conn.execute(sql + " OFFSET ?", (limit * 20, offset)).fetchall()
            if not page:
                break
            offset += len(page)
            for r in page:
                if r["source_type"] in NO_BODY_TYPES:
                    continue
                if is_machine(r["url"]):
                    continue
                if hosts and not any(h in r["url"] for h in hosts):
                    continue
                out.append(r)
                if len(out) >= limit:
                    break
    return out


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--hosts", default="",
                    help="comma-separated host substrings to restrict to")
    ap.add_argument("--urls-file", default="",
                    help="只抓这个文件里列出的 URL（一行一个），"
                         "用于「任意话题」的定向补正文")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()

    corpus = Corpus(str(CORPUS))
    conn = corpus._conn

    if args.report:
        report(conn)
        corpus.close()
        return 0

    hosts = tuple(h.strip() for h in args.hosts.split(",") if h.strip())
    if args.urls_file:
        # 定向抓取：给了 URL 清单就只抓这些，不走候选队列。
        # 「任意话题」的路径需要这个 —— 新采到的那批 URL 在队列里排在
        # 全库后面，等轮到它们要几百批。
        wanted = []
        seen_urls = set()
        try:
            with open(args.urls_file, "r", encoding="utf-8") as fh:
                for line in fh:
                    u = line.strip()
                    if not u or u in seen_urls:
                        continue
                    seen_urls.add(u)
                    wanted.append(u)
        except Exception as exc:  # noqa: BLE001
            print("  --urls-file 读取失败: %s" % exc)
            corpus.close()
            return 2
        if not wanted:
            print("  --urls-file 为空，无事可做")
            corpus.close()
            return 0
        placeholders = ",".join("?" * len(wanted))
        # 定向模式不受 --limit 的默认值 50 限制：清单本身就是范围。
        cap = args.limit if args.limit and args.limit != 50 else len(wanted)
        try:
            rows = conn.execute(
                "SELECT id, url, title, source_type, publisher, trust, "
                "LENGTH(COALESCE(content,'')) n FROM items "
                "WHERE url IN (%s) AND LENGTH(COALESCE(content,'')) < ? "
                "LIMIT ?" % placeholders,
                (*wanted, MIN_ARTICLE_CHARS, cap)).fetchall()
        except sqlite3.OperationalError as exc:
            print("  定向查询失败: %s" % exc)
            corpus.close()
            return 2
    else:
        rows = candidates(conn, args.limit, hosts)
    print("=" * 84)
    print("full-text backfill")
    print("=" * 84)
    print("  candidates: %d (summary-only, article-like URL)%s"
          % (len(rows), " for " + ", ".join(hosts) if hosts else ""))
    for r in rows[:8]:
        print("    %-46s %s" % ((r["url"] or "")[:46], (r["title"] or "")[:28]))
    if len(rows) > 8:
        print("    ... and %d more" % (len(rows) - 8))

    if args.dry_run:
        print()
        print("  dry run - nothing fetched")
        corpus.close()
        return 0

    extractor = TrafilaturaExtractor(TrafilaturaExtractorConfig())
    sem = asyncio.Semaphore(args.concurrency)
    started = time.monotonic()
    ok = short = failed = 0
    chars = 0
    # 抓取成功的记录，用于落盘。见函数末尾的 cache 写入。
    saved: list = []
    # Per-host tallies, so the run reports *where* text was recoverable instead
    # of one aggregate rate that hides "cnbeta works, oschina never does".
    host_ok: dict = {}
    host_fail: dict = {}

    def _host(url: str) -> str:
        try:
            return url.split("://", 1)[1].split("/", 1)[0]
        except IndexError:
            return "?"

    async def one(client, row):
        nonlocal ok, short, failed, chars
        host = _host(row["url"])

        # 抓取本身可能抛异常（PDF 无法解析、403、DNS 失败）。原来异常会直接
        # 冒出去，`one()` 中断，**失败标记写不进去** —— 那一行就永远留在
        # 候选队列里。实测 8 批 5,000 条的运行几乎全部空转：队首是
        # anthropic.com 的 PDF 系统卡，trafilatura 解析不了，每次都抛，
        # 每次都没标记，于是下一批从同一批 PDF 重新开始。
        try:
            async with sem:
                # 去掉追踪参数后再请求：CSDN 带 ops_request_misc 会返回 521
                text = await extractor.extract(
                    normalize_url(row["url"]), client)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            host_fail[host] = host_fail.get(host, 0) + 1
            try:
                mark_fulltext_failure(conn, row["id"], {"reason": type(exc).__name__})
            except Exception:
                pass
            return

        n = len(text or "")

        # 抓回来一大坨不是文章，是整站/整本论文集。存进去会污染检索：
        # 一条 148 万字的行含有几乎所有关键词，任何查询都会命中它。
        # 标记成失败而不是静默截断 —— 截断后的 6 万字仍然不是"一篇文章"。
        if n > MAX_ARTICLE_CHARS:
            failed += 1
            host_fail[host] = host_fail.get(host, 0) + 1
            mark_fulltext_failure(conn, row["id"], {"chars": n, "reason": "too_long"})
            return

        if n >= MIN_ARTICLE_CHARS:
            record = {"id": row["id"], "url": row["url"], "chars": n, "text": text}
            if recover_body(conn, record):
                ok += 1
                chars += n
                host_ok[host] = host_ok.get(host, 0) + 1
                saved.append(record)
            else:
                failed += 1
                host_fail[host] = host_fail.get(host, 0) + 1
        else:
            if n > 0:
                short += 1
            else:
                failed += 1
            host_fail[host] = host_fail.get(host, 0) + 1
            # Record the attempt even though it failed.
            #
            # Without this the candidate query re-selects the same failures on
            # every run, and the pass loops forever on the head of the queue:
            # four consecutive 8,000-item batches recovered 3,079 items on the
            # first and then reported an identical 3,080 on the next three -
            # every one of those batches spent its budget re-fetching the same
            # dead URLs. A marker makes the queue advance.
            #
            # It is a separate key from `fulltext` so `--report` can still tell
            # "recovered" from "tried and got nothing", and so a future run can
            # deliberately retry the `fulltext_failed` set if the extractor
            # improves.
            mark_fulltext_failure(conn, row["id"], {"chars": n})


    async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
        for i in range(0, len(rows), 20):
            chunk = rows[i:i + 20]
            await asyncio.gather(*[one(client, r) for r in chunk])
            conn.commit()
            done = ok + short + failed
            print("  %4d/%d  ok=%-4d short=%-3d failed=%-3d  %5.0fs"
                  % (done, len(rows), ok, short, failed,
                     time.monotonic() - started))

    print()
    print("=" * 84)
    print("result")
    print("=" * 84)
    print("  recovered %d items, %d chars total (avg %d)"
          % (ok, chars, chars // max(1, ok)))
    print("  too short %d, failed %d" % (short, failed))
    if host_ok or host_fail:
        print()
        print("  per host (ok / failed):")
        for host in sorted(set(host_ok) | set(host_fail),
                           key=lambda h: -(host_ok.get(h, 0) + host_fail.get(h, 0))):
            print("    %-40s %3d / %3d"
                  % (host[:40], host_ok.get(host, 0), host_fail.get(host, 0)))
    print()
    report(conn)

    # ---------- 落盘 ----------
    # 抓到的正文必须写进语料库**之外**的文件，否则重建语料就全丢：
    # 实测抓了 9,942 条正文（6,700 万字符，约 14 小时机器时间）之后
    # 按新的标题/日期规则重建了一次 corpus.db，`fulltext` 标记归零，
    # 全部重新变回「只有标题」。
    #
    # `ingest_all.py` 会读取这个文件，按 id 把正文合并回对应行。
    # 追加写而不是覆盖，这样多次运行的结果累积。
    if saved:
        # 写入方和读取方（tools/ingest_all.py）现在共用同一个路径函数。
        # 曾经这里写 ROOT/fulltext_cache.jsonl，而 ingest 读 cache 目录，
        # 两个文件都存在、都不报错，抓到的正文一次也没合并进语料。
        cache_path = paths.fulltext_cache()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}
        if cache_path.exists():
            try:
                with cache_path.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            cached = json.loads(line)
                            existing[cached["id"]] = hashlib.sha256(cached["text"].strip().encode("utf-8")).hexdigest()
                        except Exception:
                            continue
            except Exception:
                existing = {}
        added = 0
        with cache_path.open("a", encoding="utf-8") as fh:
            for rec in saved:
                content_hash = hashlib.sha256(rec["text"].strip().encode("utf-8")).hexdigest()
                if existing.get(rec["id"]) == content_hash:
                    continue
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                existing[rec["id"]] = content_hash
                added += 1
        print("  cached %d new bodies -> %s" % (added, cache_path.name))

    corpus.close()
    return 0


if __name__ == "__main__":
    force_utf8_output()
    raise SystemExit(asyncio.run(main()))

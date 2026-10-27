"""Normalize collector shards and explicitly persist them to the evidence corpus."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import hashlib
import json
import re
import sys

from periscope.collection.families import KIND_TO_SOURCE, SOURCE_KEY_TO_SOURCE, PLATFORM_TO_SOURCE
from periscope.runtime import paths
from src._cli import force_utf8_output
from src.corpus.store import Corpus
from src.models import ContentItem, Section, SourceType
from .materials import body_sections, captured_metadata, discussion_sections
from .fulltext import apply_cache


# No `sys.stdout = io.TextIOWrapper(...)` at import time. It used to be here,
# and it made this module impossible to import from a process that owns stdout:
# pytest's capture object was replaced, and the run died at shutdown with
# "I/O operation on closed file" - a crash caused by an import. `--fulltext-only`
# is now reachable in-process (see `merge_fulltext_only`), so the import has to
# be safe. The `__main__` path still calls `force_utf8_output()` below.

# 采集分片优先读取 collect/ 与 shards/，兼容未整理检出目录中的历史根目录。

ROOT = paths.ROOT

DATA_DIRS = [paths.COLLECT_DIR, paths.SHARD_DIR, ROOT]

HTTP_ITEMS = paths.COLLECT_DIR / "http_items.json"
# Per-source shards written by a filtered `collector.py <keys>` run. Read in
# addition to the merged file, never instead of it.
HTTP_SEARCH_GLOB = "http_items.*.json"
# 正文缓存。`backfill_fulltext.py` 抓到文章后写这里，一行一个 JSON。
#
# 为什么需要它：抓到的正文原本只写进 corpus.db 的 `content`/`claimable`
# 列，而 corpus.db 是每次 `ingest_all.py` 都会重建的派生数据。实测抓了
# 9,942 条正文（6,700 万字符，约 14 小时机器时间）后重建语料，全部丢失。
# 真实来源是 http_items*.json 和 ugc_*.json，它们没有正文。
#
# 所以正文必须以文件形式存在源头这一侧，重建时再合并回去。
#
# 这里存的是**函数**而不是常量。写成常量的那版在两个可写路径之间漂移
# 过（写入方用仓库根，读取方用 cache 目录），谁也没报错，结果 3,648 条
# 正文一次都没被合并进来。`paths.fulltext_cache()` 是唯一知道它在哪的
# 地方，并且它会顺手把历史位置的文件迁到规范位置。
#
# 延迟求值：迁移是一次 24 MB 的文件搬动，不该发生在 import 的时候。
def fulltext_cache() -> "Path":
    return paths.fulltext_cache()
UGC_ITEMS = paths.COLLECT_DIR / "ugc_items.json"
# Browser search results land in their own file, written incrementally per
# platform. They carry the same row shape as ugc_items (`platform` naming which
# site produced them), so they merge into the same list rather than needing a
# second code path.
UGC_SEARCH_GLOB = "ugc_search*.json"
# The merged, de-duplicated view across every platform file. Optional: when it is
# absent the individual files are read instead, so this is an optimisation rather
# than a requirement.
UGC_ALL = paths.COLLECT_DIR / "ugc_all.json"

# ContentItem validates `url` at 2,083 characters, which is also the practical
# browser limit. Platform search results regularly exceed it because every
# tracking parameter is appended, so a row is trimmed rather than dropped.
MAX_URL_CHARS = 2000

# A title longer than this is a body that was stored in the wrong field. See
# `_split_title` for what that costs the ranking.
MAX_TITLE_CHARS = 120


def _split_title(title: str, body: str) -> tuple[str, str]:
    """Separate a headline from a body that arrived in the `title` field.

    Measured on this corpus: **7,616 rows** had `title` at least as long as
    `content` and over 100 characters - 6,499 of them twitter, 990 掘金. The
    collectors for those two sources put the post text in `title`, so the
    ranking saw a "title" that was really a whole paragraph.

    That is not cosmetic. `core/search.py` gives a bonus when a query term
    appears in the title, on the theory that a headline naming the subject is
    stronger evidence than a body mentioning it. With a paragraph in the title
    the bonus fires for any post that mentions the term anywhere, and a
    stock-market recap's last sentence about 固态电池 ranked it 6th for a battery
    technology query.

    The split is deliberately conservative:

    - Only applied when the field is longer than `MAX_TITLE_CHARS` *and* there
      is no separate body, so a legitimate long headline is left alone.
    - Cut at the first sentence boundary, then the first clause separator, so
      what stays in `title` reads like a headline.
    - Everything dropped from the title moves into the body rather than being
      discarded: it is the author's text and the retrieval layer may want it.

    Returns `(title, body)`.
    """
    title = (title or "").strip()
    if len(title) <= MAX_TITLE_CHARS or body.strip():
        return title, body

    # Find the earliest usable boundary among all separators, not the first
    # *pattern* that matches anywhere.
    #
    # The first version tried sentence-enders first, then clause separators, and
    # took the first match of whichever pattern hit. On a 127-character post
    # ending in `。`, the sentence pattern matched at position 126 - so nothing
    # was split and the whole paragraph stayed a "title". Trying the patterns in
    # order is the wrong shape for this: the question is "where is the earliest
    # good cut", and every separator is a candidate for it.
    #
    # Each match is judged on its own position:
    #   - at least 8 characters in, so the title is not a single word;
    #   - at most `MAX_TITLE_CHARS` in, so the title stays a headline.
    # Among the survivors, the earliest wins - a headline ends at the first
    # sentence, not at the last one.
    #
    # `；` (fullwidth) is a Chinese sentence break; `;` (ASCII) is not. A
    # regulatory headline reads
    # `Self-Regulatory Organizations; Financial Industry Regulatory Authority`,
    # and splitting on the ASCII semicolon left a 29-character title that cut an
    # institution's name in half. The fullwidth form is listed and the ASCII one
    # is deliberately not.
    best = -1
    for pattern in (r"[！？。!?…\n]", r"[，,｜|；]"):
        for m in re.finditer(pattern, title[:MAX_TITLE_CHARS + 1]):
            if 8 <= m.start() <= MAX_TITLE_CHARS:
                if best < 0 or m.start() < best:
                    best = m.start()
                break          # this pattern's earliest usable match
    cut = best
    if cut < 0:
        # English prose often runs past a sentence with no terminator at all
        # ("How the Claude Code team does code review Code review's second job
        # has..."): two sentences concatenated with a space and no period.
        # Fall back to the last word boundary before the cap, so the title is a
        # phrase rather than a hard mid-word cut.
        window = title[:MAX_TITLE_CHARS]
        space = window.rfind(" ")
        if space >= 20:
            head, rest = window[:space].strip(), title[space + 1:].strip()
            if head and rest:
                return head, (rest + " " + body).strip() if body else rest
        return title, body
    head = title[:cut].strip()
    rest = title[cut + 1:].strip()
    if not head:
        return title, body
    return head, (rest + " " + body).strip() if body else rest
# `data/` is not writable under the current sandbox - the repository's own
# scratch directory is, which is the same reason src/_scratch.py exists.
# `core.paths` is the single switch: set PERISCOPE_DATA_DIR to move the corpus
# without touching this file.
CORPUS = paths.CORPUS

# Collector `kind` -> Periscope SourceType. The mapping is deliberate and
# incomplete: anything without an honest home is skipped, because guessing would
# hand a source the wrong credibility prior and the wrong independence family.


def _when(raw: str) -> datetime:
    """Parse whatever date a source gave, else now. Never raise on a bad date.

    Unix timestamps are handled explicitly, and that is not a nicety: Stack
    Exchange returns `creation_date` as epoch seconds and Bilibili returns
    `pubdate` the same way, so 400 items were silently stamped with the ingest
    time. The failure is quiet by construction - an unparseable date falls back
    to `now()`, which looks like a plausible value in every report - so it was
    only visible by reading the raw rows back.
    """
    text = (raw or "").strip()
    if text:
        # Epoch seconds (or milliseconds) arrive as a bare integer.
        if re.fullmatch(r"\d{9,13}", text):
            value = int(text)
            if value > 10 ** 11:          # milliseconds
                value //= 1000
            try:
                return datetime.fromtimestamp(value, timezone.utc)
            except (OverflowError, OSError, ValueError):
                pass
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %z"):
            try:
                dt = datetime.strptime(text.replace("Z", "+0000"), fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            pass
        # RFC 822 with a named zone. Google News writes
        # `Thu, 15 Oct 2026 07:00:00 GMT`, which `%z` does not accept, so all
        # 31,685 rows fell through to `now()` and the 36-month backfill landed
        # in a single month. `parsedate_to_datetime` understands both `GMT` and
        # the numeric forms, so it is tried before giving up.
        try:
            from email.utils import parsedate_to_datetime

            dt = parsedate_to_datetime(text)
            if dt is not None:
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _when_optional(raw: Any) -> Optional[datetime]:
    """像 `_when` 一样解析，但解析不出来时返回 None 而不是 `now()`。

    平台搜索行的时间戳可信度差别很大：
      B站      `pubdate` 是 epoch 秒           -> 精确
      微博     `Sun Oct 18 12:55:15 +0800 2026` -> 精确
      小红书   `昨天 20:25` / `3天前`           -> 相对，能算
      抖音     epoch 秒                         -> 精确
      快手     无日期                           -> None

    原来 UGC 侧无条件写 `now()`，所有平台内容都变成「抓取当天」。
    实测「固态电池」前 20 条里 18 条日期相同，报告上就成了
    「今天突然出现 18 条固态电池内容」——这是采样的假象，不是真实信号。

    返回 None 时 `ContentItem` 会退回 `fetched_at`，语义上诚实地区分了
    「源自己说的时间」与「我们抓到的时间」。
    """
    text = (raw or "").strip()
    if not text:
        return None

    # 相对时间：昨天/今天/前天 + 可选时分
    m = re.match(r"^(昨天|今天|前天)\s*(\d{1,2}):(\d{2})?$", text)
    if m:
        base = datetime.now(timezone.utc)
        delta = {"今天": 0, "昨天": 1, "前天": 2}[m.group(1)]
        base = base - timedelta(days=delta)
        hour = int(m.group(2) or 0)
        minute = int(m.group(3) or 0)
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # N 天/小时前
    m = re.match(r"^(\d+)\s*(天|小时|分钟)前$", text)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        delta = {"天": timedelta(days=n),
                 "小时": timedelta(hours=n),
                 "分钟": timedelta(minutes=n)}[unit]
        return datetime.now(timezone.utc) - delta

    # 纯月-日（小红书常见 `08-30`）：补当前年份
    m = re.match(r"^(\d{1,2})-(\d{1,2})$", text)
    if m:
        now = datetime.now(timezone.utc)
        try:
            dt = now.replace(month=int(m.group(1)), day=int(m.group(2)),
                             hour=0, minute=0, second=0, microsecond=0)
            if dt > now:      # 未来日期 -> 是去年的
                dt = dt.replace(year=now.year - 1)
            return dt
        except ValueError:
            return None

    # 其余交给 _when；但要判断它是否 fallback 到了 now()
    parsed = _when(text)
    now = datetime.now(timezone.utc)
    if abs((parsed - now).total_seconds()) < 120:
        # _when 在解析失败时会返回 now()，这里视为「没有日期」
        return None
    return parsed


def _stable_id(namespace: str, identity: str) -> str:
    """A content id that survives a process restart.

    Python's `hash()` is randomised per process (PYTHONHASHSEED), so an id built
    from it changes for the same row on every run. When that happened on the UGC
    side, one 哔哩哔哩 space page was stored 20 times and the corpus doubled to
    203,494 rows while the source files held 90,937. The HTTP side still had the
    old form, and a second ingest of the same `http_items.json` added all 91,016
    rows again - 70,586 hackernews rows became 141,068.

    A sha1 digest is stable across processes, which is what an identity needs.
    """
    digest = hashlib.sha1(
        ("%s\x00%s" % (namespace, identity)).encode("utf-8")).hexdigest()[:16]
    return "%s:%s" % (namespace, digest)


def _v2ex_discussion_version(row):
    comments = [{key: comment.get(key) for key in ("id", "text", "user", "parent_id", "created_at")}
                for comment in row.get("comments") or [] if isinstance(comment, dict)]
    content = {"title": row.get("title") or "", "text": row.get("text") or "",
               "author": row.get("author") or "", "comments": comments}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _http_row_identity(row):
    version = ""
    if row.get("source") == "web_crawl":
        document = row.get("document") if isinstance(row.get("document"), dict) else {}
        body = max((row.get("body") or "", row.get("text") or ""), key=len).strip()
        _, body = _split_title((row.get("title") or "").strip(), body)
        version = document.get("sha256") or hashlib.sha256(body.encode("utf-8")).hexdigest()
    elif row.get("source") == "v2ex_search" and (row.get("topic_detail") or {}).get("topic_id"):
        version = _v2ex_discussion_version(row)
    return row.get("source"), row.get("url") or row.get("title"), version


def build(http_rows, ugc_rows):
    """Turn collector rows into ContentItems, dropping what cannot be filed."""
    items = []
    skipped = Counter()

    for row in http_rows:
        kind = row.get("kind", "")
        src_key = row.get("source", "")
        source = SOURCE_KEY_TO_SOURCE.get(src_key) or KIND_TO_SOURCE.get(kind)
        title = (row.get("title") or "").strip()
        url = (row.get("url") or "").strip()
        if source is None:
            skipped["unmapped kind: %s" % kind] += 1
            continue
        if not title and not url:
            skipped["no title and no url"] += 1
            continue
        # pydantic rejects a URL past 2,083 characters, and one Google News
        # article link measured 2,105 - a single row would abort the whole
        # ingest. Trim at a parameter boundary when one exists; Google News has
        # none (the target is one base64 path segment), so fall back to a hard
        # cut rather than lose the batch.
        if len(url) > MAX_URL_CHARS:
            cut = url[:MAX_URL_CHARS]
            amp = cut.rfind("&")
            if amp > MAX_URL_CHARS - 400:
                cut = cut[:amp]
            skipped["url truncated"] += 1
            url = cut
        body = max((row.get("body") or "", row.get("text") or ""), key=len).strip()
        title, body = _split_title(title, body)
        provenance = row.get("provenance") or {}
        tier = "community" if provenance.get("content_role") == "community" else "primary"
        metadata = captured_metadata(row)
        metadata.update(source_key=src_key, source_kind=kind)
        identity = url or title
        if src_key == "web_crawl":
            document = row.get("document") if isinstance(row.get("document"), dict) else {}
            version = document.get("sha256") or hashlib.sha256(body.encode("utf-8")).hexdigest()
            identity += "\x00" + version
        elif src_key == "v2ex_search" and (row.get("topic_detail") or {}).get("topic_id"):
            identity += "\x00" + _v2ex_discussion_version(row)
        items.append(ContentItem(
            id=_stable_id(row.get("source", "src"), identity),
            source_type=source,
            title=title or url,
            url=url if url.startswith("http") else None,
            locator=url or title,
            author=(row.get("author") or "").strip() or None,
            content=body or title,
            published_at=_when_optional(row.get("date", "")),
            sections=[*body_sections(body or title, tier), *discussion_sections(row, url or title)],
            metadata=metadata,
        ))

    for row in ugc_rows:
        key = row.get("platform", "")
        source = PLATFORM_TO_SOURCE.get(key)
        title = (row.get("title") or "").strip()
        if source is None:
            skipped["unmapped platform: %s" % key] += 1
            continue
        if not title:
            skipped["empty platform title"] += 1
            continue
        # A platform post is written by a member of the crowd, not by the item's
        # author, so it is declared `community`. The claim pipeline reads only
        # `primary`, which is what stops "someone on 微博 said X" becoming an
        # atomic claim about the world.
        #
        # The URL is trimmed to what identifies the post. B站 search returns
        # 2,000+ character URLs full of tracking parameters, and pydantic rejects
        # anything past 2,083 - so keeping the whole string loses the entire row
        # over a query string nobody needs. Truncation happens at a boundary that
        # keeps the scheme and host intact.
        raw_url = (row.get("url") or "").strip()
        if raw_url.startswith("http"):
            if len(raw_url) > MAX_URL_CHARS:
                cut = raw_url[:MAX_URL_CHARS]
                # Prefer to cut at a query-parameter boundary, but only when one
                # sits in the tail - Google News encodes the article target as
                # one long base64 path segment with no `&` after it, and cutting
                # at the *last* `&` found anywhere would throw away the path and
                # keep a query string that identifies nothing.
                amp = cut.rfind("&")
                if amp > MAX_URL_CHARS - 400:
                    cut = cut[:amp]
                skipped["url truncated"] += 1
                raw_url = cut
            url_value = raw_url
        else:
            url_value = None

        # `author` when the collector captured a byline, else the platform's own
        # name. Without either, `publisher_of` derives nothing and the row casts
        # no independence vote - which is how 4,249 platform items ended up with
        # `publisher = NULL`.
        #
        # Using the platform name is not a guess: on 知乎 the venue *is* the
        # publisher of a question, the same way a forum is the publisher of a
        # thread. It does mean 4,249 items share seven publishers rather than
        # 4,249 individuals, and that is the honest reading - it says "知乎 has
        # 1,700 posts about this", not "1,700 independent people agree".
        author = (row.get("author") or "").strip() or (row.get("platform_name") or key)

        # A stable id, derived from the row's identity rather than from Python's
        # `hash()`. See `_stable_id` for what the randomised version cost.
        digest = hashlib.sha1(
            ("%s\x00%s" % (key, title)).encode("utf-8")).hexdigest()[:16]

        # 平台搜索行带 `date` 字段（微博 `created_at`、小红书 `昨天 20:25`、
        # B站 `pubdate` 是 epoch）。原来这里无条件写 `now()`，后果是所有平台
        # 内容的时间都变成「抓取当天」—— 实测「固态电池」前 20 条里有 18 条
        # 日期相同，报告里就出现了「今天突然有 18 条固态电池内容」这种假信号。
        #
        # 有 date 就解析；解析不出来就留 None，让 `ContentItem` 退回 `fetched_at`，
        # 这样 `time_basis` 能区分「源自己说的时间」和「我们抓到的时间」。
        pub = _when_optional(row.get("date", ""))

        # The same title-is-the-body problem the HTTP branch has, and here it is
        # the dominant case: 6,499 twitter rows and 990 掘金 rows stored the post
        # text in `title`. Split before building the item so both `title` and
        # `content` end up in the right fields.
        ugc_body = (row.get("text") or "").strip()
        title, ugc_body = _split_title(title, ugc_body)
        content = ugc_body or title

        items.append(ContentItem(
            id="ugc:%s:%s" % (key, digest),
            source_type=source,
            title=title,
            url=url_value,
            locator=raw_url or title,
            author=author,
            content=content,
            published_at=pub,
            sections=[*body_sections(content, "community"), *discussion_sections(row, raw_url or title)],
            metadata=captured_metadata(row),
        ))
    return items, skipped


def merge_fulltext_only() -> int:
    """只把 `fulltext_cache.jsonl` 合并进语料，不动采集数据。

    正文抓取是独立于采集的一步：抓完写缓存，然后需要把正文落到 items 的
    `content`/`claimable` 上。全量 `ingest_all.py` 会重读 130MB 分片并
    重建索引（约 100 秒），而这里只做一次 `executemany` UPDATE（秒级）。
    """
    ft_path = fulltext_cache()
    if not ft_path.exists():
        print("fulltext cache 不存在: %s" % ft_path)
        return 0
    corpus = Corpus(str(CORPUS))
    try:
        applied = apply_cache(corpus, ft_path)
        print("fulltext merged: %d records" % applied)
        return 0
    except (OSError, ValueError) as exc:
        print("fulltext cache merge failed: %s" % exc)
        return 1
    finally:
        corpus.close()


def main() -> int:
    if "--help" in sys.argv or "-h" in sys.argv:
        parser = argparse.ArgumentParser(description="将采集分片显式导入证据语料库")
        parser.add_argument("--only", metavar="FILE", help="只导入指定分片")
        parser.add_argument("--fulltext-only", action="store_true", help="只合并已有正文缓存")
        parser.add_argument("--dry-run", action="store_true", help="预览待导入内容")
        parser.print_help()
        return 0
    dry = "--dry-run" in sys.argv

    # `--only <file>` 只入库一个分片，用于「任意话题」的增量路径。
    #
    # 全量重建要读 130MB 的 JSON、重建全部索引、重算 18 万行聚类，
    # 实测约 4 分钟。而话题检索只需要把新采的那一个分片并进去。
    # 有了这个开关，新增一个话题的入库是秒级而不是分钟级。
    only = ""
    for i, a in enumerate(sys.argv):
        if a == "--only" and i + 1 < len(sys.argv):
            only = sys.argv[i + 1]
        elif a.startswith("--only="):
            only = a.split("=", 1)[1]

    # `--fulltext-only` 只把正文缓存合并进已有行，不碰采集数据。
    # 「任意话题」的流程需要它：先入库拿到行，再抓正文写缓存，
    # 最后只合并这一件事 —— 重新读 130MB 的 JSON 毫无意义。
    if "--fulltext-only" in sys.argv:
        return merge_fulltext_only()

    # HTTP rows: the merged file plus every per-source shard.
    #
    # `collector.py` writes `http_items.<keys>.json` for a filtered run, exactly
    # like the UGC collector does, so that a one-source test cannot clobber a
    # full harvest. Reading only `http_items.json` meant every shard was written
    # and then silently ignored: ten new sources were measured at 2,990 + 3,720
    # + 3,371 + 31,685 rows and contributed **zero** to the corpus. That is the
    # same failure the UGC side already had a fix for; this is that fix applied
    # to HTTP.
    #
    # De-duplication is by (source, url) rather than by title, because the same
    # title legitimately appears under different sources and both are evidence.
    http_rows = []
    http_files = []
    seen_http = set()
    # Initialised here, not after the incremental branch: `--only
    # ugc_search.*.json` fills `ugc_rows` inside that branch, and a later
    # unconditional `ugc_rows = []` used to wipe it (1 row read, 0 offered).
    ugc_rows: list = []
    ugc_files: list = []

    def _add_http(chunk, name):
        added = 0
        for row in chunk:
            key = _http_row_identity(row)
            if key in seen_http:
                continue
            seen_http.add(key)
            http_rows.append(row)
            added += 1
        if added:
            http_files.append("%s(%d)" % (name, added))

    if only:
        # ---------- 增量路径 ----------
        # 只处理这一个分片。
        #
        # 早先的 `--only` 只跳过聚类，仍然把 174,424 行全部重建、逐条
        # 试插。实测 109 秒里绝大部分时间花在「已存在的行再确认一次存在」
        # 上，每批 `stored 0`。
        #
        # 分片内的行按 (source, url) 去重后直接插入，已存在的由主键冲突
        # 跳过。全量文件不读，因此不会把别的分片抹掉 —— 它们已经在库里。
        target = ROOT / only
        if not target.exists() and not Path(only).is_absolute():
            target = next((base / only for base in DATA_DIRS if (base / only).is_file()), target)
        if not target.exists():
            print("  --only 指定的文件不存在: %s" % only)
            return 2
        try:
            # utf-8-sig accepts both plain UTF-8 and the BOM that Windows
            # editors (and PowerShell's `Set-Content -Encoding utf8`) prepend.
            chunk = json.loads(target.read_text(encoding="utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            print("  --only 读取失败: %s" % exc)
            return 2
        # A UGC shard (`ugc_search.<platform>.json`) has `platform` rows, not
        # `source` rows. Routing it through the HTTP branch would silently file
        # every platform post under the wrong family -- or skip it as an
        # unmapped kind. Detect the shard kind by filename.
        is_ugc = Path(only).name.startswith("ugc_")
        if is_ugc:
            ugc_rows = []
            seen_ugc = set()
            for row in chunk:
                key = (row.get("platform"), row.get("url") or row.get("title"))
                if key in seen_ugc:
                    continue
                seen_ugc.add(key)
                ugc_rows.append(row)
            print("  incremental (ugc): %s -> %d rows" % (only, len(ugc_rows)))
            http_rows = []
            ugc_files = ["%s(%d)" % (only, len(ugc_rows))]
        else:
            for row in chunk:
                key = _http_row_identity(row)
                if key in seen_http:
                    continue
                seen_http.add(key)
                http_rows.append(row)
            print("  incremental (http): %s -> %d rows" % (only, len(http_rows)))
            ugc_rows = []
            ugc_files = []
    else:
        # ---------- 全量路径 ----------
        if HTTP_ITEMS.exists():
            _add_http(json.loads(HTTP_ITEMS.read_text(encoding="utf-8")),
                      HTTP_ITEMS.name)
        # 两处都扫：老位置根目录 + 新位置 shards
        seen_files = {HTTP_ITEMS.resolve()}
        for base in DATA_DIRS:
            if not base.exists():
                continue
            for path in [base / "http_items.json", *sorted(base.glob(HTTP_SEARCH_GLOB))]:
                if not path.is_file() or path.resolve() in seen_files:
                    continue
                seen_files.add(path.resolve())
                try:
                    chunk = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(chunk, list):
                    continue
                _add_http(chunk, path.name)
        print("http rows: %d from %d file(s)"
              % (len(http_rows), len(http_files)))
        print("  http files: %s" % ", ".join(http_files[-8:]))

    # Every UGC file: the hot-list pass plus any per-platform search files. The
    # search collector writes `ugc_search.<keys>.json` so a filtered run cannot
    # clobber a full one, and those rows are just as much platform evidence as
    # the hot lists.
    # Every UGC file: the hot-list pass plus any search files. The search
    # collector writes per-platform files so a filtered run cannot clobber a full
    # one, and those rows are just as much platform evidence as the hot lists.
    #
    # Rows are de-duplicated here rather than in each writer, because the same
    # post legitimately appears in several files: a keyword matrix overlaps with
    # itself, and a hot list overlaps with a search. Without this the corpus
    # stores one thread once per keyword that found it, which inflates every
    # "how many voices" count downstream.
    #
    # Do NOT reset `ugc_rows` here: in `--only ugc_*` mode it was already filled
    # by the incremental branch above.
    seen_ugc = set()

    def _add_ugc(chunk, name):
        added = 0
        for row in chunk:
            key = (row.get("platform"), (row.get("title") or "")[:120])
            if key in seen_ugc:
                continue
            seen_ugc.add(key)
            ugc_rows.append(row)
            added += 1
        if added:
            ugc_files.append("%s(%d)" % (name, added))

    # 增量模式不碰 UGC 文件：平台内容与话题无关（它来自热榜与既有检索），
    # 每次话题查询都重读 5MB 是无意义的开销。
    if not only:
        if UGC_ALL.exists():
            try:
                _add_ugc(json.loads(UGC_ALL.read_text(encoding="utf-8")),
                         UGC_ALL.name)
            except Exception:
                pass
        if UGC_ITEMS.exists():
            _add_ugc(json.loads(UGC_ITEMS.read_text(encoding="utf-8")),
                     UGC_ITEMS.name)
    if not only:
        seen_ugc_files = {UGC_ALL.resolve(), UGC_ITEMS.resolve()}
        for base in DATA_DIRS:
            # The organizer retains colliding historical files with a .legacy-
            # hash suffix. They remain evidence and must be read as well.
            candidates = [base / "ugc_all.json", base / "ugc_items.json",
                          *sorted(base.glob("ugc_all.legacy-*.json")),
                          *sorted(base.glob("ugc_items.legacy-*.json")),
                          *sorted(base.glob(UGC_SEARCH_GLOB))]
            for path in candidates:
                if not path.is_file() or path.resolve() in seen_ugc_files:
                    continue
                seen_ugc_files.add(path.resolve())
                try:
                    chunk = json.loads(path.read_text(encoding="utf-8-sig"))
                except Exception:
                    continue
                if isinstance(chunk, list):
                    _add_ugc(chunk, path.name)
        print("http rows: %d   ugc rows: %d (deduped)"
              % (len(http_rows), len(ugc_rows)))
        print("  ugc sources: %s" % ", ".join(ugc_files[-8:]))

    if dry:
        items, skipped = build(http_rows, ugc_rows)
        print("built items: %d" % len(items))
        for reason, n in skipped.most_common(8):
            print("   skipped %-34s %d" % (reason, n))
        print("dry run - nothing written")
        return 0

    CORPUS.parent.mkdir(parents=True, exist_ok=True)
    corpus = Corpus(str(CORPUS))
    before = corpus.stats()
    run_id = corpus.begin_run(
        datetime.now(timezone.utc) - timedelta(days=30))

    # Insert in batches rather than building the whole collection first.
    #
    # The one-shot version materialised all 94,565 pydantic items plus a tuple
    # per row before the first insert, which on a machine with 2.9 GB free fails
    # inside SQLite - and SQLite reports the resulting allocation failure as
    # `unable to open database file`, which points at the wrong thing entirely.
    # A batch keeps peak memory flat in the batch size instead of the collection
    # size, so the same code ingests 20k or 200k rows.
    BATCH = 5000
    new = 0
    skipped: Counter = Counter()
    total_offered = 0
    batches = 0
    for start in range(0, len(http_rows), BATCH):
        chunk = http_rows[start:start + BATCH]
        items, dropped = build(chunk, [])
        skipped.update(dropped)
        total_offered += len(chunk)
        new += corpus.add_items(items, run_id)
        batches += 1
        del items
        print("  batch %-4d rows %6d-%-6d  stored %6d"
              % (batches, start, start + len(chunk), new), end="\r")
    if ugc_rows:
        items, dropped = build([], ugc_rows)
        skipped.update(dropped)
        total_offered += len(ugc_rows)
        new += corpus.add_items(items, run_id)
        del items
    print(" " * 72, end="\r")

    # ---------- 合并正文缓存 ----------
    # 语料库是派生数据，每次重建都从 JSON 源头重新生成；正文只在缓存里。
    # 这里按 id 把缓存中的正文写回，让「重建语料」不会丢掉抓取成果。
    ft_applied = 0
    ft_path = fulltext_cache()
    if ft_path.exists():
        try:
            ft_applied = apply_cache(corpus, ft_path)
        except Exception as exc:  # noqa: BLE001
            print("  fulltext cache merge failed: %s" % exc)
    if ft_applied:
        print("  fulltext cache: %d bodies merged" % ft_applied)

    # `recompute_clusters` defaults to re-grouping only the newest 500 rows,
    # which bounds the cost of a daily run. A backfill is the opposite case: with
    # the window, this ingest would cluster the newest 500 and leave the rest
    # ungrouped forever, so near-duplicates would never collapse and every
    # downstream "how many sources agree" count would read high. `None` groups
    # the whole table, and the banded LSH path keeps that affordable at this size.
    # 增量模式（--only）跳过错全量聚类：新行自己会被 `add_items` 分配
    # 初始 cluster_id，而全量重算只是为了把跨批次的近重复合并起来。
    # 代价是本次新增的行可能与既有行构成重复而未被合并 —— 下一次全量运行
    # 会修好。对于一个「随手问个话题」的路径，秒级响应比完美聚类重要。
    if only:
        clusters = 0
        print("  (incremental: skipped cluster recompute)")
    else:
        clusters = corpus.recompute_clusters(lookback_rows=None)
    corpus.finish_run(run_id, items_new=new, items_total_seen=total_offered)
    after = corpus.stats()
    corpus.close()

    print()
    print("=" * 76)
    print("ingested")
    print("=" * 76)
    print("  items before : %d" % before.get("items", 0))
    print("  items after  : %d" % after.get("items", 0))
    print("  newly stored : %d  (of %d offered)" % (new, total_offered))
    print("  clustered    : %d rows changed cluster" % clusters)
    print("  clusters     : %s" % after.get("clusters"))
    print("  by source    : %s" % after.get("by_source"))
    if skipped:
        for reason, n in skipped.most_common(6):
            print("  skipped %-38s %d" % (reason, n))

    dupes = total_offered - new
    if total_offered:
        print("  duplicate rate: %.1f%%" % (100.0 * dupes / total_offered))
    print()
    print("  corpus: %s" % CORPUS)
    return 0


if __name__ == "__main__":
    force_utf8_output()
    raise SystemExit(main())

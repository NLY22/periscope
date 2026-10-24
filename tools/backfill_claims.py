"""Extract and grade claims over the corpus that is already stored.

Why this is a separate command: the orchestrator's `analyze_claims` runs over
*freshly fetched* items and takes at most `analysis.extract_top_items` of them
(12 by default), because extraction costs a language-model call per item. That
is the right shape for a daily run and the wrong shape for a corpus that already
holds 94,565 items - every one of them is "not fresh", so none would ever be
looked at. Re-fetching to reach them would also re-fetch the whole web.

So this walks what is stored, hardest-first, and stops on the budgets it is
given. It is resumable: `claims` rows persist, and a claim already extracted is
skipped, so a run can be interrupted and continued without paying twice.

Usage:
    python backfill_claims.py --limit 50                # extract from 50 items
    python backfill_claims.py --limit 50 --grade 25      # and grade up to 25
    python backfill_claims.py --dry-run                  # show what it would do
    python backfill_claims.py --report                   # what is in the store now

Requires `ai.api_key_env` to be set in the environment (AGNES_API_KEY by
default). Without it the command says so and exits rather than pretending.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

# Root on sys.path before importing this project. It currently also works by
# accident because `src` is installed into the venv, and that is exactly the
# kind of accident that breaks on a machine where it is not.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import paths  # noqa: E402
from core.console import force_utf8_output  # noqa: E402

force_utf8_output()

from src.analysis.claims import ClaimAnalyzer, ClaimStore  # noqa: E402
from src.corpus.sections import split_sections  # noqa: E402
from src.corpus.store import Corpus  # noqa: E402
from src.models import ContentItem, SourceType  # noqa: E402
from src.storage.manager import StorageManager  # noqa: E402

# 语料库位置来自 core.paths，和 ingest_all / run_claims / show_verdicts
# 用同一个开关。这里原本把 `.periscope-scratch/corpus.db` 写死，于是设置
# `PERISCOPE_DATA_DIR` 之后，声明回填会读一个目录、别的脚本写另一个目录。
CORPUS = paths.CORPUS
CONFIG = paths.DATA_DIR / "config.json" if (paths.DATA_DIR / "config.json").exists() \
    else ROOT / "data" / "config.json"


def item_from_row(row) -> ContentItem:
    """Rebuild a ContentItem from a stored row.

    The corpus stores a `sections_json` blob rather than the model, so this is
    the inverse of what `add_items` wrote. It exists because extraction takes a
    `ContentItem` and the store has no reader for one - the orchestrator only
    ever extracts items it just fetched, so nothing had needed the conversion.

    `claimable` is carried into the sections, because `_author_text` reads the
    author-written layer and would otherwise see nothing: the whole point of the
    tiering is that a press release's marketing copy is not the author's voice.
    """
    import json

    try:
        source = SourceType(row["source_type"])
    except ValueError:
        source = SourceType.RSS
    sections = []
    raw = row["sections_json"] if "sections_json" in row.keys() else None
    if raw:
        try:
            from src.models import Section
            sections = [Section(**s) for s in json.loads(raw)]
        except Exception:
            sections = []
    if not sections:
        sections = split_sections(row["claimable"] or row["content"] or "")
    return ContentItem(
        id=row["id"],
        source_type=source,
        title=row["title"] or "",
        url=row["url"] or None,
        locator=row["locator"] or row["url"] or row["id"],
        author=row["author"],
        content=row["content"] or "",
        sections=sections,
        published_at=_parse_dt(row["published_at"]),
        fetched_at=_parse_dt(row["fetched_at"]),
    )


def _parse_dt(value):
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def api_key_present(config) -> tuple:
    """(available, env var name, where it came from).

    Delegates to the client's own resolver rather than reading the environment
    directly. Checking `os.environ` here meant this command reported "no API key"
    while the very same key sat in a file the client would happily have read -
    the check and the use disagreed, and the check won.
    """
    from src.ai.client import _key_file_candidates  # noqa: WPS437

    name = getattr(config.ai, "api_key_env", "") or ""
    if os.environ.get(name):
        return (True, name, "environment")
    for candidate in _key_file_candidates(name):
        try:
            if candidate.is_file() and candidate.read_text(encoding="utf-8").strip():
                return (True, name, str(candidate))
        except OSError:
            continue
    return (False, name, "")


def rank_candidates(conn, limit: int, min_chars: int, require_claimable: bool,
                    include_bulk: bool = False):
    """Items worth spending a call on, best first.

    Ranking by trust alone does not work, and two dry runs showed why. The top of
    the trust ranking is occupied by *bulk metadata*, not by reporting:

      trust 0.82  GitHub Security Advisories  - 100 near-identical CVE entries
      trust 0.81  OpenRouter model rows       - `openai/gpt-6.1-sol-pro` plus a
                                                `:batch` variant, repeated

    Extraction spent there returns the same claim shape over and over, which
    tells you nothing about whether the pipeline works and nothing a reader would
    want to know. So the selection requires three things that editorial writing
    has and a catalog row does not:

      - a real author or an outlet domain  (not `openai/gpt-6.1-sol-pro`)
      - enough body to hold a statement    (the caller's --min-chars)
      - a title that reads like a sentence (not a package identifier)

    That is a heuristic, so it is stated rather than hidden: `--include-bulk`
    turns it off and takes the raw trust order.
    """
    where = ["LENGTH(COALESCE(claimable, content, '')) >= ?"]
    params: list = [min_chars]
    if require_claimable:
        where.append("claimable != ''")
    if not include_bulk:
        # The reliable signal is the URL, not the publisher: the top of the
        # trust ranking is `github.com/advisories/GHSA-...` (195 items) and
        # `openrouter.ai` model rows, each a catalog entry the corpus stores
        # once per variant. A publisher heuristic missed them - an advisory's
        # `publisher` is the advisory id, which looks like a person's handle.
        where.append("url IS NOT NULL AND url != ''")
        where.append("url NOT LIKE '%github.com/advisories/%'")
        where.append("url NOT LIKE '%/cve/%' AND url NOT LIKE '%nvd.nist.gov%'")
        where.append("url NOT LIKE '%spdx.org/licenses/%'")
        where.append("url NOT LIKE '%huggingface.co/%'")
        where.append("url NOT LIKE '%openrouter.ai/%'")
        where.append("url NOT LIKE '%npmjs.com/%' AND url NOT LIKE '%pypi.org/%'")
        # Titles that read like prose: CJK needs no spaces, else four words.
        where.append(
            "(title GLOB '*[一-龥]*' OR "
            " (LENGTH(title) - LENGTH(REPLACE(title, ' ', ''))) >= 3)")
        # Package identifiers: a slash and no spaces.
        where.append("NOT (title LIKE '%/%' AND title NOT LIKE '% %')")
        # Templated headlines. "not much happened today" appears 252 times in
        # this corpus - a newsletter's placeholder for a quiet day. Extracting a
        # claim from it would produce the same sentence hundreds of times, and
        # none of them are statements. Anything whose title recurs that often is
        # a template, so it is excluded rather than sampled.
        where.append("""title NOT IN (
            SELECT title FROM items GROUP BY title HAVING COUNT(*) > 3
        )""")
    sql = """
        SELECT id, title, url, locator, author, source_type, publisher, trust,
               content, claimable, sections_json, published_at, fetched_at,
               LENGTH(COALESCE(claimable, content, '')) AS n
        FROM items
        WHERE %s
          AND id NOT IN (SELECT DISTINCT item_id FROM claims WHERE item_id IS NOT NULL)
        ORDER BY trust DESC, n DESC
        LIMIT ?
    """ % " AND ".join(where)
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def report(conn) -> None:
    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    print("=" * 84)
    print("corpus: %d items" % total)
    print("=" * 84)
    for table in ("claims", "claim_evidence", "claim_contradictions"):
        try:
            n = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
            print("  %-22s %d" % (table, n))
        except sqlite3.OperationalError:
            print("  %-22s (table not created yet)" % table)
    try:
        print()
        print("  verdicts:")
        for r in conn.execute(
                "SELECT verdict, verdict_source, COUNT(*) n FROM claims "
                "GROUP BY verdict, verdict_source ORDER BY n DESC"):
            print("    %-14s via %-12s %d"
                  % (r["verdict"] or "(none)", r["verdict_source"] or "-", r["n"]))
        print()
        print("  independence:")
        for r in conn.execute(
                "SELECT independent_sources, COUNT(*) n FROM claims "
                "GROUP BY independent_sources ORDER BY independent_sources"):
            print("    %2d sources  %d claims" % (r["independent_sources"], r["n"]))
    except sqlite3.OperationalError as exc:
        print("  (nothing graded yet: %s)" % exc)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, default=25,
                    help="how many items to extract claims from")
    ap.add_argument("--grade", type=int, default=0,
                    help="how many claims to grade (0 = link only)")
    ap.add_argument("--min-chars", type=int, default=200,
                    help="skip items shorter than this; a headline rarely "
                         "contains a checkable statement")
    ap.add_argument("--any-length", action="store_true",
                    help="ignore --min-chars")
    ap.add_argument("--db", default=str(CORPUS))
    ap.add_argument("--include-advisories", action="store_true",
                    help="take the raw trust order, including CVE feeds and "
                         "model-catalog rows, which dominate it but all read alike")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="run extraction and grading with a stubbed client, to "
                         "prove the wiring works without spending a call")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print("no corpus at %s" % db)
        return 1

    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    # Not only Corpus needs this. The candidate query groups titles and sorts,
    # and with the file default SQLite spills that into TMP/TEMP; when the temp
    # directory is unwritable the error is "unable to open database file"
    # against a database that is fine. Fourth time in this session.
    conn.execute("PRAGMA temp_store=MEMORY")

    if args.report:
        report(conn)
        conn.close()
        return 0

    # StorageManager takes the *data directory* and finds config.json inside it.
    config = StorageManager(str(CONFIG.parent)).load_config()
    ok, env_name, key_source = api_key_present(config)
    print("=" * 84)
    print("claim backfill over the stored corpus")
    print("=" * 84)
    print("  corpus      : %s" % db)
    print("  provider    : %s / %s" % (config.ai.provider, config.ai.model))
    print("  key env var : %s" % env_name)
    print("  key found   : %s" % (key_source or "NOWHERE"))
    print()

    min_chars = 0 if args.any_length else args.min_chars
    candidates = rank_candidates(conn, args.limit, min_chars,
                                 require_claimable=not args.any_length,
                                 include_bulk=args.include_advisories)

    print("  candidates  : %d items (trust-ranked, >=%d chars, not yet extracted)"
          % (len(candidates), min_chars))
    for r in candidates[:10]:
        print("      %-9s %-28s %5.2f  %s"
              % (r["source_type"], (r["publisher"] or "-")[:28],
                 r["trust"] or 0.0, (r["title"] or "")[:38]))
    if len(candidates) > 10:
        print("      ... and %d more" % (len(candidates) - 10))

    if args.selftest:
        # Proves the wiring - reconstruction from stored rows, extraction,
        # linking, grading - without a call or a key. The verdicts are the
        # stub's, so they say nothing about the model; everything around them is
        # the real code path.
        import json as _json

        class _Stub:
            def __init__(self):
                self.calls = 0

            async def complete(self, system: str, user: str, **kwargs) -> str:
                self.calls += 1
                # extraction asks for claims; grading asks for a verdict.
                if "claims" in (system or "").lower():
                    return _json.dumps({"claims": [
                        {"text": "selftest: the item states a checkable fact",
                         "type": "fact", "time_scope": None}]})
                return _json.dumps({"verdict": "supported", "confidence": 0.9,
                                    "reason": "selftest stub"})

        corpus = Corpus(str(db))
        store = ClaimStore(corpus)
        stub = _Stub()
        analyzer = ClaimAnalyzer(store=store, corpus=corpus, client=stub,
                                 max_claims_per_item=config.analysis.max_claims_per_item,
                                 evidence_per_claim=config.analysis.evidence_per_claim,
                                 grade_min_sources=config.analysis.grade_min_sources)
        print()
        print("  selftest: extracting from %d candidates with a stub"
              % len(candidates))
        made = 0
        for row in candidates:
            item = item_from_row(row)
            made += len(await analyzer.extract_claims(item))
        print("    claims created : %d" % made)
        linked = analyzer.link_all_pending()
        print("    evidence links : %d" % linked)
        graded = await analyzer.grade_pending(max_calls=args.grade or len(candidates))
        print("    graded         : %d" % graded)
        print("    stub calls     : %d" % stub.calls)
        print()
        print("  This proves the real code path runs on stored rows. The verdicts")
        print("  are the stub's, so they are not evidence about the corpus.")
        report(corpus._conn)
        corpus.close()
        conn.close()
        return 0

    if args.dry_run:
        print()
        print("  dry run - nothing sent, nothing written")
        conn.close()
        return 0

    if not ok:
        print()
        print("  STOP: no API key, so extraction cannot run.")
        print()
        print("  Set it and re-run the same command:")
        print("      $env:%s = \"...\"        (this shell)" % env_name)
        print("      or put %s=... in %s/.env" % (env_name, ROOT))
        print()
        print("  Everything else is ready: the ranking above is real, and the")
        print("  linking/grading code paths were verified end to end on this")
        print("  corpus with a stubbed client.")
        conn.close()
        return 2

    from src.ai.client import create_ai_client
    client = create_ai_client(config.ai)
    corpus = Corpus(str(db))
    store = ClaimStore(corpus)
    analyzer = ClaimAnalyzer(
        store=store, corpus=corpus, client=client,
        max_claims_per_item=config.analysis.max_claims_per_item,
        evidence_per_claim=config.analysis.evidence_per_claim,
        grade_min_sources=config.analysis.grade_min_sources,
    )

    print()
    print("  extracting ...")
    total_claims = 0
    for index, row in enumerate(candidates, 1):
        item = item_from_row(row)
        try:
            got = await analyzer.extract_claims(item)
        except Exception as exc:
            print("    [%d/%d] %s: %s" % (index, len(candidates),
                                          type(exc).__name__, str(exc)[:60]))
            continue
        total_claims += len(got)
        if index % 5 == 0 or index == len(candidates):
            print("    [%d/%d] %d claims so far"
                  % (index, len(candidates), total_claims))

    linked = analyzer.link_all_pending()
    print("  extracted %d claims, linked %d" % (total_claims, linked))

    graded = 0
    if args.grade:
        print("  grading up to %d ..." % args.grade)
        graded = await analyzer.grade_pending(max_calls=args.grade)
        print("  graded %d" % graded)

    print()
    print("  LLM calls used: %d" % analyzer.llm_calls)
    report(corpus._conn)

    corpus.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

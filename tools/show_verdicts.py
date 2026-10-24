"""The first real graded claims: what did the pipeline actually decide?

Everything before this was mechanism. This is the first output where a language
model read the corpus and the trust gate had a chance to overrule it, so the
useful thing to look at is not the counts but the reasoning: which claims were
supported, on how many independent voices, and which were refused and why.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import paths  # noqa: E402
from core.console import force_utf8_output  # noqa: E402

force_utf8_output()

CORPUS = paths.CORPUS
conn = sqlite3.connect(str(CORPUS))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA temp_store=MEMORY")

print("=" * 96)
print("graded claims, with the evidence behind each")
print("=" * 96)

rows = conn.execute("""
    SELECT cl.id, cl.text, cl.claim_type, cl.verdict, cl.confidence,
           cl.independent_sources, cl.verdict_source, cl.ungraded_reason,
           cl.trust, i.title, i.publisher
    FROM claims cl LEFT JOIN items i ON i.id = cl.item_id
    WHERE cl.verdict IS NOT NULL
    ORDER BY cl.verdict, cl.independent_sources DESC
""").fetchall()

print()
for r in rows:
    mark = {"supported": "[OK ]", "unsupported": "[NO ]",
            "contested": "[??]"}.get(r["verdict"], "[   ]")
    print("%s %s" % (mark, (r["text"] or "")[:88]))
    print("      verdict   : %s  (confidence %s, via %s)"
          % (r["verdict"], r["confidence"], r["verdict_source"]))
    print("      evidence  : %s independent publishers, aggregate trust %s"
          % (r["independent_sources"], round(r["trust"], 4) if r["trust"] else "-"))
    if r["ungraded_reason"]:
        print("      reason    : %s" % r["ungraded_reason"])
    print("      source    : %s" % (r["title"] or "")[:70])
    print()

print("=" * 96)
print("the evidence a supported claim actually rests on")
print("=" * 96)
top = conn.execute("""
    SELECT id, text, independent_sources FROM claims
    WHERE verdict = 'supported' ORDER BY independent_sources DESC LIMIT 1
""").fetchone()
if top:
    print()
    print("  claim: %s" % (top["text"] or "")[:80])
    print("  %d independent publishers:" % top["independent_sources"])
    for e in conn.execute("""
        SELECT i.publisher, i.source_type, i.title
        FROM claim_evidence ce JOIN items i ON i.id = ce.item_id
        WHERE ce.claim_id = ? LIMIT 12
    """, (top["id"],)):
        print("    %-30s %-12s %s"
              % (str(e["publisher"] or "-")[:30], e["source_type"],
                 (e["title"] or "")[:44]))

print()
print("=" * 96)
print("distribution")
print("=" * 96)
for r in conn.execute("""
    SELECT verdict, verdict_source, COUNT(*) n FROM claims
    WHERE verdict IS NOT NULL GROUP BY verdict, verdict_source
    ORDER BY n DESC
"""):
    print("  %-14s via %-12s %d" % (r["verdict"], r["verdict_source"], r["n"]))

conn.close()

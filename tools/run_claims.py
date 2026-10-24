"""Prove the trust gate can overrule the model, on the real corpus.

The previous run showed the pipeline end-to-end, but every verdict was
`supported` because the stub always said so - which proves the plumbing works and
proves nothing about the gate. The gate is the part that makes the system more
than a language model with a database: it exists to refuse a verdict the model
would happily pass, when the evidence behind it is too thin or too anonymous.

So the stub here always returns `supported`, and the evidence is varied instead.
A claim only stays `supported` when the corpus genuinely carries it.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import paths  # noqa: E402
from core.console import force_utf8_output  # noqa: E402
from src.analysis.claims import Claim, ClaimAnalyzer, ClaimStore  # noqa: E402
from src.corpus.store import Corpus  # noqa: E402

force_utf8_output()

CORPUS = paths.CORPUS


class AlwaysSupported:
    def __init__(self):
        self.body = json.dumps({"verdict": "supported", "confidence": 0.95,
                                "reason": "the stub always supports"})

    async def complete(self, system: str, user: str, **kwargs) -> str:
        return self.body


conn = sqlite3.connect(str(CORPUS))
conn.row_factory = sqlite3.Row
corpus = Corpus(str(CORPUS))
store = ClaimStore(corpus)

print("=" * 94)
print("trust gate vs an always-`supported` model, on %d real items"
      % conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])
print("=" * 94)
print()
print("  the model says `supported` every time; only the evidence changes")
print()


def run(label, term, limit=8, publishers=None):
    """`publishers` forces how many distinct voices the evidence carries, so the
    gate can be tested below `grade_min_sources` and not merely observed above
    it."""
    if publishers is not None:
        rows = conn.execute(
            "SELECT id, source_type, publisher, cluster_id, trust FROM items "
            "WHERE publisher = ? LIMIT ?", (publishers, limit)).fetchall()
        # collapse the evidence to that one publisher
        rows = rows[:limit]
    else:
        rows = conn.execute(
            "SELECT id, source_type, publisher, cluster_id, trust FROM items "
            "WHERE title LIKE ? OR content LIKE ? LIMIT ?",
            ("%%%s%%" % term, "%%%s%%" % term, limit)).fetchall()
    if not rows:
        print("  %-30s no evidence -> cannot be graded at all" % label)
        return None
    cid = "claim:%s" % term.replace(" ", "_")[:30]
    claim = Claim(id=cid, item_id=rows[0]["id"], text=label, status="linked")
    store.upsert_claims([claim])
    for r in rows:
        corpus._conn.execute(
            "INSERT OR IGNORE INTO claim_evidence "
            "(claim_id, item_id, source_type, score) VALUES (?,?,?,1.0)",
            (cid, r["id"], r["source_type"]))
    corpus._conn.commit()
    store.recompute_independence()
    got = store.get_claim(cid)

    analyzer = ClaimAnalyzer(store=store, corpus=corpus,
                             client=AlwaysSupported(), grade_min_sources=2)
    asyncio.run(analyzer.grade_claim(store.get_claim(cid)))
    row = corpus._conn.execute(
        "SELECT verdict, verdict_source FROM claims WHERE id=?", (cid,)).fetchone()
    verdict = row["verdict"] if row else "-"
    source = row["verdict_source"] if row else "-"
    # the interesting column: did the gate overrule the model?
    overruled = "YES" if source == "trust_gate" else "no"
    print("  %-30s %3d evid %3d indep  %-11s from %-11s gate overruled: %s"
          % (label, len(rows), got.independent_sources, verdict, source, overruled))
    return source


print("%-32s %8s %7s  %-11s %-11s %s"
      % ("claim", "evid", "indep", "verdict", "source", "gate?"))
print("-" * 94)
results = {}
results["well corroborated"] = run("HBM4 supply tightens", "HBM4", 8)
results["many sources"] = run("DeepSeek released a model", "DeepSeek", 8)
results["thin evidence"] = run("one niche component ships", "LWN subscriber", 1)
results["no evidence"] = run("nothing about this exists", "zzzz-nonexistent-topic", 4)
# force a single-voice claim so the gate has to make a decision
one = corpus._conn.execute(
    "SELECT publisher FROM items WHERE publisher IS NOT NULL AND publisher != '' "
    "GROUP BY publisher HAVING COUNT(*) >= 6 ORDER BY COUNT(*) DESC LIMIT 1"
).fetchone()
if one:
    results["single voice"] = run(
        "only one outlet says this", "", limit=6, publishers=one[0])

print("-" * 94)
print()
print("=" * 94)
print("what the result means")
print("=" * 94)
print("  `source=llm`        the model's verdict stood")
print("  `source=trust_gate` the evidence was too thin and the gate refused it")
print()
print("  A claim resting on one or two voices should be refused even though the")
print("  model said supported. That is the gate doing its job, and it is the")
print("  difference between this system and asking a model to summarise.")

corpus.close()
conn.close()

"""Claim-verdict agreement: export a labeling sheet, then score it.

    uv run python scripts/eval_claims.py --export data/corpus.db
    # 人工在 data/eval/claims_labels.json 里填 human_verdict
    uv run python scripts/eval_claims.py --score data/eval/claims_labels.json

This is the missing half of the correctness claim: the pipeline grades
supported / contested / unsupported, and nothing yet says how often that grade
matches a reader who checks the evidence. Human labels are the primary evidence
here; the metrics are ordinary per-class precision / recall / F1 plus macro-F1,
with agreement bucketed by independent-source count so the counting mechanism
itself can be falsified.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.analysis.agreement import independence_buckets, score_pairs  # noqa: E402
from src.corpus.trust import roc_thresholds  # noqa: E402

DEFAULT_DB = REPO_ROOT / "data" / "corpus.db"
DEFAULT_SHEET = REPO_ROOT / "data" / "eval" / "claims_labels.json"


def export_sheet(db_path: Path, out_path: Path) -> int:
    """Dump graded and linked claims into a labeling sheet, verdicts unfilled."""
    if not db_path.exists():
        raise SystemExit(
            f"找不到语料库 {db_path}。先跑一次 `uv run periscope --hours 24` 采集，"
            "或先用 data/eval/corpus_fixture.json 做演示。"
        )
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT c.id, c.text, c.claim_type, c.status, c.verdict,
                      c.confidence, c.trust, c.independent_sources, c.item_id,
                      i.title AS origin_title, i.url AS origin_url
               FROM claims c LEFT JOIN items i ON i.id = c.item_id
               WHERE c.status IN ('linked','graded')
               ORDER BY c.independent_sources DESC, c.created_at"""
        ).fetchall()
        claims: List[Dict[str, Any]] = []
        for row in rows:
            evidence = conn.execute(
                """SELECT e.item_id, e.cluster_id, e.source_type, i.title, i.url
                   FROM claim_evidence e LEFT JOIN items i ON i.id = e.item_id
                   WHERE e.claim_id=? ORDER BY e.score DESC LIMIT 6""",
                (row["id"],),
            ).fetchall()
            claims.append(
                {
                    "claim_id": row["id"],
                    "text": row["text"],
                    "claim_type": row["claim_type"],
                    "origin": {"title": row["origin_title"], "url": row["origin_url"]},
                    "independent_sources": row["independent_sources"],
                    "machine_verdict": row["verdict"],
                    "machine_confidence": row["confidence"],
                    "machine_trust": row["trust"],
                    "evidence": [dict(e) for e in evidence],
                    "human_verdict": None,
                    "human_note": "",
                }
            )
    finally:
        conn.close()

    payload = {
        "instructions": (
            "读 evidence 里的原文摘录，只按这些摘录判断：supported=独立说法一致；"
            "contested=至少一条实质性反驳；unsupported=摘录太泛或跑题，无法确认。"
            "填 human_verdict，不确定的留 null 并在 human_note 说明。"
            "注意 machine_verdict / machine_trust 是**被测对象**，本表不是盲标 —— 一致率因此偏乐观，"
            "要盲标就先把这两列遮掉再读摘录。machine_trust 是这条声明的 T 值，θ 校准要用它，别改。"
        ),
        "labels": claims,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return len(claims)


def score_sheet(sheet_path: Path, out_path: Path | None) -> str:
    payload = json.loads(sheet_path.read_text(encoding="utf-8"))
    rows = payload["labels"] if isinstance(payload, dict) else payload

    labeled = [r for r in rows if r.get("human_verdict") and r.get("machine_verdict")]
    if not labeled:
        return "标注表里还没有同时具备 human_verdict 与 machine_verdict 的条目，无法计分。"

    agreement = score_pairs([(r["human_verdict"], r["machine_verdict"]) for r in labeled])
    buckets = independence_buckets(
        [
            (int(r.get("independent_sources") or 0), r["human_verdict"], r["machine_verdict"])
            for r in labeled
        ]
    )

    lines = [agreement.table(), "", "按独立信源数分桶的人工一致率："]
    for key in ("1", "2", "3+"):
        entry = buckets[key]
        lines.append(f"- {key} 源：n={int(entry['n'])}, agreement={entry['agreement']:.3f}")
    skipped = len(rows) - len(labeled)
    if skipped:
        lines.append(f"\n（未标注或机器未评级的条目 {skipped} 条，已排除）")

    # Threshold fitting is the reason the labels exist; agreement alone would
    # leave θ_s / θ_triage hand-set and the project's open question open.
    pairs = [
        (float(r["machine_trust"]), r["human_verdict"] == "supported")
        for r in labeled
        if r.get("machine_trust") is not None
    ]
    fitted = roc_thresholds(pairs)
    lines.append("")
    if fitted is None:
        lines.append(
            f"θ 校准：跳过（带 T 值的标注 {len(pairs)} 条；还需至少两类标签）。"
            "阈值仍是**手工先验**，别写成被拟合过的。"
        )
    else:
        lines.append(
            f"θ 校准建议（来自 {len(pairs)} 条带 T 值的标注）："
            f"supported={fitted.supported:.2f}、triage={fitted.triage:.2f}。"
            "这是**建议**：写进 `trust` 配置之前，线上阈值仍是手工先验；"
            "且本表非盲标，一致率与由此得到的阈值都偏乐观。"
        )

    if out_path:
        out_path.write_text(
            json.dumps(
                {
                    "n": agreement.n,
                    "accuracy": round(agreement.accuracy, 4),
                    "macro_f1": round(agreement.macro_f1, 4),
                    "per_label": {
                        k: {m: round(v, 4) for m, v in d.items()} for k, d in agreement.per_label.items()
                    },
                    "confusion": agreement.confusion,
                    "independence_buckets": {
                        k: {"n": v["n"], "agreement": round(v["agreement"], 4)} for k, v in buckets.items()
                    },
                    "excluded": skipped,
                    "threshold_pairs": len(pairs),
                    "thresholds_suggested": None if fitted is None else {
                        "supported": fitted.supported,
                        "triage": fitted.triage,
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, default=None, metavar="DB", help="导出标注表")
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--score", type=Path, default=None, metavar="LABELS.json")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "eval" / "claims_results.json")
    parser.add_argument(
        "--tiering", choices=("sections", "marker"), default="sections",
        help="这批标注是在哪种分层判据下导出的。人评本身与判据无关，但指标要按档"
             "分别报，否则消融表的两行会共用一份 ground truth 而看不出差别。",
    )
    args = parser.parse_args()

    if args.export:
        count = export_sheet(args.export, args.sheet)
        print(f"已导出 {count} 条待标注声明 -> {args.sheet.relative_to(REPO_ROOT)}")
        return 0
    if args.score:
        print(score_sheet(args.score, args.out))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

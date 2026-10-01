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
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.analysis.agreement import LABELS, independence_buckets, score_pairs  # noqa: E402
from src.corpus.sections import split_sections  # noqa: E402
from src.corpus.trust import roc_thresholds  # noqa: E402

DEFAULT_DB = REPO_ROOT / "data" / "corpus.db"
DEFAULT_SHEET = REPO_ROOT / "data" / "eval" / "claims_labels.json"


MACHINE_COLUMNS = ("machine_verdict", "machine_verdict_source", "machine_confidence", "machine_trust")

COVERAGE_NOTE = (
    "**三个类别都要标到样本**（supported / contested / unsupported）：macro-F1 在这三类上取平均，"
    "没有样本的那类 F1 记 0，所以只标两类时即便人机完全一致也只有 0.667 —— "
    "`--score` 会在缺类时明说这个数不可解读。"
)

BLIND_NOTE = (
    "本表是**盲标**：machine_verdict / machine_confidence / machine_trust 不在表里，"
    "它们在旁边的 .machine.json 里 —— 标完之前别打开那份文件，`--score` 会自己合回来。"
    "盲标出来的一致率才是没被被测对象带偏的那一个。"
)


def sidecar_path(sheet_path: Path) -> Path:
    return sheet_path.with_name(sheet_path.stem + ".machine.json")


def split_machine_columns(claims: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Strip the measured verdicts out of `claims`, returning them by claim id.

    In place, and it must be a pop rather than a copy: a sheet that still shows
    `machine_verdict` is not blind, and it looks exactly as convincing as one
    that never did.
    """
    machine: Dict[str, Dict[str, Any]] = {}
    for claim in claims:
        machine[claim["claim_id"]] = {k: claim.pop(k) for k in MACHINE_COLUMNS if k in claim}
    return machine


EXCERPT_CHARS = 400


def claimable_excerpt(content: str | None, stored_claimable: str | None,
                      tiering: str, chars: int = EXCERPT_CHARS) -> str:
    """The text the grader was shown, under the arm being labelled.

    Same whitespace collapse and same cap as `ClaimAnalyzer._excerpt`, on purpose:
    a verdict a human wrote against different text than the model saw is not a
    measurement of the same system. `tiering="marker"` ignores the declared
    tiers and re-derives them from marker lines, which is what lets a labeler
    see the leak arm A is there to quantify.
    """
    if tiering == "marker":
        text = " ".join(
            section.text for section in split_sections(content)
            if section.tier == "primary"
        ) or (content or "")
    else:
        text = stored_claimable or content or ""
    return re.sub(r"\s+", " ", text)[:chars]


def export_sheet(db_path: Path, out_path: Path, blind: bool = False,
                 tiering: str = "sections") -> int:
    """Dump graded and linked claims into a labeling sheet, verdicts unfilled.

    With `blind`, the machine columns are held out of the sheet entirely and
    written to a sibling sidecar: agreement measured against a verdict the
    labeler can see is anchored, and the docs had been telling the maintainer
    to blank those columns by hand.
    """
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
                      c.verdict_source,
                      c.confidence, c.trust, c.independent_sources, c.item_id,
                      i.title AS origin_title, i.url AS origin_url,
                      i.content AS origin_content, i.claimable AS origin_claimable
               FROM claims c LEFT JOIN items i ON i.id = c.item_id
               WHERE c.status IN ('linked','graded')
               ORDER BY c.independent_sources DESC, c.created_at"""
        ).fetchall()
        claims: List[Dict[str, Any]] = []
        for row in rows:
            # No cap here on purpose: `add_evidence` keeps exactly
            # `analysis.evidence_per_claim` rows per claim, so reading them all
            # is what makes the sheet show the same excerpts the grader was
            # given. A second number in this query would silently relabel a
            # different evidence set than the one being graded.
            evidence = [
                {
                    "item_id": e["item_id"],
                    "cluster_id": e["cluster_id"],
                    "source_type": e["source_type"],
                    "title": e["title"],
                    "url": e["url"],
                    # The instructions tell the labeler to judge from these
                    # excerpts; before this there were none in the sheet, so the
                    # only way to do the task was to open every URL.
                    "claimable_excerpt": claimable_excerpt(
                        e["content"], e["claimable"], tiering
                    ),
                }
                for e in conn.execute(
                    """SELECT e.item_id, e.cluster_id, e.source_type, i.title, i.url,
                              i.content, i.claimable
                       FROM claim_evidence e LEFT JOIN items i ON i.id = e.item_id
                       WHERE e.claim_id=? ORDER BY e.score DESC""",
                    (row["id"],),
                ).fetchall()
            ]
            claims.append(
                {
                    "claim_id": row["id"],
                    "text": row["text"],
                    "claim_type": row["claim_type"],
                    "origin": {
                        "title": row["origin_title"], "url": row["origin_url"],
                        "claimable_excerpt": claimable_excerpt(
                            row["origin_content"], row["origin_claimable"], tiering
                        ),
                    },
                    "independent_sources": row["independent_sources"],
                    "machine_verdict": row["verdict"],
                    # Whether the stored verdict is the model's or the trust
                    # gate's veto. Mixed together they make one number that
                    # answers neither "is the model right" nor "is the gate".
                    "machine_verdict_source": row["verdict_source"],
                    "machine_confidence": row["confidence"],
                    "machine_trust": row["trust"],
                    "evidence": evidence,
                    "human_verdict": None,
                    "human_note": "",
                }
            )
    finally:
        conn.close()

    instructions = (
        f"读 evidence[].claimable_excerpt —— 那就是评级模型当时看到的同一段文字"
        f"（本表按 `tiering={tiering}` 导出，上限 {EXCERPT_CHARS} 字、空白折叠），"
        "只按这些摘录判断：supported=独立说法一致；"
        "contested=至少一条实质性反驳；unsupported=摘录太泛或跑题，无法确认。"
        "填 human_verdict，不确定的留 null 并在 human_note 说明。"
    )
    if blind:
        instructions += BLIND_NOTE
    else:
        instructions += (
            "注意 machine_verdict / machine_trust 是**被测对象**，本表不是盲标 —— 一致率因此偏乐观，"
            "要盲标就加 `--blind`（machine_* 会移到旁边的 .machine.json，评分时自动合回来）。"
            "machine_trust 是这条声明的 T 值，θ 校准要用它，别改。"
        )
    instructions += COVERAGE_NOTE

    machine_map: Dict[str, Dict[str, Any]] = {}
    if blind:
        machine_map = split_machine_columns(claims)

    payload = {"instructions": instructions, "blind": blind, "tiering": tiering,
               "labels": claims}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    if blind:
        sidecar_path(out_path).write_text(
            json.dumps(
                {
                    "instructions": "盲标副表：标注完成之前不要打开。`--score` 会按 claim_id 合回来。",
                    "machine": machine_map,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
            newline="\n",
        )
    return len(claims)


def missing_verdict_classes(agreement) -> List[str]:
    """Which of the three verdicts the human never picked.

    macro-F1 averages over the fixed three-label set, so an absent class is
    scored 0 and perfect agreement silently reads as 0.667. Printing that number
    without saying so would turn a coverage gap into a quality claim.
    """
    return [
        label for label in LABELS
        if agreement.per_label[label]["gold"] == 0
    ]


def _with_machine_fields(rows: List[Dict[str, Any]], path: Path) -> List[Dict[str, Any]]:
    """Join a blind sheet back to its sidecar, by claim_id.

    The labeler's own columns win on key collisions: the sidecar is input to
    scoring, never a replacement for what was written down.
    """
    if not path.exists():
        raise SystemExit(
            f"这是一张盲标表（行里没有 machine_verdict），但找不到副表 {path}。"
            "用 `--machine <path>` 指给它。"
        )
    table = json.loads(path.read_text(encoding="utf-8")).get("machine", {})
    return [{**(table.get(row.get("claim_id")) or {}), **row} for row in rows]


def score_sheet(sheet_path: Path, out_path: Path | None,
                machine_path: Path | None = None, tiering: str | None = None) -> str:
    payload = json.loads(sheet_path.read_text(encoding="utf-8"))
    declared = payload if isinstance(payload, dict) else {}
    rows = declared.get("labels", payload) if declared else payload

    # The two ablation arms must not share one ground truth by accident: the arm
    # a sheet was exported under is recorded in the sheet, so scoring it under a
    # different one is said out loud instead of producing a plausible table.
    sheet_arm = declared.get("tiering")
    arm_note = (
        f"⚠ 这份标注表是按 `tiering={sheet_arm}` 导出的，你现在按 `--tiering {tiering}` 报指标："
        "两档不能共用同一份 ground truth，请用导出时那一档报，或以该档重新导出再标。"
    ) if tiering and sheet_arm and sheet_arm != tiering else None

    # "No machine columns" has two very different causes - a blind sheet, and an
    # empty sheet nobody has labelled yet. Guessing from the columns alone once
    # turned the second into a crash, so join only when the sheet says it is
    # blind, the sidecar is actually there, or the caller pointed at one.
    needs_join = bool(rows) and all("machine_verdict" not in r for r in rows)
    sidecar = machine_path or sidecar_path(sheet_path)
    blind = bool(declared.get("blind")) or (needs_join and sidecar.exists())
    if needs_join and (declared.get("blind") or machine_path is not None or sidecar.exists()):
        rows = _with_machine_fields(rows, sidecar)

    labeled = [r for r in rows if r.get("human_verdict") and r.get("machine_verdict")]
    if not labeled:
        return (
            "标注表里还没有同时具备 human_verdict 与 machine_verdict 的条目，无法计分。"
            + ("（这是盲标表：用 `--machine` 指到 .machine.json 副表。）" if blind else "")
        )

    agreement = score_pairs([(r["human_verdict"], r["machine_verdict"]) for r in labeled])
    buckets = independence_buckets(
        [
            (int(r.get("independent_sources") or 0), r["human_verdict"], r["machine_verdict"])
            for r in labeled
        ]
    )

    # The warning goes above the table on purpose: the table is the part that
    # gets copied into a report, and the number at its top is unreadable without
    # this sentence.
    absent = missing_verdict_classes(agreement)
    lines = []
    if absent:
        lines.append(
            f"⚠ 人工标签里缺 {'、'.join(absent)}：macro-F1 固定在这三类上取平均，缺的那类 F1 记 0，"
            f"所以**即便人机完全一致也只有 {1 - len(absent) / 3:.3f}**。"
            "下面那行的 macro-F1 不可解读，别引用；先补齐这三类的样本。"
        )
        lines.append("")
    lines += [agreement.table(), "", "按独立信源数分桶的人工一致率："]
    for key in ("1", "2", "3+"):
        entry = buckets[key]
        lines.append(f"- {key} 源：n={int(entry['n'])}, agreement={entry['agreement']:.3f}")
    skipped = len(rows) - len(labeled)
    if skipped:
        lines.append(f"\n（未标注或机器未评级的条目 {skipped} 条，已排除）")

    # The two question marks have to be separated: agreement over
    # `machine_verdict` alone answers "does the combined system match a human",
    # while the split shows whether the disagreement sits with the model reading
    # excerpts or with the breadth gate standing behind it.
    by_source: Dict[str, Dict[str, float]] = {}
    sourced = [r for r in labeled if r.get("machine_verdict_source")]
    if sourced:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for r in sourced:
            groups.setdefault(r["machine_verdict_source"], []).append(r)
        lines.append("")
        lines.append("按判定来源拆分（llm = 模型原判定；trust_gate = 被可信度门否决后的值）：")
        for name in sorted(groups):
            sub = score_pairs([(r["human_verdict"], r["machine_verdict"]) for r in groups[name]])
            by_source[name] = {"n": float(sub.n), "agreement": round(sub.accuracy, 4)}
            lines.append(f"- {name}: n={sub.n}, agreement={sub.accuracy:.3f}")

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
            "这是**建议**：要生效就设 `analysis.supported_min_trust` 与 "
            "`analysis.triage_min_trust`（后者 0.0 = 分诊门关闭），在那之前线上阈值仍是手工先验；"
            + ("本表是盲标（machine_* 由副表合入），一致率不因看见被测判定而偏乐观。"
               if blind else
               "且本表非盲标，一致率与由此得到的阈值都偏乐观。")
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
                    "class_coverage": {
                        label: int(agreement.per_label[label]["gold"]) for label in LABELS
                    },
                    "macro_f1_interpretable": not absent,
                    "blind": blind,
                    "tiering": tiering,
                    "sheet_tiering": sheet_arm,
                    "arm_mismatch": arm_note is not None,
                    "by_verdict_source": by_source,
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
    if arm_note:
        lines.insert(0, arm_note)
        lines.insert(1, "")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, default=None, metavar="DB", help="导出标注表")
    parser.add_argument("--sheet", type=Path, default=DEFAULT_SHEET)
    parser.add_argument("--score", type=Path, default=None, metavar="LABELS.json")
    parser.add_argument(
        "--blind", action="store_true",
        help="导出时把 machine_verdict / machine_confidence / machine_trust 移到旁边的 .machine.json，"
             "标注者看不到被测判定 —— 一致率与由此拟出的阈值才不因此偏乐观。",
    )
    parser.add_argument(
        "--machine", type=Path, default=None, metavar="MACHINE.json",
        help="评分时指定的盲标副表；默认取标注表同名的 .machine.json。",
    )
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "eval" / "claims_results.json")
    parser.add_argument(
        "--tiering", choices=("sections", "marker"), default="sections",
        help="这批标注属于哪个消融档。导出的 `evidence[].claimable_excerpt` 就按这一档"
             "重算（marker 档会忽略声明层级、按标记重切，于是能看见人群文本泄漏），"
             "档位写进表里；`--score` 用它对照，两档不得共用一份 ground truth。",
    )
    args = parser.parse_args()

    if args.export:
        count = export_sheet(args.export, args.sheet, blind=args.blind,
                             tiering=args.tiering)
        print(f"已导出 {count} 条待标注声明 -> {args.sheet.relative_to(REPO_ROOT)}")
        if args.blind:
            print("盲标：machine_* 在 "
                  f"{sidecar_path(args.sheet).relative_to(REPO_ROOT)}，标完再打开；--score 会自动合回来。")
        return 0
    if args.score:
        print(score_sheet(args.score, args.out, args.machine, args.tiering))
        return 0
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

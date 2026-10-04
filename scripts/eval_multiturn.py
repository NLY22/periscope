"""Multi-turn cost and flood resistance — the objective half of spec §5.5.

    uv run python scripts/eval_multiturn.py
    uv run python scripts/eval_multiturn.py --branches 2,4,8

Three numbers, none of which is a quality claim:

1. **rounds / calls to resolve** — how many rounds a session takes to reach
   `finalize`, and what each round costs in LLM calls, including a round that
   asks the user and parks the session;
2. **partial vs full recompute** — deepening one branch against re-walking the
   whole tree, at several branch counts. This ratio is the only reason
   per-round interaction is affordable;
3. **flood resistance** — `independent_sources` before and after the P1 recount
   under a same-text pile-on. The old rule
   (`COUNT(DISTINCT COALESCE(cluster_id, item_id))`) is replayed *inside this
   script*, the way `scripts/eval_retrieval.py --tiering marker` keeps ablation
   arm A reproducible: the production code no longer contains it, so the
   comparison has to be explicit to stay checkable.

Nothing here measures latency. The repo bans wall-clock assertions, so the cost
unit is LLM calls, not seconds — say "calls", never "ms", when quoting this.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.analysis.claims import Claim, ClaimStore, EvidenceLink  # noqa: E402
from src.corpus.store import Corpus  # noqa: E402
from src.corpus.trust import classify, distinct_publishers, noisy_or  # noqa: E402
from src.models import ContentItem, SourceType  # noqa: E402
from src.research.moves import AskUser, Deepen, Finalize  # noqa: E402
from src.research.session import ResearchSession, ResearchStore  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "data" / "eval" / "multiturn_results.json"

# Fixed clock: freshness must not depend on when the harness is run.
NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def make_item(
    idx: str,
    source: SourceType,
    title: str,
    content: str,
    author: Optional[str] = "tester",
) -> ContentItem:
    return ContentItem(
        id=f"mt:{idx}",
        source_type=source,
        title=title,
        url=f"https://example.com/{idx}",
        content=content,
        author=author,
        published_at=NOW,
        fetched_at=NOW,
    )


class CountingPlanner:
    """A planner that only counts. Answers are canned, so the numbers below
    are about call counts and rounds, never about text quality."""

    def __init__(self, subs: Sequence[str], moves: Optional[Sequence[Any]] = None):
        self.subs = list(subs)
        self.moves: List[Any] = list(moves or [])
        self.calls = 0
        self.round_calls: List[int] = []

    async def decompose(self, question: str, prior: List[str]) -> List[str]:
        self.calls += 1
        return list(self.subs)

    async def answer(self, question: str, subquestion: str, evidence: List[Dict]) -> str:
        self.calls += 1
        return f"关于「{subquestion}」的回答。" if evidence else ""

    async def revise(self, question, user_message, open_subquestions):
        self.calls += 1
        return {"add": [], "drop": []}

    async def next_move(self, ctx):
        self.calls += 1
        if self.moves:
            return self.moves.pop(0)
        return Finalize("script exhausted")

    def start_round(self) -> None:
        self.round_calls.append(self.calls)

    def close_round(self) -> int:
        return self.calls - self.round_calls[-1]


def build_session(corpus: Corpus, planner: Optional[CountingPlanner]) -> ResearchSession:
    return ResearchSession(
        store=ResearchStore(corpus),
        corpus=corpus,
        planner=planner,
        evidence_per_question=3,
        planner_budget_per_invocation=200,
    )


def seed_corpus(tmp_path: Path, branches: int, topic: str = "模型评测") -> Corpus:
    """One citable item per branch, so every sub-question can reach the corpus."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    corpus = Corpus(tmp_path / "corpus.db")
    items = [
        make_item(
            f"b{i}",
            SourceType.RSS,
            f"{topic} 第 {i} 期",
            f"{topic} 第 {i} 期：准确率 {70 + i} 成本 {2 * i} 万元，样本 {100 + i} 条。",
        )
        for i in range(branches)
    ]
    corpus.add_items(items, now=NOW)
    return corpus


# ------------------------------------------------------------------ measures
async def measure_recompute(tmp_path: Path, branches: int) -> Dict[str, Any]:
    """Calls to deepen one branch vs the whole tree, same corpus, same moves.

    The moves are armed only after the session exists: `Deepen` takes real
    sub-question ids, and a hand-made id would touch no branch and print a
    flattering ratio.
    """
    corpus = seed_corpus(tmp_path, branches)
    sub_texts = [f"分支 {i} 的结论是什么" for i in range(branches)]

    one = CountingPlanner(sub_texts)
    rs_one = build_session(corpus, one)
    started = await rs_one.start("评测")
    ids = [s.id for s in rs_one.store.subquestions(started.session_id)]
    assert len(ids) == branches, "the planner's decomposition is what is being priced"

    one.calls = 0
    one.moves.append(Deepen((ids[0],)))
    await rs_one.step(started.session_id, "")
    deepening = one.calls

    allof = CountingPlanner(sub_texts)
    rs_all = build_session(corpus, allof)
    started_all = await rs_all.start("评测")
    all_ids = [s.id for s in rs_all.store.subquestions(started_all.session_id)]

    allof.calls = 0
    allof.moves.append(Deepen(tuple(all_ids)))
    await rs_all.step(started_all.session_id, "")
    whole_tree = allof.calls

    rs_one.corpus.close()
    rs_all.corpus.close()
    return {
        "branches": branches,
        "one_branch_calls": deepening,
        "whole_tree_calls": whole_tree,
        "ratio": round(whole_tree / deepening, 2) if deepening else None,
    }


async def measure_rounds(tmp_path: Path) -> Dict[str, Any]:
    """Rounds to resolve, with one round where the system asks and parks."""
    corpus = seed_corpus(tmp_path / "rounds", 3)
    planner = CountingPlanner(
        ["分支 0 的结论是什么", "分支 1 的结论是什么", "分支 2 的结论是什么"],
        [AskUser("supply_source", "有内部评测数据吗？")],
    )
    rs = build_session(corpus, planner)
    started = await rs.start("评测")
    first = rs.store.subquestions(started.session_id)[0].id
    planner.moves.extend([Deepen((first,)), Finalize("enough")])
    rounds: List[Dict[str, Any]] = []

    messages = ["开始", "", "", ""]
    for index, message in enumerate(messages):
        planner.start_round()
        result = await rs.step(started.session_id, message)
        rounds.append({
            "round": index + 1,
            "move": result.move,
            "calls": planner.close_round(),
            "revision": result.revision,
            "new_evidence": len(result.new_evidence),
            "asked_user": result.pending_request is not None,
            "session_status": rs.store.get_session(started.session_id).status,
        })
        if result.pending_request is not None:
            planner.start_round()
            answered = await rs.answer_request(
                started.session_id, result.pending_request.id, "没有，用公开数据"
            )
            rounds.append({
                "round": index + 1.5,
                "move": "answer_request",
                "calls": planner.close_round(),
                "revision": answered.revision,
                "new_evidence": len(answered.new_evidence),
                "asked_user": False,
                "session_status": rs.store.get_session(started.session_id).status,
            })
        if result.move == "finalize":
            break

    rs.corpus.close()
    return {
        "rounds": rounds,
        "total_calls": planner.calls,
        "rounds_to_finalize": sum(1 for r in rounds if r["move"] != "answer_request"),
        "reached_finalize": any(r["move"] == "finalize" for r in rounds),
        "asked_the_user": any(r["move"] == "askuser" for r in rounds),
    }


def old_style_count(corpus: Corpus, claim_id: str) -> int:
    """Replay of the pre-P1 rule: distinct clusters among linked evidence."""
    row = corpus._conn.execute(
        "SELECT COUNT(DISTINCT COALESCE(cluster_id, item_id)) AS n"
        " FROM claim_evidence WHERE claim_id=?",
        (claim_id,),
    ).fetchone()
    return int(row["n"] or 0)


def measure_flood(tmp_path: Path, reposts: int = 6) -> Dict[str, Any]:
    """A templated pile-on against one genuine source, counted the two ways.

    Six anonymous reposts in three clusters plus one signed analysis: the old
    rule called that four independent sources and sent it to grading; the P1
    rule says one publisher cast the only vote.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    corpus = Corpus(tmp_path / "flood.db")
    template = "据传该公司估值已达 100 亿美元，即将上市，内部人士透露。"
    items = [
        make_item("real", SourceType.RSS, "深度分析：估值与上市",
                  "审计报告显示该公司 2026 年营收 12 亿元，上市申请已于 2026-08-11 提交交易所。",
                  author="独立分析师"),
    ]
    items += [
        make_item(f"spam{i}", SourceType.REDDIT, f"转发{i}", template, author=None)
        for i in range(reposts)
    ]
    corpus.add_items(items, now=NOW)

    # These fixtures measure how independence is *counted*, so every planted link
    # has to survive: the production evidence cap would truncate the set at
    # `evidence_per_claim` and the curve would end up measuring the cap.
    store = ClaimStore(corpus, evidence_limit=24)
    claim = Claim(id="cl_flood", item_id="mt:real", text="该公司已提交上市申请",
                  status="linked")
    store.upsert_claims([claim])
    links = [EvidenceLink("cl_flood", "mt:real", "clu_real", "rss", 1.0)]
    for index in range(reposts):
        links.append(EvidenceLink(
            "cl_flood", f"mt:spam{index}", f"clu_{index // 2}", "reddit", 0.9
        ))
    store.add_evidence(links)

    votes = store.independence_votes("cl_flood")
    T = noisy_or(votes)
    store.recompute_independence()
    graded = store.get_claim("cl_flood")
    before = old_style_count(corpus, "cl_flood")

    corpus.close()
    return {
        "reposts": reposts,
        "clusters": 1 + (reposts + 1) // 2,
        "independent_sources_before_p1": before,
        "independent_sources_after_p1": distinct_publishers(votes),
        "noisy_or_trust": round(T, 4),
        "stored_trust": round(float(graded.trust or 0.0), 4),
        "verdict_rule_says": classify(T, votes, contradicted=False),
        "grade_min_sources_would_have_admitted_it": before >= 2,
    }


def measure_soft_spot(tmp_path: Path) -> Dict[str, Any]:
    """The known hole, with its number attached rather than left as prose.

    One person publishing under two `source_type` values is counted as two
    `(source_type, publisher)` pairs, so `distinct_publishers` says 2. Cluster
    collapse is the defence against recycling, not against a polyglot author.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    corpus = Corpus(tmp_path / "soft.db")
    corpus.add_items([
        make_item("nl", SourceType.RSS, " newsletter 版",
                  "同一作者的观点：2026 年 Q3 营收 5 亿元。", author="Alice"),
        make_item("hn", SourceType.HACKERNEWS, " HN 版",
                  "同一作者的观点：2026 年 Q3 营收 5 亿元。", author="Alice"),
    ], now=NOW)
    # These fixtures measure how independence is *counted*, so every planted link
    # has to survive: the production evidence cap would truncate the set at
    # `evidence_per_claim` and the curve would end up measuring the cap.
    store = ClaimStore(corpus, evidence_limit=24)
    store.upsert_claims([Claim(id="cl_soft", item_id="mt:nl",
                               text="Q3 营收 5 亿元", status="linked")])
    store.add_evidence([
        EvidenceLink("cl_soft", "mt:nl", "clu_nl", "rss", 1.0),
        EvidenceLink("cl_soft", "mt:hn", "clu_hn", "hackernews", 1.0),
    ])
    votes = store.independence_votes("cl_soft")
    T = noisy_or(votes)
    corpus.close()
    return {
        "scenario": "one author publishing under two source types",
        "distinct_publisher_pairs": distinct_publishers(votes),
        "noisy_or_trust": round(T, 4),
        "verdict_rule_says": classify(T, votes, contradicted=False),
    }


async def run(branches: List[int], out: Path) -> Dict[str, Any]:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        recompute = []
        for count in branches:
            recompute.append(await measure_recompute(root / f"r{count}", count))
        results = {
            "measured_at": "2026-09-29",
            "clock": "fixed 2026-09-29T00:00:00+00:00; no wall-clock assertions",
            "cost_unit": "LLM calls (never latency — see the module docstring)",
            "partial_vs_full_recompute": recompute,
            "rounds_to_resolve": await measure_rounds(root),
            "flood_resistance": measure_flood(root / "flood"),
            # The histogram spec §8 chart 6 asks for: the same claim under
            # growing pile-ons, counted both ways.
            "flood_curve": [
                measure_flood(root / f"flood_k{k}", k) for k in (0, 2, 4, 6, 8)
            ],
            "known_soft_spot": measure_soft_spot(root / "soft"),
        }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results


def print_table(results: Dict[str, Any]) -> None:
    print("\n局部重算 vs 全树重跑（单位：LLM 调用数）")
    print("  分支数   深一条   深全部   比值")
    for row in results["partial_vs_full_recompute"]:
        print(f"  {row['branches']:>6} {row['one_branch_calls']:>8}"
              f" {row['whole_tree_calls']:>8}   {row['ratio']:>5}")

    rounds = results["rounds_to_resolve"]
    print("\n达成定稿所需的轮次（含一轮向用户提问）")
    print("  轮次   动词              调用数  revision  新证据  会话状态")
    for row in rounds["rounds"]:
        print(f"  {row['round']:>5} {row['move']:<16} {row['calls']:>6}"
              f" {row['revision']:>9} {row['new_evidence']:>6}  {row['session_status']}")
    print(f"  → 轮次到定稿 {rounds['rounds_to_finalize']}，总调用数 {rounds['total_calls']}，"
          f"是否定稿 {rounds['reached_finalize']}，是否问过用户 {rounds['asked_the_user']}")

    flood = results["flood_resistance"]
    print("\n灌水抵抗（同文匿名转发 ×6 / 3 簇 + 1 条署名分析）")
    print(f"  independent_sources：旧口径 {flood['independent_sources_before_p1']}"
          f" → 新口径 {flood['independent_sources_after_p1']}")
    print(f"  T={flood['noisy_or_trust']}，判定规则给出「{flood['verdict_rule_says']}」；"
          f"旧口径下它本可进入判级（≥2 源）：{flood['grade_min_sources_would_have_admitted_it']}")

    soft = results["known_soft_spot"]
    print(f"\n已知软肋（同一作者跨两个 source_type）：{soft['distinct_publisher_pairs']}"
          f" 个发布者对，T={soft['noisy_or_trust']} → 「{soft['verdict_rule_says']}」")

    print("\n灌水曲线（同文匿名转发条数 → 两种口径下的 independent_sources）")
    print("  转发数   簇数   旧口径   新口径   T        判定")
    for row in results["flood_curve"]:
        print(f"  {row['reposts']:>6} {row['clusters']:>6} "
              f"{row['independent_sources_before_p1']:>7}"
              f" {row['independent_sources_after_p1']:>7}   {row['noisy_or_trust']:<8}"
              f" {row['verdict_rule_says']}")


def main() -> int:
    from src._cli import force_utf8_output

    force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branches", default="2,4,8",
                        help="comma-separated branch counts for the recompute ratio")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()

    branches = [int(x) for x in args.branches.split(",") if x.strip()]
    results = asyncio.run(run(branches, args.out))
    if not args.json_only:
        print_table(results)
    print(f"\n写出 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

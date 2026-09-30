"""The multi-turn / flood harness (spec §5.5's objective measures).

These are guard rails on the *harness*, not on the model: if the numbers in
`docs/evaluation.md` ever stop being produced the way they were measured, the
test that reproduces them should go red rather than the doc quietly rotting.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_multiturn as harness  # noqa: E402


# ------------------------------------------------------------------- recompute
def test_deepening_one_branch_does_not_grow_with_the_tree(tmp_path: Path) -> None:
    """The whole point of `step`: pricing one branch must not track tree size."""
    small = asyncio.run(harness.measure_recompute(tmp_path / "small", 2))
    big = asyncio.run(harness.measure_recompute(tmp_path / "big", 8))
    assert small["one_branch_calls"] == big["one_branch_calls"]
    assert big["ratio"] > small["ratio"]


def test_whole_tree_cost_is_linear_in_branch_count(tmp_path: Path) -> None:
    """`next_move` once plus one answer per branch — no hidden extra passes."""
    rows = [
        asyncio.run(harness.measure_recompute(tmp_path / f"n{n}", n))
        for n in (2, 4)
    ]
    by_branch = {row["branches"]: row["whole_tree_calls"] for row in rows}
    assert by_branch[4] - by_branch[2] == 2  # two more branches, two more answers


# ----------------------------------------------------------------------- rounds
def test_a_round_that_asks_the_user_parks_and_still_resolves(tmp_path: Path) -> None:
    """spec §9.4: `awaiting_user` is a pause, not a dead session."""
    result = asyncio.run(harness.measure_rounds(tmp_path))
    moves = [row["move"] for row in result["rounds"]]
    assert moves[0] == "askuser"
    assert "answer_request" in moves
    assert result["reached_finalize"] is True
    assert result["rounds"][-1]["session_status"] == "drafting"


# ----------------------------------------------------------------------- flood
def test_the_flood_counts_four_old_style_and_one_now(tmp_path: Path) -> None:
    """The measured pair behind the table in docs/evaluation.md.

    `independent_sources_before_p1` is a replay of the retired rule inside the
    harness, so the comparison stays checkable after the production code moved
    on — the same reason `--tiering marker` still exists.
    """
    row = harness.measure_flood(tmp_path / "flood", reposts=6)
    assert row["independent_sources_before_p1"] == 4
    assert row["independent_sources_after_p1"] == 1
    assert row["grade_min_sources_would_have_admitted_it"] is True
    assert row["verdict_rule_says"] == "unsupported"


def test_more_reposts_never_buy_independence_under_the_new_rule(
    tmp_path: Path,
) -> None:
    """The curve must be flat at 1: volume is not corroboration."""
    curve = [
        harness.measure_flood(tmp_path / f"k{k}", reposts=k)
        for k in (0, 2, 4, 6, 8)
    ]
    assert [row["independent_sources_after_p1"] for row in curve] == [1] * 5
    assert [row["independent_sources_before_p1"] for row in curve] == [1, 2, 3, 4, 5]


def test_the_trust_of_one_signed_analysis_does_not_depend_on_the_spam(
    tmp_path: Path,
) -> None:
    """Anonymous reposts cast no vote, so the aggregate they add is zero."""
    alone = harness.measure_flood(tmp_path / "alone", reposts=0)
    flooded = harness.measure_flood(tmp_path / "flooded", reposts=8)
    assert alone["noisy_or_trust"] == flooded["noisy_or_trust"]


# -------------------------------------------------------------------- soft spot
def test_the_known_soft_spot_is_measured_not_narrated(tmp_path: Path) -> None:
    """One author under two source types still counts twice — recorded as a
    number so the doc cannot quietly overstate the fix."""
    row = harness.measure_soft_spot(tmp_path / "soft")
    assert row["distinct_publisher_pairs"] == 2
    assert row["verdict_rule_says"] == "supported"


def test_no_measurement_reads_the_wall_clock(tmp_path: Path) -> None:
    """The repo bans wall-clock assertions; the harness must obey it too.

    Only the generated request ids differ between runs — everything that is
    reported as a number has to be reproducible.
    """
    def projection(result: dict) -> list:
        return [{k: v for k, v in row.items() if k != "pending_request"}
                for row in result["rounds"]]

    first = asyncio.run(harness.measure_rounds(tmp_path / "a"))
    second = asyncio.run(harness.measure_rounds(tmp_path / "b"))
    assert projection(first) == projection(second)
    assert first["total_calls"] == second["total_calls"]


def test_trust_is_reproducible_once_the_clock_is_pinned(tmp_path: Path) -> None:
    """Freshness ages the score, so `add_items` takes the clock as an argument.

    Without it the fourth decimal of every number in docs/evaluation.md is a
    function of the calendar, and a quoted figure silently rots.
    """
    from src.corpus.store import Corpus
    from src.models import SourceType

    def stored_trust(name: str) -> float:
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
        corpus = Corpus(tmp_path / name / "c.db")
        corpus.add_items([harness.make_item(
            "x", SourceType.RSS, "季度营收", "该公司 2026 年 Q3 营收 5 亿元。",
            author="Alice",
        )], now=harness.NOW)
        row = corpus._conn.execute("SELECT trust FROM items WHERE id='mt:x'").fetchone()
        corpus.close()
        return float(row["trust"])

    assert stored_trust("one") == stored_trust("two")

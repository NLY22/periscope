"""The two committed SVG charts must stay a pure function of the harness output.

A picture is the most convincing artefact in a repository and the easiest to
leave behind when the numbers move, so the guard is not "the file exists" -- it
is that re-rendering from `data/eval/multiturn_results.json` reproduces the
committed bytes, and that every value drawn in the picture is a value in that
JSON. `--check` is the same assertion from the command line, for whoever
regenerates before committing.
"""

from __future__ import annotations

import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.render_eval_charts import (  # noqa: E402
    DEFAULT_SOURCE,
    H,
    MB,
    ML,
    MT,
    PLOT_H,
    PLOT_W,
    render,
)

CHARTS = {
    "docs/assets/flood-independence.svg": "flood",
    "docs/assets/recompute-cost.svg": "recompute",
}
SVG_NS = "http://www.w3.org/2000/svg"


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(DEFAULT_SOURCE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("relative", sorted(CHARTS))
def test_committed_svg_is_exactly_what_the_data_renders_to(relative: str) -> None:
    committed = (REPO_ROOT / relative).read_text(encoding="utf-8")
    assert committed == render(DEFAULT_SOURCE)[relative], (
        f"{relative} no longer matches data/eval/multiturn_results.json -- "
        "run `uv run python scripts/render_eval_charts.py` and commit the result"
    )


@pytest.mark.parametrize("relative", sorted(CHARTS))
def test_rendered_svg_is_a_well_formed_standalone_image(relative: str) -> None:
    svg = render(DEFAULT_SOURCE)[relative]
    root = ET.fromstring(svg)
    # ElementTree folds `xmlns` into the tag, so namespaced equality is only
    # reachable when the declaration parsed -- and an <img>-referenced file has
    # to carry it in the text itself.
    assert root.tag == f"{{{SVG_NS}}}svg"
    assert f'xmlns="{SVG_NS}"' in svg
    assert root.get("viewBox")
    assert root.findtext(f"{{{SVG_NS}}}title"), "an embedded image needs accessible text"


def test_rendering_is_deterministic() -> None:
    assert render(DEFAULT_SOURCE) == render(DEFAULT_SOURCE)


def test_flood_chart_draws_the_measured_series(payload: dict) -> None:
    svg = render(DEFAULT_SOURCE)["docs/assets/flood-independence.svg"]
    curve = payload["flood_curve"]
    before = [row["independent_sources_before_p1"] for row in curve]
    after = [row["independent_sources_after_p1"] for row in curve]
    assert before == [1, 2, 3, 4, 5] and after == [1, 1, 1, 1, 1]
    assert len(re.findall(r"<polyline", svg)) == 2
    # one point label per series, so the printed numbers cannot be invented
    labels = re.findall(r'<text[^>]*font-weight="600"[^>]*>(-?\d+(?:\.\d+)?)</text>', svg)
    assert [int(v) for v in labels] == before + after
    assert "T=0.7998" in svg
    assert "旧口径自 2 条转发起就够进判级门" in svg


def test_recompute_chart_draws_the_measured_call_counts(payload: dict) -> None:
    svg = render(DEFAULT_SOURCE)["docs/assets/recompute-cost.svg"]
    rows = payload["partial_vs_full_recompute"]
    one = [row["one_branch_calls"] for row in rows]
    whole = [row["whole_tree_calls"] for row in rows]
    labels = re.findall(r'<text[^>]*font-weight="600"[^>]*>(-?\d+(?:\.\d+)?)</text>', svg)
    assert [int(v) for v in labels] == one + whole
    assert " / ".join(str(r["ratio"]) for r in rows) in svg
    assert "没有延迟轴" in svg


@pytest.mark.parametrize("relative", sorted(CHARTS))
def test_every_drawn_point_lands_inside_the_plot_box(relative: str) -> None:
    """Off-box geometry is how a hand-rolled chart turns into garbage.

    Only the polylines are checked: the legend markers deliberately sit below
    the axis, and a test that grabs every <circle> would fail on correct art.
    """
    svg = render(DEFAULT_SOURCE)[relative]
    bottom = MT + PLOT_H
    polylines = re.findall(r'<polyline points="([^"]+)"', svg)
    assert len(polylines) == 2, "two series expected"
    for line in polylines:
        for pair in line.split():
            cx, cy = (float(v) for v in pair.split(","))
            assert ML - 0.05 <= cx <= ML + PLOT_W + 0.05, pair
            assert MT - 0.05 <= cy <= bottom + 0.05, pair
    assert MT + PLOT_H == H - MB, "the x axis is meant to land on the bottom margin"
    for y in re.findall(r'<text[^>]*y="([\d.]+)"', svg):
        assert 0 < float(y) < H, f"a text baseline sits off-canvas at y={y}"

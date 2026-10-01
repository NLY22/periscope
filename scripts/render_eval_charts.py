#!/usr/bin/env python3
"""Draw spec §8 charts 6 and 7 from the harness output, with the standard library only.

Why these two and not 4 and 5: chart 4 (macro-F1) and chart 5 (ROC) need the
50-100 human claim verdicts that do not exist yet, and a chart of nothing is
worse than no chart. Charts 6 and 7 have real numbers sitting in
`data/eval/multiturn_results.json` (written by `scripts/eval_multiturn.py`), so
the only thing missing was a picture -- and this repository has no plotting
dependency (no matplotlib, no plotly), which is the maintainer's call to change,
not mine to quietly make while documenting.

Determinism rules the module has to obey, because the output is committed:
no timestamps, no randomness, no float formatting beyond `:%g`, and one fixed
series order. Run with `--check` to fail if the committed SVG no longer matches
the data; that is what keeps a picture from rotting.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = REPO_ROOT / "data" / "eval" / "multiturn_results.json"

W, H = 720, 430
ML, MR, MT, MB = 74, 26, 92, 86  # margins: left, right, top, bottom
PLOT_W = W - ML - MR
PLOT_H = H - MT - MB

INK = "#1f2937"
FAINT = "#9ca3af"
GRID = "#e5e7eb"
SERIES_COLORS = ("#2563eb", "#dc2626")
FONT = "system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif"


def _fmt(value: float) -> str:
    """Compact, locale-independent, and stable enough to diff on."""
    text = f"{value:g}"
    return text


def _ticks(upper: float, steps: int) -> List[float]:
    """`steps` evenly spaced ticks from 0 to `upper`, rounded up to whole numbers."""
    if upper <= 0:
        upper = 1.0
    top = -(-upper // steps) * steps  # ceil to a multiple of steps
    return [top * i / steps for i in range(steps + 1)]


def _x(n: float, lo: float, hi: float) -> float:
    span = hi - lo or 1.0
    return ML + PLOT_W * (n - lo) / span


def _y(n: float, top: float) -> float:
    return MT + PLOT_H * (1.0 - n / top)


def line_chart(
    *,
    title: str,
    subtitle: str,
    footnote: str,
    x_label: str,
    y_label: str,
    xs: Sequence[float],
    series: Sequence[Tuple[str, Sequence[float], bool]],
) -> str:
    """A multi-series line chart as a standalone SVG string.

    `series` entries are (label, values, show_value_labels). Values are drawn in
    the given order, so the legend order and the colour order cannot drift.
    """
    lo, hi = min(xs), max(xs)
    peak = max(max(values) for _, values, _ in series)
    ticks = _ticks(peak, 5)
    top = ticks[-1]

    parts: List[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" role="img" font-family="{FONT}">',
        f"<title>{title}</title>",
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="{ML}" y="34" fill="{INK}" font-size="21" font-weight="600">{title}</text>',
        f'<text x="{ML}" y="58" fill="#4b5563" font-size="14">{subtitle}</text>',
    ]

    for tick in ticks:
        gy = _y(tick, top)
        parts.append(
            f'<line x1="{ML}" y1="{gy:.1f}" x2="{ML + PLOT_W}" y2="{gy:.1f}" '
            f'stroke="{GRID}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{ML - 12}" y="{gy + 4:.1f}" fill="{FAINT}" font-size="13" '
            f'text-anchor="end">{_fmt(tick)}</text>'
        )

    axis_bottom = _y(0.0, top)
    parts.append(
        f'<line x1="{ML}" y1="{axis_bottom:.1f}" x2="{ML + PLOT_W}" y2="{axis_bottom:.1f}" '
        f'stroke="{INK}" stroke-width="1.5"/>'
    )
    parts.append(
        f'<line x1="{ML}" y1="{MT}" x2="{ML}" y2="{axis_bottom:.1f}" '
        f'stroke="{INK}" stroke-width="1.5"/>'
    )

    for x in xs:
        gx = _x(x, lo, hi)
        parts.append(
            f'<line x1="{gx:.1f}" y1="{axis_bottom:.1f}" x2="{gx:.1f}" '
            f'y2="{axis_bottom + 5:.1f}" stroke="{INK}" stroke-width="1.5"/>'
        )
        parts.append(
            f'<text x="{gx:.1f}" y="{axis_bottom + 22:.1f}" fill="{INK}" font-size="13" '
            f'text-anchor="middle">{_fmt(x)}</text>'
        )

    parts.append(
        f'<text x="{ML + PLOT_W / 2:.1f}" y="{H - 34}" fill="#4b5563" font-size="14" '
        f'text-anchor="middle">{x_label}</text>'
    )
    parts.append(
        f'<text x="20" y="{MT + PLOT_H / 2:.1f}" fill="#4b5563" font-size="14" '
        f'text-anchor="middle" transform="rotate(-90 20 {MT + PLOT_H / 2:.1f})">{y_label}</text>'
    )

    for index, (label, values, show_labels) in enumerate(series):
        colour = SERIES_COLORS[index % len(SERIES_COLORS)]
        points = " ".join(
            f"{_x(x, lo, hi):.1f},{_y(v, top):.1f}" for x, v in zip(xs, values)
        )
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{colour}" '
            f'stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for x, v in zip(xs, values):
            gx, gy = _x(x, lo, hi), _y(v, top)
            parts.append(
                f'<circle cx="{gx:.1f}" cy="{gy:.1f}" r="4.5" fill="#ffffff" '
                f'stroke="{colour}" stroke-width="2.5"/>'
            )
            if show_labels:
                parts.append(
                    f'<text x="{gx:.1f}" y="{gy - 12:.1f}" fill="{colour}" '
                    f'font-size="13" font-weight="600" text-anchor="middle">{_fmt(v)}</text>'
                )

    legend_y = MT + PLOT_H + 52
    for index, (label, _values, _show) in enumerate(series):
        colour = SERIES_COLORS[index % len(SERIES_COLORS)]
        lx = ML + index * 300
        parts.append(
            f'<line x1="{lx}" y1="{legend_y}" x2="{lx + 26}" y2="{legend_y}" '
            f'stroke="{colour}" stroke-width="3" stroke-linecap="round"/>'
        )
        parts.append(
            f'<circle cx="{lx + 13}" cy="{legend_y}" r="4.5" fill="#ffffff" '
            f'stroke="{colour}" stroke-width="2.5"/>'
        )
        parts.append(
            f'<text x="{lx + 34}" y="{legend_y + 5}" fill="{INK}" font-size="14">'
            f"{label}</text>"
        )

    parts.append(
        f'<text x="{ML}" y="{H - 10}" fill="{FAINT}" font-size="12">{footnote}</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts) + "\n"


def _first_admitted_reposts(curve: Sequence[Dict]) -> str:
    """The repost count at which the *old* count would have cleared the grade gate.

    Kept out of the picture and written into the caption: the SVG stays a pure
    function of the two series, while this one number is the claim being made.
    """
    hits = [row["reposts"] for row in curve if row["grade_min_sources_would_have_admitted_it"]]
    return _fmt(min(hits)) if hits else "never"


def flood_chart(payload: Dict) -> str:
    curve = payload["flood_curve"]
    xs = [row["reposts"] for row in curve]
    before = [row["independent_sources_before_p1"] for row in curve]
    after = [row["independent_sources_after_p1"] for row in curve]
    trust = curve[0]["noisy_or_trust"]
    verdict = curve[0]["verdict_rule_says"]
    return line_chart(
        title="§8 图 6 · 同文匿名转发灌不进独立性",
        subtitle=(
            f"x 轴 = 转发条数；旧口径数 SimHash 簇，新口径先折叠重复再数不同发布者。"
            f" T={_fmt(trust)} 恒定（判定 {verdict}）"
        ),
        x_label="同文匿名转发条数（每 2 条一个 SimHash 簇）",
        y_label="independent_sources",
        xs=xs,
        series=(
            ("旧口径（簇数，P1 之前）", before, True),
            ("新口径（不同 (source_type, publisher)）", after, True),
        ),
        footnote=(
            "来源 scripts/eval_multiturn.py，时钟钉在 2026-09-29T00:00:00+00:00；"
            f"旧口径自 {_first_admitted_reposts(curve)} 条转发起就够进判级门，新口径全程不达门"
        ),
    )


def recompute_chart(payload: Dict) -> str:
    rows = payload["partial_vs_full_recompute"]
    xs = [row["branches"] for row in rows]
    one = [row["one_branch_calls"] for row in rows]
    whole = [row["whole_tree_calls"] for row in rows]
    ratios = [row["ratio"] for row in rows]
    return line_chart(
        title="§8 图 7 · 局部重算 vs 全树重跑（单位是 LLM 调用数）",
        subtitle=(
            "同一个动词 Deepen，只是点名的分支数不同；比值 "
            + " / ".join(_fmt(r) for r in ratios)
            + "。**没有延迟轴** —— 本仓库禁止挂钟断言"
        ),
        x_label="子问题分支数",
        y_label="LLM 调用数",
        xs=xs,
        series=(
            ("只深被点名的 1 条", one, True),
            ("深全部（整棵树重走）", whole, True),
        ),
        footnote="来源 scripts/eval_multiturn.py；全树 = 1 + 分支数，深一条恒为 2（next_move + answer）",
    )


def render(source: Path) -> Dict[str, str]:
    payload = json.loads(source.read_text(encoding="utf-8"))
    return {
        "docs/assets/flood-independence.svg": flood_chart(payload),
        "docs/assets/recompute-cost.svg": recompute_chart(payload),
    }


def main(argv: Sequence[str] | None = None) -> int:
    from src._cli import force_utf8_output

    force_utf8_output()  # it prints captions quoting source text, and chart labels
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if the committed SVG no longer matches the data",
    )
    args = parser.parse_args(argv)

    for relative, svg in render(args.source).items():
        path = REPO_ROOT / relative
        if args.check:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != svg:
                print(f"STALE {relative} (re-run without --check)", file=sys.stderr)
                return 1
            print(f"OK {relative}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg, encoding="utf-8")
        print(f"wrote {relative} ({len(svg)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

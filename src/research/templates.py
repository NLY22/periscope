"""Report templates: 背景调查 / 市场调研 / 方法探索 need different shapes.

A flat list of answered sub-questions is not a report. What makes one is the
skeleton the reader expects: a background investigation wants a timeline and
who says what; market research wants scale, growth and named players; method
exploration wants the practices, their constraints and how strongly each is
evidenced.

Sections are assigned by keyword rules over the sub-question text, and the
three computed blocks (timeline, disagreements, open questions) come from
stored data — dates, claim verdicts, unanswered sub-questions and the widening
attempts — never from the model. That keeps the skeleton honest even when the
planner is absent: an empty section is reported as 未覆盖 instead of filled in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

TIMELINE_LIMIT = 8
DISAGREEMENT_LIMIT = 6


@dataclass
class Section:
    heading: str
    keywords: List[str] = field(default_factory=list)
    guidance: str = ""


@dataclass
class ReportTemplate:
    name: str
    label: str
    sections: List[Section]
    closing: List[str]

    def section_for(self, text: str) -> Optional[str]:
        lowered = (text or "").lower()
        best: tuple[int, str] | None = None
        for section in self.sections:
            score = sum(1 for keyword in section.keywords if keyword.lower() in lowered)
            if score and (best is None or score > best[0]):
                best = (score, section.heading)
        return best[1] if best else None

    def decompose_guidance(self) -> str:
        lines = [f"- {s.heading}：{s.guidance}" for s in self.sections if s.guidance]
        return "\n".join(lines)


BACKGROUND = ReportTemplate(
    name="background",
    label="背景调查",
    sections=[
        Section("事件经过", ["发生了什么", "经过", "过程", "时间线", "timeline", "story"], "按时间顺序发生的事实"),
        Section("参与方与说法", ["谁", "参与", "声称", "source", "says", "说法", "各方"], "每个关键说法出自谁"),
        Section("分歧与矛盾", ["分歧", "矛盾", "contradict", "争议", "denies", "否认"], "相互冲突的说法"),
        Section("关键数字与日期", ["金额", "估值", "日期", "数字", "round", "valuation", "$"], "可核查的量与时间点"),
    ],
    closing=["未决问题"],
)

MARKET = ReportTemplate(
    name="market",
    label="市场调研",
    sections=[
        Section("规模与增长", ["规模", "market size", "增长", "growth", "份额", "share"], "带来源的规模与增速"),
        Section("主要玩家", ["玩家", "厂商", "competitor", "公司", "vendor", "leader"], "谁在做什么"),
        Section("信号与动向", ["信号", "trend", "动向", "发布", "release", "收购", "融资"], "最近改变格局的事件"),
        Section("风险与不确定性", ["风险", "risk", "不确定", "监管", "regulation"], "唱反调的证据"),
    ],
    closing=["未决问题"],
)

METHOD = ReportTemplate(
    name="method",
    label="方法探索",
    sections=[
        Section("做法清单", ["做法", "方法", "method", "approach", "技术", "方案"], "被提出的具体做法"),
        Section("约束与代价", ["约束", "代价", "limit", "tradeoff", "成本", "开销", "constraint"], "每种做法的边界"),
        Section("证据强度", ["证据", "验证", "evidence", "benchmark", "实验", "evaluat"], "结论靠什么撑着"),
        Section("可复用步骤", ["步骤", "step", "复用", "how to", "操作"], "读者能直接照做的部分"),
    ],
    closing=["未决问题"],
)

TEMPLATES: Dict[str, ReportTemplate] = {
    "background": BACKGROUND,
    "market": MARKET,
    "method": METHOD,
}

_HINTS = {
    "market": ["市场", "行业", "规模", "份额", "玩家", "market", "industry", "competitor", "增长"],
    "method": ["方法", "怎么做", "如何实现", "技术路线", "方案", "method", "how to", "approach", "算法"],
    "background": ["背景", "调查", "发生了什么", "经过", "为什么", "background", "investigat", "story"],
}


def resolve_template(name: Optional[str], question: str = "") -> Optional[ReportTemplate]:
    """Pick a template by name, or infer one from the question. `flat`/`none`
    disables structuring so a session renders exactly as before."""
    key = (name or "auto").strip().lower()
    if key in {"flat", "none", "off"}:
        return None
    if key in TEMPLATES:
        return TEMPLATES[key]
    if key != "auto":
        return None
    lowered = (question or "").lower()
    scored = sorted(
        (
            (sum(1 for hint in hints if hint.lower() in lowered), template_key)
            for template_key, hints in _HINTS.items()
        ),
        key=lambda pair: (-pair[0], pair[1]),
    )
    if not scored or scored[0][0] == 0:
        return None
    return TEMPLATES[scored[0][1]]


def build_skeleton(
    template: ReportTemplate,
    subquestions: List[Any],
    evidence_rows: List[Dict[str, Any]],
    actions_by_subquestion: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Group sub-questions into the template's sections, plus computed blocks."""
    grouped: Dict[str, List[Any]] = {section.heading: [] for section in template.sections}
    uncategorised: List[Any] = []
    for sq in subquestions:
        heading = template.section_for(sq.text)
        if heading is None:
            uncategorised.append(sq)
        else:
            grouped[heading].append(sq)

    timeline: List[Dict[str, str]] = []
    for row in evidence_rows:
        published = (row.get("published_at") or "")[:10]
        if published:
            timeline.append({"date": published, "title": row.get("title", ""), "url": row.get("url", "")})
    timeline.sort(key=lambda entry: entry["date"])

    disagreements = [
        row for row in evidence_rows
        if any((claim.get("verdict") == "contested") for claim in row.get("claims", []))
    ]

    return {
        "sections": grouped,
        "uncategorised": uncategorised,
        "timeline": timeline[:TIMELINE_LIMIT],
        "disagreements": disagreements[:DISAGREEMENT_LIMIT],
        "open_questions": [sq for sq in subquestions if getattr(sq, "status", "") != "answered"],
    }

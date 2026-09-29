"""Retrieval ablation harness: measure what each evidence layer is worth.

    uv run python scripts/eval_retrieval.py

Runs the same query set against the same fixture corpus under six
configurations and prints recall / precision / nDCG / MRR. This is the file to
read before claiming that a widening or tiering change "helps".

Provenance warning, stated because it is easy to misread these tables:
`expansion-stub` and `semantic-stub` are mechanism stand-ins defined below —
they prove the legs are wired and that *if* the terms/vectors are right the
metrics move, but they are not a language model. To measure real gains, rerun
with `--expander llm --embedder provider`, which uses the configured provider.
Tiering (claimable) is the one leg that is not a stub: it is the production
code path.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.corpus.metrics import evaluate  # noqa: E402
from src.corpus.retrieval import HybridRetriever  # noqa: E402
from src.corpus.semantic import EmbeddingIndex  # noqa: E402
from src.corpus.store import Corpus  # noqa: E402
from src.models import ContentItem, SourceType  # noqa: E402

FIXTURE = REPO_ROOT / "data" / "eval" / "corpus_fixture.json"
QUERIES = REPO_ROOT / "data" / "eval" / "queries.json"
DEFAULT_OUT = REPO_ROOT / "data" / "eval" / "results.json"

CONFIGS: List[Dict[str, Any]] = [
    {"name": "A 旧行为(全量文本, 单词条)", "tier": "all", "term_budget": 4},
    {"name": "B +证据分层", "tier": "claimable", "term_budget": 4},
    {"name": "C B+放宽词条", "tier": "claimable", "term_budget": 2},
    {"name": "D B+查询扩展(stub)", "tier": "claimable", "term_budget": 4, "expander": "stub"},
    {"name": "E B+语义路(stub)", "tier": "claimable", "term_budget": 4, "embedder": "stub"},
    {
        "name": "F 全开(分层+扩展+语义+放宽)",
        "tier": "claimable",
        "term_budget": 4,
        "expander": "stub",
        "embedder": "stub",
        "widen": True,
    },
]

# Surface-form groups the stubs share. A real model would learn these; here
# they are declared so the table is reproducible and obviously synthetic.
_SURFACE_GROUPS = [
    ["估值", "valuation", "$3 billion", "30 亿"],
    ["融资", "round", "Series B", "B 轮", "close", "关闭"],
    ["领投", "led by", "Northwind Capital", "Northwind"],
    ["OpenForge", "openforge"],
    ["parser", "rewrite", "解析器", "trigram", "索引"],
]


class StubExpander:
    """AIClient-compatible stand-in: maps a query to its cross-language forms."""

    def __init__(self, planned: List[str]):
        self.planned = planned

    async def complete(self, system: str, user: str, **kwargs) -> str:
        return json.dumps({"terms": self.planned}, ensure_ascii=False)


class StubEmbedder:
    def __init__(self):
        self.calls = 0

    async def embed(self, texts: List[str]) -> List[List[float]]:
        self.calls += 1
        return [self._vec(t) for t in texts]

    @staticmethod
    def _vec(text: str) -> List[float]:
        lowered = text.lower()
        vector = [0.0] * len(_SURFACE_GROUPS)
        for slot, group in enumerate(_SURFACE_GROUPS):
            for form in group:
                if form.lower() in lowered:
                    vector[slot] = 1.0
                    break
        return vector


class RecordingRetriever(HybridRetriever):
    """Adds widening (looser terms) as an explicit second attempt."""

    def __init__(self, *args, widen: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.widen = widen

    async def gather(self, query: str, limit: int, term_budget: int = 4, source_types=None):
        rows = await super().gather(query, limit, term_budget=term_budget, source_types=source_types)
        if self.widen and len(rows) < max(3, limit // 3):
            more = await super().gather(query, limit, term_budget=2, source_types=source_types)
            seen = {row["id"] for row in rows}
            rows.extend(row for row in more if row["id"] not in seen)
        return rows[:limit]


def load_queries(path: Path = QUERIES) -> List[Dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))["queries"]


def evaluate_metrics(
    runs: Dict[str, List[str]], queries: List[Dict[str, Any]], ks: tuple = (5, 10)
) -> Dict[str, float]:
    return evaluate(runs, {entry["id"]: entry["relevant"] for entry in queries}, ks=ks)


def build_corpus(tmp_path: Path, tiering: str = "sections") -> Corpus:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    corpus = Corpus(tmp_path / "eval_corpus.db")
    items = []
    for raw in payload["items"]:
        items.append(
            ContentItem(
                id=raw["id"],
                source_type=SourceType(raw["source_type"]),
                title=raw["title"],
                url=raw["url"],
                content=raw["content"],
                author="fixture",
                published_at=datetime.fromisoformat(raw["published_at"]),
                fetched_at=datetime(2026, 9, 22, tzinfo=timezone.utc),
            )
        )
    corpus.add_items(items, tiering=tiering)
    corpus.recompute_clusters(max_distance=3, lookback_rows=500)
    return corpus


async def run_config(corpus: Corpus, config: Dict[str, Any], queries: List[Dict[str, Any]], limit: int):
    runs: Dict[str, List[str]] = {}
    for entry in queries:
        expander = None
        embedder = None
        index = None
        if config.get("expander") == "stub":
            expander = StubExpander(entry.get("expansion", []))
        if config.get("embedder") == "stub":
            embedder = StubEmbedder()
            index = EmbeddingIndex(corpus, f"stub-{config['name']}")
            retriever = RecordingRetriever(
                corpus,
                tier=config["tier"],
                index=index,
                embedder=embedder,
                expansion_client=expander,
                widen=config.get("widen", False),
            )
            await retriever.index_pending()
        else:
            retriever = RecordingRetriever(
                corpus,
                tier=config["tier"],
                expansion_client=expander,
                widen=config.get("widen", False),
            )
        rows = await retriever.gather(entry["query"], limit=limit, term_budget=config["term_budget"])
        runs[entry["id"]] = [row["id"] for row in rows]
    return runs


def markdown_table(rows: List[List[str]]) -> str:
    width = [max(len(str(r[i])) for r in rows) for i in range(len(rows[0]))]
    out = []
    for index, row in enumerate(rows):
        out.append("| " + " | ".join(str(cell).ljust(width[i]) for i, cell in enumerate(row)) + " |")
        if index == 0:
            out.append("|" + "|".join("-" * (w + 2) for w in width) + "|")
    return "\n".join(out)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, default=10, help="rank depth scored")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument(
        "--tiering", choices=("sections", "marker"), default="sections",
        help="分层判据：sections 用 scraper 声明的层级，marker 复现 P0 之前的"
             "标记反解（消融 A 档）。同一份语料两种切法，数字才可比。",
    )
    args = parser.parse_args()

    queries = load_queries()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        corpus = build_corpus(Path(tmp), args.tiering)
        table = [["配置", "recall@5", f"recall@{args.top_k}", "precision@5", f"nDCG@{args.top_k}", "MRR"]]
        results: Dict[str, Any] = {
            "top_k": args.top_k, "tiering": args.tiering, "configs": []
        }
        for config in CONFIGS:
            runs = await run_config(corpus, config, queries, args.top_k)
            metrics = evaluate_metrics(runs, queries, ks=(5, args.top_k))
            table.append(
                [
                    config["name"],
                    f"{metrics['recall@5']:.3f}",
                    f"{metrics[f'recall@{args.top_k}']:.3f}",
                    f"{metrics['precision@5']:.3f}",
                    f"{metrics[f'ndcg@{args.top_k}']:.3f}",
                    f"{metrics['mrr']:.3f}",
                ]
            )
            results["configs"].append({"name": config["name"], "settings": {
                k: v for k, v in config.items() if k != "name"}, "metrics": metrics, "runs": runs})
        results["provenance"] = {
            "fixture": str(FIXTURE.relative_to(REPO_ROOT)),
            "queries": str(QUERIES.relative_to(REPO_ROOT)),
            "stub_legs": ["expansion-stub", "semantic-stub"],
            "note": (
                "Stub legs prove plumbing, not model quality. Rerun with a real "
                "provider (--expander llm --embedder provider) before quoting "
                "expansion or semantic gains."
            ),
        }
        corpus.close()

    args.out.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    if not args.json_only:
        print(markdown_table(table))
        print(f"\n写入 {args.out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))

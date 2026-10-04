"""Retrieval ablation harness: measure what each evidence layer is worth.

    uv run python scripts/eval_retrieval.py

Runs the same query set against the same fixture corpus under six
configurations and prints recall / precision / nDCG / MRR. This is the file to
read before claiming that a widening or tiering change "helps".

Provenance warning, stated because it is easy to misread these tables:
`expansion-stub` and `semantic-stub` are mechanism stand-ins defined below —
they prove the legs are wired and that *if* the terms/vectors are right the
metrics move, but they are not a language model. To measure real gains, run

    uv run python scripts/eval_retrieval.py --expander llm
    uv run python scripts/eval_retrieval.py --embedder provider --embedding-model text-embedding-3-small

which build the legs from `data/config.json` (the expander goes through the
same cached client the product uses, so a rerun costs nothing new). A leg that
cannot be built — no config, no key, a hub without an embeddings endpoint — is
recorded as `off`, never quietly swapped back to a stub: a table that swaps a
real model for a stand-in is how a fake number gets published.
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

from src._cli import display_path, force_utf8_output  # noqa: E402
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


def load_app_config(config_path: Path | None = None):
    """Read the same config the product reads, or return None with a reason."""
    from src.models import Config
    from src.storage.manager import StorageManager

    path = config_path or (REPO_ROOT / "data" / "config.json")
    if not path.exists():
        return None, f"没有 {display_path(path, REPO_ROOT)}（先 cp data/config.example.json data/config.json）"
    try:
        return Config.model_validate(json.loads(path.read_text(encoding="utf-8"))), None
    except Exception as exc:
        return None, f"{display_path(path, REPO_ROOT)} 读不出来：{type(exc).__name__}"


class Legs:
    """Builds the optional retrieval legs for one run, and reports the truth.

    `expander_for` is per-query because the stub plans its terms from the
    labelled fixture; a real model sees only the query, like in production.
    A leg that cannot be built is switched to `off` and said out loud — it is
    never silently replaced by the stub, because that is how a stand-in number
    ends up quoted as a model result.
    """

    def __init__(self, expander_mode: str, embedder_mode: str, embedding_model: str = ""):
        self.expander_mode = expander_mode
        self.embedder_mode = embedder_mode
        self.embedding_model = embedding_model
        self.notes: List[str] = []
        self._client = None
        self._embedder = None
        self._app_config = None
        self._config_read = False

    def app_config(self):
        if not self._config_read:
            self._app_config, reason = load_app_config()
            if reason:
                self.notes.append(reason)
            self._config_read = True
        return self._app_config

    def expander_for(self, entry: Dict[str, Any]):
        if self.expander_mode == "off":
            return None
        if self.expander_mode == "stub":
            return StubExpander(entry.get("expansion", []))

        from src.ai.cache import CachingAIClient, ResponseCache
        from src.ai.client import create_ai_client

        if self._client is None:
            cfg = self.app_config()
            if cfg is None:
                self.expander_mode = "off"
                self.notes.append("扩展腿记为 off（不是 stub）：拿不到可用配置或密钥")
                return None
            cache = ResponseCache(REPO_ROOT / "data" / "llm_cache.db")
            self._client = CachingAIClient(
                create_ai_client(cfg.ai), cache, throttle_sec=cfg.ai.throttle_sec
            )
        return self._client

    def embedder(self):
        if self.embedder_mode == "off":
            return None
        if self.embedder_mode == "stub":
            return StubEmbedder()
        if self._embedder is not None:
            return self._embedder

        import os

        from src.ai.embeddings import EmbeddingClient

        cfg = self.app_config()
        model = self.embedding_model or (cfg.retrieval.embedding_model if cfg else "")
        if cfg is None or not model:
            self.embedder_mode = "off"
            self.notes.append("语义腿记为 off（不是 stub）：没给 --embedding-model，配置里也是空的")
            return None

        # The CLI flag is the operator overriding retrieval.semantic, so build
        # the client directly instead of asking from_config to re-veto it.
        base = cfg.retrieval.embedding_base_url or cfg.ai.base_url
        key_env = cfg.retrieval.embedding_api_key_env or cfg.ai.api_key_env
        key = os.environ.get(key_env or "", "").strip()
        if not base or not key:
            self.embedder_mode = "off"
            self.notes.append(
                f"语义腿记为 off：base_url={'有' if base else '空'}，环境变量 {key_env or '(未指定)'} 未导出"
            )
            return None
        self._embedder = EmbeddingClient(base_url=base, api_key=key, model=model)
        return self._embedder

    def index_model(self) -> str:
        if self.embedder_mode == "stub":
            return "stub"
        return self.embedding_model or "provider"


async def run_config(
    corpus: Corpus,
    config: Dict[str, Any],
    queries: List[Dict[str, Any]],
    limit: int,
    legs: Legs | None = None,
):
    legs = legs or Legs("stub", "stub")
    runs: Dict[str, List[str]] = {}
    for entry in queries:
        expander = None
        embedder = None
        index = None
        if config.get("expander"):
            expander = legs.expander_for(entry)
        if config.get("embedder"):
            embedder = legs.embedder()
            index = EmbeddingIndex(corpus, f"{legs.index_model()}-{config['name']}")
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
    force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, default=10, help="rank depth scored")
    parser.add_argument(
        "--out", type=Path, default=None,
        help="结果文件；用了真模型腿时默认写到 results.<腿>.json，不覆盖文档表锚定的那份",
    )
    parser.add_argument("--json-only", action="store_true")
    parser.add_argument(
        "--expander", choices=("stub", "llm", "off"), default="stub",
        help="查询扩展腿：stub 只证明通路，llm 走 data/config.json 配的模型（经同一套缓存）",
    )
    parser.add_argument(
        "--embedder", choices=("stub", "provider", "off"), default="stub",
        help="语义腿：stub 是同义形替身，provider 需要 --embedding-model 或配置里已填",
    )
    parser.add_argument("--embedding-model", default="", help="provider 腿用的向量模型名")
    parser.add_argument(
        "--tiering", choices=("sections", "marker"), default="sections",
        help="分层判据：sections 用 scraper 声明的层级，marker 复现 P0 之前的"
             "标记反解（消融 A 档）。同一份语料两种切法，数字才可比。",
    )
    args = parser.parse_args()

    legs = Legs(args.expander, args.embedder, args.embedding_model)
    real_legs = args.expander == "llm" or args.embedder == "provider"
    out = args.out or (
        DEFAULT_OUT if not real_legs
        else DEFAULT_OUT.with_name(f"results.{args.expander}{args.embedder}.json")
    )

    queries = load_queries()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        corpus = build_corpus(Path(tmp), args.tiering)
        table = [["配置", "recall@5", f"recall@{args.top_k}", "precision@5", f"nDCG@{args.top_k}", "MRR"]]
        results: Dict[str, Any] = {
            "top_k": args.top_k, "tiering": args.tiering, "configs": []
        }
        for config in CONFIGS:
            runs = await run_config(corpus, config, queries, args.top_k, legs)
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
            "fixture": display_path(FIXTURE, REPO_ROOT),
            "queries": display_path(QUERIES, REPO_ROOT),
            "legs": {
                "expander": legs.expander_mode,
                "embedder": legs.embedder_mode,
                "embedding_model": legs.embedding_model,
            },
            "stub_legs": [
                label
                for label, mode in (("expansion-stub", legs.expander_mode), ("semantic-stub", legs.embedder_mode))
                if mode == "stub"
            ],
            "notes": list(legs.notes),
            "note": (
                "Stub legs prove plumbing, not model quality. A leg that could not "
                "be built is reported as off above, never swapped back to a stub."
            ),
        }
        corpus.close()

    out.write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    if not args.json_only:
        print(markdown_table(table))
        if legs.notes:
            print("\n腿的实际状态：")
            for note in legs.notes:
                print(f"- {note}")
        if legs.expander_mode != args.expander or legs.embedder_mode != args.embedder:
            print(
                f"\n请求的腿（expander={args.expander}, embedder={args.embedder}）"
                f"实际降级为（{legs.expander_mode}, {legs.embedder_mode}）—— 表中相应行不是真模型数字。"
            )
        print(f"\n写入 {display_path(out, REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))

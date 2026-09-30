"""Import a user export into the evidence corpus from the command line.

    uv run python scripts/import_corpus.py --file export.json
    cat export.json | uv run python scripts/import_corpus.py --file -

Why a CLI at all when the MCP tool and the web endpoint exist: the export comes
from a browser session on the same machine, and the fastest honest path is one
command that says what landed and what was rejected.

This reaches no network. The whole point of the path is the spec's exclusion
list — no captcha solving, no request signing, no account pools — so a gated
source arrives as text the user's own account can already see, with its
authorship tiers declared. See `src/corpus/ingest.py` for the payload shape.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.corpus.ingest import IngestError, import_payload  # noqa: E402
from src.corpus.sections import TIERINGS  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="export JSON path, or - for stdin")
    parser.add_argument("--data-dir", default="data", help="directory holding corpus.db")
    parser.add_argument("--config", default=None, help="config path (default: <data-dir>/config.json)")
    parser.add_argument(
        "--tiering", choices=TIERINGS, default="sections",
        help="which layering rule computes `claimable` at write time; keep "
             "`marker` only to reproduce the pre-P0 ablation arm",
    )
    parser.add_argument("--dry-run", action="store_true", help="validate and report, store nothing")
    parser.add_argument("--json", action="store_true", dest="as_json", help="print the report as JSON")
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"输入不是合法 JSON：{exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        from src.corpus.ingest import preview_payload

        try:
            report = preview_payload(payload)
        except IngestError as exc:
            print(f"解析失败：{exc}", file=sys.stderr)
            return 2
        if args.as_json:
            print(json.dumps(report, ensure_ascii=False))
        else:
            print(f"可导入 {report['items_total_seen']} 条，其中 "
                  f"{report['claimable_nonempty']} 条有可 claim 的作者层（未写入）")
            for row in report["rejected"]:
                print(f"  第 {int(row['index']) + 1} 条不收：{row['reason']}", file=sys.stderr)
        return 0

    from src.web.server import build_orchestrator

    orchestrator = build_orchestrator(args.data_dir, args.config)
    corpus = orchestrator.get_corpus()
    if corpus is None:
        print("corpus 已在配置里关闭（corpus.enabled = false）", file=sys.stderr)
        return 2
    try:
        report = import_payload(corpus, payload, tiering=args.tiering)
    except IngestError as exc:
        print(f"导入失败：{exc}", file=sys.stderr)
        return 2
    finally:
        corpus.close()

    if args.as_json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(f"新增 {report['items_new']} 条 / 处理 {report['items_total_seen']} 条"
              f"（其中 {report['claimable_nonempty']} 条有作者亲写层）")
        for row in report["rejected"]:
            print(f"  拒绝 item {row['index']}：{row['reason']}")
        if not report["items_new"] and not report["items_total_seen"]:
            print("没有条目入库，检查上面列出的拒绝原因")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

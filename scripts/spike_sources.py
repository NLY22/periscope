"""Run a source-reachability probe and print the verdict (spec §6).

    uv run python scripts/spike_sources.py --source tieba --kw python --online
    uv run python scripts/spike_sources.py --source xiaohongshu \
        --url "https://www.xiaohongshu.com/explore/<note id>" --online

**Nothing is fetched without `--online`.** That flag exists because a probe that
silently hits third-party sites from a test run or an agent session is a side
effect nobody asked for, and because the recorded conclusions should be
re-checked on purpose, not incidentally. The judgement logic itself is
network-free and covered by `tests/test_reachability_probe.py`.

Compliance lines from the spec, restated where they bite: single account at
most, only content the account can already see, per-host courtesy, and never
captcha solving, request signing or account pools. A `signed_required` verdict
is a stop sign for this project, not a puzzle to solve.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.sources.reachability import probe_tieba, probe_xiaohongshu, summarise  # noqa: E402

DEFAULT_OUT = REPO_ROOT / "data" / "eval" / "reachability_results.json"


async def _run(source: str, kw: str, url: str) -> list:
    import httpx

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/124.0.0.0 Safari/537.36",
               "Accept-Language": "zh-CN,zh;q=0.9"}
    async with httpx.AsyncClient(headers=headers, timeout=20.0,
                                 follow_redirects=True) as client:
        if source == "tieba":
            return await probe_tieba(client, kw or "python")
        if not url:
            raise SystemExit("--url is required for xiaohongshu step 1 "
                             "(find a note id from what your own account can see)")
        return await probe_xiaohongshu(client, url, keyword=kw)


def main(argv: list[str] | None = None) -> int:
    from src._cli import force_utf8_output

    force_utf8_output()  # probe output echoes page text, which is not ours to sanitize
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, choices=("tieba", "xiaohongshu"))
    parser.add_argument("--kw", default="", help="keyword for tieba / xhs search page")
    parser.add_argument("--url", default="", help="note URL for xiaohongshu step 1")
    parser.add_argument("--online", action="store_true",
                        help="actually issue the requests; without this nothing leaves the machine")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    if not args.online:
        print("未加 --online：不发任何请求。\n"
              "判别逻辑本身是离线可测的：uv run pytest tests/test_reachability_probe.py\n"
              "确认你要跑之后：加 --online 重跑本命令。", file=sys.stderr)
        return 2

    probes = asyncio.run(_run(args.source, args.kw, args.url))
    summary = summarise(probes)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps({"ran_at": datetime.now(timezone.utc).isoformat(), **summary},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")

    for name, block in summary.items():
        print(f"\n{name}: {block['judgement']}"
              f"（正文/楼层可达：{block['floors_or_body_reachable']}）")
        for row in block["routes"]:
            print(f"  {row['status']} {row['bytes']:>8}B  {row['verdict']:<16} {row['url']}")
            print(f"           {row['detail']}")

    judgement = next(iter(summary.values()))["judgement"]
    if judgement == "pass":
        print("\n第 1 步通过：记录响应证据后可以进第 2 步（需要登录态）。")
    elif judgement == "list_only":
        print("\n第 1 步不通过：只取得到列表，没有正文/楼层 => 该源无法验证分层，"
              "不排进 P 序列；可作为「仅列表」的降级源，且必须标 time_basis/provenance。")
    elif judgement == "signed_required":
        print("\n第 1 步不通过：需要请求签名 => §11 明确排除，到此为止。")
    else:
        print("\n第 1 步不通过：见上面的实际响应。")
    print(f"结果已写入 {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

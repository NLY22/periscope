"""Recover article bodies for a URL list in an isolated command invocation."""

from __future__ import annotations

from pathlib import Path
import argparse
import asyncio
import json
import sys

from periscope.runtime import paths
from periscope.runtime.console import force_utf8_output
from src.extractors.trafilatura import TrafilaturaExtractor
from src.models import TrafilaturaExtractorConfig


ROOT = paths.ROOT

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


async def main() -> int:
    force_utf8_output()
    ap = argparse.ArgumentParser()
    ap.add_argument("--urls", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--concurrency", type=int, default=5)
    args = ap.parse_args()

    urls = []
    seen = set()
    for line in Path(args.urls).read_text(encoding="utf-8").splitlines():
        u = line.strip()
        if u and u not in seen:
            seen.add(u)
            urls.append(u)

    ex = TrafilaturaExtractor(TrafilaturaExtractorConfig())
    sem = asyncio.Semaphore(args.concurrency)
    out: dict[str, str] = {}

    import httpx

    async with httpx.AsyncClient(
        follow_redirects=True, timeout=12,
        headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"},
    ) as cli:

        async def one(u: str) -> None:
            async with sem:
                try:
                    t = await ex.extract(u, cli)
                except Exception:
                    return
                if t:
                    out[u] = t

        await asyncio.gather(*(one(u) for u in urls))

    Path(args.out).write_text(
        json.dumps(out, ensure_ascii=False), encoding="utf-8")
    got = sum(1 for v in out.values() if len(v) >= 400)
    print("fetched %d/%d (>=400ch: %d)" % (len(out), len(urls), got))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

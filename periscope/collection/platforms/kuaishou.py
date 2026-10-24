"""快手搜索采集器（浏览器拦截响应包，无头模式）。

HTTP 直连返回 63 字节空壳，但页面自己发出的
  /rest/v/search/feed?__NS_hxfalcon=...
响应里是完整的 feeds 列表（每页 20 条）：
  feeds[i].photo.caption  文案
  feeds[i].photo.id       视频 ID（拼 URL 用）
  feeds[i].author.name    作者
  feeds[i].tags           话题标签

无头模式实测可用，不弹窗。

用法：
    python collect_kuaishou.py --queries 人形机器人 固态电池 --scrolls 6
"""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import re
import sys
import urllib.parse

from periscope.collection.platforms.browser import browser_session, ensure_env, run_collector, wait_for_items, write_checkpoint
from periscope.runtime import paths


ROOT = paths.ROOT

PROFILES = paths.PROFILES
OUT_DEFAULT = paths.COLLECT_DIR / "ugc_search.kuaishou.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

DEFAULT_QUERIES = [
    "人形机器人", "具身智能", "大模型", "AI 应用", "智能体",
    "算力", "芯片", "英伟达", "DeepSeek", "通义千问",
    "豆包", "Kimi", "文心一言", "智谱", "MiniMax",
    "AI 副业", "AI 失业", "本地部署", "提示词", "AI 翻车",
]


def clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return " ".join(s.replace("&amp;", "&").replace("&nbsp;", " ").split())


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", nargs="*", default=None)
    ap.add_argument("--limit-queries", type=int, default=0)
    ap.add_argument("--scrolls", type=int, default=6)
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    ap.add_argument("--headed", action="store_true", default=False,
                    help="显示浏览器窗口（默认不显示）")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    out = Path(getattr(args, "out", OUT_DEFAULT))

    queries = args.queries or DEFAULT_QUERIES
    if args.limit_queries:
        queries = queries[: args.limit_queries]

    prof = PROFILES / "kuaishou"
    rows: list[dict] = []
    seen: set[str] = set()

    with browser_session(prof, headed=getattr(args, "headed", False), user_agent=UA, prepare=ensure_env,
                         viewport={"width": 1440, "height": 950}) as ctx:
        write_checkpoint(out, [])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        bucket: list[dict] = []

        def on_resp(r) -> None:
            if "search/feed" not in r.url:
                return
            ct = (r.headers or {}).get("content-type", "")
            if "json" not in ct:
                return
            try:
                body = r.json()
            except Exception:
                return
            feeds = body.get("feeds")
            if isinstance(feeds, list):
                bucket.extend([f for f in feeds if isinstance(f, dict)])

        page.on("response", on_resp)

        for qi, q in enumerate(queries, 1):
            bucket.clear()
            url = ("https://www.kuaishou.com/search/video?searchKey="
                   + urllib.parse.quote(q))
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=50000)
            except Exception:
                try:
                    page.reload(wait_until="domcontentloaded", timeout=40000)
                except Exception:
                    pass
            wait_for_items(page, lambda: len(bucket), timeout_ms=8000)

            before = len(rows)
            stall = 0
            for scroll in range(max(0, args.scrolls) + 1):
                if scroll:
                    before_response = len(bucket)
                    page.mouse.wheel(0, 2900)
                    wait_for_items(page, lambda: len(bucket), previous=before_response, timeout_ms=2600)

                added = 0
                for f in list(bucket):
                    photo = f.get("photo") or {}
                    cap = clean(photo.get("caption") or "")
                    pid = str(photo.get("id") or "")
                    if not cap or len(cap) < 4:
                        continue
                    key = pid or cap
                    if key in seen:
                        continue
                    seen.add(key)
                    author = (f.get("author") or {}).get("name") or ""
                    tags = ",".join(
                        clean(t.get("name") or "")
                        for t in (f.get("tags") or []) if isinstance(t, dict)
                    )
                    rows.append({
                        "platform": "kuaishou",
                        "platform_name": "快手搜索",
                        "kind": "视频",
                        "title": cap[:120],
                        "url": ("https://www.kuaishou.com/short-video/%s" % pid) if pid else "",
                        "text": cap[:400],
                        "author": clean(author),
                        "date": "",
                        "tags": tags,
                        "query": q,
                    })
                    added += 1

                if added:
                    write_checkpoint(out, rows)
                if added == 0:
                    stall += 1
                    if stall >= 3:
                        break
                else:
                    stall = 0

            print("  [%d/%d] %-14s +%-4d 累计 %d" % (
                qi, len(queries), q, len(rows) - before, len(rows)), flush=True)

    write_checkpoint(out, rows)
    print()
    print("queries: %d   items: %d   -> %s" % (len(queries), len(rows), out.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_collector(main))

"""Inspect a topic run: what did this query actually collect?

P0 has one job that is not collection itself: making the collected data
inspectable. Without this the answer to "did we get engagement fields?" is a
guess, and the analysis stage would be designed against an imagined shape.

Usage:
    python tools/dump_shard.py                # newest run
    python tools/dump_shard.py --last-topic 人形机器人
    python tools/dump_shard.py --file .periscope-scratch/topics/x.json
    python tools/dump_shard.py --field metrics
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import paths, runstore  # noqa: E402

# Which layer names a run shard uses, and where each came from.
LAYERS = {"api": "DeepSeek Search", "local": "本地语料", "platform": "平台采集"}


def _pick(payload: dict, field: str) -> list:
    rows = payload.get("rows") or []
    if not field:
        return rows
    return [r for r in rows if field in r]


def _coverage(rows: list) -> dict:
    """How many rows carry each optional field.

    This is the number that decides whether the next stage is worth building:
    `metrics 0/233` means the endpoint does not report counts, not that the
    topic had no engagement.
    """
    if not rows:
        return {}
    fields = ("date", "author", "text", "body", "url", "metrics", "transcript",
              "comments", "ids")
    out = {}
    for name in fields:
        present = sum(1 for r in rows if r.get(name))
        if present:
            out[name] = "%d/%d" % (present, len(rows))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="查看一次话题运行的采集结果")
    ap.add_argument("--file", default="")
    ap.add_argument("--last", action="store_true", help="最近一次运行")
    ap.add_argument("--last-topic", default="", help="某话题最近一次运行")
    ap.add_argument("--field", default="",
                    help="只看含该字段的行（metrics/transcript/comments）")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = ap.parse_args()

    if args.file:
        path = Path(args.file)
    elif args.last_topic:
        path = runstore.find(args.last_topic)
    else:
        path = runstore.latest()

    if path is None or not Path(path).exists():
        print("没有找到运行分片。先跑一次 python topic.py --topic ...")
        print("分片目录: %s" % paths.TOPIC_DIR)
        return 2

    payload = runstore.read(path)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    rows = payload.get("rows") or []
    picked = _pick(payload, args.field)

    print("=" * 78)
    print("运行分片: %s" % path)
    print("=" * 78)
    print("  话题      : %s" % payload.get("topic"))
    print("  时间      : %s" % payload.get("created_at"))
    print("  耗时      : %ss" % payload.get("elapsed"))
    print("  参数      : %s" % " ".join(payload.get("argv") or []))
    stats = payload.get("stats") or {}
    print("  统计      : %s" % json.dumps(stats, ensure_ascii=False))
    print("  行数      : %d" % len(rows))

    by_layer: dict = {}
    for row in rows:
        by_layer.setdefault(row.get("_layer") or "?", []).append(row)
    print("  分层      :")
    for layer, group in sorted(by_layer.items(), key=lambda kv: -len(kv[1])):
        print("    %-9s %5d  %s" % (layer, len(group),
                                    LAYERS.get(layer, "")))

    print("  字段覆盖  :")
    for layer, group in sorted(by_layer.items(), key=lambda kv: -len(kv[1])):
        cov = _coverage(group)
        print("    %-9s %s" % (layer, json.dumps(cov, ensure_ascii=False)))

    if args.field:
        print("\n含 `%s` 的行: %d" % (args.field, len(picked)))

    print("\n" + "-" * 78)
    print("样例（前 %d 条）" % min(args.limit, len(picked)))
    print("-" * 78)
    for row in picked[: args.limit]:
        print("  [%s] %s" % (row.get("_layer"), (row.get("title") or "")[:64]))
        meta = []
        if row.get("author"):
            meta.append("作者=%s" % row["author"])
        if row.get("date"):
            meta.append("日期=%s" % str(row["date"])[:10])
        if row.get("metrics"):
            meta.append("互动=%s" % json.dumps(row["metrics"], ensure_ascii=False))
        if row.get("ids"):
            meta.append("id=%s" % json.dumps(row["ids"], ensure_ascii=False)[:50])
        if meta:
            print("       " + "  ".join(meta))
        body = row.get("body") or row.get("text") or ""
        if body:
            print("       %s..." % " ".join(str(body).split())[:120])
        print()

    notes = payload.get("notes") or []
    if notes:
        print("备注:")
        for note in notes:
            print("  - %s" % note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

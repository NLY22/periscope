"""真相：DeepSeek 从平台拿到的内容，和我们拿到的，差在哪里。

对比四个维度：
  1. 时效   —— 多久以前的内容
  2. 数量   —— 同一个话题能挖多深
  3. 粒度   —— 拿到的是帖子本身还是汇总页
  4. 互动   —— 有没有点赞/评论/播放数据
"""

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths


import io
import re
from collections import Counter
from urllib.parse import urlparse

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

K = paths.deepseek_key()
H = {"Authorization": "Bearer " + K, "Content-Type": "application/json"}
S = "https://api.deepseek.com/v1/tools/search"

TOPIC = "人形机器人"

print("=" * 86)
print("1. 最大数量：count=50 时，各平台能拿多少条")
print("=" * 86)
for name, dom in [("微博", "weibo.com"), ("抖音", "douyin.com"),
                  ("B站", "bilibili.com"), ("知乎", "zhihu.com"),
                  ("快手", "kuaishou.com"), ("虎扑", "hupu.com")]:
    try:
        r = httpx.post(S, headers=H, json={
            "query": "%s site:%s" % (TOPIC, dom), "count": 50,
            "need_content": True}, timeout=120)
        res = r.json().get("results") or [] if r.status_code == 200 else []
    except Exception:
        res = []
    dates = sorted((x.get("date") or "")[:10] for x in res if x.get("date"))
    span = ("%s ~ %s" % (dates[0], dates[-1])) if dates else "无日期"
    print("  %-8s %2d 条   日期跨度: %s" % (name, len(res), span))

print()
print("=" * 86)
print("2. 条目粒度：url 的形状能看出是「单条内容」还是「列表/汇总页」")
print("=" * 86)
for name, dom in [("微博", "weibo.com"), ("抖音", "douyin.com"),
                  ("B站", "bilibili.com")]:
    try:
        r = httpx.post(S, headers=H, json={
            "query": "%s site:%s" % (TOPIC, dom), "count": 10,
            "need_content": False}, timeout=90)
        res = r.json().get("results") or [] if r.status_code == 200 else []
    except Exception:
        res = []
    print("  --- %s ---" % name)
    for x in res[:6]:
        u = x.get("url") or ""
        path = urlparse(u).path
        # 判断形状
        kind = "?"
        if re.search(r"/(detail|status|video|explore|note)/[A-Za-z0-9]{6,}", path):
            kind = "单条"
        elif re.search(r"/(search|tag|topic|hot|brilliant|explore|recommend)", path):
            kind = "列表页"
        elif path.count("/") <= 1:
            kind = "首页"
        else:
            kind = "其他"
        print("    %-6s %s" % (kind, u[:76]))

print()
print("=" * 86)
print("3. 互动数据：content 里有没有点赞/播放/评论")
print("=" * 86)
for name, dom in [("抖音", "douyin.com"), ("B站", "bilibili.com"), ("微博", "weibo.com")]:
    try:
        r = httpx.post(S, headers=H, json={
            "query": "%s site:%s" % (TOPIC, dom), "count": 5,
            "need_content": True}, timeout=90)
        res = r.json().get("results") or [] if r.status_code == 200 else []
    except Exception:
        res = []
    if not res:
        continue
    c0 = res[0].get("content") or ""
    has_like = bool(re.search(r"\d+\s*(赞|点赞|喜欢)", c0))
    has_play = bool(re.search(r"(播放|观看)\s*[:：]?\s*\d+", c0))
    has_cmt = bool(re.search(r"\d+\s*(评论|条评论)", c0))
    print("  %-6s 赞=%s 播放=%s 评论=%s" % (name, has_like, has_play, has_cmt))
    print("         正文片段: %s" % " ".join(c0.split())[:120])

"""独特层候选评估：逐个平台实测 API 的实际内容质量。

“能不能搜到”不够，要看“拿到的是不是真内容”。
判定用三个可测指标：
  A. 覆盖条数
  B. 壳层比例（标题是“相关视频”“_bilibili”这类搜索结果页壳）
  C. 正文可得性（content 字段是否真有内容）
"""

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths


import io
import time

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

K = paths.deepseek_key()
H = {"Authorization": "Bearer " + K, "Content-Type": "application/json"}
S = "https://api.deepseek.com/v1/tools/search"

TOPIC = "人形机器人"

# 尽可能全的平台清单
PLATFORMS = [
    ("小红书", "xiaohongshu.com"),
    ("微博", "weibo.com"),
    ("抖音", "douyin.com"),
    ("快手", "kuaishou.com"),
    ("B站", "bilibili.com"),
    ("知乎", "zhihu.com"),
    ("CSDN", "blog.csdn.net"),
    ("掘金", "juejin.cn"),
    ("V2EX", "v2ex.com"),
    ("虎扑", "hupu.com"),
    ("头条", "toutiao.com"),
    ("公众号", "mp.weixin.qq.com"),
    ("YouTube", "youtube.com"),
    ("豆瓣", "douban.com"),
    ("雪球", "xueqiu.com"),
    ("贴吧", "tieba.baidu.com"),
    ("什么值得买", "smzdm.com"),
    ("少数派", "sspai.com"),
    ("36氪", "36kr.com"),
    ("钛媒体", "tmtpost.com"),
    ("虎嗅", "huxiu.com"),
    ("机器之心", "jiqizhixin.com"),
    ("量子位", "qbitai.com"),
    ("新浪财经", "finance.sina.com.cn"),
    ("东方财富", "eastmoney.com"),
    ("B站专栏", "read.bilibili.com"),
    ("微博头条", "weibo.cn"),
    ("小红书笔记", "xhslink.com"),
    ("即刻", "okjike.com"),
    ("NGA", "bbs.nga.cn"),
    ("水木", "newsmth.net"),
    ("天涯", "tianya.cn"),
    ("豆瓣小组", "douban.com/group"),
    ("ProductHunt", "producthunt.com"),
    ("Reddit", "reddit.com"),
    ("HackerNews", "news.ycombinator.com"),
    ("GitHub", "github.com"),
    ("知乎专栏", "zhuanlan.zhihu.com"),
]

SHELL_MARKS = ("相关视频", "_bilibili", "搜索", "分类分类", "- 站内")


def is_shell(title: str) -> bool:
    t = title or ""
    if len(t) < 12:
        return True
    return any(m in t for m in SHELL_MARKS)


def probe(name, domain, topic):
    q = "%s site:%s" % (topic, domain)
    try:
        r = httpx.post(S, headers=H, json={
            "query": q, "count": 10, "need_content": True}, timeout=90)
        if r.status_code != 200:
            return name, domain, 0, 0, 0, "HTTP %d" % r.status_code
        res = r.json().get("results") or []
    except Exception as exc:
        return name, domain, 0, 0, 0, type(exc).__name__

    n = len(res)
    shells = sum(1 for x in res if is_shell(x.get("title") or ""))
    withbody = sum(1 for x in res if len(x.get("content") or "") >= 400)
    return name, domain, n, shells, withbody, ""


print("%-14s %-24s %-6s %-8s %-8s %s" % (
    "平台", "域名", "条数", "壳层", "有正文", "判定"))
print("-" * 92)

rows = []
for name, domain in PLATFORMS:
    name_, dom, n, shells, wb, err = probe(name, domain, TOPIC)
    if err:
        verdict = err
    elif n == 0:
        verdict = "★无法替代"
    elif shells >= n * 0.6:
        verdict = "★只有壳层"
    elif wb >= n * 0.8:
        verdict = "可替代"
    else:
        verdict = "部分可替代"
    rows.append((name_, n, shells, wb, verdict))
    print("%-14s %-24s %-6d %-8d %-8d %s" % (name_, dom, n, shells, wb, verdict))
    time.sleep(0.2)

print()
print("=" * 92)
uniq = [r[0] for r in rows if r[4].startswith("★")]
weak = [r[0] for r in rows if r[4] == "部分可替代"]
rep = [r[0] for r in rows if r[4] == "可替代"]
print("★ 独特层（API 做不到）: %d 个" % len(uniq))
print("   " + "、".join(uniq))
print()
print("部分可替代: %d 个 -> %s" % (len(weak), "、".join(weak)))
print()
print("可替代（建议删）: %d 个 -> %s" % (len(rep), "、".join(rep)))

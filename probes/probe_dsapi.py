
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


def call(query, **kw):
    body = {"query": query}
    body.update(kw)
    t0 = time.monotonic()
    try:
        r = httpx.post(S, headers=H, json=body, timeout=120)
    except Exception as e:
        return None, 0, str(e)
    el = time.monotonic() - t0
    if r.status_code != 200:
        return None, el, "HTTP %d %s" % (r.status_code, r.text[:100])
    return (r.json().get("results") or []), el, ""


def show(tag, query, **kw):
    res, el, err = call(query, **kw)
    print("=" * 84)
    print("[%s] query=%r  %s" % (tag, query, " ".join("%s=%s" % (k, v) for k, v in kw.items())))
    if err:
        print("  失败:", err)
        return
    print("  %d 条  %.2fs" % (len(res), el))
    from collections import Counter

    sites = Counter((x.get("site_name") or "?") for x in res)
    auth = Counter(x.get("authority") for x in res)
    print("  站点:", ", ".join("%s×%d" % (s, n) for s, n in sites.most_common(6)))
    print("  authority 分布:", dict(sorted(auth.items(), key=lambda x: (x[0] is None, x[0]))))
    for x in res[:4]:
        print("    · %-46s %-10s a=%s r=%.2f %s" % (
            (x.get("title") or "")[:46],
            (x.get("site_name") or "?")[:10],
            x.get("authority"), x.get("rerank_score") or 0,
            (x.get("date") or "")[:10]))
    print()


# ---------- 1. 长尾话题 ----------
show("长尾-小众", "硼中子俘获治疗 临床")
show("长尾-极冷", "深海采矿 锰结核 商业开采 2026")

# ---------- 2. 中文平台内容 ----------
show("平台-小红书", "小红书 人形机器人 实测", count=10)
show("平台-抖音", "抖音 人形机器人 测评", count=10)
show("site-限定小红书", "人形机器人 site:xiaohongshu.com", count=10)
show("site-限定抖音", "人形机器人 site:douyin.com", count=10)
show("site-限定B站", "人形机器人 site:bilibili.com", count=10)

# ---------- 3. 争议/传言类 ----------
show("传言类", "某某公司裁员 传闻 内部消息", count=10)
show("负面评价", "人形机器人 泡沫 骗局 智商税", count=10)

# ---------- 4. 时效 ----------
show("近期", "人形机器人 今天 新闻", count=10)
show("历史", "人形机器人 2020年 发展 报告", count=10, need_content=False)

# ---------- 5. 极短查询 ----------
show("两字词", "芯片", count=10)
show("英文", "humanoid robot battery", count=10)

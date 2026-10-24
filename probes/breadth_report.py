"""广度快照：平台覆盖矩阵。"""

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from periscope.runtime import paths




from periscope.collection.transport import client as collector

OUT = str(paths.REPORT_DIR / '.breadth.txt')
L: list[str] = []

cat = collector.CATALOG

# 分类：可搜索 / 只抓列表 / 需浏览器
searchable = [c for c in cat if c.queries]
listonly = [c for c in cat if not c.queries and not c.needs_browser]
browser = [c for c in cat if c.needs_browser]

L.append("源总数: %d" % len(cat))
L.append("  可搜索（关键词驱动）: %d" % len(searchable))
L.append("  只抓列表/快照     : %d" % len(listonly))
L.append("  需浏览器渲染      : %d" % len(browser))
L.append("")

# 平台矩阵：把已知平台按状态归位
PLATFORMS = {
    # 名称: (状态, 方式, 单话题实测)
    "微博": ("可用", "HTTP 直连（游客cookie）", "464 条/10页"),
    "B站": ("可用", "HTTP wbi + 时间窗", "196 条/词，4年"),
    "知乎": ("可用", "HTTP + z_c0", "153 条/12页"),
    "知乎话题": ("可用", "HTTP + z_c0", "26 条"),
    "CSDN": ("可用", "HTTP", "450 条/15页"),
    "CSDN问答": ("可用", "HTTP", "150 条"),
    "掘金": ("可用", "HTTP", "100 条/5页"),
    "掘金专栏": ("可用", "HTTP", "100 条/5页"),
    "V2EX": ("可用", "HTTP sov2ex", "100 条/5页"),
    "虎扑": ("可用", "HTTP HTML", "20 条"),
    "头条": ("可用", "HTTP HTML", "36 条"),
    "公众号": ("可用", "HTTP 搜狗", "10 条"),
    "YouTube": ("可用", "HTTP HTML", "69 条"),
    "GoogleNews": ("可用", "RSS + 时间窗", "100 条/月"),
    "贴吧": ("可用", "浏览器", "27-30 条/词"),
    "豆瓣": ("部分", "HTTP 仅榜单", "49 条"),
    "抖音": ("待修", "浏览器需有头", "10 条/页已验证"),
    "小红书": ("阻塞", "账号风控 300011", "0"),
    "快手": ("待接", "浏览器拦响应", "feeds 已验证"),
    "什么值得买": ("阻塞", "需登录", "200 条热榜"),
    "雪球": ("阻塞", "需 JS", "0"),
    "东方财富": ("阻塞", "需 JS", "0"),
    "36氪": ("阻塞", "需 JS", "0"),
    "脉脉": ("未探", "-", "-"),
    "B站专栏": ("可用", "HTTP wbi", "20 条"),
    "B站用户": ("可用", "HTTP wbi", "20 条"),
}

L.append("=" * 86)
L.append("平台覆盖矩阵")
L.append("=" * 86)
L.append("%-14s %-8s %-26s %s" % ("平台", "状态", "方式", "单话题实测"))
L.append("-" * 86)
for name, (st, how, got) in PLATFORMS.items():
    L.append("%-14s %-8s %-26s %s" % (name, st, how, got))

avail = sum(1 for s, _, _ in PLATFORMS.values() if s == "可用")
L.append("-" * 86)
L.append("完全可用: %d / %d" % (avail, len(PLATFORMS)))
L.append("")

L.append("=" * 86)
L.append("可搜索源明细（collector 里真实注册的）")
L.append("=" * 86)
for c in sorted(searchable, key=lambda x: -len(x.queries)):
    L.append("  %-20s q=%-3d pages=%-3d %s" % (
        c.key, len(c.queries), c.max_pages, c.name[:22]))

open(OUT, "w", encoding="utf-8").write("\n".join(L))

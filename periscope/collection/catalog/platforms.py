"""Platforms source definitions."""

from ..models import Source
from .queries import AI_TERMS, CN_TERMS, ARXIV_QUERIES, RECENT_MONTHS


SOURCES = [
    Source("douyin_hot", "抖音热点榜", "https://www.douyin.com/hot", "ugc",
           fmt="html", max_pages=1, needs_browser=True),
    Source("kuaishou_hot", "快手热榜", "https://www.kuaishou.com/brilliant", "ugc",
           fmt="html", max_pages=1, needs_browser=True),
    Source("weibo_hot", "微博热搜", "https://s.weibo.com/top/summary", "ugc",
           fmt="html", max_pages=1, needs_browser=True),
    Source("douban_explore", "豆瓣讨论精选", "https://www.douban.com/group/explore", "ugc",
           fmt="html", max_pages=1, needs_browser=True),
    Source("hupu", "虎扑步行街", "https://bbs.hupu.com/bxj", "ugc",
           fmt="html", max_pages=1, needs_browser=True),
    Source("bili_popular_browser", "B站排行榜（浏览器）", "https://www.bilibili.com/v/popular/rank/all", "ugc",
           fmt="html", max_pages=1, needs_browser=True),
    Source("smzdm", "什么值得买", "https://www.smzdm.com/", "ugc",
           fmt="html", max_pages=1, needs_browser=True),
    Source("producthunt", "Product Hunt", "https://www.producthunt.com/", "ugc",
           fmt="html", max_pages=1, needs_browser=True),
    Source("threads", "Threads", "https://www.threads.net/", "ugc",
           fmt="html", max_pages=1, needs_browser=True),
    Source("xhs_explore", "小红书", "https://www.xiaohongshu.com/explore", "ugc",
           fmt="html", max_pages=1, needs_browser=True, profile="xhs"),
    Source("zhihu_hot", "知乎热榜", "https://www.zhihu.com/hot", "ugc",
           fmt="html", max_pages=1, needs_browser=True, profile="zhihu"),
    Source("tieba_python", "贴吧 python 吧", "https://tieba.baidu.com/f?kw=python", "ugc",
           fmt="html", max_pages=1, needs_browser=True, profile="tieba"),
]

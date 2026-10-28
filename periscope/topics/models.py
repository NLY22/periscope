"""Requests and results for topic search; no I/O."""

from __future__ import annotations

from dataclasses import dataclass, field


SPEED_FAST = "fast"
SPEED_NORMAL = "normal"
SPEED_DEEP = "deep"
API_DEPTH = {SPEED_FAST: 3, SPEED_NORMAL: 5, SPEED_DEEP: 7, "off": 3}
FAST_SOURCES = [
    "weibo_search", "zhihu_search", "zhihu_topic", "csdn_search", "csdn_ask",
    "juejin_search", "juejin_column", "v2ex_search", "hupu_search",
    "toutiao_search", "wechat_search", "youtube_cn",
]
# Retained for users of the old script API; execution uses BROWSER_MODULES.
BROWSER_SCRIPTS = {
    "xhs": ("collectors/collect_xhs.py", "ugc_search.xhs.json"),
    "douyin": ("collectors/collect_douyin.py", "ugc_search.douyin.json"),
    "kuaishou": ("collectors/collect_kuaishou.py", "ugc_search.kuaishou.json"),
}
BROWSER_MODULES = {key: "periscope.collection.platforms." + key for key in BROWSER_SCRIPTS}


@dataclass(frozen=True)
class TopicRequest:
    topic: str
    limit: int = 8
    cached_only: bool = False
    no_api: bool = False
    speed: str = SPEED_FAST
    api_count: int = 50
    api_depth: int = 0
    budget: float = 30.0
    enrich: bool = False
    save: bool = False
    coverage: str = "focused"
    sources: tuple[str, ...] = ()
    source_kinds: tuple[str, ...] = ()
    sources_file: str = ""
    no_local: bool = False
    crawl_seeds: tuple[str, ...] = ()
    crawl: bool = False
    crawl_pages: int = 12
    crawl_depth: int = 2
    crawl_budget: float = 30.0
    crawl_fields: tuple[tuple[str, str], ...] = ()


@dataclass
class TopicResult:
    topic: str
    local_rows: list[dict] = field(default_factory=list)
    api_rows: list[dict] = field(default_factory=list)
    platform_rows: list[dict] = field(default_factory=list)
    crawl_rows: list[dict] = field(default_factory=list)
    merged: list[dict] = field(default_factory=list)
    local_total: int = 0
    elapsed: float = 0.0
    breakdown: str = ""
    notes: list[str] = field(default_factory=list)
    source_reports: list[dict] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)
    crawl_reports: list[dict] = field(default_factory=list)

    @property
    def raw_rows(self) -> list[dict]:
        return self.api_rows + self.local_rows + self.platform_rows + self.crawl_rows

    @property
    def info(self) -> dict:
        return {"elapsed": self.elapsed, "breakdown": self.breakdown, "notes": self.notes,
                "source_reports": self.source_reports, "coverage": self.coverage, "crawl_reports": self.crawl_reports}

    @property
    def stats(self) -> dict:
        return {"local": self.local_total, "local_rows": len(self.local_rows),
                "api": len(self.api_rows), "platform": len(self.platform_rows),
                "merged": len(self.merged), "coverage": self.coverage,
                "crawl": len(self.crawl_rows), "crawl_reports": self.crawl_reports,
                "source_reports": self.source_reports}

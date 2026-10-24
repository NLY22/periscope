"""Shared runtime paths and environment overrides for checkout and installed use."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping
import os
import sys


def default_data_dir(*, workspace: Path | None = None,
                     environ: Mapping[str, str] | None = None,
                     platform: str | None = None, home: Path | None = None) -> Path:
    """Writable default for a source checkout or an installed distribution."""
    env = os.environ if environ is None else environ
    if workspace is not None:
        return workspace / ".periscope-scratch"
    user_home = Path.home() if home is None else home
    if (sys.platform if platform is None else platform) == "win32":
        local = env.get("LOCALAPPDATA")
        return Path(local) / "Periscope" if local else user_home / ".periscope"
    return Path(env.get("XDG_DATA_HOME", str(user_home / ".local" / "share"))) / "periscope"


_candidate = Path(__file__).resolve().parents[2]
WORKSPACE = _candidate if (_candidate / "pyproject.toml").is_file() and (_candidate / "topic.py").is_file() else None
ROOT = (WORKSPACE or Path(os.environ.get("PERISCOPE_DATA_DIR", default_data_dir())).expanduser()).resolve()


# ---------------------------------------------------------------- 数据目录
# `data/` 在沙箱下不可写（ACL 限制），所以运行期数据放在 `.periscope-scratch/`。
# 这个限制是宿主环境造成的，不是设计选择；一旦 ACL 放开，把 SCRATCH 指过去即可。
SCRATCH = Path(os.environ.get("PERISCOPE_SCRATCH_DIR", default_data_dir(workspace=WORKSPACE) if WORKSPACE else ROOT)).expanduser().resolve()
SCRATCH.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 数据目录
# B 只有一个数据目录开关。设置 PERISCOPE_DATA_DIR 就能把语料库搬到别处，
# 不需要改任何脚本；默认仍放在 SCRATCH，因为本项目的开发沙箱里 `data/`
# 不可写（见 src/_scratch.py 与 docs/foundation.md）。
DATA_DIR = Path(os.environ.get("PERISCOPE_DATA_DIR", str(SCRATCH))).expanduser().resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 语料库。放在 DATA_DIR 是因为 SQLite 需要写 .wal / .shm 同目录文件。
CORPUS = DATA_DIR / "corpus.db"

# ---------------------------------------------------------------- 话题运行分片
# 每次 `topic.py` 跑完都把这一轮的原始行写成一份分片。它和 `SHARD_DIR` 不是
# 一回事：`SHARD_DIR` 是 collector 的采集产物（按源分文件），这里是「一次查询
# 的完整快照」——本地 + API + 平台三层合并前的原始行，外加查询参数与统计。
#
# 为什么必须先落盘再谈入库：入库要写 SQLite 并建索引，几秒到几十秒；而这一轮
# 的答案在内存里已经排好序。落盘是毫秒级的，且进程被杀也能重放。
TOPIC_DIR = DATA_DIR / "topics"
TOPIC_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 分片目录
# 采集结果按「谁采的」分开，但统一放在 shards/ 下。
SHARDS = ROOT / "data" / "shards"
CACHE = ROOT / "data" / "cache"

# 浏览器会话（cookie / localStorage 导出）。这是**凭据**，不是数据：
#
# - 默认位置是仓库内的 data/cookies/，已由 .gitignore 整目录排除。
# - `PERISCOPE_COOKIE_DIR` 可以把它指到仓库外。这不是可选的便利：
#   在只读挂载或共享 checkout 上，把一份真实账号的会话写进仓库目录
#   是错的，而唯一的替代是「不要用需要登录的源」。
COOKIES = Path(
    os.environ.get("PERISCOPE_COOKIE_DIR", str(ROOT / "data" / "cookies"))
).expanduser().resolve()


def subprocess_env() -> dict[str, str]:
    """Keep runtime path overrides anchored when a child changes directory."""
    return {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PERISCOPE_DATA_DIR": str(DATA_DIR.resolve()),
        "PERISCOPE_SCRATCH_DIR": str(SCRATCH.resolve()),
        "PERISCOPE_COLLECT_DIR": str(COLLECT_DIR.resolve()),
        "PERISCOPE_REPORT_DIR": str(REPORT_DIR.resolve()),
        "PERISCOPE_COOKIE_DIR": str(COOKIES.resolve()),
    }

for _d in (SHARDS, CACHE, COOKIES):
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except Exception:
        # data/ 可能不可写；此时回退到 SCRATCH，不阻断运行
        pass


def shard_dir() -> Path:
    """分片实际存放目录：优先 data/shards，不可写则用 scratch。"""
    probe = SHARDS / ".writable"
    try:
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        return SHARDS
    except Exception:
        alt = SCRATCH / "shards"
        alt.mkdir(parents=True, exist_ok=True)
        return alt


def cache_dir() -> Path:
    probe = CACHE / ".writable"
    try:
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        return CACHE
    except Exception:
        alt = SCRATCH / "cache"
        alt.mkdir(parents=True, exist_ok=True)
        return alt


SHARD_DIR = shard_dir()
CACHE_DIR = cache_dir()

# 正文缓存（抓到的全文，一行一个 JSON）
#
# 这个文件曾经有两个位置，读的和写的各用一个，而且谁也不报错：
#   collectors/backfill_fulltext.py 写 ROOT / "fulltext_cache.jsonl"
#   tools/ingest_all.py             读 CACHE_DIR / "fulltext_cache.jsonl"
# 结果是抓到的正文从来没有被合并进语料 —— 仓库根目录那份 3,648 条、
# 24 MB 的缓存，`--fulltext-only` 一次也没读到过。两个路径都存在、
# 都可写，所以失败是静默的：覆盖率表只是永远不动。
#
# 现在只有 `fulltext_cache()` 知道它在哪，读写都用它。
FULLTEXT_CACHE = CACHE_DIR / "fulltext_cache.jsonl"

# 上面那个 bug 留下的历史位置。不改名、不删除：那份数据是真实抓取结果
# （按作者记录约 14 小时机器时间），迁移它比丢弃它便宜得多。
LEGACY_FULLTEXT_CACHE = ROOT / "fulltext_cache.jsonl"


def fulltext_cache() -> Path:
    """正文缓存的位置，读写共用这一个入口。

    首次调用时把历史位置的文件搬到规范位置。为什么是「搬」而不是
    「也读历史位置」：两个可写路径就是下一个 bug 的温床，而这里已经
    因为两个路径丢过一次数据。搬一次之后就只有一个位置了。

    迁移失败（跨盘 rename 不支持、权限不足）不让调用方崩：返回一个
    两者中已有内容的路径，让这一轮能读到数据，下一次再试。
    """
    legacy = LEGACY_FULLTEXT_CACHE
    if not FULLTEXT_CACHE.exists() and legacy.is_file():
        try:
            LEGACY_FULLTEXT_CACHE.parent.mkdir(parents=True, exist_ok=True)
            FULLTEXT_CACHE.parent.mkdir(parents=True, exist_ok=True)
            legacy.replace(FULLTEXT_CACHE)
        except OSError:
            # 跨卷 replace 在 Windows 上会失败；退回复制后删除。
            try:
                import shutil

                shutil.copy2(legacy, FULLTEXT_CACHE)
                legacy.unlink(missing_ok=True)
            except OSError:
                return legacy
    return FULLTEXT_CACHE

# ---------------------------------------------------------------- 采集落盘目录
# `periscope.collection.http`（collector.py）的历史输出落在仓库根目录：
# http_items*.json / ugc_*.json / deep_search_*.json，二十多个文件约 100 MB，
# 把入口目录变成了一个数据堆。现在统一写 `collect/`：
#
# - 默认 `SCRATCH/collect/`（沙箱里 data/ 不可写，scratch 是唯一保证可写的树）；
# - `PERISCOPE_COLLECT_DIR` 可整体搬到别处；
# - 读端 `periscope.operations.ingest` 优先读取 COLLECT_DIR 与 SHARD_DIR，
#   同时兼容尚未整理的检出目录里的历史根目录文件。
#
# 历史文件已移入默认位置（2026-10-19 重构）；根目录不再出现新采集文件，
# 任何在根目录重新冒出来的 http_items*.json 都是回归，不是兼容行为。
COLLECT_DIR = Path(
    os.environ.get("PERISCOPE_COLLECT_DIR", str(SCRATCH / "collect"))
).expanduser().resolve()
COLLECT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR = Path(os.environ.get("PERISCOPE_REPORT_DIR", str(SCRATCH / "reports"))).expanduser().resolve()
REPORT_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 浏览器
PROFILES = ROOT / ".pwprofiles"
BROWSERS = ROOT / ".playwright-browsers"
PW_TMP = ROOT / ".pwtmp"

# ---------------------------------------------------------------- 凭据
DEEPSEEK_KEY_FILE = ROOT / "data" / ".deepseek_api_key"


def deepseek_key() -> str:
    """读 DeepSeek API key：环境变量优先，其次本地文件。"""
    k = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if k:
        return k
    try:
        return DEEPSEEK_KEY_FILE.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------- 职责边界
# 哪些平台由我们的采集器负责，哪些交给 DeepSeek Search。
#
# 依据是实测（见 REFACTOR_PLAN.md）：
#   - 小红书：API `site:xiaohongshu.com` 返回 0 条 -> 必须自己做
#   - 微博/抖音/快手：API 有数据，但是陈旧快照（抖音停在 2 月、
#     快手停在 7 月 9 日），且没有互动数据 -> 自己做实时
#   - B站/YouTube/V2EX：API 返回 0 条或只有播放器壳 -> 自己做
#   - 其余静态文章/新闻/论坛：API 覆盖更好更快 -> 交给 API
UNIQUE_PLATFORMS = (
    "xhs",        # 小红书：API 完全搜不到
    "weibo",      # 微博：只要最新的（API 有 1-10 天延迟）
    "douyin",     # 抖音：API 是 2 月快照，且无点赞数
    "kuaishou",   # 快手：API 全部集中在 7 月 9 日
    "bilibili",   # B站：API 只有播放器壳，且我们能回溯 4 年
    "youtube",    # YouTube：API 0 条
    "v2ex",       # V2EX：API 0 条
)

# 交给 DeepSeek Search 的（我们不自己采）
DELEGATED_TO_API = (
    "csdn", "juejin", "zhihu", "hupu", "toutiao", "baidu_zhidao",
    "wechat", "douban", "xueqiu", "tieba", "smzdm", "sspai",
    "36kr", "tmtpost", "huxiu", "jiqizhixin", "qbitai",
    "sina_finance", "eastmoney", "producthunt", "hackernews", "github",
)

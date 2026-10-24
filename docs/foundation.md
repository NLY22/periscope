# B 的地基：当前框架与边界（2026-10-19）

> **代码整理更新：** 新版正式实现现位于 `periscope/`，`topic.py`、`core/`、
> `collectors/` 及已迁移运维脚本保留为兼容入口。下文目录和路径故障记录描述
> 整理前后的历史背景；当前模块职责、安装运行方式以
> [代码组织说明](code-organization.md) 为准。现有数据库位置不变。

> 这份文档只描述**现在真实在跑的东西**。A 的历史、实现细节和评测仍以
> [README.md](../README.md)、[docs/architecture.md](architecture.md) 和
> [docs/REFACTOR_2026-10-18.md](REFACTOR_2026-10-18.md) 为准。

## 0. 两句话

- **B 是活跃产品**：`topic.py` + `core/` + `collectors/`，按需做“本地语料 +
  搜索 API + 平台采集”的三层话题检索。
- **A 是 legacy**：`src/` 的日报、Profile、投递、Web/MCP、声明核查仍存在，
  但不再作为 B 的产品面维护。B 只借用 A 的证据内核（模型、语料库、trust、
  分层、正文抽取）。

## 1. 目录与角色

| 路径 | 角色 | 状态 |
|---|---|---|
| `topic.py` | B 的唯一产品入口 | 活跃 |
| `core/` | B 内核：`paths` / `dsapi` / `merge` / `search` | 活跃 |
| `collectors/` | B 采集：179 源 catalog、13 源 fanout、浏览器三平台、正文回填 | 活跃 |
| `tools/` | B 运维：入库、正文合并、持久化；另有 A 的声明诊断脚本 | 混合 |
| `probes/` | 一次性探测与评测 | 归档 |
| `src/` | A 的日报 + 证据库；B 只依赖其中的内核模块 | legacy |
| `tests/` | A 的测试 + B 的边界/冒烟测试（实测 1,165 条） | A 为主 |
| `docs/` | A 的文档 + 本文件 + 重构记录 | 混合 |
| `profiles/` | A 的 Profile | 仅 A |

## 2. 当前数据流

请求路径（只读）：

```
topic.py
  ├─ 本地语料   core/search.py   →  <DATA_DIR>/corpus.db
  ├─ 搜索 API   core/dsapi.py    →  POST /v1/tools/search
  └─ 平台采集   collectors/fanout.py → 13 个 HTTP 源
                ↓
          core/merge.py（标题指纹合并 + 打分）
                ↓
          终端输出 / JSON（不回写）
```

写入路径（侧车，和请求路径没有自动连接）：

```
collectors/collector.py ──→ http_items*.json ─┐
ugc_*.json ───────────────────────────────────┼→ tools/ingest_all.py
collectors/backfill_fulltext.py → fulltext_cache.jsonl ─┘
                ↓
          <DATA_DIR>/corpus.db
```

`tools/topic_persist.py` 是把上面两步串起来的后台脚本；它现在只修好了路径，
`topic.py` 还不会自动调用它。也就是说：**一次话题检索采到的平台内容，默认不落库。**

## 3. 数据目录

只有一个开关：

```bash
export PERISCOPE_DATA_DIR=/path/to/data     # 可选；默认 .periscope-scratch/
```

`core/paths.py` 的解析顺序：

1. `PERISCOPE_DATA_DIR`（显式指定）
2. `.periscope-scratch/`（仓库内，当前沙箱唯一可写的位置）

目录布局：

```
<DATA_DIR>/
├── corpus.db        # 证据库
└── topics/          # 每次查询的运行分片
    └── 20261019-132547_人形机器人.json
```

语料库固定为 `<DATA_DIR>/corpus.db`。`tools/ingest_all.py`、
`collectors/backfill_fulltext.py`、`tools/run_claims.py`、`tools/show_verdicts.py`
都走同一个 `paths.CORPUS`，不再各自硬编码。

> A 的产品入口仍使用 `data/corpus.db`（默认 `corpus.path = "corpus.db"`，
> 相对 `--data-dir`）。这是**两个库**：B 的库是 1.56 GB / 18.9 万条，
> A 的库是 0.2 MB 的旧数据。合库是下一步的结构性动作，不在本文件假装已完成。

## 3.5 采集与入库（P0）

**采集与写库是两件事，刻意分开。**

```bash
python topic.py --topic "人形机器人"            # 采集 + 落分片，不写库
python topic.py --ingest-last                   # 把最近分片入库
python topic.py --topic "人形机器人" --save      # 采集 + 落分片 + 入库
python tools/dump_shard.py                      # 看这次采到了什么
```

每次查询都会写一份**运行分片**到 `<DATA_DIR>/topics/`：

| 内容 | 说明 |
|---|---|
| `topic` / `created_at` / `argv` | 这次查询是什么 |
| `stats` | 三层各多少条、合并后多少 |
| `rows[]` | 三层原始行，每行带 `_layer` |
| `metrics` / `ids` | 平台互动与后续富化所需的 id（有才存） |

**为什么先落盘**：入库要建索引、写几十秒；落盘是毫秒级，且进程被杀也能重放。
**为什么入库是手动的**：写库是可撤销的副作用，不该在用户只是想知道答案时发生。

`rows[]` 存的是原始行，不是合并结果——合并策略会变，只留输出会丢掉下一版策略
需要的证据。

### 已采集的互动字段

| 平台 | 字段 | 备注 |
|---|---|---|
| B站 | `play / like / favorites / danmaku / reply` | 搜索响应自带，无需二次请求 |
| 微博 | `reposts / comments / likes` | 来自 `*_count`；**搜索接口第 1 页常全为 0**，翻页后才见真实值 |

字段名保持各平台原样，不做归一化——“播放”和“阅读”不是同一个量，映射属于分析层。

### 两张只写表

```sql
engagement_snapshots(item_id, platform, collected_at, metrics_json, source_run)
transcripts(item_id, lang, method, segments_json, text, confidence, fetched_at)
```

- **互动是时间序列**：同一条今天 100 赞、下周 500 赞，两次都要留下；单列会覆盖掉
  让这条排上去的那个观察。
- **字幕是媒体属性**：更好的抽取结果应该覆盖旧的，所以 `ON CONFLICT DO UPDATE`。
- **`method` 必须留**：`cc` / `asr` / `ocr` / `vlm` 的可信度不同，`vlm` 是“对画面的猜测”，
  不能和创作者的字幕混为一谈。
- **P0 阶段只写不读**：没有任何检索路径依赖这两张表。

### 富化（`--enrich`）

```bash
python topic.py --topic "人形机器人 视频" --speed normal --limit 3 --enrich
```

对结果里前 K 条**可富化的**（有 B站 bvid 的）补：互动计数、热评、字幕。

| 项 | 实测 |
|---|---|
| 5 条并发富化耗时 | 约 2.1 秒 |
| 互动 | ✅ 稳定拿到（play/like/coin/favorite/danmaku/reply/share） |
| 评论 | ✅ 稳定拿到 |
| 字幕 | ❌ **匿名访问拿不到**（见下） |

**B站字幕的实测结论（2026-10-19）**：匿名请求 `player/v2` 时
`need_login_subtitle: false` 且 `subtitles` 恒为空——探测了 80 个搜索命中，
外加一个标题明确写着「中英双语字幕」的视频，全部为空。`/x/player/v2` 与
`/x/player/wbi/v2` 行为一致。

所以「空轨道列表」**不等于**「视频没有字幕」。适配器据此返回两种不同原因：

- `subtitle:need-login` —— 平台明确要求登录
- `subtitle:empty-track-list` —— 端点返回空

要真正拿到字幕，需要提供 `SESSDATA` cookie；这是用户决策，适配器不会偷偷使用。

**富化只对 top-K**：Top-3 在新闻话题里通常是文章（有正文，排序奖励它），而文章
没有 B站视频可富化。所以选择逻辑是「前 K 条里**有视频 id 的**」，不是「前 K 条」。

## 4. 当前入口

| 命令 | 作用 |
|---|---|
| `python topic.py --topic "话题"` | B 的三层检索（默认 fast），并落运行分片 |
| `python topic.py --topic "话题" --cached-only` | 只查本地语料 |
| `python topic.py --topic "话题" --save` | 采集 + 落分片 + 入库 |
| `python topic.py --ingest-last` | 把最近一次分片入库（不重新查询） |
| `python tools/dump_shard.py` | 查看最近一次运行采到了什么、字段覆盖如何 |
| `python collectors/fanout.py "话题" [pages]` | 并发跑 13 个 HTTP 源 |
| `python collectors/collect_http.py` | 单源/指定源采集（脚本） |
| `python tools/ingest_all.py` | 全量/增量入库 |
| `python tools/topic_persist.py --shard <run>` | 一条命令入库 + 抓正文（`topic.py` 的后台侧车） |
| `python collectors/backfill_fulltext.py --report` | 看正文覆盖率 |
| `python tools/clean_titles.py [--apply]` | 清理库里标题的残留标记（默认只报告，幂等） |
| `python tools/run_claims.py` / `show_verdicts.py` | A 的声明诊断（非 B 主链路） |

A 的 `periscope` / `periscope-web` / `periscope-mcp` 仍在 `pyproject.toml`
里，能启动，但读的是 `data/corpus.db`，不属于 B 的当前路径。

## 5. 依赖边界

B 的运行时（`core/`、`collectors/`、`topic.py`）只允许从 A 借用这些内核：

```
src._cli          force_utf8_output
src.models        ContentItem / Section / SourceType / SOURCE_SPECS / ExtractorConfig
src.corpus.store  Corpus（schema、FTS、add_items）
src.corpus.trust  publisher_of / trust / independence
src.corpus.sections
src.corpus.simhash
src.extractors
src.url_security
src._scratch
```

`tests/test_b_import_boundary.py` 扫描并钉住这条边界。`tools/` 里的声明诊断
（`run_claims` / `show_verdicts` / `backfill_claims`）是 legacy 运维脚本，
允许使用 `src.analysis` 与 `src.ai`，不在此边界内。

## 5.5 本次整理已经钉住的东西（2026-10-19）

- **数据目录**：`core/paths.py` 的 `PERISCOPE_DATA_DIR` 是 B 的唯一开关；
  `ingest_all` / `backfill_fulltext` / `run_claims` / `show_verdicts` /
  `test_retrieval_quality` 都走 `paths.CORPUS`，不再硬编码 `D:\work\...`。
- **入口**：`topic.py --deep` 的浏览器脚本改到 `collectors/`，缺脚本会进
  `failed` 而不是静默；`topic_persist` 的 `ingest_all` / `backfill_fulltext`
  路径改对，`--shard` 支持绝对、相对 shard 目录、相对仓库根三种写法。
- **导入**：`collectors/collector.py` 不再在 import 时重绑 `sys.stdout`
  （这是 pytest 在退出时 `I/O operation on closed file` 的根因）。
- **catalog**：删掉重复的 `bili_popular` 键（浏览器版改名
  `bili_popular_browser`），`fanout` 的字典不再静默丢源。
- **增量入库**：`--only ugc_*.json` 现在走 UGC 分支，不再被当成 HTTP 行；
  BOM 输入用 `utf-8-sig` 读取。
- **测试**：新增 `tests/test_b_*.py`（路径、merge、导入边界、catalog、
  入口冒烟），并把历史唯一的 `data/` 权限失败改成 `scratch_path`。

## 5.6 二轮代码级修复：七个静默错误（2026-10-19 晚）

这一轮的七个问题都属于同一类：**两条路径/两份状态都合法，谁也不报错，
后果是数据被丢、被截断，或被显示成另一种样子。** 每一个都先复现、再修、
再钉测试（`tests/test_b_path_agreement.py` 26 条、
`tests/test_b_title_cleaning.py` 17 条、`tests/test_b_clean_titles.py` 6 条、
`tests/test_b_local_bodies.py` 7 条、`tests/test_b_merge.py` +6 条）。

### 1. 正文缓存有两个位置，读的那个从来没有内容

```
写端  collectors/backfill_fulltext.py   ROOT / "fulltext_cache.jsonl"
读端  tools/ingest_all.py               CACHE_DIR / "fulltext_cache.jsonl"
```

实测：仓库根目录那份 **3,648 条 / 24 MB** 的缓存，`--fulltext-only`
一次也没读到过（读的那条路径根本不存在）。两个路径都可写、都存在过，
所以失败完全静默 —— 覆盖率表只是永远不动。

**修法**：`core/paths.py` 新增 `fulltext_cache()`，读写共用；它会把
历史位置的文件**迁到**规范位置（首次调用时），迁移失败退回复制。
`ingest_all` 改成函数调用而不是模块常量，因为常量就是漂移的载体。
历史位置仍以 `LEGACY_FULLTEXT_CACHE` 命名保留 —— 不记录旧路径，
同一个 bug 会被诊断第二次。

### 2. `--ingest-last` 会毒化后续所有运行

`_materialise()` 把临时文件写在 `<DATA_DIR>/topics/` 里，文件名
`http_items.topic.json`。而 `runstore.latest()` 按**文件名**排序，
`h` 排在所有 `2026...` 之后 —— 于是一次 `--ingest-last` 之后，
下一次 `--ingest-last` 和 `dump_shard` 都会读到那个残留文件。

**修法**：临时文件改写到 scratch 下的随机名（`mkstemp`），
`runstore._run_files()` 只认 `<YYYYmmdd-HHMMSS>_` 前缀的 .json。
两道防线，因为这类"残留文件抢位"在任何目录都可能再发生。

### 3. 浏览器采集一直在弹用户自己的 Chrome

```
collectors/browser_launch.py   ROOT = Path(__file__).resolve().parent   ← collectors/
```

它在 `collectors/.playwright-browsers` 找自带 chromium（不存在），
于是判定"没有自带浏览器"，返回 `channel="chrome"` —— Playwright 的
`chrome` 通道驱动**系统安装的** Chrome，共享用户的书签、登录态和窗口。
这正好是该模块文档里写着「要避免」的行为。

实测确认：修复前 `browser_channel()` 返回 `"chrome"`，修复后返回
`None`（= 用 Playwright 自带的 chromium）。

**修法**：改用 `core.paths` 的 `ROOT` / `BROWSERS` / `PW_TMP`；
三个浏览器采集器的 `PROFILES` 也统一走 `paths.PROFILES`，
`sys.path` 从 `parent` 改成 `parent.parent`。

### 4. `sys.stdout` 在 import 时被重绑，八处

`sys.stdout = io.TextIOWrapper(sys.stdout.buffer, ...)` 会把
「当前拥有这个流的东西」（pytest 的 capture、重定向、调用方的 wrapper）
从真实 fd 上摘下来，进程在收尾时抛 `I/O operation on closed file`。
工作已经做完，却在最后一步看起来像崩了。

**修法**：新增 `core/console.py` 的 `force_utf8_output()`，用
`stream.reconfigure(encoding=...)` 只改编码、不换对象；不能
reconfigure 的流（`StringIO`、capture 对象）原样保留。
八个模块改为调用它，且只在自己的 `__main__` 路径调用。

### 5. `tools/topic_persist.py` 从未成功过

它把**运行分片**（一个带 `rows` 的字典）直接交给 `ingest_all --only`，
而后者读的是**行列表**。于是 ingest 迭代了字典的键，并对字符串
`"version"` 调用 `.get`：

```
AttributeError: 'str' object has no attribute 'get'
```

这个错误发生在 ingest **没有任何有效输出之后**，所以日志只写
`ingest rc=1`，抓正文那一半也没拿到行。实测修复前：

```
$ python tools/topic_persist.py --shard <run>.json --fetch-body 0
ingest rc=1: AttributeError: 'str' object has no attribute 'get'
```

修复后：

```
$ python tools/topic_persist.py --shard <run>.json --fetch-body 2
ingest ok: 189117 items
```

**修法**：转换逻辑收进 `core/runstore.py`
的 `ingestable_rows()` / `write_ingest_file()`，`topic.py --ingest-last`
和 `topic_persist.py` 共用同一份 —— 两个调用方各自实现一份，
正是它们当初漂移开的原因。顺带把 `.urls.txt` 与临时分片都移出
topic 目录（和问题 2 同源：任何残留在那个目录里的文件都可能抢位）。

### 6. 标题上的标记一路漏到屏幕上，还破坏了去重

两处，都是「清洗函数只处理了它见过的形态」：

**（a）`collectors/collector.py` 的 `_clean()`** 只删真标签 `<em>`。
头条把高亮标记**转义在 JSON 字符串内部**返回，所以 `json.loads` 之后
文本里是字面量 `\u003cem\u003e`，标签正则匹配不到。实测这个仓库
自己的分片里有 **60 条**这样的标题，实测库里 **114 条**。
HTML 实体（`&amp;` / `&quot;` / `&#39;`）也从没解过，库里另有 **331 条**。

```
修复前：掌握 GPU 并行编程（硬件 &amp; 软件）- 第二章
修复后：掌握 GPU 并行编程（硬件 & 软件）- 第二章
```

**修法**：`_clean()` 增加两个步骤，顺序是先解 `\uXXXX`、再删标签、
最后解实体。解转义只替换 `\uXXXX`（不用 `unicode_escape` —— 它会把
`\n` 变成控制字符，还会按 latin-1 解码，把中文变成乱码；第一版
就是这么写的，`test_cjk_survives_the_escape_decode` 钉住了它）。

**（b）`core/merge.py` 的 `norm_text()`** 不处理实体，于是
`A &amp; B 公司发布新产品` 与 `A & B 公司发布新产品` 归一化成不同指纹，
**同一条新闻算两条**。这不只是重复展示：`_independent` 把两个标题
当成两家独立报道，**虚假地给出「多家独立报道」的加成**。
这是这个 bug 最坏的形态 —— 重复项不仅可见，还被当成互相印证。

**修法**：`norm_text()` 在标点清理**之前**先 `html.unescape()`。
顺序是必须的：`&` 是标点，会在下一步被删掉，两边才真正对齐。
只解一层，`&amp;amp;` 保持为 `&amp;`。

**已有数据的修复**：新增 `tools/clean_titles.py`（默认只报告，
`--apply` 才写库，幂等）。实测跑了 445 行：

```
escaped tag    114 -> 0
entity &amp;    167 -> 0
entity &quot;    95 -> 0
entity &#39;     73 -> 0
items / items_fts 均保持 189,117（FTS 触发器跟随更新）
```

### 7. 本地层只返回正文的前 400 字

排序刻意只读 `SUBSTR(content,1,400)` —— 它要打分几百条候选，整列很大，
这个取舍对**排序**是对的。但 `topic.py` 把 `snippet` 当成 `body`
交给调用方，于是**一篇 22,680 字的文章到达读者手里只有 400 字**，
没有省略号、没有长度提示，看起来像「这篇文章很短」，而不是「我们截断了」。

最便宜的本地层，反而返回最少的内容。

**修法**：新增 `core/search.py:hydrate_bodies()`，只对**排完序留下来
的那几条**补一次查询取正文（默认上限 4,000 字/条，防止单篇病态文档
撑爆输出）。实测同一条从 400 字变成 4,000 字。

顺带修掉一个只有跑真库才会暴露的错误：`search()` 返回的是
`sqlite3.Row`（只读），直接 `r["body"] = ...` 会抛 `TypeError`，
必须转成 dict。

### 顺带清掉的

- **`--cached-only --save` 静默无效**：本地行本来就在库里，入库无事可做 ——
  这是对的，但它**不说**。用户看到分片路径、看不到入库行，无从判断是
  保存了还是跳过了。现在会打一行说明，并指向正确的命令。
- `collectors/backfill_fulltext.py` 里重复的 `return out`（死代码）。
- `tools/ingest_all.py` 重复的 `ROOT = ...` 赋值和 import 时重绑 stdout。
- `topic.py` 里空的 `force_stdout()`（保留调用点但什么都不做，且注释与
  实际行为不符）。
- `collectors/collector.py` 里写死的 `D:\work\periscope\data\cookies`
  改成 `core.paths.COOKIES`，并给 `COOKIES` 加了 `PERISCOPE_COOKIE_DIR`
  覆盖（凭据可能不该落在仓库里）。
- `tools/` 里六个脚本写死的 `.pwprofiles` / `data/cookies` 路径。
- `tools/backfill_claims.py` 写死的 `.periscope-scratch/corpus.db`
  （设了 `PERISCOPE_DATA_DIR` 之后它会读一个库、别人写另一个库）。
- 一批未使用的 import（`io` / `time` / `sys` / `Any` / `field` / `Callable`）。

## 5.7 检索地基：把「广度」做成可测量的（2026-10-19 晚）

### 先量清楚三层各给了什么

| 层 | 一次查询产出 | 带正文 | 特点 |
|---|---|---|---|
| API（单次查询） | 50（接口硬上限） | 10/10 | 正文最丰富，中位 2,484 字 |
| 本地语料 | 150–14,303 | 3–45% | 广度大，正文少 |
| 平台采集 | 204–261 | ~0.5% | 最新，几乎无正文 |

结论：**API 是正文层，本地是广度层，平台是时效层**。三者不能互相替代，
而 API 层被单次 50 条的上限卡住了。

### 核心发现：换问法能突破 50 条上限

同一个话题换几种问法，返回集合差别很大。实测「固态电池」：

| 变体 | 新增唯一结果 |
|---|---|
| `固态电池`（基线） | 50 |
| `固态电池 site:news.cn` | +49 |
| `固态电池 site:people.com.cn` | +47 |
| `固态电池 最新进展` | +33 |
| `固态电池 2026` | +39 |
| `固态电池 技术` | +27 |
| `固态电池 量产` | +20 |
| **合计** | **266（5.3 倍）** |

**质量不降**：266 条里 264 条带正文（99%），中位 2,118 字，92 个独立发布者。

机理：变体打散的是**排序**，不是相关性。单次查询的 50 条是同一个 rerank
视角的前 50，换问法就换视角，捞起的是原本排在 51 名之后、但同样相关的
内容。早先「多查几次收益很小」的结论只看前三名——前三名确实重叠，
**第 51 名之后不重叠**，而广度正在那里。

### 分档实测（`count=50`）

| depth | 结果 | 耗时 | 正文≥400 | 发布者 |
|---|---|---|---|---|
| 1 | 50 | 1.2s | 50 | 35 |
| 3 | 149 | 2.6s | 149 | 42 |
| 5 | 218 | 4.2s | 216 | 75 |
| 7 | 300 | 5.0s | 297 | 88 |

`API_DEPTH = {fast: 3, normal: 5, deep: 7, off: 3}`。默认 fast 付 1.4 秒
换 3 倍广度；`--api-depth 1` 完全恢复旧行为。

### 默认路径的前后对比（话题「固态电池」）

```
之前：本地 150 · API 50 · 平台 261  → 6.0s
之后：本地 150 · API 149 · 平台 204 → 8.2s
```

且前 8 条从「部分带正文」变成 **8/8 带正文、8/8 多源印证**。

### 顺带修掉的三个「采了但没用」

1. **v2ex_search 的 20 条无关内容**：sov2ex 是分词 OR 索引，`固态电池`
   返回 511 条，前 20 条是「油车换电车」「MacBook 二手」「三星手机」
   —— 它分别匹配了 `固态` 和 `电池`。加引号返回 0 条，所以过滤只能
   在我们这一侧做。新增 `Source.require_query_in_title`，按**去空白后的
   短语**过滤（CSDN 会在标题里插高亮空格，精确匹配会误杀）。
   实测这 20 条现在被挡在门外，而 CSDN 的 28/30 条相关内容保留。

2. **hupu_search 白烧预算**：搜索页是客户端渲染，121 KB HTML 里只有
   `<title>`/`<meta>` 含查询词，列表走 XHR。且它间歇性返回 405，
   重试逻辑读作「限流」再试 3 次 —— 每次运行在 30 秒预算里烧掉约 12 秒
   采一个**不可能出结果**的源。标为 `needs_browser=True`，`fanout` 跳过
   并在 `skipped_browser` 里报告，而不是假装它采到了 0 条。

3. **平台行的正文被丢弃**：`platform_lookup` 把 `"body"` 硬编码为空，
   而 v2ex 返回最多 1,200 字的帖子正文就放在 `text` 里。三个地方
   因此读错：渲染显示「正文: 0 字」、分片存 `body=""`、正文计数偏少。
   现在 `text ≥ 400` 就作为 `body`（阈值对齐 `core.search.MIN_BODY`）。

### 正文覆盖率的真实约束

按 host 实测 `trafilatura` 恢复率（每个 host 抽 4 条摘要行）：

```
能恢复：theshamblog / github / twitter / windowslatest / yoshuabengio /
        deepmind / lwn / arxiv / anthropic / martinalderson / lmstudio /
        microsoft.github.io / spectrum.ieee      → 13/18 个 host
完全阻断：docker.com / ai.meta.com / ai.facebook.com /
        arstechnica.com / reuters.com            → 5/18 个 host（403/400/405）
```

且 qbitai 这类站点是 **JS 渲染**：48 KB HTML 里纯文本只有 1,266 字，
正文由前端加载，`trafilatura` 提取不到不是 bug。

所以「正文覆盖率 13.5%」不是抽取器调参能解决的：一部分 host 阻断，
一部分 JS 渲染。要提高只能靠浏览器采集或换有正文的接口。

### 验证

`pytest -p no:cacheprovider` → **1254 passed**，其中新增
`test_b_api_breadth.py`（14 条）、`test_b_platform_body.py`（5 条）、
`test_b_source_filter.py`（8 条）。

```bash
python topic.py --topic "固态电池"                    # 默认 fast，3 问法
python topic.py --topic "固态电池" --speed deep       # 7 问法，约 300 条
python topic.py --topic "固态电池" --api-depth 1      # 旧的单次查询
```

## 6. 已知断点（本文件不粉饰）

1. **入库仍是手动**：`topic.py` 不会自动入库；`--save` / `--ingest-last` 是显式的
   一步。这是设计选择（写库是副作用），不是遗漏。
2. **两个 corpus**：B 的 `<DATA_DIR>/corpus.db` 与 A 的 `data/corpus.db`
   仍是两个文件。
3. **13 / 179 接进话题检索**：179 个 catalog 源里只有 12 个真正跑 HTTP
   （hupu_search 已标为 browser-only，v2ex_search 加了短语过滤）。其余是
   列表/快照/浏览器源。这是最大的一块未开发广度 —— 但注意
   §5.7 的结论：**广度不只在源数量，更在问法数量**。API 层一个话题
   换 7 种问法就从 50 条到 300 条，比再找 20 个源更便宜。
4. **互动只对两个源采集**：B站与微博已在搜索响应里声明 `metrics`；其余源
   尚未声明，因此覆盖率表会显示 47/314 这样的真实数字。
5. **B站不进 fast 模式**：它单源约 26 秒，会让默认查询从 6 秒变成 30 秒；
   目前只在 `--normal` / `--deep` 启用。
6. **深度模式未在线验证**：脚本路径已修到 `collectors/`，但小红书/抖音/快手
   仍受登录态与风控限制。**注意**：修好 `browser_launch.py` 之前，这一条
   更准确的说法是「深度模式会弹用户自己的 Chrome」—— 隔离性刚修好，
   但深度的**结果**仍未在线验证过。
7. **B 仍多数未跟踪**：`core/`、`collectors/`、`tools/`、`topic.py` 尚未提交。

## 7. 验证

```bash
# B 的边界与冒烟
pytest -q tests/test_b_paths.py tests/test_b_merge.py \
         tests/test_b_import_boundary.py tests/test_b_catalog.py

# 路径一致性（二轮修复后新增；26 条，全部对应一个真实的静默错误）
pytest -q tests/test_b_path_agreement.py
# 标题清洗与正文完整性
pytest -q tests/test_b_title_cleaning.py tests/test_b_clean_titles.py \
         tests/test_b_local_bodies.py

# 全量（A 仍占大头；唯一的历史环境失败已改为 scratch_path）
pytest -q
```

实测（2026-10-19 14:25，本机 `pytest -p no:cacheprovider`）：
**1204 passed in 62.35s**，其中新增的 B 侧测试 57 条：
`test_b_path_agreement.py` 27、`test_b_title_cleaning.py` 17、
`test_b_local_bodies.py` 7、`test_b_clean_titles.py` 6，另有
`test_b_merge.py` 从 6 条增到 10 条、`test_retrieval_quality.py` +1 条。
（历史文档里的 1,093 是按文件数估的，不是实测；这里改成实测值。）

端到端手测（修复后，全部实跑过）：

```bash
python topic.py --topic "固态电池" --cached-only     # 落分片，0.03s
python topic.py --ingest-last                        # 9 行 -> in-place，无残留
python tools/ingest_all.py --fulltext-only           # 读到 3,648 条缓存
python tools/topic_persist.py --shard <run>.json     # ingest ok: 189117 items
python tools/clean_titles.py --apply                 # 445 行标题洗净，可重复跑
```

## 8. 下一步（需要单独拍板）

- **A 物理迁移**：`src/` → `legacy/src/`，并同步 import；这会改动 A 的
  1,100+ 条测试（实测全量 1,165，A 占绝大多数），必须单独做。
- **合库**：把 `<DATA_DIR>/corpus.db` 迁到 `data/corpus.db`，或反过来让
  A 的入口指向 `PERISCOPE_DATA_DIR`；1.56 GB 文件迁移需要确认。
- **回写闭环**：让 `topic.py` 在输出后异步调用 `topic_persist.py`，并加
  一条“采 10 条 → 入库 → 再查命中”的端到端测试。

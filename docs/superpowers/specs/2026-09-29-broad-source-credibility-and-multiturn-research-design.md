# 设计：广源可信取证 + 多轮共创调研文档

- 日期：2026-09-29（v4。v2 对照代码审计，v3 分层泄漏实测 + 贴吧可达性实测，v4 三期交付记录，见 §13、§14、§15）
- 状态：P0 / P2 / P1 已实现并推送（§15）。剩余阻塞项只有维护者能做的两件：声明 verdict 的人工标注、S1/S2 探针的后两步
- 范围：本仓库（`NLY22/periscope`，fork 自 `Thysrael/Horizon`）的两项能力扩展；不改动上游日报管线的行为
- 本文所有行号于 2026-09-29 对照 `main`（`8be37ed`）核实；测试基线 697 collected（2026-09-29 本机复核 `uv run pytest --collect-only` = `697 tests collected in 1.81s`），三期 + 文档/UI/贡献者侧护栏 + §6.1 的导出入库通路 + §6 的可达性判别工具、以及 §8 的三张结构性图与图元护栏 + 6/7 号数据图与图像护栏 + 消融表逐格对齐 + 采集边界与全仓库符号网、导入面板、路由可达性、命令行标志与 DNS 注入接缝完成后 **972 collected 全绿**（逐轮增量见 §15 与 `CHANGELOG.md`）
- **v3 的两条实测结论推翻了 v2 的两个前提**，都记在 §14：① 分层污染不是"未来接新源才会发生"，而是**现役 3 个源正在污染** claim 链路；② 贴吧楼层页从本机不可达，**不能**作为 P0 的验证载体。

---

## 0. 目标的形式化

- **G1 广源取证**：把质量良莠不齐的源（贴吧、小红书等）纳入后，**每条进入证据链的内容都带可审计的可信度依据**，且"独立信源数"不会因为同源转载、模板化文案、评论区文本而虚高。判据不是"采到多少条"，而是"claim 的 verdict 在人评上站得住"。
- **G2 多轮共创**：调研文档从"一次性投影"变成**有版本、可被用户增量塑形、可局部重算的持久工件**；系统能在轮次之间**主动向用户索取缺失输入**，而不是每次都整树重跑再吐一整份报告。

两者的公共前置是一处地基缺陷（见 §1.1），所以先修地基。

---

## 1. 决定设计的现状接缝（均已核实到行号）

### 1.1 静默地雷：仓库里有**两套互不相交**的分层词表，其中一套正在漏

`src/corpus/sections.py:33-39` 用 5 个精确中文字符串（`【评论区 Top】`、`【视频字幕节选】`、`【回复精选】`、`【楼层讨论】`、`【评论区】`）把 `content` 反解成 primary / community 两层。`split_sections:56-80` 的规则是"**第一个标记之前的文本一律算 primary**"。

但仓库里还有**第二套**分层实现：`src/processing/content.py:7` 的 `COMMENTS_MARKER = "--- Top Comments ---"` 与 `split_content:16-23`。两套词表**零交集**，服务的链路也不同：

| 词表 | 实现 | 服务的链路 | 发出该标记的 scraper |
|---|---|---|---|
| 英文 `--- Top Comments ---` | `processing/content.py:split_content` | **日报/富化**：`ai/analyzer.py:109`、`ai/prompting/enrichment.py:155` | `reddit.py:498`、`hackernews.py:113`、`twitter.py:253` |
| 中文 `【…】` × 5 | `corpus/sections.py:claimable_text` | **取证/claim/研究**：`claims.py:443`→`:624-631`、`claims.py:503`、`claims.py:639`、`store.py:222`、`session.py:716` | `bilibili.py:106/114`、`discourse.py:155`、`v2ex.py:109` |

**后果（2026-09-29 本机实测，不是推演）**：Reddit / Hacker News / Twitter 三个源的人群文本，在日报链路里被正确剔除，在 claim 链路里却**整段被判为作者亲写**。复现：

```python
from src.corpus.sections import claimable_text
hn_link_post = "\n\n".join(["", "--- Top Comments ---",
                            "[alice]: the benchmark is rigged",
                            "[bob]: no it isnt, here is data"])
claimable_text(hn_link_post)
# '--- Top Comments ---\n\n[alice]: the benchmark is rigged\n\n[bob]: no it isnt, here is data'
# community_text(hn_link_post) == ''   ← 空
```

HN 的**链接帖**（`story.get("text")` 为空，`hackernews.py:109-110`）是最坏情况：`content` 里 100% 是评论，而 100% 都进了 `claimable`。这些文本随后：进 `claim_fts`（`store.py:93-97`）→ 成为 claim 抽取输入（`claims.py:443`）→ 被 `link_evidence` 以 `tier="claimable"` 关联（`claims.py:503`）→ 成为 grader 摘录（`claims.py:639`）→ 抬高 `independent_sources`（`claims.py:209-225`）。

**没有任何测试会失败**：`tests/test_evidence_tiers.py` 只覆盖中文标记，`tests/test_content_selection.py:5` 只覆盖日报侧的英文标记，`tests/test_twitter.py:589` 甚至**断言了** `--- Top Comments ---` 出现在 content 里（即把泄漏当成了预期行为）。

所以 v2 把这条写成"任何新 scraper 只要不用这几个字符串"是**低估**了：不需要等新源，**现役 3 个源已经在污染**，而且 `hackernews` 在默认配置里就是 `enabled=True`（`models.py:245`）。这决定了 P0 的核心不是"加一个源"，而是**把分层从字符串约定改成类型化字段 + 测试守护，并把 6 个 emitter 全部迁过去**。

### 1.2 `ContentItem` 的两个必填字段挡掉了噪声源
`src/models.py:119 url: HttpUrl`（必填）、`:122 published_at`（必填），且 `ContentItem` 是 `extra="forbid"`（`:114`）。**10 个 scraper 文件、12 处**写了 `if published_at < since: return None` 这类时间门（bilibili / discourse / github / hackernews / reddit / rss / telegram / twitter / twitter_playwright / youtube；v2ex、openbb、gdelt、google_news 没有）。App-only 内容、无稳定 canonical URL、热榜/推荐流这类非时间序源，条目会**静默消失**，只在日志里显示 "Found 0 items"（`orchestrator.py:857-896`）。

跨源去重完全建立在 URL 上，而且有**两套互不相干的实现**：`orchestrator._deduplication_url_key:63-89`（返回 scheme/user/host/port/path/query 七元组，已剔除 `utm_*` 与追踪参数）与 `processing/history.py:_url_key`（`urldefrag` + 去尾斜杠）。后者读的是 **markdown 摘要文件**（`history.py:60` glob `horizon-*.md`，顺带一处旧名残留），不是数据库。

### 1.3 加一个源要手工同步 5 处（第 6 处是装饰性）
- `models.py:10-26`（闭合 `SourceType` enum）
- `models.py:37-52`（`SOURCE_REGISTRY`）
- `models.py:614-630`（`SourcesConfig`，每源一个强类型字段）
- `orchestrator.py:19-33`（静态 import）
- `orchestrator.py:766-841`（14 段硬编码 `if config.sources.X and X.enabled:`）
- ~~`web/static/index.html:76-82`~~ → 每源一个 pill CSS 类，但有 `.p-default` 回退，属**装饰性**，漏改不影响正确性

**MCP 侧不是同步点**：`mcp/horizon_adapter.py:20` 已经是 `VALID_SOURCES = frozenset(SOURCE_REGISTRY)`，本来就派生。无一致性测试守护上述 5 处。

### 1.4 图文内容在语料里不存在
证据链是纯文本 FTS5 trigram（`store.py:69-73`）。全仓无 OCR/vision；图片只把 URL 塞进 metadata（`twitter_playwright.py:239`、`youtube.py:145`）。唯一的媒体→文本通路是 B 站 CC 字幕（`bilibili.py:120-140`），需登录的 AI 字幕被显式跳过。

### 1.5 多轮交互缺"停下来问用户"的接缝
- `Planner` 协议只有 `decompose/answer/revise`（`session.py:328-342`），`decide` 是可选的查询改写（`:537-558`）。**没有"向用户索取输入"这个动词。**
- `followup:870-897` 是原子整树重跑：`reset_budget()` + `investigate()` 遍历**所有** open 子问题（`:436`），返回整份报告。
- 报告是纯函数实时重渲（`render_report:758-850`），与 investigate 存的 report turn 快照（`:454`）可能漂移；**无草稿态、无版本、无 diff**。
- 会话状态机 `created → investigating → reported → closed`（`:71`），**没有"等用户"这个状态**。
- Web 面板 start/followup 阻塞到整份报告返回（`web/app.py:210/220`，`static/index.html:330` 全程转圈）。

### 1.6 核查层：能力已实现，效果零实测；且计数门的位置和我原先想的不同
`VERDICTS = {supported, contested, unsupported}`（`claims.py:43`）已落地。关键是 **`grade_min_sources=2`（`models.py:561`）不是判定门，而是分诊门**：`pending_grading:227-234` 用 `WHERE status='linked' AND independent_sources>=?` 筛出"值得花 LLM 预算评级"的 claim，verdict 本身由 LLM `grade_claim:560-596` 给出（只看 400 字符摘录，`:633-640`）。低于阈值的 claim **永远停在 `status='linked'`、`verdict IS NULL`**，而报告的核查摘要只显示 `status='graded' OR independent_sources >= 2`（`session.py:859-862`）—— 也就是说这些 claim 在报告里彻底不可见，读者无法知道"还有一批没被评级"。

实测缺口：`data/eval/` 下只有 `corpus_fixture.json`、`queries.json`、`results.json`，**`claims_labels.json` 与 `claims_results.json` 都不存在** —— 从未跑过一次真实人评；检索消融表的语义腿用的是 stub（`scripts/eval_retrieval.py:57-79`）。`docs/evaluation.md:40` 已自陈。

### 1.7 限速/重试是两处重复实现，不是完全没有
`reddit.py:539-542` 与 `telegram.py:67-68` 各写了一份 429 + `Retry-After` 处理，`ai/client.py:630` 另有一份 LLM 侧的。`BaseScraper`（`base.py:11-34`）本身不含任何节流，`orchestrator.py:843` 用 `asyncio.gather` 全并发。

### 1.8 可以复用的资产（不要重造）
SQLite 会话状态 + `research_turns` + `research_actions`（`session.py:79-124`）已可中断恢复；自适应取证梯子 `_retrieval_ladder:459-471` 已有"记录每次尝试"的审计习惯；`citations.audit_report:57` 已能反解成品报告校验引用；`analysis/agreement.py:50 score_pairs` + `:92 independence_buckets` 已是标准分类评测口径；`store.py:155-178` 已有 `ALTER TABLE` + backfill 的迁移范式可直接照搬；`RunStore.invalidate_after:67` 的"上游变→下游失效"思路正确，只是**破坏性**（直接删文件），草稿工件要非破坏性版本。

---

## 2. 架构总览

```
[源接入层]  SourceSpec 元数据(models.py) + SCRAPER_BINDINGS(src/sources/registry.py)
             ├─ throttle（per-host 令牌桶）
             ├─ auth（cookie/token 两种 provider）
             └─ scraper.fetch() → ContentItem{locator, sections[], time_basis}
                        ↓
[证据层]    corpus.db  items(sections 结构化) + source_profiles + media_text(P3)
             ├─ 分层：由 sections[].tier 决定（不再反解字符串）
             ├─ 指纹：text SimHash/MinHash + image pHash → cluster_id
             └─ 检索：FTS5(claimable) + RRF（+ 可选向量腿）
                        ↓
[可信度层]  trust(item) = σ(源先验, 作者等级, 交叉支持, 可核验实体, 新鲜度,
                            provenance, −模板度)
             ├─ independent(claim) = 跨 (source_type, publisher) 去重计数
             └─ 分诊门与判定门都换成 noisy-OR 聚合信任     θ 由人评 ROC 定
                        ↓
[研究会话层] 状态机 created→planning→investigating⇄awaiting_user→drafting→reported→closed
             ├─ Planner.next_move() → AskUser | Rescope | Deepen | Finalize
             ├─ research_drafts（revision, 章节级 locked/hash）
             └─ 局部重算：section ← subquestion ← evidence 依赖图
                        ↓
[入口层]    MCP hz_research_step / draft / edit   +   Web 逐轮时间线与可编辑章节
```

---

## 3. P0：地基（结构化分层 + 6 个 emitter 迁移 + 源注册表）

**为什么先做**：不修 §1.1，接任何噪声源都会立刻污染证据层，而且污染是静默的 —— 更要紧的是**污染已经在发生**：HN / Reddit / Twitter 的人群文本此刻就在 `claimable` 列里。

### 3.1 数据模型
把 `src/corpus/sections.py:47` 的 `Section` dataclass 提升为 `models.py` 里的 Pydantic 模型并扩字段（`sections.py` 保留 `split_sections` 作为**老库 backfill 与消融 A 档的 legacy 路径**，不再是新写入的判据）：

```python
class Section(BaseModel):
    tier: Literal["primary", "community"]
    text: str
    author: str | None = None    # 楼层/评论的作者；迁移后 content 靠它保留 "@user:" 归属
    provenance: Literal["author", "transcript", "ocr", "vlm", "legacy_marker"] = "author"
    asserted: bool = True      # False = 不是作者的断言（如 VLM 对画面的描述）
    confidence: float | None = None
    locator: str | None = None # 楼层号/评论 id，用于精确引用
```

`author` 是 v3 补的字段，理由：今天 `discourse.py:153` 与 `reddit.py:506` 把作者写进拼接字符串（`- @helper: …`、`[alice (42 pts)]: …`），而 `items_fts` 索引 `content`、面板也显示 `content`。若 `Section` 不带 `author`，迁移后归属信息就丢了 —— 这正是"改成类型化字段反而丢数据"的典型。

`ContentItem` 改动（`models.py:111-126`）：
- 新增 `locator: str`（稳定标识，可以是 URL，也可以是 `tieba:p/123#45`、`xhs:note:abc` 这类非 URL 稳定串）；`url: Optional[HttpUrl]` 降级为展示用。
- `published_at: Optional[datetime]` + 新增 `time_basis: Literal["published", "crawled", "unknown"]`。`since` 过滤**只对 `time_basis != "unknown"` 生效**。
- 新增 `sections: List[Section]`；`content` 保留为 sections 的拼接（向后兼容），`claimable` 由 `sections` 中 `tier=="primary" and asserted` 计算，而非 marker 反解。

**⚠ 归一化放在模型层，不放写入层。** v2 打算让 `store.py` 在写入时回填 `url`/`published_at`，但那要改 12+ 个消费点。改为在 `ContentItem` 的 `model_validator(mode="after")` 里一次做完：
- `locator` 缺省 → 取 `str(url)`；两者都没有 → 校验失败（`locator` 是不可让渡的身份）
- `published_at` 为 `None` → 填 `fetched_at`，并把 `time_basis` 置为 `"unknown"`
- `sections` 非空 → `content` 由 `sections_to_content(sections)` 重算（单一事实源）

**净效果：构造完成后 `item.published_at` 永不为 `None`**，于是 `claims.py:449` 的 `.date()`、`store.py:219` 的 `.astimezone()`、以及所有按时间排序的查询都不用改；时效语义只由 `time_basis` 承载。`url` 仍可能为 `None`，但实测其消费点大多是优雅降级（`summarizer.py:27 _safe_url(None)` 返回 `None` → 标题不带链接），只有 6 处会把字面量 `"None"` 写进 LLM 提示词或 webhook 载荷，统一换成新属性 `ContentItem.citation_url`（`str(url) if url else locator`）：`ai/prompting/analysis.py:43`、`ai/prompting/classification.py:38`、`ai/prompting/enrichment.py:169`、`analysis/claims.py:448`、`processing/tools.py:78`、`services/webhook.py:552`。

**仍然不动数据库的 NOT NULL 约束。** `items.url TEXT NOT NULL`（`store.py:54`）与 `published_at TEXT NOT NULL`（`:56`）背后有 `idx_items_published`（`:66`）和大量按时间排序的查询；把列改成可空是破坏性迁移，收益为零。写入侧：`url` 为空 → 写 `locator`（`store.py:217` 的 `str(item.url)` 必须改，否则写入字符串 `"None"`）；`published_at` 已由模型层保证非空。

去重：不要新造第三套规范化逻辑。`locator` 是 URL 时复用 `orchestrator._deduplication_url_key:63-89`，不是 URL 时退化为 `("locator", 原值)` 二元组（`_deduplication_url_key` 的七元组语义与 `tests/test_cross_source_duplicates.py:36-72` 的既有断言都不动）。`history.py:_url_key`（`urldefrag` + 去尾斜杠）**不需要改**：它只处理日报 markdown 里的链接，输入恒为真实 URL；对非 URL locator 它也已经能安全降级（`urldefrag("tieba:p/123#45")` → `tieba:p/123`，而去掉片段正是文件侧去重想要的）。要改的只是它的调用点 `processing/tools.py:78`，把 `str(current_item.url)` 换成 `current_item.citation_url`。**不声称两套口径已统一** —— 它们服务不同介质（DB 行 vs markdown 文件），统一是假目标；只保证各自对 locator 不崩、不误判。

`time_basis="unknown"` 的条目没有时间门 → 每轮都会被重抓。幂等由 `INSERT OR IGNORE` + `id UNIQUE`（`store.py:229`、`:51`）保证不会重复入库，但**必须在分析/富化阶段跳过已见过的 id**，否则会反复烧 LLM 预算。

迁移：`store.py:38 SCHEMA_VERSION 2 → 3`，沿用 `:155-178` 的 backfill 范式：新增 `locator`/`time_basis`/`sections_json` 三列，老行的 `sections` 由 `split_sections(content)` 生成并标 `provenance="legacy_marker"`，老行 `locator` 取 `url`、`time_basis` 取 `published`。

### 3.2 源注册表（分两层，否则会循环依赖）
**⚠ `SourceSpec` 不能持有 scraper 类。** `models.py` 只 import stdlib + pydantic（已核实），而所有 scraper 都 import `models`；把 `type[BaseScraper]` 放进 `models.py` 会造成循环导入。拆两层：

```python
# models.py —— 纯元数据，无 scraper 引用
@dataclass(frozen=True)
class RateLimit:
    requests: int = 1
    per_seconds: float = 2.0
    jitter: float = 0.3          # ±30% 抖动，避免固定间隔被识别

@dataclass(frozen=True)
class SourceSpec:
    key: str                                   # "discourse"
    kind: Literal["official", "forum", "ugc_social", "aggregator", "search_engine"]
    credibility_prior: float                   # 0..1，手工设定，P1 用人评校准
    login_required: bool
    editorial_gate: bool
    time_basis_default: Literal["published", "crawled", "unknown"]
    rate_limit: RateLimit | None
    # 下面三个是现有 SourceDefinition 的字段，必须一起搬进来，否则
    # SOURCE_REGISTRY 无法由 SOURCE_SPECS 派生（mcp/horizon_adapter.py:200/218 在消费它们）
    config_field: str                          # "discourse"
    config_is_list: bool = False               # github / rss 为 True
    item_fields: tuple[str, ...] = ()          # ("sites",) 之类

# src/sources/registry.py —— 新模块，可以 import 两边
# ⚠ 不能是 dict[str, type[BaseScraper]]：两个源的构造不是"类 + config + client"
#   · RSS    需要第三个参数 ExtractorRegistry(config.extractors)（orchestrator.py:776-781）
#   · Twitter 按 cfg.mode 在 TwitterScraper / TwitterPlaywrightScraper 之间二选一，
#             且 Playwright 版构造时**不接受** client（orchestrator.py:795-800）
# 所以绑定是工厂函数，不是类：
ScraperFactory = Callable[[Any, Optional[httpx.AsyncClient], BuildContext], Optional[BaseScraper]]
SCRAPER_BINDINGS: dict[str, ScraperFactory] = {"discourse": simple(DiscourseScraper), ...}
```

派生关系（单向，不得反向依赖）：

```python
SOURCE_SPECS: tuple[SourceSpec, ...] = (...)                  # 唯一手工维护处
SOURCE_REGISTRY = {s.key: SourceDefinition(s.config_field, s.config_is_list, s.item_fields)
                   for s in SOURCE_SPECS}                       # 派生，horizon_adapter 不用改
```

**⚠ 不改 `data/config.json` 的格式。** `SourcesConfig`（`models.py:614-630`）是每源一个强类型字段的静态 Pydantic 模型；"字段从注册表派生"只有把 config 改成 `Dict[str, ...]` 才做得到，那是对所有现有用户的**破坏性格式变更**，收益不抵成本。所以注册表的职责收窄为：

| 同步点 | P0 之后 |
|---|---|
| `SourceType` enum | 保留闭合 enum（`SourceType.` 在 src/ 出现 37 次，放宽成 `str` 太侵入），新源加成员，**由守护测试强制与注册表一致** |
| `SOURCE_REGISTRY` | 由 `SourceSpec` 列表派生 |
| `SourcesConfig` 字段 | **仍需手工加**（守护测试强制存在） |
| `orchestrator` import + 14 段 `if` | 改为遍历注册表 + `SCRAPER_BINDINGS`；并发按 `rate_limit` 分组 |
| MCP `VALID_SOURCES` | 已经是派生的，不动 |
| Web pill CSS | 装饰性，新源可用 `.p-default`，不阻塞 |

净结果：**手工同步点从 5 处降到 2 处**（config 字段 + scraper 实现），其余派生或测试守护。这比原先承诺的"降到 1 处"弱，但是真的。

守护测试 `tests/test_source_registry.py`：注册表 ↔ enum ↔ `SourcesConfig` 字段三者一致；每个 binding 的类存在且能用默认 config 实例化；每个 scraper 必须声明 sections 策略（见 §3.4）。

### 3.3 抓取基础设施
- `src/scrapers/throttle.py`：per-host 令牌桶 + 抖动 + `429/Retry-After`，把 `reddit.py:539-542` 与 `telegram.py:67-68` 这两份重复实现收上来。
- `src/scrapers/auth.py`：两种 provider —— env token、cookie 文件（含**过期检测与失效重试一次**）。`BaseScraper.__init__`（`base.py:14`）签名扩为可选注入 `throttle`/`auth`，**保持现有 14 个 scraper 不改也能跑**（默认值 = 现行为）。签名类鉴权（x-s/x-t）留到 S1 有结论再说，现在不预留空壳。
- 共享 client 统一 `follow_redirects=True`（修 `orchestrator.py:763` 与 `:458` 不一致）。

### 3.4 P0 的验证载体：迁移全部 6 个 emitter（贴吧降级为 S2，理由见 §14.2）

v2 打算用"接百度贴吧"当 P0 的端到端验证载体，理由是"无登录墙的结构化 HTML，楼层天然对应 sections"。**这个前提在 2026-09-29 实测中被推翻**（完整证据见 §14.2）：贴吧的楼层页 `/p/{tid}` 与列表页 `/f?kw=` 从本机全部返回 `HTTP 403` + `百度安全验证`（BIOC 验证码），唯一可达的 `/f/good?kw=` 只有主题列表（tid + 标题 + 作者 + 摘要），**没有楼层**；官方客户端 API `c.tieba.baidu.com/c/f/frs/page` 返回 `error_code 110001`，即需要 `sign`，而那属于 §11 明确排除的签名逆向。没有楼层就没有 community 层，贴吧无法验证分层改造。

改为用**已实测可达的 Discourse** 作载体，并且 P0 的验收对象从"一个新源"改成"**6 个 emitter 全部迁移**"：

| 为什么 Discourse 能替代 | 实测依据（2026-09-29） |
|---|---|
| 官方 key-less JSON，无登录、无验证码 | `GET https://users.rust-lang.org/latest.json?order=created` → `HTTP 200`，42 KB |
| 楼层天然对应 sections | `GET /t/{id}.json` → `post_stream.posts[]`，每条带 `post_number`（楼层号）、`username`、`created_at`、`cooked` |
| 无 HTML 皮肤/编码方差 | 对比 Discuz 系：`52pojie.cn` 需要"阅读权限高于 10"（登录墙）、GBK 编码、按皮肤变模板 |
| 已是**多站点族**（`DiscourseSiteConfig.base_url`） | 加一个新的噪声论坛 = 改 config，正好验证 §3.2 注册表"加一个源只需 2 处手工同步" |

**P0 迁移清单（6 个 emitter，一个都不能漏）**：

| 文件 | 现状 | 迁移后 |
|---|---|---|
| `scrapers/hackernews.py:113` | `--- Top Comments ---` + `[by]: text` | 主帖 → `Section(primary)`；每条评论 → `Section(community, locator="#<comment_id>")` |
| `scrapers/reddit.py:498` | `--- Top Comments ---` + `[author (N pts)]: body` | 同上，`locator` 取评论 `id`，`metadata` 保留 score |
| `scrapers/twitter.py:247-266` | `append_discussion_content` 拼 `--- Top Comments ---` | 每条 reply → `Section(community, locator="@handle/status_id")`；函数签名保留但改为写 `sections` |
| `scrapers/discourse.py:147-155` | `【楼层讨论】` | `posts[0]` → `Section(primary)`；`posts[1:]` → `Section(community, locator="#<post_number>")` |
| `scrapers/bilibili.py:101-117` | `【视频字幕节选】` + `【评论区 Top】` | 字幕 → `Section(primary, provenance="transcript")`；评论 → `Section(community, locator="#<rpid>")` |
| `scrapers/v2ex.py:105-112` | `【回复精选】` | 每条回复 → `Section(community, locator="#<reply_id>")` |

`【评论区】` 这个标记在 `src/` 里**没有任何 emitter**（只有 `sections.py:36` 的字典条目），迁移后随 legacy 路径一起保留即可，不必为它写代码。

**第 7 处必改点（不是 scraper，但漏了会改坏上游日报）**：`processing/content.py:split_content` 靠英文标记剔除评论，服务 `ai/analyzer.py:109` 与 `ai/prompting/enrichment.py:155`。emitter 迁移后 `content` 里不再有标记，`split_content` 会把评论并进 `main`，日报摘要因此**开始引用陌生人评论**。所以同批必须加 `split_item_content(item: ContentItem) -> ContentParts`：有 `sections` 时按 tier 切，无 `sections` 时回落到现有标记路径；两个调用点改传 `item`。`split_content` 本身保留（legacy 与消融 A 档要用）。详见 §9.13。

**P0 的验收测试（关键，直接对应 §1.1 的现役泄漏）**：
1. 一条 HN 链接帖（`story.text` 为空、3 条 `kids`）：`claimable` **必须为空**，评论文本不得被 `claim_fts` 命中，不得使 `independent_sources` 增加。这是**先写失败的测试**——它在当前 `main` 上就是红的。
2. 一条含 5 个楼层的 Discourse 主题：楼层文本不得进 `claimable`，且每个楼层 section 的 `locator` 等于 `#<post_number>`。
3. 迁移后 `src/scrapers/` 里不得再出现任何分层标记字面量（用一条 `grep` 型守护测试钉住，防止第 7 个 emitter 悄悄长出来）。

`tests/test_twitter.py:589` 现在断言 `"--- Top Comments ---" in item.content` —— 它钉的是泄漏行为，**必须随迁移一起改**，改成断言 reply 落在 `tier="community"` 的 section 里。

### 3.5 P0 不做
不做 OCR/VLM（P3）、不做 trust 打分（P1 只做源先验这一维，作为常量进库）、不动上游日报管线的**输出语义**（`processing/content.py:split_content` 保留为 legacy 路径，日报侧改走 §3.4 的 `split_item_content`；`main` = 作者层、`comments` = 人群层的语义不变，唯一差别是 `comments` 里不再含 `--- Top Comments ---` 这行标记本身，而它本来就不该进摘要）、不改名、不改 config 格式、**不接任何需要登录/验证码/签名才能取到内容的源**（贴吧见 S2、小红书见 S1）。

---

## 4. P2：多轮共创调研文档

排在 P1 之前，因为 P1 的瓶颈是人工标注（可并行进行），而 P2 是纯工程且是"能力形态"的改变。

### 4.1 状态机与草稿工件
```
created → planning → investigating ⇄ awaiting_user → drafting → reported → closed
```
`awaiting_user` 是新增的可挂起态（SQLite 已支持跨进程恢复，`tests/test_research_session.py:189` 已验证同类行为）。

```sql
CREATE TABLE research_drafts (
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, revision INTEGER NOT NULL,
  origin TEXT NOT NULL,           -- render | user_edit | merge
  sections_json TEXT NOT NULL,    -- [{id,title,body,evidence_ids,locked,hash,stale}]
  created_at TEXT NOT NULL, UNIQUE(session_id, revision)
);
CREATE TABLE research_requests (   -- 系统向用户索取的输入
  id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT,
  kind TEXT NOT NULL,             -- clarify | confirm_claim | choose_scope | supply_source
  payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL, answered_at TEXT
);
```
`research_turns.role`（`session.py:62`）扩为 `user | report | assistant_question`。

草稿语义：
- `render` revision 由系统生成；`user_edit` revision 由用户改动生成，**永不被自动覆盖**。
- 每章节带 `hash`（body 的内容哈希）与 `locked`（用户显式锁定）。
- 依赖变了但章节被锁定/被用户改过 → 打 `stale=True`，等用户处理，而不是覆盖。这是 `RunStore.invalidate_after`（`run_store.py:67`）的**非破坏性版本**。
- 引用编号稳定性：草稿固定 `citation_number → item_id` 映射，revision 重排编号但 item_id 不变；每个 revision 重跑 `citations.audit_report`（`citations.py:57`）并把摘要附在末尾（沿用 `session.py:849` 的做法）。

### 4.2 Planner 新动词
现有 `decompose/answer/revise/decide` 全部保留为底层能力，上面加一个顶层决策点：
```python
class Move(Union): AskUser(kind, question, options) | Rescope(add, drop) | Deepen(subq_ids) | Finalize()
async def next_move(self, ctx: MoveContext) -> Move
```
`ctx` 含：主问题、子问题树与状态、当前草稿 revision 的章节摘要、未回答的 `research_requests`、剩余预算。`next_move` 与现有调用共用 `planner_budget`（`session.py:368`），预算耗尽时确定性回退到 `Deepen(所有 open 子问题)`（等价于今天的 `investigate`），保证**无 LLM 也不会卡死**。

`AskUser` 的四种 kind 对应真实的缺口，不是为了交互而交互：
- `clarify`：问题歧义，子问题树分叉（如"市场调研"是指竞品还是价格）
- `confirm_claim`：某条 claim 的 verdict 是 `contested`，需要人裁决后才能进正文
- `choose_scope`：证据在两个源族里冲突，问用户要哪一面
- `supply_source`：语料里没有相关内容，请用户补链接/文件（这条是 G1 与 G2 的接合点：噪声源覆盖不到的时候，人补）

### 4.3 局部重算
依赖图：`draft section ← subquestion ← evidence item`（三者都已在库里：`research_subquestions.evidence_json`、`research_drafts.sections_json`）。
- `Rescope/Deepen` 只重算受影响子问题对应的章节，**不再 `reset_budget` + 全树重跑**（替换 `session.py:875/896` 的行为）。
- 一轮的返回不是整份报告，而是：
```python
@dataclass
class TurnResult:
    revision: int
    changed_sections: list[str]
    new_evidence: list[str]
    verdict_changes: list[tuple[str, str, str]]   # (claim_id, old, new)
    pending_request: ResearchRequest | None
    move: str
```
`followup` 保留为"连续推进直到 Finalize"的宏，向后兼容现有 MCP/Web 调用方。

### 4.4 入口改造
- MCP：`hz_research_step(session_id, user_message?)`（单次推进）、`hz_research_draft(session_id, revision?)`、`hz_research_edit(session_id, section_id, body)`、`hz_research_answer(session_id, request_id, answer)`。现有 `hz_research_start/followup/status/list`（`mcp/server.py:484-531`）保留。
- Web（`web/app.py`）：`POST /api/research/{id}/step`、`GET /api/research/{id}/draft`、`PATCH /api/research/{id}/draft/sections/{sid}`、`POST /api/research/{id}/requests/{rid}`。面板从"转圈等整报告"改成**左侧轮次时间线 + 中间可编辑章节 + 右侧待回答请求卡片**（`static/index.html:330` 的 `ask()` 重写）。
- 不做 SSE/WebSocket：请求-响应 + revision 轮询足够，流式是 YAGNI。

---

## 5. P1：可信度模型、独立性重定义与人评实测

### 5.1 条目可信度（可解释优先，不用黑盒）
```
trust(i) = σ( w0 + w1·prior(source_i) + w2·author_rank_i + w3·cross_support_i
                + w4·entity_checkable_i + w5·freshness_i
                + w6·provenance_factor_i − w7·template_score_i )
```
- `prior`：P0 已入库的 `SourceSpec.credibility_prior`
- `author_rank`：源侧可见的作者等级/认证（贴吧等级、论坛声望）；无则 0.5 中性
- `cross_support`：同 cluster 内跨 source_type 的条目数
- `entity_checkable`：文本中是否含可核验实体（人名/机构/数字/日期/URL），复用现有 `discriminating_terms`（`claims.py:322-370`）的 CJK n-gram + DF 打分
- `provenance_factor`：`author=1.0`、`transcript=0.95`、`ocr=confidence`、`legacy_marker=0.8`（老库分层可靠性较低，见 §9.1）。**这一维是 P3 的前置**：没有它，OCR 转写会拿到与作者原文同等的信任
- `template_score`：与同 cluster 其他条目的 5-gram Jaccard 均值（抓模板化营销文案）
- 权重先手工设定（可解释、可写进论文），再用 §5.3 的人评标注做逻辑回归校准，报 ROC 并选 θ

落库：`items` 加 `trust REAL`、`trust_features_json TEXT`（**特征必须留档**，否则"为什么这条被当证据"不可审计）。

### 5.2 独立性与两个门（分诊门 + 判定门）
**先说清 §1.6 查出的事实**：现在有两个不同的门，改动方式不同。
- **分诊门**（决定哪些 claim 值得花 LLM 预算）：`pending_grading:227-234` 的 `independent_sources >= grade_min_sources`
- **判定门**（决定 verdict 取值）：LLM `grade_claim:560-596`，无显式阈值

P1 两处都要改：

1. **指纹升级**：现有 SimHash over `title\ncontent`（`store.py:211`）→ 保留，另加 MinHash over 5-gram shingles（抗模板化）与图像 pHash 列（P3 才写值）。同簇判定：**任一指纹相同即同簇**（比现在更保守，防止模板文案互相算独立票）。
2. **独立性重定义**（替换 `recompute_independence:209-225` 的 `COUNT(DISTINCT COALESCE(cluster_id, item_id))`）：
   ```
   independent(claim) = |{ (source_type, publisher) }|  去重后计数
   publisher = items.author；为空时退到 locator 的作者段；仍为空 → 该条不计入独立源
   ```
   `publisher` 为空就不计票是**刻意保守**：宁可低估独立性，也不让匿名灌水互相当独立信源（`items.author` 可空，`store.py:55`）。
3. **聚合信任用 noisy-OR，不用求和**：`Σtrust ≥ θ` 有方法论缺陷 —— 无上限求和意味着足够多的低质源能把任何 claim 刷成 supported。改为
   ```
   T(claim) = 1 − Π_i (1 − trust_i · d_i)
   d_i = 1.0 若该条来自尚未出现的 source_type；0.5 若同 source_type 内的第 2+ 个 publisher
   ```
   `d_i` 的折扣是必要的：`source_families()`（`store.py:323-329`）就是 source_type 分组，若坚持"跨 source_type ≥2 才算 supported"，那么**只有单一论坛源（例如只有一个 Discourse 站点，或将来的贴吧）证据的结论永远无法 supported** —— 这与"广源取证"的目标直接冲突。论坛内多作者印证是弱证据，但不是零证据。
4. **verdict 规则**：
   - `supported`：`T ≥ θ_s` 且（跨 source_type ≥ 2 **或** 同 source_type 内 ≥3 个不同 publisher 且该源 `credibility_prior ≥ 0.4`）
   - `contested`：存在矛盾对
   - `unsupported`：其余
5. **分诊门换成 `T ≥ θ_triage`**（θ_triage < θ_s），并把**未评级 claim 在报告里显式列出条数**（现在 `session.py:859-862` 的过滤让它们彻底不可见）。
6. 新增 `claim_contradictions(claim_a, claim_b, relation, evidence_ids, created_by)`，由 `grade_claim` 阶段 LLM 显式产出并落库（现在 `contested` 只是一个取值，无证据可查）。
7. **`unsupported` 在报告里拆成两种呈现**：`证据不足`（没找到）vs `可信度不足`（找到了但 T 低）。这是对现有口径的细化，必须同步写进 `docs/evaluation.md`，并说明与 FEVER `not_enough_information` 的关系（该文件 `:48` 已声明"不合并"，别自相矛盾）。

### 5.3 人评协议（主证据；工具已就位，从未跑过）
- **分层维度必须是 `SourceSpec.kind`（5 类）× tier（2）= 10 层，不是 `source_type × tier`**。原先按 source_type 分层，14+ 源 × 2 = 28+ 层，n=80 摊下来每层约 3 条，per-class 指标不可报。按 kind 分层每层 8 条，才有意义。
- n = 80（在 50–100 区间取中）；指标只在 **verdict 三类**上报 per-class P/R，源层只用于抽样配额，不单独报数
- 流程：`scripts/eval_claims.py --export` 生成 `data/eval/claims_labels.json`（每条附 ≤6 条证据摘录，`eval_claims.py:34-87`）→ **第一轮盲评（隐藏 `machine_verdict`，避免锚定）** → 第二轮再看机器判定并记录分歧理由 → `--score` 出数
- 指标（全部复用现成实现，不造新指标）：per-class P/R/F1 + macro-F1 + accuracy + 混淆矩阵（`analysis/agreement.py:50`）、按独立源数分桶（`:92`）；若能拉到第二个标注者，加报 Cohen's κ（Cohen 1960 的标准口径，需在文档里注明出处）
- 产物：`data/eval/claims_labels.json`、`data/eval/claims_results.json`（两者当前都不存在）

### 5.4 消融（回答"每一维到底值多少"）
| 档 | 配置 | 指标 |
|---|---|---|
| A | 现状：marker 反解分层 + SimHash + 计数≥2 | macro-F1、独立源分布 |
| B | 结构化 sections（P0） | 同上 |
| C | + 源先验 `credibility_prior` | 同上 |
| D | + item trust 加权（noisy-OR）分诊与判定 | 同上 + ROC/θ |
| E | + MinHash/pHash 独立性 | 同上 + 灌水条目数下降 |

**⚠ A 档必须可复现，否则这张表就是"声称测但测不了"。** P0 合并后新写入走 sections 路径，A 档要求的是 marker 路径。所以 `scripts/eval_claims.py` 与 `eval_retrieval.py` 要加 `--tiering=marker|sections` 开关，`marker` 档复用保留下来的 `split_sections`（这也是 §3.1 不删它的原因）。

检索侧沿用 `docs/evaluation.md:15` 的 Recall@5/@10、P@5、nDCG@10、MRR（实现在 `corpus/metrics.py:21-63`），但 fixture 必须扩容：现在只有 19 条语料 / 5 个问题（`data/eval/corpus_fixture.json`），接入新的噪声论坛站点后要新建一份含噪声源的 fixture 与分级标注。**语义腿继续用 stub 就必须继续标注为 stub**（`eval_retrieval.py:57-79`、`evaluation.md:32,39`），不能混进真实结果。

### 5.5 多轮交互侧的评测（不能用 IR 指标）
- 人评：文档有用性（Likert 1-5）、任务完成率、"系统问的问题是否问在点上"
- 系统客观量（可测，不是主观）：**达成同一结论所需的轮次数**、每轮 LLM 调用数与端到端延迟、局部重算 vs 全树重跑的调用数比值
- 借协议不借指标：对话系统的 turns-to-resolution 口径

---

## 6. S1 / S2：源可达性探针（spike，独立于 P0/P1/P2）

两个中文 UGC 源都**先做判别实验、后写代码**。共同的合规红线：单账号、仅用户可见内容、限速、**不做多账号池、不做验证码打码、不做签名逆向分发**。

> **判别逻辑已做成可复跑的工具**（`src/sources/reachability.py` + `scripts/spike_sources.py`）：六种判定 `pass / list_only / blocked_captcha / signed_required / blocked_auth / error`，客户端注入所以离线可测（`tests/test_reachability_probe.py` 用的响应体就是从 §14.2 / §14.4 抄下来的）。**不加 `--online` 不发任何请求** —— 可达性是会被平台改动推翻的事实，必须能被重新检查，而不是停留在某次下午的手工 curl 记录里。

### 6.1 S1 小红书
三步，每步有明确通过判据，用维护者自己的账号、只抓其可见的内容：

1. 未登录 HTTP 取笔记详情页 → 判据：能否解析出正文（预期不通过，但要记录**实际**返回，不猜）
2. 登录态 cookie → 判据：同上 + 是否存在 x-s/x-t 类签名校验（观察 406/461/验证码）
3. Playwright 浏览器自动化（仓库已有 optional extra，`pyproject.toml:39-42`）→ 判据：能否稳定取到 10 条笔记的正文 + 图片，30 分钟内不触发风控

**只有第 3 步通过才排 P3（图文通路）。** 不通过则走降级路径：用户侧导出（浏览器扩展或手动 JSON），配一个 `hz_corpus_import` 入口，不硬啃反爬。

> **该降级路径已于 2026-09-30 实现**（`src/corpus/ingest.py` + 三个入口：`scripts/import_corpus.py`、`POST /api/import`、`hz_corpus_import`，见 §15.9）。所以 S1 判"不通过"不再等于"小红书永远进不来"，而是"由用户导出、按声明层级入库、来源方式记 `manual_export` 并打折"。

### 6.2 S2 百度贴吧（第 1 步已于 2026-09-29 做完，结论：不通过）

| 通路 | 实测结果 | 结论 |
|---|---|---|
| `GET https://tieba.baidu.com/f?kw=python&pn=0`（PC UA） | `HTTP 403`，2499 B，`<title>百度安全验证</title>`，含 `BIOC_OPTIONS` + `seccaptcha.baidu.com` | 不可达 |
| 同上，带首页预热 cookie + `Referer` | 预热请求**未下发任何 cookie**；仍 `HTTP 403` 安全验证 | 不可达 |
| 同上，Googlebot UA | `HTTP 403` 安全验证 | 不可达 |
| `GET https://tieba.baidu.com/mo/q/m?kw=python&lp=5024`（移动 UA） | `HTTP 403`，2508 B，安全验证 | 不可达 |
| `GET http://c.tieba.baidu.com/c/f/frs/page?kw=python&pn=1&rn=10` | `HTTP 200`，但 body = `{"error_msg":"未知错误","error_code":110001}` | 缺 `sign`；POST 表单、加 `_client_version` 同样 110001 → 属签名逆向，§11 排除 |
| `GET https://tieba.baidu.com/p/{tid}`（3 个从列表页取到的**真实** tid） | 三个全部 `HTTP 403` 安全验证 | **楼层不可达** |
| `GET https://tieba.baidu.com/f/good?kw=python` | `HTTP 200`，301 KB，无验证码 | **唯一可达通路** |

`/f/good` 能拿到什么（实测）：`data-field='{"id":10037465698,"author_name":"ashi876","author_nickname":"…","author_portrait":"tb.1.…"}'` 形式的结构化属性，108 个标题块、105 个作者、72 条摘要、36 个回复数；`&pn=50` 翻页可用（140 KB），中文吧名可用（`kw=机器学习` → 200，169 KB）。

**结论**：贴吧在无登录、不做签名逆向、不用浏览器自动化的前提下，只能取到**主题列表级**信息（tid / 标题 / 作者 / 摘要 / 回复数），**取不到楼层正文**。没有楼层就没有 community 层，因此：
- 贴吧**不能**作为 P0 分层改造的验证载体（已改用 Discourse，§3.4）；
- 贴吧作为"广源"仍有价值 —— 主题标题 + 作者 + 摘要本身就是 primary 层证据，且 `/f/good` 可达；
- S2 剩余两步（登录态 BDUSS cookie / Playwright 过安全验证）由维护者决定是否投入；**只有拿到楼层才排贴吧进 P 序列**，否则最多做一个"仅列表层"的降级源，且必须标 `time_basis` 与 `provenance`，不得声称覆盖贴吧讨论。

---

## 7. P3（条件执行）：图文 → 文本通路
依赖 S1 结论。新增与 `src/extractors/` 同构的 `src/media/` 层：本地 OCR（RapidOCR/PaddleOCR）+ VLM caption（走现有 AI provider 抽象，`models.py:129-140`）。产物落 `media_text` 表并以 Section 形式并入：
- OCR 出的图上文字（往往就是作者的主张）→ `tier="primary", provenance="ocr", asserted=True, confidence=OCR 置信度`
- VLM 对画面的描述 → `tier="primary", provenance="vlm", asserted=False` → **只能当线索（lead），不得进 claim 抽取**（`claims.py:443` 的输入过滤）

> **这条已升级为类型不变式**（`f9baa4f`）：`Section` 的校验器在 `provenance="vlm"` 时强制 `asserted=False`，而 `claimable_from_sections` 只收 `asserted` 的 primary 段。交给各 scraper 自己写那个参数，等于离一次忘写只差一行 —— 那时一句对画面的猜测会变成对世界的断言。OCR 相反：图上烧进去的字往往就是作者本人的主张，所以它保留 `asserted`、只按 `confidence` 打折，这个不对称才是 §7 的实际内容。此改动**不依赖 S1 结论**，所以不必等那个被挡住的源变可达。

必须先测 OCR 错误率对 verdict 的影响，**在此之前不得声称"图文源已可用"**。

---

## 8. 图表清单（汇报与论文都要用）

**状态（2026-09-30）**：1、2、3 号是结构性图，已经画在 [`docs/architecture.md`](../../architecture.md)（PR #6），图里的每个状态名 / 动词名 / 表名 / 判定名 / 阶梯步都由 `tests/test_docs_match_code.py` 与代码双向对齐 —— 改了代码不画图会红，图里写了代码没有的名字也会红。**渲染未验证**：本机没有可用的 mermaid CLI，被验证的只有"内容与代码一致"。6、7 号也已画成 SVG 并嵌进 `docs/evaluation.md`（`docs/assets/flood-independence.svg`、`docs/assets/recompute-cost.svg`，由 `scripts/render_eval_charts.py` 从 `data/eval/multiturn_results.json` 用**标准库**渲染 —— 见 §15.13）。4、5 号没有人评标注就是画不出来；7 号的延迟那一列至今没有数据（本仓库禁止挂钟断言，见 §15.4），所以图上只有调用数那半。

1. 架构图：源接入 → sections → corpus → trust → claims → 会话 → 草稿（§2 那张的正式版）✅ 已画
2. 会话状态机图，突出 `awaiting_user` 与四种 `AskUser` ✅ 已画
3. 一轮交互的时序图：`step → next_move → 局部重算 → TurnResult → draft revision` ✅ 已画（轮次与调用数取自 §15.4）
4. 消融柱状图：A–E 各档的 macro-F1 与 per-class P/R（卡人评）
5. `T(claim)` vs 人评 verdict 的 ROC，标出选定的 θ_s 与 θ_triage（卡人评）
6. `independent_sources` 分布直方图：灌水修复前后对比（旧 1/2/3/4/5 vs 新恒为 1，已有数字）
7. 局部重算 vs 全树重跑：LLM 调用数 / 延迟随轮次增长的折线（调用数比值 1.5 / 2.5 / 4.5 已测；延迟无数据）

---

## 9. 逻辑接缝自查（这些是最容易被挑出来的矛盾点）
1. **老库 tier 可信度低于新库**：backfill 走 marker 路径并标 `provenance="legacy_marker"`，且 §5.1 给它 `provenance_factor=0.8` 的折扣；引用老数据时报告要标出来。不得表述为"分层问题已彻底修复"。
2. **OCR 文本能不能当作者断言**：`asserted` 字段区分，VLM 描述明确排除在 claim 抽取之外；OCR 走 `confidence` 折扣；P3 前不声称图文可用。
3. **局部重算 vs 引用编号稳定性**：编号可重排，item_id 映射固定，每 revision 重跑 `audit_report`。
4. **`awaiting_user` 会不会变成死会话**：`hz_research_list` 与面板要能看出"在等你回答"，请求可 `skipped`。
5. **trust 加权会不会把"没证据"和"证据差"混为一谈**：§5.2 第 7 点已拆成两种呈现，并同步 `docs/evaluation.md`。
6. **无 LLM 时必须仍可跑通**：`next_move` 预算耗尽回退到确定性 `Deepen`，沿用 `_decompose:409-410` 已有的"单节点树胜过没有会话"思路。
7. **注册表驱动 enum 会不会读不出老数据**：enum 保持闭合、值仍是 str，入库数据不变；迁移测试覆盖。
8. **循环导入**：`SourceSpec` 纯元数据留 `models.py`，scraper 绑定放 `src/sources/registry.py`（§3.2）。
9. **"注册表消灭手工同步"是否被夸大**：已降级为 5 → 2 处，config 字段与 scraper 实现仍需手工（§3.2 表）。
10. **单平台研究会不会永远无法 supported**：noisy-OR + 同 source_type 内多 publisher 的折扣路径解决（§5.2 第 3-4 点）。
11. **`time_basis=unknown` 会不会反复烧预算**：`id` 幂等入库 + 分析阶段跳过已见 id（§3.1）。
12. **消融 A 档在 P0 之后还能不能测**：`--tiering=marker|sections` 开关（§5.4）。
13. **迁移会不会改变上游日报的输出**：不会，而且必须测。`processing/content.py:split_content` 保留，`ContentItem.content` 仍由 sections 拼接而成（拼接产物里**不再**含标记字面量），所以 `split_content` 对迁移后的条目会走 `COMMENTS_MARKER not in content` 分支、把全文当 `main`。这正是日报侧想要的效果吗？—— 不是：日报侧原本靠标记剔除评论。所以 **`split_content` 必须同步改为读 `item.sections`**，否则日报会开始把评论当正文摘要。这是 §3.5"不动日报行为"的真正含义：行为不变，实现换轨。守护测试要覆盖两侧（claim 侧与日报侧）对同一条 Reddit 条目的一致性。
14. **现有测试是否钉住了错误行为**：`tests/test_twitter.py:589` 断言 `"--- Top Comments ---" in item.content`，钉的正是 §1.1 的泄漏。迁移必须**同时改这条断言**，否则 P0 会在"测试全绿"的假象下把泄漏固化。
15. **贴吧/小红书在探针通过前不写抓取代码**：S1/S2 的结论是"能不能取到"，不是"要不要做"。在拿到可达通路之前，任何贴吧/小红书 scraper 代码都是猜的（§14）。

---

## 10. 交付顺序与验收

| 期 | 内容 | 验收（可执行判据） |
|---|---|---|
| **P0** | 结构化 sections + **6 个 emitter 全量迁移** + 两层注册表 + throttle/auth | ① HN 链接帖（`story.text` 空、3 条 `kids`）的 `claimable` 为空，评论不被 `claim_fts` 命中、不抬高 `independent_sources` —— **这条测试在当前 `main` 上必须先红后绿**；② Discourse 楼层落 `tier="community"`，`locator == "#<post_number>"`；③ `src/scrapers/` 内不再出现任何分层标记字面量（`【…】` 与 `--- Top Comments ---` 都不许），守护测试钉住；④ 手工同步点 5 → 2（守护测试）；⑤ 老库迁移不丢数据且 `locator`/`time_basis`/`sections_json` 回填正确；⑥ `uv run pytest -q` 全绿（基线 697 collected，2026-09-29 实测；P0 会新增测试，数字只增不减） |
| **P2** | 状态机 + 草稿工件 + `next_move` + 局部重算 + Web 逐轮 UI | 一次调研 ≥3 轮且至少一轮系统主动 `AskUser`；用户改过的章节在后续重算中不被覆盖（`stale` 标记）；每轮 LLM 调用数明显低于全树重跑（给实测数字） |
| **P1** | trust 模型 + noisy-OR 独立性 + 矛盾对 + 人评 80 条 + 消融 A–E | `data/eval/claims_labels.json` 有 ≥80 条真实标注；报出 macro-F1 / κ（若有第二标注者）/ ROC 与 θ_s、θ_triage；消融表 A–E 有数字且 A 档可复现 |
| **S1** | 小红书探针 | 三步判据的实测结论（通过/不通过 + 证据），决定 P3 是否启动 |
| **S2** | 贴吧探针 | 第 1 步已完成（§6.2，结论：楼层不可达）；剩余两步（登录态 cookie / Playwright）由维护者决定是否投入，**拿到楼层之前贴吧不进 P 序列** |
| **P3** | 图文通路（条件执行） | OCR 错误率实测 + `asserted` 过滤生效的测试 |

P1 的人工标注由维护者完成，可与 P2 的工程并行（导出工具已就位，这是唯一的人工阻塞项）。

---

## 11. 明确不做（YAGNI）
多账号池 / 反爬对抗 / 验证码打码 / 签名逆向（含贴吧客户端 API 的 `sign`）；SSE 或 WebSocket 流式；向量检索默认开启（保持 optional，`models.py:601`）；改名 `veriscope`（独立决定，不塞进这三期）；上游日报管线的**输出格式与阶段顺序**改造（§9.13 的 `split_item_content` 只是把同一行为换到 sections 上实现，输出不变）；`data/config.json` 格式变更；把 `items.url`/`published_at` 改成可空列；在 S1/S2 探针通过之前写任何小红书/贴吧抓取代码；仓库 issue 开关与 CI 是否在本平台执行（只能网页侧确认，与本设计无关）。

---

## 12. 下一步
三期的实现计划均已单独成文：

- **P0**（实现**前**写的计划）：`docs/superpowers/plans/2026-09-29-p0-structured-sections-and-source-registry.md`
- **P2**（实现**后**补写，见 §15）：`docs/superpowers/plans/2026-09-29-p2-multiturn-research-drafts.md`
- **P1**（实现**后**补写，见 §15）：`docs/superpowers/plans/2026-09-29-p1-trust-independence-and-gates.md`

S1 / S2 不出计划：它们是可达性探针，产出是「通过 / 不通过 + 证据」，不是代码分期。P3 的条件是 S1 通过，届时再写。

---

## 13. 审计修订记录（v1 → v2，2026-09-29）
对照代码逐条核实后修正的问题。**前三条是 v1 写错的现状事实**，后面是会在实现时崩的设计缺陷。

| # | v1 的说法 | 核实结果 | v2 的处理 |
|---|---|---|---|
| 1 | "14 个 scraper 各自 `if published_at < since`" | 实为 **10 个文件、12 处**；v2ex/openbb/gdelt/google_news 没有 | §1.2 改为准确计数并列出源名 |
| 2 | "MCP `VALID_SOURCES` 是第 6 个手工同步点" | **错**，`horizon_adapter.py:20` 已是 `frozenset(SOURCE_REGISTRY)`，本来就派生；web CSS 有 `.p-default` 回退属装饰性 | §1.3 改为"5 处硬 + 1 处装饰"，并把 MCP 从同步点里划掉 |
| 3 | "`grade_min_sources=2` 是 verdict 的纯计数门" | **错**，它是**分诊门**（`pending_grading:227-234` 决定哪些 claim 花 LLM 预算），verdict 由 LLM 给；低于阈值的 claim 永远 `verdict IS NULL` 且在报告里不可见 | §1.6 重写；§5.2 拆成"分诊门 + 判定门"两处分别改，并要求报告显式列出未评级条数 |
| 4 | `SourceSpec` 持有 `scraper: type[BaseScraper]` | `models.py` 不 import scrapers，而 scrapers import models → **循环导入** | §3.2 拆两层：元数据留 `models.py`，绑定放新模块 `src/sources/registry.py` |
| 5 | "`SourcesConfig` 字段从注册表派生" | `models.py:614-630` 是每源一个强类型字段的静态模型；派生等于把 `config.json` 改成 dict → **破坏所有现有用户的配置** | §3.2 明确不改 config 格式；注册表职责收窄；§11 列入不做 |
| 6 | "`SourceType` 改注册表驱动" | `SourceType.` 在 src/ 出现 **37 次**，放宽成 `str` 是侵入式变更 | §3.2 保留闭合 enum + 守护测试；§9.7 说明老数据不受影响 |
| 7 | 承诺"手工同步 6 处 → 1 处" | 由 #5 #6 可知做不到 | §3.2 降级为 **5 → 2 处**，§9.9 自陈夸大风险 |
| 8 | `url`/`published_at` 改为 Optional | `items.url`/`published_at` 都是 `NOT NULL`（`store.py:54/56`），写入处 `str(item.url)`（`:217`）与 `.astimezone()`（`:219`）会在 None 上崩；还有 `idx_items_published` 与报告引用行（`session.py:845`） | §3.1 改为**写入侧回填**（url→locator、published_at→fetched_at + `time_basis='unknown'`），不动 schema 约束；逐点列出必改位置 |
| 9 | 去重键改为 `dedup_key`（新规范化） | 仓库已有**两套**去重实现（`orchestrator:63-89` 七元组、`history.py:_url_key`），再发明第三套只会加剧不一致 | §3.1 改为复用 `_deduplication_url_key`，并同步 `history.py` |
| 10 | `time_basis=unknown` 的条目照样入库 | 无时间门 → 每轮重抓；`INSERT OR IGNORE` 保证不重复入库，但会重复烧分析预算 | §3.1 补"分析/富化阶段跳过已见 id"；§9.11 |
| 11 | `supported` 需 `Σtrust ≥ θ` | 求和无上限 → 足够多的低质源可刷过阈值 | §5.2 改 **noisy-OR** `1 − Π(1 − trust_i·d_i)` |
| 12 | `supported` 需"跨 source_family ≥ 2" | `source_families()` 就是 source_type 分组（`store.py:323-329`）→ **只有贴吧证据的结论永远无法 supported**，与"广源取证"目标冲突 | §5.2 加同 source_type 内多 publisher 的折扣路径（`d_i=0.5`）与替代判据；§9.10 |
| 13 | 独立性用 `(source_family, publisher_id)` | `items.author` 可空（`store.py:55`），`(type, NULL)` 会把同族匿名条目折叠成一票 → 低估 | §5.2 规定 publisher 缺失时退到 locator 作者段，仍缺则**不计票**（刻意保守） |
| 14 | 人评按 `source_family × tier` 分层，n=80 | 14+ 源 × 2 = 28+ 层 → 每层约 3 条，per-class 指标不可报 | §5.3 改为按 `SourceSpec.kind`（5 类）× tier = 10 层，每层 8 条；指标只在 verdict 三类上报 |
| 15 | 消融 A 档 = "现状 marker 分层" | P0 合并后新写入走 sections 路径，A 档**无法复现** → 属"声称测但测不了" | §5.4 加 `--tiering=marker\|sections` 开关，这也是 §3.1 保留 `split_sections` 的原因 |
| 16 | trust 特征未含 provenance | P3 的 OCR 转写会拿到与作者原文同等信任 | §5.1 加 `provenance_factor`（author 1.0 / transcript 0.95 / ocr=confidence / legacy_marker 0.8） |
| 17 | "reddit 是全仓唯一的 429 处理" | `telegram.py:67-68` 也有一份，`ai/client.py:630` 另有 LLM 侧的 | §1.7 改为"两处重复实现"，§3.3 收拢两份 |
| 18 | 基线"697 通过"来自 3 天前的记忆 | 2026-09-29 本机复核：collect 697、全量跑 exit 0 | §10 标注实测日期 |

---

## 14. v2 → v3 修订记录与实测日志（2026-09-29）

v2 是"对照代码"的审计，v3 是"对照运行时与真实网络"的审计。两条 v2 的前提被实测推翻。

### 14.1 修订表

| # | v2 的说法 | 核实结果 | v3 的处理 |
|---|---|---|---|
| 19 | §1.1 把分层污染写成**未来风险**："任何新 scraper 只要不用这几个字符串…" | **现役 bug**：`reddit.py:498`、`hackernews.py:113`、`twitter.py:253` 都用英文 `--- Top Comments ---`，不在那 5 个中文标记里 → 三个源的评论此刻就在 `claimable` 列（§14.3 有可复现输出）。HN 默认 `enabled=True`（`models.py:245`） | §1.1 重写为"两套互不相交的词表"+ 泄漏链路逐跳；§3.4 改为 6 个 emitter 全量迁移；§10 验收①要求"先红后绿" |
| 20 | §1.1 隐含"只有一套分层实现" | 有两套：`corpus/sections.py`（中文标记，服务 claim/取证/研究）与 `processing/content.py:7,16-23`（英文标记，服务日报/富化，`ai/analyzer.py:109`、`ai/prompting/enrichment.py:155`） | §1.1 加对照表；§3.4 补第 7 处必改点 `split_item_content`；§9.13 说明为什么漏改会改坏日报 |
| 21 | §3.4"贴吧是无登录墙的结构化 HTML，楼层天然对应 sections" | **错**：`/f?kw=`、`/mo/q/m`、`/p/{真实 tid}` 全部 `HTTP 403` + `百度安全验证`；客户端 API `error_code 110001`（需 `sign`，属 §11 排除项）；仅 `/f/good?kw=` 可达且**无楼层** | §3.4 载体换成 Discourse（§14.4 实测可达且有 `post_number` 楼层）；贴吧降为 §6.2 的 S2 探针，第 1 步已判"不通过" |
| 22 | 未检查现有测试是否钉住错误行为 | `tests/test_twitter.py:589` 断言 `"--- Top Comments ---" in item.content`，正是把泄漏当预期 | §9.14 要求随迁移一起改；§3.4 明写 |
| 23 | §11 写"上游日报管线改造"不做 | 与 §9.13 的 `split_item_content` 表面冲突 | §11 收窄为"输出格式与阶段顺序不改造"，并说明换实现≠改输出 |
| 24 | v2 的 `Section` 无 `author` 字段 | 今天 `discourse.py:153`/`reddit.py:506` 把作者写进拼接串，而 `items_fts` 与面板都读 `content` → 迁移会丢归属 | §3.1 给 `Section` 补 `author`，并说明 `sections_to_content` 负责保留 `@user:` 前缀 |
| 25 | v2 让 `store.py` 在写入侧回填 `url`/`published_at` | 消费点有 12+ 处（`claims.py:449 .date()`、`summarizer.py:365`、`webhook.py:552` …），逐个改易漏 | §3.1 改为在 `model_validator(mode="after")` 归一化：构造后 `published_at` 永不为 `None`，`url` 的 6 处字面量 `"None"` 风险统一走新属性 `citation_url` |
| 26 | v2 的 `SCRAPER_BINDINGS: dict[str, type[BaseScraper]]` | RSS 需要第三个参数 `ExtractorRegistry`（`orchestrator.py:776-781`），Twitter 按 `mode` 二选一且 Playwright 版不接受 client（`:795-800`）→ 装不进"类"这个形状 | §3.2 改为 `dict[str, ScraperFactory]` + `simple(cls)` 适配器 |
| 27 | v2 要求 `history.py:_url_key` 与 DB 侧"口径必须一致" | 假目标：`_url_key` 只读日报 markdown 里的链接（输入恒为 URL），且 `tests/test_cross_source_duplicates.py:36-72` 已钉住七元组语义 | §3.1 改为只动调用点 `processing/tools.py:78`，并明写"不声称两套口径已统一" |
| 28 | v2 未考虑跨源合并会破坏 sections | `orchestrator.py:970-972` 合并重复项时把另一源的 `content` 直接拼进 `primary.content`；若 `content` 仍由 sections 派生，拼接就会与 sections 脱钩 | 计划 Task 4 要求合并时**同时**追加 `primary.sections`，让 `content` 始终由 sections 重算 |

### 14.2 贴吧可达性实测日志

探测机：本机（Windows，中国大陆网络出口），2026-09-29，`curl` + 真实 UA，全部 `-L` 跟随重定向。

```
[pc-forum-list]   https://tieba.baidu.com/f?kw=python&pn=0        HTTP=403 SIZE=2499  <title>百度安全验证</title>  BIOC/seccaptcha=1
[mobile-forum]    https://tieba.baidu.com/mo/q/m?kw=python&lp=5024 HTTP=403 SIZE=2508  <title>百度安全验证</title>  BIOC/seccaptcha=1
[client-api-frs]  http://c.tieba.baidu.com/c/f/frs/page?kw=python&pn=1&rn=10  HTTP=200 SIZE=115
                  body: {"error_msg":"未知错误","error_code":110001,"logid":"0453335642",...}
[pc-thread]       https://tieba.baidu.com/p/9000000000            HTTP=403 SIZE=2494  安全验证
[googlebot-ua]    https://tieba.baidu.com/f?kw=python             HTTP=403 SIZE=2494  安全验证
[cookie-warmup]   先 GET https://tieba.baidu.com/ 取 cookie（未下发任何 cookie），再带 -b/-e 请求 /f?kw=  HTTP=403 SIZE=2494  安全验证
[good-list]       https://tieba.baidu.com/f/good?kw=python        HTTP=200 SIZE=301741 无验证码
[good-list-pn]    https://tieba.baidu.com/f/good?kw=python&pn=50  HTTP=200 SIZE=140457
[good-list-cjk]   https://tieba.baidu.com/f/good?kw=机器学习       HTTP=200 SIZE=169569
[thread-from-good] /p/10717972520、/p/1250852756、/p/117245460（三个从 /f/good 取到的真实 tid）  全部 HTTP=403 安全验证
```

`/f/good` 页内可解析到的字段（实测计数）：`j_th_tit` 标题块 108、作者 105、`threadlist_abs` 摘要 72、`threadlist_rep_num` 回复数 36；结构化属性形如
`data-field='{"id":10037465698,"author_name":"ashi876","author_nickname":"…","author_portrait":"tb.1.e553a59d.WHqgsVcVMW…"}'`。
→ 有主题级元数据，**没有楼层正文**。

### 14.3 分层泄漏复现日志

```console
$ uv run python -c "from src.corpus.sections import claimable_text, community_text; \
  b='Post author writes: the migration broke prod.\n\n--- Top Comments ---\n\
[ stranger1 (42 pts)]: totally fake, never happened'; \
  print(repr(claimable_text(b))); print(repr(community_text(b)))"
'Post author writes: the migration broke prod.\n\n--- Top Comments ---\n[ stranger1 (42 pts)]: totally fake, never happened'
''
```

HN 链接帖（`story.text` 为空 → `hackernews.py:109-110` 不 append 作者段）：

```console
$ uv run python -c "from src.corpus.sections import claimable_text; \
  s='\n\n'.join(['','--- Top Comments ---','[alice]: the benchmark is rigged','[bob]: no it isnt']); \
  print(repr(claimable_text(s)))"
'--- Top Comments ---\n\n[alice]: the benchmark is rigged\n\n[bob]: no it isnt'
```

即：`content` 的 100% 是评论，`claimable` 的 100% 也是评论。

### 14.4 替代载体对比实测

| 候选 | 探测 | 结果 | 判定 |
|---|---|---|---|
| Discourse（users.rust-lang.org） | `GET /latest.json?order=created` | `HTTP 200`，42 KB，含 `topic_list.topics[]` | ✅ |
| Discourse 楼层 | `GET /t/52690.json` | `HTTP 200`，`post_stream.posts[]` 带 `post_number`、`username`、`created_at`、`cooked`、`posts_count:7` | ✅ 楼层结构完整 |
| V2EX 镜像 | `GET https://global.v2ex.co/api/topics/hot.json` | `HTTP 200`，55 KB | ✅（仓库已接入） |
| V2EX 主站 | `GET https://www.v2ex.com/api/topics/hot.json` | 连接超时 | ⚠ 主站不稳，镜像可用 |
| Discuz（52pojie） | `GET /thread-2126541-1-1.html`（1027 回复的帖子） | `HTTP 200` 但 body 是"提示信息"页：`抱歉，您需要【阅读权限】高于 10 才能阅读`；GBK 编码 | ❌ 登录+权限墙 |
| Discuz（pediy / chiphell / zol / autohome） | 列表页 | 均 `HTTP 200` | 未深入（52pojie 已证明 Discuz 系有登录墙与皮肤方差，P0 不引入） |
| linux.do | `GET /latest.json` | 连接失败（端口 443 超时） | ❌ 网络不可达 |

选 Discourse 的净理由：**官方 JSON、无登录、无验证码、有 `post_number` 楼层、无 HTML 皮肤与编码方差**，且它已经是多站点族（`DiscourseSiteConfig.base_url`），加一个新的噪声论坛只需改 config —— 正好验证 §3.2 注册表把"加一个源"降到 2 处手工同步的承诺。

---

## 15. 交付记录（v3 → v4，2026-09-29）

P0 / P2 / P1 三期已实现并推送。**本节只记三件事：验收实测值、设计与代码不符之处（含本 spec 自己写错的地方）、还剩什么。**

### 15.1 分期验收实测

| 期 | 分支 / PR | 验收结果 |
|---|---|---|
| **P0** | `feat/p0-structured-sections` / PR #4 | §10 的六条全绿。`test_tier_guard.py` 先在 `main` 上跑红（HN 链接帖的 `claimable` 里是 `[stranger_b]: no it isnt`），失败输出留在 commit `20d7ff2`。collected **697 → 817** |
| **P2** | `feat/p2-multiturn-drafts` / PR #5 | 三条全绿：3 轮且 `moves == [askuser, deepen, finalize]`；锁定节只标 `stale` 不覆盖；`Deepen(一条)` = **2** 次模型调用 vs 全树 **5** 次。collected **817 → 836** |
| **P1** | `feat/p1-trust-independence` / PR #6 | §10 的 P1 六条里工程五条全绿（第六见人评）。`--tiering marker` 与 `docs/evaluation.md` 表格逐格一致，A 档仍可复现。§5.5 的客观量与 §8 的图表 6/7 由 `scripts/eval_multiturn.py` 产出（见 §15.4）。P2 的三条验收另有 UI 级证据（§15.6）。collected **836 → 859 → 862 → 871 → 882**（后两轮是文档一致性与面板重绘的护栏）；本分支后续几轮（§15.7–§15.17）把数字推到 **972** |

### 15.2 设计在实现中被改写的地方

| # | spec 原设计 | 实际做法 | 原因 |
|---|---|---|---|
| 1 | §3.1 老库分层由 store 侧回填 | 改在 `ContentItem` 的 `model_validator(mode="after")` 归一化 | 构造后 `published_at` 永不为 `None`，`claims.py` 的 `.date()`、`store.py` 的 `.astimezone()` 与全部按时间排序的查询一行都不用改 |
| 2 | §3.3 共享管线用构造参数注入 | 注册表 `_wire()` 事后赋值 | 13 个 scraper 子类各自以不同形状转发 `super().__init__`，逐个改签名风险大且无行为收益 |
| 3 | §3.1 `time_basis` 缺省一律降级 | 只降级**调用方留了默认值**的 | 显式声明 `crawled` 的源知道一些我们不知道的事，不许覆盖 |
| 4 | §5.1 trust 模型放 `analysis/` | 放 `src/corpus/trust.py` | `corpus/store.py` 写入时就要它；`import analysis.trust` 会绕回 `corpus.store`，真循环（运行 import 撞出来的） |
| 5 | §5.2.2 同族折半 + 跨族宽度 | 加了 `deep_single` 分支（同族 ≥3 个发布者且族先验 ≥0.40 也算 supported） | 只要求「两个来源族」会让「只有论坛讨论过」永久不可判定，与本 fork 命题冲突 |
| 6 | §4.1 `followup()` 内部实现可变 | 保持签名与语义不变，改成 `step()` 的宏 | MCP / Web / scripts 的调用方零改动，P2 之前的研究测试一字未改仍绿 |
| 7 | §5.1 未规定 trust 的时钟来源 | `Corpus.add_items(..., now=)` 可注入（默认仍是当前时间） | 新鲜度读 `datetime.now()` 会让**同一内容在不同日期入库得到不同 trust**，文档里引用到第四位小数的数字会悄悄过期 |

### 15.3 本 spec 与 PR 正文写错的句子（已修）

- **PR #5 正文**：「草稿每章节存 `evidence_ids`」—— 字段一直存在，但渲染路径**从未填过**。两个可见缺陷：面板每节显示「0 条证据」（那一节实际引用三条），`MoveContext.contested_claims` 恒为空 → 决定下一个动词的模型从来看不到矛盾。`commit 5f3862c` 补齐（节标题 = 子问题文本，或节内 `###` 小标题 → 证据并集；一节对应多个分支时 `subquestion_id` 留 `None`，指向其中之一是谎报血缘）。
- **PR #5 正文**：`test_research_p2.py` 写的 20 条实际 19 条。
- **PR #5 / #6 正文都写过**「面板没在浏览器里点过，接线正确性只到端点级测试」。**去真点了一遍，当场发现缺陷**：点「开始研究」后报告渲染了，但轮次时间线、可编辑章节、待回答卡片全空 —— `ask()` 只手工刷 `#report`，没有向服务器要会话的其余状态，等于 P2 的界面在它唯一被使用的地方不可见；状态行还硬编码成 `· active`，而 `awaiting_user` / `reported` / `drafting` 正是 P2 新增的状态。修在 `905ab17`，护栏 `tests/test_web_panel.py::test_panel_repaints_round_state_after_it_changes_the_session`（文本级，证明不了渲染，但回到旧写法会红）。
- **§1.1**（v2）曾把分层污染写成「接新源之后才会发生」，实测是三个现役源正在泄漏 —— 已在 §14.1 记为 v2 的错误事实，此处不重复。

浏览器实测（固定语料 + 无 API key 的降级路径）逐条结果见 §15.6。

### 15.4 §5.5 的客观量与 §8 图表：哪些已经有数字

`scripts/eval_multiturn.py`（PR #6 附带）用一个**只计数、不联网**的替身规划器产出了 §5.5 里「系统客观量」那一半，以及 §8 图表清单中的 6、7 两号。**成本单位是 LLM 调用数，不是延迟** —— 本仓库禁止挂钟断言，所以延迟那一列至今没有数据，谁引用谁就要说清。

| §5.5 的客观量 | 实测 | 图表 |
|---|---|---|
| 局部重算 vs 全树重跑的调用数比值 | 深一条恒为 **2**；全树 = `1 + 分支数`；分支 2/4/8 的比值 **1.5 / 2.5 / 4.5**（线性） | §8 第 7 号 |
| 达成同一结论所需的轮次数 | **2 轮**到定稿，其中 1 轮是系统主动 `AskUser` 并 parked；多轮阶段共 8 次调用 | §8 第 3 号（时序图的数据） |
| 灌水前后的 `independent_sources` 分布 | 同文匿名转发 0/2/4/6/8 条 → 旧口径 **1/2/3/4/5**，新口径**恒为 1**；`T` 不变（0.7998），判定 `unsupported` | §8 第 6 号 |

三张表都在 `docs/evaluation.md`「多轮成本与灌水抵抗」一节，可复跑；`tests/test_eval_multiturn.py`（9 条）是护栏，包括「曲线必须平在 1」与「时钟钉住后 trust 可复现」。

**这一节顺带把一个不好看的设计后果量化了**：`T=0.7998` 已越过 `supported=0.55`，判定仍是 `unsupported`，因为 §5.2.2 还要求跨族宽度或同族 ≥3 个发布者 —— 也就是**一篇独立硬稿单独无法构成 supported**。这是有意为之，但它究竟对不对，属于下面 15.5 第一行人评要回答的问题，不属于工程能自己宣布的结论。

§8 的 1、2、3 号（结构性图）已于 2026-09-30 画在 `docs/architecture.md`，图元与代码由护栏双向对齐（§15.12）；第 4 号 macro-F1 与第 5 号 ROC 仍卡在人评；任何图的延迟轴都没有数据（见上）。

### 15.5 还剩什么

| 项 | 状态 | 谁能做 |
|---|---|---|
| 50–100 条声明 verdict 人评 → θ_s / θ_triage 校准 | 工具就位（`scripts/eval_claims.py --export/--score --tiering`），数据为零；`roc_thresholds()` 在没有标注时返回 `None` | **只有维护者** |
| S1 小红书探针 | 判别逻辑已做成工具（`scripts/spike_sources.py --source xiaohongshu --url … --online`，不加 `--online` 不发请求）。**第 1 步按定义不需要账号**，一条命令即可跑并落 `data/eval/reachability_results.json`；本轮我试着跑时出站请求被会话的权限层拦下，所以实际响应仍未记录（不许拿推测当结果）。第 2–3 步才需要登录态 cookie / Playwright | 第 1 步任何人（包括你）能跑；第 2–3 步只有维护者 |
| S2 贴吧探针第 2–3 步 | 第 1 步的结论现在**可断言**：`tests/test_reachability_probe.py::test_the_tieba_probe_reproduces_the_recorded_conclusion` 用 §14.2 抄下来的响应体跑出 `list_only` + 楼层不可达（平台改版时这条测试会先红）。拿到楼层之前不进 P 序列 | 维护者决定是否投入 |
| P3 图文 → 文本（OCR / VLM） | 条件执行，卡 S1 | 工程，等条件 |
| 改名 `veriscope` | 未执行。上游后期也自名 Periscope，「Periscope」分不开。要动包名 + 6 个 `periscope-*` 入口 + Docker 服务名 + 文档全量引用，留一版别名 | 维护者决定 |
| 仓库 issue 开关 / CI 是否在本平台执行 / `deploy-docs.yml` | 只能网页侧确认；`check_tasks_num: 0` 说明 GitHub 语法的 workflow 不被执行，所以**每个 PR 都需要人工过一遍** | 维护者 |

**因此当前项目里唯一「能力已实现、效果未主张」的一块仍是核查层。** 除它之外的所有主张都有上表的实测数字或先红后绿的测试撑着；引用本 spec 时，这一句限制要跟着走。

### 15.6 §10 P2 验收的 UI 级证据（浏览器实测，2026-09-30）

端点级测试证明不了「用户看得见」。用固定语料 + 无 API key（即降级路径，最苛刻的一档）把面板真跑了一遍，P2 的三条验收全部有观察证据：

| 动作 | 观察到的 DOM 状态 |
|---|---|
| 开始研究 | 3 节渲染；子问题节标「**3 条证据**」（overview / 引用节为 0，符合预期） |
| 推一轮 | toast「第 2 版 · 补了证据 · 改了 1 节 · 新增 0 条证据」，时间线 `#1 → #2` |
| 编辑一节并保存 | `PATCH` 落地 → 该节出现「已锁定」，正文变成用户写的文本 |
| 再推一轮 | 被改过的节**保留用户文本**并获得「上游已变 · 未覆盖」，未改动的节不会 → 这就是 §10 P2 第②条 |
| 状态行 | 从服务器取真状态（`reported`），不再是硬编码 |

这一轮同时产出 §15.3 里那条缺陷修复（`905ab17`）与它的文本护栏。抽取出的 `<script>` 过 `node --check`。

### 15.7 文档一致性核查（同一轮做的）

代码变了三轮而面向读者的文档还停在 P0 之前 —— 对一个把可审计当卖点的项目，这等于让下一个人去重新实现我们已经删掉的标记反解层。修了七个文件，并把「文档说的」变成可检查的断言（`tests/test_docs_match_code.py`，10 条）：

- `docs/configuration.md`（1100 行）此前**完全没写**本 fork 的四个配置块；现在逐字段列表，且每个默认值都跟 `model_fields` 对过。顺带发现严格性不一致：`Section` / `ContentItem` / 上游那几个块是 `extra="forbid"`，四个证据块**不是** —— 拼错的键会被静默忽略。这条写进文档而没有顺手改代码，因为收紧会让带杂键的既有用户配置直接加载失败，那是维护者的决定。
- MCP 工具数 README 写 22、`src/mcp/server.py` 实际注册 **26**；`src/mcp/README.md` 只列了 21 个，还写了一个不存在的 `hz_claims`。
- 相对链接脚本校过；唯一跨分支依赖是指向 `docs/superpowers/` 的链接，所以**合并顺序是先 #3 再 #4→#5→#6**。
- 护栏对旧提交跑过一次确认非空洞：在 `HEAD~2`/`HEAD~1` 上它们会报 `22 ≠ 26`、5 个未列工具、1 个虚构工具名、30 个未记录配置键。

三期完成后全量 **882 passed**（P0 基线 697 → P0 817 → P2 836 → P1 859 → lineage 862 → 多轮/灌水 harness 871 → 文档一致性护栏 881 → 面板重绘护栏 882）。

### 15.8 贡献者侧文档与「报告路由」修正（`7e08b24`，PR #6）

同一轮里还查出一类更严重的信息错配：**面向贡献者与报告者的三个文件全是上游原文**，等于把本 fork 的问题送去一个改不了它的人手里。

| 文件 | 原来的样子 | 现在 |
|---|---|---|
| `CONTRIBUTING.md` | 标题就是「Contributing to Horizon」，四步通用流程，**一条测试命令都没有**，还把信息源投稿指向上游站点 | 重写为本 fork 的硬约束（现有 8 条）（分层靠声明不靠字符串 / 人群文本只能当线索 / 加源只准动两处 / 禁挂钟断言 / `models.py` 的 import 边界 / 新能力要么进消融表要么别说有用 / 文档要有测试说实话）+ 环境命令 + **本平台没有 CI，验证是提交者的责任** + PR 正文要求（实测数字、先红后绿的失败输出、明确没做、偏差） |
| `SECURITY.md` | 唯一的披露渠道是上游作者邮箱 `thysrael@gmail.com` | 顶部加「本页两部分」，本 fork 的披露走仓库私信 / `[security]` issue，并列出**本 fork 特有的风险面**（跨运行持久化的 `corpus.db` 与 `llm_cache.db`、cookie provider 会点名哪条 cookie 坏了但不输出值、`on_demand_collection` 与查询扩展会中途联网花额度、反爬边界明确不做） |
| `CODE_OF_CONDUCT.md` | 执行联系邮箱同样是上游作者 | 保留准则原文，只加一句：本仓库的事件请走本仓库，发给上游不会得到处理 |
| `README.md` | 开发一节写「CI 见 `.github/workflows/tests.yml`：Linux 与 Windows 各跑一遍」—— 与 §15.5 承认的「本平台不执行 workflow」**自相矛盾** | 改成明确陈述：文件保留但不执行（`check_tasks_num: 0` 已实测），因此 PR 必须自带可复现命令与数字；另把上游安全/准则邮箱加进「属于上游渠道」的隔离清单，并补 `eval_multiturn.py` 与 `--tiering marker` 两条命令 |

`tests/test_docs_match_code.py` 从 10 条涨到 **13 条**，新增的三条正是防这件事回潮：`SECURITY.md` / `CODE_OF_CONDUCT.md` 必须在出现任何联系邮箱**之前**说明本 fork 与上游的区别；`CONTRIBUTING.md` 必须含测试命令、必须提 `test_tier_guard`、必须说明本平台的 CI 状况。对 `HEAD`（改动前）跑过一次：三条全部会红。

至此全量 **885 passed**（697 → … → 882 → 885）。

### 15.9 §6.1 的降级路径实现为一条真正的入口（`8ffccea`，PR #6）

spec 写下「用户侧导出 + 配一个 `hz_corpus_import` 入口」时，那个入口并不存在 —— 于是「不硬啃反爬」只是一句态度，不是一条通路。现在它是：`src/corpus/ingest.py` 一个函数，三个入口（`scripts/import_corpus.py`、`POST /api/import`、`hz_corpus_import`，MCP 工具数 26 → **27**）。

承重的是四条规则，不是接线：

1. **分层仍然是声明式的。** 导入的 `community` 文字在全文索引里查得到，但进不了 `claimable` / 声明蒸馏 / 独立信源计数（测试直接断言这个不对称）。完全没有分层信息的导出会落成 `legacy_marker` 一节，即按"层级是推断出来的"那个既有折扣处理。
2. **新增来源方式 `manual_export`，折扣 0.85** —— 刻意排在"自己抓的"(1.0) 与"反解出来的"(0.8) 之间：分层可以诚实声明，但 Periscope 没有亲眼见过那一页，导入买不到作者亲写的置信度。
3. **`source_type` 只能从已注册的 14 个族里选**，因为它决定源先验与独立性计数里的"族"。人不许在导入里给自己发明先验；人类可读的名字放 `metadata.source_label`。
4. **身份必填且确定性生成**（id 由 locator 派生），所以同一个文件导两次是 `items_new: 0`，不是两份副本。逐条报错且一次报全（源类型 / 身份 / 内容 / 体积），部分失败照旧入库；全失败时 Web 侧才是 400。

新增 20 条测试。文档护栏在加工具的那一刻就抓住了计数漂移 —— 它要求 README 与 `src/mcp/README.md` 同步写成 27 并列出新动词，这正是它被写出来的目的。

`3847c04` 又把负载样例做成**仓库文件** `data/export.example.json`（而不是文档里的一段代码），四条 item 分别覆盖：作者文本 + 两条陌生人回帖、`asserted=false` 的作者补充说明、transcript/OCR（OCR 自带 0.62 置信度，`provenance_factor` 就返回 0.62）、以及一条完全没有 primary 层的"只是线索"。**测试直接解析这个发布文件** —— 样例与实现脱节会立刻变红，这也是 `--dry-run` 不建库被断言的原因（检查格式不该冒着写入的风险）。

### 15.10 可达性判别从"一段散文"变成"可复跑的判定"（`e940330`，PR #6）

§14.2 / §14.4 的贴吧与 Discuz 结论是某个下午手工 curl 的记录。**可达性是会被平台改版推翻的事实**，用散文记录等于把它冻在昨天；真正需要它时（比如有人问"贴吧现在能抓了吗"）没人能复现。

`src/sources/reachability.py` 把这个判断做成一个接受注入客户端的纯函数，六种判定各有不同后果：

| 判定 | 含义 | 本项目的后果 |
|---|---|---|
| `pass` | 能解析出作者正文 | 可以进第 2 步（登录态） |
| `list_only` | 列表/标题可达、正文或楼层不可达 | **不能用来验证分层**（贴吧正是这条，所以 P0 换成 Discourse） |
| `blocked_captcha` | 回来的是验证中间页 | §11 排除打码，停 |
| `signed_required` | 端点应答但要求请求签名 | §11 排除签名逆向，停 —— 是停止标志，不是待解的谜题 |
| `blocked_auth` | 登录/权限墙（注意：**HTTP 200 也可能**是权限提示页，Discuz 就是这样） | 与验证码分开判，因为两者处置不同 |
| `error` | 传输失败或非预期状态 | 也是结果，不是异常 |

`scripts/spike_sources.py` **不加 `--online` 就拒绝发请求**（探针静默打第三方站点是没人要的副作用）。14 条测试全部走 `httpx.MockTransport`，响应体从 §14.2/§14.4 抄来，其中一条直接断言"贴吧 = `list_only` 且楼层不可达" —— 手工结论变成了回归。

至此全量 **925 passed**（… → 905 → 911 → 925）。

**一处自己的失误要记在这里**：`7e08b24` 的 commit message 只有一个标题行 —— 我把 `git push` 接在同一行的 heredoc 之后，正文被 shell 吃掉了。已推送的提交不做 `force-push` 改写，所以完整理由写在本节与 PR #6 的评论里。教训与本项目其他地方同源：**能复现的记录比事后修饰更值钱**。

### 15.11 P3 的一条前置不变式（`f9baa4f`，PR #6）

§7 要求「VLM 画面描述只能当线索、不得进声明抽取」，但原实现是靠各 scraper 自己写 `asserted=False` —— 离一次忘写只差一个关键字参数，而那次忘写会让一句对画面的猜测变成对世界的断言。现在 `Section` 的校验器在 `provenance="vlm"` 时强制 `asserted=False`，测试断言这个排除一路有效到 `claimable` 层（哪怕该段声明自己是 `primary`）。OCR 故意相反：图上烧进去的字往往就是作者本人的主张，所以保留 `asserted`、只按 `confidence` 打折 —— 这个不对称才是 §7 的实际内容。

**它不依赖 S1 结论**，所以不必等那个被挡住的源变可达就能立；P3 剩下的（OCR 错误率实测）仍然等 S1。全量 **934 passed**。

### 15.12 §8 的 1–3 号有产物了，并且图元被钉在代码上（`cace976`，PR #6）

`docs/architecture.md` 画了三张结构性图：广源→可用证据的通路（含三条入口：14 个已注册源族、用户导出、可达性探针，以及 `items`/`claim_fts`/簇/trust 四条落库支线）、研究会话状态机（`awaiting_user` 画成一等状态，并注明 `AskUser` 只在有模型时才可能被选出）、一轮交互的时序（调用数取自 §15.4 的实测，不是估的）。图下每张都带**代码落点**与一张 corpus.db 的表清单。

**为什么图也要护栏。** 结构性图比配置表更容易烂：它没有编译器可读，读的人又容易对图让步——图上写着的名字看起来就是事实。所以护栏是**双向**的：`Session` 的 7 个状态、`SubQuestion` 的 3 个、`Turn` 的 3 个角色、`Move` 的 4 个动词（用 `typing.get_args` 读，不把清单再抄一遍）、corpus.db 的 12 张普通表 + 2 张 FTS5 虚表、6 种可达性判定、5 步加宽阶梯、源族数量必须逐条出现在文档里；反向也必须成立——文档里反引号包起来的代码名，若在 `src/` + `scripts/` + 面板 JS 里找不到，就红。

**两处自我更正，都留在这里而不是悄悄改掉：**

1. 我一开始把图里的 `SOURCE_REGISTRY` 当成臆造的旧名"修"成了 `SCRAPER_BINDINGS`。**它是真的**：`src/models.py:106` 由 `SOURCE_SPECS` 派生它，`horizon_adapter.py` 用它校验源名。抓回这个错误的是护栏自己的自检用例（它断言"这个名字不在代码里"，于是立刻红）。现在图上写的是完整派生链 `SOURCE_SPECS → SOURCE_REGISTRY → SCRAPER_BINDINGS`。教训与 §14 那条同源：不要把"我一时没找到"写成"它不存在"。
2. 表清单的标题写着「11 张表」，底下数出 12 行，并且**整张 `items_fts` 被我漏了** —— 而 `items_fts`（含社区层，面板要召回）与 `claim_fts`（只索引作者亲写层）的**差别**正是分层真正生效的地方，漏了它等于把机制的核心画没了。现在按 12 + 2 分开标类型，并由测试对着 `CREATE TABLE` / `CREATE VIRTUAL TABLE` 扫描核数。

顺带发现 README 的文档表没有链 `docs/twitter-cookies.md`（从首屏根本走不到那篇），已补行并加一条"顶层 `docs/*.md` 必须被 README 链接"的护栏（`docs/index.md` 例外：它是 GitHub Pages 站点首页，本平台不构建）。

**这一节没有做的事**：mermaid 的**渲染效果**未验证（本机没有可用的 mermaid CLI，`npx` 也起不来），被验证的只有内容与代码一致；§8 的 4、5 号仍没有人评标注就画不出来，7 号的延迟轴仍无数据；6、7 号当时**还没画成图**，被记成的理由是 `pyproject.toml` 与 `uv.lock` 里没有绘图依赖；那个理由在 §15.13 被拆掉了（标准库就能写这两条折线），但「不顺手加依赖」这条约束仍然成立。图文件本身随 **PR #6** 进来，所以只合 #3 而不合 #6 时本节与 §8 的链接会悬空到 #6 落地为止。另有一条**给贡献者的** consequence 落在 `CONTRIBUTING.md` 第 9 条：改 `Session.status` / `Move` 动词 / corpus 表 / 六种判定 / 五步阶梯 / 源族数量就得同步改图，且那条规则明写"预期它会红"。全量 **955 passed**（934 之后 +21，见 §15.12 与 §15.13）。

### 15.13 6、7 号数据图：一句我自己写下的借口（`7f93d16`，PR #6）

§15.12 刚记完「6、7 号有数字但还没画成图，因为没有绘图依赖」。那句话的**结论对，理由是错的**：`pyproject.toml` / `uv.lock` 里没有 matplotlib / plotly 是真的，而"不加依赖"这条约束也该守（那是维护者的决定，不是我写文档时能顺手做的）—— 但两条折线不需要绘图库，标准库就能写出 SVG。把"我不想加依赖"写成"画不出来"，是同一类错误的第三个版本：给一个没做的事找一个看起来像客观限制的理由。所以这一节做的是把借口拆掉，而不是补一句解释。

**做了什么。** `scripts/render_eval_charts.py` 从 `data/eval/multiturn_results.json` 渲染两张图，嵌在 `docs/evaluation.md` 对应小节：`docs/assets/flood-independence.svg`（旧口径 1→5 涨、新口径恒为 1，`T` 全程不变）与 `docs/assets/recompute-cost.svg`（深一条恒 2、深全部 `1+分支数`，比值 1.5/2.5/4.5）。**提交进仓库的字节就是交付物**，所以护栏必须是可复算的：

| 护栏 | 断言 |
|---|---|
| 图与数据同源 | `--check` 与 `tests/test_eval_charts.py` 要求重渲染**逐字节相同**；数据变了而图没重画就红 |
| 数字不是手写进图的 | 图上每个点的数值标签逐个回查，必须等于 JSON 里的对应序列 |
| **说明句也是算出来的** | 「旧口径自 2 条转发起就够进判级门」= `min(reposts where grade_min_sources_would_have_admitted_it)`；比值串取自 `ratio` 字段；「没有延迟轴」写进副标题 —— 引用图的人不可能读出没有的东西 |
| 几何不能跑到画布外 | 折线点全部落在绘图框内；任何文字基线越出画布就红；轴落点与下边距一致 |
| 图是真图片 | `xml.etree` 解析通过、根节点带 `xmlns`、有 `<title>` 供 `<img>` 的无障碍名 |

**验证到了哪一层，说清楚。** 上面五条是**结构与可复算性**。像素层面只做到：Chromium 把它作为独立图片文档载入（无障碍树里读到了标题）—— 但**没有看过渲染出来的样子**，会话的浏览器没有可见 surface，截屏调用直接以 `viewport=0x0 / visibilityState=hidden` 失败。三张 mermaid 图仍是**渲染完全未验证**。引用这两张图时，"数字来自那份 JSON、几何在画布内"是可主张的，"排版好看"不是。

**4、5 号仍然没有图**，而且这不是同一个问题：它们要 50–100 条人评声明标注，数据为零。画一张空图比不画更坏 —— 空图的视觉语义是"测过了，平的"。

全量 **955 passed**（本轮 +10：9 条图像护栏 + 1 条「文档里嵌的图片文件必须存在」）。

**一处与清单字面不一致，写在这里而不是蒙过去**：§8 第 6 号原话是「`independent_sources` 分布**直方图**」，我画的是**折线**（x = 转发条数，两条序列 = 新旧口径）。理由是这份数据是"同一个量随灌水量变化"的曲线，直方图会把它读成"若干互不相关的桶"，而 §15.4 的表头本来就是按条数排的。如果维护者要的是字面那张直方图（比如按声明统计 `independent_sources` 的频次分布），那需要**真实语料跑一轮**才有的样本量，现在手上只有 harness 的 5 个点 —— 那是另一张图，不在这张里。

### 15.14 §11 排除的东西，在一份继承文档里被当成卖点是剩下的唯一未审区（`aa34f7b`，PR #6）

最后一批没被审过的文档（`scoring.md` / `profiles.md` / `extractors.md` / `twitter-cookies.md` / `horizon-hub-design.md`）里查出一处**正面冲突**：`docs/twitter-cookies.md` 第 4 节标题是「多账号轮询（防封策略）」，教读者导出多个账号的 cookie 让 Horizon 轮询，排障表里还有一句「或增加 cookie 数量」；而本仓库的 `SECURITY.md` 与本文 §11 写明**不做多账号池**。两者不可能同时为真，而被读者翻到的是那份文档。

**改法是把两件事拆开写，而不是删了了事**：代码**确实**会按匹配文件逐个开浏览器上下文、把账号列表切成等份、失败后重试一轮，且每个上下文的 UA 版本号按序号递增；本 fork 的**边界**是一个账号、只取该账号本来就能看到的内容，依赖账号池或指纹分散的改动不会被合并。中间那句「UA 按序号递增」是本仓库不该美化的一种形态（§11 的反爬对抗），所以它写在"代码会做什么"那一栏而不是藏在功能介绍里。也补了一条与账号数无关的真实故障：**留着的过期旧导出会被当成第二个上下文**，它预热失败后还分走一半账号，日志表现为 `Cookie #2 warm-up failed` + `page shows login gate`。

同一批里第二处是**类别错**而不是冲突：`docs/horizon-hub-design.md` 是上游**未实现的提案**（本仓库没有 marketplace / OAuth / 推荐服务的任何代码），README 却把它列成「架构与生态设计」。现在文档顶部与 README 那一行都写明未实现、没有对应代码，架构指向 `docs/architecture.md`。

两处各有一条护栏（`test_cookie_guide_states_the_collection_boundary`、`test_unimplemented_upstream_proposal_is_labelled_everywhere_it_is_linked`），且**在改动前都会红** —— 前者查「防封」与「多账号轮询」两个词不再出现、要求引用 `SECURITY.md`，后者查提案文档前 8 行内必须有"未实现"字样且 README 那一行要同时说"未实现"与"没有对应代码"。这一节的真正收获是方法上的：**继承下来的文档要按"它和本文 §11 是否一致"来审，不是按"它写得好不好"来审**。

另加一处同类的机械化：`docs/evaluation.md` 的 A–F 消融表（全项目被引用最多的一组数字）此前只靠眼睛与 `data/eval/results.json` 核，现在逐格对齐（四位指标按三位小数比），行首字母要对上配置名，且文档里给的复现命令必须含 results.json 记录的那个 `tiering` 档；一条 tamper 用例改坏两个格子、要求恰好报出两条定位到行列的错误，证明这个检查不是自我确认。全量 **960 passed**。

### 15.15 把符号存在性检查从一份文档铺到全部读者侧文档（`1cd1deb`，PR #6）

§15.12 那条"图里反引号的代码名必须在代码里存在"只守着一份文档，而它当初确实抓到了东西 —— 那就把它铺到 15 份面向读者与贡献者的文档上（README、`docs/` 各指南、MCP 指南、CONTRIBUTING、SECURITY、CHANGELOG）。

**收获要按比例说，别夸。** 全跑一遍的产出是**一处真缺陷 + 两条我从前没核过的断言**，不是"二十处谎言"：

- **真缺陷**：`docs/configuration.md` 让读者"在 `.env` 里设 `RESEND_API_KEY`"，而 `.env.example` 里**一个邮件变量都没有** —— 那条路要靠 `email.password_env`（默认 `EMAIL_PASSWORD`，由 `src/services/email.py` 用 `os.getenv` 读）选中，读者照文档做完仍然是空的。已把 `EMAIL_PASSWORD` / `RESEND_API_KEY` / `LWN_KEY` 补进 `.env.example`，并写明"哪个由配置指定"。
- **两条断言查下来不是虚构符号而是外部名字**：`past_7_days`（OSSInsight 的 period，文档注明"上游现在坏了"）与 `benzinga`（OpenBB 的 provider，由用户选）。它们进的是**写明理由的外部词表**，不是被忽略 —— 区别很重要：将来若 `ct0` 这类名字从代码里消失，豁免不会让它继续成立。

**两条规则避免了把网织成筛子**：① `${VAR}` 里的名字是**用户自己起的**，不算仓库符号（否则配置指南通通行）；② `UC...` 这类省略号是留给读者补全的**形状**，不是符号。

**反身性两次都以"响亮地失败"而不是"安静地空转"暴露出来**，值得记下来：本文件的豁免清单必须从词库里**减掉**，否则"auth_token 不在词库里"这句自检断言会被自己的允许清单喂饱；而证明检查会红的假名字要**运行时拼**，写死的假名字会随着这份测试文件一起进词库，自检当场变成假通过。这两处都是先写错、跑红、才改对的 —— 不是我预见到的。

**能力边界**（引用它时带上）：这张网只能证明"这个名字在仓库某处存在"，**不能**证明它被用在对的地方、值对、默认值对 —— 那些由逐字段与逐格的护栏负责（§15.7、§15.14）。`docs/superpowers/` 明确排除在外：计划文档必须在实现之前点名接口，那是计划的定义而不是错误；把它们纳入只会造成两种结局 —— 设计上就红，或者写一份大到算撒谎的词表。全量 **962 passed**。

### 15.16 §6.1 的"三个入口"有一句是半真的，现在补上了（`05181ea`，PR #6）

文档一直写导入通路有三个入口：MCP `hz_corpus_import`、CLI `scripts/import_corpus.py`、面板。前两个是真的；面板那边 `POST /api/import` **路由存在**，`index.html` 里**没有任何一处调用它** —— 也就是说，最需要这个通路的人（在浏览器里做研究、不敲命令的用户）恰好是唯一拿不到的那个。这是本轮做完成度审计时按"文档点名的每个入口是否真的可达"逐条查出来的，不是按测试覆盖率查出来的。

**新增的面板一节**叫「导入你导出的内容」：贴 JSON 或选文件 → **只校验**（看会收几条、每条为什么不收）→ **入库**（写完后刷新证据列表与统计，和其它动作的既有行为一致）。

**顺带暴露并修掉一个真 bug**：CLI 的 `--dry-run` 用的是**严格**解析器（`parse_import_payload`，遇第一个错就抛），而真实导入用的是**宽容**路径（逐条拒绝、继续收）。于是"30 条里有 1 个错字"的文件，预览报整体失败、实导入会收 29 条 —— 预览与结果不一致的预览比没有预览更坏，因为它教用户不信预览。**现在预览与写入共用 `prepare_import`**（`preview_payload` 就是那段 + 一份不写的报告），报告的键与写入完全一致（`items_new=0` 与 `dry_run=true` 成对出现，避免读者把预览的 0 读成"导入失败"）。这条**也**改掉了 §6.1 早先文档里没写的语义差别：CLI 现在把每条拒绝都列出来，而不是抛第一个就退。

**证据等级要说清楚。** 面板这类东西最难验：本轮能给的是三条 —— ① `node --check` 过（整个 `<script>` 语法正确，语法错会让整页空白而端点测试全绿）；② 一条测试把界面读取的响应字段与端点**实际返回**的字段做子集比较（防 `r.items_added` 这类永远为 undefined 的读法）；③ 端点级测试覆盖 `dry_run` 不写库、非 `dry_run` 写库、且校验与写库计数一致。**仍然没有**在浏览器里看过布局 —— 会话的浏览器没有可见 surface（`viewport=0x0`），截屏调用直接失败。所以"面板可用"这句不许写成"UI 已实测"；§15.6 那句 P2 的浏览器实测证据是**前一轮**拿到的，与本轮这一节无关。④ 新加了一条**对着 git 验过**的护栏：`tests/test_web_panel.py` 取服务真实注册的路由表，逐条问「面板里有没有代码调用它」；豁免只有两条（`/api/docs` 是 FastAPI 自带的 Swagger 页，`/api/collect/status` 与 `/api/stats` 是同一份状态留给 API 客户端），且每条必须写明理由并要求「面板确实没调它」，否则免单独自变成藏东西的地方。拿改动前的 `index.html`（`1cd1deb`）跑，它精确报出 `/api/import`；拿现在的 HEAD 跑，报 0 条 —— 这就是本轮那条 bug 的机械化版本。匹配故意宽松（路径每个字面片段都要在脚本里出现），因为 `${id}` 模板串让精确匹配做不到，除非去解析 JS；这份宽松也写在测试注释里而不是装作它是精确的调用图。全量 **969 passed**。

### 15.17 两件同族的事：文档承诺的命令行，与机器决定的红绿（`727a89e` + `7a905b6`，PR #6）

**(a) 命令行也成了检查对象。** §15.16 那条"路由必须真有人调"的做法搬到命令面上：代码片段/代码块里出现的每个 `--flag`，都要被它紧跟的那条命令的 argparse 接受（13 个目标、23 处标志，全对 —— 这半是预防）。同一个问题问到工具上时抓到了真东西：**`scripts/spike_sources.py` 在全部文档里只被提过一次，且没有一条可复制的命令**，README 写"一条命令即可跑"却不给命令 —— 而这个探针正是 S1 / P3 那条链的开关。对改动前的 README 量过：1 处提及、0 条可跑示例，所以新加的 `test_the_reachability_probe_is_documented_as_a_runnable_command` 会红。README 与 CONTRIBUTING 补了真实用法（`--source/--kw/--url/--online` 逐项对过脚本）。三次假警报也写进了测试注释，因为它们界定了这张网的形状：`--data-dir/--config` 声明在共享的 `src/_cli.py` 而不是入口模块；`--rm/--entrypoint` 属于 docker，且 `docker compose run --rm --entrypoint uv periscope-collect run periscope-wechat test --lang zh` 一行两个命令会串位（于是标志按"到下一个命令为止"分段归属）；**连散文一起扫会让 CHANGELOG 自己讨论 `--flag` 的那句话变成一条虚假承诺**，所以只扫代码片段与代码块。

**(b) 一次"红得不该"的教训。** 2026-10-01 这轮开跑，套件是 `21 failed, 951 passed`。不是回归：`src/url_security.py` 的 SSRF 校验**有意去解析主机名**（防的是把通知目标配成 `169.254.169.254` 这类元数据地址），而这台机器的出站解析被网络拦截，把 `example.com` 一类名字回答成 **RFC 2544 基准段 `198.18.0.x`** —— 不是 globally routable，于是**一个正确工作的安全检查**判红了 20 条 webhook 测试（另有一条只在整体顺序下红）。

**处理方式是加接缝，不是放宽检查**：`_default_resolver` 成为可注入的查找函数（生产默认行为不变；调用方可 `validate_public_http_url(url, resolver=...)`），`tests/conftest.py` 用 autouse fixture 钉住**默认查找**。没有删测试、没有改断言、没有放松校验；而需要测解析分支的测试仍然自己 patch `_resolve_hostname`（见 `tests/test_url_security.py`），**拒绝路径的覆盖因此没被这个钉固定住** —— 这正是"钉在默认查找而不是钉在校验"的理由。与 §15.2 里 `add_items(now=)`、以及仓库既有的 clock / sleeper / rng 约定同形：**凡"读外部世界"的地方都要有可注入的入口，否则测试的绿就依赖机器**。修后 972 passed，连跑两次一致。

**这台机器留下的可复用事实**：出站 DNS 会被拦截并把示例域名解析到 `198.18.0.0/15`。任何"真去解析主机名"的代码在这台机器上都会得到非公网地址 —— 这不是代码坏了，别去改校验。

**(c) 同一个提交把 §6.1 弄斜了第二次，记录在这里。** 我给 CLI 加 `--dry-run`、给面板加「只校验」，**却没动 `hz_corpus_import`** —— 于是"三个入口"又变成半真，而且这次的制造者正是上一段里批评别人漏入口的我。补平之后加了 `tests/test_mcp_parity.py`（3 条）钉形状：五个面（MCP 工具、service、`POST /api/import`、CLI、面板按钮）都要能预览；校验只准发生在 `src/corpus/ingest.py`（别处再实现一遍逐条判断等于第二套真相）；`tiering` **只在 MCP 与 CLI 暴露，面板故意不给** —— `marker` 是复现消融档位的开关，摆在用户上传证据的界面上等于邀请他用更弱的分层规则入库。这条**有意的不平等**写在测试注释里，不让它看起来像遗漏。

**另一条自揭更要留：** 那个 parity 检查的**第一版不咬人** —— 它拿整个函数体做字符串匹配，所以我把参数 `dry_run: bool = False` 删掉后，docstring 里残留的 `dry_run` 三个字仍让它通过。是我自己写的"证明它会红"那一步当场抓住的；改成只读**签名**（括号内参数列）后，同样的删除确实报红。教训写成规则：**护栏在被亲手弄红一次之前不算存在；而被文档字符串救活的检查，比没有检查更危险**，因为它会让人以为这条对称已经被守住了。实体见 PR #6 的 `41d23c4`，全量 **975 passed**。

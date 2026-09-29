# 设计：广源可信取证 + 多轮共创调研文档

- 日期：2026-09-29
- 状态：已与维护者对齐，待分期实现（P0 → P2 → P1，S1 探针并行）
- 范围：本仓库（`NLY22/periscope`，fork 自 `Thysrael/Horizon`）的两项能力扩展；不改动上游日报管线的行为

---

## 0. 目标的形式化

- **G1 广源取证**：把质量良莠不齐的源（贴吧、小红书等）纳入后，**每条进入证据链的内容都带可审计的可信度依据**，且"独立信源数"不会因为同源转载、模板化文案、评论区文本而虚高。判据不是"采到多少条"，而是"claim 的 verdict 在人评上站得住"。
- **G2 多轮共创**：调研文档从"一次性投影"变成**有版本、可被用户增量塑形、可局部重算的持久工件**；系统能在轮次之间**主动向用户索取缺失输入**，而不是每次都整树重跑再吐一整份报告。

两者的公共前置是一处地基缺陷（见 §1.1），所以先修地基。

---

## 1. 决定设计的现状接缝（均已核实到行号，以当前 `main` 为准）

### 1.1 静默地雷：分层判据是一个闭合的中文标记字典
`src/corpus/sections.py:33-39` 用 5 个精确字符串（`【评论区 Top】` 等）把 `content` 反解成 primary / community 两层。`split_sections:56-80` 的规则是"**第一个标记之前的文本一律算 primary**"。后果：任何新 scraper 只要不用这几个字符串拼接评论区，它的人群文本就会被判为作者亲写 → 进入 `claimable`（`store.py:59`，`claim_fts` 只索引这一列，`store.py:93-97`）→ 成为 claim 抽取的输入（`claims.py:443`）、被 `link_evidence` 关联、并抬高 `independent_sources`（`claims.py:209-225`）。**没有任何测试会失败。**

这决定了 P0 的核心不是"加一个源"，而是**把分层从字符串约定改成类型化字段 + 测试守护**。

### 1.2 `ContentItem` 的两个必填字段挡掉了噪声源
`src/models.py:119 url: HttpUrl`（必填）、`:122 published_at`（必填）。14 个 scraper 各自 `if published_at < since: return None`（如 `bilibili.py:73-74`、`reddit.py:141`）。App-only 内容、无稳定 canonical URL、热榜/推荐流这类非时间序源，条目会**静默消失**，只在日志里显示 "Found 0 items"（`orchestrator.py:857-896`）。跨源去重也完全建立在 URL 上（`orchestrator.py:63-89`、`processing/history.py:44-45`）。

### 1.3 加一个源要手工同步 6 处
`models.py:10-26`（闭合 enum）、`:37-52`（`SOURCE_REGISTRY`）、`:614-630`（`SourcesConfig`）、`orchestrator.py:19-33`（静态 import）、`:766-841`（14 段硬编码 `if`）、`web/static/index.html:76-82`（每源一个 CSS 类）。无一致性测试。

### 1.4 图文内容在语料里不存在
证据链是纯文本 FTS5 trigram（`store.py:69-73`）。全仓无 OCR/vision；图片只把 URL 塞进 metadata（`twitter_playwright.py:239`、`youtube.py:145`）。唯一的媒体→文本通路是 B 站 CC 字幕（`bilibili.py:120-140`），需登录的 AI 字幕被显式跳过。

### 1.5 多轮交互缺"停下来问用户"的接缝
- `Planner` 协议只有 `decompose/answer/revise`（`session.py:328-342`），`decide` 是可选的查询改写（`:537-558`）。**没有"向用户索取输入"这个动词。**
- `followup:870-897` 是原子整树重跑：`reset_budget()` + `investigate()` 遍历**所有** open 子问题（`:436`），返回整份报告。
- 报告是纯函数实时重渲（`render_report:758-850`），与 investigate 存的 report turn 快照（`:454`）可能漂移；**无草稿态、无版本、无 diff**。
- 会话状态机 `created → investigating → reported → closed`（`:71`），**没有"等用户"这个状态**。
- Web 面板 start/followup 阻塞到整份报告返回（`web/app.py:210/220`，`static/index.html:330` 全程转圈）。

### 1.6 核查层：能力已实现，效果零实测
`VERDICTS = {supported, contested, unsupported}`（`claims.py:43`）已落地；`grade_claim:560-596` 只看 400 字符摘录（`:633-640`），受 `grade_budget_per_run=8`、`grade_min_sources=2` 双重限制（`models.py:561-562`）。但 `data/eval/claims_labels.json` **不存在** —— 从未跑过一次真实人评；检索消融表的语义腿用的是 stub（`scripts/eval_retrieval.py:57-79`）。`docs/evaluation.md:40` 已自陈。

### 1.7 可以复用的资产（不要重造）
SQLite 会话状态 + `research_turns` + `research_actions`（`session.py:79-124`）已可中断恢复；自适应取证梯子 `_retrieval_ladder:459-471` 已有"记录每次尝试"的审计习惯；`citations.audit_report:57` 已能反解成品报告校验引用；`analysis/agreement.py:50 score_pairs` + `:92 independence_buckets` 已是标准分类评测口径；`RunStore.invalidate_after:67` 的"上游变→下游失效"思路正确，只是**破坏性**（直接删文件），草稿工件要非破坏性版本。

---

## 2. 架构总览

```
[源接入层]  SourceSpec 注册表（单一事实源）
             ├─ throttle（per-host 令牌桶）
             ├─ auth（cookie/token/签名 三种 provider）
             └─ scraper.fetch() → ContentItem{locator, sections[], time_basis}
                        ↓
[证据层]    corpus.db  items(sections 结构化) + source_profiles + media_text
             ├─ 分层：由 sections[].tier 决定（不再反解字符串）
             ├─ 指纹：text SimHash/MinHash + image pHash → cluster_id
             └─ 检索：FTS5(claimable) + RRF（+ 可选向量腿）
                        ↓
[可信度层]  trust(item) = σ(源先验, 作者等级, 交叉支持, 可核验实体, 新鲜度, −模板度)
             ├─ independent(claim) = 跨 source_family 且跨 publisher 的去重计数
             └─ verdict = f(Σtrust ≥ θ, 矛盾对存在性)      θ 由人评 ROC 定
                        ↓
[研究会话层] 状态机 created→planning→investigating⇄awaiting_user→drafting→reported→closed
             ├─ Planner.next_move() → AskUser | Rescope | Deepen | Finalize
             ├─ research_drafts（revision, 章节级 locked/hash）
             └─ 局部重算：section ← subquestion ← evidence 依赖图
                        ↓
[入口层]    MCP hz_research_step / draft / edit   +   Web 逐轮时间线与可编辑章节
```

---

## 3. P0：地基（结构化分层 + 源注册表 + 贴吧端到端）

**为什么先做**：不修 §1.1，接任何噪声源都会立刻污染证据层，而且污染是静默的。

### 3.1 数据模型
把 `src/corpus/sections.py:47` 的 `Section` dataclass 提升为 `models.py` 里的 Pydantic 模型并扩字段（`sections.py` 保留 `split_sections` 作为**老库 backfill 的 legacy 路径**，不再是新写入的判据）：

```python
class Section(BaseModel):
    tier: Literal["primary", "community"]
    text: str
    provenance: Literal["author", "transcript", "ocr", "vlm", "legacy_marker"] = "author"
    asserted: bool = True      # False = 不是作者的断言（如 VLM 对画面的描述）
    confidence: float | None = None
    locator: str | None = None # 楼层号/评论 id，用于精确引用
```

`ContentItem` 改动（`models.py:111-126`）：
- 新增 `locator: str`（稳定标识，可以是 URL，也可以是 `tieba:p/123#45`、`xhs:note:abc`）；`url: Optional[HttpUrl]` 降级为展示用。
- 跨源去重键从 `url` 改为 `dedup_key`（`locator` 规范化：去 query 中的追踪参数、统一协议、小写 host）。
- `published_at: Optional[datetime]` + 新增 `time_basis: Literal["published", "crawled", "unknown"]`。`since` 过滤**只对 `time_basis != "unknown"` 生效**；`unknown` 的条目照样入库，但在报告里标"时效不明"。这消灭 §1.2 的静默丢弃。
- 新增 `sections: List[Section]`；`content` 保留为 sections 的拼接（向后兼容），`claimable` 由 `sections` 中 `tier=="primary" and asserted` 计算，而非 marker 反解。

迁移：`store.py:38 SCHEMA_VERSION 2 → 3`，沿用现有 backfill 风格（`store.py:155-178`）：老行的 `sections` 由 `split_sections(content)` 生成并标 `provenance="legacy_marker"`。

### 3.2 源注册表（消灭 6 处手工同步）
```python
@dataclass(frozen=True)
class SourceSpec:
    key: str                                   # "tieba"
    kind: Literal["official", "forum", "ugc_social", "aggregator", "search_engine"]
    credibility_prior: float                   # 0..1，手工设定，P1 用人评校准
    login_required: bool
    editorial_gate: bool                       # 是否有编辑/审核门
    time_basis_default: str
    scraper: type[BaseScraper]
    rate_limit: RateLimit | None               # 每 host 的令牌桶参数
```
- `SOURCE_REGISTRY`、`SourcesConfig` 字段、MCP `VALID_SOURCES`（`mcp/horizon_adapter.py:20`）、Web 面板源列表、`docs/scrapers.md` 的源表**全部从注册表派生**。
- `SourceType` 闭合 enum 改为注册表驱动（值仍是 str，保证已入库数据可读）。
- `orchestrator.fetch_all_sources:749-843` 的 14 段 `if` 改成遍历注册表；并发从 `asyncio.gather` 全放开改为**按 `rate_limit` 分组**，无声明的源沿用旧行为。
- 新增守护测试 `tests/test_source_registry.py`：注册表是唯一事实源（enum/config/MCP/web 列表四者一致）、每个 spec 的 scraper 类存在且可实例化、每个 scraper 必须声明 sections 策略。

### 3.3 抓取基础设施（现在完全没有）
- `src/scrapers/throttle.py`：per-host 令牌桶 + 抖动 + `429/Retry-After` 处理（把 `reddit.py:539-542` 这个孤例提上来复用）。
- `src/scrapers/auth.py`：三种 provider —— env token、cookie 文件（含**过期检测与失效重试一次**）、无鉴权。`BaseScraper.__init__`（`base.py:14`）签名扩为可选注入 `throttle`/`auth`，**保持现有 14 个 scraper 不改也能跑**（默认值 = 现行为）。
- 共享 client 统一 `follow_redirects=True`（修 `orchestrator.py:763` 与 `:458` 不一致）。

### 3.4 第一个新源：百度贴吧
选它而不是小红书，因为它是**无登录墙的结构化 HTML，楼层天然对应 sections**，能在最短路径上端到端验证 §3.1 的分层改造：
- 主帖 → `Section(tier="primary", provenance="author")`
- 每个楼层 → `Section(tier="community", locator=f"#{floor}")`，**不再拼成一个字符串**
- `locator = https://tieba.baidu.com/p/{tid}#{floor}`；`time_basis="published"`（楼层有时间）
- `SourceSpec(kind="forum", credibility_prior=0.35, login_required=False, editorial_gate=False, rate_limit=1req/2s)`

**P0 的验收测试（关键，直接对应 §1.1 的地雷）**：一条含 20 个楼层的贴吧条目，其楼层文本**不得**出现在 `claimable` 列、不得被 `claim_fts` 索引、不得使 `independent_sources` 增加。

### 3.5 P0 不做
不做 OCR/VLM（P3）、不做 trust 打分（P1 只做源先验这一维，作为常量进库）、不动上游日报管线、不改名。

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
                + w4·entity_checkable_i + w5·freshness_i − w6·template_score_i )
```
- `prior`：P0 已入库的 `SourceSpec.credibility_prior`
- `author_rank`：源侧可见的作者等级/认证（贴吧等级、论坛声望）；无则 0.5 中性
- `cross_support`：同 cluster 内跨 source_family 的条目数
- `entity_checkable`：文本中是否含可核验实体（人名/机构/数字/日期/URL），复用现有 `discriminating_terms`（`claims.py:322-370`）的 CJK n-gram + DF 打分
- `template_score`：与同 cluster 其他条目的 5-gram Jaccard 均值（抓模板化营销文案）
- 权重先手工设定（可解释、可写进论文），再用 §5.3 的人评标注做逻辑回归校准，报 ROC 并选 θ

落库：`items` 加 `trust REAL`、`trust_features_json TEXT`（**特征必须留档**，否则"为什么这条被当证据"不可审计）。

### 5.2 独立性与 verdict
- 指纹升级：现有 SimHash over `title\ncontent`（`store.py:211`）→ 保留，另加 MinHash over 5-gram shingles（抗模板化）与图像 pHash 列（P3 才写值）。同簇判定：**任一指纹相同即同簇**（比现在更保守，防止模板文案互相算独立票）。
- `independent(claim)`（替换 `claims.py:209-225`）：`|{(source_family, publisher_id)}|`，且要求跨 `source_family ≥ 2`。
- verdict 规则（替换 `grade_min_sources=2` 这个纯计数门）：
  - `supported`：`Σ trust ≥ θ_s` 且跨 family ≥ 2
  - `contested`：存在矛盾对
  - `unsupported`：其余
- 新增 `claim_contradictions(claim_a, claim_b, relation, evidence_ids, created_by)`，由 `grade_claim` 阶段 LLM 显式产出并落库（现在 `contested` 只是一个取值，无证据可查）。
- **`unsupported` 要在报告里拆成两种呈现**：`证据不足`（没找到）vs `可信度不足`（找到了但 trust 低）。这是对现有口径的细化，必须同步写进 `docs/evaluation.md`，并说明与 FEVER `not_enough_information` 的关系（该文件 `:48` 已有相关声明，别自相矛盾）。

### 5.3 人评协议（主证据；工具已就位，从未跑过）
- 抽样：按 `source_family × tier` 分层随机，n = 80（在 50–100 区间取中）
- 流程：`scripts/eval_claims.py --export` 生成 `data/eval/claims_labels.json`（每条附 ≤6 条证据摘录，`eval_claims.py:34-87`）→ **第一轮盲评（隐藏 `machine_verdict`，避免锚定）** → 第二轮再看机器判定并记录分歧理由 → `--score` 出数
- 指标（全部复用现成实现，不造新指标）：per-class P/R/F1 + macro-F1 + accuracy + 混淆矩阵（`analysis/agreement.py:50`）、按独立源数分桶（`:92`）；若能拉到第二个标注者，加报 Cohen's κ（Cohen 1960 的标准口径，需在文档里注明出处）
- 产物：`data/eval/claims_labels.json`、`data/eval/claims_results.json`（两者当前都不存在）

### 5.4 消融（回答"每一维到底值多少"）
| 档 | 配置 | 指标 |
|---|---|---|
| A | 现状：marker 反解分层 + SimHash + 计数≥2 | macro-F1、独立源分布 |
| B | 结构化 sections（P0） | 同上 |
| C | + 源先验 `credibility_prior` | 同上 |
| D | + item trust 加权 verdict | 同上 + ROC/θ |
| E | + MinHash/pHash 独立性 | 同上 + 灌水条目数下降 |

检索侧沿用 `docs/evaluation.md:15` 的 Recall@5/@10、P@5、nDCG@10、MRR（实现在 `corpus/metrics.py:21-63`），但 fixture 必须扩容：现在只有 19 条语料 / 5 个问题（`data/eval/corpus_fixture.json`），加了贴吧后要新建一份含噪声源的 fixture 与分级标注。**语义腿继续用 stub 就必须继续标注为 stub**（`eval_retrieval.py:57-79`、`evaluation.md:32,39`），不能混进真实结果。

### 5.5 多轮交互侧的评测（不能用 IR 指标）
- 人评：文档有用性（Likert 1-5）、任务完成率、"系统问的问题是否问在点上"
- 系统客观量（可测，不是主观）：**达成同一结论所需的轮次数**、每轮 LLM 调用数与端到端延迟、局部重算 vs 全树重跑的调用数比值
- 借协议不借指标：对话系统的 turns-to-resolution 口径

---

## 6. S1：小红书可行性探针（spike，独立于 P0/P1/P2）

**在写任何小红书代码之前**做判别实验。三步，每步有明确通过判据，用维护者自己的账号、只抓其可见的内容：

1. 未登录 HTTP 取笔记详情页 → 判据：能否解析出正文（预期不通过，但要记录**实际**返回，不猜）
2. 登录态 cookie → 判据：同上 + 是否存在 x-s/x-t 类签名校验（观察 406/461/验证码）
3. Playwright 浏览器自动化（仓库已有 optional extra，`pyproject.toml:39-42`）→ 判据：能否稳定取到 10 条笔记的正文 + 图片，30 分钟内不触发风控

**只有第 3 步通过才排 P3（图文通路）。** 不通过则走降级路径：用户侧导出（浏览器扩展或手动 JSON），配一个 `hz_corpus_import` 入口，不硬啃反爬。

合规红线：单账号、仅用户可见内容、限速、**不做多账号池、不做验证码打码、不做签名逆向分发**。

---

## 7. P3（条件执行）：图文 → 文本通路
依赖 S1 结论。新增与 `src/extractors/` 同构的 `src/media/` 层：本地 OCR（RapidOCR/PaddleOCR）+ VLM caption（走现有 AI provider 抽象，`models.py:129-140`）。产物落 `media_text` 表并以 Section 形式并入：
- OCR 出的图上文字（往往就是作者的主张）→ `tier="primary", provenance="ocr", asserted=True, confidence=OCR 置信度`
- VLM 对画面的描述 → `tier="primary", provenance="vlm", asserted=False` → **只能当线索（lead），不得进 claim 抽取**（`claims.py:443` 的输入过滤）

必须先测 OCR 错误率对 verdict 的影响，**在此之前不得声称"图文源已可用"**。

---

## 8. 图表清单（汇报与论文都要用）
1. 架构图：源接入 → sections → corpus → trust → claims → 会话 → 草稿（§2 那张的正式版）
2. 会话状态机图，突出 `awaiting_user` 与四种 `AskUser`
3. 一轮交互的时序图：`step → next_move → 局部重算 → TurnResult → draft revision`
4. 消融柱状图：A–E 各档的 macro-F1 与 per-class P/R
5. trust 分数 vs 人评 verdict 的 ROC，标出选定的 θ
6. `independent_sources` 分布直方图：灌水修复前后对比
7. 局部重算 vs 全树重跑：LLM 调用数 / 延迟随轮次增长的折线

---

## 9. 逻辑接缝自查（这些是最容易被挑出来的矛盾点）
1. **老库 tier 可信度低于新库**：backfill 走 marker 路径并标 `provenance="legacy_marker"`，引用老数据时报告要标出来。不得表述为"分层问题已彻底修复"。
2. **OCR 文本能不能当作者断言**：`asserted` 字段区分，VLM 描述明确排除在 claim 抽取之外；P3 前不声称图文可用。
3. **局部重算 vs 引用编号稳定性**：编号可重排，item_id 映射固定，每 revision 重跑 `audit_report`。
4. **`awaiting_user` 会不会变成死会话**：`hz_research_list` 与面板要能看出"在等你回答"，请求可 `skipped`。
5. **trust 加权会不会把"没证据"和"证据差"混为一谈**：§5.2 已拆成两种呈现，并同步 `docs/evaluation.md`。
6. **无 LLM 时必须仍可跑通**：`next_move` 预算耗尽回退到确定性 `Deepen`，沿用 `_decompose:409-410` 已有的"单节点树胜过没有会话"思路。
7. **注册表驱动 enum 会不会读不出老数据**：值仍是 str，入库数据不变；迁移测试覆盖。

---

## 10. 交付顺序与验收

| 期 | 内容 | 验收（可执行判据） |
|---|---|---|
| **P0** | 结构化 sections + SourceSpec 注册表 + throttle/auth + 贴吧 | 贴吧入库；楼层文本不进 `claimable`/`claim_fts`/`independent_sources`（专门测试）；6 处手工同步减为 1 处（守护测试）；老库迁移不丢数据；`uv run pytest -q` 全绿（当前基线 697 通过） |
| **P2** | 状态机 + 草稿工件 + `next_move` + 局部重算 + Web 逐轮 UI | 一次调研 ≥3 轮且至少一轮系统主动 `AskUser`；用户改过的章节在后续重算中不被覆盖（`stale` 标记）；每轮 LLM 调用数明显低于全树重跑（给实测数字） |
| **P1** | trust 模型 + 独立性重定义 + 矛盾对 + 人评 80 条 + 消融 A–E | `data/eval/claims_labels.json` 有 ≥80 条真实标注；报出 macro-F1 / κ（若有第二标注者）/ ROC 与 θ；消融表有数字 |
| **S1** | 小红书探针 | 三步判据的实测结论（通过/不通过 + 证据），决定 P3 是否启动 |
| **P3** | 图文通路（条件执行） | OCR 错误率实测 + `asserted` 过滤生效的测试 |

P1 的人工标注由维护者完成，可与 P2 的工程并行（导出工具已就位，这是唯一的人工阻塞项）。

---

## 11. 明确不做（YAGNI）
多账号池 / 反爬对抗 / 验证码打码 / 签名逆向；SSE 或 WebSocket 流式；向量检索默认开启（保持 optional，`models.py:601`）；改名 `veriscope`（独立决定，不塞进这三期）；上游日报管线改造；仓库 issue 开关与 CI 是否在本平台执行（只能网页侧确认，与本设计无关）。

---

## 12. 下一步
本 spec 合并后，只为 **P0** 写实现计划（P1/P2 等 P0 落地后各自成计划，避免计划随代码漂移而过期）。

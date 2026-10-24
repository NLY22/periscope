> **当前阶段目标：** 优先扩展平台文字与深层讨论、交互查询数据和用户授权内容；以实际新增有效资料对照通用 Web Search API。目标范围及逐轮框架决策见[信息源目标记录](docs/source-system.md#当前阶段目标与框架讨论2026-10-19)。本阶段暂不推进多模态或报告生成能力建设。

> **当前代码结构（2026-10-19）：** 话题检索的正式实现已整理到 `periscope/`，
> 可用 `uv run periscope-topic --topic "人形机器人"` 运行；原有 `python topic.py`
> 和 `core/`、`collectors/` 导入继续兼容。`src/` 保留证据内核及既有日报、研究、
> Web/MCP 能力。结构与运行方式见 [代码组织说明](docs/code-organization.md)，
> 数据与历史演进见 [框架记录](docs/foundation.md)。

> **目录导航：** [文档入口](docs/README.md) · [代码与数据布局](docs/code-organization.md) · [采集链路与边界](docs/collection-pipeline.md) · [功能审计](docs/project-audit-2026-10-22.md)。网络采集、调度、爬虫分别归入 `periscope/collection/transport/`、`strategies/`、`crawling/`；历史产物统一放入 `.periscope-scratch/collect/` 与 `.periscope-scratch/reports/`。

> **信息源重构：** 统一登记 184 个来源配置，默认查询计划接入 25 个搜索入口和 84 个相关订阅源；支持按来源筛选、追加机构订阅、逐源状态及本轮 API 链接差集。登记数不代表在线可用数，详见[信息源体系与边界](docs/source-system.md)。

> **自主爬取：** 使用 `--crawl` 跟进本轮资料，或通过 `--crawl-seeds` 从站点入口发现详情与 PDF 附件，提取正文、保存出处和不同内容版本；支持关闭搜索 API 独立运行。见[爬虫使用与边界](docs/native-crawler.md)。

<div align="center">
<h1>🌅 Periscope</h1>

<p><strong>把广而杂的信息源，变成可核查的证据库</strong></p>

<p><sub>除了帮你读，还要说清每句话出自谁、有几家独立支撑、哪里互相矛盾。</sub></p>

<br>

[![License](https://img.shields.io/badge/license-MIT-green.svg?style=for-the-badge&logo=opensourceinitiative&logoColor=white)](LICENSE)
[![Tool uv](https://img.shields.io/badge/Tool-uv-4B275F?style=for-the-badge&logo=uv&logoColor=white)](https://github.com/astral-sh/uv)
[![Repo](https://img.shields.io/badge/Repo-AtomGit-263238?style=for-the-badge&logo=git&logoColor=white)](https://atomgit.com/NLY22/periscope)
![Status](https://img.shields.io/badge/status-Phase%20F1%E2%80%93F6%20已合并%20main-2ea44f?style=for-the-badge)
[![Commits](https://img.shields.io/badge/Commits-main-blue?style=for-the-badge&logo=git&logoColor=white)](https://atomgit.com/NLY22/periscope/commits/main)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=for-the-badge&logo=github&logoColor=white)](https://atomgit.com/NLY22/periscope/pulls)
![Sources Welcome](https://img.shields.io/badge/sources-welcome-f97316?style=for-the-badge&logo=rss&logoColor=white)

![Claude](https://img.shields.io/badge/Claude-f0daba?style=flat-square&logo=anthropic&logoColor=black)
![GPT](https://img.shields.io/badge/GPT-10A37F?style=flat-square&logo=openai&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-8E75B2?style=flat-square&logo=googlegemini&logoColor=white)
![DeepSeek](https://img.shields.io/badge/DeepSeek-0A6DC2?style=flat-square&logo=deepseek&logoColor=white)
![Doubao](https://img.shields.io/badge/Doubao-00D6C2?style=flat-square&logo=bytedance&logoColor=white)
![MiniMax](https://img.shields.io/badge/MiniMax-FF6F00?style=flat-square&logo=minimax&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-FFFFFF?style=flat-square&logo=Ollama&logoColor=black)

📡 围绕研究问题，联合网页搜索、原生平台、学术资料、机构订阅与历史语料，保留资料出处，沉淀为可检索、可核查的证据库。项目同时提供中英双语日报与研究会话功能。

[📋 配置指南](docs/configuration.md) · [🧩 Profile 定制](docs/profiles.md) · [🏗️ 架构图](docs/architecture.md) · [🔎 取证检索机制](docs/retrieval.md) · [📊 评测](docs/evaluation.md) · [🔧 重构记录](docs/REFACTOR_2026-10-18.md)

</div>

---

## 目录

- [它是什么](#它是什么)
- [现在真的能做什么](#现在真的能做什么)
- [运行一次会发生什么](#运行一次会发生什么)
- [三层能力](#三层能力)
- [三条支撑机制](#三条支撑机制)
- [三个入口](#三个入口)
- [支持的 AI 提供商](#支持的-ai-提供商)
- [支持的数据源](#支持的数据源)
- [投递渠道](#投递渠道)
- [快速开始](#快速开始)
- [配置](#配置)
- [项目结构](#项目结构)
- [开发与测试](#开发与测试)
- [安全与可靠性](#安全与可靠性)
- [文档](#文档)
- [项目状态](#项目状态)
- [贡献](#贡献)
- [许可证](#许可证)

## 它是什么

好的内容散落在订阅源、论坛与时间线里，而你的注意力是有限的。**Periscope 会替你收集、筛选、去重**，再把真正值得一读的内容连同背景与社区讨论一起送到你面前。

你的品味决定了你读什么，也决定了你希望从中得到什么。一篇新闻报道需要回答「为什么重要」，一篇工程深度长文需要回答「我能用上什么」。Periscope 的 **Profile（画像）** 为每一类内容定义各自的评分标准与输出形式，让简报读起来像是为你手工挑选的。

在此之上它回答第二个问题：**「这个说法站不站得住」**。它把采集到的一切沉淀进 **证据语料库**（`corpus.db`，跨运行、跨入口累积，不会次日即忘），把高分条目蒸馏成 **原子声明** 并逐条核查（独立信源计数 + 引用反解核验），再把这条证据链交给 **多轮共创研究**（长会话里逐轮取证、改范围、深化、定稿）。三者由 **Web 面板** 与 **MCP** 两个额外入口暴露，与 CLI 共享同一份数据库。

**本项目的立场一句话**：把来源放宽到论坛与流媒体，信息质量必然下降；全部工作在于让"降下去的质量"被分层、检索、计数与引用核验**重新补回来**，并用消融表说明补回了多少。

## 现在真的能做什么

这一节先说清边界，避免后面的内容被读成超出实际的承诺。

### ✅ 已交付，有测试钉住

| 能力 | 落在哪 |
|---|---|
| 14 类源并发采集（每个源独立成败），含 Bilibili CC 字幕层 | `src/scrapers/`、`src/sources/registry.py` |
| Profile 驱动的评分、理由、摘要、标签；主题去重与分类配额 | `src/ai/analyzer.py`、`src/orchestrator.py` |
| 中英双语日报，邮件 / Webhook（飞书·钉钉·Slack·Discord）/ 微信投递 | `src/ai/summarizer.py`、`src/services/` |
| 交互式配置向导 | `src/setup/wizard.py` |
| 证据语料库：SQLite + FTS5 + 手写 SimHash 聚簇，跨运行累积 | `src/corpus/store.py`、`src/corpus/simhash.py` |
| 用户导出入库通路（取不到的源不靠抓取） | `src/corpus/ingest.py`、`scripts/import_corpus.py` |
| 证据分层：scraper **声明**的类型化 `Section`，独立信源计数只认作者亲写层 | `src/models.py` 的 `Section`、`src/corpus/sections.py` |
| 条目可信度与独立性：可拆解的 trust 分数、noisy-OR 聚合、两道门 | `src/corpus/trust.py` |
| 多轮共创：逐轮动词 + 带 revision/locked/stale 的草稿工件 + 主动索取输入 | `src/research/moves.py`、`src/research/drafts.py` |
| 取证检索：查询扩展 + 向量路 + RRF 融合 | `src/corpus/retrieval.py` |
| 长会话研究：子问题树、崩溃续跑、缺证时自适应加宽 | `src/research/` |
| 报告骨架（背景调查 / 市场调研 / 方法探索）+ 引用反解核验 | `src/research/templates.py`、`src/corpus/citations.py` |
| Web 面板、MCP 服务（27 个工具） | `src/web/`、`src/mcp/` |
| 检索消融评测（Recall / Precision / nDCG / MRR） | `src/corpus/metrics.py`、`scripts/eval_retrieval.py` |

### ⚠️ 能力已实现，效果未主张

**声明核查层的「评级准确率」是整个项目唯一一块"做完了但不敢声称效果"的地方。**

判定一条声明是 supported / contested / unsupported，需要一个经过校准的阈值。这一步需要 **50–100 条人工标注** 才能报出 macro-F1、并按独立信源数分桶算一致率（工具已就位：`scripts/eval_claims.py --export/--score`），而**目前标注数据为零**。

因此在拿到标注之前：

- 阈值是**手工先验**，不是校准结果；
- 分诊门 `triage_min_trust` **默认为 0，即关闭**；
- 下面所有关于 trust 与阈值的描述，都**不得被读成已验证的准确率提升**。

这一点在 [CHANGELOG.md](CHANGELOG.md) 里逐条注明，因为它是最容易被引用到 README 或幻灯片里而丢掉前提的一块。

### ❌ 明确不做

这些边界由 `tests/test_spec_exclusions.py` 以代码方式钉住，不是口头约定：

- **不做验证码打码，不做请求签名逆向**。`blocked_captcha` 是**判定**（识别出这个源被墙），不是绕过。
- **不做账号池**（多账号轮询换稳定性）。
- **不自动突破访问限制**。用户已有授权会话可见的文字内容纳入当前扩源目标，现有实现只有部分 Cookie 与浏览器会话基础，尚无统一授权采集通路。取不到的源仍走[用户导出通路](#取不到的源靠导出)降级。
- **不加 SSE / WebSocket / 流式传输**。请求-响应加轮询足够。
- **语义检索永不默认开启**：需要 `retrieval.semantic` 与 `retrieval.embedding_model` **两个条件同时成立**，后者默认为空串。
- **图文（OCR / VLM）通路尚未实现**：图片目前只把 URL 存进 metadata，不参与检索；唯一的媒体→文本通路是 B 站 CC 字幕。语料库已能**接收**声明了 `provenance` 为 `ocr` / `vlm` 的文本（见[导出入库](#取不到的源靠导出)），但**自动从图片提取文字**的部分还没有。

## 运行一次会发生什么

`uv run periscope --hours 24` 的阶段顺序如下，每个阶段都标了代码落点：

1. **采集**（`Orchestrator.fetch_all_sources`）— 按注册表遍历 14 类源并发抓取，每个源独立成败；结果写入证据语料库并重算近似重复聚簇。
2. **跨源去重**（`merge_cross_source_duplicates`）— 规范化 URL（剥离 `utm_*`、`gclid` 等跟踪参数），合并指向同一内容的多源条目，保留内容最丰富的一条。
3. **分析**（`analyze_items`）— 分类路由到 Profile，由 AI 完成评分、理由、摘要与标签。
4. **选择与过滤**（`select_digest_items`）— Profile 阈值过滤 → AI 主题去重 → 均衡配额（`digest` 配置）。
5. **富化**（`enrich_items`）— 第二遍 AI，按 Profile 定义的 block 生成多语言产物，可调用联网检索与历史检索工具。
6. **声明核查**（`analyze_claims`）— 将高分条目蒸馏为原子声明，关联语料证据并评级（尽力而为）。注意它跑在评分**之后**（抽取才能对准最有价值的条目），但吃的是全部已分析条目而非最终入选条目。
7. **摘要与投递** — 程序化渲染多语言 Markdown 日报，保存到 `data/summaries/`，并按配置发布或投递。

每个阶段都可被单独调用——这正是 MCP 能分阶段驱动流水线的原因。结构与图元（通路、会话状态机、一轮时序）见 [docs/architecture.md](docs/architecture.md)，那三张图与代码是双向对齐的，画进去的名字由测试核对。

## 三层能力

这一节按 `src/` 的实际目录组织，每一项都给出"想读代码从哪开始"。

### 1. 日报：采集 → 评分 → 投递

完整流水线见上一节。核心是 **Profile**：一套可复用的编辑规则——什么内容该收录、什么值得保留、该写成什么样。

| 你关注的内容 | Profile | 你将得到 |
|---|---|---|
| 科技新闻 | `tech-news` | 事件与背景，必要时附影响分析与社区讨论 |
| 工程深度长文 | `tech-blog` | 背景、解决方案与可落地的要点 |
| AI 创作者素材 | `ai-creator` | 摘要，必要时附热点切入角度与内容思路 |
| 财经资讯 | `finance-news` | 公司动态与市场脉络 |

你可以为某个信息源指定 Profile，也可以让 AI 自动选择（`"profile": "auto"`）。想要不一样的风格？通常无需改 Python，直接改编现有 Profile 即可。[定制你的 Profile →](docs/profiles.md)

> 单 Profile 的用户偏好（评分阈值、主题去重开关）写在 `processing.profile_settings`，**不写在 Profile 文件里**。

**代码入口**：`src/orchestrator.py`（流水线）· `src/processing/profiles.py`（Profile 加载）· `src/ai/analyzer.py`（评分）

### 2. 证据语料库：`corpus.db`

所有曾经采集过的条目都会带 SimHash 指纹与近似重复聚簇被持久化存储，可用 SQLite FTS5（trigram 分词，对 CJK 友好、无需分词库）检索。**知识跨运行累积，而不是在生成摘要后就蒸发。**

库内是 **12 张普通表 + 2 张 FTS5 虚表**（完整的表清单与作用见 [docs/architecture.md](docs/architecture.md)）：

- **`items`** — 条目本体，含 `claimable`（作者亲写层）、`sections_json`、`publisher`、`trust` 与 `trust_features_json`、`fingerprint`、`cluster_id`。
- **`items_fts`** — 对 `title` / `content` / `author` 的全文索引。面板检索走这条（要召回率）。
- **`claim_fts`** — **只索引 `claimable`**。声明关联与评级走这条，因此评论永远不会被当作证据。
- **`runs`** — 每轮运行的新增与总量，崩溃续跑靠它。

**关键性质**：聚簇是**尽力而为的分组**，不是硬去重——**什么都不删**。独立信源计数需要重复条目留在库里（先按簇折叠、再数不同发布者），删掉就没法数了。

**代码入口**：`src/corpus/store.py`（schema 与门面）· `src/corpus/simhash.py`（指纹）· `src/corpus/retrieval.py`（取证检索）

### 3. 声明核查：从条目到 verdict

高分条目被蒸馏为**原子化、可核查的声明**，每条声明与语料证据关联并评级（supported / contested / unsupported + 置信度）。

三个设计决定值得单独说明：

- **独立性计数**：先按簇折叠重复内容，**再数不同的 `(source_type, publisher)`**；解析不出发布者的条目不投票。
- **noisy-OR 聚合**：`T = 1 − Π(1 − trust_i · d_i)`。不用求和是因为求和没有上界，够多的低质源能把任何结论刷成 supported。
- **`contested` 不是标签而是证据**：`claim_contradictions` 表记下是哪两条声明、靠哪些条目互相冲突，且矛盾会作为**判定输入**（已评级的声明遇到矛盾立刻转 `contested`）。

**报告不藏"没走到评级"的声明**：`⏳ 未评级（低于分诊门）` / `🔍 证据不足` / `🏷️ 无可核验发布者` / `❌ 可信度不足（T=…）`。这四行比 verdict 本身更重要——它们把"没评级"和"评了但不确定"分开。

> ⚠️ 评级**准确率**未主张，看[界定说明](#-能力已实现效果未主张)。

**代码入口**：`src/analysis/claims.py` · `src/corpus/trust.py` · `src/corpus/citations.py`

### 4. 多轮共创研究：逐轮动词

研究循环的顶层动词是 **`AskUser | Rescope | Deepen | Finalize`**（`src/research/moves.py`）。提出一个问题，会得到一棵分解后的子问题树，逐题对照语料取证，并输出带引用的 Markdown 报告；追问会在同一会话上跨天、跨重启迭代（状态保存在 SQLite 中）。

**报告是带 revision 的草稿工件，不是每轮重刷的投影**：

- 你**改过或锁定**的章节在后续重算中**只被标记为陈旧（`stale`），不会被覆盖**；
- 被删分支的章节也带 `stale` 留下——删分支是范围决定，静默删掉你写的字是版本化工件绝不能做的事；
- 章节身份用**标题**，所以锁在你改过的那一节上，即使它下面的引用编号变了也依然有效。

**缺输入时会主动提问**并把会话停在 `awaiting_user`（一等状态，不是异常路径）。没有可用 LLM 时，回退策略只产出 `deepen` / `finalize`——**联系不到模型的会话仍会终止，而不是连环追问**。

**一轮 `step` 只重算被点名的分支**，并返回这一轮改了什么（`revision` / `changed_sections` / `new_evidence` / `verdict_changes` / `pending_request`）。局部重算 2 次调用 vs 全树 5 次（4 分支），这是多轮的**成本**含义。

**代码入口**：`src/research/session.py`（循环与状态机）· `src/research/moves.py`（动词）· `src/research/drafts.py`（版本化工件）· `src/research/planner.py`（唯一的 LLM 接缝）

## 三条支撑机制

上面四项能力决定了"能做什么"；下面三项决定了它们在噪声里**是否真的站得住**。

### 1. 证据分层：类型化字段，不靠字符串约定

抓取器把评论、楼层回复、视频字幕**声明**为 `ContentItem.sections`：

- **`primary`**（作者亲写，可承载声明）
- **`community`**（人群发言，只能当线索）

每段带 `author` / `provenance` / `locator`（可引用到具体一层）。声明只从前者蒸馏、证据只与前者关联、报告引用的也是前者。

**这不是约定，是类型**：分层曾由扫描五个中文字符串标记来恢复，那会让任何用别的分隔符的抓取器把人群文本静默提升为作者亲写。现在分层随文本一起传递（`Section.tier`），而 `src/scrapers/` 里**已不允许出现任何分层标记字面量**——由 `tests/test_tier_guard.py` 钉住。

`provenance` 有六个值：`author` / `transcript` / `ocr` / `vlm` / `legacy_marker` / `manual_export`。其中一条**类型级不变式**值得单独提：

> `provenance="vlm"`（对画面的描述）会**被强制为 `asserted=False`**，因此永远进不了声明抽取。图上烧进去的字（`ocr`）相反：保留断言资格，只按 `confidence` 打折。这个不对称才是设计内容——一句对画面的猜测，离变成对世界的断言只差一次忘写参数。

### 2. 条目可信度与独立性

每条入库内容算一个**可拆解**的信任分：

```
σ(源先验, 作者等级, 交叉支持, 可核验实体, 新鲜度, 来源方式 − 模板度)
```

**特征与分数一起落库**（`items.trust` + `items.trust_features_json`），所以"为什么这条被当成证据"随时答得出来，而不是一个黑箱数字。

`provenance` 作为其中一个特征被折价：`author` 1.0 / `transcript` 0.95 / `vlm` 0.6 / `legacy_marker` 0.8 / `manual_export` 0.85 / `ocr` 用该段自己的 `confidence`。

### 3. 自适应取证加宽

一个子问题**不是只有一次查询**。找不到证据时按固定阶梯逐级换打法：

```
baseline → widen_terms → switch_source_family → rewrite_query → collect_keywords
```

顺序原则是**免费动作在前、付费动作在后**（约束是限流的免费层，不是算力）。每次尝试都写进 `research_actions`，未回答的子问题会在报告里列出「取证尝试：动作(+新增条数)」——把"语料里确实没有"和"这次的问法没查到"区分开。

### 取不到的源，靠导出

对于尚未适配、授权会话失效或遇到验证码的来源，保留用户导出降级通路（见[明确不做](#-明确不做)）：**你的账号本来就能看见的内容，导出成 JSON 交给我入库**。用户已有授权会话的直接采集属于当前扩源方向，不代表所有平台已经接通。分层靠负载自己声明，`provenance` 默认为 `manual_export`（折扣 0.85）。

```bash
# 先预览（只校验，不建库、不写入）
uv run python scripts/import_corpus.py --file data/export.example.json --data-dir data --dry-run
# 确认后入库
uv run python scripts/import_corpus.py --file data/export.example.json --data-dir data
```

同一份负载还能走 MCP 的 `ps_corpus_import` 与面板的 `POST /api/import`。样例形状见 `data/export.example.json`（**仓库里的文件本身由测试解析**，样例与实现脱节会立刻变红）。校验逻辑只有一处：`src/corpus/ingest.py`。

## 三个入口

CLI、Web 面板与 MCP **共享同一份 `corpus.db`**，因此会话与证据跨入口、跨重启都可见。

### CLI

```bash
uv run periscope --hours 24            # 采集 → 评分 → 日报 → 语料库 + 声明
uv run periscope-wizard                # 交互式配置向导
uv run periscope-web --data-dir data   # Web 面板（默认 http://localhost:8790）
uv run periscope-mcp                   # MCP 服务（stdio）
uv run periscope-webhook --dry-run     # 预览 Webhook 请求
uv run periscope-wechat status         # 微信投递登录状态
```

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--hours N` | 取 `collection.time_window_hours` | 抓取最近 N 小时的内容 |
| `-d`, `--data-dir PATH` | `data` | 数据目录路径 |
| `-c`, `--config PATH` | `<data-dir>/config.json` | 配置文件路径 |
| `-l`, `--log-level LEVEL` | `WARNING` | DEBUG / INFO / WARNING / ERROR / CRITICAL |

`--data-dir` 改变**状态目录**（摘要、订阅者、语料库、LLM 缓存与默认配置位置）；`--config` 只改变配置文件。路径组合与初始化方式见[配置路径](docs/configuration.md#configuration-paths)。

### Web 面板

```bash
uv run periscope-web --data-dir data        # 默认 http://localhost:8790
```

三栏式界面：**证据库**（全文检索）、**研究报告**（长会话研究）、**核查台**（按 verdict 展示声明与证据）。研究报告栏按轮次组织：推一轮按钮、每节的「已锁定」与「新版已变 · 未覆盖」标记、待回答请求卡片。顶部可一键触发采集，另有「导入你导出的内容」一节（贴 JSON 或选文件 → 只校验 → 入库，逐条报告第几条为什么不收）。API 文档位于 `/api/docs`。

### MCP

```bash
uv run periscope-mcp
```

以 stdio 方式运行，提供 **27 个工具**与 7 个 `periscope://` 资源，覆盖配置校验、分阶段流水线、运行产物、证据检索、声明核查、研究会话与 Webhook 通知。详见 [MCP 工具说明](src/mcp/README.md) 与[客户端配置](src/mcp/integration.md)。

## 支持的 AI 提供商

默认使用免费的 **Agnes** 层，其余提供商可任选其一，或通过 `provider_chain` 组合成降级链。

| 提供商 | 默认模型 | API Key 环境变量 |
|--------|----------|------------------|
| `agnes`（默认，免费层） | `agnes-2.5-flash` | `AGNES_API_KEY` |
| `anthropic` | `claude-3-5-sonnet-20241022` | `ANTHROPIC_API_KEY` |
| `openai` | `gpt-4` | `OPENAI_API_KEY` |
| `azure` | `gpt-4` | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` |
| `gemini` | `gemini-1.5-flash` | `GOOGLE_API_KEY` |
| `ali`（通义千问） | `qwen-plus` | `DASHSCOPE_API_KEY` |
| `doubao`（豆包） | `doubao-pro-32k` | `DOUBAO_API_KEY` |
| `minimax` | `MiniMax-M3` | `MINIMAX_API_KEY` |
| `deepseek` | `deepseek-chat` | `DEEPSEEK_API_KEY` |
| `ollama`（本地部署） | `llama3.1` | 无需 Key |

设置 `ai.provider_chain`（逗号分隔的提供商列表）即可启用链式回退：遇到 429/限流、401/403/额度不足、502/503 或空响应时，自动切换到下一个提供商。

**所有 LLM 调用都经过持久化响应缓存与限流**（`data/llm_cache.db`）。这对免费层是承重结构而非优化：崩溃恢复运行与后续追问会重复问几乎相同的提示，每一次都应该是零调用。

## 支持的数据源

| 数据源 | 抓取内容 | 评论 / 附加能力 |
|--------|----------|-----------------|
| **Hacker News** | 按分数排序的热门帖子 | 是（前 N 条评论） |
| **RSS / Atom** | 任意 RSS 或 Atom 源 | 可选全文抽取 |
| **Reddit** | 子版块 + 用户帖子 | 是（前 N 条评论） |
| **Telegram** | 公开频道消息 | — |
| **Twitter / X** | 用户时间线 + 关键词搜索（Apify）；另支持 Playwright + Cookie 的免 token 模式 | 是（前 N 条回复） |
| **GitHub** | 用户事件与仓库 Release | — |
| **OpenBB** | 按自选股/提供商抓取公司财经新闻（可选 SEC filings） | — |
| **OSS Insight** | 开源趋势仓库 | — |
| **GDELT** | 匹配搜索词的全球新闻 | — |
| **Google News** | 经 RSS 的新闻搜索 | — |
| **Bilibili** | 热门视频（标题、统计、热门评论） | 是；有 CC 字幕时提取转录 |
| **V2EX** | 节点主题 + 热门主题 | 是（含回复） |
| **Discourse** | 任意 Discourse 论坛的最新主题 | 是（含楼层讨论） |
| **YouTube** | 经官方公开 atom feed 抓取频道更新 | 标题 + 描述文本 |

每个源在 `SOURCE_SPECS`（`src/models.py`）里带一段纯元数据：**类型**（official / forum / ugc_social / aggregator）、**源先验**（进入可信度计算）、**默认时间基准**、**限流**。抓取器与源的绑定在 `src/sources/registry.py`，加一个源目前只需两处同步。

**礼貌抓取**：per-host 令牌桶 + 抖动 + 429 重试一次；`Retry-After` 同时支持秒数与 HTTP-date。

## 投递渠道

| 渠道 | 作用 |
|------|------|
| **邮件订阅** | 向订阅者发送每日简报，并通过 SMTP/IMAP 处理订阅/退订请求 |
| **Webhook 通知** | 把成功或失败结果推送到飞书/Lark、钉钉、Slack、Discord 或任意自定义 Webhook 端点 |
| **微信通知** | 通过 iLink Bot 在扫码登录并收到你的消息后发送简报；受微信回复条数限制 |

## 快速开始

### 1. 安装

**方式 A：本地安装**

```bash
git clone https://atomgit.com/NLY22/periscope.git
cd periscope

# 使用 uv 安装（推荐）
uv sync

# 需要测试/开发依赖时
uv sync --extra dev

# 或使用 pip
pip install -e .
```

Periscope 需要 **Python 3.11 及以上**（见 `pyproject.toml` 的 `requires-python`）。安装 pytest 等开发依赖请用 `uv sync --extra dev`（`dev` 是可选 extra）。

可选 extra：`openbb`（财经新闻源）、`twitter`（Playwright 浏览器与系统依赖）。

```bash
uv sync --extra openbb
uv sync --extra twitter
```

如果 `openbb` 在你的机器上拉取到没有 wheel 的包，可只用二进制方式手动安装 SDK：

```bash
uv pip install --only-binary=:all: openbb openbb-benzinga
```

**方式 B：Docker**

```bash
git clone https://atomgit.com/NLY22/periscope.git
cd periscope

# 可选：首次运行前用逗号分隔的 extras 构建
docker compose build --build-arg EXTRAS=openbb periscope-collect

docker compose run --rm periscope-collect --hours 24   # 采集器，运行一次
docker compose up -d periscope-web                     # Web 面板，端口 8790
```

基于 `trafilatura` 的正文抽取已包含在基础安装中。`twitter` extra 还需要 Playwright 浏览器与系统依赖，当前 Dockerfile 并未安装。

### 2. 配置

**方式 A：交互式向导（推荐）**

```bash
uv run periscope-wizard
```

向导会询问你的兴趣（例如「LLM 推理」「嵌入式」「Web 安全」），并自动生成 `data/config.json`。CLI 选项见[交互式向导](docs/configuration.md#interactive-wizard)。

**方式 B：手动配置**

```bash
cp .env.example .env                           # 填入你的 API Key
cp data/config.example.json data/config.json   # 定制你的信息源
```

最小手动配置示例：

```jsonc
{
  "ai": {
    "provider": "openai",
    "model": "gpt-4",
    "api_key_env": "OPENAI_API_KEY"
  },
  "sources": {
    "rss": [
      {
        "name": "Simon Willison",
        "url": "https://simonwillison.net/atom/everything/",
        "profile": "tech-news"
      }
    ]
  },
  "processing": {
    "profiles_dir": "profiles",
    "default_profile": "tech-news",
    "profile_settings": {
      "tech-news": { "threshold": 7.0, "topic_dedup": true }
    }
  }
}
```

`api_key_env` 必须是**环境变量的名字**，而不是 API Key 本身。把真正的密钥放进 `.env`：

```bash
OPENAI_API_KEY=sk-your-key
```

`data/config.json` 中任意字符串值都可以用 `${VAR_NAME}` 引用环境变量，适用于 `ai.base_url`、私有 RSS 源地址、Webhook 端点或自定义请求头模板等。

### 3. 运行

```bash
uv run periscope --hours 24        # 本地
docker compose run --rm periscope-collect --hours 24   # Docker
```

生成的报告保存在 `data/summaries/`（若设置了 `--data-dir` 则为 `<data-dir>/summaries/`）。

### 4. 自动化（可选）

Periscope 适合用系统定时器调度，例如 `cron` 或 `systemd timer`；Docker Compose 也可直接配合定时任务使用（`docker compose run --rm periscope-collect --hours 24` 配 cron 即可）。

仓库里另有 `.github/workflows/tests.yml`。**本平台不执行任何 GitHub 语法的 workflow**（已实测：PR 的 `check_tasks_num` 为 0），那条文件只对在 GitHub 上跑的人有意义；在这个平台上要定时运行，就用任何能执行 shell 的调度器，按上面的命令排程即可。

## 配置

完整参考见[配置指南](docs/configuration.md)，密钥见 [`.env.example`](.env.example)，可复制模板见 `data/config.example.json`。

主要配置块及其作用：

| 配置块 | 作用 |
|--------|------|
| `ai` | 提供商、模型、`api_key_env`、`base_url`、`provider_chain`、温度、并发与限流 |
| `sources` | 各数据源及其分类、Profile 指定 |
| `collection` | 默认时间窗口 `time_window_hours` |
| `digest` | 简报总长度与分类配额（`max_items`、`category_groups`） |
| `processing` | `profiles_dir`、`default_profile`、单 Profile 偏好 `profile_settings` |
| `display` | 图标风格 `icon_style`（`emoji` / `nerd` / `ascii`） |
| `extractors` | 正文抽取器配置 |
| `email` / `webhook` / `wechat` | 三个投递渠道 |
| `corpus` | 证据语料库：开关、路径、聚簇参数 |
| `analysis` | 声明核查：抽取条数、证据数、评级预算与**两道门的阈值** |
| `research` | 长会话研究：证据预算、加宽轮数、报告骨架 |
| `retrieval` | 取证检索：查询扩展、语义腿、按需采集 |

> 后四个块（`corpus` / `analysis` / `research` / `retrieval`）是本项目在日报之上的证据层配置，**每个键及其默认值都在[配置指南](docs/configuration.md)里逐项列出**，并由测试核对默认值不得滞后于模型定义。这里不复制那张表，避免两处漂移。

**严格度是分裂的，而且是有意的**：`Section` / `ContentItem` 是 `extra="forbid"`（写错字段名立刻报错，因为分层字段写错代价太大），而四个证据配置块**故意不是** `forbid`（收紧会让带杂键的现有配置直接加载失败）。这个分裂由测试钉住。

## 项目结构

```
periscope/
├── src/
│   ├── main.py               # CLI 主入口
│   ├── _cli.py               # 各入口共享的参数（--data-dir / --config / --log-level）
│   ├── orchestrator.py       # 流水线编排与阶段复用
│   ├── models.py             # 数据模型、Section、SOURCE_SPECS
│   ├── scrapers/             # 14 类数据源抓取器 + 限流 + 鉴权
│   ├── sources/              # 源注册表（key → 抓取器）与可达性探针
│   ├── ai/                   # 多 provider 客户端、分析/富化/摘要/本地化/缓存
│   ├── processing/           # Profile 加载、历史检索、内容处理
│   ├── corpus/               # 证据语料库（SQLite + FTS5 + SimHash + 分层 + trust）
│   ├── analysis/             # 声明级核查
│   ├── research/             # 长会话研究（动词 / 会话 / 草稿 / 骨架）
│   ├── web/                  # Web 面板（FastAPI + 单文件三栏 UI）
│   ├── mcp/                  # MCP 服务与运行产物存储
│   ├── services/             # 邮件 / Webhook / 微信投递
│   ├── setup/                # 交互式配置向导
│   ├── extractors/           # 正文抽取（trafilatura）
│   └── storage/              # 配置与摘要存储
├── profiles/                 # 内置 Profile（tech-news / tech-blog / ai-creator / finance-news）
├── docs/                     # 文档、架构图与站点资源
├── tests/                    # 测试（含文档护栏）
├── data/                     # 配置示例、导出样例与运行数据
├── scripts/                  # 辅助脚本（导入、评测、可达性探针）
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

每个 Profile 目录是固定的四文件布局：`profile.json` / `match.md` / `analysis.md` / `enrichment.md`。`profiles/` 会被打包进 wheel 的 `src/_builtin_profiles`，因此 `.venv` 里出现的副本是安装产物。

## 开发与测试

```bash
uv sync --extra dev                        # 安装开发依赖
uv run pytest                              # 运行全部测试
uv run pytest tests/test_corpus.py         # 运行单个测试文件
uv run python scripts/eval_retrieval.py    # 检索消融表（docs/evaluation.md）
uv run python scripts/eval_retrieval.py --tiering marker   # 复现分层前的 A 档
uv run python scripts/eval_multiturn.py    # 多轮调用数比值 / 轮次 / 灌水曲线（不联网）
```

**关于 CI：这个平台上没有自动执行。** `.github/workflows/tests.yml` 是 GitHub 语法的配置，AtomGit 不执行它（每个 PR 的 `check_tasks_num` 都是 0，已实测确认）。所以：

- 那条文件保留着，迁到 GitHub 或支持该语法的平台就生效；内容仍是可信的验收脚本（Linux + Windows 各跑一遍全量，再跑一次检索 harness，只看能否复现，不在 CI 里断言指标数值）。
- 但在当前平台，**验证是提交者的责任**：本地跑 `uv run pytest` 与相关 harness，把实测数字写进 PR 正文。本项目不接受「有 CI 兜底」作为质量证据。

### 文档护栏

`tests/test_docs_match_code.py` 把文档里的可核对声明变成断言，因为此前 README、MCP 指南与配置指南曾**同时**出现三处不实（工具数、工具清单、四个证据块一个都没记）。它现在检查：

- 每个证据配置键都在配置指南里、默认值与模型逐格一致；
- MCP 指南不漏列、也不虚构工具；README 引用的工具数与服务端注册数相等；
- 六个 CLI 命令都被 README 提到；每张顶层文档都从 README 链得到；
- 文档里反引号包起来的每个符号都真实存在于仓库；
- 文档里的 `--flag` 都被对应的 argparse 接受；
- `docs/architecture.md` 的状态名 / 动词名 / 表名与代码双向一致。

改动配置、MCP 工具或研究会话语汇时，跑一遍它。**CONTRIBUTING 第 9 条**列出了必须同步改架构图的清单。

## 安全与可靠性

- **SSRF 防护** — 抓取与正文抽取前校验 URL 的 scheme、主机与端口，解析后要求所有地址为公网，并逐跳校验重定向。
- **路径逃逸防护** — 摘要与运行产物写入前校验目标路径位于允许目录内。
- **凭据脱敏** — MCP 返回的有效配置会递归脱敏 `token`、`secret`、`password` 等字段，Webhook 日志同样脱敏。
- **诚实的降级而非崩溃** — 没有 LLM key 也能运行：语料照常增长、证据照常确定性关联、报告会如实标注「尚未回答」而不是编造内容。单源抓取失败、LLM 缓存读写失败、声明评级失败都不会中断整条流水线。

**采集边界**：当前目标包含用户已有授权会话可见的文字内容；不做验证码打码、请求签名逆向与账号池，取不到的源仍走用户导出通路。技术边界见 [SECURITY.md](SECURITY.md)，既有代码约束由 `tests/test_spec_exclusions.py` 检查；授权内容的统一采集仍属于待建设能力。

## 文档

| 指南 | 说明 |
|------|------|
| [配置](docs/configuration.md) | AI 提供商、信息源、Profile、过滤、邮件、Webhook、微信与 MCP 配置 |
| [处理画像](docs/profiles.md) | Profile 路由、提示词、运行期过滤偏好、富化 block 与工具 |
| [评分](docs/scoring.md) | Periscope 如何评估与排序新闻条目 |
| [抓取器](docs/scrapers.md) | 各数据源抓取器细节与扩展说明 |
| [正文抽取](docs/extractors.md) | RSS 源的全文抽取 |
| [架构图](docs/architecture.md) | 三张结构性图：广源→可用证据的通路、研究会话状态机（`awaiting_user` 是一等状态）、一轮交互的时序；每张图带代码落点，图元由护栏测试与代码对齐 |
| [取证检索与证据分层](docs/retrieval.md) | 噪声从哪来、类型化分层怎么声明、可信度与独立性怎么重算、找不到时怎么加宽、多轮草稿怎么保住了用户的字、怎么复现 |
| [检索与取证评测](docs/evaluation.md) | 消融表、标注口径、多轮成本与灌水曲线、已知不足 |
| [自助测试手册](docs/selftest.md) | 五条命令自己把效果重测一遍：每步该看到什么、哪些"怪现象"是正常的、真模型腿取不到时怎么降级 |
| [Twitter / X Cookie 配置](docs/twitter-cookies.md) | 免费方案：Playwright + 自己账号的 cookie 抓推文（Apify 订阅的替代路径） |
| [MCP 工具](src/mcp/README.md) | MCP 兼容客户端可用的工具参考 |
| [地基调研](docs/foundation-survey-2026-10-19.md) | 可 fork 的上位项目逐项实测：许可证、stars、活跃度，以及"广度缺口在接入而不在源数量"的结论 |
| [变更记录](CHANGELOG.md) | 已合入 `main` 的各期改动（P0 / P1 / P2 + 文档护栏）各自做了什么、修了什么、有多少实测数字；也写明尚未发布任何版本 |
| [设计 spec 与三期实现计划](docs/superpowers/specs/2026-09-29-broad-source-credibility-and-multiturn-research-design.md) | 为什么这么改（spec v4，§15 是交付记录）、`docs/superpowers/plans/` 下逐 Task 的计划 |

## 项目状态

**尚未发布任何版本**：`pyproject.toml` 仍是 `0.1.0`，`git tag` 为空。

日报闭环（多源采集、Profile 驱动的分析与富化、去重、评论摘要、双语生成、三个投递渠道、Docker 部署、MCP 集成与配置向导）与证据层（语料库、声明核查、多轮研究，及支撑它们的证据分层、条目可信度、自适应加宽）**均已合入 `main`**，Web 面板是独立入口。各期的实测数字见 [CHANGELOG.md](CHANGELOG.md)。

后续计划：

- **声明 verdict 的人工标注 50–100 条** → 报 macro-F1 与按独立信源数分桶的一致率，再用 `roc_thresholds()` 校准两道门的阈值。这是本项目唯一"能力已实现、效果未主张"的一块：工具已就位（`scripts/eval_claims.py --export/--score`），缺的是标注本身。
- **图文 → 文本通路（OCR / VLM）**：目前图片只把 URL 存进 metadata，不参与检索。语料库与信任模型已为 `provenance="ocr"` / `"vlm"` 预留位置（含 VLM 强制不可断言的类型不变式），但**自动提取**的部分尚未实现。按设计，OCR 错误率对 verdict 的影响必须先实测，在此之前不得声称"图文源已可用"。
- **源可达性探针**：判别逻辑已做成可复跑的工具（`src/sources/reachability.py` + `scripts/spike_sources.py`，判定分为 `pass / list_only / blocked_captcha / signed_required / blocked_auth / error`，离线用 `httpx.MockTransport` 测，**不加 `--online` 不发任何请求**）。小红书与贴吧的探针**不需要账号**就能跑，结论落到 `data/eval/reachability_results.json`：

  ```bash
  uv run python scripts/spike_sources.py --source xiaohongshu --url <公开笔记页 URL> --online
  uv run python scripts/spike_sources.py --source tieba --kw <吧名或关键词> --online
  ```

  （`--online` 是唯一会让它真的发请求的开关；去掉它就是打印判定计划。）探针不通过时的降级路径**已经实现**（用户导出 → 入库），所以"取不到的源"不再是死路，只是不自动。
- **支持更多数据源类型**，例如 Discord。
- **平台侧未决**：GitHub 语法的 workflow 在本平台不被执行（护栏与测试要人工过一遍）、仓库 issue 开关只能在网页打开。
- **在 AtomGit 上发布 Release**；发布到 PyPI，支持 `pip install`。

## 贡献

欢迎贡献：issue 与 PR 请开在 <https://atomgit.com/NLY22/periscope>。规范见 [CONTRIBUTING.md](CONTRIBUTING.md)，行为准则见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)，安全问题见 [SECURITY.md](SECURITY.md)。

动手前建议先读 [docs/retrieval.md](docs/retrieval.md) 与 [docs/evaluation.md](docs/evaluation.md) —— 新增能力要能进消融表，否则只是加功能。信息源与 Profile 的改动直接开 PR 或 issue。

## 许可证

[MIT](LICENSE)

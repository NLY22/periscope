> **阶段状态（已合入 `main`）：** 平台文字与深层讨论深采、共享本地调查后端、带固定证据版本引用的交互式调查、Web 深度调查工作区、MCP 36 工具协议集成、免 Key 自主公网统一检索（DuckDuckGo 首选 / DeepSeek 备选）及 DOCX/XLSX/PDF 结构化附件解析已全部合并入主线。

> **代码结构与入口导航：** 话题深度检索核心位于 `periscope/`，支持通过 `uv run periscope-topic --topic "人形机器人"` 运行；原有 `python topic.py` 与 `core/`、`collectors/` 保持完全兼容；`src/` 承载证据内核、智能日报、多轮共创研究及 Web/MCP 服务。详见 [代码组织说明](docs/code-organization.md) 与 [架构图](docs/architecture.md)。

> **文档快速索引：** [文档入口](docs/README.md) · [代码与数据布局](docs/code-organization.md) · [采集链路与边界](docs/collection-pipeline.md) · [12 平台深采验收](docs/platform-deep-collection-matrix.md) · [功能审计](docs/project-audit-2026-10-22.md) · [爬虫使用与边界](docs/native-crawler.md) · [信息源体系与边界](docs/source-system.md) · [取证检索与证据分层](docs/retrieval.md) · [评测体系](docs/evaluation.md) · [配置指南](docs/configuration.md) · [重构记录](docs/REFACTOR_2026-10-18.md) · [框架记录](docs/foundation.md)

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

📡 围绕研究与调查问题，联合自主免 Key 公网检索、12+ 平台原生直采、结构化文档解析、学术资料与历史语料，保留精确版本出处，沉淀为可检索、可核查的证据库。项目同时提供 Web 深度调查工作区、中英双语智能日报与 MCP 智能体集成。

[📋 配置指南](docs/configuration.md) · [🧩 Profile 定制](docs/profiles.md) · [🏗️ 架构图](docs/architecture.md) · [🔎 取证检索机制](docs/retrieval.md) · [📊 评测](docs/evaluation.md) · [🔧 重构记录](docs/REFACTOR_2026-10-18.md)

</div>

---

## 目录

- [它是什么](#它是什么)
- [为什么需要 Periscope](#为什么需要-periscope)
- [现在真的能做什么](#现在真的能做什么)
- [核心系统能力架构](#核心系统能力架构)
- [四大工作模式](#四大工作模式)
- [三条核心支撑机制](#三条核心支撑机制)
- [入口工具链与 Console Scripts](#入口工具链与-console-scripts)
- [支持的 AI 提供商](#支持的-ai-提供商)
- [支持的数据源与深采矩阵](#支持的数据源与深采矩阵)
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

海量内容散落在公网网页、技术论坛、社交平台和各类文档附件中，而信息的真实度参差不齐，AI 摘要往往脱离原文、缺乏论据支撑。**Periscope 会替你采集、提取、清洗、去重与相互印证**，把真正有据可查的内容连同背景脉络、社区讨论与溯源链接完整呈现。

Periscope 解决两个核心问题：
1. **「怎样从全网广阔渠道获取深层真实信息」**：不仅支持 14 类标准订阅与免 Key 自主公网检索，更打通了 12+ 垂直社交平台深层讨论（GitHub Issue/PR 回复树、V2EX 嵌套帖、知乎/微博展开评论、B站 CC 字幕等），并支持跟进 DOCX、XLSX、PDF 附件与公开 GET 查询表单。
2. **「这个说法站不站得住、出处究竟在哪」**：一切采集内容均沉淀进 **证据语料库**（`corpus.db`，跨会话累积持久化）。通过**类型化证据分层**（作者亲写层 vs 社区发声层严格隔离）、**独立发布者信源计数**、**原子声明关联核查**及**固定证据版本引用**，为每一次多轮交互调查生成带锚点证明的严谨简答。

**本项目的立场一句话**：把来源放宽到论坛、社交媒体与非结构化网页，信息质量必然充满噪声；全部工作在于让"降下去的质量"被分层、检索、计数、版本化日志与引用核验**重新补回来**。

## 为什么需要 Periscope

| 维度 | 普通搜索引擎 | 纯 LLM 检索 / 朴素 RAG | Periscope 可信取证调查引擎 |
|---|---|---|---|
| **信源覆盖** | 仅索引公开静态网页，无法穿透深层讨论 | 通常依赖单个商业 Search API 或本地静态向量库 | **混合矩阵**：免 Key 公网检索 + 12 平台深层回复树 + 自主爬虫与 DOCX/XLSX/PDF 附件抽取 |
| **可信度分层** | SEO 权重与竞价主导，无信源鉴别 | 检索文本块碎片，把网友吐槽、机器洗稿与权威发布同等对待 | **类型化严格分层**：`primary`（作者亲写层）与 `community`（评论线索）类型隔离，严禁将评论算作证据 |
| **真实性印证** | 用户需自行点开几十个网页比对 | 容易产生合成幻觉，结论脱离具体依据 | **独立发布者计数**（Noisy-OR 聚合）+ **原子声明核查**（Supported / Contested / Unsupported） |
| **引用与可溯源** | 仅给出原始 URL 链接（易失效、篡改） | 给出段落引用，但模型重跑结果随机漂移 | **版本化快照日志（Journal）**，输出固定证据版本锚点，一键精准穿透回查当时快照 |
| **交互与协作** | 仅支持关键词单次搜索 | 单次问答，模型重新推理可能覆盖用户原意见 | **多轮共创状态机**：支持追问、加宽、重设范围；草稿锁定（`stale` 保护，用户编辑内容绝不被覆盖） |

## 现在真的能做什么

这一节先说清边界，避免将功能设想读成超出代码实现的承诺。

### ✅ 已交付，有测试钉住

| 核心领域 | 具体能力 | 代码落点 |
|---|---|---|
| **免 Key 公网检索** | DuckDuckGo 首选检索、自然语言长句清洗（Query cleaning）、跨语言技术术语桥（中英对齐）、DeepSeek 自动备选兜底、前排结果正文自适应快速水化（Fulltext hydration） | `periscope/search/` |
| **12+ 平台深层讨论直采** | HN（话题/嵌套回复树）、GitHub（正文至多 30,000 字+评论并行）、V2EX、Stack Overflow、B 站（CC 字幕层）、知乎（子评论页）、微博（独立回复分页与乱序树重建）、小红书（持久浏览器会话）、抖音、CSDN、掘金、快手（验证码感知终止） | `periscope/collection/catalog/`、`src/scrapers/` |
| **自主爬虫与附件抽取** | `--crawl` 与 `--crawl-seeds` 跟进页面与 Sitemap；结构化抽取 DOCX（段落/表格顺序）、XLSX（工作表/行号/坐标）、PDF 附件并标注 complete/partial 覆盖度；支持公开 GET 表单参数化查询 | `periscope/collection/crawling/` |
| **交互式深度调查工作区** | Web 面板与 MCP 共用本地调查服务：多轮追问、停止、流式进度、历史轮次回放、带固定证据版本锚点引用的简答生成，支持跨轮次复用前序高可信证据 | `periscope/investigation/`、`src/web/` |
| **证据分层与条目可信度** | 严格类型化区分 `primary`（作者亲写，可承载声明）与 `community`（社区讨论，仅作线索）；可拆解的 trust 特征与打分（源先验、实体核验、新鲜度、来源方式） | `src/models.py`、`src/corpus/sections.py`、`src/corpus/trust.py` |
| **证据语料库与重放账本** | SQLite + FTS5 + SimHash 聚簇；公开网页与调查资料按批落日志、建立对象/版本与观察记录，崩溃重启后支持重放恢复 | `src/corpus/store.py`、`periscope/collection/journal/` |
| **原子声明核查** | 高分条目蒸馏为原子声明，关联证据库、计算独立信源计数（Noisy-OR 聚合）、矛盾判定（`contested`） | `src/analysis/claims.py`、`src/corpus/citations.py` |
| **智能画像日报** | 4 种内置 Profile、分类配额、双语生成；支持邮件、Webhook（飞书/钉钉/Slack/Discord）与微信投递 | `src/ai/`、`src/orchestrator.py`、`src/services/` |
| **三端工具链集成** | 包含 7 个 Console Scripts 命令行工具；FastAPI 三栏式 Web 面板；标准 stdio MCP 服务（注册 36 个工具） | `periscope/cli/`、`src/web/`、`src/mcp/` |

### ⚠️ 能力已实现，效果未主张

**声明核查层的「评级准确率」是整个项目唯一一块"做完了但不敢声称效果"的地方。**

判定一条声明是 supported / contested / unsupported，需要一个经过校准的阈值。这一步需要 **50–100 条人工标注** 才能报出 macro-F1、并按独立信源数分桶算一致率（工具已就位：`scripts/eval_claims.py --export/--score`），而**目前标注数据为零**。

因此在拿到标注之前：
- 阈值是**手工先验**，不是校准结果；
- 分诊门 `triage_min_trust` **默认为 0，即关闭**；
- 下面所有关于 trust 与阈值的描述，都**不得被读成已验证的准确率提升**。详见 [CHANGELOG.md](CHANGELOG.md)。

### ❌ 明确不做

这些边界由 `tests/test_spec_exclusions.py` 等测试以代码方式严格钉住：

- **不做验证码打码，不做请求签名逆向**。遇到验证码直接标记为 `blocked_captcha` 并停下，绝不尝试绕过。
- **不做账号池**（多账号轮询换稳定性）。
- **不自动突破未授权访问限制**。用户未授权的内容不强行抓取；取不到的源仍走[用户导出通路](#取不到的源靠导出)降级。
- **不加 SSE / WebSocket / 流式传输**。轮询与标准请求-响应足够保证实时与恢复。
- **语义向量检索永不默认开启**：需要 `retrieval.semantic` 与 `retrieval.embedding_model` 两个条件同时配置成立。
- **图文（OCR / VLM）自动提取尚未接入**：图片仅保留 URL 元数据，未打通自动图像 OCR 提取为正文的检索流。

## 核心系统能力架构

```
                             ┌──────────────────────────────────────────────┐
                             │               用户入口层 (Tri-Modal)          │
                             │  Web 深度调查面板 │ CLI 工具链 │ MCP 36 原生工具 │
                             └──────────────────────┬───────────────────────┘
                                                    │
                                ┌───────────────────┴───────────────────┐
                                ▼                                       ▼
                    【实时深度调查流水线】                   【智能日报与全网聚合】
                    periscope-topic / web                 periscope --hours 24
                                │                                       │
     ┌──────────────────────────┴──────────────────────────┐            │
     ▼                                                     ▼            ▼
┌─────────────────────────┐                             ┌─────────────────────────┐
│     多源混合检索与直采    │                             │       Profile 画像编排   │
│ ├─ 免Key公网搜索(DuckDuckGo)│                            │ ├─ tech-news / blog     │
│ ├─ 12+平台深层讨论回复树 │                             │ ├─ ai-creator / finance │
│ ├─ 自主爬虫 & 表单跟进   │                             │ └─ 理由评分 / 主题去重  │
│ └─ DOCX / XLSX / PDF 解析│                             └──────────────┬──────────┘
└────────────┬────────────┘                                            │
             │                                                         │
             ▼                                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           核心可信证据语料库 (corpus.db)                          │
│  ├─ 证据分层: Primary (作者亲写层) vs Community (社区发言层)                    │
│  ├─ 信源可信度模型: Trust Prior + 独立信源 Noisy-OR 计数                        │
│  ├─ 版本化日志与重放: Evidence Journal & Versioned Ledger                       │
│  └─ SQLite + FTS5 全文索引 + SimHash 近似聚簇                                   │
└────────────────────────────────────────┬────────────────────────────────────────┘
                                         │
                                         ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           核查与带版本引用输出层                                │
│  ├─ 原子声明提取 & 矛盾判定 (Supported / Contested / Unsupported)               │
│  ├─ 固定证据版本锚点反解 (Fixed Version Citations)                              │
│  └─ 邮件 / 飞书 / 钉钉 / Slack / Discord / 微信 多渠道投递                      │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## 四大工作模式

### 1. 深度话题调查模式（Interactive Topic Investigation）
围绕指定的研究议题，并发利用免 Key 公网搜索与平台深采，沉淀版本化证据，生成带固定版本引用的事实回答：
```bash
uv run periscope-topic --topic "固态电池量产进度" --speed normal
```
* 自动清洗长句查询，中英术语双向拓展；
* 支持 `--crawl` 自动跟进页面及附件；
* 结果保存为版本化观察记录，支持跨轮次复用高可信证据。

终端执行示例与时序分解：
```text
>>> 查询话题: 固态电池量产进度
[1/3 检索层] 公网搜索 18 条 / 3 问法 / 0.9s (DuckDuckGo 免Key)
[2/3 直采层] 平台 24 条 / 1.8s (知乎 6 条 + V2EX 讨论树 4 条 + HN 2 条)
[3/3 附件层] 爬虫跟进发现 1 个 PDF 公告附件 (已提取表格与正文，状态 complete)
>>> 证据落库: 新增 42 条条目入库 (已归入 corpus.db，重放日志记录 batch_id=74)
>>> 生成回答:
根据工信部最新公告 [v1.2#1] 及行业代表企业披露，半固态电池已进入示范装车阶段...
但关于全固态装车时间，机构预测 [v1.2#4] 与社区工程师讨论 [v1.2#7] 存在分歧 (contested)...
```

### 2. 每日智能简报模式（Daily Digest & Ingestion）
周期性拉取订阅源并评分沉淀：
```bash
uv run periscope --hours 24
```
* **阶段时序**：并发采集 → 跨源 URL 规范化去重 → Profile 评分 → 阈值与主题过滤 → AI 富化多语言产物 → 声明蒸馏与核查 → 渲染多语言 Markdown 日报并投递。

### 3. Web 交互式调查与证据面板（Web Workspace）
提供直观的三栏式操作界面：
```bash
uv run periscope-web --data-dir data
```
* **证据库**：全文检索与条目审查；
* **深度调查专区**：发起新调查、设定公开站点范围、实时进度展示、追问与停止、固定版本证据引用回查；
* **核查台**：按 verdict 分组查看声明与证据支持链。

### 4. 智能体 MCP 协议服务（Agent Integration）
通过标准 stdio 协议提供 36 个原生工具，供外部智能体驱动：
```bash
uv run periscope-mcp
```

## 三条核心支撑机制

### 1. 证据分层：类型化字段，绝不靠字符串约定
抓取器将文本显式**声明**为类型化 `Section`：
- **`primary`**：作者亲写层，唯一可承载事实声明与证据引用的层级；
- **`community`**：评论、楼层回复、人群讨论，仅作为线索保留，绝不计入独立证据。
分层通过 `Section.tier` 强约束，`src/scrapers/` 中严禁出现标记字面量。

### 2. 条目可信度与独立性计算
每条入库内容计算可拆解的透明信任分：
$$\text{Trust} = \sigma(\text{源先验}, \text{作者等级}, \text{交叉支持}, \text{可核验实体}, \text{新鲜度}, \text{来源方式} - \text{模板度})$$
重复条目先按 SimHash 簇折叠，再数不同的 `(source_type, publisher)`；使用 Noisy-OR 聚合避免无限刷分。

### 3. 自适应取证加宽
当子问题未检索到充足证据时，按免费到付费的固定阶梯加宽：
$$\text{baseline} \rightarrow \text{widen\_terms} \rightarrow \text{switch\_source\_family} \rightarrow \text{rewrite\_query} \rightarrow \text{collect\_keywords}$$

### 取不到的源，靠导出
对于未适配或触发反爬保护的源，提供用户导出降级通路：
```bash
# 校验格式
uv run python scripts/import_corpus.py --file data/export.example.json --data-dir data --dry-run
# 确认后入库
uv run python scripts/import_corpus.py --file data/export.example.json --data-dir data
```

## 入口工具链与 Console Scripts

项目统一在 `pyproject.toml` 中注册了 7 个命令行命令，全部开箱即用：

| 命令 | 核心作用 | 关键参数示例 |
|---|---|---|
| `periscope` | 主流水线运行（采集、分析、简报、投递） | `uv run periscope --hours 24` |
| `periscope-topic` | 话题检索与深采调查 CLI | `uv run periscope-topic --topic "人形机器人" --speed normal` |
| `periscope-web` | 启动本地 Web 面板（三栏界面与深度调查工作区） | `uv run periscope-web --data-dir data` |
| `periscope-mcp` | 启动标准 stdio MCP 协议服务（注册 36 个工具） | `uv run periscope-mcp` |
| `periscope-wizard` | 交互式生成配置向导 | `uv run periscope-wizard` |
| `periscope-webhook` | 预览或重放 Webhook 投递消息 | `uv run periscope-webhook --dry-run` |
| `periscope-wechat` | 微信机器人登录状态与投递测试 | `uv run periscope-wechat status` |

## 支持的 AI 提供商

默认使用免费的 **Agnes** 层，可配置备用链 `provider_chain` 实现故障自动切换：

| 提供商 | 默认模型 | API Key 环境变量 |
|---|---|---|
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

所有 LLM 请求均经过本地持久化缓存（`data/llm_cache.db`），崩溃重试与重复提示词均为零调用。

## 支持的数据源与深采矩阵

### 1. 深度采集平台与专项适配（12+）
- **Hacker News**：关键词话题发现、热门帖子、嵌套回复树及父子关系重建；
- **GitHub**：Issue / PR 详情（至多 30,000 字）与评论树并行采集；
- **V2EX**：节点主题、热门帖、嵌套回复树与版本覆盖状态记录；
- **Stack Overflow**：技术问答正文与高票答案深采；
- **Bilibili**：热门视频信息与评论，支持提取 CC 字幕文本；
- **知乎**：公开问答回答及有限子评论页展开采集；
- **微博**：博文与独立回复分页采集，支持乱序回复树合并；
- **小红书**：基于持久化浏览器会话展开笔记正文与子评论页；
- **抖音**：滚动加载根评论与回复分页控件触发；
- **CSDN / 掘金**：技术文章与评论提取；
- **快手**：检索探测与验证码状态识别感知。

### 2. 标准资讯与订阅数据源
- **RSS / Atom**：全网任意标准 RSS/Atom 源，带全文正文抽取；
- **Reddit**：子版块热门与评论；
- **Twitter / X**：Apify 抓取或 Playwright + Cookie 免 token 模式（详见 [Twitter Cookies 配置](docs/twitter-cookies.md)）；
- **OpenBB / GDELT / Google News / Telegram / YouTube**：财经、新闻与官方流。

礼貌抓取规范：Per-host 令牌桶限流 + 抖动 + 429 自动指数退避重试。

## 投递渠道

- **邮件订阅**：SMTP 发送多语言 Markdown 简报，IMAP 监听订阅退订；
- **Webhook 通知**：支持推送到飞书、钉钉、Slack、Discord 或自定义 Webhook；
- **微信通知**：iLink Bot 扫码登录会话发送。

## 快速开始

### 1. 安装

```bash
git clone https://atomgit.com/NLY22/periscope.git
cd periscope

# 推荐使用 uv 安装（要求 Python 3.11+）
uv sync

# 需要开发与完整测试依赖时
uv sync --extra dev

# 可选额外支持：openbb（财经源）、twitter（Playwright）
uv sync --extra openbb
uv sync --extra twitter
```

### 2. 配置

推荐通过交互式向导快速初始化：
```bash
uv run periscope-wizard
```
或复制配置样例：
```bash
cp .env.example .env
cp data/config.example.json data/config.json
```

### 3. Docker 部署

```bash
# 构建镜像
docker compose build --build-arg EXTRAS=openbb periscope-collect

# 运行一次采集器
docker compose run --rm periscope-collect --hours 24

# 后台启动 Web 面板（访问 http://localhost:8790）
docker compose up -d periscope-web
```

关于定时任务：项目适配 `cron` 或 `systemd timer`。仓库中的 `.github/workflows/tests.yml` 使用 GitHub 语法，在部分国内平台（如 AtomGit）上不执行，仅供在支持 GitHub Actions 的平台上跑 CI。

## 配置

完整参数说明请参阅 [配置指南](docs/configuration.md)。核心配置块概览：

| 配置块 | 作用 |
|---|---|
| `ai` | 提供商、模型、环境变量名、链式回退 `provider_chain`、并发与限流 |
| `sources` | 数据源列表、分类与关联 Profile |
| `collection` | 默认时间窗口 `time_window_hours` |
| `digest` | 简报容量 `max_items` 与分类配额 |
| `processing` | Profile 路径 `profiles_dir`、默认画像 `default_profile`（默认为 `tech-news`）与单画像偏好设置 |
| `display` | 图标风格 `icon_style`（可选 `emoji` / `nerd` / `ascii`） |
| `extractors` | 正文抽取器配置（基于 trafilatura） |
| `email` / `webhook` / `wechat` | 三大通知投递通道 |
| `corpus` | 语料库存储路径（默认 `data` 目录）、FTS5 与聚簇参数 |
| `analysis` | 原子声明提取、证据数量与核查分诊门阈值 |
| `research` | 长会话多轮研究预算与骨架模板 |
| `retrieval` | 检索扩展策略、语义向量腿与自适应加宽参数 |

## 项目结构

```
periscope/
├── periscope/                # 现代核心业务架构
│   ├── cli/topic.py          # periscope-topic 命令适配
│   ├── collection/           # 采集架构：catalog/、crawling/、journal/、transport/
│   ├── investigation/        # 深度调查与版本化证据重放引擎
│   ├── runtime/              # 跨平台环境与路径管理
│   ├── search/               # 公网免Key检索、清洗、跨语言桥与正文补全
│   └── topics/               # 话题检索多层编排 (layers/service)
├── src/                      # 既有功能与证据内核
│   ├── main.py               # periscope 主入口
│   ├── _cli.py               # 共享 CLI 参数 (--data-dir, --config, --log-level)
│   ├── orchestrator.py       # 日报流水线编排
│   ├── models.py             # Section, ContentItem 与 SOURCE_SPECS
│   ├── scrapers/             # 传统数据源抓取器
│   ├── sources/              # 数据源注册表与探针
│   ├── ai/                   # AI 客户端抽象、缓存与总结
│   ├── corpus/               # 证据语料库、SimHash、Trust 模型与取证检索
│   ├── analysis/             # 声明蒸馏与核查
│   ├── research/             # 多轮共创长会话 (moves / drafts)
│   ├── web/                  # Web 面板 (FastAPI + 三栏式工作区)
│   └── mcp/                  # MCP 服务 (36 个原生工具)
├── profiles/                 # 内置 Profile (tech-news / tech-blog / ai-creator / finance-news)
├── docs/                     # 20 篇详细工程文档与设计规范
├── tests/                    # 完整测试套件与文档自动化护栏
├── data/                     # 数据目录 (SQLite 数据库、配置与缓存)
├── scripts/                  # 导入、评测与探针辅助工具
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## 开发与测试

```bash
# 安装完整依赖
uv sync --extra dev

# 运行全量单元测试与护栏测试
uv run pytest

# 运行可达性探针（以小红书和贴吧为例）
uv run python scripts/spike_sources.py --source xiaohongshu --url <公开笔记页 URL> --online
uv run python scripts/spike_sources.py --source tieba --kw <吧名或关键词> --online

# 运行检索消融评测
uv run python scripts/eval_retrieval.py
```

### 自动化文档护栏
项目内置 `tests/test_docs_match_code.py`，确保文档中的承诺与代码现实严格对齐：
- 严格验证 README 中标注的 MCP 工具数量与服务器注册完全一致（36 个工具）；
- 校验所有命令行脚本、文档链接与 CLI 参数的有效性；
- 检查配置指南默认值与 Pydantic 模型逐格一致。

## 安全与可靠性

- **严防 SSRF**：抓取与正文抽取前强制校验 URL scheme、端口与公网 IP，禁止访问内网保留地址；
- **防路径逃逸**：写入数据与产物前严格校验限定于指定目录内；
- **敏感凭据脱敏**：MCP 输出与 Webhook 日志自动对 token/secret/password 字段脱敏；
- **优雅降级而非崩溃**：单源失败不阻断流水线；模型断网时不编造结论，明确报告证据状态。

## 文档

| 指南 | 说明 |
|---|---|
| [文档入口](docs/README.md) | 整体文档架构与阅读引导 |
| [配置指南](docs/configuration.md) | AI 模型、多数据源、投递渠道与全部证据层参数参考 |
| [代码组织说明](docs/code-organization.md) | `periscope/` 与 `src/` 代码模块分层规划 |
| [架构图](docs/architecture.md) | 广源取证通路、多轮调查状态机与调用时序图 |
| [取证检索与证据分层](docs/retrieval.md) | 类型化分层机制、Trust 独立性计算与自适应加宽 |
| [采集链路与边界](docs/collection-pipeline.md) | 抓取、清洗、去重与附件提取机制 |
| [12 平台深采验收](docs/platform-deep-collection-matrix.md) | 12 个重点平台的讨论与回复树抓取能力矩阵 |
| [爬虫使用与边界](docs/native-crawler.md) | 自主爬虫、Sitemap、GET 表单与 DOCX/XLSX/PDF 附件抽取规范 |
| [信息源体系与边界](docs/source-system.md) | 统一注册的来源配置、分类与在线可用性 |
| [功能审计](docs/project-audit-2026-10-22.md) | 既有功能实现与质量盘点 |
| [处理画像](docs/profiles.md) | Profile 定制、路由规则与富化 block |
| [评分机制](docs/scoring.md) | 条目重要性与质量评估模型 |
| [抓取器实现](docs/scrapers.md) | 传统抓取器编写与维护指南 |
| [正文抽取](docs/extractors.md) | 基于 trafilatura 的非结构化网页正文提取 |
| [检索与取证评测](docs/evaluation.md) | 评测基准、消融实验与指标口径 |
| [自助测试手册](docs/selftest.md) | 本地端到端手工验证与故障排查脚本 |
| [Twitter Cookies 配置](docs/twitter-cookies.md) | 免 Token 抓取推文的会话配置 |
| [地基调研](docs/foundation-survey-2026-10-19.md) | 业内方案与上位项目横向技术对比 |
| [框架记录](docs/foundation.md) | 数据模型、演进历史与关键决策日志 |
| [重构记录](docs/REFACTOR_2026-10-18.md) | 历史架构重构全过程备忘 |

## 项目状态

**尚未正式发布 1.0 版本**：`pyproject.toml` 当前为 `0.1.0`。

目前已合入 `main` 的能力包括：免 Key 公网检索、12 平台深层讨论抓取、DOCX/XLSX/PDF 附件解析、证据版本日志、Web 深度调查工作区、原子声明核查、多轮研究草稿保护、双语简报生成、三渠道投递与 MCP 36 工具协议集成。

后续工作重点：
1. **声明 verdict 的 50–100 条人工校准标注**：通过 `scripts/eval_claims.py` 测定准确率并校准两道门阈值；
2. **自动化图文（OCR / VLM）文本通路**：在保证断言严肃性的前提下实测打通图像正文抽取；
3. **扩展更多信源与发布包制作**：支持 PyPI 发布与更多现代知识平台接入。

## 贡献

欢迎参与共建！Issue 与 PR 请提交至 <https://atomgit.com/NLY22/periscope>。
请遵守 [CONTRIBUTING.md](CONTRIBUTING.md)、[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 与 [SECURITY.md](SECURITY.md)。在新增功能时，请确保本地通过 `uv run pytest`，并同步更新相关的文档与测试护栏。

## 许可证

[MIT](LICENSE)

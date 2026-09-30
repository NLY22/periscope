<div align="center">
<h1>🌅 Periscope</h1>

<p><strong>把广而杂的信息源，变成可核查的证据库</strong></p>

<p><sub>上游 Horizon 的口号是「享受新闻本身，其余交给 Periscope」；本 fork 多做一步 —— 除了帮你读，还要说清每句话出自谁、有几家独立支撑、哪里互相矛盾。</sub></p>

> ⚠️ **本仓库是 fork（`NLY22/periscope`），不是上游。** 下面的 Trendshift / HelloGitHub 徽章、在线演示站点、QQ 群与赞助位都属于上游项目；本 fork 独有的能力见[能力对照](#本-fork-与上游的能力对照)，协作请在本仓库开 issue / PR。

<a href="https://trendshift.io/repositories/22864?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-22864" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/22864/daily" alt="Periscope | Trendshift" width="250" height="55"/></a>
<a href="https://trendshift.io/repositories/22864?utm_source=trendshift-badge&amp;utm_medium=badge&amp;utm_campaign=badge-trendshift-22864" target="_blank" rel="noopener noreferrer"><img src="https://trendshift.io/api/badge/trendshift/repositories/22864/weekly?language=Python" alt="Periscope | Trendshift" width="250" height="55"/></a>
<a href="https://hellogithub.com/repository/Thysrael/Periscope" target="_blank"><img src="https://abroad.hellogithub.com/v1/widgets/recommend.svg?rid=7a4b606e28e4477998d35851cf4fdddf&claim_uid=rtjnLkYT7ziQJUG" alt="Featured｜HelloGitHub" style="width: 250px; height: 54px;" width="250" height="54" /></a>
<br>

[![License](https://img.shields.io/badge/license-MIT-green.svg?style=for-the-badge&logo=opensourceinitiative&logoColor=white)](LICENSE)
[![Tool uv](https://img.shields.io/badge/Tool-uv-4B275F?style=for-the-badge&logo=uv&logoColor=white)](https://github.com/astral-sh/uv)
[![Repo](https://img.shields.io/badge/Repo-AtomGit-263238?style=for-the-badge&logo=git&logoColor=white)](https://atomgit.com/NLY22/periscope)
[![Fork of](https://img.shields.io/badge/fork%20of-Thysrael%2FHorizon-orange?style=for-the-badge&logo=github&logoColor=white)](https://github.com/Thysrael/Horizon)
![Status](https://img.shields.io/badge/status-Phase%20F1%E2%80%93F6%20已合并%20main-2ea44f?style=for-the-badge)
[![Commits](https://img.shields.io/badge/Commits-main-blue?style=for-the-badge&logo=git&logoColor=white)](https://atomgit.com/NLY22/periscope/commits/main)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg?style=for-the-badge&logo=git&logoColor=white)](https://atomgit.com/NLY22/periscope/pulls)
![Sources Welcome](https://img.shields.io/badge/sources-welcome-f97316?style=for-the-badge&logo=rss&logoColor=white)

![Claude](https://img.shields.io/badge/Claude-f0daba?style=flat-square&logo=anthropic&logoColor=black)
![GPT](https://img.shields.io/badge/GPT-10A37F?style=flat-square&logo=openai&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-8E75B2?style=flat-square&logo=googlegemini&logoColor=white)
![DeepSeek](https://img.shields.io/badge/DeepSeek-0A6DC2?style=flat-square&logo=deepseek&logoColor=white)
![Doubao](https://img.shields.io/badge/Doubao-00D6C2?style=flat-square&logo=bytedance&logoColor=white)
![MiniMax](https://img.shields.io/badge/MiniMax-FF6F00?style=flat-square&logo=minimax&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-FFFFFF?style=flat-square&logo=Ollama&logoColor=black)

📡 你专属的 AI 新闻雷达：聚合多源信息，自动筛选、去重、富化，生成中英双语每日简报，并把每一天的知识沉淀成可检索、可核查、可研究的证据库。

📖 在线演示（**上游站点**，非本 fork 部署） · [📋 配置指南](docs/configuration.md) · [🧩 Profile 定制](docs/profiles.md) · [🔎 取证检索机制](docs/retrieval.md) · [📊 评测](docs/evaluation.md)

</div>

---

## 目录

- [先说清楚：这是 fork，不是上游](#先说清楚这是-fork不是上游)
- [简介](#简介)
- [本 fork 与上游的能力对照](#本-fork-与上游的能力对照)
- [核心特性](#核心特性)
- [截图](#截图)
- [工作原理](#工作原理)
- [本 fork 的独有层次](#本-fork-的独有层次)
- [快速开始](#快速开始)
- [支持的 AI 提供商](#支持的-ai-提供商)
- [支持的数据源](#支持的数据源)
- [投递渠道](#投递渠道)
- [进阶用法](#进阶用法)
- [配置参考](#配置参考)
- [项目结构](#项目结构)
- [开发与测试](#开发与测试)
- [安全与可靠性](#安全与可靠性)
- [文档](#文档)
- [项目状态](#项目状态)
- [贡献](#贡献)
- [社区](#社区)
- [赞助支持](#赞助支持)
- [致谢](#致谢)
- [许可证](#许可证)

## 先说清楚：这是 fork，不是上游

| | 上游 | 本仓库 |
|---|---|---|
| 项目 | [Thysrael/Horizon](https://github.com/Thysrael/Horizon)（上游后期也把项目改名为 Periscope） | `NLY22/periscope`，Horizon 的 fork |
| 回答的问题 | 今天有什么值得读 | 这个说法**站不站得住**，以及围绕它如何做长时研究 |
| 来源面 | 以搜索引擎可索引的内容为主 | 加上论坛、视频描述、CC 字幕、评论区 |
| 记忆 | 每次运行独立，次日即忘 | `corpus.db` 持久证据库，跨运行、跨入口累积 |
| 结论可核性 | AI 评分与摘要 | 声明级核查 + 独立信源计数 + 引用反解核验 |
| 维护与协作 | 上游作者的渠道 | <https://atomgit.com/NLY22/periscope> 的 issue / PR |

> **本页面上的 Trendshift、HelloGitHub、LINUX.DO、小红书徽章，在线演示 `thysrael.github.io`，QQ 群、`periscope1123.top` 与 `thysrael@163.com` / `thysrael@gmail.com`（后者是上游的安全披露与行为准则执行邮箱）以及三家赞助位，全部属于上游项目。** 本 fork 不经这些渠道分发，也不为其内容负责；本 fork 的问题请开在本仓库，安全问题是私下联系本仓库维护者（见 [SECURITY.md](SECURITY.md)）。下文用 `（上游）` / `（本 fork）` 标注每条能力的出处。

## 简介

好的内容散落在订阅源、论坛与时间线里，而你的注意力是有限的。**Periscope 会替你收集、筛选、去重**，再把真正值得一读的内容连同背景与社区讨论一起送到你面前。

你的品味决定了你读什么，也决定了你希望从中得到什么。一篇新闻报道需要回答「为什么重要」，一篇工程深度长文需要回答「我能用上什么」。Periscope 的 **Profile（画像）** 为每一类内容定义各自的评分标准与输出形式，让简报读起来像是为你手工挑选的。

Periscope 是 [Horizon](https://github.com/Thysrael/Horizon) 的 fork。上游只回答「今天有什么值得读」，到了第二天就遗忘；本 fork 在此之上增加了**证据语料库、声明级核查与多轮共创研究**三项核心能力，并提供 **Web 面板**与扩展的 **MCP**（27 个工具）入口，让知识能够跨运行累积；再往下是三项支撑机制——**证据分层**、**条目可信度**与**自适应取证**，它们决定了前三项在噪声里是否真的站得住。详见[本 fork 的独有层次](#本-fork-的独有层次)。

## 本 fork 与上游的能力对照

| 能力 | 出处 | 落在哪 |
|---|---|---|
| 多源聚合、Profile 评分、双语日报、邮件 / Webhook / 微信投递、配置向导 | （上游） | `src/scrapers`（四个新源除外）、`src/processing`、`src/services`、`src/setup` |
| Bilibili / V2EX / Discourse / YouTube 四个源，含 B 站 CC 字幕层 | （本 fork） | `src/scrapers/{bilibili,v2ex,discourse,youtube}.py` |
| 证据语料库：SQLite + FTS5 + 手写 SimHash 聚簇，跨运行累积 | （本 fork） | `src/corpus/store.py`、`src/corpus/simhash.py` |
| 用户导出入库通路（取不到的源不靠抓取）| （本 fork） | `src/corpus/ingest.py`、`scripts/import_corpus.py`、`hz_corpus_import`、`POST /api/import` |
| 证据分层：scraper **声明**的类型化 `Section`（作者亲写 vs 人群发言），独立信源计数只认前者 | （本 fork） | `src/models.py` 的 `Section`、`src/corpus/sections.py`、`items.claimable` + `claim_fts` |
| 条目可信度与独立性：可拆解的 trust 分数、noisy-OR 聚合、两道门 | （本 fork） | `src/corpus/trust.py`、`items.trust` + `trust_features_json` |
| 多轮共创：逐轮动词 + 带 revision/locked/stale 的草稿工件 + 向用户索取输入 | （本 fork） | `src/research/{moves,drafts}.py`、`research_drafts` / `research_requests` 表 |
| 源注册表与抓取基础设施：per-host 令牌桶、可注入时钟、env/cookie 鉴权与过期检测 | （本 fork） | `src/sources/registry.py`、`src/scrapers/{throttle,auth}.py`、`SOURCE_SPECS` |
| 取证检索：查询扩展 + 向量路 + RRF 融合 | （本 fork） | `src/corpus/retrieval.py`、`src/ai/{expand,embeddings}.py`、`src/corpus/semantic.py` |
| 声明级核查 + 评级一致率工具 | （本 fork） | `src/analysis/{claims,agreement}.py`、`scripts/eval_claims.py` |
| 长会话研究：子问题树、崩溃续跑、缺证时自适应加宽 | （本 fork） | `src/research/`、`research_actions` 表 |
| 报告骨架（背景调查 / 市场调研 / 方法探索）+ 引用反解核验 | （本 fork） | `src/research/templates.py`、`src/corpus/citations.py` |
| 检索消融评测（Recall / Precision / nDCG / MRR） | （本 fork） | `src/corpus/metrics.py`、`scripts/eval_retrieval.py`、`docs/evaluation.md` |
| Web 面板 | （本 fork） | `src/web/`（上游无） |

**这个 fork 的立场一句话**：把来源放宽到论坛与流媒体，信息质量必然下降；全部工作在于让"降下去的质量"被分层、检索、计数与引用核验**重新补回来**，并用消融表说明补回了多少。

## 核心特性

下面每条标明出处，便于与上游对比：

- **📡 聚合你的信息源（上游 + 本 fork）** — RSS、Hacker News、Reddit、Telegram、X、GitHub、财经资讯等 10 类来自上游；**Bilibili、V2EX、Discourse、YouTube 四类及 B 站 CC 字幕层是本 fork 加的**。
- **🎯 判断什么值得读** — 用 Profile 定义评分细则，为每个 Profile 设置阈值，并在同一 Profile 内合并重复报道。
- **🧩 为每篇内容定制处理方式** — 通过 Markdown 提示词与 JSON block 定义，自由组合摘要、背景、解决方案或要点。
- **💬 不止于标题** — 在有助于解释事件时，自动补充联网检索到的背景与社区讨论。
- **⚖️ 兼顾你的所有兴趣** — 限制简报总长度与各分类占比，避免某一热门话题挤占其余内容。
- **📬 在你习惯的地方阅读** — 生成中英双语 Markdown 简报，发布到 Pages，或通过邮件、Webhook、微信投递。
- **🧠 让每一天都可积累（本 fork）** — 所有采集过的内容进入证据语料库，可全文检索、做声明核查，并作为长会话研究的素材；**证据分层**保证评论与回复只作线索，不充当信源。
- **🧯 诚实的降级（本 fork）** — 没有 LLM key 也能运行：语料照常增长、证据照常确定性关联，报告会如实标注「尚未回答」而不是编造内容。
- **🔍 找不到就换打法（本 fork）** — 一个子问题不再只有一次查询：放宽词条、换来源族、让模型改写问法，每步写入 `research_actions`，报告尾部列出「取证尝试」，把"语料里没有"和"问法不对"分开。
- **✅ 引用可自查（本 fork）** — 成品报告可被反向核验：幽灵引用、指向不存在条目、只靠人群发言支撑的引用，都会被点名（`src/corpus/citations.py`）。

### 一份简报，多种读法

Profile 是一套可复用的编辑规则：**什么内容该收录、什么值得保留、该写成什么样。** 内置示例如下：

| 你关注的内容 | Profile | 你将得到 |
|---|---|---|
| 科技新闻 | `tech-news` | 事件与背景，必要时附影响分析与社区讨论 |
| 工程深度长文 | `tech-blog` | 背景、解决方案与可落地的要点 |
| AI 创作者素材 | `ai-creator` | 摘要，必要时附热点切入角度与内容思路 |
| 财经资讯 | `finance-news` | 公司动态与市场脉络 |

你可以为某个信息源指定 Profile，也可以让 AI 自动选择。想要不一样的风格？通常无需改 Python，直接改编现有 Profile 即可。[定制你的 Profile →](docs/profiles.md)

## 截图

<table>
<tr>
<td width="50%">
<p align="center"><strong>按评分排序的每日简报</strong></p>
<img src="docs/assets/overview_zh.png" alt="每日总览" />
</td>
<td width="50%">
<p align="center"><strong>背景、摘要与讨论</strong></p>
<img src="docs/assets/one_news_zh.png" alt="新闻详情" />
</td>
</tr>
</table>

<details>
<summary><strong>更多截图</strong></summary>
<br>
<table>
<tr>
<td width="50%" valign="top">
<p align="center"><strong>终端输出</strong></p>
<img src="docs/assets/terminal_log.png" alt="终端输出" />
</td>
<td width="50%" valign="top">
<p align="center"><strong>飞书通知</strong></p>
<img src="docs/assets/feishu_zh.png" alt="飞书通知" />
</td>
</tr>
<tr>
<td width="50%" valign="top">
<p align="center"><strong>邮件投递</strong></p>
<img src="docs/assets/email.png" alt="邮件投递" />
</td>
<td width="50%" valign="top">
<p align="center"><strong>微信投递</strong></p>
<img src="docs/assets/wechat.jpg" alt="Periscope 简报的微信页头、总览与详情" />
</td>
</tr>
</table>
</details>

## 工作原理

![Periscope 架构：十余个信息源汇入 Profile 驱动的流水线，结合历史与联网检索工具，投递到 Markdown、Pages、邮件、微信与 Webhook。](docs/assets/architecture.svg)

[可编辑的 OmniGraffle 源文件](docs/assets/architecture.graffle)

**Profile 决定处理方式，运行期配置反映你的阅读偏好。** 每个条目只会路由到一个 Profile。分析、过滤与去重在富化之前完成，随后入选条目按 Profile 分组形成简报。

一次完整运行（`periscope --hours 24`）的阶段顺序如下：

1. **采集** — 并发抓取所有已启用数据源，每个源独立成败，结果同时写入证据语料库并重算近似重复聚簇。
2. **跨源去重** — 规范化 URL（剥离 `utm_*`、`gclid` 等跟踪参数），合并指向同一内容的多源条目。
3. **分析** — 分类路由到 Profile，由 AI 完成评分、理由、摘要与标签。
4. **选择与过滤** — Profile 阈值过滤 → AI 主题去重 → 均衡配额（`digest` 配置）。
5. **富化** — 第二遍 AI，按 Profile 定义的 block 生成多语言产物，可调用联网检索与历史检索工具。
6. **声明核查** — 将高分条目蒸馏为原子声明，关联语料证据并评级（本 fork 新增，尽力而为）。
7. **摘要与投递** — 程序化渲染多语言 Markdown 日报，保存到 `data/summaries/`，并按配置发布或投递。

你可以通过 CLI 运行完整流水线，也可以让 AI 助手经由 [MCP](src/mcp/README.md) 调用其中的各个阶段。

## 本 fork 的独有层次

上游 Horizon 只回答「今天有什么值得读」，并且到了第二天就遗忘。本 fork 在此基础上做了八项增强：

1. **证据语料库（`corpus.db`）** — 所有曾经采集过的条目都会带 SimHash 指纹与近似重复聚簇被持久化存储，可用 SQLite FTS5（对 CJK 友好）检索。知识会跨运行累积，而不是在生成摘要后就蒸发。
2. **声明级正确性核查** — 高分条目会被蒸馏为原子化、可核查的声明；每条声明都与语料证据关联并评级（supported / contested / unsupported + 置信度），评级每轮有预算上限。`contested` 不再只是一个标签：`claim_contradictions` 会记下是哪两条声明、靠哪些条目互相冲突。
3. **长会话研究** — 提出一个问题，会得到一棵分解后的子问题树，逐题对照语料取证，并输出带引用的 Markdown 报告。追问会在同一会话上跨天、跨重启迭代（状态保存在 SQLite 中）。
4. **多轮共创（逐轮动词）** — 研究循环的顶层动词是 `AskUser | Rescope | Deepen | Finalize`。一轮 `step` 只重算被点名的分支，返回这一轮改了什么；报告是**带 revision 的草稿工件**，用户改过或锁定的章节在后续重算中**只被标记为陈旧、不会被覆盖**；缺输入时系统会主动提问并把会话停在 `awaiting_user`（没有可用 LLM 时回退只 `deepen`/`finalize`，不会连环追问）。
5. **处处诚实降级** — 没有 LLM key？语料照常增长，证据照常确定性关联，报告会写明*「尚未回答」*并附上已收集的证据，而不是编造内容。默认 LLM 是免费的 **Agnes** 层（`agnes-2.5-flash`），且每次调用都经过持久化响应缓存与限流，因此崩溃恢复运行与重复提示词都不消耗额外额度。
6. **证据分层（类型化字段，不靠字符串约定）** — 抓取器把评论、楼层回复、视频字幕**声明**为 `ContentItem.sections`：`primary`（作者亲写，可承载声明）与 `community`（人群发言，只能当线索），每段带 `author` / `provenance` / `locator`（可引用到具体一层）。声明只从前者蒸馏、证据只与前者关联、报告引用的也是前者。旧的标记反解只作为 `tiering="marker"` 的消融档与老库回填存在，`src/scrapers/` 里已不允许出现任何分层标记字面量。可用 `analysis.claimable_only` / `research.claimable_only` 关闭，用于对照。
7. **条目可信度与独立性** — 每条入库内容算一个**可拆解**的信任分：`σ(源先验, 作者等级, 交叉支持, 可核验实体, 新鲜度, 来源方式 − 模板度)`，**特征与分数一起落库**，"为什么这条被当成证据"随时答得出来。独立信源**先按簇折叠重复内容、再数不同的 `(source_type, publisher)`，解析不出发布者的条目不投票**；声明级聚合用 **noisy-OR** 而不是求和（求和没有上界，够多的低质源能把任何结论刷成 supported）。阈值 `θ` 是手工先验、分诊门默认关闭，等人工标注到位才谈校准。
8. **自适应取证加宽** — 一个子问题不再只有一次查询：先放宽词条，再换没查过的来源族，然后让模型改写查询，最后（显式开启时）用 GDELT / Google News 现采一轮。每次尝试都写进 `research_actions`，未回答的子问题会在报告里列出「取证尝试：动作(+新增条数)」，把"语料里确实没有"和"这次的问法没查到"区分开。

这些能力由 CLI、Web 面板与 MCP 三个入口共享同一份 `corpus.db`，因此会话与证据跨入口、跨重启都可见。各层的实际收益见[检索与取证评测](docs/evaluation.md)。

### 驱动它

```bash
# 终端：一次采集（fetch -> score -> digest -> corpus + claims）
uv run periscope --hours 24

# Web 面板：证据库 / 研究报告 / 核查台（http://localhost:8790）
uv run periscope-web --data-dir data

# MCP：面向任意 MCP 客户端的 27 个工具（hz_research_start、hz_research_step、hz_corpus_search 等）
uv run periscope-mcp

# 取不到的源：用户自己导出，按声明的层级入库（不联网、不碰验证码与签名）
uv run python scripts/import_corpus.py --file export.json --data-dir data --dry-run
uv run python scripts/import_corpus.py --file export.json --data-dir data
```

配置键：`corpus`、`analysis`、`research`（见 `data/config.example.json`）。把 `.env` 指向 `AGNES_API_KEY` 即可获得完整体验；不配置任何 key，其余功能也能运行。

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

Periscope 需要 **Python 3.11 及以上**（见 `pyproject.toml` 的 `requires-python`）。

`dev` 目前是 `pyproject.toml` 中的可选 extra，因此安装 pytest 等开发依赖请使用 `uv sync --extra dev`。

如果你想启用可选的 OpenBB 财经新闻源，还需要安装它的 extra：

```bash
uv sync --extra openbb
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
cp .env.example .env          # 填入你的 API Key
cp data/config.example.json data/config.json  # 定制你的信息源
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
      "tech-news": {
        "threshold": 7.0,
        "topic_dedup": true
      }
    }
  }
}
```

信息源显式指定 `profile` 时会直接使用该 Profile；省略或设为 `"auto"` 时，由 AI 将条目与所有可用 Profile 进行匹配；设为数组（如 `["tech-news", "finance-news"]`）则把 AI 匹配限定在这些 Profile 内。Profile 的结构与行为见[处理画像](docs/profiles.md)。诸如评分阈值、主题去重之类的**单 Profile 用户偏好**应写在 `processing.profile_settings`，而不是 Profile 文件里。

**均衡简报（可选）**

限制最终简报的规模，避免某一分类独占结果。分类来自信息源配置，例如 `sources.rss[].category`。

```jsonc
{
  "digest": {
    "max_items": 20,
    "category_groups": {
      "ai": {
        "limit": 5,
        "categories": ["ai-news", "ai-tools", "machine-learning"]
      },
      "finance": {
        "limit": 5,
        "categories": ["finance", "business", "equities"]
      }
    },
    "default_group": "other",
    "default_group_limit": 3
  }
}
```

分组限额在 Profile 过滤之后、富化之前生效。省略 `category_groups` 与 `max_items` 时，不施加均衡简报限制。

`api_key_env` 必须是**环境变量的名字**，而不是 API Key 本身。把真正的密钥放进 `.env`：

```bash
OPENAI_API_KEY=sk-your-key
```

使用 Gemini 时，改为 `GOOGLE_API_KEY`：

```jsonc
{
  "ai": {
    "provider": "gemini",
    "model": "gemini-2.0-flash",
    "api_key_env": "GOOGLE_API_KEY"
  }
}
```

`data/config.json` 中任意字符串值都可以用 `${VAR_NAME}` 引用环境变量，适用于 `ai.base_url`、私有 RSS 源地址、Webhook 端点或自定义请求头模板等。

完整参考见[配置指南](docs/configuration.md)。

### 3. 运行

**A. 本地安装**

```bash
uv run periscope [OPTIONS]
```

**B. Docker**

```bash
# 采集器，运行一次
docker compose run --rm periscope-collect --hours 24
# Web 面板，端口 8790
docker compose up -d periscope-web
```

| 选项 | 默认值 | 说明 |
|------|--------|------|
| `--hours N` | 取 `collection.time_window_hours`（示例配置为 24） | 抓取最近 N 小时的内容 |
| `-d`, `--data-dir PATH` | `data` | 数据目录路径 |
| `-c`, `--config PATH` | `<data-dir>/config.json` | 配置文件路径 |
| `-l`, `--log-level LEVEL` | `WARNING` | 日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL） |

`--data-dir` 会改变状态目录，包括摘要、订阅者以及默认配置位置；`--config` 只改变配置文件。生成的报告保存在 `data/summaries/`（若设置了 `--data-dir` 则为 `<data-dir>/summaries/`）。两者组合使用及自定义配置位置的初始化方式见[配置路径](docs/configuration.md#configuration-paths)。

### 4. 自动化（可选）

Periscope 适合用系统定时器调度，例如 `cron` 或 `systemd timer`；Docker Compose 也可直接配合定时任务使用。若将其托管在 GitHub，仓库内提供了[每日工作流模板](.github/workflows/daily-summary.yml.disabled)（本仓库中处于禁用状态），配置好后重命名为 `daily-summary.yml` 即可启用。

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

## 投递渠道

Periscope 可以通过多种方式发布或投递生成的简报：

| 渠道 | 作用 |
|------|------|
| **GitHub Pages 每日站点** | 把生成的 Markdown 复制到 `docs/`，由 GitHub Pages 发布每日更新的简报站点 |
| **邮件订阅** | 向订阅者发送每日简报，并通过 SMTP/IMAP 处理订阅/退订请求 |
| **Webhook 通知** | 把成功或失败结果推送到飞书/Lark、钉钉、Slack、Discord 或任意自定义 Webhook 端点 |
| **微信通知** | 通过 iLink Bot 在扫码登录并收到你的消息后发送简报；受微信回复条数限制 |

## 进阶用法

### Web 面板

```bash
uv run periscope-web --data-dir data        # 默认 http://localhost:8790
```

三栏式界面：**证据库**（全文检索）、**研究报告**（长会话研究）、**核查台**（按 verdict 展示声明与证据）。研究报告栏按轮次组织：`推一轮` 按钮、每节的「已锁定」与「上游已变 · 未覆盖」标记、待回答请求卡片；报告正文会列出未评级声明的原因（低于分诊门 / 证据不足 / 无可核验发布者 / 可信度不足）。顶部可一键触发采集。API 文档位于 `/api/docs`。

### MCP 服务

```bash
uv run periscope-mcp
```

以 stdio 方式运行，提供 22 个 `hz_*` 工具与 7 个 `horizon://` 资源，覆盖配置校验、分阶段流水线、运行产物、证据检索、声明核查、研究会话与 Webhook 通知。详见 [MCP 工具说明](src/mcp/README.md) 与[客户端配置](src/mcp/integration.md)。

### 微信与 Webhook 命令行

```bash
uv run periscope-wechat login      # 扫码登录 iLink Bot
uv run periscope-wechat status     # 查看登录状态
uv run periscope-wechat test       # 测试发送（可 --dry-run 预览）

uv run periscope-webhook --dry-run # 预览 Webhook 请求
```

## 配置参考

完整示例见 [`data/config.example.json`](data/config.example.json)，密钥见 [`.env.example`](.env.example)。主要配置块：

| 配置块 | 作用 |
|--------|------|
| `ai` | 提供商、模型、`api_key_env`、`base_url`、`provider_chain`、温度、并发与限流 |
| `sources` | 各数据源及其分类、Profile 指定 |
| `collection` | 默认时间窗口 `time_window_hours` |
| `digest` | 简报总长度与分类配额（`max_items`、`category_groups`） |
| `processing` | `profiles_dir`、`default_profile`、单 Profile 偏好 `profile_settings` |
| `display` | 图标风格 `icon_style`（`emoji` / `nerd` / `ascii`） |
| `extractors` | 正文抽取器配置 |
| `email` | 邮件订阅与 SMTP/IMAP 设置 |
| `webhook` | Webhook 端点、平台适配、消息模板与投递语言 |
| `wechat` | 微信投递开关、语言与分块大小 |
| `corpus` | 证据语料库：`enabled`、`path`、`cluster_max_distance`、`cluster_lookback_rows` |
| `analysis` | 声明核查：`max_claims_per_item`、`evidence_per_claim`、`grade_min_sources`、`triage_min_trust`（分诊门，默认 `0.0` = 关闭）、`grade_budget_per_run`、`extract_top_items`、`item_content_chars`、`claimable_only` |
| `research` | 长会话研究：`evidence_per_question`、`max_evidence_chars`、`planner_budget_per_invocation`、`claimable_only`、`max_retrieval_rounds`、`min_evidence_for_answer`、`report_template`（`auto` / `flat` / 指定骨架） |
| `retrieval` | 取证检索：`query_expansion`、`expansion_max_terms`、`semantic` + `embedding_model`/`embedding_base_url`/`embedding_api_key_env`、`semantic_top_k`、`index_batch_size`、`on_demand_collection` |

## 项目结构

```
periscope/
├── src/
│   ├── main.py               # CLI 主入口
│   ├── orchestrator.py       # 流水线编排与阶段复用
│   ├── models.py             # 数据模型与配置定义
│   ├── scrapers/             # 14 类数据源抓取器
│   ├── ai/                   # 多 provider 客户端、分析/富化/摘要/本地化
│   ├── processing/           # Profile 加载、历史检索、内容处理
│   ├── corpus/               # 证据语料库（SQLite + FTS5 + SimHash）
│   ├── analysis/             # 声明级核查
│   ├── research/             # 长会话研究
│   ├── web/                  # Web 面板（FastAPI + 单文件三栏 UI）
│   ├── mcp/                  # MCP 服务与运行产物存储
│   ├── services/             # 邮件 / Webhook / 微信投递
│   ├── setup/                # 交互式配置向导
│   ├── extractors/           # 正文抽取（trafilatura）
│   └── storage/              # 配置与摘要存储
├── profiles/                 # 内置 Profile（tech-news / tech-blog / ai-creator / finance-news）
├── docs/                     # 文档与站点资源
├── tests/                    # 测试
├── data/                     # 配置示例与运行数据
├── scripts/                  # 辅助脚本
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## 开发与测试

```bash
uv sync --extra dev                        # 安装开发依赖
uv run pytest                              # 运行全部测试
uv run pytest tests/test_corpus.py         # 运行单个测试文件
uv run python scripts/eval_retrieval.py    # 检索消融表（docs/evaluation.md）
uv run python scripts/eval_retrieval.py --tiering marker   # 复现分层前的 A 档
uv run python scripts/eval_multiturn.py    # 多轮调用数比值 / 轮次 / 灌水曲线（不联网）
```

**关于 CI：这个平台上没有自动执行。** `.github/workflows/tests.yml` 与 `deploy-docs.yml` 是 GitHub 语法的配置，AtomGit 不执行它们（每个 PR 的 `check_tasks_num` 都是 0，已实测确认）。所以：

- 那些文件**保留着**，迁到 GitHub 或支持该语法的平台就生效；内容仍然是可信的验收脚本（Linux + Windows 各跑一遍全量，再跑一次检索 harness，只看能否复现，不在 CI 里断言指标数值）。
- 但在当前平台，**验证是提交者的责任**：本地跑 `uv run pytest` 与相关 harness，把实测数字写进 PR 正文。本项目不接受「有 CI 兜底」作为质量证据，PR 模板性的「测试通过」需要能复现的命令。
- `deploy-docs.yml` 对应的 GitHub Pages 站点也不生效，`docs/` 目前只是仓库内的 Markdown；要么换平台静态托管，要么删除该文件（未决）。

测试位于 `tests/`，覆盖流水线各阶段、各数据源抓取器、证据语料库与声明核查、研究会话、Web 面板与 MCP 服务等。

## 安全与可靠性

- **SSRF 防护** — 抓取与正文抽取前校验 URL 的 scheme、主机与端口，解析后要求所有地址为公网，并逐跳校验重定向。
- **路径逃逸防护** — 摘要与运行产物写入前校验目标路径位于允许目录内。
- **凭据脱敏** — MCP 返回的有效配置会递归脱敏 `token`、`secret`、`password` 等字段，Webhook 日志同样脱敏。
- **降级而非崩溃** — LLM 缓存读写失败、单个数据源抓取失败、声明评级失败都不会中断整条流水线。

## 文档

| 指南 | 说明 |
|------|------|
| [配置](docs/configuration.md) | AI 提供商、信息源、Profile、过滤、邮件、Webhook、微信、GitHub Pages 与 MCP 配置 |
| [处理画像](docs/profiles.md) | Profile 路由、提示词、运行期过滤偏好、富化 block 与工具 |
| [评分](docs/scoring.md) | Periscope 如何评估与排序新闻条目 |
| [抓取器](docs/scrapers.md) | 各数据源抓取器细节与扩展说明 |
| [正文抽取](docs/extractors.md) | RSS 源的全文抽取 |
| [检索与取证评测](docs/evaluation.md) | 消融表、标注口径、多轮成本与灌水曲线、已知不足 |
| [取证检索与证据分层](docs/retrieval.md) | 噪声从哪来、类型化分层怎么声明、可信度与独立性怎么重算、找不到时怎么加宽、多轮草稿怎么保住了用户的字、怎么复现 |
| [MCP 工具](src/mcp/README.md) | 面向 MCP 兼容客户端的 27 个工具参考 |
| [设计 spec 与三期实现计划](docs/superpowers/specs/2026-09-29-broad-source-credibility-and-multiturn-research-design.md) | 为什么这么改（spec v4，§15 是交付记录）、`docs/superpowers/plans/` 下逐 Task 的计划与「执行记录」里写错的句子 |
| [架构与生态设计](docs/horizon-hub-design.md) | HorizonHub 数据源市场与推荐的产品设计 |

## 项目状态

本 fork 继承的日报闭环（上游）：多源采集、Profile 驱动的分析与富化、去重、评论摘要、双语生成、邮件 / Webhook / 微信投递、Docker 部署、MCP 集成与配置向导。其中 **GitHub Pages 发布这一条在本平台上不生效** —— `deploy-docs.yml` 仍是 GitHub Pages 专用配置，待换成平台静态托管或删除。

本 fork 在此基础上额外提供证据语料库、声明级核查与多轮共创研究三项核心能力，以及证据分层、条目可信度与自适应取证三项支撑机制，并提供 Web 面板这一独立入口（见[本 fork 的独有层次](#本-fork-的独有层次)）。

后续计划（与 `docs/superpowers/specs/` 里设计 spec 的 §15.5「还剩什么」一致）：

- **声明 verdict 的人工标注 50–100 条** → 报 macro-F1 与按独立信源数分桶的一致率，再用 `roc_thresholds()` 校准 θ_s / θ_triage。这是本项目唯一"能力已实现、效果未主张"的一块：工具已就位（`scripts/eval_claims.py --export/--score`），缺的是标注本身，在此之前阈值是手工先验、分诊门默认关闭
- **源可达性探针**：小红书 S1 三步、贴吧 S2 的第 2–3 步（第 1 步已判不通过：楼层不可达）。S1 的第 1 步本身不需要账号，只是一次未登录 HTTP 判别；第 2–3 步才需要登录态或浏览器。**S1 不通过时的降级路径已经实现**（用户导出 → `scripts/import_corpus.py` / `hz_corpus_import` / `POST /api/import`，分层靠声明），所以"取不到的源"不再是死路，只是不自动。
- **P3 图文 → 文本通路**（OCR / VLM）：条件执行，卡在 S1 结论；VLM 描述只能当线索，不进声明蒸馏
- 支持更多数据源类型，例如 Discord
- 平台侧未决：CI 是否在本平台执行（GitHub 语法的 workflow 不被执行，`deploy-docs.yml` 待替换或删除）、仓库 issue 开关只能在网页打开
- 在 AtomGit 上发布 Release；发布到 PyPI，支持 `pip install`
- 命名：上游后期也把项目改叫 Periscope，两个仓库同名分不开 —— 是否改名由维护者决定，尚未执行

## 贡献

欢迎在**本仓库**贡献：issue 与 PR 请开在 <https://atomgit.com/NLY22/periscope>，上游仓库的 issue 与本 fork 无关。规范见 [CONTRIBUTING.md](CONTRIBUTING.md)，行为准则见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)，安全问题见 [SECURITY.md](SECURITY.md)。动手前建议先读 [docs/retrieval.md](docs/retrieval.md) 与 [docs/evaluation.md](docs/evaluation.md) —— 新增能力要能进消融表，否则只是加功能。

### 分享信息源

想把发现的优质信息源分享出去？**上游社区**通过 **[periscope1123.top](https://periscope1123.top)** 收集（该站点不由本 fork 运营）；本 fork 的信息源改动请直接开 PR 或 issue。

## 社区

> 归属说明：本 fork 的代码仓库、issue 与 PR 均在 <https://atomgit.com/NLY22/periscope>。页面上的 Trendshift / HelloGitHub 徽章、在线演示 `thysrael.github.io`、QQ 群与赞助位沿自上游项目，不是本 fork 的运营渠道。

欢迎加入 Periscope 用户与开发者 QQ 群，分享信息源、Profile 与部署经验。

<p align="center">
  <img src="docs/assets/qq-group.png" alt="Periscope QQ 群 1106121909 二维码" width="240" height="240" />
  <br />
  <strong>QQ 群：1106121909</strong><br />
  用 QQ 扫码或搜索群号加入。
</p>

## 赞助支持

Periscope 是一个利用业余时间维护的开源项目。如果你想支持本项目或希望出现在此列表中，欢迎[提交 Issue](https://atomgit.com/NLY22/periscope/issues/new)或[邮件联系](mailto:thysrael@163.com)。

| 支持者 | 详情 |
|-----------|---------|
| [<img src="docs/assets/compshare-logo.png" alt="Compshare / 优云智算" width="220" />](https://www.compshare.cn/?ytag=GPU_YY_git_Periscope) | Compshare 目前为 Periscope 提供支持。Compshare 是 UCloud 旗下的 AI 云平台，提供高性价比的包月与按量付费国内模型 Agent 方案，低至 49 元/月起，同时提供稳定官方转发的海外模型，支持 Claude Code、Codex 及 API 使用，具备企业级高并发、7×24 技术支持与自助开票能力。<br><br>通过他们的[链接](https://www.compshare.cn/?ytag=GPU_YY_git_Periscope)注册可获赠 5 元试用额度。 |
| [<img src="docs/assets/apimart-logo.jpg" alt="APIMart" width="220" />](https://go.apimart.ai/gh-periscope) | 感谢 APIMart 赞助本项目！APIMart 是一个低成本的 AI 图像与视频生成 API 平台——GPT-Image-2 低至 $0.006/张，一美元可生成 160+ 张图片。一套异步 API 同时覆盖图像与视频：提交任务、获取 ID、通过轮询或回调取回结果。可批量处理数万张图片而不超时，切换模型无需改动代码。按量付费、无月费——[点此注册](https://go.apimart.ai/gh-periscope)即可开始。 |
| [<img src="docs/assets/ofoxai-logo.svg" alt="OfoxAI" width="220" />](https://ofox.ai/?utm_source=github&utm_medium=sponsorship&utm_content=periscope) | OfoxAI 是一个统一的 AI API 平台，汇集多家提供商的文本、图像与视频模型。凭借 OpenAI 兼容端点以及原生 Anthropic 与 Gemini 接口，开发者可通过一个平台访问用于 AI 应用、Agent 与内容创作的各类模型。<br><br>[探索 OfoxAI 的模型与 API →](https://ofox.ai/?utm_source=github&utm_medium=sponsorship&utm_content=periscope) |

## 致谢

- 本项目 fork 自 [Thysrael/Horizon](https://github.com/Thysrael/Horizon)：日报流水线、Profile 体系与投递渠道均为上游成果；本 fork 的贡献集中在证据分层、取证检索、声明核查与长会话研究（见[能力对照](#本-fork-与上游的能力对照)）。
- **命名提示**：上游后期也把项目称为 Periscope，因此**"Periscope"这个名字本身不足以区分两个项目**。请以仓库地址 `NLY22/periscope` 与本页的对照表为准。若要彻底改名（包名与 `periscope-*` 六个 CLI 入口一起改）是一个独立决定，尚未执行。
- 特别感谢 [LINUX.DO](https://linux.do/) 提供推广平台（上游渠道）。
- 特别感谢 [HelloGitHub](https://hellogithub.com/) 提供宝贵的指导与建议。
- 特别感谢 [AIGC Link](https://xhslink.com/m/80ngts127cA) 在小红书上的推广。

## 许可证

[MIT](LICENSE)

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

[📋 配置指南](docs/configuration.md) · [🧩 Profile 定制](docs/profiles.md) · [🔎 取证检索机制](docs/retrieval.md) · [📊 评测](docs/evaluation.md)

</div>

---

## 目录

- [简介](#简介)
- [能力总览](#能力总览)
- [核心特性](#核心特性)
- [截图](#截图)
- [工作原理](#工作原理)
- [独有层次](#独有层次)
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
- [许可证](#许可证)

## 简介

好的内容散落在订阅源、论坛与时间线里，而你的注意力是有限的。**Periscope 会替你收集、筛选、去重**，再把真正值得一读的内容连同背景与社区讨论一起送到你面前。

你的品味决定了你读什么，也决定了你希望从中得到什么。一篇新闻报道需要回答「为什么重要」，一篇工程深度长文需要回答「我能用上什么」。Periscope 的 **Profile（画像）** 为每一类内容定义各自的评分标准与输出形式，让简报读起来像是为你手工挑选的。

Periscope 回答的不只是「今天有什么值得读」，还有**「这个说法站不站得住」**。它在采集与简报之上做三件事：**证据语料库**（`corpus.db` 跨运行、跨入口累积，不会次日即忘）、**声明级核查**（独立信源计数 + 引用反解核验）与**多轮共创研究**（长会话里逐轮取证、改范围、深化、定稿），并给出 **Web 面板**与 **MCP**（27 个工具）两个入口；再往下是三项支撑机制——**证据分层**、**条目可信度**与**自适应取证**，它们决定了前三项在噪声里是否真的站得住。详见[独有层次](#独有层次)。

## 能力总览

| 能力 | 落在哪 |
|---|---|
| 多源聚合（14 类源）、Profile 评分、双语日报、邮件 / Webhook / 微信投递、配置向导 | `src/scrapers`、`src/processing`、`src/services`、`src/setup` |
| Bilibili / V2EX / Discourse / YouTube 四个源，含 B 站 CC 字幕层 | `src/scrapers/{bilibili,v2ex,discourse,youtube}.py` |
| 证据语料库：SQLite + FTS5 + 手写 SimHash 聚簇，跨运行累积 | `src/corpus/store.py`、`src/corpus/simhash.py` |
| 用户导出入库通路（取不到的源不靠抓取）| `src/corpus/ingest.py`、`scripts/import_corpus.py`、`ps_corpus_import`、面板「导入你导出的内容」（`POST /api/import`，可先只校验）|
| 证据分层：scraper **声明**的类型化 `Section`（作者亲写 vs 人群发言），独立信源计数只认前者 | `src/models.py` 的 `Section`、`src/corpus/sections.py`、`items.claimable` + `claim_fts` |
| 条目可信度与独立性：可拆解的 trust 分数、noisy-OR 聚合、两道门（跑在 `grade_claim`，判定来源可追） | `src/corpus/trust.py`、`items.trust` + `trust_features_json`、`claims.verdict_source` |
| 多轮共创：逐轮动词 + 带 revision/locked/stale 的草稿工件 + 向用户索取输入 | `src/research/{moves,drafts}.py`、`research_drafts` / `research_requests` 表 |
| 源注册表与抓取基础设施：per-host 令牌桶、可注入时钟、env/cookie 鉴权与过期检测 | `src/sources/registry.py`、`src/scrapers/{throttle,auth}.py`、`SOURCE_SPECS` |
| 取证检索：查询扩展 + 向量路 + RRF 融合 | `src/corpus/retrieval.py`、`src/ai/{expand,embeddings}.py`、`src/corpus/semantic.py` |
| 声明级核查 + 评级一致率工具 | `src/analysis/{claims,agreement}.py`、`scripts/eval_claims.py` |
| 长会话研究：子问题树、崩溃续跑、缺证时自适应加宽 | `src/research/`、`research_actions` 表 |
| 报告骨架（背景调查 / 市场调研 / 方法探索）+ 引用反解核验 | `src/research/templates.py`、`src/corpus/citations.py` |
| 检索消融评测（Recall / Precision / nDCG / MRR） | `src/corpus/metrics.py`、`scripts/eval_retrieval.py`、`docs/evaluation.md` |
| Web 面板 | `src/web/` |

**本项目的立场一句话**：把来源放宽到论坛与流媒体，信息质量必然下降；全部工作在于让"降下去的质量"被分层、检索、计数与引用核验**重新补回来**，并用消融表说明补回了多少。

## 核心特性

下面每条都是本项目自带的能力：

- **📡 聚合你的信息源** — RSS、Hacker News、Reddit、Telegram、X、GitHub、财经资讯等 10 类，再加 **Bilibili、V2EX、Discourse、YouTube 四类与 B 站 CC 字幕层**，共 14 类。
- **🎯 判断什么值得读** — 用 Profile 定义评分细则，为每个 Profile 设置阈值，并在同一 Profile 内合并重复报道。
- **🧩 为每篇内容定制处理方式** — 通过 Markdown 提示词与 JSON block 定义，自由组合摘要、背景、解决方案或要点。
- **💬 不止于标题** — 在有助于解释事件时，自动补充联网检索到的背景与社区讨论。
- **⚖️ 兼顾你的所有兴趣** — 限制简报总长度与各分类占比，避免某一热门话题挤占其余内容。
- **📬 在你习惯的地方阅读** — 生成中英双语 Markdown 简报，发布到 Pages，或通过邮件、Webhook、微信投递。
- **🧠 让每一天都可积累** — 所有采集过的内容进入证据语料库，可全文检索、做声明核查，并作为长会话研究的素材；**证据分层**保证评论与回复只作线索，不充当信源。
- **🧯 诚实的降级** — 没有 LLM key 也能运行：语料照常增长、证据照常确定性关联，报告会如实标注「尚未回答」而不是编造内容。
- **🔍 找不到就换打法** — 一个子问题不再只有一次查询：放宽词条、换来源族、让模型改写问法，每步写入 `research_actions`，报告尾部列出「取证尝试」，把"语料里没有"和"问法不对"分开。
- **✅ 引用可自查** — 成品报告可被反向核验：幽灵引用、指向不存在条目、只靠人群发言支撑的引用，都会被点名（`src/corpus/citations.py`）。

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

**Profile 决定处理方式，运行期配置反映你的阅读偏好。** 每个条目只会路由到一个 Profile。分析、过滤与去重在富化之前完成，随后入选条目按 Profile 分组形成简报。结构与图元（通路、会话状态机、一轮时序）见 [docs/architecture.md](docs/architecture.md)——那三张图与代码是双向对齐的，画进去的名字由测试核对。

一次完整运行（`periscope --hours 24`）的阶段顺序如下：

1. **采集** — 并发抓取所有已启用数据源，每个源独立成败，结果同时写入证据语料库并重算近似重复聚簇。
2. **跨源去重** — 规范化 URL（剥离 `utm_*`、`gclid` 等跟踪参数），合并指向同一内容的多源条目。
3. **分析** — 分类路由到 Profile，由 AI 完成评分、理由、摘要与标签。
4. **选择与过滤** — Profile 阈值过滤 → AI 主题去重 → 均衡配额（`digest` 配置）。
5. **富化** — 第二遍 AI，按 Profile 定义的 block 生成多语言产物，可调用联网检索与历史检索工具。
6. **声明核查** — 将高分条目蒸馏为原子声明，关联语料证据并评级（尽力而为）。
7. **摘要与投递** — 程序化渲染多语言 Markdown 日报，保存到 `data/summaries/`，并按配置发布或投递。

你可以通过 CLI 运行完整流水线，也可以让 AI 助手经由 [MCP](src/mcp/README.md) 调用其中的各个阶段。

## 独有层次

只回答「今天有什么值得读」、并且到了第二天就遗忘的工具，产出的是一天的信息，不是可复查的证据。Periscope 在采集与简报之上做了八项增强，让"来源放宽必然掉下去的质量"能被补回来：

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

# MCP：面向任意 MCP 客户端的 27 个工具（ps_research_start、ps_research_step、ps_corpus_search 等）
uv run periscope-mcp

# 取不到的源：用户自己导出，按声明的层级入库（不联网、不碰验证码与签名）
#   样例负载见 data/export.example.json（把内容换成你账号本来就能看见的东西）
uv run python scripts/import_corpus.py --file data/export.example.json --data-dir data --dry-run
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

Periscope 适合用系统定时器调度，例如 `cron` 或 `systemd timer`；Docker Compose 也可直接配合定时任务使用（`docker compose run --rm periscope-collect --hours 24` 配 cron 即可）。

仓库里另有 `.github/workflows/tests.yml`。**本平台不执行任何 GitHub 语法的 workflow**（已实测：PR 的 `check_tasks_num` 为 0），那条文件只对在 GitHub 上跑的人有意义；在这个平台上要定时运行，就用任何能执行 shell 的调度器，按下面的命令排程即可。

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
| **邮件订阅** | 向订阅者发送每日简报，并通过 SMTP/IMAP 处理订阅/退订请求 |
| **Webhook 通知** | 把成功或失败结果推送到飞书/Lark、钉钉、Slack、Discord 或任意自定义 Webhook 端点 |
| **微信通知** | 通过 iLink Bot 在扫码登录并收到你的消息后发送简报；受微信回复条数限制 |

## 进阶用法

### Web 面板

```bash
uv run periscope-web --data-dir data        # 默认 http://localhost:8790
```

三栏式界面：**证据库**（全文检索）、**研究报告**（长会话研究）、**核查台**（按 verdict 展示声明与证据）。研究报告栏按轮次组织：`推一轮` 按钮、每节的「已锁定」与「新版已变 · 未覆盖」标记、待回答请求卡片；报告正文会列出未评级声明的原因（低于分诊门 / 证据不足 / 无可核验发布者 / 可信度不足）。顶部可一键触发采集。API 文档位于 `/api/docs`。

### MCP 服务

```bash
uv run periscope-mcp
```

以 stdio 方式运行，提供 27 个 `ps_*` 工具与 7 个 `periscope://` 资源（4 个固定 + 3 个带参数的模板，实测握手），覆盖配置校验、分阶段流水线、运行产物、证据检索、声明核查、研究会话与 Webhook 通知。详见 [MCP 工具说明](src/mcp/README.md) 与[客户端配置](src/mcp/integration.md)。

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
| `analysis` | 声明核查：`max_claims_per_item`、`evidence_per_claim`、`grade_min_sources`、`triage_min_trust`（分诊门，默认 `0.0` = 关闭）、`supported_min_trust` / `same_family_prior` / `same_family_publishers`（`supported` 判定门的阈值，默认全部 = 手工先验）、`grade_budget_per_run`、`extract_top_items`、`item_content_chars`、`claimable_only` |
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

**关于 CI：这个平台上没有自动执行。** `.github/workflows/tests.yml` 是 GitHub 语法的配置，AtomGit 不执行它（每个 PR 的 `check_tasks_num` 都是 0，已实测确认）。所以：

- 那条文件保留着，迁到 GitHub 或支持该语法的平台就生效；内容仍然是可信的验收脚本（Linux + Windows 各跑一遍全量，再跑一次检索 harness，只看能否复现，不在 CI 里断言指标数值）。
- 但在当前平台，**验证是提交者的责任**：本地跑 `uv run pytest` 与相关 harness，把实测数字写进 PR 正文。本项目不接受「有 CI 兜底」作为质量证据，PR 模板性的「测试通过」需要能复现的命令。

测试位于 `tests/`，覆盖流水线各阶段、各数据源抓取器、证据语料库与声明核查、研究会话、Web 面板与 MCP 服务等。

## 安全与可靠性

- **SSRF 防护** — 抓取与正文抽取前校验 URL 的 scheme、主机与端口，解析后要求所有地址为公网，并逐跳校验重定向。
- **路径逃逸防护** — 摘要与运行产物写入前校验目标路径位于允许目录内。
- **凭据脱敏** — MCP 返回的有效配置会递归脱敏 `token`、`secret`、`password` 等字段，Webhook 日志同样脱敏。
- **降级而非崩溃** — LLM 缓存读写失败、单个数据源抓取失败、声明评级失败都不会中断整条流水线。

## 文档

| 指南 | 说明 |
|------|------|
| [配置](docs/configuration.md) | AI 提供商、信息源、Profile、过滤、邮件、Webhook、微信与 MCP 配置 |
| [处理画像](docs/profiles.md) | Profile 路由、提示词、运行期过滤偏好、富化 block 与工具 |
| [评分](docs/scoring.md) | Periscope 如何评估与排序新闻条目 |
| [抓取器](docs/scrapers.md) | 各数据源抓取器细节与扩展说明 |
| [正文抽取](docs/extractors.md) | RSS 源的全文抽取 |
| [检索与取证评测](docs/evaluation.md) | 消融表、标注口径、多轮成本与灌水曲线、已知不足 |
| [自助测试手册](docs/selftest.md) | 五条命令自己把效果重测一遍：每步该看到什么、哪些"怪现象"是正常的、真模型腿取不到时怎么降级 |
| [变更记录](CHANGELOG.md) | 已合入 `main` 的阶段与各期改动（P0 / P1 / P2 + 文档护栏）各自做了什么、修了什么、有多少实测数字；也写明尚未发布任何版本 |
| [取证检索与证据分层](docs/retrieval.md) | 噪声从哪来、类型化分层怎么声明、可信度与独立性怎么重算、找不到时怎么加宽、多轮草稿怎么保住了用户的字、怎么复现 |
| [架构图](docs/architecture.md) | 三张结构性图（spec §8 的 1–3 号）：广源→可用证据的通路、研究会话状态机（`awaiting_user` 是一等状态）、一轮交互的时序；每张图带代码落点，图元由护栏测试与代码对齐 |
| [Twitter / X Cookie 配置](docs/twitter-cookies.md) | 免费方案：Playwright + 自己账号的 cookie 抓推文（Apify 订阅的替代路径） |
| [MCP 工具](src/mcp/README.md) | 面向 MCP 兼容客户端的 27 个工具参考 |
| [设计 spec 与三期实现计划](docs/superpowers/specs/2026-09-29-broad-source-credibility-and-multiturn-research-design.md) | 为什么这么改（spec v4，§15 是交付记录）、`docs/superpowers/plans/` 下逐 Task 的计划与「执行记录」里写错的句子 |

## 项目状态

日报闭环（本项目自带）：多源采集、Profile 驱动的分析与富化、去重、评论摘要、双语生成、邮件 / Webhook / 微信投递、Docker 部署、MCP 集成与配置向导。

在此基础上，证据语料库、声明级核查与多轮共创研究是三项核心能力，证据分层、条目可信度与自适应取证是三项支撑机制，Web 面板是独立入口（见[独有层次](#独有层次)）。

后续计划（与 `docs/superpowers/specs/` 里设计 spec 的 §15.5「还剩什么」一致）：

- **声明 verdict 的人工标注 50–100 条** → 报 macro-F1 与按独立信源数分桶的一致率，再用 `roc_thresholds()` 校准 θ_s / θ_triage。这是本项目唯一"能力已实现、效果未主张"的一块：工具已就位（`scripts/eval_claims.py --export/--score`），缺的是标注本身，在此之前阈值是手工先验、分诊门默认关闭
- **源可达性探针**：判别逻辑已经做成可复跑的工具 —— `src/sources/reachability.py` + `scripts/spike_sources.py`（判定分为 `pass / list_only / blocked_captcha / signed_required / blocked_auth / error`，离线用 `httpx.MockTransport` 测，**不加 `--online` 不发任何请求**）。贴吧 S2 的第 1 步结论（列表可达、楼层不可达）现在是测试里可复现的判定而不是散文；小红书 S1 的第 1 步同样**不需要账号**，一条命令就能跑并把结果落到 `data/eval/reachability_results.json`：

```bash
uv run python scripts/spike_sources.py --source xiaohongshu --url <公开笔记页 URL> --online
uv run python scripts/spike_sources.py --source tieba --kw <吧名或关键词> --online
```

（`--online` 是唯一会让它真的发请求的开关；去掉它就是打印判定计划。）第 2–3 步才需要登录态或浏览器。**S1 不通过时的降级路径已经实现**（用户导出 → `scripts/import_corpus.py` / `ps_corpus_import` / `POST /api/import`，分层靠声明），所以"取不到的源"不再是死路，只是不自动。
- **P3 图文 → 文本通路**（OCR / VLM）：条件执行，卡在 S1 结论；VLM 描述只能当线索，不进声明蒸馏
- 支持更多数据源类型，例如 Discord
- 平台侧未决：GitHub 语法的 workflow 在本平台不被执行（护栏与测试要人工过一遍）、仓库 issue 开关只能在网页打开
- 在 AtomGit 上发布 Release；发布到 PyPI，支持 `pip install`

## 贡献

欢迎贡献：issue 与 PR 请开在 <https://atomgit.com/NLY22/periscope>。规范见 [CONTRIBUTING.md](CONTRIBUTING.md)，行为准则见 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)，安全问题见 [SECURITY.md](SECURITY.md)。动手前建议先读 [docs/retrieval.md](docs/retrieval.md) 与 [docs/evaluation.md](docs/evaluation.md) —— 新增能力要能进消融表，否则只是加功能。信息源与 Profile 的改动直接开 PR 或 issue。

## 许可证

[MIT](LICENSE)

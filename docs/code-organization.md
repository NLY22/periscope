# 代码结构与运行入口

新版话题检索的正式实现位于 `periscope/`。原有脚本和导入路径继续可用，转交同一份实现，避免复制逻辑。其他专题见[文档导航](README.md)。

```text
periscope/
├── cli/                       命令参数与输出
├── topics/                    查询编排、结果、富化、运行记录与保存
├── search/                    本地检索、搜索 API、身份与合并排序
├── collection/
│   ├── catalog/               来源定义
│   ├── transport/             HTTP 请求与响应解析
│   ├── strategies/            广泛、聚焦、批量、浏览器调度
│   ├── crawling/              站点爬取、文档提取、解析子进程
│   ├── platforms/             平台专用适配
│   └── models.py / registry.py / extensions.py 等共享模型与目录
├── operations/                入库、回填、维护与产物整理
└── runtime/                   路径、环境与终端

src/                           既有证据内核与日报、研究、Web/MCP
core/、collectors/、topic.py    兼容入口
scripts/                       评测、诊断、导入等可复用脚本
probes/                        历史实验
tests/architecture/            结构与兼容验证
tests/collection/              来源与爬虫验证
docs/                          使用文档与设计记录
.periscope-scratch/
├── collect/                   原始采集结果
├── reports/                   探测日志与迁移清单
├── topics/                    查询运行记录
└── 其他缓存、隔离验证与本地语料
```

## 目录职责

| 目录 | 职责 |
|---|---|
| `periscope/cli/` | 参数解析、命令输出、退出码 |
| `periscope/topics/` | 查询请求与结果、三层检索编排、富化、展示、运行记录、显式入库 |
| `periscope/search/` | 本地全文检索、搜索 API、多源聚合与排序 |
| `periscope/collection/catalog/` | 按类别组织的信息源配置及稳定顺序 |
| `periscope/collection/` | 共享源模型、注册目录、来源分类与旧路径兼容模块 |
| `periscope/collection/transport/` | HTTP 请求、分页、限速策略和响应解析 |
| `periscope/collection/strategies/` | 广泛、聚焦、批量和浏览器调度与逐源状态 |
| `periscope/collection/crawling/` | 站点发现、网页/PDF 提取与解析子进程 |
| `periscope/collection/platforms/` | B 站和浏览器平台的专属处理 |
| `periscope/runtime/` | 路径、环境配置和终端编码 |
| `periscope/operations/` | 分片导入、正文抓取、回填与有清单的文件整理 |
| `src/` | 既有证据内核，以及日报、研究会话、Web 和 MCP 产品 |
| `core/`、`collectors/` | 原路径的兼容入口 |
| `tools/` | 运维兼容入口和既有诊断工具 |
| `probes/` | 历史探测脚本，排除在产品安装包之外 |

## 主要调用关系

```text
CLI → TopicRequest → TopicService.search → TopicResult
                         ├── 本地语料检索
                         ├── 搜索 API
                         ├── 来源计划 → 原生搜索 / 相关订阅 / 可选浏览器
                         └── 合并与可选富化

TopicResult → 文本 / JSON
            → 原始运行分片 → 显式导入语料库
```

`TopicService` 不负责打印、保存运行分片或启动入库。调用方可以直接使用结构化结果；`raw_rows` 保留合并前资料，`merged` 是排序后的展示结果。

```python
from periscope.topics.models import TopicRequest
from periscope.topics.service import TopicService

result = TopicService().search(TopicRequest(topic="人形机器人", cached_only=True))
for row in result.merged:
    print(row["title"], row["url"])
```

原有信息源定义可以通过 `periscope.collection.sources` 独立读取；新增配置位于 `collection/extensions.py`，统一通过 `collection/registry.py` 构建检索计划。读取目录无需联网、读取凭据或初始化浏览器。原有 179 条目录及顺序保留，CLI 默认扩大实际查询范围，来源能力与限制见[信息源体系](source-system.md)。

新版仅在适配位置借用 `src` 的模型、语料库、可信度、分层、正文抽取和 URL 安全模块，不调用旧产品的日报或研究编排。依赖测试扫描正式实现，避免只检查兼容文件。

`--save` 与 `--ingest-last` 共用运行记录到采集行的转换，再调用入库模块。启动子进程时传递已解析的绝对数据、临时和凭据目录，避免相对路径随工作目录变化而指向另一处资料。

## 运行命令

安装项目后，推荐使用：

```text
periscope-topic --topic "人形机器人"
periscope-topic --topic "人形机器人" --cached-only
periscope-topic --topic "人形机器人" --save
periscope-topic --ingest-last
```

`uv run periscope-topic ...`、`python -m periscope.cli.topic ...` 和原有 `python topic.py ...` 使用同一份实现。

运维模块也可直接执行，安装后不依赖源码仓库中的脚本路径：

```text
python -m periscope.operations.ingest --help
python -m periscope.operations.backfill --help
python -m periscope.operations.persist --help
```

原有日报命令 `periscope`，以及 `periscope-web`、`periscope-mcp` 保持原行为。Docker 默认仍执行日报；运行话题检索时将入口设为 `periscope-topic`，并设置 `PERISCOPE_DATA_DIR=/app/data`，使运行记录写入已有的数据挂载。

## 数据与兼容

- 源码仓库中的默认语料路径仍为 `.periscope-scratch/corpus.db`。
- `PERISCOPE_DATA_DIR` 指定语料和话题记录目录；`PERISCOPE_SCRATCH_DIR` 指定临时目录；`PERISCOPE_COOKIE_DIR` 指定凭据目录。
- `PERISCOPE_COLLECT_DIR` 指定原始采集结果目录，默认 `.periscope-scratch/collect/`；`PERISCOPE_REPORT_DIR` 指定日志和探测报告目录，默认 `.periscope-scratch/reports/`。
- 采集、浏览器独立运行、历史探测脚本与入库共享这些路径。旧根目录采集数据仍可读取，以兼容其他尚未整理的检出目录。
- 安装到仓库外时，默认使用用户可写数据目录：Windows 为 `%LOCALAPPDATA%/Periscope`，无该变量时为 `~/.periscope`；其他平台为 `$XDG_DATA_HOME/periscope`，无该变量时为 `~/.local/share/periscope`。
- 数据库、缓存和浏览器会话保持原位；已知的根目录采集产物通过显式整理迁移，保留文件校验与迁移清单。
- 旧导入路径与正式路径使用同一个模块对象，路径覆盖不会变成两份状态。新增代码应导入正式模块。
- `periscope.collection.http` 等原有扁平模块同样保留为兼容别名；网络、调度和爬虫的新实现分别放入三个子目录。
- 旧版 `src` 的默认语料路径保持不变；这次整理没有把两套产品的数据库合并。

整理旧检出目录中散落的已知采集产物，可先查看计划，再执行：

```text
python -m periscope.operations.organize
python -m periscope.operations.organize --apply
```

只处理已知的采集结果、诊断报告与日志；不搬动数据库、凭据或浏览器会话。迁移逐文件记录原位置、新位置及 SHA256，重名文件另存并保留原件。当前工作目录的 58 个历史文件已迁移，清单位于 `reports/layout-migration-*.json`。

## 验证范围

原有测试继续验证采集、解析、路径、检索、研究和界面契约。新增结构测试覆盖兼容状态、模块帮助、浏览器延迟初始化、产物安全迁移、重名历史文件入库，以及隔离数据中的“结果 → 入库 → 再检索”。本轮完整测试为 1361 项通过。

安装包已在源码目录之外独立验证：使用本地 HTTP 站点完成列表发现、网页/PDF 解析、子进程入库、本地溯源与文档版本保存，并检查相对数据目录在子进程中保持一致。该验证不代表已重新实测所有外部信息源。详细结果见[整理实施记录](superpowers/plans/2026-10-19-layout-cleanup.md)。

此次工作整理代码结构与交付方式；前次分析发现的研究引用映射及来源判断问题仍需独立修复，不将它们视为此次重构已解决的能力。

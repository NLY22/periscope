# 文档导航

理解当前项目，建议依次阅读：

1. [代码结构与入口](code-organization.md)：正式实现在哪里，各目录负责什么。
2. [信息源体系](source-system.md)：当前文本扩源目标、框架问答决策、已接入来源与实际覆盖限制。
3. [自主爬虫](native-crawler.md)：站点发现、正文/PDF 提取和运行命令。
4. [配置指南](configuration.md)：模型、信息源、投递与运行参数。

## 按任务查找

| 任务 | 文档 |
|---|---|
| 查看实际采集、保存、再检索链路与浏览器资源控制 | [采集链路与边界（2026-10-24）](collection-pipeline.md) |
| 查看最新代码状态、已复现问题与推进建议 | [项目分析与扩源主链审计（2026-10-22）](project-audit-2026-10-22.md) |
| 审阅下一阶段的多轮调查与扩源框架 | [本地多轮调查与信息源框架设计](superpowers/specs/2026-10-19-local-investigation-source-framework-design.md)（设计草案，尚未实施） |
| 新增来源或排查采集结果 | [信息源体系](source-system.md)、[采集器](scrapers.md)、[正文提取](extractors.md) |
| 检索、可信度与证据处理 | [检索机制](retrieval.md)、[评分](scoring.md) |
| 运行验证和评测 | [自检](selftest.md)、[评测](evaluation.md) |
| 配置内容画像 | [Profiles](profiles.md) |
| 理解既有日报、研究和 Web/MCP | [架构](architecture.md) |
| 查看整理决策和演进 | [框架记录](foundation.md)、[历史调研](foundation-survey-2026-10-19.md)、[实施记录](superpowers/plans/) |

`superpowers/` 保存设计与实施记录；历史记录可能描述旧结构和当时的实测结果，当前运行方式以代码结构、来源体系与爬虫文档为准。

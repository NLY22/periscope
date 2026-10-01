# Security Policy

> **本页有两部分。** 下面「Applies to this fork」是本 fork（`NLY22/periscope`）的披露渠道；再往下原文沿自上游 [Thysrael/Horizon](https://github.com/Thysrael/Horizon)，其中的邮箱 **thysrael@gmail.com 属于上游作者，不接收本 fork 的报告**。本 fork 的抓取器改动、证据语料库、声明核查、研究会话与 Web/MCP 入口都是上游没有的代码，把它们发给上游只会得到无人能修的沉默。

## Reporting a vulnerability in this fork

请按以下顺序，任选一条可用的渠道，**不要先开公开 issue**：

1. 仓库页面的私信 / 「联系维护者」入口：<https://atomgit.com/NLY22/periscope>
2. 在该仓库开一条标题带 `[security]` 的 issue 并说明需要私下沟通（注意：本仓库的 issue 开关目前只能由维护者在网页端确认是否已开启；若无法开 issue，请用第 1 条）

请包含：

- 问题的清晰描述，以及它影响本 fork 的哪一层（取证检索 / 声明核查 / 语料库 / Web 面板 / MCP / 抓取器）
- 复现步骤
- 受影响的版本、commit 或运行环境

本 fork 没有自动 CI 与发布流水线，因此也没有「安全更新随版本发布」的承诺；修复以维护者手工合入为准。请给我们合理的响应时间后再公开披露。

## Risk surface specific to this fork

评审自己的报告是否属于安全问题时，这几条是本 fork 特有的：

- **持久化与执行**：`corpus.db` / `llm_cache.db` 跨运行保留被采集内容；Web 面板与 MCP 服务在同一台机器上读取同一份库。能污染语料或缓存的输入，也可能污染后续报告与引用。
- **凭证处理**：cookie 文件与 env-token provider（`src/scrapers/auth.py`）。cookie 过期检测会在日志里**点名是哪条 cookie**，但不会输出 cookie 值本身；任何把凭证写进日志、`corpus.db` 或报告的路径都算漏洞。
- **出网与花费**：`retrieval.on_demand_collection` 与查询扩展会在会话中途联网并消耗模型额度；能诱导无限制抓取或无限调用扩展的路径属于拒绝服务/滥用类问题。
- **反爬边界**：本 fork 明确不做验证码打码、签名逆向与多账号池（见设计 spec §11）。任何要求走这些手段的「改进建议」不属于安全披露，也不会被实现。

## Reporting a Vulnerability (upstream Horizon)

If you discover a security vulnerability in Horizon, please do **not** open a public issue.
Instead, please report it privately by email:
**thysrael@gmail.com**

Please include:
- A clear description of the issue
- Steps to reproduce the problem
- The affected version, commit, or environment
- Any proof-of-concept, screenshots, or logs that may help

## Response Process

I will try to:

- Acknowledge receipt within **7 days**
- Investigate and validate the report
- Work on a fix and coordinate responsible disclosure when appropriate

## Supported Versions

Security updates are provided for the latest maintained version of Horizon.

## Disclosure Policy

Please avoid public disclosure until I have had a reasonable opportunity to investigate and release a fix.
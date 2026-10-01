---
layout: default
title: Home
---

# Horizon / Periscope fork

> 本仓库是 [Horizon](https://github.com/thysrael/Horizon) 的 fork。上游只回答「今天有什么值得读」，第二天就遗忘；本 fork 在此之上加了**证据语料库、声明级核查与多轮共创研究**，以及决定它们能否站立的**类型化证据分层**与**条目可信度**。下面标 (fork) 的页面是本 fork 的。

<div id="lang-zh" class="lang-section" markdown="1">

欢迎来到 [Horizon](https://github.com/thysrael/Horizon)，一个 AI 驱动的信息聚合系统。

## 文档

- [配置指南](configuration) — AI 提供商、信息源、过滤规则与环境变量替换
- [信息源采集器](scrapers) — 如何从 GitHub、Hacker News、RSS、Reddit 等采集内容；本 fork 另有源注册表、限速与鉴权基础设施
- [评分系统](scoring) — 基于 AI 的内容分析与 0-10 评分体系
- [处理画像](profiles) — Profile 路由、提示词、运行期过滤偏好与富化 block
- **[架构图](architecture) (fork)** — 三张结构性图：广源→可用证据的通路、研究会话状态机（`awaiting_user` 是一等状态）、一轮交互的时序；每张图带代码落点，画进图里的名字由测试与代码双向对齐
- **[取证检索与证据分层](retrieval) (fork)** — 噪声从哪来、类型化 `Section` 分层、条目可信度与独立性重算、缺证时怎么加宽、多轮草稿工件，以及怎么复现
- **[检索与取证评测](evaluation) (fork)** — 六配置消融表、标注口径、多轮成本与灌水曲线、已知不足
- **[设计 spec 与三期实现计划](superpowers/specs/2026-09-29-broad-source-credibility-and-multiturn-research-design.md) (fork)** — 为什么这么改、逐 Task 计划、以及交付记录里被实测推翻的前提

## 每日速递 <a class="rss-icon" href="{{ '/feed-zh.xml' | relative_url }}" aria-label="订阅中文"><svg viewBox="0 0 448 512" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M128.081 415.959c0 35.369-28.672 64.041-64.041 64.041S0 451.328 0 415.959s28.672-64.041 64.041-64.041 64.04 28.673 64.04 64.041zm175.66 47.25c-8.354-154.6-132.185-278.587-286.95-286.95C7.656 175.765 0 183.105 0 192.253v48.069c0 8.415 6.49 15.472 14.887 16.018 111.832 7.284 201.473 96.702 208.772 208.772.547 8.397 7.604 14.887 16.018 14.887h48.069c9.149.001 16.489-7.655 15.995-16.79zm144.249.288C439.596 229.677 251.465 40.445 16.503 32.01 7.473 31.686 0 38.981 0 48.016v48.068c0 8.625 6.835 15.645 15.453 15.999 191.179 7.839 344.627 161.316 352.465 352.465.353 8.618 7.373 15.453 15.999 15.453h48.068c9.034-.001 16.329-7.474 16.005-16.504z"/></svg></a>

<ul>
  {% assign zh_posts = site.posts | where: "lang", "zh" %}
  {% for post in zh_posts limit:20 %}
    <li>
      <a href="{{ post.url | relative_url }}">{{ post.date | date: "%Y-%m-%d" }}</a>
    </li>
  {% else %}
    <li><em>暂无内容</em></li>
  {% endfor %}
</ul>

</div>

<div id="lang-en" class="lang-section" markdown="1">

Welcome to [Horizon](https://github.com/thysrael/Horizon), an AI-driven information aggregation system.

## Documentation

- [Configuration Guide](configuration) — AI providers, information sources, filtering, and environment variable substitution
- [Source Scrapers](scrapers) — How content is collected from GitHub, Hacker News, RSS, Reddit, Bilibili, V2EX, Discourse and YouTube, plus this fork's source registry, throttling and auth plumbing
- [Scoring System](scoring) — AI-based content analysis and the 0-10 scoring scale
- [Digest Profiles](profiles) — Profile routing, prompts and enrichment blocks
- **[Architecture diagrams](architecture)** (fork) — Three structural pictures: the broad-source-to-evidence path, the session state machine (`awaiting_user` drawn as a first-class state) and one round's sequence; each carries its code anchors, and every drawn name is checked against the source in both directions
- **[Retrieval and Evidence Layering](retrieval)** (fork) — Where the noise comes from, declared `Section` tiers, item trust and the redefined independence count, widening steps, the versioned research draft, and how to reproduce it
- **[Retrieval Evaluation](evaluation)** (fork) — Six-configuration ablation, labelling protocol, multi-turn cost and flood curves, known gaps
- **[Design spec and phase plans](superpowers/specs/2026-09-29-broad-source-credibility-and-multiturn-research-design.md)** (fork) — Why it changed this way, task-by-task plans, and the delivery record including the premises measurement overturned

## Daily Digest <a class="rss-icon" href="{{ '/feed-en.xml' | relative_url }}" aria-label="Subscribe English"><svg viewBox="0 0 448 512" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" d="M128.081 415.959c0 35.369-28.672 64.041-64.041 64.041S0 451.328 0 415.959s28.672-64.041 64.041-64.041 64.04 28.673 64.04 64.041zm175.66 47.25c-8.354-154.6-132.185-278.587-286.95-286.95C7.656 175.765 0 183.105 0 192.253v48.069c0 8.415 6.49 15.472 14.887 16.018 111.832 7.284 201.473 96.702 208.772 208.772.547 8.397 7.604 14.887 16.018 14.887h48.069c9.149.001 16.489-7.655 15.995-16.79zm144.249.288C439.596 229.677 251.465 40.445 16.503 32.01 7.473 31.686 0 38.981 0 48.016v48.068c0 8.625 6.835 15.645 15.453 15.999 191.179 7.839 344.627 161.316 352.465 352.465.353 8.618 7.373 15.453 15.999 15.453h48.068c9.034-.001 16.329-7.474 16.005-16.504z"/></svg></a>

<ul>
  {% assign en_posts = site.posts | where: "lang", "en" %}
  {% for post in en_posts limit:20 %}
    <li>
      <a href="{{ post.url | relative_url }}">{{ post.date | date: "%Y-%m-%d" }}</a>
    </li>
  {% else %}
    <li><em>No posts yet</em></li>
  {% endfor %}
</ul>

</div>

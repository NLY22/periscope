# 地基调研：可 fork 的上位项目（2026-10-19）

> 只读调研，未改动任何产品代码。所有 stars / 许可证 / 活跃度均通过 GitHub API
> 实测于 2026-10-19，非记忆。许可证以仓库 LICENSE 原文为准，不以 README 徽章为准。

## 0. 结论先行

**在你给的四个约束（可 fork 上位项目 / 广度优先 / 中文平台 / 许可证可商用）下，
不存在一个可以整体替换 Periscope 的项目。** 这不是没找到，是搜到之后逐一验证的结果：

- 中文聚合/舆情的头部项目（TrendRadar 63.0k、BettaFish 42.5k）是 **GPL**；
- 中文平台采集的头部项目（MediaCrawler 67.1k）是 **禁止商用**；
- 覆盖长尾最广的 RSSHub（46.6k）是 **AGPL**，只能进程隔离；
- 唯一 MIT 且真能补广度的 DailyHotApi（4.1k）**只做热榜，没有证据层**。

因此本报告给出的不是"换哪个地基"，而是**"地基分层借用"**：
主框架继续用你已 fork 的 Horizon（MIT），只在三个具体位置上引进可商用组件。

**最重要的一个反直觉发现**：你的广度缺口不在"源不够多"，而在"源没接进来"。
`CATALOG` 实测 **179 个源**（community 51 / media 39 / official 15 / ugc 12 / …），
但 `docs/foundation.md` §6 自己记着 **13 / 179** 接进了话题检索。
**再买 100 个源不会让这个数字变成 113。**

---

## 1. 你的上游本身就是最好的地基

`git remote` 与 README 均确认：Periscope fork 自 **Thysrael/Horizon**。

| 项 | 实测值 |
|---|---|
| stars / forks | 10,755 / 1,632 |
| 许可证 | **MIT**（可商用、可闭源分发） |
| 语言 / 最近 push | Python / **2026-10-19**（当天仍在提交） |
| archived | false |
| 结构 | 109 个 py 文件，`src/scrapers/`、`src/extractors/`（trafilatura）、`src/mcp/` |

这意味着"找上位项目"这件事你已经做过一次，而且选对了：**MIT + Python + 活跃**。
下面所有候选，都应该拿这个基线去比"它比 Horizon 多给了什么"，而不是比 stars。

---

## 2. 逐类排除记录（含许可证原文）

### 2.1 中文聚合 / 舆情：全部撞 copyleft

| 项目 | stars | 许可证 | 对"可商用闭源"的含义 | 判定 |
|---|---|---|---|---|
| sansan0/TrendRadar | 63,013 | **GPL-3.0** | 衍生作品须以 GPL 开源 | ❌ 不可 fork |
| 666ghj/BettaFish | 42,458 | **GPL-2.0** | 同上，且更强 | ❌ 不可 fork |
| newsnext/newsnow | 22,080 | MIT | 可商用 | ⚠️ TypeScript，无证据层 |
| imsyy/DailyHotApi | 4,090 | **MIT** | 可商用 | ✅ 见 §3.1 |
| KKKKhazix/AIHOT | 8,033 | MIT | 可商用 | ⚠️ TS，2026-09 新建，太新 |
| RSSNext/Folo | 39,119 | **AGPL-3.0** | 网络服务即触发开源义务 | ❌ 不可嵌入 |

TrendRadar 值得单独说一句：它的 MCP 层（`mcp_server/tools/` 下 9 个工具）与 SQLite
存储的设计**和你的 A 面高度相似**，读架构有参考价值，但 **GPL 意味着连抄代码都不行**。
只可读思路，不可取代码。

### 2.2 中文平台采集：许可证与你的边界双重冲突

| 项目 | stars | 许可证 | 阻断原因 |
|---|---|---|---|
| NanmiCoder/MediaCrawler | 67,147 | **非商业学习许可 1.1** | 见下方原文 |
| JoeanAmier/XHS-Downloader | 13,046 | GPL-3.0 | copyleft + 走签名逆向 |
| JoeanAmier/TikTokDownloader | 16,961 | GPL-3.0 | copyleft + 走签名逆向 |
| lixi5338619/lxSpider | 1,974 | GPL-3.0 | topics 直接写 `douyinsignature` |
| dataabc/weibo-crawler | 4,671 | **无许可证** | 无许可 = 保留所有权利 |
| 3441293738/creatorhub | 2,396 | **无许可证** | 同上 |
| pbeenigg/LittleCrawler | 757 | MIT | ✅ 唯一过线，但仅小红书/知乎/闲鱼 |

MediaCrawler 的 LICENSE 原文（我已下载核实，非转述）：

> "the Software is limited to learning and research purposes only, and **may not be
> used for large-scale crawling** or activities that disrupt platform operations."
>
> 「未经版权所有者书面同意，**不得将本软件用于任何商业用途**。」

它覆盖的平台恰好是你最想要的七个，但**一行代码都不能进你的仓库**。
GitHub API 报 `NOASSERTION` 正是因为它不是标准协议——这类项目最容易被误判为"开源就能用"。

### 2.3 深度研究 / 核查：没有一个同时具备"生成 + 可审计证据"

| 项目 | stars | 许可证 | 多轮 | 草稿版本 | 引用反解 | 独立信源计数 | 矛盾检测 |
|---|---|---|---|---|---|---|---|
| assafelovic/gpt-researcher | 30,163 | Apache-2.0 | 部分 | ❌ | 部分 | 部分 | ❌ |
| stanford-oval/storm | 31,665 | MIT | ✅ | 部分 | 部分 | ❌ | ❌ |
| langchain-ai/open_deep_research | 12,681 | MIT | ✅ | ❌ | 部分 | ❌ | ❌ |
| dzhng/deep-research | 19,791 | MIT | ✅ | ❌ | ❌ | ❌ | ❌ |
| superwesleyhys-ux/factcircuit | 768 | MIT | ✅ | ✅ | ✅ | ✅ | ✅ |

`open_deep_research` 已 **archived=true**，不能当长期上游。

**关键错配（这决定 fork 与否）**：gpt-researcher README 明写其可靠性假设是
"抓的站点越多，全都错的可能性极低"——**按站点频率投票**。
而你的 `src/corpus/trust.py` 是**先按簇折叠、再数不同 `(source_type, publisher)`**，
并用 noisy-OR 聚合。方向相反：fork 生成型项目 = 用朴素频率投票替换掉你唯一的差异化。
所以 README 里那句"能力已实现、效果未主张"的坦白，反而是你**不该 fork** 这些项目的理由。

`factcircuit` 是唯一站在证据侧的（exact span 反解、publisher 谱系去重、矛盾记录），
但它是**核查器不是研究者**：无报告生成、无 `claimable` 分层、无 draft revision。
768★ 单人 research preview，作者自述未建立准确率优势。**只借架构，不当上游。**

---

## 3. 真正值得引进的三块（按性价比排序）

### 3.1 DailyHotApi —— 补齐 10 / 12 个浏览器源（最高价值）

这是我找到的**唯一同时满足 MIT + 中文热榜 + 无需浏览器**的项目。

我把你的 `CATALOG` 里 `needs_browser=True` 的 12 个源，与它的 56 个路由命名空间做了精确比对：

| 你的浏览器源 | DailyHotApi 路由 | 覆盖 |
|---|---|---|
| `weibo_hot` 微博热搜 | `weibo` | ✅ |
| `zhihu_hot` 知乎热榜 | `zhihu` | ✅ |
| `douyin_hot` 抖音热点榜 | `douyin` | ✅ |
| `kuaishou_hot` 快手热榜 | `kuaishou` | ✅ |
| `bili_popular_browser` B站排行 | `bilibili` | ✅ |
| `tieba_python` 贴吧 | `tieba` | ✅ |
| `douban_explore` 豆瓣 | `douban-group` | ✅ |
| `hupu` 虎扑 | `hupu` | ✅ |
| `smzdm` 什么值得买 | `smzdm` | ✅ |
| `producthunt` | `producthunt` | ✅ |
| `xhs_explore` 小红书 | 无 | ❌ |
| `threads` | 无 | ❌ |

**10 / 12**。这直接命中你 `foundation.md` §6 第 6 条"深度模式未在线验证"的根因：
小红书/抖音/快手受登录态与风控限制。热榜类根本不需要浏览器登录。

代价与风险（必须知道）：

- **必须自建**。我实测 `https://api-hot.imsyy.top/bilibili` 在本机 **SSL EOF 失败**，
  公共实例不可依赖。自建需要 Node 运行时（Vercel/Cloudflare 部署也是一条路）。
- **2026-03-11 之后无提交**（约 7 个月）。热榜接口会随平台改版腐烂，自建意味着自己修路由。
- 它是 **TypeScript**，不能 import 进 Python；只能当 **HTTP 旁路服务**，与你 `fanout.py`
  的 HTTP 源同构——这反而是好事，接进来就是加一个 `Source` 条目。

### 3.2 wangfenjin/simple —— 中文分词（MIT 分支）

- 870★，C++，`NOASSERTION` 只是因为 **MIT/GPL-3.0 双许可**，许可证原文：
  "dual license system (MIT or GPL version 3…). You are free to choose" → **选 MIT 即可闭源商用**。
- 它是 SQLite FTS5 的可加载扩展，`conn.load_extension()` 直接进你那 1.56 GB 的库，
  **无需服务进程**，不破坏你"单机单文件"的形态。
- 提供中文切词 + 拼音，可替代现在的 trigram。

**但要先说清一件事**：子代理报告里说"trigram 对 2 字中文查询返回空是 Periscope 的 bug"——
**这条不成立，我复现后退了**。SQLite 3.49.1 实测 `MATCH '降息'` 确实返回 0，
但你的两条检索路径都已经有 LIKE 兜底：

- `core/search.py:226-276` — `short_terms` 走 `LIKE`，并注明"trigram 只能匹配 >=3 字符"
- `src/corpus/store.py:497-502` — `len(term) < 3` 同样分流到 `like_terms`

所以它是**已被设计规避的库限制**，不是缺陷。`simple` 的价值是**把 LIKE 全表扫描换成真索引**
（性能与排序质量），不是"修 bug"。优先级低于 3.1。

### 3.3 datasketch —— 替换手写 SimHash 聚簇

- 2,971★，Python，MIT，2026-10-10 活跃。
- MinHash + LSH 作用在 **token 集合**上，与语言无关：中文直接用**字符 n-gram 当 token**，
  不需要分词库。
- 可替掉 `src/corpus/simhash.py` 的手写聚簇。注意你的语义是"尽力而为分组，什么都不删"，
  换库时要保住这条——LSH 只负责候选召回，聚类与保留策略仍是你的。

**不推荐**：`dedupeio/dedupe`（MIT 但需人工标注成对样本训练，无人值守摄取管线喂不了）；
`txtai`（Apache-2.0 但会拖入 torch 全家桶，只为 RRF 不值得）；
`sqlite-vec` 现在用没问题，但它仍是 `v0.1.10-alpha.4` 且无 ANN 索引，别把语义检索的默认开关打开。

---

## 4. RSSHub：能用，但只能用一条路

| 项 | 实测 |
|---|---|
| stars / 许可证 | 46,579 / **AGPL-3.0** |
| 路由文件 | 6,806 个（`lib/routes/`），未截断 |
| 中文命名空间 | **257** 个路由文件 |
| 分平台 | bilibili 54、zhihu 33、douban 33、douyin 13、weibo 12、wechat 12、xiaohongshu 5、kuaishou 2 |

覆盖密度确实高，但：

1. **AGPL 网络传染**：只能**独立部署 + HTTP 消费 RSS**（进程隔离，非衍生作品）。
   **绝不能 import 它的 route 函数，也不能 vendor 代码。**
2. **登录态没消失，只是转移**：微博/小红书/抖音/知乎的高价值路由照样要配 cookie。
   我实测本机 `weibo.com/ajax/side/hotSearch` 返回 **403**，而
   `api.bilibili.com/x/web-interface/search/all/v2` 匿名访问 **code=0 成功**——
   说明"要不要登录"是**分平台**的，不是一个统一开关。
3. **自建需 Node + Redis**，是一套要运维的旁路服务。
4. 平台改版即路由失效，修复节奏在上游手里。

**结论**：RSSHub 适合吃"资讯/论坛/媒体长尾"，**不适合**当核心，也**解决不了**小红书/抖音。

---

## 5. 一个被高估的项目：Agent-Reach

99,185★、MIT、Python、README 明写支持 Bilibili/XiaoHongShu，看起来是最优解。
**我读了它的源码，它不是采集器。**

`agent_reach/channels/base.py` 的 `Channel` 抽象只有两个方法：
`can_handle(url)`（这个 URL 属不属于我）与 `check(config)`（上游工具装没装）。
`channels/bilibili.py` 的后端是 `["bili-cli", "OpenCLI", "B站搜索 API"]`——
它**探测**这些外部工具是否可用，真正干活的是别人。`backends/opencli.py` 更是去
检查 Chrome 扩展和 `~/.opencli` 目录。

它对你的真实价值只有一条**已验证的情报**，而且很有用：

> `channels/bilibili.py` 注释：yt-dlp 被 B 站风控 **412 拦截**（"in every configuration we
> tried — latest version, direct, proxied, with warmed cookies"），已从 B 站后端移除；
> `bili-cli` 可匿名做 search/hot/video detail。

这与你的 `foundation.md` 里"匿名拿不到字幕、需 SESSDATA"互为印证。

---

## 6. 建议

**不换地基。** 按下面三档推进（每档都可独立回退）：

| 优先级 | 动作 | 依据 | 风险 |
|---|---|---|---|
| **P0** | 自建 DailyHotApi，把 10 个热榜源接成 HTTP `Source` | MIT，命中 10/12 浏览器源 | 7 个月未更新，需自己修路由 |
| **P1** | 把 `13/179` 的接入率提上去，而不是加新源 | `CATALOG` 已有 179 源 | 纯内部工作，无许可证风险 |
| **P2** | `simple`(MIT) 换 trigram、`datasketch` 换手写 SimHash | 性能与排序质量 | 动到 1.56 GB 库，需迁移测试 |

明确不建议：fork TrendRadar / BettaFish（GPL）、抄 MediaCrawler（非商用）、
嵌 RSSHub 代码（AGPL）、拿 gpt-researcher 当上游（会用频率投票吃掉你的独立信源计数）。

---

## 7. 本次调研的边界（不粉饰）

- `web_search` 工具缺 `DEEPSEEK_API_KEY`，全程改用 GitHub API + raw 直连。
- GitHub 核心 API 在过程中触发 60 次/小时限流，部分仓库只能拿到 search 接口的元数据。
- 本机网络对 `api-hot.imsyy.top` 与 `weibo.com` 存在阻断/403，**未能在线验证
  DailyHotApi 的实时输出质量**——这是 P0 落地前必须先验的一步。
- 各项目的"能否接本地语料"多为读 README/源码判断，未逐一跑通。

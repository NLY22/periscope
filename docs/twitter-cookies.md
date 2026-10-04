# Twitter / X Cookie 配置指南（Playwright 模式）

Periscope 支持两种 Twitter 抓取方式：

| 方式 | 成本 | 稳定性 | 适用场景 |
|------|------|--------|----------|
| **Apify** (默认) | 需订阅 ($49/月起) | ⭐⭐⭐ 高 | 生产环境、大量账号 |
| **Playwright + Cookie** (免费) | 免费 | ⭐⭐ 中 | 个人使用、少量账号 |

本文档介绍免费方案的配置方法。

---

## 1. 安装依赖

```bash
uv sync --extra twitter
uv run playwright install chromium
```

---

## 2. 获取 Cookie

### 方法一：浏览器扩展（推荐）

1. 用 Chrome/Edge/Firefox 登录 [x.com](https://x.com)
2. 安装扩展 **"Cookie-Editor"** 或 **"Get cookies.txt LOCALLY"**
3. 在 x.com 页面打开扩展，导出为 **JSON 格式**
4. 保存到 `data/x_cookies_1.json`

### 方法二：开发者工具

1. 登录 x.com 后按 `F12` 打开开发者工具
2. 切换到 **Application (应用)** → **Cookies** → `https://x.com`
3. 找到以下关键 Cookie（名称可能略有不同）：
   - `auth_token`
   - `ct0`
   - `twid`
4. 手动构建 JSON 数组：

```json
[
  {
    "name": "auth_token",
    "value": "你的auth_token值",
    "domain": ".x.com",
    "path": "/",
    "secure": true,
    "httpOnly": true
  },
  {
    "name": "ct0",
    "value": "你的ct0值",
    "domain": ".x.com",
    "path": "/",
    "secure": true,
    "httpOnly": false
  }
]
```

---

## 3. 配置文件

编辑 `data/config.json`：

```json
{
  "sources": {
    "twitter": {
      "enabled": true,
      "mode": "playwright",
      "users": ["karpathy", "ylecun"],
      "fetch_limit": 10,
      "cookie_dir": "data",
      "cookie_file_pattern": "x_cookies_*.json"
    }
  }
}
```

Keyword search (`sources.twitter.keywords`) is Apify-only. Playwright logs a warning and skips those queries.

---

## 4. 多个 cookie 文件：代码会做什么，本项目允许什么

先把两件事分开说，它们很容易被混在一起。

**代码会做的**（`src/scrapers/twitter_playwright.py`）：`cookie_dir` 下所有匹配 `cookie_file_pattern` 的文件会被排序后**逐个开一个浏览器上下文**，然后把配置的账号列表切成同样数量的队列分头抓，失败的一轮之后重试一次。每个上下文的 UA 里的 Chrome 版本号按序号递增。

**本 fork 允许的**：**一个账号，你自己的账号，只取这个账号本来就能看到的内容。** 多账号池不是"稳定性配置"，而是本仓库写明不做的采集边界 —— 见 [SECURITY.md](../SECURITY.md) 的反爬边界与设计 spec §11（验证码打码、签名逆向、多账号池一律排除）。按这个边界，上面那段"每个上下文 UA 递增"属于反爬对抗的形态，**本 fork 不支持基于它的用法，也不会合并依赖它的改动**；如果你的目的就是把请求分散到多个账号上，这个项目不是合适的载体。

有一个实际后果与是否只有一个账号无关：**过期的旧导出也会被当成第二个上下文**。比如 `data/x_cookies_1.json` 旁边留了一份 `x_cookies_stale.json`，代码会开两个上下文并把账号列表切成两半，旧那份预热失败，于是它分到的账号直接抓不到（日志里是 `Cookie #2 warm-up failed` 与 `page shows login gate`）。现在**开跑前就会点名**：匹配到多个 cookie 文件时日志先警告一句本 fork 的采集边界是一个账号，并说明账号列表会被切成几份 —— 那句话是让你删掉多余的那份，不是让你再多准备几个。重新导出就覆盖原文件，别留第二份。

单账号的正常配置就是一个文件：

```
data/x_cookies_1.json   # 你自己在浏览器里登录后的导出
```

---

## 5. 代理配置（可选）

如果在中国大陆或需要代理：

```bash
export https_proxy=http://127.0.0.1:7890
```

Playwright 会自动读取 `PROXY`、`https_proxy`、`http_proxy`、`all_proxy` 环境变量。

---

## 6. 注意事项

⚠️ **Cookie 有有效期**：通常 1-4 周，过期后需要重新导出  
⚠️ **不要提交 Cookie 文件**：已加入 `.gitignore`，请妥善保管  
⚠️ **账号选择**：本 fork 的采集边界是"你自己的账号，只取它本来就能看到的内容"（第 4 节）。准备多个小号来分散封禁风险不在这个边界之内，也不是这份指南要帮你做的事  
⚠️ **抓取频率**：每账号间隔 5-10 秒；被限流就停下来过一阵再跑，而不是把间隔调到最小或换账号重试

---

## 7. 故障排除

| 问题 | 原因 | 解决 |
|------|------|------|
| "No cookie files found" | cookie 文件名不匹配 | 检查 `cookie_file_pattern` 和实际文件名 |
| "page shows login gate" | cookie 已过期 | 重新登录并导出最新 cookie |
| "no GraphQL data intercepted" | 页面结构变化或被限流 | 等几分钟再跑；**不要靠加 cookie 文件绕过**（见第 4 节的采集边界），先减少 `fetch_limit` 与账号数 |
| Playwright 未安装 | 依赖缺失 | 运行 `uv sync --extra twitter && uv run playwright install chromium` |

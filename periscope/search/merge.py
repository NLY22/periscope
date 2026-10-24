"""Cross-source display clustering and scoring; raw rows remain in the run store."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import html
import re

from .identity import canonical_url


# ---------------------------------------------------------------- 归一化


def norm_text(s: str) -> str:
    """去掉空白、标点、下划线、常见的栏目前后缀，只留实词。

    搜索引擎返回的标题常带站点名后缀（`_哔哩哔哩_bilibili`）、
    栏目前缀（`相关视频 - `）、以及为了高亮插入的空格（`人形 机器人`）。
    不清理的话同一篇内容会因为来源不同而算作两条。

    归一化必须先于**聚类键**的截取发生，所以这里清理的东西会直接决定
    两条标题算不算同一条 —— 漏一种形态就是一次漏合并。
    """
    s = s or ""

    # HTML 实体必须先解开，而且要早于标点清理：
    #
    # 转载站会把 `&` 写成 `&amp;`，于是「A &amp; B 公司发布新产品」与
    # 「A & B 公司发布新产品」归一化后一个留 `&amp`、一个留 `&`，
    # 指纹不同，同一条新闻算两条。这一步放在标点清理之前，
    # `&` 是标点、会在下一步被删掉，两边才真正对齐。
    #
    # 只解一层：`&amp;amp;` 是作者本来就想写 `&amp;`，解两次会改掉原文。
    s = html.unescape(s)

    # 站点名后缀（可能带下划线或横线前缀）
    s = re.sub(r"[_\-|｜\s]*(哔哩哔哩|bilibili|知乎|微博|抖音|今日头条|新浪\S*|搜狐\S*|国家一类新闻网站)$",
               "", s, flags=re.IGNORECASE)
    # 栏目前缀
    s = re.sub(r"^(相关视频|相关阅读|查看更多|视频丨|视频｜)\s*[-:：]\s*", "", s)
    # 空白与下划线全部去掉（中文标题里它们只是分隔符）
    s = re.sub(r"[\s\u3000_]+", "", s)
    # 标点与符号：用显式字符集，避免转义告警
    s = re.sub("[" + re.escape("，。！？、；：“”‘’（）【】《》…—·,.!?;:\"'()[]<>|/-") + "]", "", s)
    # HTML 实体残渣：google_news 的标题里带 `\u003cem\u003e` 这类转义，
    # 不清理会污染指纹、也会让展示的标题出现乱码
    s = re.sub(r"\\u003c/?em\\u003e", "", s)
    s = re.sub(r"\\u003c[^\\]*\\u003e", "", s)
    s = re.sub(r"</?em>", "", s)
    return s.lower()


def _dedupe_repeats(s: str) -> str:
    """去掉标题里自我重复的后半段。

    转载站常把标题写两遍：
        `钠离子电池加速从实验室走向应用场 - 钠离子电池加速从实验室走向应用场`
    norm 之后变成同一句话拼接两次。检测并截断。
    """
    n = len(s)
    if n < 8:
        return s
    half = n // 2
    # 前一半与后一半高度重合（允许几字误差）
    a, b = s[:half], s[half:half * 2]
    if len(a) >= 6 and a[:min(len(a), len(b)) - 2] == b[:min(len(a), len(b)) - 2]:
        return a
    return s


def key_of(title: str, url: str = "") -> str:
    """聚类键：归一化标题里最长的那一段内容。

    实测失败的例子：
        「中国人形机器人走上世界舞台」
        「可以凌空飞踢！中国人形机器人走上世界舞台」
    两者讲同一件事（都源自人民日报海外版），但后者带前置修饰。
    前缀匹配对不上，而它们的共同核心是「中国人形机器人走上世界舞台」。

    做法：**在归一化之前**按标点切段（归一化会删掉标点，之后就找不到
    分界了），取最长的段。这是唯一能覆盖「短句修饰 + 完整内容」的办法。
    """
    raw = (title or "").strip()
    if not raw:
        return "u:" + (url or "")[:64] if url else ""

    # 先按原文字的标点切段，再各自归一化
    segs = re.split(r"[！!？?。…—\|\|]", raw)
    normed = [_dedupe_repeats(norm_text(x)) for x in segs]
    normed = [x for x in normed if len(x) >= 6]

    if normed:
        t = max(normed, key=len)
    else:
        t = _dedupe_repeats(norm_text(raw))

    if len(t) < 6:
        return "u:" + (url or "")[:64] if url else ""

    # 去掉开头的非内容词
    t = re.sub(r"^(视频|组图|图集|直播|快讯|独家|重磅)", "", t)
    return t[:20]


def title_fingerprint(title: str) -> str:
    """兼容旧调用：返回 key_of 的哈希。"""
    k = key_of(title)
    return hashlib.sha1(k.encode("utf-8")).hexdigest()[:12] if k else ""


# ---------------------------------------------------------------- 打分


def score_of(row: dict) -> float:
    """把不同来源的分数映射到 0-1。

    三种输入：
      - dsapi 的行：有 authority(0-4) 与 rerank(0-3)
      - 平台的行：有 trust(0-1)，没有 rerank
      - 本地语料的行：有 trust(0-1)

    权重按实测含义定：authority 是「来源是否权威」，rerank 是
    「与查询的相关度」。转载合并后再加上「几个独立来源」的加成。

    平台行额外加一项「新鲜度」：它的独特价值就是「现在正在发生」。
    实测不加这一项时，API 的 authority=4 权威媒体（人民网/央广网）
    会拿满分 1.0，而平台的 trust 只有 0.6，结果 Top 10 全是 API——
    平台采到的当天微博一条也进不来。而「今天微博上在说什么」
    恰恰是 API 给不了的。
    """
    a = row.get("authority")
    r = row.get("rerank")
    t = row.get("trust")

    parts = []
    if a is not None and a >= 0:
        parts.append(("auth", min(max(a, 0) / 4.0, 1.0)))
    if r is not None and r > 0:
        parts.append(("rerank", min(r / 3.0, 1.0)))
    if t is not None:
        parts.append(("trust", min(max(float(t), 0.0), 1.0)))

    if not parts:
        base = 0.5
    else:
        # 权威性与相关性各占一半；只有一种时就用它
        weights = {"auth": 0.5, "rerank": 0.5, "trust": 0.5}
        num = sum(v * weights[k] for k, v in parts)
        den = sum(weights[k] for k, _ in parts)
        base = num / den if den else 0.5

    # 新鲜度加成：24 小时内 +0.12，3 天内 +0.06。
    # 只对平台/本地行生效（API 的日期本来就滞后一天以上）。
    if row.get("_origin") in ("platform", "local"):
        base += _freshness_bonus(row.get("date") or row.get("published_at") or "")

    return min(base, 1.0)


def _freshness_bonus(datestr: str) -> float:
    """按日期离现在多远给加成。解析不出来就不加。"""
    import datetime as _dt

    s = (datestr or "")[:10]
    if len(s) != 10:
        return 0.0
    try:
        d = _dt.date.fromisoformat(s)
    except Exception:  # noqa: BLE001
        return 0.0
    today = _dt.datetime.now(_dt.timezone.utc).date()
    days = (today - d).days
    if days < 0:
        return 0.0        # 未来日期，可能是解析错了，不加
    if days <= 1:
        return 0.12
    if days <= 3:
        return 0.06
    if days <= 7:
        return 0.02
    return 0.0


# ---------------------------------------------------------------- 合并


@dataclass
class Cluster:
    """一组讲同一件事的条目（可能是多家转载）。"""

    key: str
    rows: list[dict] = field(default_factory=list)

    @property
    def head(self) -> dict:
        """代表条目：分数最高的那条。"""
        return max(self.rows, key=score_of)

    @property
    def publishers(self) -> set[str]:
        out = set()
        seen_urls = set()
        for r in self.rows:
            identity = canonical_url(r.get("url") or "")
            if identity and identity in seen_urls:
                continue
            p = (r.get("author") or r.get("site_name") or
                 r.get("publisher") or r.get("source") or "")
            p = p.strip()
            if p:
                out.add(p[:40])
                if identity:
                    seen_urls.add(identity)
        return out

    @property
    def independent(self) -> int:
        return len(self.publishers)

    def merged(self) -> dict:
        """合并成一条：用代表条目的内容，但记录独立来源数。"""
        h = dict(self.head)
        n = self.independent
        h["_publishers"] = sorted(self.publishers)
        h["_independent"] = n
        h["_variants"] = len(self.rows)
        h["sources"] = [
            {"url": row.get("url") or "", "title": row.get("title") or "",
             "source": row.get("source") or "", "origin": row.get("_origin") or "",
             "provenance": row.get("provenance") or {}}
            for row in self.rows
        ]
        h["body_source_url"] = h.get("body_source_url") or h.get("url") or ""
        # 独立来源数作为加成：3 家以上独立报道，可信度上调
        bonus = min((n - 1) * 0.06, 0.18)
        h["_score"] = min(score_of(h) + bonus, 1.0)
        # 正文取最长的那份（转载常有删减）
        best = max(self.rows, key=lambda r: len(r.get("body") or r.get("text") or ""))
        body = best.get("body") or best.get("text") or ""
        if len(body) > len(h.get("body") or h.get("text") or ""):
            h["body"] = body
            h["text"] = body
            h["body_source_url"] = best.get("body_source_url") or best.get("url") or ""
            if best.get("document"):
                h["document"] = best["document"]
            else:
                h.pop("document", None)
        return h


def merge(api_rows: list[dict], platform_rows: list[dict],
          cluster_all: bool = True) -> list[dict]:
    """合并两路结果并去重。

    `cluster_all=False` 时只在各自内部去重，不合并跨路 —— 用于想看
    「API 给了什么、我们自己给了什么」的对照场景。
    """
    buckets: dict[str, Cluster] = {}
    order: list[str] = []
    url_buckets: dict[str, str] = {}
    # Establish resource identity before API rows can form title clusters.
    exact_identities = {canonical_url(row.get("url") or "") for row in (*api_rows, *platform_rows)
                        if (row.get("provenance") or {}).get("identity") == "url"} - {""}

    def find_bucket(key: str) -> str:
        """找一个已存在的、与 key 互为前缀的桶。"""
        if key in buckets:
            return key
        if not key or key.startswith("u:"):
            return key
        for exist in order:
            if exist.startswith("u:") or not exist:
                continue
            # 短的是长的前缀，或反之
            short, long_ = (exist, key) if len(exist) <= len(key) else (key, exist)
            if long_.startswith(short) and len(short) >= 8:
                return exist
        return key

    def add(row: dict) -> None:
        identity = canonical_url(row.get("url") or "")
        exact_resource = identity in exact_identities
        key = "u:" + identity if exact_resource else key_of(row.get("title") or "", row.get("url") or "")
        if not key:
            return
        target = url_buckets.get(identity) if identity else None
        target = target or find_bucket(key)
        if target not in buckets:
            buckets[target] = Cluster(key=target)
            order.append(target)
        buckets[target].rows.append(row)
        if identity:
            url_buckets[identity] = target

    for r in api_rows:
        add(r)
    for r in platform_rows:
        add(r)

    merged = [buckets[k].merged() for k in order]

    # 排序：先按合并后的分数，并列时独立来源多的优先。
    #
    # 无正文的条目降一档：实测排出来第 3 位是一条 0 字的 google_news，
    # 因为它的 trus 高、又没有正文可扣分。用户看到的应该是能读的内容。
    def sort_key(r: dict):
        body = len(r.get("body") or r.get("text") or "")
        has_body = 1 if body >= 400 else 0
        return (-has_body, -(r.get("_score") or 0),
                -(r.get("_independent") or 1))

    merged.sort(key=sort_key)

    if not cluster_all:
        api_sorted = sorted(api_rows, key=lambda r: -score_of(r))
        plat_sorted = sorted(platform_rows, key=lambda r: -score_of(r))
        return api_sorted + plat_sorted

    return merged


def stats(merged: list[dict]) -> dict:
    """给输出用的小结。"""
    n = len(merged)
    multi = sum(1 for r in merged if (r.get("_independent") or 1) > 1)
    bodies = sum(1 for r in merged
                 if len(r.get("body") or r.get("text") or "") >= 400)
    return {
        "total": n,
        "with_body": bodies,
        "multi_source": multi,
        "avg_independent": round(
            sum((r.get("_independent") or 1) for r in merged) / n, 2) if n else 0,
    }

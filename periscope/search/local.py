"""Local FTS5 retrieval, relevance reranking and selected-result body hydration."""

from __future__ import annotations

from pathlib import Path
import argparse
import json
import math
import re
import sqlite3

from periscope.runtime import paths


OUT_DEFAULT = paths.SCRATCH / "research_out.txt"
CORPUS = paths.CORPUS

# trigram 能参与匹配的最短长度
MIN_GRAM = 3
# 正文长度到这个数就不再加分（超过 5,000 字的文章，长度不是质量信号）
BODY_SATURATION = 5000
MIN_BODY = 400


def force_stdout() -> None:
    """UTF-8 console output for the CLI only. See `core/console.py`."""
    from periscope.runtime.console import force_utf8_output

    force_utf8_output()


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(str(CORPUS), timeout=15.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA temp_store=MEMORY")
    # 采集/抓正文进程可能在写库。默认 busy_timeout 是 0，一撞就抛
    # `database is locked`；15 秒的等待足够让写事务完成，读请求只是稍慢。
    db.execute("PRAGMA busy_timeout=15000")
    return db


def terms_of(topic: str) -> list[str]:
    """把话题拆成词。

    中文没有空格分词，`人形机器人` 就是一个词；英文数字要单独切出来，
    否则 `DeepSeek R1` 会变成一个 11 字的串，短语匹配反而更差。
    """
    topic = (topic or "").strip()
    if not topic:
        return []
    out: list[str] = []
    for chunk in re.split(r"\s+", topic):
        if not chunk:
            continue
        for m in re.finditer(r"[\u4e00-\u9fff]+|[A-Za-z0-9][A-Za-z0-9\.\-\+]*", chunk):
            out.append(m.group(0))
    return out or [topic]


def fts_phrase(s: str) -> str:
    return '"' + s.replace('"', '""') + '"'


def build_match(terms: list[str]) -> tuple[str, list[str]]:
    """构造 MATCH 表达式，返回 (表达式, 被丢弃的短词)。"""
    usable = [t for t in terms if len(t) >= MIN_GRAM]
    dropped = [t for t in terms if len(t) < MIN_GRAM]
    if not usable:
        return "", dropped
    return " AND ".join(fts_phrase(t) for t in usable), dropped


def _norm(s: str) -> str:
    """归一化：去掉所有空白，只保留实字符。

    为什么必须这么做：平台侧返回的标题经常在词中间插空格，用来做搜索
    高亮。实测 CSDN 搜索「深海采矿」返回的标题是
    `深海 采矿 技术图谱：从勘探到商业化的关键路径` —— 三元组索引能命中，
    但打分时 `"深海采矿" in title` 是 False，相关性被算成 0，
    结果那一批最相关的长文全排到了短微博后面。

    中文没有词间空格，所以去掉空白不会把两个不同的词粘在一起。
    """
    return "".join((s or "").split())


def search(db: sqlite3.Connection, topic: str, limit: int,
           prefer_body: bool = True) -> tuple[list, str]:
    """返回 (行, 说明)。

    trigram 分词器只能匹配 >=3 字符的串，而中文里两字词极多
    （芯片、车企、降价、幻觉、量产、面试、宇树）。对这些词 FTS 完全无能，
    只能走 LIKE；但 LIKE 也必须打分排序，否则「按正文长度倒序」会把
    随便一篇长文顶上来 —— 压力测试里 `AI` 的第一名就是这样变成
    "Vector Database benchmark" 的。

    所以这里有两条路径，都能给出排序后的结果：
      长词 -> FTS + bm25 候选池 -> hybrid 重排
      短词 -> LIKE 扫描（限制行数）-> 同样的 hybrid 重排
    长短混合时先各取一半再合并。
    """
    terms = terms_of(topic)
    long_terms = [t for t in terms if len(t) >= MIN_GRAM]
    short_terms = [t for t in terms if len(t) < MIN_GRAM]
    notes: list[str] = []

    rows: list = []

    if long_terms:
        match = " AND ".join(fts_phrase(t) for t in long_terms)
        pool = max(limit * 20, 120)
        try:
            rows = db.execute("""
                SELECT i.id, i.title, i.publisher, i.source_type, i.published_at, i.url,
                       i.author, i.trust, LENGTH(COALESCE(i.content,'')) clen,
                       SUBSTR(COALESCE(i.content,''),1,400) snippet,
                       bm25(items_fts) bm
                FROM items i JOIN items_fts f ON i.rowid = f.rowid
                WHERE items_fts MATCH ?
                ORDER BY bm25(items_fts) LIMIT ?
            """, (match, pool)).fetchall()
        except sqlite3.OperationalError:
            rows = []

        # BM25 偏爱短文本，所以「BM25 前 N」里几乎没有长文：实测
        # 「人形机器人」库中有 238 条带正文的命中，但 BM25 前 120 条里
        # 带正文的不足 5 条 —— 重排器再聪明也没材料可选。
        #
        # 因此候选池要由两部分组成：BM25 前排（保相关性）+ 带正文的
        # 命中（保内容量）。两部分各自有上限，合并后去重再重排。
        body_pool = max(limit * 15, 90)
        try:
            body_rows = db.execute("""
                SELECT i.id, i.title, i.publisher, i.source_type, i.published_at, i.url,
                       i.author, i.trust, LENGTH(COALESCE(i.content,'')) clen,
                       SUBSTR(COALESCE(i.content,''),1,400) snippet,
                       bm25(items_fts) bm
                FROM items i JOIN items_fts f ON i.rowid = f.rowid
                WHERE items_fts MATCH ? AND LENGTH(COALESCE(i.content,'')) >= ?
                ORDER BY i.trust DESC, clen DESC LIMIT ?
            """, (match, MIN_BODY, body_pool)).fetchall()
        except sqlite3.OperationalError:
            body_rows = []

        # 第三部分：标题里直接含查询词的。
        # 实测「深海采矿」的 BM25 前排被微博短帖占满（每条 150 字，
        # 正文里顺带提了一句采矿），而 CSDN 那批标题就叫《深海采矿 4大
        # 子系统》的长文进不了队列。标题命中是最强的切题信号，
        # 所以单独取一批，不受 BM25 的长度偏好影响。
        title_pool = max(limit * 10, 60)
        title_rows: list = []
        for t in long_terms[:2]:
            try:
                got = db.execute("""
                    SELECT i.id, i.title, i.publisher, i.source_type, i.published_at, i.url,
                           i.author, i.trust, LENGTH(COALESCE(i.content,'')) clen,
                           SUBSTR(COALESCE(i.content,''),1,400) snippet,
                           bm25(items_fts) bm
                    FROM items i JOIN items_fts f ON i.rowid = f.rowid
                    WHERE items_fts MATCH ?
                      AND REPLACE(i.title, ' ', '') LIKE ?
                    ORDER BY LENGTH(COALESCE(i.content,'')) DESC LIMIT ?
                """, (match, "%" + _norm(t) + "%", title_pool)).fetchall()
                title_rows.extend(got)
            except sqlite3.OperationalError:
                pass

        seen_keys = set()
        merged: list = []
        for group in (title_rows, body_rows, rows):
            for r in group:
                k = _norm(r["title"] or "")[:80]
                if not k or k in seen_keys:
                    continue
                seen_keys.add(k)
                merged.append(r)
        rows = merged

        if not rows and len(long_terms) > 1:
            # 多词 AND 无交集：放宽到最长那个词
            longest = max(long_terms, key=len)
            try:
                rows = db.execute("""
                    SELECT i.id, i.title, i.publisher, i.source_type, i.published_at, i.url,
                           i.author, i.trust, LENGTH(COALESCE(i.content,'')) clen,
                           SUBSTR(COALESCE(i.content,''),1,400) snippet,
                           bm25(items_fts) bm
                    FROM items i JOIN items_fts f ON i.rowid = f.rowid
                    WHERE items_fts MATCH ?
                    ORDER BY bm25(items_fts) LIMIT ?
                """, (fts_phrase(longest), pool)).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if rows:
                notes.append("多词无交集，改用「%s」" % longest)

    if short_terms:
        # 短词必须走 LIKE。多个短词时要求**全部命中**，而不是任一命中：
        # `AI 面试` 若取「含 AI 或含 面试」，`AI` 会把大量无关文章带进来，
        # 实测第 1 名变成了 iPhone 新闻。用户打两个词是想同时限定。
        #
        # 先试全部命中的条件；命中太少时才放宽到任一命中。
        like_pool = max(limit * 40, 240)
        params_all: list = []
        for t in short_terms:
            params_all.extend(["%" + t + "%", "%" + t + "%"])
        and_cond = " AND ".join(
            "(title LIKE ? OR content LIKE ?)" for _ in short_terms)
        params_all.append(like_pool)
        short_rows = []
        try:
            short_rows = db.execute("""
                SELECT id, title, publisher, source_type, published_at, url, author,
                       trust, LENGTH(COALESCE(content,'')) clen,
                       SUBSTR(COALESCE(content,''),1,400) snippet,
                       0.0 bm
                FROM items WHERE %s
                ORDER BY trust DESC LIMIT ?
            """ % and_cond, params_all).fetchall()
        except sqlite3.OperationalError:
            short_rows = []

        if len(short_rows) < limit and len(short_terms) > 1:
            # 严格 AND 太少：放宽到 OR 补充
            or_cond = " OR ".join(
                "(title LIKE ? OR content LIKE ?)" for _ in short_terms)
            params_or: list = []
            for t in short_terms:
                params_or.extend(["%" + t + "%", "%" + t + "%"])
            params_or.append(like_pool)
            try:
                extra = db.execute("""
                    SELECT id, title, publisher, source_type, published_at, url, author,
                           trust, LENGTH(COALESCE(content,'')) clen,
                           SUBSTR(COALESCE(content,''),1,400) snippet,
                           0.0 bm
                    FROM items WHERE %s
                    ORDER BY trust DESC LIMIT ?
                """ % or_cond, params_or).fetchall()
                seen = {(r["title"] or "")[:60] for r in short_rows}
                short_rows = list(short_rows) + [
                    r for r in extra if (r["title"] or "")[:60] not in seen]
                notes.append("短词严格匹配不足，已放宽")
            except sqlite3.OperationalError:
                pass

        notes.append("短词走 LIKE: %s" % ",".join(short_terms))
        if short_rows:
            # 长词结果已有时，短词只补充，避免淹没
            if rows:
                seen_titles = {(r["title"] or "")[:60] for r in rows}
                extra = [r for r in short_rows
                         if (r["title"] or "")[:60] not in seen_titles]
                rows = list(rows) + extra
            else:
                rows = list(short_rows)

    if not rows:
        return [], " | ".join(notes) if notes else "无结果"

    if not long_terms and not short_terms:
        return [], "无法解析查询词"

    # ---------- 假阳性过滤 ----------
    # 结果必须真的含某个查询词。压力测试里 `不存在的词xyz123` 返回了 3 条：
    # trigram 是子串匹配，`xyz123` 的三元组撞上了别的英文词。
    #
    # 这里不能用「全空就保留原样」的写法 —— 那等于承认过滤没起作用。
    # 过滤后为空就返回空，让「查不到」和「查到了」在行为上可区分。
    # 查询词也归一化，才能和标题里的空格形态对上
    norm_terms = [_norm(t) for t in terms if _norm(t)]

    def contains_any(r) -> bool:
        blob = _norm((r["title"] or "") + (r["snippet"] or ""))
        return any(t in blob for t in norm_terms)

    before = len(rows)
    rows = [r for r in rows if contains_any(r)]
    if before and not rows:
        notes.append("%d 条候选均不含查询词，判定无结果" % before)
        return [], " | ".join(notes)

    scored = []
    for r in rows:
        title = r["title"] or ""
        n_title = _norm(title)
        n_blob = _norm(title + (r["snippet"] or ""))
        # 词越长越具体，命中它越能说明切题。`芯片 出口管制` 里
        # `出口管制`(4字) 的判别力远高于 `芯片`(2字) —— 后者是一个
        # 泛词，单独命中说明不了什么。实测：`芯片` 的候选量是
        # `出口管制` 的几十倍，不加权就会被泛词淹没，第 1 名变成
        # 一条 RRAM 芯片流片新闻。
        #
        # 权重用词长做温和放大（log），避免长词完全压过短词组合。
        total_w = 0.0
        hit_w = 0.0
        for t, nt in zip(terms, norm_terms):
            w = 1.0 + math.log1p(max(len(t) - 2, 0))
            total_w += w
            if nt and nt in n_title:
                hit_w += w * 1.0
            elif nt and nt in n_blob:
                hit_w += w * 0.30
        rel = hit_w / total_w if total_w else 0.0

        clen = r["clen"] or 0
        body = min(math.log1p(clen) / math.log1p(BODY_SATURATION), 1.0)

        trust = r["trust"] or 0.5
        # 权重按「这个项目要什么」定：用户要的是能看懂内容，不是一串标题。
        # 因此相关性略高于正文量（0.45 vs 0.35），可信度只作微调。
        #
        # 早期版本给正文 0.25、相关性 0.55，结果「人形机器人」前 10 条里
        # 只有 2 条带正文 —— 一条 45 字的视频标题因为标题精确命中而压过了
        # 1,200 字的报道，而后者才答得了「到底说了什么」。
        if prefer_body:
            if clen < MIN_BODY:
                body *= 0.15      # 只有标题的条目大幅降权
            score = rel * 0.45 + body * 0.35 + trust * 0.20
        else:
            score = rel * 0.60 + body * 0.20 + trust * 0.20
        # 标题命中是「这篇在讲什么」的判据，正文命中只说明「提到了」。
        # 实测「深海采矿」前 5 条里有两条是美股行情，因为正文末尾带了一句
        # 采矿新闻 —— 它们与 CSDN 那篇《深海采矿 4大子系统》同分竞争，
        # 靠短文本的 body 归一化反而更高。这里给标题命中一个正交加分，
        # 让「标题里就有」的排在「正文里提到」的前面。
        #
        # 但加分必须看查询词**在标题里的位置**，不能只看「在不在」。
        # 实测语料里有 7,616 行是「标题 = 整段正文」（UGC 采集把正文写进了
        # title 字段），于是「下周热点板块预判……固态电池……」这种股市复盘
        # 的查询词出现在标题第 76%~87% 的位置，仍然拿到满额标题加分，
        # 排进了「固态电池」的前 8 —— 用户看到的是主板跌幅榜。
        #
        # 判据：查询词出现在标题靠前 60%（按归一化后的字符位置）才算
        # 「标题在讲这件事」。这不需要知道标题有多长、也不需要新增字段，
        # 对正常标题（几十字）几乎无损，对「标题即正文」的行则恢复正确语义。
        title_hit = _title_leads_with(n_title, norm_terms)
        if title_hit:
            score += 0.12
        scored.append([score, r, rel, body, trust])

    scored.sort(key=lambda x: -x[0])

    # ---------- 来源多样性 ----------
    # 纯按分数排会被单一高产来源霸屏：实测 `DeepSeek` 前 20 条里 15 条来自
    # qbitai.com（它关于 DeepSeek 的文章多，且 trust 高）。这对「这个话题
    # 有哪几种说法」是坏事 —— 20 条来自 1 个编辑部，不是 20 个独立视角。
    #
    # 但硬性配额是错的第一个版本：它把 qbitai 的长文全部压到后面，
    # 「人形机器人」前 10 条里有正文的从 5 条掉到 2 条 —— 因为被换上来的
    # 是短视频标题（50-170 字）。多样性不该赢过内容质量。
    #
    # 正确做法是**软惩罚**：同一发布者每多出现一次，分数乘一个衰减系数。
    # 分数差距大的文章仍能凭质量胜出；分数接近时才轮到别的来源。
    DIVERSITY_DECAY = 0.72
    rescored = []
    pub_seen: dict[str, int] = {}
    for item in scored:
        pub = (item[1]["publisher"] or "?")[:40]
        n = pub_seen.get(pub, 0)
        pub_seen[pub] = n + 1
        if n:
            item[0] = item[0] * (DIVERSITY_DECAY ** n)
        rescored.append(item)
    rescored.sort(key=lambda x: -x[0])

    return [d[1] for d in rescored[:limit]], " | ".join(notes)


def hydrate_bodies(db: sqlite3.Connection, rows: list,
                   max_chars: int = 4000) -> list:
    """Attach each row's stored content to it as `body`, in place.

    Why this is separate from `search()`: the ranking deliberately reads
    `SUBSTR(content,1,400)` because it runs over hundreds of candidates and the
    full column is large (this corpus holds a 22,680-character article). Reading
    full bodies for the candidate pool would cost tens of megabytes per query.

    But the *selected* rows are a different matter. `topic.py` used to hand
    `snippet` to the caller as `body`, so a 22,680-character article reached the
    reader as 400 characters - with no ellipsis, no length field, and nothing
    that said it had been cut. The local layer, the cheapest and most reliable
    of the three, was the one that returned the least content.

    Mutates the dicts in place and returns them, so callers do not have to
    thread a new list through. sqlite3.Row is immutable, so any row object is
    converted to a dict first - the alternative was a `TypeError` at the call
    site, which is how this was found.
    """
    rows[:] = [dict(row) if isinstance(row, sqlite3.Row) else row for row in rows]
    if not rows:
        return rows
    identity_field = "id" if all(row.get("id") for row in rows) else "url"
    identities = [row.get(identity_field) for row in rows if row.get(identity_field)]
    if not identities:
        return rows
    found: dict = {}
    ambiguous = set()
    placeholders = ",".join("?" * len(identities))
    query = "SELECT %s, SUBSTR(COALESCE(content,''),1,?) body%%s FROM items WHERE %s IN (%s)" % (identity_field, identity_field, placeholders)
    try:
        try:
            hydrated = db.execute(query % ", metadata_json", (max_chars, *identities)).fetchall()
        except sqlite3.OperationalError:
            hydrated = db.execute(query % "", (max_chars, *identities)).fetchall()
        for got in hydrated:
            identity = got[identity_field]
            if identity in found:
                ambiguous.add(identity)
            metadata = {}
            if "metadata_json" in got.keys():
                try:
                    metadata = json.loads(got["metadata_json"] or "{}")
                except (TypeError, ValueError):
                    pass
            found[identity] = (got["body"] or "", metadata if isinstance(metadata, dict) else {})
    except sqlite3.OperationalError:
        return rows
    for row in rows:
        identity = row.get(identity_field)
        if identity not in found or identity in ambiguous:
            continue
        body, metadata = found[identity]
        row["body"] = body
        for field in ("source_key", "source_kind", "provenance", "metrics", "ids", "published_text", "body_source_url", "sources", "document"):
            if metadata.get(field):
                row[field] = metadata[field]
    return rows


def _replace_in_list(rows: list, old, new) -> None:
    """Swap `old` for `new` without relying on equality (Row has none useful)."""
    for i, item in enumerate(rows):
        if item is old:
            rows[i] = new
            return


def _title_leads_with(n_title: str, norm_terms: list[str],
                      lead_fraction: float = 0.6) -> bool:
    """Does the title *open* on the query, or merely contain it somewhere?

    The distinction matters because 7,616 rows in this corpus have
    `title == content`: the UGC collectors stored the whole post in `title`, so
    a stock-market recap that mentions 固态电池 in its last line still has the
    term "in the title" and collects the full title-hit bonus. Measured on
    「固态电池」, two such rows ranked 6th and 7th with the term at 77% and 87%
    of the way through the title.

    A position check is the cheapest signal that needs no new column and no
    knowledge of how long a title "should" be:

    - A real headline (a few dozen characters, term near the front) passes.
    - A title-is-the-body row only passes when the term genuinely leads it,
      which is the case where it really is the subject.

    `min_head` is the part that makes this safe for short titles. A percentage
    alone truncates the head mid-term: `韩国放宽工厂机器人安全限制` is 13
    characters and 机器人 starts at 6, while 60% of 13 rounds down to 7 - the
    head became `韩国放宽工厂机` and the term was sliced in half. The head is
    therefore the longer of "`lead_fraction` of the title" and "enough to hold
    the term that starts closest to the front". That only ever *adds* room, so
    it cannot make a late mention pass: a term at 77% needs a head wider than
    77% of the title, which the fraction rule already rejects.
    """
    if not n_title or not norm_terms:
        return False

    limit = max(int(len(n_title) * lead_fraction), 1)

    # Enough room to contain a term that *starts* inside the fraction window.
    #
    # A percentage alone truncates the head mid-term: `韩国放宽工厂机器人安全限制`
    # is 13 characters, 机器人 starts at 6, and 60% of 13 rounds down to 7 - the
    # head became `韩国放宽工厂机` and sliced the term in half, so a genuine
    # headline lost its bonus.
    #
    # Widening the head to cover a term that begins inside the window can only
    # *add* room, never make a late mention pass: a term at 77% needs a head
    # wider than 77% of the title, which the fraction rule already rejects
    # before this widening runs.
    for nt in norm_terms:
        if not nt:
            continue
        pos = n_title.find(nt)
        if 0 <= pos < limit:
            limit = max(limit, pos + len(nt))

    head = n_title[:limit]
    return any(nt and nt in head for nt in norm_terms)


def render(topic: str, rows: list, note: str, total: int, show_body: bool) -> str:
    L: list[str] = []
    L.append("=" * 92)
    L.append("输入: %s" % topic)
    L.append("=" * 92)
    if note:
        L.append("  检索说明: %s" % note)
    with_body = sum(1 for r in rows if (r["clen"] or 0) >= MIN_BODY)
    L.append("  命中 %d 条，展示 %d 条（其中 %d 条带正文）"
             % (total, len(rows), with_body))
    L.append("")

    if not rows:
        L.append("  （无结果）")
        return "\n".join(L)

    for i, r in enumerate(rows, 1):
        L.append("-" * 92)
        L.append("[%d] %s" % (i, (r["title"] or "(无标题)")[:80]))
        L.append("    来源: %-26s 类型: %-11s 日期: %s" % (
            (r["publisher"] or "?")[:26],
            r["source_type"],
            (r["published_at"] or "?")[:10]))
        L.append("    正文: %-6d 字   可信度: %.2f" % (r["clen"] or 0, r["trust"] or 0))
        if r["url"]:
            L.append("    链接: %s" % (r["url"] or "")[:88])
        if show_body and (r["clen"] or 0) >= MIN_BODY:
            L.append("    节选: %s..." % " ".join((r["snippet"] or "").split())[:320])
        elif (r["clen"] or 0) < MIN_BODY:
            L.append("    节选: （仅标题，无正文）")
        L.append("")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="按话题检索语料")
    ap.add_argument("--topic", required=True)
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--no-body", action="store_true",
                    help="不偏好长正文（用于只看最新动态）")
    ap.add_argument("--json", default="", help="同时写出 JSON")
    ap.add_argument("--out", default=str(OUT_DEFAULT))
    args = ap.parse_args()

    force_stdout()
    db = connect()

    terms = terms_of(args.topic)
    match, _ = build_match(terms)
    total = 0
    if match:
        try:
            total = db.execute(
                "SELECT COUNT(*) FROM items_fts WHERE items_fts MATCH ?",
                (match,)).fetchone()[0]
        except sqlite3.OperationalError:
            total = 0

    rows, note = search(db, args.topic, args.limit, prefer_body=not args.no_body)
    text = render(args.topic, rows, note, total, True)
    Path(args.out).write_text(text, encoding="utf-8")

    if args.json:
        payload = {
            "topic": args.topic,
            "total_hits": total,
            "note": note,
            "results": [
                {
                    "title": r["title"],
                    "publisher": r["publisher"],
                    "source_type": r["source_type"],
                    "published_at": r["published_at"],
                    "url": r["url"],
                    "author": r["author"],
                    "trust": r["trust"],
                    "body_chars": r["clen"],
                    "snippet": r["snippet"],
                }
                for r in rows
            ],
        }
        Path(args.json).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    db.close()
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Parse source responses into normalized rows without I/O."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
from xml.etree import ElementTree
import html
import json
import re

from ..models import Source


MAX_TITLE_CHARS = 300

def _dig(obj: Any, path: str) -> Any:
    """Read a dot path out of nested dicts/lists. Missing -> None."""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            idx = int(part) if part.isdigit() else 0
            cur = cur[idx] if idx < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _decode_escapes(text: str) -> str:
    """Turn literal `\\u003c` sequences into the characters they stand for.

    Only `\\uXXXX` is touched, and only when the four digits are hex. That rules
    out the two things a blanket `unicode_escape` decode gets wrong:

    - it decodes `\\n` and `\\t` into control characters, which then have to be
      cleaned up as whitespace anyway;
    - it decodes as latin-1, so a title that already contains CJK comes back as
      mojibake unless the bytes are round-tripped through latin-1 - and that
      round-trip itself fails on any character outside it.

    A targeted substitution has neither problem and cannot corrupt text it was
    not asked to change.
    """

    def repl(m: "re.Match[str]") -> str:
        code = int(m.group(1), 16)
        # Surrogate halves cannot be represented on their own; leave them alone
        # rather than produce a string Python cannot encode later.
        if 0xD800 <= code <= 0xDFFF:
            return m.group(0)
        return chr(code)

    return _ESCAPE_RE.sub(repl, text)


def _clean(value: Any) -> str:
    """Make a scraped string printable.

    Three forms of markup arrive here and only one of them was handled before:

    1. Real tags: `<em>人形机器人</em>`. Stripped by the regex below - always
       worked.
    2. Tags that an API escaped *inside* the JSON string: the bytes read
       `\\u003cem\\u003e`, i.e. the literal six characters `\\u003c`. A normal
       JSON reader produces a real `<`, but 头条 search returns the title
       already escaped inside the string, so after `json.loads` the text still
       reads `\\u003cem\\u003e`. The tag regex never matched it and nothing
       stripped it: 60 rows in this repository's own shards carried it to the
       screen verbatim.
    3. HTML entities: `&amp;` `&#39;` `&quot;`. Also never decoded, so titles
       printed `A &amp; B`.

    Order: decode escapes, strip tags, unescape entities. Decoding after
    stripping would leave the `\\u003c` form in place; unescaping entities
    before stripping would turn a literal `&lt;` into a tag and delete text the
    author wrote.
    """
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value)

    if "\\u" in text:
        text = _decode_escapes(text)

    # Real tags.
    text = re.sub(r"<[^>]+>", " ", text)

    # Named and numeric entities, one level only: `&amp;amp;` is the author
    # writing "&amp;", and unescaping twice would silently change it to "&".
    text = html.unescape(text)

    return re.sub(r"\s+", " ", text).strip()


def _rss_items(body: str) -> List[Dict[str, Any]]:
    """Parse Atom or RSS into dicts. Namespace-agnostic on purpose."""
    out: List[Dict[str, Any]] = []
    try:
        root = ElementTree.fromstring(body.encode("utf-8", "replace"))
    except Exception:
        return out
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        row: Dict[str, Any] = {}
        for child in node:
            name = child.tag.split("}")[-1]
            if name == "link":
                row.setdefault("link", child.get("href") or (child.text or ""))
            elif name in ("title", "summary", "content", "description", "published",
                          "updated", "pubDate", "id", "name"):
                row.setdefault(name, (child.text or "").strip())
            elif name == "author":
                sub = [c for c in child if c.tag.split("}")[-1] == "name"]
                row.setdefault("author", (sub[0].text or "").strip() if sub else "")
        out.append(row)
    return out


def _looks_like_href(s: str) -> bool:
    """判断一段匹配文本更像链接还是更像标题。

    不能靠分组顺序决定：不同站点的 HTML 结构里，`<a href=...>text</a>`
    的 href 和 text 谁在前取决于正则怎么写。实测 `wechat_search` 与
    `hupu_search` 的正则把 href 放在 group 1，而 `_regex_items` 固定认为
    group 1 是标题 —— 结果标题被写进了 url 字段，产生了
    `https://weixin.sogou.com/大模型 不是巨头的宠物` 这种 URL，
    后续正文抓取全部 404。
    """
    s = (s or "").strip()
    if not s:
        return False
    if s.startswith(("http://", "https://", "//", "/")):
        return True
    # 纯路径形态：无空格、无中文、含常见分隔符
    if " " in s or "\u4e00" <= s[0] <= "\u9fff":
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9%\-._~:/?#\[\]@!$&'()*+,;=]+", s))


def _regex_items(body: str, pattern: str, base_url: str) -> List[Dict[str, Any]]:
    """Pull items out of HTML using one declared regex per source.

    The pattern may supply a title alone, or a title plus a link. Which capture
    group holds which is decided per match by inspecting the text rather than
    by position, because both orders occur in practice and getting it backwards
    is silent: the row still appears, it just carries a title in its `url`.

    This exists because the generic `_html_items` sweep keeps everything that
    looks like a heading, which on a search results page means nav bars and
    footers dominate the row count - 虎扑 returned 20 real threads but the
    coarser path also offered "登录", "更多" and "首页" as titles.
    """
    out: List[Dict[str, Any]] = []
    seen = set()
    for m in re.finditer(pattern, body, re.S | re.I):
        groups = [g for g in m.groups() if g is not None]
        if not groups:
            continue

        if len(groups) == 1:
            title, href = _clean(groups[0]), ""
        else:
            a, b = _clean(groups[0]), _clean(groups[1])
            a_href, b_href = _looks_like_href(a), _looks_like_href(b)
            # 恰好一个是链接 -> 另一个是标题
            if a_href and not b_href:
                href, title = a, b
            elif b_href and not a_href:
                title, href = a, b
            else:
                # 都不像链接（或都像）-> 按约定：group 1 是标题
                title, href = a, b

        if not title or len(title) < 4 or title in seen:
            continue
        # 标题不该是链接
        if _looks_like_href(title):
            continue
        seen.add(title)
        out.append({
            "title": title,
            "url": urljoin(base_url, href) if href else "",
            "date": "", "author": "", "text": "",
        })
    return out


def _html_items(body: str, base_url: str) -> List[Dict[str, Any]]:
    """Pull candidate items out of a rendered HTML page.

    Why this exists: fifteen sources in the catalog are `fmt="html"` - pricing
    pages, model cards, survey reports, ministry notices - and every one of them
    came back with zero items because the extractor only understood JSON and
    RSS. The pages were fetched, counted as requests, and thrown away.

    There is no schema to read here, so the strategy is deliberately coarse:
    take anchors and headings, keep the ones with enough text to be a title, and
    de-duplicate. That is enough for two things the rest of the pipeline needs -
    a URL to follow and a label to score - and it is honest about being a
    fallback rather than pretending to parse each site's layout.
    """
    out: List[Dict[str, Any]] = []
    seen = set()

    # headings first: they are the titles on marketing and policy pages
    for m in re.finditer(r"<(h[1-4])[^>]*>(.*?)</\1>", body, re.S | re.I):
        text = _clean(m.group(2))
        if 12 <= len(text) <= 200 and text not in seen:
            seen.add(text)
            out.append({"title": text, "url": "", "date": "", "author": "", "text": ""})

    # then anchors with meaningful link text
    for m in re.finditer(r'<a\s[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', body, re.S | re.I):
        href, label = m.group(1), _clean(m.group(2))
        if not (10 <= len(label) <= 200):
            continue
        if label in seen:
            continue
        if href.startswith(("javascript:", "mailto:")):
            continue
        seen.add(label)
        out.append({
            "title": label,
            "url": urljoin(base_url, href),
            "date": "", "author": "", "text": "",
        })
    return out


def extract_rows(source: Source, body: str, ctype: str,
                 base_url: str = "") -> List[Dict[str, Any]]:
    """Turn one response into normalised rows."""
    rows: List[Dict[str, Any]] = []

    if source.key == "youtube_cn":
        from ..platforms.youtube import extract_search
        return extract_search(body)

    if source.fmt == "html":
        if source.html_pattern:
            return _regex_items(body, source.html_pattern, base_url or source.url)
        return _html_items(body, base_url or source.url)

    if source.fmt == "rss" or "xml" in ctype or body.lstrip().startswith("<?xml"):
        for it in _rss_items(body):
            rows.append({
                "title": _clean(it.get("title")),
                "url": _clean(it.get("link") or it.get("id")),
                "date": _clean(it.get("published") or it.get("updated") or it.get("pubDate")),
                "author": _clean(it.get("author")),
                "text": _clean(it.get("summary") or it.get("content")
                               or it.get("description"))[:1200],
            })
        return rows

    try:
        data = json.loads(body)
    except Exception:
        return rows

    container = _dig(data, source.items_path) if source.items_path else data
    if isinstance(container, dict):
        # a bare object, or the first list value inside it
        listy = [v for v in container.values() if isinstance(v, list)]
        container = listy[0] if listy else [container]
    if not isinstance(container, list):
        return rows

    for it in container:
        if not isinstance(it, dict):
            continue
        title = _clean(_dig(it, source.title))
        if not title:
            for alt in source.title_fallback:
                title = _clean(_dig(it, alt))
                if title:
                    break
        url = _clean(_dig(it, source.url_field))
        if not url:
            for alt in source.url_fallback:
                url = _clean(_dig(it, alt))
                if url:
                    break
        if not title and not url:
            continue
        # A title is a label, not a payload. `litellm_prices` is a raw JSON
        # price table with no `items_path`, so `_dig` returned the whole
        # document and `_clean` turned its 2.28 million characters into the
        # row's title. That single row then matched every query in the corpus
        # (it contains every model name and every keyword), which is why one
        # search returned a pricing blob as its top hit.
        #
        # Truncating here rather than filtering downstream keeps the row (it
        # still records that the source was fetched) while making the title
        # usable as a label.
        if len(title) > MAX_TITLE_CHARS:
            title = title[:MAX_TITLE_CHARS].rstrip() + "…"
        # 微博 returns `mblogid` (a short slug like `Rne0o4vuy`), not a URL, so
        # the canonical post link has to be built here. Left as a bare id the
        # row would carry a locator nobody can open, and `publisher_of` would
        # find no host.
        if url and not url.startswith("http") and source.key.startswith("weibo"):
            url = "https://weibo.com/detail/%s" % url
        # sov2ex 返回的是 V2EX 的主题号（纯数字），不是 URL。
        # 不拼成链接的话 `publisher_of` 拿不到 host，整行 publisher 为 None，
        # 独立信源计数就漏掉它；而且正文抓取无法进行。
        if (url and not url.startswith("http")
                and source.key == "v2ex_search" and url.isdigit()):
            url = "https://www.v2ex.com/t/%s" % url
        if (url and not url.startswith("http")
                and source.key in {"juejin_search", "juejin_column"} and url.isdigit()):
            url = "https://juejin.cn/post/%s" % url
        rows.append({
            "title": title,
            "url": url,
            "date": _clean(_dig(it, source.date_field)) if source.date_field else "",
            "author": _clean(_dig(it, source.author_field)) if source.author_field else "",
            "text": _clean(_dig(it, source.text_field))[:1200] if source.text_field else "",
            **_extra_fields(it, source),
        })
    return rows


def _num(value: Any) -> Optional[int]:
    """A count as an int, or None when the source did not provide one.

    `None` and `0` are different facts: "nobody liked it" vs "this endpoint does
    not report likes". Collapsing them to 0 would make a video with no metrics
    look unpopular rather than unmeasured, and the analysis layer has no way to
    recover the distinction later.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    # 微博/小红书-style counts: `1.2万`, `3亿`, or a raw "12345".
    mult = 1
    for suffix, factor in (("亿", 100_000_000), ("万", 10_000), ("w", 10_000),
                           ("k", 1_000), ("K", 1_000)):
        if text.endswith(suffix):
            mult = factor
            text = text[: -len(suffix)]
            break
    try:
        return int(float(text) * mult)
    except ValueError:
        return None


def _extra_fields(item: Dict[str, Any], source: Source) -> Dict[str, Any]:
    """Metrics and follow-up ids for one row.

    Only keys the source declares are emitted, and only when they resolve to a
    value. An absent metric stays absent rather than becoming 0.
    """
    out: Dict[str, Any] = {}
    metrics = {}
    for name in source.metrics:
        path = name.partition("=")[0] if "=" in name else name
        alias = name.partition("=")[2] if "=" in name else name
        value = _num(_dig(item, path))
        if value is not None:
            metrics[alias] = value
    if metrics:
        out["metrics"] = metrics
    ids = {}
    for name in source.id_fields:
        value = _dig(item, name)
        if value not in (None, ""):
            ids[name.partition("=")[0]] = value
    if ids:
        out["ids"] = ids
    return out

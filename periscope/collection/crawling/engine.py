"""Bounded seed crawling with discovery paths, document text and source outcomes."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import heapq
import math
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from periscope.search.identity import canonical_url
from src.url_security import UnsafeURLError, validate_http_url, validate_public_http_url
from .documents import extract_document_async


USER_AGENT = "PeriscopeBot/0.1"
MAX_FORM_URL_CHARS = 2000  # Same maximum used when storing source URLs.
ASSETS = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".css", ".js", ".woff", ".woff2", ".zip", ".exe", ".mp4", ".mp3")


@dataclass(frozen=True)
class CrawlOptions:
    seeds: tuple[str, ...]
    max_pages: int = 12
    depth: int = 2
    budget: float = 30
    timeout: float = 10
    delay: float = 0.5
    max_bytes: int = 5 * 1024 * 1024
    form_fields: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        if self.max_pages < 1 or self.depth < 0 or self.max_bytes < 1:
            raise ValueError("invalid crawl limits")
        if any(not math.isfinite(v) or v < 0 for v in (self.budget, self.delay)) or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("invalid crawl timing")
        for url in self.seeds:
            validate_http_url(url)
        if self.form_fields:
            if len(self.seeds) != 1 or self.depth < 1 or self.max_pages < 2:
                raise ValueError("form query requires one seed, depth >= 1 and at least two pages")
            if len(self.form_fields) > 20:
                raise ValueError("too many form fields")
            names = set()
            for pair in self.form_fields:
                if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                    raise ValueError("invalid form field")
                name, value = pair
                if (not isinstance(name, str) or not isinstance(value, str) or not name.strip()
                        or len(name) > 100 or len(value) > 500
                        or any(ord(char) < 32 for char in name + value) or name in names):
                    raise ValueError("invalid or duplicate form field")
                names.add(name)


class CrawlError(ValueError):
    def __init__(self, reason: str, status: str = "failed"):
        super().__init__(reason)
        self.status = status


class DeliveryError(RuntimeError):
    """A consumer could not durably accept a collected page."""


def _matches(text: str, topic: str) -> bool:
    normalize = lambda value: "".join(value.casefold().split())
    haystack = normalize(text)
    return all(normalize(term) in haystack for term in topic.split())


def select_seeds(rows: list[dict], limit: int = 6) -> tuple[str, ...]:
    """Prefer one result per host before spending the budget on another."""
    groups = {}
    seen = set()
    for row in rows:
        url = canonical_url(row.get("url") or "")
        if not url or url in seen:
            continue
        try:
            validate_http_url(url)
        except UnsafeURLError:
            continue
        seen.add(url)
        groups.setdefault(urlsplit(url).hostname, []).append(url)
    chosen = []
    while groups and len(chosen) < limit:
        for host in list(groups):
            chosen.append(groups[host].pop(0))
            if not groups[host]:
                del groups[host]
            if len(chosen) >= limit:
                break
    return tuple(chosen)


async def crawl_async(topic: str, options: CrawlOptions, *, client=None, on_row=None) -> tuple[list[dict], dict]:
    if not topic.strip():
        raise ValueError("topic must not be empty")
    started = time.monotonic()
    observed = datetime.now(timezone.utc).isoformat()
    rows, reports, queue = [], {}, []
    hosts = {(urlsplit(url).hostname or "").lower().removeprefix("www.") for url in options.seeds}
    robots, last_request, sitemap_origins = {}, {}, set()
    stat = {"pages": 0, "requests": 0, "skipped_external": 0}

    def in_scope(url):
        return (urlsplit(url).hostname or "").lower().removeprefix("www.") in hosts

    def enqueue(url, parent="", depth=0, label="", seed="", query_fields=None,
                query_warnings=None, form_query=False):
        identity = canonical_url(url)
        if not identity or identity in reports or len(reports) >= options.max_pages * 20:
            return False
        if not in_scope(identity):
            stat["skipped_external"] += 1
            return False
        if depth > options.depth or urlsplit(identity).path.lower().endswith(ASSETS):
            return False
        priority = (100 if depth == 0 else 90 if form_query else
                    (20 if label and _matches(label, topic) else 0) +
                    (25 if urlsplit(identity).path.lower().endswith((".pdf", ".xlsx", ".docx")) else 0))
        reports[identity] = {"url": identity, "parent_url": parent, "depth": depth,
                             "seed_url": seed or identity, "label": label, "status": "queued",
                             "error": "", "chars": 0, "bytes": 0}
        if query_fields:
            reports[identity]["query_fields"] = dict(query_fields)
        if query_warnings:
            reports[identity]["query_warnings"] = list(query_warnings)
        heapq.heappush(queue, (-priority, len(reports), identity))
        return True

    for seed in options.seeds:
        enqueue(seed)

    async def run(http):
        async def fetch(url, limit, interval=options.delay, obey_robots=False):
            current = url
            for _ in range(6):
                if not in_scope(current):
                    raise CrawlError("redirect_outside_seed_hosts", "blocked")
                await validate_public_http_url(current)
                if obey_robots:
                    policy = await policy_for(current)
                    if not policy.can_fetch(USER_AGENT, current):
                        raise CrawlError("robots_disallowed", "blocked")
                    interval = max(interval, policy.crawl_delay(USER_AGENT) or 0)
                host = urlsplit(current).netloc.lower()
                if host in last_request:
                    await asyncio.sleep(max(0, interval - (time.monotonic() - last_request[host])))
                last_request[host] = time.monotonic()
                stat["requests"] += 1
                async with http.stream("GET", current, timeout=options.timeout, follow_redirects=False,
                                       headers={"User-Agent": USER_AGENT}) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        if not location:
                            raise CrawlError("redirect_without_location")
                        current = urljoin(current, location)
                        continue
                    length = response.headers.get("content-length", "")
                    if length.isdigit() and int(length) > limit:
                        raise CrawlError("response_size_limit", "too_large")
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > limit:
                            raise CrawlError("response_size_limit", "too_large")
                        chunks.append(chunk)
                    return response.status_code, b"".join(chunks), response.headers.get("content-type", ""), current
            raise CrawlError("too_many_redirects")

        async def policy_for(url):
            parsed = urlsplit(url)
            origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
            if origin not in robots:
                status, content, _, final = await fetch(origin + "/robots.txt", 256 * 1024)
                policy = RobotFileParser(origin + "/robots.txt")
                if status in (401, 403):
                    policy.disallow_all = True
                elif status in (404, 410):
                    policy.parse([])
                elif status == 200:
                    policy.parse(content.decode("utf-8", errors="replace").splitlines())
                else:
                    raise CrawlError("robots_HTTP_" + str(status))
                robots[origin] = policy
            return robots[origin]

        while queue and stat["pages"] < options.max_pages:
            _, _, url = heapq.heappop(queue)
            report = reports[url]
            stat["pages"] += 1
            report["status"] = "fetching"
            try:
                policy = await policy_for(url)
                if not policy.can_fetch(USER_AGENT, url):
                    raise CrawlError("robots_disallowed", "blocked")
                origin = (urlsplit(url).scheme, urlsplit(url).netloc)
                if origin not in sitemap_origins:
                    sitemap_origins.add(origin)
                    for sitemap in policy.site_maps() or []:
                        enqueue(sitemap, url, report["depth"] + 1, "sitemap", report["seed_url"])
                delay = max(options.delay, policy.crawl_delay(USER_AGENT) or 0)
                status, content, content_type, final = await fetch(url, options.max_bytes, delay, obey_robots=True)
                report.update(http_status=status, bytes=len(content), final_url=final)
                if status != 200:
                    raise CrawlError("HTTP_" + str(status))
                if report.get("query_fields") and len(final) > MAX_FORM_URL_CHARS:
                    report.update(status="partial", error="form_result_url_too_long")
                    continue
                doc = await extract_document_async(content, content_type, final)
                report.update(format=doc.format, chars=len(doc.text))
                if doc.text and _matches(doc.title + "\n" + doc.text, topic):
                    metadata = {**doc.metadata(), "sha256": hashlib.sha256(content).hexdigest()}
                    provenance = {"source_key": "web_crawl", "method": "crawl", "identity": "url",
                                  "observed_at": observed, "url": final, "parent_url": report["parent_url"],
                                  "seed_url": report["seed_url"], "depth": report["depth"], "content_role": "source_text"}
                    if report.get("query_fields"):
                        provenance["query_fields"] = report["query_fields"]
                    if report.get("query_warnings"):
                        provenance["query_warnings"] = report["query_warnings"]
                    row = {"title": doc.title or report["label"] or final, "url": final,
                                 "author": urlsplit(final).hostname or "", "date": "",
                                 "body": doc.text if len(doc.text) >= 400 else "", "text": doc.text,
                                 "source": "web_crawl", "kind": "media", "trust": 0.5, "_origin": "crawl",
                                 "body_source_url": final, "document": metadata,
                                 "provenance": provenance}
                    if on_row is not None:
                        try:
                            await on_row(row)
                        except Exception as exc:
                            raise DeliveryError("evidence delivery failed") from exc
                    rows.append(row)
                    report["status"] = "partial" if doc.format in {"xlsx", "docx"} and doc.truncated else "ok"
                else:
                    report["status"] = ("partial" if doc.format in {"xlsx", "docx"} and doc.truncated else
                                        "needs_ocr" if doc.needs_ocr else "discovery" if doc.links else
                                        "filtered" if doc.text else "empty")
                if doc.format in {"xlsx", "docx"} and doc.truncated:
                    report["error"] = ",".join(doc.incomplete_reasons) or "document_limit"
                if options.form_fields and report["depth"] == 0:
                    required = {name for name, _value in options.form_fields}
                    matched = [form for form in doc.forms if required <= set(form.field_names)]
                    candidates = [form for form in matched if form.method == "get"]
                    reason = ""
                    if not matched:
                        reason = "form_fields_unavailable"
                    elif not candidates:
                        reason = "form_method_unsupported"
                    elif len(candidates) != 1:
                        reason = "form_ambiguous"
                    else:
                        form = candidates[0]
                        supplied = dict(options.form_fields)
                        if form.has_sensitive_controls:
                            reason = "form_auth_or_file_unsupported"
                        elif any(not supplied.get(name, "").strip() for name in form.required_fields):
                            reason = "form_required_fields_missing"
                        elif any(name in form.select_options and value not in form.select_options[name]
                               for name, value in options.form_fields):
                            reason = "form_invalid_option"
                        else:
                            action = urlsplit(form.action)
                            target = urlunsplit((action.scheme, action.netloc, action.path,
                                                 urlencode([(name, value) for name, value in options.form_fields]), ""))
                            normalized = canonical_url(target)
                            if normalized and len(normalized) > MAX_FORM_URL_CHARS:
                                reason = "form_query_url_too_long"
                            elif (not normalized or not in_scope(normalized) or
                                    sorted(parse_qsl(urlsplit(normalized).query, keep_blank_values=True)) !=
                                    sorted((name, value) for name, value in options.form_fields)):
                                reason = "form_action_blocked"
                            else:
                                try:
                                    validate_http_url(normalized)
                                except UnsafeURLError:
                                    reason = "form_action_blocked"
                                else:
                                    warnings = []
                                    if form.has_hidden_fields:
                                        warnings.append("form_hidden_fields_omitted")
                                    if any(name not in supplied for name in form.field_names):
                                        warnings.append("form_fields_omitted")
                                    if form.has_named_submit:
                                        warnings.append("form_submit_control_omitted")
                                    if enqueue(normalized, final, report["depth"] + 1,
                                               "form query", report["seed_url"], dict(options.form_fields),
                                               query_warnings=warnings,
                                               form_query=True):
                                        report["form_status"] = "queued"
                                        if warnings:
                                            report.update(status="partial", error=",".join(warnings))
                                    else:
                                        reason = "form_result_not_queued"
                    if reason:
                        report.update(status="partial", error=reason, form_status="skipped")
                for link in doc.links:
                    enqueue(link.url, final, report["depth"] + 1, link.label, report["seed_url"],
                            report.get("query_fields"), report.get("query_warnings"))
            except DeliveryError:
                raise
            except asyncio.CancelledError:
                report.update(status="over_budget", error="overall_deadline")
                raise
            except UnsafeURLError as exc:
                report.update(status="blocked", error=str(exc))
            except CrawlError as exc:
                report.update(status=exc.status, error=str(exc))
            except Exception as exc:
                reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
                report.update(status="unsupported" if reason.startswith("unsupported_") else "failed", error=reason[:150])

    async def bounded(http):
        try:
            if options.budget:
                await asyncio.wait_for(run(http), max(0.001, options.budget - (time.monotonic() - started)))
            else:
                await run(http)
        except asyncio.TimeoutError:
            pass

    if client is None:
        async with httpx.AsyncClient(timeout=options.timeout, follow_redirects=False) as http:
            await bounded(http)
    else:
        await bounded(client)
    for report in reports.values():
        if report["status"] == "queued":
            report["status"] = "page_limit" if stat["pages"] >= options.max_pages else "over_budget"
    stat.update(rows=len(rows), reports=list(reports.values()), elapsed=time.monotonic() - started)
    return rows, stat


def crawl_topic(topic: str, seeds: tuple[str, ...], *, max_pages=12, depth=2, budget=30, form_fields=()):
    return asyncio.run(crawl_async(topic, CrawlOptions(tuple(seeds), max_pages=max_pages, depth=depth,
                                                      budget=budget, form_fields=tuple(form_fields))))

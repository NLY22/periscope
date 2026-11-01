"""Bounded, cancellable browser sources for the investigation workflow."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import aclosing
from urllib.parse import urlsplit

from periscope.runtime import paths
from .crawl import CollectionIncomplete
from .reuse import _terms


_MODULES = {"xhs_browser": "periscope.collection.platforms.xhs",
            "douyin_browser": "periscope.collection.platforms.douyin",
            "kuaishou_browser": "periscope.collection.platforms.kuaishou"}


def _process_options() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}


def _browser_env() -> dict:
    temporary = paths.SCRATCH / "browser-tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    return {**paths.subprocess_env(), "TEMP": str(temporary), "TMP": str(temporary), "TMPDIR": str(temporary)}


async def _terminate_process_tree(process) -> None:
    """Stop only the process tree launched by this collector."""
    if process.returncode is not None:
        return
    if os.name == "nt":
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            stopper = await asyncio.create_subprocess_exec(
                str(executable), "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                await asyncio.wait_for(stopper.wait(), 5)
            except TimeoutError:
                stopper.kill()
                await stopper.wait()
        except OSError:
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await process.wait()


async def _stream_search(topic: str, source: str, budget: float):
    deadline = time.monotonic() + budget
    gate = _browser_gate()
    try:
        await asyncio.wait_for(gate.acquire(), max(0.001, budget))
    except TimeoutError as exc:
        raise CollectionIncomplete(source + ":search_time_budget") from exc
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CollectionIncomplete(source + ":search_time_budget")
        async with aclosing(_run_search(topic, source, remaining)) as stream:
            async for row in stream:
                yield row
    finally:
        gate.release()


def _browser_gate() -> asyncio.Semaphore:
    """Share one browser slot across investigations on this backend loop."""
    loop = asyncio.get_running_loop()
    gate = getattr(loop, "_periscope_browser_gate", None)
    if gate is None:
        gate = asyncio.Semaphore(1)
        setattr(loop, "_periscope_browser_gate", gate)
    return gate


async def _run_search(topic: str, source: str, budget: float):
    """Read worker checkpoints as they arrive; cancellation reaps its browser."""
    if source not in _MODULES:
        raise ValueError("unsupported browser source")
    deadline = time.monotonic() + budget
    paths.SHARD_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="investigation-browser-", dir=paths.SHARD_DIR) as directory:
        output = Path(directory) / "results.json"
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", _MODULES[source], "--queries", topic, "--scrolls", "2", "--out", str(output),
            cwd=str(paths.ROOT), env=_browser_env(), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, **_process_options())
        seen, timed_out = set(), False
        try:
            while True:
                try:
                    data = json.loads(output.read_text(encoding="utf-8-sig")) if output.exists() else []
                except (OSError, ValueError):
                    data = []
                if isinstance(data, list):
                    for row in data:
                        if not isinstance(row, dict):
                            continue
                        key = str(row.get("url") or "")
                        if key and key not in seen:
                            seen.add(key)
                            yield row
                if process.returncode is not None:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    await _terminate_process_tree(process)
                    continue
                await asyncio.sleep(min(0.4, remaining))
        finally:
            await _terminate_process_tree(process)
        if timed_out:
            raise CollectionIncomplete(source + ":search_time_budget")
        if process.returncode:
            reason = {3: "profile_busy", 4: "captcha_required"}.get(process.returncode, "browser_unavailable")
            raise CollectionIncomplete(source + ":" + reason)


def _identity(source: str, url: str):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return None
    if source == "xhs_browser":
        from periscope.collection.platforms.xhs import _note_id
        identifier = _note_id(url)
        return {"source": "xhs", "type": "note", "native_id": identifier} if identifier else None
    if source == "douyin_browser":
        from periscope.collection.platforms.douyin import _aweme_id
        identifier = _aweme_id(url)
        return {"source": "douyin", "type": "video", "native_id": identifier} if identifier else None
    if source == "kuaishou_browser" and parsed.hostname in {"kuaishou.com", "www.kuaishou.com"}:
        match = re.fullmatch(r"/short-video/([A-Za-z0-9_-]+)/?", parsed.path)
        if match:
            return {"source": "kuaishou", "type": "video", "native_id": match.group(1)}
    return None


def identify_browser_url(url: str):
    for source in _MODULES:
        identity = _identity(source, url)
        if identity:
            return source, identity
    return None


async def _fetch_detail(source: str, card: dict, budget: float, max_comments: int):
    deadline = time.monotonic() + budget
    gate = _browser_gate()
    try:
        await asyncio.wait_for(gate.acquire(), max(0.001, budget))
    except TimeoutError as exc:
        raise CollectionIncomplete(source + ":discussion_time_budget") from exc
    try:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CollectionIncomplete(source + ":discussion_time_budget")
        return await _run_detail(source, card, remaining, max_comments)
    finally:
        gate.release()


async def _run_detail(source: str, card: dict, budget: float, max_comments: int):
    if source not in {"xhs_browser", "douyin_browser"}:
        raise CollectionIncomplete(source + ":discussion_unsupported")
    deadline = time.monotonic() + budget
    work_deadline = deadline - min(3.0, budget * 0.25)
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "periscope.investigations.browser_detail_worker",
        cwd=str(paths.ROOT), env=_browser_env(), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, **_process_options())
    payload = json.dumps({"source": source, "url": card["url"], "budget": budget, "deadline": work_deadline,
                          "max_comments": max_comments}, ensure_ascii=True).encode("ascii")
    try:
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(payload), max(0.001, deadline - time.monotonic()))
        except TimeoutError as exc:
            raise CollectionIncomplete(source + ":discussion_time_budget") from exc
    finally:
        await _terminate_process_tree(process)
    if process.returncode:
        raise CollectionIncomplete(source + ":browser_unavailable")
    try:
        result = json.loads(stdout.decode("utf-8"))
        detail, comments, coverage = result["detail"], result["comments"], result["coverage"]
        if not isinstance(detail, dict) or not isinstance(comments, list) or not isinstance(coverage, dict):
            raise ValueError("invalid response")
        return detail, comments, coverage
    except (ValueError, KeyError, TypeError, UnicodeError) as exc:
        raise CollectionIncomplete(source + ":browser_invalid_output") from exc


async def stream_browser_detail(card: dict, *, budget: float, max_comments: int = 20):
    source = card["source"]
    detail, comments, coverage = await _fetch_detail(source, card, budget, max_comments)
    yield [{**card, "text": detail.get("text") or card.get("text") or "",
            "author": detail.get("author") or card.get("author") or "",
            "evidence": {**card["evidence"], "content_level": "full" if detail.get("text") else card["evidence"].get("content_level", "search_result")},
            "comments": comments, "discussion_coverage": coverage,
            "provenance": {**card.get("provenance", {}), "method": "browser_detail"}}]
    if coverage.get("status") != "complete":
        reasons = [str(value) for value in (coverage.get("errors") or []) + (coverage.get("truncated_by") or [])]
        known = ("login_required", "time_budget", "comment_limit", "page_limit", "profile_busy")
        safe = [value for value in known if any(value in reason for reason in reasons)]
        if any("BrowserProfileBusy" in reason for reason in reasons) and "profile_busy" not in safe:
            safe.append("profile_busy")
        raise CollectionIncomplete(source + ":" + ",".join(safe or ["discussion_incomplete"]))


async def stream_browser_source(topic: str, source: str, *, budget: float, depth: int = 2):
    """Discover platform leads and deepen one supported discussion within budget."""
    if source not in _MODULES:
        raise ValueError("unsupported browser source")
    deadline = time.monotonic() + budget
    cards, errors = [], []
    try:
        async with aclosing(_stream_search(topic, source, min(15.0, max(0.0, budget * 0.5)))) as stream:
            async for row in stream:
                identity = _identity(source, str(row.get("url") or ""))
                if not identity:
                    continue
                card = {**row, "source": source, "kind": "community", "_origin": "platform",
                        "evidence": {**identity, "content_level": "search_result"},
                        "discussion_coverage": {"status": "unsupported" if source == "kuaishou_browser" else "not_attempted"},
                        "provenance": {"method": "browser_search", "content_role": "community"}}
                cards.append(card)
                yield [card]
    except CollectionIncomplete as exc:
        errors.append(str(exc))
    if cards and depth and source != "kuaishou_browser":
        terms = _terms(topic)
        cards.sort(key=lambda card: -len(terms & _terms(str(card.get("title") or "") + " " + str(card.get("text") or ""))))
        remaining = deadline - time.monotonic()
        if remaining > 0:
            try:
                async for batch in stream_browser_detail(cards[0], budget=remaining, max_comments=13):
                    yield batch
            except CollectionIncomplete as exc:
                errors.append(str(exc))
        else:
            errors.append("time_budget")
    if cards and (source == "kuaishou_browser" or len(cards) > (1 if depth else 0)):
        errors.append("discussion_unsupported" if source == "kuaishou_browser" else "candidate_limit")
    if errors:
        raise CollectionIncomplete(source + ":" + ",".join(errors))

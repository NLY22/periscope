"""Read actual video identities from YouTube search-page data, without a browser."""
from __future__ import annotations

import json
import re


def _text(node: dict) -> str:
    return node.get("simpleText") or "".join(run.get("text", "") for run in node.get("runs", []))


def extract_search(html: str) -> list[dict]:
    decoder = json.JSONDecoder()
    payload = None
    for match in re.finditer(r'(?:ytInitialData|window\["ytInitialData"\])\s*=\s*', html):
        try:
            payload, _ = decoder.raw_decode(html[match.end():])
            break
        except ValueError:
            continue
    if payload is None:
        return []
    stack, rows, seen = [payload], [], set()
    while stack:
        node = stack.pop()
        if isinstance(node, list):
            stack.extend(reversed(node))
        elif isinstance(node, dict):
            video = node.get("videoRenderer")
            if isinstance(video, dict):
                identity = video.get("videoId")
                title = _text(video.get("title") or {})
                if identity and title and identity not in seen:
                    seen.add(identity)
                    rows.append({"title": title, "url": "https://www.youtube.com/watch?v=" + identity,
                                 "author": _text(video.get("ownerText") or video.get("longBylineText") or {}),
                                 "date": "", "published_text": _text(video.get("publishedTimeText") or {}),
                                 "text": _text(video.get("descriptionSnippet") or {}),
                                 "ids": {"video_id": identity}})
            stack.extend(reversed(list(node.values())))
    return rows

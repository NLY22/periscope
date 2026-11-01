"""Conservative lexical matching for Chinese and mixed-language queries."""
from __future__ import annotations

import re


def matches_query(text: str, query: str) -> bool:
    haystack = "".join(str(text or "").casefold().split())
    tokens = re.findall(r"[a-z0-9_+.-]+|[\u4e00-\u9fff]+", str(query or "").casefold())
    if not tokens:
        return False
    for token in tokens:
        if token in haystack:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{3,}", token):
            grams = {token[index:index + 2] for index in range(len(token) - 1)}
            if sum(gram in haystack for gram in grams) * 2 >= len(grams):
                continue
        return False
    return True

"""Query expansion for evidence retrieval.

Why it exists: the corpus is worded differently from the question. A sub-
question in Chinese rarely repeats the exact English product name, and a
paraphrase ("估值", "融资额") matches none of the trigrams in the source text.
Expansion asks the model for the terms the corpus is likely to use, then the
*existing* lexical machinery retrieves them — so recall improves without the
pipeline ever depending on an embedding endpoint.

Every call goes through the shared caching client, so a repeated sub-question
costs nothing, and the whole step is skipped when no key is configured.
"""

from __future__ import annotations

import logging
import re
from typing import Any, List, Optional

from ..ai.utils import parse_json_response

logger = logging.getLogger(__name__)

EXPANSION_SYSTEM = """\
You expand a research question into search terms for a news/forum corpus.

Rules:
- Return the literal words a source would USE, not synonyms you prefer:
  product names, organisation names, acronyms, numbers, transliterations.
- Cover both Chinese and English surface forms when the topic has them.
- 2-6 terms, each 1-4 words, no explanation.
- Return ONLY a JSON object: {"terms": ["...", "..."]}
  If the question has no searchable entities, return {"terms": []}."""


async def expand_query(
    client: Optional[Any],
    query: str,
    max_terms: int = 4,
) -> List[str]:
    """Extra search terms for `query`; [] without a client or on any failure."""
    text = (query or "").strip()
    if client is None or not text:
        return []
    try:
        response = await client.complete(system=EXPANSION_SYSTEM, user=f"问题: {text}")
    except Exception as exc:  # expansion is an enhancement, never a break
        logger.warning("query expansion failed (%s); using original terms", exc)
        return []
    parsed = parse_json_response(response)
    if not isinstance(parsed, dict):
        return []
    raw = parsed.get("terms")
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for term in raw:
        cleaned = re.sub(r"\s+", " ", str(term)).strip()
        if len(cleaned) < 2 or len(cleaned) > 60:
            continue
        if cleaned.lower() in [o.lower() for o in out]:
            continue
        out.append(cleaned)
        if len(out) >= max_terms:
            break
    return out

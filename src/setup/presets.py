"""Preset source library loader and keyword matching.

The preset library is a local file (`data/presets.json`). The wizard reads it
offline, so a first run never contacts a remote service.
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .tag_aliases import get_tag_aliases


def load_presets(presets_path: str = "data/presets.json") -> Dict:
    """Load the local preset library.

    Raises:
        FileNotFoundError: when the file is missing. `data/presets.json` ships
            in the repository, so this means the working directory or the
            storage data dir was pointed somewhere without it.
    """
    path = Path(presets_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Presets file not found: {path}\n"
            f"`data/presets.json` ships in the repository; run from the repo "
            f"root or restore that file."
        )

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def match_domains(
    user_input: str,
    presets: Dict,
    threshold: float = 0.1,
) -> List[Tuple[Dict, float]]:
    """Match user interest description against preset domains.

    Performs case-insensitive keyword matching against domain keywords and
    source tags. Returns matched domains sorted by relevance score.

    Args:
        user_input: Free-form user interest description (supports mixed languages).
        presets: Loaded presets dictionary.
        threshold: Minimum score (0–1) to include a domain.

    Returns:
        List of (domain_dict, score) tuples sorted by descending score.
    """
    tokens = set(user_input.lower().split())
    input_lower = user_input.lower()

    results = []
    for domain in presets.get("domains", []):
        score = 0.0
        domain_keywords = [k.lower() for k in domain.get("keywords", [])]
        total_keywords = len(domain_keywords) or 1

        for kw in domain_keywords:
            if kw in tokens or kw in input_lower:
                score += 1.0

        for source in domain.get("sources", []):
            for tag in source.get("tags", []):
                if tag.lower() in tokens or tag.lower() in input_lower:
                    score += 0.3

        normalized = min(score / total_keywords, 1.0)
        if normalized >= threshold:
            results.append((domain, normalized))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def collect_sources_from_domains(
    matched_domains: List[Tuple[Dict, float]],
) -> List[Dict]:
    """Flatten matched domains into a deduplicated list of source configs.

    Args:
        matched_domains: Output from match_domains().

    Returns:
        List of source dicts (each with type, description, config, origin="preset").
    """
    seen = set()
    sources = []

    for domain, _score in matched_domains:
        for src in domain.get("sources", []):
            key = _source_unique_key(src)
            if key not in seen:
                seen.add(key)
                sources.append({**src, "origin": "preset"})

    return sources


def _tag_matches_input(tag: str, tokens: set, input_lower: str) -> bool:
    """Check if a tag (or any of its aliases) matches the user input."""
    tag_lower = tag.lower()
    if tag_lower in tokens or tag_lower in input_lower:
        return True
    for alias in get_tag_aliases(tag):
        alias_lower = alias.lower()
        if alias_lower in tokens or alias_lower in input_lower:
            return True
    return False


def match_sources(
    user_input: str,
    presets: Dict,
    threshold: float = 0.1,
) -> List[Tuple[Dict, float]]:
    """Match user interest description against individual sources.

    Scores each source based on:
    - Category keyword matches (+1.0 per keyword)
    - Source tag matches (+0.5 per tag)
    - Description word matches (+0.3 per word, min length 3)

    Args:
        user_input: Free-form user interest description (supports mixed languages).
        presets: Loaded presets dictionary.
        threshold: Minimum normalized score to include a source.

    Returns:
        List of (source_dict, score) tuples sorted by descending score.
        Each source_dict has origin="preset" added.
    """
    tokens = set(user_input.lower().split())
    input_lower = user_input.lower()
    total_tokens = len(tokens) or 1

    seen = set()
    results = []

    for domain in presets.get("domains", []):
        domain_keywords = [k.lower() for k in domain.get("keywords", [])]

        category_score = sum(
            1.0 for kw in domain_keywords
            if kw in tokens or kw in input_lower
        )

        for src in domain.get("sources", []):
            key = _source_unique_key(src)
            if key in seen:
                continue
            seen.add(key)

            tag_score = sum(
                0.5 for tag in src.get("tags", [])
                if _tag_matches_input(tag, tokens, input_lower)
            )

            description = src.get("description", "").lower()
            desc_tokens = set(description.split())
            desc_score = sum(
                0.3 for token in tokens
                if len(token) >= 3 and token in desc_tokens
            )

            raw_score = category_score + tag_score + desc_score
            normalized = min(raw_score / total_tokens, 1.0)

            if normalized >= threshold:
                results.append(({**src, "origin": "preset"}, normalized))

    results.sort(key=lambda x: x[1], reverse=True)
    return results


def _source_unique_key(source: Dict) -> str:
    """Generate a unique key for a source to enable deduplication."""
    src_type = source.get("type", "")
    cfg = source.get("config", {})

    if src_type == "rss":
        return f"rss:{cfg.get('url', '')}"
    elif src_type == "reddit_subreddit":
        return f"reddit:{cfg.get('subreddit', '')}"
    elif src_type == "reddit_user":
        return f"reddit_user:{cfg.get('username', '')}"
    elif src_type == "github_user":
        return f"github_user:{cfg.get('username', '')}"
    elif src_type == "github_repo":
        return f"github_repo:{cfg.get('owner', '')}/{cfg.get('repo', '')}"
    elif src_type == "telegram":
        return f"telegram:{cfg.get('channel', '')}"
    else:
        return f"{src_type}:{json.dumps(cfg, sort_keys=True)}"
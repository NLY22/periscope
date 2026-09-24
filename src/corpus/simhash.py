"""SimHash near-duplicate fingerprints.

A hand-rolled implementation (no external libs): the whole point of the
corpus layer is to know when ten "different" articles are actually one
syndicated press release, and to do that we need fingerprints we fully
control.

Approach
--------
1. Tokenise text into features:
   - latin: lowercased word unigrams
   - CJK:   character bigrams (word segmentation is not required for a
            similarity fingerprint; bigrams are robust and language-
            agnostic across zh/ja)
2. Feature weights = term frequency.
3. Each feature is hashed to a 64-bit integer (blake2b, stable across
   processes — unlike Python's randomised builtin hash()).
4. Classic SimHash voting yields one 64-bit fingerprint per document.

Near-duplicate detection then uses Hamming distance between fingerprints:
documents within a small radius are "the same story", which powers the
source-independence count downstream.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Iterable

_LATIN_WORD = re.compile(r"[a-z0-9][a-z0-9_.+#-]*")
_CJK_CHAR = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

_MASK64 = (1 << 64) - 1


def tokenize(text: str) -> Counter:
    """Split text into weighted features (latin words + CJK bigrams)."""
    lowered = text.lower()
    features: Counter = Counter()
    for word in _LATIN_WORD.findall(lowered):
        features[word] += 1
    # CJK bigrams: slide over maximal runs of CJK characters.
    run: list[str] = []
    for ch in lowered:
        if _CJK_CHAR.match(ch):
            run.append(ch)
        else:
            if len(run) == 1:
                features[f"@{run[0]}"] += 1  # single-char run fallback
            else:
                for a, b in zip(run, run[1:]):
                    features[a + b] += 1
            run = []
    if len(run) == 1:
        features[f"@{run[0]}"] += 1
    else:
        for a, b in zip(run, run[1:]):
            features[a + b] += 1
    return features


def feature_hash64(feature: str) -> int:
    """Stable 64-bit hash for a feature token."""
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def simhash(features: Counter) -> int:
    """Compute the 64-bit SimHash fingerprint of weighted features."""
    if not features:
        return 0
    votes = [0] * 64
    for feature, weight in features.items():
        h = feature_hash64(feature)
        for bit in range(64):
            if h >> bit & 1:
                votes[bit] += weight
            else:
                votes[bit] -= weight
    fingerprint = 0
    for bit in range(64):
        if votes[bit] > 0:
            fingerprint |= 1 << bit
    return fingerprint


def fingerprint(text: str) -> int:
    return simhash(tokenize(text))


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & _MASK64).count("1")


def cluster_pairs(
    ids: Iterable[tuple[str, int]], max_distance: int = 3
) -> list[tuple[str, str]]:
    """Return pairs of IDs whose fingerprints are within max_distance.

    O(n^2) by design: corpora slices here are per-run (tens to hundreds
    of items), and correctness beats cleverness at this scale. If the
    table grows past ~10k rows per window, swap in a multi-table LSH
    band split.
    """
    entries = list(ids)
    pairs: list[tuple[str, str]] = []
    for i in range(len(entries)):
        id_a, fp_a = entries[i]
        for j in range(i + 1, len(entries)):
            id_b, fp_b = entries[j]
            if hamming(fp_a, fp_b) <= max_distance:
                pairs.append((id_a, id_b))
    return pairs

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


def _bands(fingerprint: int, band_count: int, band_bits: int) -> list[tuple[int, int]]:
    """Split a fingerprint into `band_count` slices of `band_bits` bits each.

    Returns (band_index, band_value) pairs so two fingerprints can be compared
    per band without recomputing the split.
    """
    out = []
    for b in range(band_count):
        shift = b * band_bits
        out.append((b, (fingerprint >> shift) & ((1 << band_bits) - 1)))
    return out


def cluster_pairs(
    ids: Iterable[tuple[str, int]], max_distance: int = 3
) -> list[tuple[str, str]]:
    """Return pairs of IDs whose fingerprints are within max_distance.

    Two implementations behind one contract, because the right one depends on
    the size of the slice:

    - Small slices (a daily run, tens to hundreds of rows) compare every pair.
      It is exact and the cost is irrelevant at that size.
    - Large slices use multi-table LSH banding. The original docstring named this
      as the fix for "past ~10k rows"; at 89,734 rows the exact path is 4.0
      billion comparisons and does not finish - a backfill ran 19 minutes of CPU
      without writing a row.

    The banded path is *approximately* exact, and the approximation is safe in
    the direction that matters: with `band_count * band_bits >= 64` and
    `band_bits > max_distance`, any pair within the radius is guaranteed to
    share a band (pigeonhole: `max_distance` differing bits cannot touch every
    band), so no true duplicate is missed. It can only admit a few extra
    candidates, which the exact Hamming check then rejects.
    """
    entries = list(ids)
    n = len(entries)
    if n < 2:
        return []

    # Below this size the quadratic scan is both exact and fast; above it, the
    # banding pays for itself. 2,000 rows is 2.0M comparisons, well under a
    # second in Python.
    if n <= 2000:
        pairs: list[tuple[str, str]] = []
        for i in range(n):
            id_a, fp_a = entries[i]
            for j in range(i + 1, n):
                id_b, fp_b = entries[j]
                if hamming(fp_a, fp_b) <= max_distance:
                    pairs.append((id_a, id_b))
        return pairs

    # 4 bands of 16 bits. band_bits (16) > max_distance (3), so a within-radius
    # pair must agree exactly on at least one band.
    band_bits = 16
    band_count = 64 // band_bits

    buckets: dict[tuple[int, int], list[int]] = {}
    for idx, (_id, fp) in enumerate(entries):
        for band in _bands(fp, band_count, band_bits):
            buckets.setdefault(band, []).append(idx)

    seen_pairs: set[tuple[int, int]] = set()
    pairs = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                i, j = members[a], members[b]
                if i > j:
                    i, j = j, i
                key = (i, j)
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                if hamming(entries[i][1], entries[j][1]) <= max_distance:
                    pairs.append((entries[i][0], entries[j][0]))
    return pairs

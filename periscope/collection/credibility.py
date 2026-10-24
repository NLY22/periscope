"""Source-family credibility priors shared with the ingestion adapter."""

from __future__ import annotations

from typing import Optional

from periscope.runtime import paths


#: Used when a source has no family the project recognises. Matches the
#: community default in `tools/ingest_all.py` rather than inventing a value.

ROOT = paths.ROOT

DEFAULT_PRIOR = 0.5


def _source_type_of(source_key: str, kind: str = "") -> Optional[str]:
    """The `SourceType` name for a catalog key, or None if unknown.

    Mirrors `ingest_all`: the per-source table wins, then the coarse
    `kind` -> SourceType table. Returns the *name* so this module does not need
    to import `src.models` at module scope.
    """
    try:
        from .families import KIND_TO_SOURCE, SOURCE_KEY_TO_SOURCE
    except Exception:  # noqa: BLE001 - tools/ may not be importable standalone
        return None

    st = SOURCE_KEY_TO_SOURCE.get(source_key)
    if st is not None:
        return getattr(st, "name", str(st))

    try:
        from periscope.collection import sources as collector
        from .extensions import EXTRA_SOURCES

        src = next((s for s in (*collector.CATALOG, *EXTRA_SOURCES) if s.key == source_key), None)
        st = KIND_TO_SOURCE.get(src.kind if src is not None else kind)
    except Exception:  # noqa: BLE001
        return None
    return getattr(st, "name", str(st)) if st is not None else None


def prior_for(source_key: str, kind: str = "") -> float:
    """Credibility prior (0-1) for the family `source_key` belongs to.

    Accepts either a catalog key (`hupu_search`) or a bare family name
    (`hupu`), because the browser collectors emit the short platform name as the
    row's `source`.
    """
    if not source_key:
        return DEFAULT_PRIOR

    name = _source_type_of(source_key, kind)
    if name is None:
        # A bare platform name: try it as a family key directly.
        name = source_key

    try:
        from src.corpus.trust import SOURCE_PRIORS
    except Exception:  # noqa: BLE001
        return DEFAULT_PRIOR

    prior = SOURCE_PRIORS.get(name.lower())
    return float(prior) if prior is not None else DEFAULT_PRIOR

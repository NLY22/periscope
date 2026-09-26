"""OpenAI-compatible embeddings client for the semantic retrieval leg.

Kept separate from `client.py` because it is a different failure profile: an
embedding call is only ever an *enhancement*, so every error here must end as
"no vectors", never as a broken pipeline or a half-written index.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

import httpx

logger = logging.getLogger(__name__)


class EmbeddingError(RuntimeError):
    """Raised when the embeddings endpoint cannot serve this request."""


class EmbeddingClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    async def embed(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        payload = {"model": self.model, "input": texts}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/embeddings", json=payload, headers=headers
                )
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise EmbeddingError(f"embeddings request failed: {exc}") from exc

        rows = data.get("data") or []
        if len(rows) != len(texts):
            raise EmbeddingError(
                f"embeddings returned {len(rows)} rows for {len(texts)} inputs"
            )
        ordered = sorted(rows, key=lambda r: r.get("index", 0))
        vectors = [list(r.get("embedding") or []) for r in ordered]
        if any(len(v) == 0 for v in vectors):
            raise EmbeddingError("embeddings response contained empty vectors")
        return vectors

    @classmethod
    def from_config(cls, config: Any) -> Optional["EmbeddingClient"]:
        """Build the client when retrieval.semantic is configured, else None.

        Key and base URL fall back to the primary AI provider so an OpenAI-
        compatible hub (the default Agnes layer) needs no extra secrets, but
        embeddings are OFF unless a model name is given — the endpoint may not
        serve them, and silence is cheaper than a failing call every run.
        """
        retrieval = getattr(config, "retrieval", None)
        if retrieval is None or not getattr(retrieval, "semantic", False):
            return None
        model = (getattr(retrieval, "embedding_model", "") or "").strip()
        if not model:
            logger.info("retrieval.semantic enabled but no embedding_model; lexical only")
            return None

        ai = config.ai
        base_url = (getattr(retrieval, "embedding_base_url", "") or "").strip()
        if not base_url:
            base_url = getattr(ai, "base_url", "") or ""
        key_env = (getattr(retrieval, "embedding_api_key_env", "") or "").strip()
        if key_env:
            api_key = _env(key_env)
        else:
            api_key = _env(getattr(ai, "api_key_env", "") or "")
        if not base_url or not api_key:
            logger.info("semantic leg disabled: missing embedding base_url or api key")
            return None
        return cls(base_url=base_url, api_key=api_key, model=model)


def _env(name: str) -> str:
    if not name:
        return ""
    import os

    return os.environ.get(name, "").strip()

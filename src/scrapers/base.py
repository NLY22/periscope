"""Base scraper interface."""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, List, Optional
import httpx

from ..models import ContentItem
from .auth import AuthProvider, NullAuth
from .throttle import Throttle


class BaseScraper(ABC):
    """Abstract base class for all scrapers."""

    def __init__(
        self,
        config: dict,
        http_client: httpx.AsyncClient,
        throttle: Optional[Throttle] = None,
        auth: Optional[AuthProvider] = None,
    ):
        """Initialize scraper.

        Args:
            config: Scraper-specific configuration
            http_client: Shared async HTTP client
            throttle: Per-host rate limiter. Defaults to an unlimited one, so
                the fourteen existing scrapers keep their current behaviour
                without being touched.
            auth: Credential provider. Defaults to no credentials.
        """
        self.config = config
        self.client = http_client
        self.throttle = throttle if throttle is not None else Throttle()
        self.auth = auth if auth is not None else NullAuth()

    @abstractmethod
    async def fetch(self, since: datetime) -> List[ContentItem]:
        """Fetch content items published since the given time.

        Args:
            since: Only fetch items published after this time. A source whose
                spec declares `time_basis_default="unknown"` (hot lists,
                recommendation feeds) must not filter on it — an item with no
                publish time is not an old item.

        Returns:
            List[ContentItem]: Fetched content items
        """
        pass

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """One throttled, authenticated request.

        The auth provider's headers are merged into whatever the caller
        passed, so a scraper's own User-Agent survives. On 401/403 the
        provider gets exactly one chance to refresh (reload a cookie file); a
        second refusal is returned to the caller untouched.
        """
        headers = dict(kwargs.pop("headers", None) or {})
        headers.update(self.auth.headers())
        response = await self.throttle.request(
            self.client, method, url, headers=headers, **kwargs
        )
        if response.status_code in (401, 403) and self.auth.on_unauthorized():
            headers.update(self.auth.headers())
            response = await self.throttle.request(
                self.client, method, url, headers=headers, **kwargs
            )
        return response

    def _generate_id(self, source_type: str, subtype: str, native_id: str) -> str:
        """Generate unique content item ID.

        Args:
            source_type: Source type (github, hackernews, etc.)
            subtype: Content subtype (event, release, story, etc.)
            native_id: Native ID from the source platform

        Returns:
            str: Unique ID in format {source}:{subtype}:{native_id}
        """
        return f"{source_type}:{subtype}:{native_id}"

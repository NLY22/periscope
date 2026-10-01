"""Credential providers for sources that need more than an anonymous GET.

Deliberately narrow: an environment token and a cookie file. Signature-based
schemes (x-s/x-t style) are out of scope — see the spec's "explicitly not
doing" list — so no empty abstraction is reserved for them here.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Mapping, Optional, Protocol, Tuple

logger = logging.getLogger(__name__)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_HTTP_ONLY = "#HttpOnly_"


class AuthProvider(Protocol):
    """Headers to attach, and whether a 401/403 is worth one retry."""

    def headers(self) -> Dict[str, str]: ...

    def on_unauthorized(self) -> bool: ...


class NullAuth:
    """The default: behave exactly like an unauthenticated client."""

    def headers(self) -> Dict[str, str]:
        return {}

    def on_unauthorized(self) -> bool:
        return False


class EnvTokenAuth:
    """A bearer/api-key token read from an environment variable."""

    def __init__(
        self,
        env_var: str,
        scheme: str = "Bearer",
        header: str = "Authorization",
        environ: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.env_var = env_var
        self.scheme = scheme
        self.header = header
        self._environ = os.environ if environ is None else environ

    def headers(self) -> Dict[str, str]:
        token = (self._environ.get(self.env_var) or "").strip()
        if not token:
            return {}
        value = f"{self.scheme} {token}" if self.scheme else token
        return {self.header: value}

    def on_unauthorized(self) -> bool:
        return False


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        logger.warning("Could not read cookie file %s: %s", path.name, exc)
        return None


def _netscape_entries(raw: str) -> Iterator[Tuple[str, str, str]]:
    """Yield (expiry_field, name, value) for each Netscape cookie record.

    `#HttpOnly_` is a flag prefix on a real record, not a comment. Skipping it
    as a comment would silently drop exactly the cookie a logged-in source
    needs (BDUSS style session tokens are always HttpOnly).
    """
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(_HTTP_ONLY):
            line = line[len(_HTTP_ONLY):]
        elif line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) >= 7 and fields[5]:
            yield fields[4], fields[5], fields[6]


def _json_entries(raw: str) -> List[dict]:
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("Unparseable JSON cookie file: %s", exc)
        return []
    return [e for e in entries if isinstance(e, dict) and e.get("name")] if isinstance(
        entries, list
    ) else []


def load_cookie_file(path: Path | str) -> Dict[str, str]:
    """Read a Netscape cookies.txt or a browser-exported JSON cookie list."""
    raw = _read(Path(path))
    if raw is None:
        return {}
    if raw.lstrip().startswith("["):
        return {
            str(e["name"]): str(e.get("value", "")) for e in _json_entries(raw)
        }
    return {name: value for _, name, value in _netscape_entries(raw)}


def _cookie_expiry(path: Path) -> Dict[str, datetime]:
    """Best-effort name -> expiry map; entries without one are omitted."""
    raw = _read(path)
    if raw is None:
        return {}
    out: Dict[str, datetime] = {}
    if raw.lstrip().startswith("["):
        for entry in _json_entries(raw):
            stamp = entry.get("expirationDate") or entry.get("expires")
            if isinstance(stamp, (int, float)) and stamp > 0:
                out[str(entry["name"])] = _EPOCH + timedelta(seconds=float(stamp))
        return out
    for expiry_field, name, _ in _netscape_entries(raw):
        if expiry_field.isdigit() and int(expiry_field) > 0:
            out[name] = _EPOCH + timedelta(seconds=int(expiry_field))
    return out


class CookieFileAuth:
    """Cookies loaded from disk, with expiry detection and one reload.

    A stale cookie file is the most common reason a logged-in scraper silently
    starts returning login pages, so the failure is named in `expired_names`
    instead of surfacing downstream as "found 0 items".
    """

    def __init__(
        self,
        path: Path | str,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        max_age: Optional[timedelta] = None,
    ) -> None:
        self.path = Path(path)
        self._now = now
        self._max_age = max_age
        self._reloaded = False
        self._cookies: Dict[str, str] = {}
        self.expired_names: Tuple[str, ...] = ()
        self._load()

    def _load(self) -> None:
        moment = self._now()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        expiries = _cookie_expiry(self.path)
        rejected: List[str] = []
        usable: Dict[str, str] = {}
        for name, value in load_cookie_file(self.path).items():
            deadline = expiries.get(name)
            expired = deadline is not None and deadline <= moment
            not_fresh = (
                self._max_age is not None
                and deadline is not None
                and deadline - moment > self._max_age
            )
            if expired or not_fresh:
                rejected.append(name)
            else:
                usable[name] = value
        self._cookies = usable
        self.expired_names = tuple(rejected)
        if rejected:
            logger.warning(
                "Cookie file %s has unusable entries: %s",
                self.path.name, ", ".join(rejected),
            )

    def headers(self) -> Dict[str, str]:
        if not self._cookies:
            return {}
        return {"Cookie": "; ".join(f"{k}={v}" for k, v in self._cookies.items())}

    def on_unauthorized(self) -> bool:
        if self._reloaded:
            return False
        self._reloaded = True
        self._load()
        return bool(self._cookies)

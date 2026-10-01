"""Credential providers: no network, no real clock, no real environment."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.scrapers.auth import (
    CookieFileAuth,
    EnvTokenAuth,
    NullAuth,
    load_cookie_file,
)

T0 = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
FRESH = 1893456000  # 2030-01-01, comfortably after T0
STALE = 1000000000  # 2001-09-09, comfortably before T0

NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    f"#HttpOnly_.example.com\tTRUE\t/\tTRUE\t{FRESH}\tBDUSS\tsecret-value\n"
    f".example.com\tTRUE\t/\tFALSE\t{FRESH}\tBAIDUID\tABCD:1234\n"
)

BROWSER_JSON = """[
  {"name": "BDUSS", "value": "secret-value", "domain": ".example.com",
   "expirationDate": %d},
  {"name": "BAIDUID", "value": "ABCD:1234", "domain": ".example.com",
   "expirationDate": %d}
]""" % (FRESH, FRESH)


def test_null_auth_contributes_nothing() -> None:
    assert NullAuth().headers() == {}
    assert NullAuth().on_unauthorized() is False


def test_env_token_reads_the_named_variable() -> None:
    assert EnvTokenAuth("MY_TOKEN", environ={"MY_TOKEN": "abc123"}).headers() == {
        "Authorization": "Bearer abc123"
    }


def test_env_token_supports_a_custom_scheme_and_header() -> None:
    auth = EnvTokenAuth("K", scheme="token", header="X-Api-Key", environ={"K": "v"})
    assert auth.headers() == {"X-Api-Key": "token v"}


def test_schemeless_token_is_sent_bare() -> None:
    assert EnvTokenAuth("K", scheme="", environ={"K": "v"}).headers() == {"Authorization": "v"}


def test_missing_or_blank_env_token_degrades_to_no_headers() -> None:
    assert EnvTokenAuth("ABSENT", environ={}).headers() == {}
    assert EnvTokenAuth("K", environ={"K": "   "}).headers() == {}


def test_env_token_cannot_be_refreshed() -> None:
    assert EnvTokenAuth("K", environ={"K": "v"}).on_unauthorized() is False


def test_loads_netscape_cookie_file(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(NETSCAPE, encoding="utf-8")
    assert load_cookie_file(path) == {"BDUSS": "secret-value", "BAIDUID": "ABCD:1234"}


def test_loads_browser_json_cookie_file(tmp_path: Path) -> None:
    path = tmp_path / "cookies.json"
    path.write_text(BROWSER_JSON, encoding="utf-8")
    assert load_cookie_file(path) == {"BDUSS": "secret-value", "BAIDUID": "ABCD:1234"}


def test_unreadable_cookie_file_degrades_to_empty(tmp_path: Path) -> None:
    assert load_cookie_file(tmp_path / "nope.txt") == {}


def test_cookie_auth_sends_one_cookie_header(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(NETSCAPE, encoding="utf-8")
    headers = CookieFileAuth(path, now=lambda: T0).headers()
    assert set(headers) == {"Cookie"}
    pairs = dict(p.split("=", 1) for p in headers["Cookie"].split("; "))
    assert pairs == {"BDUSS": "secret-value", "BAIDUID": "ABCD:1234"}


def test_missing_cookie_file_degrades_to_no_headers(tmp_path: Path) -> None:
    assert CookieFileAuth(tmp_path / "nope.txt", now=lambda: T0).headers() == {}


def test_expired_cookies_are_named_instead_of_silently_sent(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f".example.com\tTRUE\t/\tTRUE\t{STALE}\tBDUSS\told\n",
        encoding="utf-8",
    )
    auth = CookieFileAuth(path, now=lambda: T0)
    assert auth.headers() == {}
    assert auth.expired_names == ("BDUSS",)


def test_a_stale_file_allows_exactly_one_reload(tmp_path: Path) -> None:
    path = tmp_path / "cookies.txt"
    path.write_text(NETSCAPE, encoding="utf-8")
    auth = CookieFileAuth(path, now=lambda: T0)
    assert auth.on_unauthorized() is True  # re-read from disk
    assert auth.on_unauthorized() is False  # never again for this instance


def test_max_age_rejects_a_file_that_is_not_freshly_exported(tmp_path: Path) -> None:
    """max_age means 'the cookie must be within this far of expiring'.

    A cookie expiring in 2030 is not proof of a recently exported file, so a
    one-day max_age rejects it. This is the guard against replaying a cookie
    dump somebody committed months ago.
    """
    path = tmp_path / "cookies.txt"
    path.write_text(NETSCAPE, encoding="utf-8")
    auth = CookieFileAuth(path, now=lambda: T0, max_age=timedelta(days=1))
    assert auth.headers() == {}
    assert auth.expired_names == ("BDUSS", "BAIDUID")

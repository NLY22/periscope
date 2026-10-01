"""Throttling with an injected clock: no test in here sleeps for real."""

import asyncio
import random

import httpx
import pytest

from src.models import RateLimit
from src.scrapers.throttle import Throttle, retry_after_seconds


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def run(coro):
    return asyncio.run(coro)


def throttle(time: FakeTime, default: RateLimit | None, **kw) -> Throttle:
    return Throttle(default=default, clock=time.clock, sleeper=time.sleep,
                    rng=random.Random(0), **kw)


def test_no_limit_means_no_waiting() -> None:
    t = FakeTime()
    th = throttle(t, None)
    run(th.acquire("https://a.example/x"))
    run(th.acquire("https://a.example/x"))
    assert t.slept == []


def test_second_request_to_the_same_host_waits_one_interval() -> None:
    t = FakeTime()
    th = throttle(t, RateLimit(requests=1, per_seconds=2.0, jitter=0.0))
    run(th.acquire("https://a.example/1"))
    run(th.acquire("https://a.example/2"))
    assert t.slept == [2.0]


def test_first_request_never_waits() -> None:
    t = FakeTime()
    th = throttle(t, RateLimit(requests=1, per_seconds=5.0, jitter=0.0))
    run(th.acquire("https://a.example/1"))
    assert t.slept == []


def test_different_hosts_are_independent() -> None:
    t = FakeTime()
    th = throttle(t, RateLimit(requests=1, per_seconds=2.0, jitter=0.0))
    run(th.acquire("https://a.example/1"))
    run(th.acquire("https://b.example/1"))
    assert t.slept == []


def test_per_host_limit_overrides_the_default() -> None:
    t = FakeTime()
    th = throttle(t, RateLimit(requests=1, per_seconds=10.0, jitter=0.0),
                  limits={"a.example": RateLimit(requests=1, per_seconds=1.0, jitter=0.0)})
    run(th.acquire("https://a.example/1"))
    run(th.acquire("https://a.example/2"))
    assert t.slept == [1.0]


def test_requests_per_window_shortens_the_interval() -> None:
    t = FakeTime()
    th = throttle(t, RateLimit(requests=4, per_seconds=2.0, jitter=0.0))
    for i in range(3):
        run(th.acquire(f"https://a.example/{i}"))
    assert t.slept == [0.5, 0.5]


def test_jitter_stays_within_the_declared_band() -> None:
    t = FakeTime()
    th = throttle(t, RateLimit(requests=1, per_seconds=2.0, jitter=0.3))
    run(th.acquire("https://a.example/1"))
    run(th.acquire("https://a.example/2"))
    assert 1.4 <= t.slept[0] <= 2.6


def test_limit_for_accepts_a_bare_host() -> None:
    th = throttle(FakeTime(), None, limits={"a.example": RateLimit(per_seconds=3.0)})
    assert th.limit_for("a.example").per_seconds == 3.0
    assert th.limit_for("https://a.example/p").per_seconds == 3.0
    assert th.limit_for("https://b.example/p") is None


def test_429_is_retried_once_after_retry_after() -> None:
    t = FakeTime()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = run(throttle(t, None).request(client, "GET", "https://a.example/x"))
    run(client.aclose())
    assert response.status_code == 200
    assert calls == ["/x", "/x"]
    assert 3.0 in t.slept


def test_429_without_retry_after_uses_the_fallback() -> None:
    t = FakeTime()
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(429) if len(seen) == 1 else httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    run(throttle(t, None).request(client, "GET", "https://a.example/x"))
    run(client.aclose())
    assert 5.0 in t.slept


def test_429_twice_is_not_retried_a_third_time() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(429)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = run(throttle(FakeTime(), None).request(client, "GET", "https://a.example/x"))
    run(client.aclose())
    assert response.status_code == 429
    assert len(seen) == 2


def test_non_429_errors_are_not_retried() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        return httpx.Response(500)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    run(throttle(FakeTime(), None).request(client, "GET", "https://a.example/x"))
    run(client.aclose())
    assert len(seen) == 1


@pytest.mark.parametrize("header,expected", [
    ("7", 7.0), ("0", 0.0), ("2.5", 2.5), ("", 5.0),
    ("Wed, 21 Oct 2026 07:28:00 GMT", 5.0), ("-3", 0.0),
])
def test_retry_after_parsing_never_raises(header: str, expected: float) -> None:
    headers = {"Retry-After": header} if header else {}
    assert retry_after_seconds(httpx.Response(429, headers=headers)) == expected


# ----------------------------------------------------------- BaseScraper glue
def test_scraper_request_merges_throttle_and_auth_headers() -> None:
    from src.scrapers.auth import EnvTokenAuth
    from src.scrapers.base import BaseScraper

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.headers))
        return httpx.Response(200, json={"ok": True})

    class Probe(BaseScraper):
        async def fetch(self, since):
            return []

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    scraper = Probe({}, client, auth=EnvTokenAuth("T", environ={"T": "tok"}))
    run(scraper._request("GET", "https://a.example/x", headers={"User-Agent": "probe"}))
    run(client.aclose())
    assert seen["authorization"] == "Bearer tok"
    assert seen["user-agent"] == "probe"


def test_scraper_retries_once_when_auth_says_refresh() -> None:
    from src.scrapers.base import BaseScraper

    attempts = []

    class FlakyAuth:
        def __init__(self) -> None:
            self.n = 0

        def headers(self):
            self.n += 1
            return {"Authorization": f"attempt-{self.n}"}

        def on_unauthorized(self):
            return self.n < 2

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.headers["authorization"])
        return httpx.Response(401 if len(attempts) == 1 else 200)

    class Probe(BaseScraper):
        async def fetch(self, since):
            return []

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    response = run(Probe({}, client, auth=FlakyAuth())._request("GET", "https://a.example/x"))
    run(client.aclose())
    assert response.status_code == 200
    assert attempts == ["attempt-1", "attempt-2"]


def test_default_throttle_has_no_limits_so_existing_scrapers_are_unchanged() -> None:
    from src.scrapers.base import BaseScraper

    class Probe(BaseScraper):
        async def fetch(self, since):
            return []

    scraper = Probe({}, httpx.AsyncClient())
    assert scraper.throttle.limit_for("https://anything.example/x") is None
    assert scraper.auth.headers() == {}

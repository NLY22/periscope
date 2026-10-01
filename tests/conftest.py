from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def pin_dns(monkeypatch):
    """Never let the machine's resolver decide whether the suite is green.

    `src/url_security.py` resolves webhook hostnames on purpose -- that check
    is what stops a config typo from pointing the notifier at 169.254.169.254.
    But behind a network that intercepts DNS, `example.com` answers with an
    address from the RFC 2544 benchmark range, which is *not* globally
    routable, so a security check that is working correctly failed 20 webhook
    tests on one machine and not another.

    Tests that want to exercise the resolver itself patch
    `src.url_security._resolve_hostname` directly (see test_url_security.py),
    so pinning only the default lookup keeps both the determinism and the
    coverage.
    """

    def fake_resolver(hostname: str, port: int) -> list:
        return [(2, 1, 6, "", ("93.184.215.14", port))]

    monkeypatch.setattr("src.url_security._default_resolver", fake_resolver)

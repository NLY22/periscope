"""The reachability probe harness (spec §6's "measure, then write code" rule).

The bodies below are transcribed from the responses recorded in spec §14.2, so
these tests assert the harness reaches the *same* conclusions that were reached
by hand — and will notice the day those platforms stop returning them.
Everything runs against `httpx.MockTransport`; nothing here touches the network.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.sources.reachability import (  # noqa: E402
    Probe, classify, probe_tieba, probe_xiaohongshu, summarise, to_json,
)

TIEBA_SECURITY_PAGE = (
    '<html><head><title>百度安全验证</title></head><body>'
    '<script>var BIOC_OPTIONS = {app: "bioc"};</script>'
    '<iframe src="https://seccaptcha.baidu.com/v2"></iframe></body></html>'
)
TIEBA_JSON_REFUSAL = '{"error_msg":"未知错误","error_code":110001}'
TIEBA_GOOD_LIST = (
    '<html><head><title>python吧 精华</title></head><body>'
    '<li class="t_con" data-field=\'{"id":10037465698,"author_name":"ashi876",'
    '"reply_count":37}\'><a>一个关于 GIL 的讨论</a></li>'
    '</body></html>'
)
XHS_NOTE_PAGE = (
    '<html><head><title>某笔记 - 小红书</title></head><body><script>'
    'window.__INITIAL_STATE__={"note":{"noteDesc":"官方定价是每百万 token 2 元，'
    '我把 512K 的合同丢进去测了一下。"}};'
    '</script></body></html>'
)
XHS_LOGIN_WALL = (
    '<html><head><title>小红书</title></head><body>'
    '<div class="login-tip">登录后查看搜索结果，请先登录</div></body></html>'
)


def client_for(responses: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for needle, response in responses.items():
            if needle in str(request.url):
                return response
        return httpx.Response(404, text="not modelled")

    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, follow_redirects=True)


# ------------------------------------------------------------------- classify
def test_a_captcha_interstitial_is_named_as_one_even_when_it_arrives_as_200() -> None:
    probe = classify("tieba", 1, "https://tieba.baidu.com/f?kw=python",
                     status=200, text=TIEBA_SECURITY_PAGE)
    assert probe.verdict == "blocked_captcha"
    assert "百度安全验证" in probe.markers or "seccaptcha" in probe.markers


def test_a_signed_api_refusal_is_not_reported_as_a_parse_failure() -> None:
    probe = classify("tieba", 1, "http://c.tieba.baidu.com/c/f/frs/page",
                     status=200, text=TIEBA_JSON_REFUSAL)
    assert probe.verdict == "signed_required"
    assert "110001" in probe.detail


def test_a_list_page_without_bodies_is_list_only_not_pass() -> None:
    """This is the tieba conclusion in one row: titles reachable, floors not."""
    probe = classify("tieba", 1, "https://tieba.baidu.com/f/good?kw=python",
                     status=200, text=TIEBA_GOOD_LIST)
    assert probe.verdict == "list_only"
    assert probe.body_found is False


def test_a_note_page_with_author_text_passes() -> None:
    probe = classify("xiaohongshu", 1, "https://www.xiaohongshu.com/explore/abc",
                     status=200, text=XHS_NOTE_PAGE)
    assert probe.verdict == "pass"
    assert probe.body_found is True


def test_a_login_wall_is_distinguished_from_a_captcha() -> None:
    probe = classify("xiaohongshu", 1, "https://www.xiaohongshu.com/search_result",
                     status=403, text=XHS_LOGIN_WALL)
    assert probe.verdict == "blocked_auth"


def test_a_transport_failure_becomes_a_row_instead_of_raising() -> None:
    probe = classify("tieba", 1, "https://tieba.baidu.com/f?kw=x",
                     error="ConnectTimeout: timed out")
    assert probe.verdict == "error"
    assert "ConnectTimeout" in probe.detail


def test_every_verdict_is_from_the_declared_vocabulary() -> None:
    from src.sources.reachability import VERDICTS

    probes = [
        classify("s", 1, "u", status=200, text=TIEBA_SECURITY_PAGE),
        classify("s", 1, "u", status=200, text=TIEBA_JSON_REFUSAL),
        classify("s", 1, "u", status=200, text=XHS_NOTE_PAGE),
        classify("s", 1, "u", error="boom"),
    ]
    assert {p.verdict for p in probes} <= set(VERDICTS)


# --------------------------------------------------------------------- routes
def test_the_tieba_probe_reproduces_the_recorded_conclusion(tmp_path) -> None:
    """§14.2 said: PC/mobile/thread blocked, /f/good reachable, JSON signed."""
    async def run() -> list:
        async with client_for({
            "tieba.baidu.com/f/good": httpx.Response(200, text=TIEBA_GOOD_LIST),
            "c.tieba.baidu.com": httpx.Response(200, text=TIEBA_JSON_REFUSAL),
            "tieba.baidu.com": httpx.Response(403, text=TIEBA_SECURITY_PAGE),
        }) as client:
            return await probe_tieba(client, "python")

    probes = asyncio.run(run())
    verdicts = {p.url.split("baidu.com")[1].split("?")[0]: p.verdict for p in probes}
    assert verdicts["/f/good"] == "list_only"
    assert verdicts["/c/f/frs/page"] == "signed_required"
    assert verdicts["/f"] == "blocked_captcha"
    assert verdicts["/p/10037465698"] == "blocked_captcha"
    summary = summarise(probes)["tieba"]
    assert summary["judgement"] == "list_only", (
        "floors unreachable => the source cannot validate the tiering work, "
        "which is exactly why P0 used Discourse instead"
    )
    assert summary["floors_or_body_reachable"] is False


def test_the_xhs_probe_reports_the_step1_verdict_from_the_note_body() -> None:
    async def run() -> list:
        async with client_for({
            "search_result": httpx.Response(403, text=XHS_LOGIN_WALL),
            "explore": httpx.Response(200, text=XHS_NOTE_PAGE),
        }) as client:
            return await probe_xiaohongshu(
                client, "https://www.xiaohongshu.com/explore/abc", keyword="定价")

    probes = asyncio.run(run())
    assert [p.verdict for p in probes] == ["blocked_auth", "pass"]
    assert summarise(probes)["xiaohongshu"]["judgement"] == "pass"


def test_a_blocked_everywhere_xhs_run_does_not_claim_success() -> None:
    async def run() -> list:
        async with client_for({
            "xiaohongshu.com": httpx.Response(200, text="<html>请先登录才能查看</html>"),
        }) as client:
            return await probe_xiaohongshu(
                client, "https://www.xiaohongshu.com/explore/missing", keyword="")

    probes = asyncio.run(run())
    assert summarise(probes)["xiaohongshu"]["judgement"] != "pass"


# ------------------------------------------------------------------ reporting
def test_the_json_report_is_serialisable_and_carries_evidence() -> None:
    async def run() -> list:
        async with client_for({
            "tieba.baidu.com/f/good": httpx.Response(200, text=TIEBA_GOOD_LIST),
            "c.tieba.baidu.com": httpx.Response(200, text=TIEBA_JSON_REFUSAL),
            "tieba.baidu.com": httpx.Response(403, text=TIEBA_SECURITY_PAGE),
        }) as client:
            return await probe_tieba(client, "python")

    payload = json.loads(to_json(asyncio.run(run())))
    row = payload["tieba"]["routes"][0]
    assert {"verdict", "status", "bytes", "markers", "detail"} <= set(row)
    assert payload["tieba"]["judgement"] == "list_only"


def test_a_probe_reports_ok_only_for_a_real_pass() -> None:
    assert Probe("s", 1, "u", "pass").ok is True
    assert Probe("s", 1, "u", "list_only").ok is False
    assert Probe("s", 1, "u", "blocked_captcha").ok is False


# ------------------------------------------------------------------ live gate
def test_the_script_refuses_to_fetch_without_the_online_flag(
    tmp_path: Path, capsys,
) -> None:
    """A probe that silently hits third-party sites is a side effect nobody asked for."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import spike_sources

    before = set(tmp_path.iterdir())
    code = spike_sources.main(["--source", "tieba", "--kw", "python",
                               "--out", str(tmp_path / "r.json")])
    assert code == 2
    assert set(tmp_path.iterdir()) == before, "no result file without --online"
    assert "--online" in capsys.readouterr().err


def test_the_script_also_refuses_xhs_without_a_url(tmp_path: Path, capsys) -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import spike_sources

    with pytest.raises(SystemExit) as exc:
        spike_sources.main(["--source", "xiaohongshu", "--online",
                            "--out", str(tmp_path / "r.json")])
    assert "note id" in str(exc.value)

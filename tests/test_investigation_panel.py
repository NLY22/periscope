from pathlib import Path
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest


ROOT = Path(__file__).resolve().parents[1] / "src" / "web" / "static"


def test_investigation_workspace_has_real_controls_and_backend_client():
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    script = (ROOT / "assets" / "investigation.js").read_text(encoding="utf-8")
    for element in ("investigationView", "invMessage", "invSend", "invStop", "invRounds", "invEvidence", "invHistory"):
        assert f'id="{element}"' in html
    for route in ("/api/investigations", "/api/investigation-rounds/", "/api/evidence/"):
        assert route in script
    assert "evidence_refreshed" in script and "version_id" in script


def test_browser_can_start_followup_view_evidence_and_stop(scratch_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    edge = Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe"
    monkeypatch.setenv("TEMP", str(scratch_path))
    monkeypatch.setenv("TMP", str(scratch_path))
    handler = partial(SimpleHTTPRequestHandler, directory=str(ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    requests, current = [], {"round": 0, "reads": 0, "stop": False, "evidence_reads": 0}
    def route_api(route):
        request = route.request
        path = request.url.split("?", 1)[0].split("/api/", 1)[-1]
        requests.append((request.method, path))
        data = {}
        if path == "investigations" and request.method == "POST":
            data = {"investigation_id": "inv_test"}
        elif path == "investigations":
            data = {"investigations": [{"id": "inv_test", "question": "robot"}]}
        elif path == "investigations/inv_test/rounds":
            current["round"] += 1
            current["reads"] = 0
            current["stop"] = False
            data = {"round_id": f"r{current['round']}"}
        elif path.startswith("investigation-rounds/") and path.endswith("/stop"):
            current["stop"] = True
            data = {"status": "cancelled"}
        elif path.startswith("investigation-rounds/"):
            rid = path.split("/")[1]
            current["reads"] += 1
            done = (rid == "r1" and current["reads"] > 1) or rid == "r3"
            data = {"round": {"id": rid, "message": "robot" if rid == "r1" else "followup", "status": "cancelled" if current["stop"] else "completed" if done else "running", "created_at": "2026-01-01T00:00:00Z"},
                    "events": [] if "after=1" in request.url or rid == "r2" else [{"seq": 1, "type": "evidence", "origin": {"kind": "collected", "observed_at": "2026-01-01T00:00:00Z"}, "references": [{"object_id": "obj", "version_id": "ver"}]}],
                    "answer": {"status": "completed", "text": "发现 [1]", "citations": [{"number": 1, "reference": {"object_id": "obj", "version_id": "ver"}}]} if done and rid == "r1" else None}
        elif path == "evidence/obj":
            current["evidence_reads"] += 1
            if current["evidence_reads"] == 1:
                route.fulfill(status=503, content_type="application/json", body='{"detail":"temporary"}')
                return
            data = {"version": {"title": "证据标题", "text": "正文 <script>unsafe</script>", "url": "https://example.org/"}}
        elif path == "stats":
            data = {"corpus": {"items": 0}, "research": {"sessions": 0}, "claims": {}, "ai_available": False}
        elif path in {"items", "search"}:
            data = {"items": [], "count": 0}
        elif path == "claims":
            data = {"claims": []}
        elif path == "research":
            data = {"sessions": []}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(data, ensure_ascii=False))
    try:
        with playwright.sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, executable_path=str(edge) if edge.is_file() else None)
            except playwright.Error as exc:
                pytest.skip(f"browser unavailable: {exc}")
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.route("**/api/**", route_api)
            page.goto(f"http://127.0.0.1:{server.server_port}/index.html")
            assert page.locator("#investigationView").is_visible()
            page.locator("#invMessage").fill("robot")
            page.locator("#invSend").click()
            page.locator(".inv-source summary").click()
            page.get_by_role("button", name="重新读取").click()
            page.get_by_text("证据标题", exact=True).wait_for()
            page.get_by_role("button", name="查看引用 1").wait_for(timeout=8000)
            page.get_by_role("button", name="查看引用 1").click()
            assert page.locator(".inv-source[open] .inv-source-text").inner_text() == "正文 <script>unsafe</script>"
            if os.environ.get("PERISCOPE_UI_SCREENSHOT"):
                page.screenshot(path=os.environ["PERISCOPE_UI_SCREENSHOT"], full_page=True)
            page.locator("#invMessage").fill("followup")
            page.locator("#invSend").click()
            page.locator("#invStop").wait_for()
            page.locator("#invStop").click()
            page.get_by_text("已停止", exact=True).first.wait_for()
            assert ("POST", "investigation-rounds/r2/stop") in requests
            page.locator('.inv-round[data-round="r1"]').click()
            assert page.locator("#invStatus").inner_text() == "本轮已完成"
            page.locator('.inv-round[data-round="r2"]').click()
            assert page.locator("#invStatus").inner_text() == "已停止"
            page.locator("#invMessage").fill("third")
            page.locator("#invSend").click()
            page.get_by_text("本轮未生成简答，可检查模型配置并查看原始资料。", exact=True).wait_for()
            # A slow history load must not allow a follow-up or reset to race it.
            page.evaluate("""() => {
              const original = window.fetch;
              window.fetch = (url, init) => String(url).endsWith('/api/investigations/inv_test')
                ? window.historyFails ? Promise.resolve(new Response('{}',{status:503})) : new Promise(resolve => { window.releaseHistory = () => resolve(new Response(JSON.stringify({investigation:{question:'robot'},rounds:[]}),{headers:{'Content-Type':'application/json'}})); })
                : original(url, init);
            }""")
            page.locator("#invHistory").select_option("")
            page.locator("#invHistory").select_option("inv_test")
            page.wait_for_function("typeof window.releaseHistory === 'function'")
            assert page.locator("#invSend").is_disabled()
            assert page.locator("#newResearch").is_disabled()
            page.evaluate("window.releaseHistory()")
            page.wait_for_function("!document.querySelector('#invSend').disabled")
            page.evaluate("window.historyFails = true")
            page.locator("#invHistory").select_option("")
            page.locator("#invHistory").select_option("inv_test")
            page.get_by_text("调查记录读取失败，请重新选择。", exact=True).wait_for()
            assert page.locator("#invHistory").input_value() == ""
            assert page.locator("#invSend").inner_text() == "开始调查"
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.screenshot(path=str(scratch_path / "investigation-mobile.png"), full_page=True)
            browser.close()
    finally:
        server.shutdown()
        server.server_close()

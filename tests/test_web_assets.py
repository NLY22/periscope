"""The panel's bundled artwork is available without exposing application files."""

from fastapi.testclient import TestClient

from src.web.app import create_app


def test_bundled_lens_artwork_is_served():
    with TestClient(create_app(None)) as client:
        response = client.get("/assets/optical-lens-v2.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_assets_do_not_expose_other_panel_files():
    with TestClient(create_app(None)) as client:
        for path in ("/assets/index.html", "/assets/%2e%2e/app.py"):
            assert client.get(path).status_code == 404


def test_bundled_font_has_web_font_content_type():
    with TestClient(create_app(None)) as client:
        response = client.head("/assets/fonts/NotoSansSC-VF.woff2")
    assert response.status_code == 200
    assert response.headers["content-type"] == "font/woff2"

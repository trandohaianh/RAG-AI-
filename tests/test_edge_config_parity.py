"""Hai cấu hình biên (deploy/cloud/Caddyfile và deploy/production/nginx/nginx.conf) được viết
tay và từng lệch nhau (Caddy chặn nhúng /embed; nginx chặn geolocation mà màn điều phối cần).
Test này khoá các bất biến chung để lần lệch sau bị CI bắt thay vì lộ ra trên production."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CADDY = (ROOT / "deploy/cloud/Caddyfile").read_text(encoding="utf-8")
NGINX = (ROOT / "deploy/production/nginx/nginx.conf").read_text(encoding="utf-8")
WEB_SOURCES = "\n".join(p.read_text(encoding="utf-8", errors="ignore")
                        for p in (ROOT / "frontend").glob("**/*.tsx") if "node_modules" not in p.parts)


def _permissions_policy(text: str) -> str:
    match = re.search(r'Permissions-Policy\s+"([^"]+)"', text)
    assert match, "Permissions-Policy header is missing"
    return match.group(1)


def test_both_edges_send_the_same_baseline_security_headers():
    for name, text in (("Caddy", CADDY), ("nginx", NGINX)):
        for header in ("Strict-Transport-Security", "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy"):
            assert header in text, f"{name} does not send {header}"
    assert _permissions_policy(CADDY) == _permissions_policy(NGINX)


def test_permissions_policy_allows_every_browser_capability_the_web_app_uses():
    for name, text in (("Caddy", CADDY), ("nginx", NGINX)):
        policy = _permissions_policy(text)
        if "navigator.geolocation" in WEB_SOURCES:
            assert "geolocation=(self)" in policy, f"{name} blocks geolocation used by the dispatch editor"


def test_embed_widget_stays_frameable_on_both_edges():
    # Caddy: X-Frame-Options chỉ cho các path KHÔNG phải /embed.
    assert re.search(r"@not_embed\s+not\s+path\s+/embed\s+/embed/\*", CADDY)
    assert re.search(r'header\s+@not_embed\s+X-Frame-Options', CADDY)
    assert not re.search(r"^\s*X-Frame-Options", CADDY.split("@not_embed", 1)[0], re.M), \
        "a site-wide X-Frame-Options would blank the embed widget"
    # nginx: không đặt X-Frame-Options toàn cục (chính sách khung do CSP của ứng dụng quyết định).
    assert "X-Frame-Options" not in NGINX


def test_streaming_chat_is_not_buffered_by_either_edge():
    assert "flush_interval -1" in CADDY
    chat_block = re.search(r"location ~ \^/\(chat/\|embed/chat\$\) \{(.*?)\}", NGINX, re.S)
    assert chat_block and "proxy_buffering off" in chat_block.group(1)

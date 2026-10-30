"""Regression tests for ``getBaseUrl``/``getUrlScheme`` in ``static/js/api.js``.

The backend API and WebSocket URL scheme must be derived from the current page
protocol so a UI served over HTTPS talks to the backend over HTTPS/WSS too,
avoiding active/passive mixed-content blocking. These tests execute the *real*
``static/js/api.js`` source (only its ``import``/``export`` statements are
stripped so it can load as a classic script) inside an embedded QuickJS engine,
with a stubbed ``window`` whose ``location.protocol`` controls the derived
scheme. ``getBaseUrl``/``getUrlScheme`` are synchronous, so their results are
read back directly; ``createStreamingConnection`` is exercised with a stubbed
``WebSocket`` that records the URL it was constructed with.
"""

import json
from pathlib import Path

import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
API_JS_PATH = REPO_ROOT / "static" / "js" / "api.js"
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"

# Browser / import-binding stubs injected before loading api.js. ``localStorage``
# returns null for every key so ``getSettings`` falls back to its defaults
# (serverAddress ``localhost``, serverPort ``42069``, websocketPort ``8765``).
# No ``window`` is declared here, so callers that want a browser-like page
# protocol must evaluate their own ``var window = {...}`` before the api.js
# source. ``signData``/``wifToAddress`` stand in for api.js's ``./bitcoin.js``
# import bindings.
_STUBS = """
var console = { log: function () {}, error: function () {} };
var localStorage = {
    getItem: function () { return null; },
    setItem: function () {},
    removeItem: function () {}
};
var signData = function (data, wif) { return Promise.resolve(data); };
var wifToAddress = function () { return 'addr'; };
var __result = null;
"""

# ``WebSocket`` stub that records the URL argument ``createStreamingConnection``
# passes to ``new WebSocket(...)`` and exposes assignable handler slots.
_WEBSOCKET_STUB = """
var __wsUrl = null;
var WebSocket = function (url) {
    __wsUrl = url;
    return {
        send: function () {},
        onopen: null,
        onmessage: null,
        onerror: null,
        onclose: null
    };
};
"""


def _load_api_source() -> str:
    """Read ``static/js/api.js`` and turn it into a classic script quickjs can eval.

    ``import`` lines are commented out (their bindings are supplied as stubs),
    and the leading ``export`` keyword is dropped from ``export`` declarations
    while the declaration body is preserved verbatim.
    """
    lines = []
    for line in API_JS_PATH.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            lines.append("// " + line)
        elif stripped.startswith("export "):
            lines.append(line.replace("export ", "", 1))
        else:
            lines.append(line)
    return "\n".join(lines)


def _build_context(window_js: str | None = None) -> quickjs.Context:
    """Create a QuickJS context with stubs, an optional ``window`` stub, and api.js."""
    ctx = quickjs.Context()
    ctx.eval(_STUBS)
    if window_js:
        ctx.eval(window_js)
    ctx.eval(_load_api_source())
    return ctx


def _value(ctx: quickjs.Context, js_expression: str):
    """Evaluate ``js_expression`` and return its JSON round-tripped value."""
    return json.loads(ctx.eval("JSON.stringify(" + js_expression + ")"))


def _import_line(content: str) -> str:
    """Return the api.js import line from a template's module script."""
    for line in content.splitlines():
        if "import {" in line and "api.js" in line:
            return line
    raise AssertionError("no /static/js/api.js import found")


# --- scheme derivation ---------------------------------------------------------


def test_get_base_url_uses_https_scheme_when_page_is_https():
    ctx = _build_context("var window = { location: { protocol: 'https:' } };")
    assert _value(ctx, "getBaseUrl()") == "https://localhost:42069"


def test_get_base_url_uses_http_scheme_when_page_is_http():
    ctx = _build_context("var window = { location: { protocol: 'http:' } };")
    assert _value(ctx, "getBaseUrl()") == "http://localhost:42069"


def test_get_base_url_falls_back_to_http_without_window():
    ctx = _build_context()
    assert _value(ctx, "getBaseUrl()") == "http://localhost:42069"


def test_get_url_scheme_under_https():
    ctx = _build_context("var window = { location: { protocol: 'https:' } };")
    assert _value(ctx, "getUrlScheme()") == "https:"
    assert _value(ctx, "getUrlScheme(true)") == "wss:"


def test_get_url_scheme_under_http():
    ctx = _build_context("var window = { location: { protocol: 'http:' } };")
    assert _value(ctx, "getUrlScheme()") == "http:"
    assert _value(ctx, "getUrlScheme(true)") == "ws:"


# --- WebSocket streaming connection -------------------------------------------


def test_create_streaming_connection_uses_wss_under_https():
    ctx = _build_context(
        "var window = { location: { protocol: 'https:' } };\n" + _WEBSOCKET_STUB
    )
    ctx.eval("createStreamingConnection('cid', function () {}, function () {}, function () {});")
    assert _value(ctx, "__wsUrl") == "wss://localhost:8765"


def test_create_streaming_connection_uses_ws_under_http():
    ctx = _build_context(
        "var window = { location: { protocol: 'http:' } };\n" + _WEBSOCKET_STUB
    )
    ctx.eval("createStreamingConnection('cid', function () {}, function () {}, function () {});")
    assert _value(ctx, "__wsUrl") == "ws://localhost:8765"


# --- source-level invariant: no hardcoded http base URLs in templates ----------


def test_templates_no_longer_hardcode_http_base_url():
    for name in ("agent_home.html", "agents.html", "conversation.html"):
        content = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
        assert "`http://${settings.serverAddress}:${settings.serverPort}`" not in content
        assert "getBaseUrl" in _import_line(content)


def test_templates_no_longer_hardcode_ws_url():
    """Regression guard: the streaming WebSocket must derive its scheme
    (``wss://`` under HTTPS) from the page protocol via ``getUrlScheme(true)``
    rather than hardcoding ``ws://``.
    """
    for name in ("agent_home.html", "agents.html", "conversation.html"):
        content = (TEMPLATES_DIR / name).read_text(encoding="utf-8")
        assert "`ws://${settings.serverAddress}" not in content
        assert "ws://" not in content
        if "new WebSocket(" in content:
            assert "getUrlScheme(true)" in content

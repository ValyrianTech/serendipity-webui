"""Regression tests for URL scheme / transport hardening in ``static/js/api.js``.

The backend API and WebSocket URL scheme must be derived from the current page
protocol so a UI served over HTTPS talks to the backend over HTTPS/WSS too,
avoiding active/passive mixed-content blocking. These tests execute the *real*
``static/js/api.js`` source (only its ``import``/``export`` statements are
stripped so it can load as a classic script) inside an embedded QuickJS engine,
with a stubbed ``window`` whose ``location.protocol`` controls the derived
scheme. ``getBaseUrl``/``getUrlScheme`` are synchronous, so their results are
read back directly; ``createStreamingConnection`` is exercised with a stubbed
``WebSocket`` that records the URL it was constructed with.

The transport-hardening helpers (``isLoopbackHost``/``isInsecureTransport``/
``assertSecureTransport``) are exercised here too, both at runtime and via a
source-level guard that every signed helper calls ``assertSecureTransport()``
immediately before signing.
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


def _build_context_storage(map_json: str, window_js: str | None = None) -> quickjs.Context:
    """Create a context with a configurable ``localStorage`` map, optional ``window``, and api.js.

    ``getSettings`` reads ``localStorage.getItem``, so a JSON object map is used
    to control ``settings.serverAddress`` (and friends) for the transport checks.
    """
    ctx = quickjs.Context()
    ctx.eval(_STUBS)
    ctx.eval(
        "var __map = " + map_json + ";\n"
        "var localStorage = {\n"
        "    getItem: function (key) { return (key in __map) ? __map[key] : null; },\n"
        "    setItem: function (key, value) { __map[key] = value; },\n"
        "    removeItem: function (key) { delete __map[key]; }\n"
        "};\n"
    )
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


# --- transport hardening helpers ----------------------------------------------


def test_is_loopback_host():
    ctx = _build_context()
    for host in ("localhost", "127.0.0.1", "::1", "[::1]", "foo.localhost", " 127.0.0.1 "):
        assert _value(ctx, "isLoopbackHost(" + json.dumps(host) + ")") is True
    for host in ("192.168.1.10", "example.com", ""):
        assert _value(ctx, "isLoopbackHost(" + json.dumps(host) + ")") is False


def test_is_insecure_transport_true_for_http_lan():
    ctx = _build_context_storage(
        json.dumps({"serendipity_server_address": "192.168.1.10"}),
        "var window = { location: { protocol: 'http:' } };",
    )
    assert _value(ctx, "isInsecureTransport()") is True


def test_is_insecure_transport_false_for_http_localhost():
    ctx = _build_context_storage(
        json.dumps({"serendipity_server_address": "localhost"}),
        "var window = { location: { protocol: 'http:' } };",
    )
    assert _value(ctx, "isInsecureTransport()") is False


def test_is_insecure_transport_false_for_https_lan():
    ctx = _build_context_storage(
        json.dumps({"serendipity_server_address": "192.168.1.10"}),
        "var window = { location: { protocol: 'https:' } };",
    )
    assert _value(ctx, "isInsecureTransport()") is False


def test_is_insecure_transport_false_without_window():
    ctx = _build_context_storage(json.dumps({"serendipity_server_address": "192.168.1.10"}))
    assert _value(ctx, "isInsecureTransport()") is False


def test_assert_secure_transport_throws_for_http_lan():
    ctx = _build_context_storage(
        json.dumps({"serendipity_server_address": "192.168.1.10"}),
        "var window = { location: { protocol: 'http:' } };",
    )
    ctx.eval(
        "function __tryAssert() { try { assertSecureTransport(); return { ok: true }; } "
        "catch (e) { return { ok: false, msg: e.message }; } }"
    )
    result = _value(ctx, "__tryAssert()")
    assert result["ok"] is False
    assert "HTTPS" in result["msg"] or "plaintext" in result["msg"]


def test_assert_secure_transport_ok_for_https_lan():
    ctx = _build_context_storage(
        json.dumps({"serendipity_server_address": "192.168.1.10"}),
        "var window = { location: { protocol: 'https:' } };",
    )
    assert _value(ctx, "assertSecureTransport()") is True


def test_assert_secure_transport_ok_for_http_localhost():
    ctx = _build_context_storage(
        json.dumps({"serendipity_server_address": "localhost"}),
        "var window = { location: { protocol: 'http:' } };",
    )
    assert _value(ctx, "assertSecureTransport()") is True


def test_every_signed_helper_calls_assert_secure_transport():
    lines = API_JS_PATH.read_text(encoding="utf-8").splitlines()
    signed_helpers = 0
    for idx, line in enumerate(lines):
        if "const signedData = await signData(" not in line:
            continue
        signed_helpers += 1
        prev = idx - 1
        while prev >= 0 and not lines[prev].strip():
            prev -= 1
        assert prev >= 0, f"no preceding line for signData at line {idx + 1}"
        assert lines[prev].strip() == "assertSecureTransport();", (
            f"line {idx + 1} missing assertSecureTransport() immediately before signData"
        )
    assert signed_helpers > 0


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

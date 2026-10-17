"""Regression tests for ``parseJsonResponse`` in ``static/js/api.js``.

Every ``fetch`` wrapper in ``api.js`` funnels its response through the shared
``parseJsonResponse(response, context)`` helper. That helper turns non-OK HTTP
responses into friendly ``<Context> failed: ...`` errors (never leaking a raw
``SyntaxError`` such as ``Unexpected token '<'`` when an HTML error page is
mistakenly parsed as JSON), surfaces the backend's ``error``/``detail`` fields
when a JSON error payload is returned, and rejects successful responses whose
content type is not ``application/json``.

These tests execute the *real* ``static/js/api.js`` source (only its
``import``/``export`` statements are stripped so it can load as a classic
script) inside an embedded QuickJS engine, with a stubbed ``fetch`` that returns
a fake ``Response``. The settlement of each wrapper is captured via a promise
chain and read back after draining QuickJS's pending-job (microtask) queue. The
single ``return response.json();`` invariant is additionally locked in via
source inspection.
"""

import json
from pathlib import Path

import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
API_JS_PATH = REPO_ROOT / "static" / "js" / "api.js"

# Browser / import-binding stubs injected before loading api.js. ``localStorage``
# lets ``getSettings``/``getBaseUrl`` run; ``signData``/``wifToAddress`` stand in
# for api.js's ``./bitcoin.js`` import bindings; ``__result`` is where the
# promise-chain harness records a settled wrapper call.
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


def _make_fetch(ok, status, status_text, content_type, json_impl):
    """Build a ``var fetch = function ...`` stub returning a fake ``Response``."""
    ok_js = "true" if ok else "false"
    content_type_js = "null" if content_type is None else json.dumps(content_type)
    return (
        "var fetch = function (url, options) {\n"
        "    return Promise.resolve({\n"
        "        ok: " + ok_js + ",\n"
        "        status: " + str(status) + ",\n"
        "        statusText: " + json.dumps(status_text) + ",\n"
        "        headers: { get: function (name) { return " + content_type_js + "; } },\n"
        "        json: function () { " + json_impl + " }\n"
        "    });\n"
        "};\n"
    )


def _build_context(fetch_impl_js: str) -> quickjs.Context:
    """Create a QuickJS context with stubs, the provided ``fetch``, and api.js."""
    ctx = quickjs.Context()
    ctx.eval(_STUBS)
    ctx.eval(fetch_impl_js)
    ctx.eval(_load_api_source())
    return ctx


def _drain(ctx: quickjs.Context, limit: int = 1000) -> None:
    """Run QuickJS pending jobs until the microtask queue is empty."""
    for _ in range(limit):
        if not ctx.execute_pending_job():
            return
    raise AssertionError("too many pending jobs; possible infinite loop")


def _call(ctx: quickjs.Context, js_expression: str) -> dict:
    """Evaluate ``js_expression``, settle the resulting promise, and return its outcome."""
    ctx.eval(
        "__result = null;\n"
        "try {\n"
        "    var __p = (" + js_expression + ");\n"
        "    Promise.resolve(__p).then(\n"
        "        function (v) { __result = { ok: true, value: v }; },\n"
        "        function (e) { __result = { ok: false, message: e.message, name: e.name }; }\n"
        "    );\n"
        "} catch (e) {\n"
        "    __result = { ok: false, message: e.message, name: e.name };\n"
        "}\n"
    )
    _drain(ctx)
    return json.loads(ctx.eval("JSON.stringify(__result)"))


def _extract_function_body(source: str, signature: str) -> str:
    """Return the body (between the first braces) of ``signature`` in ``source``."""
    start = source.index(signature)
    open_idx = source.index("{", start)
    depth = 0
    for i in range(open_idx, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_idx + 1:i]
    raise AssertionError("unbalanced braces in " + signature)


# --- HTML error pages never leak a JSON parse SyntaxError ----------------------


def test_html_error_page_raises_friendly_error_not_syntax_error():
    fetch_js = _make_fetch(
        ok=False,
        status=502,
        status_text="Bad Gateway",
        content_type="text/html",
        json_impl="return Promise.reject(new SyntaxError('Unexpected token <'));",
    )
    ctx = _build_context(fetch_js)
    result = _call(ctx, "getAgents()")
    assert result["ok"] is False
    assert result["message"] == "GetAgents failed: 502 Bad Gateway"
    assert result["name"] == "Error"


# --- JSON error payloads surface the right field -------------------------------


def test_json_error_payload_uses_error_field():
    fetch_js = _make_fetch(
        ok=False,
        status=500,
        status_text="Internal Server Error",
        content_type="application/json",
        json_impl="return Promise.resolve({ error: 'boom' });",
    )
    ctx = _build_context(fetch_js)
    result = _call(ctx, "getAgents()")
    assert result["ok"] is False
    assert result["message"] == "GetAgents failed: boom"


def test_json_error_payload_falls_back_to_detail():
    fetch_js = _make_fetch(
        ok=False,
        status=400,
        status_text="Bad Request",
        content_type="application/json",
        json_impl="return Promise.resolve({ detail: 'nope' });",
    )
    ctx = _build_context(fetch_js)
    result = _call(ctx, "getAgents()")
    assert result["ok"] is False
    assert result["message"] == "GetAgents failed: nope"


def test_json_error_payload_without_error_or_detail_uses_status():
    fetch_js = _make_fetch(
        ok=False,
        status=404,
        status_text="Not Found",
        content_type="application/json",
        json_impl="return Promise.resolve({});",
    )
    ctx = _build_context(fetch_js)
    result = _call(ctx, "getAgents()")
    assert result["ok"] is False
    assert result["message"] == "GetAgents failed: 404 Not Found"


# --- successful responses ------------------------------------------------


def test_success_json_body_is_returned():
    fetch_js = _make_fetch(
        ok=True,
        status=200,
        status_text="OK",
        content_type="application/json",
        json_impl="return Promise.resolve({ agents: ['a'] });",
    )
    ctx = _build_context(fetch_js)
    result = _call(ctx, "getAgents()")
    assert result["ok"] is True
    assert result["value"] == {"agents": ["a"]}


def test_success_with_non_json_content_type_throws():
    fetch_js = _make_fetch(
        ok=True,
        status=200,
        status_text="OK",
        content_type="text/html",
        json_impl="return Promise.resolve({ anything: 1 });",
    )
    ctx = _build_context(fetch_js)
    result = _call(ctx, "getAgents()")
    assert result["ok"] is False
    assert result["message"] == "GetAgents returned non-JSON response (text/html)"


def test_missing_content_type_reports_unknown():
    fetch_js = _make_fetch(
        ok=True,
        status=200,
        status_text="OK",
        content_type=None,
        json_impl="return Promise.resolve({});",
    )
    ctx = _build_context(fetch_js)
    result = _call(ctx, "getAgents()")
    assert result["ok"] is False
    assert "non-JSON response (unknown)" in result["message"]


# --- POST wrappers (signed and unsigned) route through the same helper ---------


def test_archive_conversation_uses_helper():
    fetch_js = _make_fetch(
        ok=False,
        status=502,
        status_text="Bad Gateway",
        content_type="text/html",
        json_impl="return Promise.reject(new SyntaxError('Unexpected token <'));",
    )
    ctx = _build_context(fetch_js)
    result = _call(ctx, "archiveConversation({})")
    assert result["ok"] is False
    assert result["message"] == "ArchiveConversation failed: 502 Bad Gateway"


def test_add_message_uses_helper():
    fetch_js = _make_fetch(
        ok=False,
        status=502,
        status_text="Bad Gateway",
        content_type="text/html",
        json_impl="return Promise.reject(new SyntaxError('Unexpected token <'));",
    )
    ctx = _build_context(fetch_js)
    result = _call(ctx, "addMessage('wif', {})")
    assert result["ok"] is False
    assert result["message"] == "AddMessage failed: 502 Bad Gateway"


# --- structural invariant: one return of response.json(), inside the helper ----


def test_parse_json_response_is_the_only_place_that_returns_json():
    source = API_JS_PATH.read_text(encoding="utf-8")
    assert source.count("return response.json();") == 1
    body = _extract_function_body(source, "async function parseJsonResponse(response, context)")
    assert "return response.json();" in body

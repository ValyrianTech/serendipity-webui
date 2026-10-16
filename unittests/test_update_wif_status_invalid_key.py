"""Regression tests for the Invalid-key branch in ``updateWifStatus``.

``updateWifStatus(loaded)`` (in ``app/templates/base.html``) derives the
Bitcoin address from ``window.currentWif`` via ``wifToAddress``. When a
malformed WIF is restored from ``sessionStorage``, ``wifToAddress`` returns
``null`` and the original code called ``address.substring(0, 12)`` on it,
throwing a TypeError that aborted ``initializeApp()``.

These tests extract the *real* ``updateWifStatus`` function from the template
(using the same brace-matching extractor as ``test_xss_sanitization.py``) and
execute it inside an embedded QuickJS engine with ``document``, ``window``, and
``wifToAddress`` supplied as stubs, proving the invalid branch renders an
'Invalid key' state and hides the lock button instead of throwing.
"""

import json
import re
from pathlib import Path

import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_HTML_PATH = REPO_ROOT / "app" / "templates" / "base.html"

_VALID_WIF = "KwDidQJHSE67VJ6MWRvbBKAxhD3F48DvqRT6JRqrjd7MHLBjGF7V"
_MALFORMED_WIF = "MALFORMED_WIF"
_VALID_ADDRESS = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"


def _extract_function(source: str, name: str) -> str:
    """Extract the full text of ``function name(...) {...}`` from ``source``.

    Locates the function signature with a regex and then brace-matches to the
    matching closing ``}`` so the function body is captured verbatim (nested
    braces in object literals are handled correctly).
    """
    match = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", source)
    assert match, f"function {name} not found in template source"
    start = match.start()
    depth = 0
    i = match.end() - 1
    while i < len(source):
        char = source[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1
    raise AssertionError(f"could not find closing brace for function {name}")


# Browser stubs injected before evaluating the extracted ``updateWifStatus``.
# ``document.getElementById`` returns fake element objects that record their
# ``innerHTML`` and expose a ``classList`` whose add/remove/contains mutate a
# plain object so class state can be inspected from Python. ``window`` holds the
# in-memory WIF and ``wifToAddress`` returns a real-looking address for the known
# valid WIF and ``null`` for anything else.
_STUBS = """
function __makeElement() {
    var classes = {};
    return {
        innerHTML: '',
        classList: {
            add: function (cls) { classes[cls] = true; },
            remove: function (cls) { delete classes[cls]; },
            contains: function (cls) { return classes[cls] === true; }
        },
        __classes: function () { return classes; }
    };
}
var __status = __makeElement();
var __lockButton = __makeElement();
var document = {
    getElementById: function (id) {
        if (id === 'wif-status') { return __status; }
        if (id === 'lock-button') { return __lockButton; }
        return null;
    }
};
var window = { currentWif: null };
function wifToAddress(wif) {
    if (wif === __VALID_WIF__) { return __VALID_ADDRESS__; }
    return null;
}
"""

# Wrapper that drives the real function and reports whether it threw plus the
# resulting element state, serialized as JSON.
_HARNESS = """
function __run(loaded, wif) {
    var result = { threw: false, message: null };
    try {
        window.currentWif = wif;
        updateWifStatus(loaded);
    } catch (error) {
        result.threw = true;
        result.message = error.message;
    }
    result.statusInnerHTML = __status.innerHTML;
    result.lockClasses = __lockButton.__classes();
    return result;
}
"""


def _build_context() -> quickjs.Context:
    """Create a QuickJS context with stubs and the real updateWifStatus loaded."""
    ctx = quickjs.Context()
    stubs = _STUBS.replace("__VALID_WIF__", json.dumps(_VALID_WIF)).replace(
        "__VALID_ADDRESS__", json.dumps(_VALID_ADDRESS)
    )
    ctx.eval(stubs)
    ctx.eval(_extract_function(BASE_HTML_PATH.read_text(encoding="utf-8"), "updateWifStatus"))
    ctx.eval(_HARNESS)
    return ctx


def _run(ctx: quickjs.Context, loaded: bool, wif: str) -> dict:
    """Run ``updateWifStatus(loaded)`` with ``window.currentWif = wif`` and report the result."""
    payload = ctx.eval("JSON.stringify(__run(" + json.dumps(loaded) + ", " + json.dumps(wif) + "))")
    return json.loads(payload)


def test_invalid_key_branch_does_not_throw_and_hides_lock_button():
    ctx = _build_context()
    result = _run(ctx, True, _MALFORMED_WIF)
    assert result["threw"] is False, result["message"]
    assert "Invalid key" in result["statusInnerHTML"]
    assert "hidden" in result["lockClasses"]


def test_valid_wif_shows_truncated_address_and_shows_lock_button():
    ctx = _build_context()
    result = _run(ctx, True, _VALID_WIF)
    assert result["threw"] is False, result["message"]
    assert _VALID_ADDRESS[:12] in result["statusInnerHTML"]
    assert "hidden" not in result["lockClasses"]


def test_not_loaded_renders_no_key_loaded_and_hides_lock_button():
    ctx = _build_context()
    result = _run(ctx, False, None)
    assert result["threw"] is False, result["message"]
    assert "No key loaded" in result["statusInnerHTML"]
    assert "hidden" in result["lockClasses"]

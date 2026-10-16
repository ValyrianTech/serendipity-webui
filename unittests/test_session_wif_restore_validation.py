"""Regression tests for session-WIF validation in ``initializeApp()``.

``initializeApp()`` (in ``app/templates/base.html``) restores a decrypted WIF
from ``sessionStorage``. A malformed/tampered/legacy session key must never be
accepted verbatim: ``window.currentWif`` must not be set to it and it must not
be left in ``sessionStorage``, otherwise downstream signing/address operations
(``wifToPrivateKey``) would throw and the user would never be re-prompted.

These tests extract the *real* ``initializeApp`` function from the template
(using the same brace-matching extractor as ``test_xss_sanitization.py`` and
``test_update_wif_status_invalid_key.py``) and execute it inside an embedded
QuickJS engine with ``document``, ``window``, storage, and the WIF helpers
supplied as recording stubs. ``wifToAddress`` returns a real-looking address
for the known-good WIF and ``null`` for anything else, mirroring the real
``static/js/bitcoin.js`` behaviour.
"""

import json
import re
from pathlib import Path

import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_HTML_PATH = REPO_ROOT / "app" / "templates" / "base.html"

_VALID_WIF = "KwDidQJHSE67VJ6MWRvbBKAxhD3F48DvqRT6JRqrjd7MHLBjGF7V"
_VALID_ADDRESS = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
_MALFORMED_WIF = "MALFORMED_WIF"


def _extract_function(source: str, name: str) -> str:
    """Extract the full text of ``[async] function name(...) {...}`` from ``source``.

    Locates the function signature with a regex (capturing an optional leading
    ``async `` keyword) and then brace-matches to the matching closing ``}`` so
    the function body is captured verbatim.
    """
    match = re.search(r"(async\s+)?function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", source)
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


# Recording stubs injected before evaluating the extracted ``initializeApp``.
# ``sessionStorage``/``localStorage`` round-trip through plain objects,
# ``document.getElementById`` returns fake elements whose ``classList.remove``
# records which modal was shown, and ``wifToAddress`` returns a real-looking
# address for the known valid WIF and ``null`` for anything else.
_STUBS = """
var __state = {
    updateWifStatusCalls: [],
    showToastCalls: [],
    clearSessionWifCalls: 0,
    modalShown: [],
    threw: false,
    error: null,
};
var window = { currentWif: null };

var __sessionStorageData = {};
var sessionStorage = {
    getItem: function (key) { return (key in __sessionStorageData) ? __sessionStorageData[key] : null; },
    setItem: function (key, value) { __sessionStorageData[key] = value; },
    removeItem: function (key) { delete __sessionStorageData[key]; },
};
var __localStorageData = {};
var localStorage = {
    getItem: function (key) { return (key in __localStorageData) ? __localStorageData[key] : null; },
    setItem: function (key, value) { __localStorageData[key] = value; },
    removeItem: function (key) { delete __localStorageData[key]; },
};
var console = { log: function () {}, error: function () {} };

function __makeElement(name) {
    return {
        name: name,
        classList: {
            add: function () {},
            remove: function () { __state.modalShown.push(name); }
        }
    };
}
var document = { getElementById: function (id) { return __makeElement(id); } };

var __storage = null;
function loadWifStorage() { return __storage; }
function loadSessionWif() { return sessionStorage.getItem('wif_session_key'); }
function clearSessionWif() { __state.clearSessionWifCalls += 1; sessionStorage.removeItem('wif_session_key'); }
function clearWifStorage() { localStorage.removeItem('serendipity_wif'); }
function isLegacyPlaintextWif(storage) { return Boolean(storage && storage.data && storage.encrypted !== true); }
function updateWifStatus(loaded) { __state.updateWifStatusCalls.push(loaded); }
function showToast(message, type) { __state.showToastCalls.push({ message: message, type: type }); }
function updateNavigation() {}
function getSettings() { return {}; }
function loadFolders(agentName) {}
function wifToAddress(wif) {
    if (wif === __VALID_WIF__) { return __VALID_ADDRESS__; }
    return null;
}
"""

# Wrapper that seeds the storages, runs the real ``initializeApp``, and reports
# the resulting state (window.currentWif, sessionStorage, and every recorded
# side effect) as JSON.
_HARNESS = """
function __run(storage, sessionWif) {
    __state.updateWifStatusCalls = [];
    __state.showToastCalls = [];
    __state.clearSessionWifCalls = 0;
    __state.modalShown = [];
    __state.threw = false;
    __state.error = null;
    window.currentWif = null;
    __sessionStorageData = {};
    if (sessionWif !== null) { __sessionStorageData['wif_session_key'] = sessionWif; }
    __storage = storage;

    try {
        initializeApp();
    } catch (error) {
        __state.threw = true;
        __state.error = error.message;
    }

    __state.currentWif = window.currentWif;
    __state.sessionWifAfter = sessionStorage.getItem('wif_session_key');
    return __state;
}
"""


def _build_context() -> quickjs.Context:
    """Create a QuickJS context with stubs and the real ``initializeApp`` loaded."""
    ctx = quickjs.Context()
    stubs = _STUBS.replace("__VALID_WIF__", json.dumps(_VALID_WIF)).replace(
        "__VALID_ADDRESS__", json.dumps(_VALID_ADDRESS)
    )
    ctx.eval(stubs)
    ctx.eval(_extract_function(BASE_HTML_PATH.read_text(encoding="utf-8"), "initializeApp"))
    ctx.eval(_HARNESS)
    return ctx


def _run(ctx: quickjs.Context, storage, session_wif) -> dict:
    """Run ``initializeApp`` with the given localStorage payload and session WIF."""
    payload = ctx.eval(
        "JSON.stringify(__run(" + json.dumps(storage) + ", " + json.dumps(session_wif) + "))"
    )
    return json.loads(payload)


def test_malformed_session_wif_is_discarded_and_falls_back_to_unlock():
    ctx = _build_context()
    result = _run(ctx, {"encrypted": True, "data": "encblob"}, _MALFORMED_WIF)
    assert result["threw"] is False, result["error"]

    assert result["currentWif"] is None
    assert result["sessionWifAfter"] is None
    assert result["clearSessionWifCalls"] == 1
    assert result["updateWifStatusCalls"] == []

    error_toasts = [c for c in result["showToastCalls"] if c["type"] == "error"]
    assert error_toasts, result["showToastCalls"]
    assert any("invalid" in c["message"].lower() for c in error_toasts)

    assert "unlock-modal" in result["modalShown"]


def test_valid_session_wif_is_accepted_without_unlock_prompt():
    ctx = _build_context()
    result = _run(ctx, {"encrypted": True, "data": "encblob"}, _VALID_WIF)
    assert result["threw"] is False, result["error"]

    assert result["currentWif"] == _VALID_WIF
    assert result["sessionWifAfter"] == _VALID_WIF
    assert result["clearSessionWifCalls"] == 0
    assert result["updateWifStatusCalls"] == [True]
    assert "unlock-modal" not in result["modalShown"]
    assert "setup-wif-modal" not in result["modalShown"]


def test_absent_session_wif_falls_back_to_unlock():
    ctx = _build_context()
    result = _run(ctx, {"encrypted": True, "data": "encblob"}, None)
    assert result["threw"] is False, result["error"]

    assert result["currentWif"] is None
    assert result["clearSessionWifCalls"] == 0
    assert result["updateWifStatusCalls"] == []
    assert "unlock-modal" in result["modalShown"]


def test_malformed_session_wif_without_storage_falls_back_to_setup():
    ctx = _build_context()
    result = _run(ctx, None, _MALFORMED_WIF)
    assert result["threw"] is False, result["error"]

    assert result["currentWif"] is None
    assert result["clearSessionWifCalls"] == 1
    assert "setup-wif-modal" in result["modalShown"]
    assert "unlock-modal" not in result["modalShown"]

"""Regression tests for `saveWif()` in ``static/js/bitcoin.js``.

The Serendipity Web UI persists the user's Bitcoin WIF private key to the
browser's ``localStorage``. A plaintext WIF must never reach ``localStorage``:
`saveWif()` is the single choke point that enforces this invariant, refusing to
store a WIF unless both the ``encrypted`` flag and an ``encryptedData`` blob are
supplied.

These tests guard that security invariant against regressions by executing the
*real* ``static/js/bitcoin.js`` source (only its ``import``/``export``
statements are stripped so it can load as a classic script) inside an embedded
QuickJS engine. ``saveWif`` is therefore the actual shipped function, not a
reimplementation, and the tests assert that every rejection path leaves
``localStorage`` untouched.
"""

import json
from pathlib import Path

import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
BITCOIN_JS_PATH = REPO_ROOT / "static" / "js" / "bitcoin.js"

_UNDEFINED = object()

# Browser stubs injected before loading bitcoin.js so its module-level code can
# run. ``localStorage`` records every call into ``__localStorageCalls`` so the
# tests can assert it was never touched on the rejection paths.
_STUBS = """
var __localStorageCalls = [];
var localStorage = {
    getItem: function (key) { __localStorageCalls.push({ method: 'getItem', key: key }); return null; },
    setItem: function (key, value) { __localStorageCalls.push({ method: 'setItem', key: key, value: value }); },
    removeItem: function (key) { __localStorageCalls.push({ method: 'removeItem', key: key }); },
};
var secp256k1 = {};
var sha256 = function () {};
var ripemd160 = function () {};
var window = {};
"""

# Tiny JS harness that invokes the real ``saveWif`` and reports whether it threw
# and which localStorage calls it made, as JSON.
_HARNESS = """
function __runSaveWif(wif, encrypted, encryptedData) {
    __localStorageCalls = [];
    var result = { threw: false, message: null, calls: [] };
    try {
        saveWif(wif, encrypted, encryptedData);
    } catch (error) {
        result.threw = true;
        result.message = error.message;
    }
    result.calls = __localStorageCalls;
    return result;
}
"""


def _strip_module_syntax(source: str) -> str:
    """Turn the ES-module source into a classic script quickjs can ``eval``.

    ``import`` lines are commented out (their bindings are supplied as stubs),
    and the leading ``export`` keyword is dropped from ``export`` declarations
    while the declaration body is preserved verbatim.
    """
    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            lines.append("// " + line)
        elif stripped.startswith("export "):
            lines.append(line.replace("export ", "", 1))
        else:
            lines.append(line)
    return "\n".join(lines)


def _load_bitcoin_source() -> str:
    """Return the real ``bitcoin.js`` source, ready to eval as a classic script."""
    return _strip_module_syntax(BITCOIN_JS_PATH.read_text(encoding="utf-8"))


def _build_context() -> quickjs.Context:
    """Create a QuickJS context with browser stubs and the real bitcoin.js loaded."""
    ctx = quickjs.Context()
    ctx.eval(_STUBS)
    ctx.eval(_load_bitcoin_source())
    ctx.eval(_HARNESS)
    return ctx


def _run_save_wif(ctx: quickjs.Context, wif, encrypted, encrypted_data=_UNDEFINED) -> dict:
    """Call the real ``saveWif`` in the context and return its result as a dict."""
    args = [wif, encrypted] if encrypted_data is _UNDEFINED else [wif, encrypted, encrypted_data]
    payload = ctx.eval("JSON.stringify(__runSaveWif(..." + json.dumps(args) + "))")
    return json.loads(payload)


def test_save_wif_refuses_plaintext_wif():
    ctx = _build_context()
    result = _run_save_wif(ctx, "WIFKEY", False)
    assert result["threw"] is True
    assert "Refusing to store an unencrypted WIF" in result["message"]
    assert result["calls"] == []


def test_save_wif_refuses_null_encrypted_data():
    ctx = _build_context()
    result = _run_save_wif(ctx, "WIFKEY", True, None)
    assert result["threw"] is True
    assert "Refusing to store an unencrypted WIF" in result["message"]
    assert result["calls"] == []


def test_save_wif_refuses_undefined_encrypted_data():
    ctx = _build_context()
    result = _run_save_wif(ctx, "WIFKEY", True)
    assert result["threw"] is True
    assert "Refusing to store an unencrypted WIF" in result["message"]
    assert result["calls"] == []


def test_save_wif_persists_encrypted_wif():
    ctx = _build_context()
    result = _run_save_wif(ctx, None, True, "encblob")
    assert result["threw"] is False

    set_calls = [call for call in result["calls"] if call["method"] == "setItem"]
    assert len(set_calls) == 1

    call = set_calls[0]
    assert call["key"] == "serendipity_wif"

    payload = json.loads(call["value"])
    assert payload["encrypted"] is True
    assert payload["data"] == "encblob"

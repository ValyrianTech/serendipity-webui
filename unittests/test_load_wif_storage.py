"""Regression tests for ``loadWifStorage()`` resilience to corrupted storage.

``loadWifStorage()`` in ``static/js/bitcoin.js`` previously called
``JSON.parse(stored)`` directly, so a malformed (or non-object) value left in
``localStorage`` under ``serendipity_wif`` raised an uncaught ``SyntaxError``
during page bootstrap and aborted initialization.

It now returns ``null`` when nothing is stored, parses inside a ``try``/``catch``,
treats any non-null non-object value as corrupted, warns via ``console.warn``,
clears the bad entry with ``localStorage.removeItem(STORAGE_KEY)`` so the user
can recover, and returns ``null``.

These tests execute the *real* ``static/js/bitcoin.js`` source through QuickJS
(the same harness pattern as ``test_legacy_wif_migration.py``) with a
controllable ``localStorage`` stub, so ``loadWifStorage`` is the actual shipped
function, not a reimplementation.
"""

import json
from pathlib import Path

import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
BITCOIN_JS_PATH = REPO_ROOT / "static" / "js" / "bitcoin.js"

_STORAGE_KEY = "serendipity_wif"

# Browser stubs injected before loading bitcoin.js so its module-level code can
# run. The localStorage stub is backed by a mutable ``__store`` object that the
# tests drive through ``__setStoredRaw``/``__clearStored``, and ``console.warn``
# is captured into ``__warns``.
_STUBS = """
var __store = {};
var localStorage = {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(__store, k) ? __store[k] : null; },
    setItem: function (k, v) { __store[k] = String(v); },
    removeItem: function (k) { delete __store[k]; },
};
var __warns = [];
var console = { warn: function () { __warns.push(Array.prototype.slice.call(arguments).join(' ')); } };
var secp256k1 = {};
var sha256 = function () {};
var ripemd160 = function () {};
var window = {};
"""

# JS harness that seeds the store, invokes the real ``loadWifStorage``, and
# reports a JSON-able result object capturing the returned value (serialized,
# with a flag for null), whether the entry survived, and any console warnings.
_HARNESS = """
function __setStoredRaw(text) {
    __store['serendipity_wif'] = text;
}

function __clearStored() {
    delete __store['serendipity_wif'];
}

function __runLoad() {
    var result = { threw: false, message: null, value: null, valueIsNull: false, stillStored: false, warns: [] };
    try {
        var v = loadWifStorage();
        result.valueIsNull = (v === null);
        if (v !== null) {
            result.value = JSON.stringify(v);
        }
    } catch (error) {
        result.threw = true;
        result.message = error.message;
    }
    result.stillStored = localStorage.getItem('serendipity_wif') !== null;
    result.warns = __warns.slice();
    return result;
}
"""


def _strip_module_syntax(source: str) -> str:
    """Turn the ES-module source into a classic script quickjs can ``eval``."""
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


def _build_context() -> quickjs.Context:
    """Create a QuickJS context with browser stubs and the real bitcoin.js loaded."""
    ctx = quickjs.Context()
    ctx.eval(_STUBS)
    ctx.eval(_strip_module_syntax(BITCOIN_JS_PATH.read_text(encoding="utf-8")))
    ctx.eval(_HARNESS)
    return ctx


def _set_stored_raw(ctx: quickjs.Context, text: str) -> None:
    ctx.eval("__setStoredRaw(" + json.dumps(text) + ")")


def _clear_stored(ctx: quickjs.Context) -> None:
    ctx.eval("__clearStored()")


def _run_load(ctx: quickjs.Context) -> dict:
    payload = ctx.eval("JSON.stringify(__runLoad())")
    return json.loads(payload)


def test_no_stored_value_returns_null():
    ctx = _build_context()
    result = _run_load(ctx)
    assert result["threw"] is False
    assert result["valueIsNull"] is True
    assert result["warns"] == []
    assert result["stillStored"] is False


def test_valid_object_returned_as_is():
    ctx = _build_context()
    _set_stored_raw(
        ctx,
        '{"encrypted": true, "data": "abc", "timestamp": "2026-01-01T00:00:00.000Z"}',
    )
    result = _run_load(ctx)
    assert result["threw"] is False
    assert result["valueIsNull"] is False
    value = json.loads(result["value"])
    assert value["encrypted"] is True
    assert value["data"] == "abc"
    assert value["timestamp"] == "2026-01-01T00:00:00.000Z"
    assert result["warns"] == []
    assert result["stillStored"] is True


def test_malformed_json_returns_null_warns_and_removes_entry():
    ctx = _build_context()
    _set_stored_raw(ctx, "{not json")
    result = _run_load(ctx)
    assert result["threw"] is False
    assert result["valueIsNull"] is True
    assert len(result["warns"]) == 1
    assert "Discarding corrupted WIF storage entry" in result["warns"][0]
    assert result["stillStored"] is False


def test_json_null_returns_null_warns_and_removes_entry():
    ctx = _build_context()
    _set_stored_raw(ctx, "null")
    result = _run_load(ctx)
    assert result["threw"] is False
    assert result["valueIsNull"] is True
    assert len(result["warns"]) == 1
    assert "Discarding corrupted WIF storage entry" in result["warns"][0]
    assert result["stillStored"] is False


def test_json_number_primitive_returns_null_warns_and_removes_entry():
    ctx = _build_context()
    _set_stored_raw(ctx, "42")
    result = _run_load(ctx)
    assert result["threw"] is False
    assert result["valueIsNull"] is True
    assert len(result["warns"]) == 1
    assert "Discarding corrupted WIF storage entry" in result["warns"][0]
    assert result["stillStored"] is False


def test_json_string_primitive_returns_null_warns_and_removes_entry():
    ctx = _build_context()
    _set_stored_raw(ctx, '"hello"')
    result = _run_load(ctx)
    assert result["threw"] is False
    assert result["valueIsNull"] is True
    assert len(result["warns"]) == 1
    assert "Discarding corrupted WIF storage entry" in result["warns"][0]
    assert result["stillStored"] is False


def test_json_boolean_primitive_returns_null_warns_and_removes_entry():
    ctx = _build_context()
    _set_stored_raw(ctx, "true")
    result = _run_load(ctx)
    assert result["threw"] is False
    assert result["valueIsNull"] is True
    assert len(result["warns"]) == 1
    assert "Discarding corrupted WIF storage entry" in result["warns"][0]
    assert result["stillStored"] is False


def test_source_guards_json_parse_with_try_catch():
    source = BITCOIN_JS_PATH.read_text(encoding="utf-8")
    assert "return JSON.parse(stored);" not in source
    assert "console.warn('Discarding corrupted WIF storage entry:', e.message)" in source
    assert "localStorage.removeItem(STORAGE_KEY)" in source

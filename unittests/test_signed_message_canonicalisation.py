"""Golden-vector tests for the JS signing canonicalisation.

``jsonSortedEncode()`` in ``static/js/bitcoin.js`` is the JS-side canonical
JSON serializer used to build the message that ``signData()`` hashes and signs.
The backend signs over Python ``json.dumps(obj, sort_keys=True, indent=2,
ensure_ascii=False)``, so the JS output must be BYTE-IDENTICAL to that Python
output. Any drift (number formatting, key ordering, or string escaping) makes
the two sides hash different bytes and the signature is rejected.

These tests execute the *real* ``static/js/bitcoin.js`` source inside an
embedded QuickJS engine (as in ``test_wif_signing_propagation.py``) and compare
``jsonSortedEncode`` byte-for-byte against Python's ``json.dumps`` for a set of
golden fixtures, plus a full ``signData`` hash golden test and source-inspection
guards that lock in the canonicalisation contract.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest
import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
BITCOIN_JS_PATH = REPO_ROOT / "static" / "js" / "bitcoin.js"
SHA256_JS_PATH = REPO_ROOT / "static" / "js" / "vendor" / "sha256.js"


# Browser stubs injected before loading bitcoin.js. ``secp256k1`` is a
# deterministic stub whose ``sign`` returns a fixed 64-byte compact signature so
# ``signMessage``/``signData`` complete without real cryptography; ``ripemd160``
# is a fixed-length stub. The real ``sha256`` is loaded from the vendored module.
# ``window.crypto.getRandomValues`` is deterministic so ``generateWifKey()``
# produces a stable, valid WIF. ``TextEncoder`` is a UTF-8-correct stub (the
# one in ``test_wif_signing_propagation.py`` truncates to ``charCodeAt & 0xff``,
# which corrupts non-ASCII) so hashing non-ASCII strings matches Python UTF-8.
_STUBS = """
var console = { log: function () {}, error: function () {} };
var secp256k1 = {
    getPublicKey: function (pk, compressed) {
        var out = new Uint8Array(compressed ? 33 : 65);
        out[0] = compressed ? 2 : 4;
        for (var i = 1; i < out.length; i++) {
            out[i] = pk[(i - 1) % pk.length];
        }
        return out;
    },
    sign: function (msgHash, privateKey, opts) {
        var r = new Uint8Array(64);
        for (var i = 0; i < 64; i++) {
            r[i] = (msgHash[i % msgHash.length] + privateKey[i % privateKey.length]) & 0xff;
        }
        return { recovery: 0, toCompactRawBytes: function () { return r; } };
    }
};
var ripemd160 = function () { return new Uint8Array(20); };
var window = {
    crypto: {
        getRandomValues: function (arr) {
            for (var i = 0; i < arr.length; i++) { arr[i] = (i * 7 + 1) & 0xff; }
            return arr;
        }
    }
};
var localStorage = {
    getItem: function () { return null; },
    setItem: function () {},
    removeItem: function () {}
};
var sessionStorage = {
    getItem: function () { return null; },
    setItem: function () {},
    removeItem: function () {}
};
var btoa = function (s) { return "stub"; };
var atob = function (s) { return "stub"; };
var TextEncoder = function () {
    this.encode = function (str) {
        var encoded = encodeURIComponent(str);
        var bytes = [];
        for (var i = 0; i < encoded.length; i++) {
            if (encoded[i] === '%') {
                bytes.push(parseInt(encoded.slice(i + 1, i + 3), 16));
                i += 2;
            } else {
                bytes.push(encoded.charCodeAt(i));
            }
        }
        return new Uint8Array(bytes);
    };
};
"""

# Harness that calls the real functions and reports their outcome as JSON.
_HARNESS = """
var __signDataResult = null;

function __runSignData(data, wif) {
    __signDataResult = { status: 'pending', message: null, data: null, error: null };
    var p = signData(data, wif);
    p.then(
        function (v) { __signDataResult = { status: 'fulfilled', message: v.message, data: v.data, error: null }; },
        function (e) { __signDataResult = { status: 'rejected', message: null, data: null, error: e.message }; }
    );
}

function __generateWif() {
    return generateWifKey();
}

function __tryEncode(expr) {
    try {
        return 'ok:' + jsonSortedEncode(expr);
    } catch (e) {
        return 'err:' + e.message;
    }
}
"""

_EXPORT_BLOCK_RE = re.compile(r"export\{([^}]*)\};")


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


def _load_sha256_source() -> str:
    """Return the vendored sha256.js as a classic script exposing ``sha256``."""
    source = SHA256_JS_PATH.read_text(encoding="utf-8")
    source = source.replace("export default null;", "")
    match = _EXPORT_BLOCK_RE.search(source)
    assert match is not None, "expected an export block in sha256.js"
    declarations = []
    for part in match.group(1).split(","):
        local, exported = part.split(" as ")
        declarations.append("var " + exported.strip() + " = " + local.strip() + ";")
    source = source[: match.start()] + "\n".join(declarations) + source[match.end():]
    return "\n".join(
        ("// " + line if line.strip().startswith("import ") else line)
        for line in source.splitlines()
    )


def _build_context() -> quickjs.Context:
    """Create a QuickJS context with stubs, the real sha256, and bitcoin.js."""
    ctx = quickjs.Context()
    ctx.eval(_load_sha256_source())
    ctx.eval(_STUBS)
    ctx.eval(_strip_module_syntax(BITCOIN_JS_PATH.read_text(encoding="utf-8")))
    ctx.eval(_HARNESS)
    return ctx


def _drain_jobs(ctx: quickjs.Context, limit: int = 1000) -> None:
    """Run QuickJS pending jobs until the microtask queue is empty."""
    for _ in range(limit):
        if not ctx.execute_pending_job():
            return
    raise AssertionError("too many pending jobs; possible infinite loop")


def _js_encode(ctx: quickjs.Context, fixture) -> str:
    """Return ``jsonSortedEncode(fixture)`` evaluated in the QuickJS context."""
    return ctx.eval("jsonSortedEncode(" + json.dumps(fixture) + ")")


def _py_encode(fixture) -> str:
    """Return Python's canonical serialisation of ``fixture``."""
    return json.dumps(fixture, sort_keys=True, indent=2, ensure_ascii=False)


# Golden fixtures. Each is a JSON-compatible object/array/string/scalar that the
# JS serializer must match byte-for-byte. Integral floats (e.g. ``1.0``) are
# deliberately avoided in these fixtures because JavaScript cannot distinguish
# ``1`` from ``1.0`` and an integral value renders as a bare integer (see the
# dedicated tests below).
GOLDEN_FIXTURES = [
    # Simple nested dict / list.
    {
        "z": [1, 2, {"x": 3}],
        "a": {"nested": {"k": "v"}},
        "m": ["beta", "alpha"],
    },
    # Non-integral floats exercise CPython float repr.
    {"a": 0.7, "b": 3.14, "c": -1.5},
    {"d": 0.1 + 0.2},
    # Integers (safe range) are printed without a decimal point.
    {"int_zero": 0, "int_pos": 123, "int_neg": -42, "large_int": 9007199254740991},
    # Non-ASCII strings (Latin-1, CJK, astral emoji) emitted raw.
    {"greeting": "héllo 日本語 😀"},
    # Control characters, quotes and backslashes escape exactly like Python.
    {"controls": "\n\t\u0001\u001f\"quoted\" \\ backslash"},
    # Nested empty object / array stay compact.
    {"empty_obj": {}, "empty_arr": [], "nested": {"deep": []}},
    # Booleans and null.
    {"t": True, "f": False, "n": None},
    # Code-point key ordering differs from UTF-16 ordering: Python sorts
    # '\uffff' (0xFFFF) before '\U00010000' (0x10000), while a UTF-16 code-unit
    # sort would place the astral key's leading high surrogate (0xD800) first.
    {"\uffff": 1, "\U00010000": 2},
    # Deeper nesting with arrays of objects.
    {"items": [{"c": 1}, {"a": 2}, {"b": {"z": None}}]},
    # Top-level scalar (not an object/array).
]


@pytest.mark.parametrize("fixture", GOLDEN_FIXTURES)
def test_json_sorted_encode_matches_python(fixture):
    ctx = _build_context()
    js_output = _js_encode(ctx, fixture)
    assert js_output == _py_encode(fixture)


def test_top_level_scalar_matches_python():
    ctx = _build_context()
    for scalar in ["just a string", 0.7, -1.5, 123, True, False, None, "é\n"]:
        assert _js_encode(ctx, scalar) == _py_encode(scalar)


def test_top_level_array_matches_python():
    ctx = _build_context()
    fixture = [{"b": 1, "a": 2}, 0.7, "x\n", [None, True, False], []]
    assert _js_encode(ctx, fixture) == _py_encode(fixture)


def test_json_sorted_encode_omits_undefined_object_values():
    ctx = _build_context()
    # ``JSON.stringify`` drops keys whose value is ``undefined``; the encoder
    # must do the same so the signed bytes match the POSTed body (the backend
    # hashes the data as received).
    assert ctx.eval("jsonSortedEncode({a: 1, b: undefined})") == _py_encode({"a": 1})
    assert ctx.eval("jsonSortedEncode({b: undefined})") == "{}"


def test_json_sorted_encode_renders_undefined_array_elements_as_null():
    ctx = _build_context()
    # ``JSON.stringify`` renders ``undefined`` inside an array as ``null``.
    assert ctx.eval("jsonSortedEncode([1, undefined])") == _py_encode([1, None])


def test_sign_data_omits_undefined_fields():
    ctx = _build_context()
    wif = ctx.eval("__generateWif()")
    expected_message = "/sha256/" + hashlib.sha256(
        _py_encode({"a": 1}).encode("utf-8")
    ).hexdigest()

    ctx.eval("__runSignData({a: 1, b: undefined}, " + json.dumps(wif) + ")")
    _drain_jobs(ctx)
    result = json.loads(ctx.eval("JSON.stringify(__signDataResult)"))

    assert result["status"] == "fulfilled", result.get("error")
    assert result["message"] == expected_message


# The signData hash golden test: a representative apiData-like payload. The
# signed ``message`` must be ``/sha256/<hex>`` of Python's UTF-8-encoded
# canonical serialisation.
_SIGN_FIXTURE = {
    "conversation_id": "conv-123",
    "sender": "user",
    "respond_as": "assistant",
    "content": "hello \u00e9\u65e5\u672c\u8a9e \U0001f600\n",
    "agent": "Serendipity",
    "folder_name": "default",
    "memory": 0,
    "temperature": 0.7,
    "thinking_level": 2,
    "model_name": "gpt-4",
    "run": True,
    "files": [],
}


def test_sign_data_message_matches_python_sha256():
    ctx = _build_context()
    wif = ctx.eval("__generateWif()")

    expected_digest = hashlib.sha256(
        _py_encode(_SIGN_FIXTURE).encode("utf-8")
    ).hexdigest()
    expected_message = "/sha256/" + expected_digest

    ctx.eval("__runSignData(" + json.dumps(_SIGN_FIXTURE) + ", " + json.dumps(wif) + ")")
    _drain_jobs(ctx)
    result = json.loads(ctx.eval("JSON.stringify(__signDataResult)"))

    assert result["status"] == "fulfilled", result.get("error")
    assert result["message"] == expected_message


# --- integral values are signed exactly as they are sent -----------------------


def test_json_sorted_encode_integral_values_render_bare():
    ctx = _build_context()
    # JS has no int/float distinction, so an integral value renders as a bare
    # integer — which is exactly what the request body carries, so the backend
    # hashes the same bytes.
    assert ctx.eval("jsonSortedEncode(1)") == "1"
    assert ctx.eval("jsonSortedEncode(0)") == "0"
    assert ctx.eval("jsonSortedEncode(1.0)") == "1"


def test_json_sorted_encode_rejects_non_finite_numbers():
    ctx = _build_context()
    for expr in ["NaN", "Infinity", "-Infinity"]:
        result = ctx.eval("__tryEncode(" + expr + ")")
        assert result.startswith("err:"), result
        assert "non-finite" in result


def test_sign_data_integral_temperature_matches_python_int():
    ctx = _build_context()
    wif = ctx.eval("__generateWif()")
    fixture = dict(_SIGN_FIXTURE)
    fixture["temperature"] = 1
    expected_message = "/sha256/" + hashlib.sha256(
        _py_encode(fixture).encode("utf-8")
    ).hexdigest()

    ctx.eval("__runSignData(" + json.dumps(fixture) + ", " + json.dumps(wif) + ")")
    _drain_jobs(ctx)
    result = json.loads(ctx.eval("JSON.stringify(__signDataResult)"))

    assert result["status"] == "fulfilled", result.get("error")
    assert result["message"] == expected_message


def test_sign_data_returns_the_signed_data_unchanged():
    ctx = _build_context()
    wif = ctx.eval("__generateWif()")

    ctx.eval("__runSignData(" + json.dumps(_SIGN_FIXTURE) + ", " + json.dumps(wif) + ")")
    _drain_jobs(ctx)
    result = json.loads(ctx.eval("JSON.stringify(__signDataResult)"))

    assert result["status"] == "fulfilled", result.get("error")
    # ``signData`` must return the payload it signed, so the POSTed body is
    # byte-identical to the hashed bytes.
    assert result["data"] == _SIGN_FIXTURE


# --- the canonicalisation contract is documented and structurally enforced ---


def test_canonicalisation_contract_documented_in_source():
    source = BITCOIN_JS_PATH.read_text(encoding="utf-8")
    assert "sort_keys=True" in source
    assert "ensure_ascii=False" in source
    assert "code point" in source


def test_json_sorted_encode_uses_custom_encoder_not_raw_json_stringify():
    source = BITCOIN_JS_PATH.read_text(encoding="utf-8")
    # The old implementation was a single JSON.stringify over a sorted object.
    assert "JSON.stringify(sortObject" not in source
    # A custom encoder is present (CPython float repr via toExponential).
    assert "toExponential" in source
    assert "function jsonSortedEncode" in source

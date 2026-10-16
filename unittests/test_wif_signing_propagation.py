"""Regression tests for WIF validation error propagation through signing.

``wifToPrivateKey()`` in ``static/js/bitcoin.js`` now *throws* on malformed WIF
input (``Invalid WIF: bad length`` / ``bad checksum`` / ``unsupported version
byte`` / ``bad compression flag``). ``signMessage()`` and ``signData()`` call it
directly and do **not** wrap the call in ``try``/``catch``, so a malformed WIF
now rejects the returned promise and the exception propagates up through the
~30 signing helpers in ``static/js/api.js`` (which import ``signData`` from
``./bitcoin.js``) instead of silently signing against a wrong/truncated key.

These tests execute the *real* ``static/js/bitcoin.js`` source (only its
``import``/``export`` statements are stripped so it can load as a classic
script) inside an embedded QuickJS engine, together with the *real* vendored
SHA-256 implementation so the Base58Check checksum path is exercised for real.
The rejection behavior of ``signMessage``/``signData`` is asserted by draining
QuickJS's pending-job (microtask) queue, and the "no try/catch" invariant is
additionally locked in via source inspection.
"""

import json
import re
from pathlib import Path

import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
BITCOIN_JS_PATH = REPO_ROOT / "static" / "js" / "bitcoin.js"
SHA256_JS_PATH = REPO_ROOT / "static" / "js" / "vendor" / "sha256.js"

# A short, valid-base58 WIF whose decoded payload is nowhere near 37/38 bytes,
# so ``wifToPrivateKey`` throws ``Invalid WIF: bad length``.
MALFORMED_WIF_BAD_LENGTH = "abc"
# 52 base58 chars (decodes to 38 bytes) but one char flipped so the Base58Check
# checksum no longer matches -> ``Invalid WIF: bad checksum``.
MALFORMED_WIF_BAD_CHECKSUM = "KwDid2JHSE67VJ6MWRvbBKAxhD3F48DvqRT6JRqrjd7MHLBjGF7V"


# Browser stubs injected before loading bitcoin.js so its module-level code can
# run. ``secp256k1`` is a deterministic stub (its signing output is never
# reached for a malformed WIF); ``ripemd160`` is a fixed-length stub. The real
# ``sha256`` is loaded from the vendored module. ``window.crypto.getRandomValues``
# is deterministic so ``generateWifKey()`` produces a stable, valid WIF.
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
var TextEncoder = function () {
    this.encode = function (str) {
        var bytes = new Uint8Array(str.length);
        for (var i = 0; i < str.length; i++) { bytes[i] = str.charCodeAt(i) & 0xff; }
        return bytes;
    };
};
"""

# Harnesses that call the real functions and report their outcome as JSON.
# ``__runSignMessage``/``__runSignData`` attach ``then``/``catch`` handlers to
# the returned promise and record the settlement into ``__signResult``; the test
# drains QuickJS's pending-job queue before reading it back.
_HARNESS = """
var __signResult = null;

function __runSignMessage(message, wif) {
    __signResult = { status: 'pending', threw: false, message: null, value: null };
    var p = signMessage(message, wif);
    p.then(
        function (v) { __signResult = { status: 'fulfilled', threw: false, message: null, value: v }; },
        function (e) { __signResult = { status: 'rejected', threw: true, message: e.message, value: null }; }
    );
    return __signResult;
}

function __runSignData(data, wif) {
    __signResult = { status: 'pending', threw: false, message: null, value: null };
    var p = signData(data, wif);
    p.then(
        function (v) { __signResult = { status: 'fulfilled', threw: false, message: null, value: v }; },
        function (e) { __signResult = { status: 'rejected', threw: true, message: e.message, value: null }; }
    );
    return __signResult;
}

function __tryPrivateKey(wif) {
    var result = { threw: false, message: null, length: null };
    try {
        var key = wifToPrivateKey(wif);
        result.length = key.length;
    } catch (error) {
        result.threw = true;
        result.message = error.message;
    }
    return result;
}

function __generateWif() {
    return generateWifKey();
}
"""

_EXPORT_BLOCK_RE = re.compile(r"export\{([^}]*)\};")


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


def _load_sha256_source() -> str:
    """Return the vendored sha256.js as a classic script exposing ``sha256``.

    The bundle is minified and uses a trailing ``export{a as sha224,b as
    sha256};`` plus ``export default null;``. Those are rewritten into plain
    ``var`` declarations so the function bindings become globals.
    """
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


def _run_sign_message(ctx: quickjs.Context, message, wif) -> dict:
    ctx.eval("__runSignMessage(" + json.dumps(message) + ", " + json.dumps(wif) + ")")
    _drain_jobs(ctx)
    return json.loads(ctx.eval("JSON.stringify(__signResult)"))


def _run_sign_data(ctx: quickjs.Context, data, wif) -> dict:
    ctx.eval("__runSignData(" + json.dumps(data) + ", " + json.dumps(wif) + ")")
    _drain_jobs(ctx)
    return json.loads(ctx.eval("JSON.stringify(__signResult)"))


def _run_private_key(ctx: quickjs.Context, wif: str) -> dict:
    payload = ctx.eval("JSON.stringify(__tryPrivateKey(" + json.dumps(wif) + "))")
    return json.loads(payload)


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
                return source[open_idx + 1: i]
    raise AssertionError("unbalanced braces in " + signature)


# --- signMessage rejects malformed WIFs instead of signing a wrong key ---------


def test_sign_message_rejects_malformed_wif():
    ctx = _build_context()
    result = _run_sign_message(ctx, "hello", MALFORMED_WIF_BAD_LENGTH)
    assert result["status"] == "rejected"
    assert result["message"] == "Invalid WIF: bad length"


def test_sign_message_rejects_bad_checksum_wif():
    ctx = _build_context()
    result = _run_sign_message(ctx, "hello", MALFORMED_WIF_BAD_CHECKSUM)
    assert result["status"] == "rejected"
    assert result["message"] == "Invalid WIF: bad checksum"


# --- signData propagates the signMessage rejection to its callers ---------------


def test_sign_data_rejects_malformed_wif():
    ctx = _build_context()
    result = _run_sign_data(ctx, {"a": 1}, MALFORMED_WIF_BAD_LENGTH)
    assert result["status"] == "rejected"
    assert result["message"] == "Invalid WIF: bad length"


def test_sign_data_rejects_bad_checksum_wif():
    ctx = _build_context()
    result = _run_sign_data(ctx, {"a": 1}, MALFORMED_WIF_BAD_CHECKSUM)
    assert result["status"] == "rejected"
    assert result["message"] == "Invalid WIF: bad checksum"


# --- sanity: a valid WIF from generateWifKey still parses cleanly ---------------


def test_generate_wif_key_produces_valid_wif():
    ctx = _build_context()
    wif = ctx.eval("__generateWif()")
    result = _run_private_key(ctx, wif)
    assert result["threw"] is False, result["message"]
    assert result["length"] == 32


# --- the propagation is structural: no try/catch swallows the exception --------


def test_sign_message_does_not_catch_wif_to_private_key():
    source = BITCOIN_JS_PATH.read_text(encoding="utf-8")
    body = _extract_function_body(source, "async function signMessage(message, wif)")
    assert "wifToPrivateKey(wif)" in body
    assert "try" not in body
    assert "catch" not in body


def test_sign_data_does_not_catch_sign_message_or_wif_to_address():
    source = BITCOIN_JS_PATH.read_text(encoding="utf-8")
    body = _extract_function_body(source, "async function signData(messageData, wif)")
    assert "signMessage(message, wif)" in body
    assert "wifToAddress(wif)" in body
    assert "try" not in body
    assert "catch" not in body

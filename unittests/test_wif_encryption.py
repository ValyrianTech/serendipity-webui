"""Regression tests for the pure-JS WIF encryption in ``static/js/bitcoin.js``.

The WIF-at-rest encryption must work in *non-secure* contexts (plain HTTP on a
LAN address), where the Web Crypto ``crypto.subtle`` API is unavailable. The
implementation therefore uses pure-JS PBKDF2-HMAC-SHA256 + ChaCha20 +
HMAC-SHA256 (encrypt-then-MAC).

These tests execute the *real* ``static/js/bitcoin.js`` source (only its
``import``/``export`` statements are stripped so it can load as a classic
script) inside an embedded QuickJS engine, together with the *real* vendored
SHA-256 implementation. The PBKDF2 primitive is cross-checked against Python's
``hashlib.pbkdf2_hmac`` and ChaCha20 against the RFC 8439 test vector, and
``encryptWif``/``decryptWif`` are exercised for round-trip, wrong-password, and
tamper-detection behaviour with ``crypto.subtle`` deliberately absent.
"""

import base64
import hashlib
import json
import re
from pathlib import Path

import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
BITCOIN_JS_PATH = REPO_ROOT / "static" / "js" / "bitcoin.js"
SHA256_JS_PATH = REPO_ROOT / "static" / "js" / "vendor" / "sha256.js"

_VALID_WIF = "KwDidQJHSE67VJ6MWRvbBKAxhD3F48DvqRT6JRqrjd7MHLBjGF7V"

# RFC 8439 section 2.3.2 ChaCha20 test vector (counter = 1).
_CHACHA_KEY = bytes(range(32))
_CHACHA_NONCE = bytes.fromhex("000000090000004a00000000")
_CHACHA_COUNTER = 1
_CHACHA_KEYSTREAM = bytes.fromhex(
    "10f1e7e4d13b5915500fdd1fa32071c4"
    "c7d1f4c733c068030422aa9ac3d46c4e"
    "d2826446079faa0914c2d705d98b02a2"
    "b5129cd1de164eb9cbd083e8a2503c4e"
)

# Browser stubs injected before loading bitcoin.js so its module-level code can
# run. ``window.crypto`` deliberately exposes only ``getRandomValues`` (which is
# available in non-secure contexts) and *no* ``subtle``, reproducing the plain
# HTTP on a LAN address environment. ``TextEncoder``/``TextDecoder``/``btoa``/
# ``atob`` are supplied because QuickJS does not provide them.
_STUBS = """
var console = { log: function () {}, error: function () {} };
var secp256k1 = { etc: {} };
var ripemd160 = function () { return new Uint8Array(20); };

var __randomCounter = 0;
var window = {
    crypto: {
        getRandomValues: function (arr) {
            for (var i = 0; i < arr.length; i++) { arr[i] = (__randomCounter = (__randomCounter * 31 + 7) & 0xff); }
            return arr;
        }
    }
};
var localStorage = { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {} };
var sessionStorage = { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {} };

var TextEncoder = function () {
    this.encode = function (str) {
        var bytes = [];
        for (var i = 0; i < str.length; i++) {
            var code = str.charCodeAt(i);
            if (code < 0x80) { bytes.push(code); }
            else if (code < 0x800) { bytes.push(0xc0 | (code >> 6), 0x80 | (code & 0x3f)); }
            else { bytes.push(0xe0 | (code >> 12), 0x80 | ((code >> 6) & 0x3f), 0x80 | (code & 0x3f)); }
        }
        return new Uint8Array(bytes);
    };
};
var TextDecoder = function () {
    this.decode = function (bytes) {
        var str = '';
        for (var i = 0; i < bytes.length; i++) { str += String.fromCharCode(bytes[i]); }
        return str;
    };
};

var __b64chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
if (typeof btoa !== 'function') {
    var btoa = function (binary) {
        var result = '';
        for (var i = 0; i < binary.length; i += 3) {
            var b0 = binary.charCodeAt(i);
            var b1 = i + 1 < binary.length ? binary.charCodeAt(i + 1) : -1;
            var b2 = i + 2 < binary.length ? binary.charCodeAt(i + 2) : -1;
            result += __b64chars.charAt(b0 >> 2);
            result += __b64chars.charAt(((b0 & 3) << 4) | (b1 < 0 ? 0 : b1 >> 4));
            result += b1 < 0 ? '=' : __b64chars.charAt(((b1 & 15) << 2) | (b2 < 0 ? 0 : b2 >> 6));
            result += b2 < 0 ? '=' : __b64chars.charAt(b2 & 63);
        }
        return result;
    };
}
if (typeof atob !== 'function') {
    var atob = function (base64) {
        var lookup = {};
        for (var i = 0; i < __b64chars.length; i++) { lookup[__b64chars.charAt(i)] = i; }
        var clean = base64.replace(/[^A-Za-z0-9+/]/g, '');
        var result = '';
        for (var j = 0; j < clean.length; j += 4) {
            var c0 = lookup[clean.charAt(j)];
            var c1 = lookup[clean.charAt(j + 1)];
            var c2 = lookup[clean.charAt(j + 2)];
            var c3 = lookup[clean.charAt(j + 3)];
            result += String.fromCharCode((c0 << 2) | (c1 >> 4));
            if (clean.charAt(j + 2) !== '') { result += String.fromCharCode(((c1 & 15) << 4) | (c2 >> 2)); }
            if (clean.charAt(j + 3) !== '') { result += String.fromCharCode(((c2 & 3) << 6) | c3); }
        }
        return result;
    };
}
"""

# Harnesses that call the real functions and report their outcome as JSON. The
# async ``encryptWif``/``decryptWif`` results are captured by attaching
# ``then``/``catch`` handlers and draining QuickJS's pending-job queue.
_HARNESS = """
var __asyncResult = null;

function __hexToBytes(hex) {
    var bytes = new Uint8Array(hex.length / 2);
    for (var i = 0; i < bytes.length; i++) { bytes[i] = parseInt(hex.substr(i * 2, 2), 16); }
    return bytes;
}

function __bytesToHex(bytes) {
    var hex = '';
    for (var i = 0; i < bytes.length; i++) { hex += ('0' + bytes[i].toString(16)).slice(-2); }
    return hex;
}

function __runPbkdf2(password, saltHex, iterations, keyLength) {
    var salt = __hexToBytes(saltHex);
    return __bytesToHex(pbkdf2Sha256(new TextEncoder().encode(password), salt, iterations, keyLength));
}

function __runChacha20(keyHex, nonceHex, counter, length) {
    var data = new Uint8Array(length);
    return __bytesToHex(chacha20Xor(__hexToBytes(keyHex), __hexToBytes(nonceHex), counter, data));
}

function __runEncrypt(wif, password, iterations) {
    __asyncResult = { status: 'pending', threw: false, message: null, value: null };
    encryptWif(wif, password, iterations).then(
        function (v) { __asyncResult = { status: 'fulfilled', threw: false, message: null, value: v }; },
        function (e) { __asyncResult = { status: 'rejected', threw: true, message: e.message, value: null }; }
    );
    return __asyncResult;
}

function __runDecrypt(blob, password) {
    __asyncResult = { status: 'pending', threw: false, message: null, value: null };
    decryptWif(blob, password).then(
        function (v) { __asyncResult = { status: 'fulfilled', threw: false, message: null, value: v }; },
        function (e) { __asyncResult = { status: 'rejected', threw: true, message: e.message, value: null }; }
    );
    return __asyncResult;
}

function __runRoundTrip(wif, password, iterations) {
    __asyncResult = { status: 'pending', threw: false, message: null, value: null };
    encryptWif(wif, password, iterations).then(function (blob) {
        return decryptWif(blob, password);
    }).then(
        function (v) { __asyncResult = { status: 'fulfilled', threw: false, message: null, value: v }; },
        function (e) { __asyncResult = { status: 'rejected', threw: true, message: e.message, value: null }; }
    );
    return __asyncResult;
}

// Reconstructs the legacy SCv2 blob layout (magic || salt || nonce ||
// ciphertext || tag) at the fixed 1000 iteration count, exactly as the old
// pure-JS writer produced it, so decryptWif's SCv2 read path can be exercised.
function __buildScv2Blob(wif, password) {
    var salt = window.crypto.getRandomValues(new Uint8Array(WIF_SALT_LENGTH));
    var nonce = window.crypto.getRandomValues(new Uint8Array(WIF_NONCE_LENGTH));
    var keyMaterial = pbkdf2Sha256(utf8Encode(password), salt, WIF_LEGACY_PBKDF2_ITERATIONS, 64);
    var encryptionKey = keyMaterial.slice(0, 32);
    var macKey = keyMaterial.slice(32, 64);
    var ciphertext = chacha20Xor(encryptionKey, nonce, 0, utf8Encode(wif));
    var header = concatBytes(WIF_CRYPTO_MAGIC_V2, salt, nonce);
    var tag = hmacSha256(macKey, header, ciphertext);
    return btoa(String.fromCharCode.apply(null, concatBytes(header, ciphertext, tag)));
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


def _run_pbkdf2(ctx: quickjs.Context, password: str, salt_hex: str, iterations: int, key_length: int) -> str:
    return ctx.eval(
        "JSON.stringify(__runPbkdf2("
        + json.dumps(password) + ", " + json.dumps(salt_hex) + ", "
        + str(iterations) + ", " + str(key_length) + "))"
    ).strip('"')


def _run_chacha20(ctx: quickjs.Context, key_hex: str, nonce_hex: str, counter: int, length: int) -> str:
    return ctx.eval(
        "JSON.stringify(__runChacha20("
        + json.dumps(key_hex) + ", " + json.dumps(nonce_hex) + ", "
        + str(counter) + ", " + str(length) + "))"
    ).strip('"')


def _run_async(ctx: quickjs.Context, call: str) -> dict:
    ctx.eval(call)
    _drain_jobs(ctx)
    return json.loads(ctx.eval("JSON.stringify(__asyncResult)"))


def _encrypt(ctx: quickjs.Context, wif: str, password: str, iterations: int | None = None) -> str:
    args = [json.dumps(wif), json.dumps(password)]
    if iterations is not None:
        args.append(str(iterations))
    result = _run_async(ctx, "__runEncrypt(" + ", ".join(args) + ")")
    assert result["status"] == "fulfilled", result["message"]
    return result["value"]


def _decrypt(ctx: quickjs.Context, blob: str, password: str) -> dict:
    return _run_async(ctx, "__runDecrypt(" + json.dumps(blob) + ", " + json.dumps(password) + ")")


# --- Primitive correctness against trusted references --------------------------


def test_pbkdf2_sha256_matches_hashlib():
    ctx = _build_context()
    vectors = [
        (b"password", b"salt", 1, 32),
        (b"password", b"salt", 4096, 32),
        (b"passwordPASSWORDpassword", b"saltSALTsaltSALTsaltSALTsaltSALTsalt", 4096, 40),
        (b"pass\0word", b"sa\0lt", 4096, 16),
    ]
    for password, salt, iterations, key_length in vectors:
        expected = hashlib.pbkdf2_hmac("sha256", password, salt, iterations, key_length).hex()
        actual = _run_pbkdf2(ctx, password.decode("latin-1"), salt.hex(), iterations, key_length)
        assert actual == expected, (password, salt, iterations, key_length)


def test_pbkdf2_sha256_known_answer_vector():
    ctx = _build_context()
    actual = _run_pbkdf2(ctx, "password", "73616c74", 4096, 32)
    assert actual == "c5e478d59288c841aa530db6845c4c8d962893a001ce4e11a4963873aa98134a"


def test_chacha20_matches_rfc8439_vector():
    ctx = _build_context()
    actual = _run_chacha20(ctx, _CHACHA_KEY.hex(), _CHACHA_NONCE.hex(), _CHACHA_COUNTER, len(_CHACHA_KEYSTREAM))
    assert actual == _CHACHA_KEYSTREAM.hex()


# --- encryptWif / decryptWif work without crypto.subtle ------------------------


def test_round_trip_without_crypto_subtle():
    ctx = _build_context()
    result = _run_async(
        ctx,
        "__runRoundTrip(" + json.dumps(_VALID_WIF) + ", " + json.dumps("correct horse battery staple") + ", 1000)",
    )
    assert result["status"] == "fulfilled", result["message"]
    assert result["value"] == _VALID_WIF


def test_encrypted_blob_has_magic_prefix():
    ctx = _build_context()
    default_iterations = ctx.eval("WIF_PBKDF2_ITERATIONS")
    assert default_iterations >= 600000
    assert default_iterations == 600000

    blob = _encrypt(ctx, _VALID_WIF, "password", 1000)
    raw = base64.b64decode(blob)
    assert raw[:4] == b"SCv3"
    assert len(raw) == 4 + 4 + 16 + 12 + len(_VALID_WIF) + 32
    assert int.from_bytes(raw[4:8], "big") == 1000


def test_encryption_is_randomised():
    ctx = _build_context()
    first = _encrypt(ctx, _VALID_WIF, "password", 1000)
    second = _encrypt(ctx, _VALID_WIF, "password", 1000)
    assert first != second


def test_decrypt_with_wrong_password_is_rejected():
    ctx = _build_context()
    blob = _encrypt(ctx, _VALID_WIF, "right-password", 1000)
    result = _decrypt(ctx, blob, "wrong-password")
    assert result["status"] == "rejected"
    assert "Invalid password" in result["message"]


def test_decrypt_rejects_tampered_ciphertext():
    ctx = _build_context()
    blob = _encrypt(ctx, _VALID_WIF, "password", 1000)
    raw = bytearray(base64.b64decode(blob))
    raw[4 + 4 + 16 + 12] ^= 0x01  # flip a bit in the first ciphertext byte
    tampered = base64.b64encode(bytes(raw)).decode()
    result = _decrypt(ctx, tampered, "password")
    assert result["status"] == "rejected"
    assert "Invalid password" in result["message"]


def test_decrypt_rejects_tampered_tag():
    ctx = _build_context()
    blob = _encrypt(ctx, _VALID_WIF, "password", 1000)
    raw = bytearray(base64.b64decode(blob))
    raw[-1] ^= 0x01  # flip a bit in the authentication tag
    tampered = base64.b64encode(bytes(raw)).decode()
    result = _decrypt(ctx, tampered, "password")
    assert result["status"] == "rejected"


def test_decrypt_rejects_tampered_iteration_count():
    ctx = _build_context()
    blob = _encrypt(ctx, _VALID_WIF, "password", 1000)
    raw = bytearray(base64.b64decode(blob))
    # Flip the low bit of the 32-bit big-endian iteration count so it reads 1001
    # instead of 1000 (still small enough to derive quickly) while the stored
    # ciphertext/tag were produced with the original 1000 iterations.
    raw[7] ^= 0x01
    tampered = base64.b64encode(bytes(raw)).decode()
    result = _decrypt(ctx, tampered, "password")
    assert result["status"] == "rejected"
    assert "Invalid password" in result["message"]


def test_decrypt_rejects_absurd_iteration_count():
    ctx = _build_context()
    blob = _encrypt(ctx, _VALID_WIF, "password", 1000)
    raw = bytearray(base64.b64decode(blob))
    # Overwrite the 32-bit big-endian iteration field with 0xFFFFFFFF. The guard
    # must reject the header before PBKDF2 runs, so this returns quickly rather
    # than looping ~4 billion times.
    raw[4:8] = b"\xff\xff\xff\xff"
    tampered = base64.b64encode(bytes(raw)).decode()
    result = _decrypt(ctx, tampered, "password")
    assert result["status"] == "rejected"
    assert "Invalid password" in result["message"]


def test_decrypt_rejects_zero_iteration_count():
    ctx = _build_context()
    blob = _encrypt(ctx, _VALID_WIF, "password", 1000)
    raw = bytearray(base64.b64decode(blob))
    # A zero iteration count is invalid and must be rejected before PBKDF2 runs.
    raw[4:8] = b"\x00\x00\x00\x00"
    tampered = base64.b64encode(bytes(raw)).decode()
    result = _decrypt(ctx, tampered, "password")
    assert result["status"] == "rejected"
    assert "Invalid password" in result["message"]


def test_decrypt_accepts_low_iteration_scv3_blob():
    ctx = _build_context()
    blob = _encrypt(ctx, _VALID_WIF, "password", 1000)
    result = _decrypt(ctx, blob, "password")
    assert result["status"] == "fulfilled"
    assert result["value"] == _VALID_WIF


def test_decrypt_accepts_legacy_scv2_blob():
    ctx = _build_context()
    blob = ctx.eval("__buildScv2Blob(" + json.dumps(_VALID_WIF) + ", " + json.dumps("password") + ")")
    result = _decrypt(ctx, blob, "password")
    assert result["status"] == "fulfilled"
    assert result["value"] == _VALID_WIF


def test_legacy_blob_without_crypto_subtle_raises_clear_error():
    ctx = _build_context()
    # A legacy AES-GCM blob (salt || iv || ciphertext) carries no magic prefix.
    legacy = base64.b64encode(bytes(16 + 12 + 20)).decode()
    result = _decrypt(ctx, legacy, "password")
    assert result["status"] == "rejected"
    assert "secure context" in result["message"]

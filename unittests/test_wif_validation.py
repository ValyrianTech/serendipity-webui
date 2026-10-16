"""Regression tests for WIF validation in ``static/js/bitcoin.js``.

``wifToPrivateKey()`` is the single function that turns a user-supplied Bitcoin
WIF into the raw 32-byte private key used for signing and address derivation.
A malformed or slightly mistyped WIF must be *rejected*, never silently coerced
into a different key.

These tests execute the *real* ``static/js/bitcoin.js`` source (only its
``import``/``export`` statements are stripped so it can load as a classic
script) inside an embedded QuickJS engine, together with the *real* vendored
SHA-256 implementation so the Base58Check checksum path is exercised for real.
``wifToPrivateKey`` and ``wifToAddress`` are therefore the actual shipped
functions, not reimplementations.
"""

import json
import re
from pathlib import Path

import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
BITCOIN_JS_PATH = REPO_ROOT / "static" / "js" / "bitcoin.js"
SHA256_JS_PATH = REPO_ROOT / "static" / "js" / "vendor" / "sha256.js"
BASE_HTML_PATH = REPO_ROOT / "app" / "templates" / "base.html"

# A deterministic 32-byte private key used as the basis for every WIF vector.
PRIVATE_KEY_HEX = "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f"

# Valid compressed mainnet WIF for PRIVATE_KEY_HEX: 0x80 || key || 0x01, Base58Check.
VALID_COMPRESSED_WIF = "KwDidQJHSE67VJ6MWRvbBKAxhD3F48DvqRT6JRqrjd7MHLBjGF7V"
# Valid uncompressed mainnet WIF for PRIVATE_KEY_HEX: 0x80 || key, Base58Check.
VALID_UNCOMPRESSED_WIF = "5HpHgWkLaovGWySEFpng1XQ6pdG1TzNWR7SrETvfTRVdKHNXZh8"
# Same 52-char payload as VALID_COMPRESSED_WIF but one base58 char flipped, so the
# checksum no longer matches (still valid base58, still 38 decoded bytes).
BAD_CHECKSUM_WIF = "KwDid2JHSE67VJ6MWRvbBKAxhD3F48DvqRT6JRqrjd7MHLBjGF7V"
# Valid checksum but the version byte is 0xef (testnet) rather than 0x80.
BAD_VERSION_WIF = "cMai6KJ8sHnNejZctqjiYdg2KSLeiaKcuTbZQrJNEjmMY5JQw6eP"
# 38 decoded bytes, valid checksum, version 0x80, but compression flag byte is
# 0x02 instead of 0x01.
BAD_FLAG_WIF = "KwDidQJHSE67VJ6MWRvbBKAxhD3F48DvqRT6JRqrjd7MHLNPJLxJ"
# 39 decoded bytes: valid checksum and version, but the overall length is wrong.
BAD_LENGTH_WIF = "2SaK8jfqHYLmZdtSdWu1XrXCxpU8u2nt4civAPveeX8P2X57rpUbkZ"
# Contains the base58-invalid character '0'.
BAD_BASE58_WIF = "KwDid0JHSE67VJ6MWRvbBKAxhD3F48DvqRT6JRqrjd7MHLBjGF7V"


# Browser stubs injected before loading bitcoin.js so its module-level code can
# run. ``secp256k1`` is a deterministic stub (its output is irrelevant to WIF
# parsing itself); ``ripemd160`` is a fixed-length stub. The real ``sha256`` is
# loaded from the vendored module.
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
var window = { crypto: {} };
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
"""

# Harnesses that call the real functions and report their outcome as JSON.
_HARNESS = """
function __tryPrivateKey(wif) {
    var result = { threw: false, message: null, length: null, hex: null };
    try {
        var key = wifToPrivateKey(wif);
        result.length = key.length;
        var hex = '';
        for (var i = 0; i < key.length; i++) {
            hex += ('0' + key[i].toString(16)).slice(-2);
        }
        result.hex = hex;
    } catch (error) {
        result.threw = true;
        result.message = error.message;
    }
    return result;
}

function __tryAddress(wif) {
    var result = { threw: false, address: null, isNull: false };
    try {
        var address = wifToAddress(wif);
        result.address = address;
        result.isNull = (address === null);
    } catch (error) {
        result.threw = true;
        result.message = error.message;
    }
    return result;
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


def _run_private_key(ctx: quickjs.Context, wif: str) -> dict:
    payload = ctx.eval("JSON.stringify(__tryPrivateKey(" + json.dumps(wif) + "))")
    return json.loads(payload)


def _run_address(ctx: quickjs.Context, wif: str) -> dict:
    payload = ctx.eval("JSON.stringify(__tryAddress(" + json.dumps(wif) + "))")
    return json.loads(payload)


# --- Positive cases: valid WIFs decode to the right 32-byte key ----------------


def test_valid_compressed_wif_decodes_to_private_key():
    ctx = _build_context()
    result = _run_private_key(ctx, VALID_COMPRESSED_WIF)
    assert result["threw"] is False, result["message"]
    assert result["length"] == 32
    assert result["hex"] == PRIVATE_KEY_HEX


def test_valid_uncompressed_wif_decodes_to_private_key():
    ctx = _build_context()
    result = _run_private_key(ctx, VALID_UNCOMPRESSED_WIF)
    assert result["threw"] is False, result["message"]
    assert result["length"] == 32
    assert result["hex"] == PRIVATE_KEY_HEX


# --- Negative cases: malformed WIFs are rejected loudly ------------------------


def test_short_wif_throws_bad_length():
    ctx = _build_context()
    result = _run_private_key(ctx, "abc")
    assert result["threw"] is True
    assert result["message"] == "Invalid WIF: bad length"


def test_wrong_length_wif_throws_bad_length():
    ctx = _build_context()
    result = _run_private_key(ctx, BAD_LENGTH_WIF)
    assert result["threw"] is True
    assert result["message"] == "Invalid WIF: bad length"


def test_tampered_checksum_throws_bad_checksum():
    ctx = _build_context()
    result = _run_private_key(ctx, BAD_CHECKSUM_WIF)
    assert result["threw"] is True
    assert result["message"] == "Invalid WIF: bad checksum"


def test_testnet_version_throws_unsupported_version():
    ctx = _build_context()
    result = _run_private_key(ctx, BAD_VERSION_WIF)
    assert result["threw"] is True
    assert result["message"] == "Invalid WIF: unsupported version byte"


def test_bad_compression_flag_throws():
    ctx = _build_context()
    result = _run_private_key(ctx, BAD_FLAG_WIF)
    assert result["threw"] is True
    assert result["message"] == "Invalid WIF: bad compression flag"


def test_invalid_base58_character_throws():
    ctx = _build_context()
    result = _run_private_key(ctx, BAD_BASE58_WIF)
    assert result["threw"] is True
    assert result["message"] == "Invalid base58 character"


# --- wifToAddress maps failures to null instead of propagating ----------------


def test_wif_to_address_returns_string_for_valid_wif():
    ctx = _build_context()
    result = _run_address(ctx, VALID_COMPRESSED_WIF)
    assert result["threw"] is False
    assert result["isNull"] is False
    assert isinstance(result["address"], str)


def test_wif_to_address_returns_null_for_malformed_wif():
    ctx = _build_context()
    result = _run_address(ctx, BAD_CHECKSUM_WIF)
    assert result["threw"] is False
    assert result["isNull"] is True
    assert result["address"] is None


def test_wif_to_address_returns_null_for_short_wif():
    ctx = _build_context()
    result = _run_address(ctx, "abc")
    assert result["threw"] is False
    assert result["isNull"] is True


# --- The setup form refuses to persist a key that derives no address ----------


def test_save_setup_wif_rejects_underivable_key():
    content = BASE_HTML_PATH.read_text(encoding="utf-8")
    assert "if (!wifToAddress(wif)) {" in content
    assert "'Invalid WIF key: please check for typos'" in content

    # The rejection must happen before the key is encrypted or persisted.
    reject_idx = content.index("if (!wifToAddress(wif)) {")
    encrypt_idx = content.index("await encryptWif(wif, password);")
    persist_idx = content.index("saveWif(null, true, encrypted);")
    assert reject_idx < encrypt_idx
    assert reject_idx < persist_idx

"""Regression tests for legacy plaintext WIF migration.

Before PR #6, ``saveWif()`` persisted the raw WIF string to ``localStorage``
under ``serendipity_wif`` with an ``encrypted`` flag that was falsy/absent, e.g.
``{encrypted: false, data: "<raw WIF>", timestamp}``. That shape can never be
decrypted (the ``data`` field holds plaintext, not an AES-GCM blob), so a user
who still has it would be routed to the unlock modal and be permanently stuck.

The migration detects that legacy shape on startup via ``isLegacyPlaintextWif``,
clears the plaintext from ``localStorage`` (it must never remain at rest), and
routes the user to the setup modal with an explicit message.

These tests execute the *real* ``static/js/bitcoin.js`` source through QuickJS
(the same harness pattern as ``test_save_wif.py``) so ``isLegacyPlaintextWif``
is the actual shipped function, not a reimplementation.
"""

import json
from pathlib import Path

import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
BITCOIN_JS_PATH = REPO_ROOT / "static" / "js" / "bitcoin.js"
BASE_HTML_PATH = REPO_ROOT / "app" / "templates" / "base.html"

# Browser stubs injected before loading bitcoin.js so its module-level code can
# run. Only the bindings used at module load time need to exist.
_STUBS = """
var localStorage = {
    getItem: function () { return null; },
    setItem: function () {},
    removeItem: function () {},
};
var secp256k1 = {};
var sha256 = function () {};
var ripemd160 = function () {};
var window = {};
"""

# Tiny JS harness that invokes the real ``isLegacyPlaintextWif`` and reports its
# result as JSON.
_HARNESS = """
function __runIsLegacy(storage) {
    var result = { threw: false, message: null, value: null };
    try {
        result.value = isLegacyPlaintextWif(storage) ? true : false;
    } catch (error) {
        result.threw = true;
        result.message = error.message;
    }
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


def _run_is_legacy(ctx: quickjs.Context, storage) -> bool:
    """Call the real ``isLegacyPlaintextWif`` and return its boolean result."""
    payload = ctx.eval("JSON.stringify(__runIsLegacy(" + json.dumps(storage) + "))")
    result = json.loads(payload)
    assert result["threw"] is False, result["message"]
    return result["value"]


def test_is_legacy_true_for_legacy_shape_with_encrypted_false():
    ctx = _build_context()
    storage = {"encrypted": False, "data": "<raw WIF string>", "timestamp": "2026-01-01T00:00:00.000Z"}
    assert _run_is_legacy(ctx, storage) is True


def test_is_legacy_true_for_shape_without_encrypted_flag():
    ctx = _build_context()
    storage = {"data": "<raw WIF string>"}
    assert _run_is_legacy(ctx, storage) is True


def test_is_legacy_false_for_null():
    ctx = _build_context()
    assert _run_is_legacy(ctx, None) is False


def test_is_legacy_false_for_encrypted_blob():
    ctx = _build_context()
    storage = {"encrypted": True, "data": "<encrypted base64 blob>"}
    assert _run_is_legacy(ctx, storage) is False


def test_is_legacy_false_for_empty_and_missing_data():
    ctx = _build_context()
    assert _run_is_legacy(ctx, {}) is False
    assert _run_is_legacy(ctx, {"encrypted": True}) is False


def test_base_html_imports_and_exposes_is_legacy_plaintext_wif():
    content = BASE_HTML_PATH.read_text(encoding="utf-8")
    assert "isLegacyPlaintextWif" in content
    assert "clearWifStorage, isLegacyPlaintextWif" in content
    assert "window.isLegacyPlaintextWif = isLegacyPlaintextWif;" in content


def test_base_html_initialize_app_clears_legacy_plaintext_and_messages():
    content = BASE_HTML_PATH.read_text(encoding="utf-8")
    assert "if (isLegacyPlaintextWif(storage)) {" in content
    assert "clearWifStorage();" in content
    assert "Please re-enter your WIF key and set a password." in content

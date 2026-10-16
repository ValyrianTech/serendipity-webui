"""Regression tests for the sessionStorage WIF-unlock feature.

After the user unlocks their encrypted WIF (or completes initial setup), the
*decrypted* WIF key is held in ``sessionStorage`` under ``wif_session_key`` so
that page navigation within the same tab does not re-prompt for the password,
while the key is still forgotten when the tab closes and never written to disk
(the encrypted blob alone remains in ``localStorage``).

Three invariants are guarded here:

1. ``initializeApp()`` (in ``app/templates/base.html``) restores the decrypted
   key from ``sessionStorage`` *before* falling back to the unlock prompt.
2. Locking the session clears ``sessionStorage``.
3. The decrypted WIF is written only to ``sessionStorage`` and never leaks into
   ``localStorage``.

These tests execute the *real* ``static/js/bitcoin.js`` source (only its
``import``/``export`` statements are stripped so it can load as a classic
script) inside an embedded QuickJS engine, following the same harness pattern
as ``test_save_wif.py`` and ``test_legacy_wif_migration.py``. ``saveSessionWif``,
``loadSessionWif``, and ``clearSessionWif`` are therefore the actual shipped
functions, not reimplementations.
"""

import json
from pathlib import Path

import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
BITCOIN_JS_PATH = REPO_ROOT / "static" / "js" / "bitcoin.js"
BASE_HTML_PATH = REPO_ROOT / "app" / "templates" / "base.html"

SESSION_STORAGE_KEY = "wif_session_key"

# Browser stubs injected before loading bitcoin.js so its module-level code can
# run. ``sessionStorage`` records every call into ``__sessionStorageCalls`` and
# round-trips values through ``__sessionStorageData`` so ``loadSessionWif``
# returns what ``saveSessionWif`` stored. ``localStorage`` records its calls too
# so tests can prove the decrypted WIF never lands there.
_STUBS = """
var __localStorageCalls = [];
var localStorage = {
    getItem: function (key) { __localStorageCalls.push({ method: 'getItem', key: key }); return null; },
    setItem: function (key, value) { __localStorageCalls.push({ method: 'setItem', key: key, value: value }); },
    removeItem: function (key) { __localStorageCalls.push({ method: 'removeItem', key: key }); },
};
var __sessionStorageCalls = [];
var __sessionStorageData = {};
var sessionStorage = {
    getItem: function (key) { __sessionStorageCalls.push({ method: 'getItem', key: key }); return (key in __sessionStorageData) ? __sessionStorageData[key] : null; },
    setItem: function (key, value) { __sessionStorageCalls.push({ method: 'setItem', key: key, value: value }); __sessionStorageData[key] = value; },
    removeItem: function (key) { __sessionStorageCalls.push({ method: 'removeItem', key: key }); delete __sessionStorageData[key]; },
};
var secp256k1 = {};
var sha256 = function () {};
var ripemd160 = function () {};
var window = {};
"""

# Tiny JS harness that invokes the real session helpers and reports what they
# stored and which storage calls they made, as JSON.
_HARNESS = """
function __resetCalls() {
    __localStorageCalls = [];
    __sessionStorageCalls = [];
    __sessionStorageData = {};
}

function __runSessionLifecycle(wif) {
    __resetCalls();
    var result = { threw: false, message: null, saved: null, cleared: null, sessionCalls: [], localStorageCalls: [] };
    try {
        saveSessionWif(wif);
        result.saved = loadSessionWif();
        clearSessionWif();
        result.cleared = loadSessionWif();
    } catch (error) {
        result.threw = true;
        result.message = error.message;
    }
    result.sessionCalls = __sessionStorageCalls;
    result.localStorageCalls = __localStorageCalls;
    return result;
}

function __runSaveSessionOnly(wif) {
    __resetCalls();
    var result = { threw: false, message: null, sessionCalls: [], localStorageCalls: [] };
    try {
        saveSessionWif(wif);
    } catch (error) {
        result.threw = true;
        result.message = error.message;
    }
    result.sessionCalls = __sessionStorageCalls;
    result.localStorageCalls = __localStorageCalls;
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


def _build_context() -> quickjs.Context:
    """Create a QuickJS context with browser stubs and the real bitcoin.js loaded."""
    ctx = quickjs.Context()
    ctx.eval(_STUBS)
    ctx.eval(_strip_module_syntax(BITCOIN_JS_PATH.read_text(encoding="utf-8")))
    ctx.eval(_HARNESS)
    return ctx


def _run_session_lifecycle(ctx: quickjs.Context, wif) -> dict:
    """Run ``saveSessionWif`` -> ``loadSessionWif`` -> ``clearSessionWif`` -> ``loadSessionWif``."""
    payload = ctx.eval("JSON.stringify(__runSessionLifecycle(" + json.dumps(wif) + "))")
    return json.loads(payload)


def _run_save_session_only(ctx: quickjs.Context, wif) -> dict:
    """Run only ``saveSessionWif`` and report every storage call it made."""
    payload = ctx.eval("JSON.stringify(__runSaveSessionOnly(" + json.dumps(wif) + "))")
    return json.loads(payload)


def test_session_wif_roundtrips_then_clears_on_lock():
    ctx = _build_context()
    result = _run_session_lifecycle(ctx, "somewif")
    assert result["threw"] is False, result["message"]

    assert result["saved"] == "somewif"
    assert result["cleared"] is None

    remove_calls = [call for call in result["sessionCalls"] if call["method"] == "removeItem"]
    assert any(call["key"] == SESSION_STORAGE_KEY for call in remove_calls)


def test_decrypted_wif_never_lands_in_localstorage():
    ctx = _build_context()
    result = _run_save_session_only(ctx, "DECRYPTED_WIF_VALUE")
    assert result["threw"] is False, result["message"]

    local_set_calls = [call for call in result["localStorageCalls"] if call["method"] == "setItem"]
    for call in local_set_calls:
        assert "DECRYPTED_WIF_VALUE" not in str(call.get("value", ""))

    session_set_calls = [call for call in result["sessionCalls"] if call["method"] == "setItem"]
    assert len(session_set_calls) == 1
    assert session_set_calls[0]["key"] == SESSION_STORAGE_KEY
    assert session_set_calls[0]["value"] == "DECRYPTED_WIF_VALUE"


def test_base_html_bootstrap_uses_session_wif_before_unlock_branch():
    content = BASE_HTML_PATH.read_text(encoding="utf-8")
    assert "const sessionWif = loadSessionWif();" in content
    assert "if (sessionWif) {" in content
    assert "window.currentWif = sessionWif;" in content
    assert "updateWifStatus(true);" in content
    assert "} else if (storage && storage.data) {" in content

    assert content.index("if (sessionWif) {") < content.index("} else if (storage && storage.data) {")


def test_base_html_lock_session_clears_session_wif_and_shows_unlock_modal():
    content = BASE_HTML_PATH.read_text(encoding="utf-8")
    assert "window.lockSession = function() {" in content
    assert "clearSessionWif();" in content
    assert 'onclick="lockSession()"' in content

    lock_idx = content.index("window.lockSession = function() {")
    clear_idx = content.index("clearSessionWif();")
    unlock_modal_idx = content.index(
        "document.getElementById('unlock-modal').classList.remove('hidden');",
        lock_idx,
    )
    assert lock_idx < clear_idx < unlock_modal_idx


def test_base_html_clear_and_setup_new_key_clears_session_wif():
    content = BASE_HTML_PATH.read_text(encoding="utf-8")
    assert "window.clearAndSetupNewKey = function() {" in content

    setup_fn_idx = content.index("window.clearAndSetupNewKey = function() {")
    clear_session_idx = content.index("clearSessionWif();", setup_fn_idx)
    null_wif_idx = content.index("window.currentWif = null;", setup_fn_idx)
    update_status_idx = content.index("updateWifStatus(false);", setup_fn_idx)
    setup_modal_idx = content.index(
        "document.getElementById('setup-wif-modal').classList.remove('hidden');",
        setup_fn_idx,
    )

    assert setup_fn_idx < clear_session_idx < setup_modal_idx
    assert setup_fn_idx < null_wif_idx < setup_modal_idx
    assert setup_fn_idx < update_status_idx < setup_modal_idx


def test_base_html_exposes_session_helpers_and_calls_save_session_wif():
    content = BASE_HTML_PATH.read_text(encoding="utf-8")
    assert "saveSessionWif, loadSessionWif, clearSessionWif" in content
    assert "window.saveSessionWif = saveSessionWif;" in content
    assert "window.loadSessionWif = loadSessionWif;" in content
    assert "window.clearSessionWif = clearSessionWif;" in content

    assert content.count("saveSessionWif(wif);") >= 2

    unlock_fn_idx = content.index("window.unlockWif = async function() {")
    unlock_save_idx = content.index("saveSessionWif(wif);", unlock_fn_idx)
    close_unlock_modal_idx = content.index("window.closeUnlockModal = function() {", unlock_fn_idx)
    assert unlock_fn_idx < unlock_save_idx < close_unlock_modal_idx

    setup_fn_idx = content.index("window.saveSetupWif = async function() {")
    setup_save_idx = content.index("saveSessionWif(wif);", setup_fn_idx)
    show_toast_fn_idx = content.index("window.showToast = function(message, type = 'info') {", setup_fn_idx)
    assert setup_fn_idx < setup_save_idx < show_toast_fn_idx


def test_base_html_lock_button_hidden_and_toggled_in_update_wif_status():
    content = BASE_HTML_PATH.read_text(encoding="utf-8")

    # (a) the Lock button has a stable id and keeps its onclick handler
    assert 'id="lock-button"' in content
    assert 'onclick="lockSession()"' in content

    # (b) the Lock button is hidden by default (its class list includes `hidden`)
    lock_button_idx = content.index('id="lock-button"')
    button_tag_end = content.index(">", lock_button_idx)
    button_tag = content[lock_button_idx:button_tag_end]
    assert 'class="' in button_tag
    class_attr = button_tag.split('class="', 1)[1].split('"', 1)[0]
    assert "hidden" in class_attr.split()

    # (c) updateWifStatus toggles the Lock button based on window.currentWif
    update_fn_idx = content.index("function updateWifStatus(loaded) {")
    update_assign_idx = content.index("window.updateWifStatus = updateWifStatus;", update_fn_idx)

    lock_ref_idx = content.index("document.getElementById('lock-button')", update_fn_idx)
    cond_idx = content.index("loaded && window.currentWif", update_fn_idx)
    remove_idx = content.index("classList.remove('hidden')", update_fn_idx)
    add_idx = content.index("classList.add('hidden')", update_fn_idx)

    assert update_fn_idx < lock_ref_idx < update_assign_idx
    assert update_fn_idx < cond_idx < remove_idx
    assert update_fn_idx < cond_idx < add_idx
    assert update_fn_idx < remove_idx < add_idx < update_assign_idx

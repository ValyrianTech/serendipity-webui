"""Regression tests for the WIF key-derivation busy state in the UI.

At the production PBKDF2 iteration count (~600k) key derivation blocks the main
thread for a noticeable fraction of a second. The templates wrap the expensive
``encryptWif`` / ``decryptWif`` calls in ``window.withWifBusy``, which disables
the triggering button, shows a busy label, and yields a macrotask so the browser
can paint the updated label before the blocking derivation begins.

These assertions pin that wiring so a future edit cannot silently drop the busy
state and reintroduce a frozen-looking UI during save/unlock.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_HTML_PATH = REPO_ROOT / "app" / "templates" / "base.html"
SETTINGS_HTML_PATH = REPO_ROOT / "app" / "templates" / "settings.html"


def test_with_wif_busy_helper_is_defined_in_both_templates():
    for path in (BASE_HTML_PATH, SETTINGS_HTML_PATH):
        content = path.read_text(encoding="utf-8")
        assert "window.withWifBusy = async function" in content, path
        # The helper must yield a macrotask so the busy label paints before the
        # synchronous derivation starts, otherwise the UI still appears frozen.
        assert "setTimeout(resolve, 0)" in content, path
        assert "button.disabled = true" in content, path


def test_unlock_derivation_is_wrapped_in_busy_state():
    content = BASE_HTML_PATH.read_text(encoding="utf-8")
    unlock_fn_idx = content.index("window.unlockWif = async function() {")
    busy_idx = content.index("window.withWifBusy('unlock-button'", unlock_fn_idx)
    decrypt_idx = content.index("await decryptWif(storage.data, password);", unlock_fn_idx)
    # The busy wrapper must be entered before the blocking derivation.
    assert unlock_fn_idx < busy_idx < decrypt_idx
    assert 'id="unlock-button"' in content


def test_save_setup_derivation_is_wrapped_in_busy_state():
    content = BASE_HTML_PATH.read_text(encoding="utf-8")
    setup_fn_idx = content.index("window.saveSetupWif = async function() {")
    busy_idx = content.index("window.withWifBusy('save-setup-wif-button'", setup_fn_idx)
    encrypt_idx = content.index("await encryptWif(wif, password);", setup_fn_idx)
    assert setup_fn_idx < busy_idx < encrypt_idx
    assert 'id="save-setup-wif-button"' in content


def test_settings_save_derivation_is_wrapped_in_busy_state():
    content = SETTINGS_HTML_PATH.read_text(encoding="utf-8")
    save_fn_idx = content.index("window.saveNewWifKey = async function() {")
    busy_idx = content.index("window.withWifBusy('save-new-wif-button'", save_fn_idx)
    encrypt_idx = content.index("await encryptWif(wif, password);", save_fn_idx)
    assert save_fn_idx < busy_idx < encrypt_idx
    assert 'id="save-new-wif-button"' in content

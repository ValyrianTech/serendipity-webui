"""Regression tests ensuring the Settings page never echoes WIF key material.

The Settings page's ``updateWifDisplay()`` function previously wrote the first
and last 10 characters of the unlocked WIF private key into the
``#wif-key-display`` textarea. A WIF is ~52 characters, so 20 characters of key
material leaked into the DOM. These tests pin the redaction so a future edit
cannot reintroduce that leak.

Coverage is split into two layers:

* **Runtime tests** execute the *real* ``updateWifDisplay()`` source (extracted
  from ``app/templates/settings.html``) inside QuickJS with a stubbed DOM, so
  the shipped function is exercised against runtime DOM state rather than
  reimplemented. These assert on the actual ``.value`` the function writes to
  ``#wif-key-display`` / ``#current-address`` and that no WIF substring ever
  reaches any element.
* **Source-level assertions** (``test_template_never_slices_current_wif_into_dom``
  and ``test_update_wif_display_has_no_current_wif_slicing``) statically scan the
  template text. They are SOURCE-LEVEL only and are kept as defence-in-depth;
  the runtime coverage lives in the QuickJS tests below.
"""

import json
from pathlib import Path

import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_HTML_PATH = REPO_ROOT / "app" / "templates" / "settings.html"

# Named constant for the cosmetic placeholder text shown in the unlocked state.
# Keeping it in one place avoids coupling the template and test assertions to a
# duplicated literal.
HIDDEN_PLACEHOLDER = "(key loaded — hidden)"

# A real-looking WIF used to prove no key material leaks at runtime.
_VALID_WIF = "KwDidQJHSE67VJ6MWRvbBKAxhD3F48DvqRT6JRqrjd7MHLBjGF7V"


def _extract_js_function_body(content, signature):
    start = content.index(signature)
    brace = content.index("{", start)
    depth = 0
    for i in range(brace, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                return content[start:i + 1]
    raise AssertionError(f"Unbalanced braces in {signature}")


def _extract_update_wif_display_source() -> str:
    """Return the real ``updateWifDisplay`` function text shipped in settings.html."""
    content = SETTINGS_HTML_PATH.read_text(encoding="utf-8")
    return _extract_js_function_body(content, "function updateWifDisplay()")


# --- Source-level assertions (defence-in-depth only) -------------------------
# These inspect the static template source, not runtime DOM state. They guard
# against an obvious regression (re-introducing ``substring``/``slice`` on the
# WIF) but do NOT execute the function; runtime behaviour is covered by the
# QuickJS tests below.


def test_template_never_slices_current_wif_into_dom():
    # SOURCE-LEVEL assertion.
    content = SETTINGS_HTML_PATH.read_text(encoding="utf-8")
    assert "window.currentWif.substring(" not in content
    assert "currentWif.substring" not in content
    assert "currentWif.slice" not in content
    assert HIDDEN_PLACEHOLDER in content


def test_update_wif_display_has_no_current_wif_slicing():
    # SOURCE-LEVEL assertion.
    fn_body = _extract_update_wif_display_source()
    assert "'...'" not in fn_body
    assert "currentWif.substring" not in fn_body
    for unrelated in ("saveServerSettings", "saveNewWifKey", "withWifBusy"):
        assert unrelated not in fn_body
    assert len(fn_body) < 1500


# --- QuickJS runtime harness -------------------------------------------------
# A stubbed DOM where ``document.getElementById`` returns a stable per-id object
# with a mutable ``.value``, a settable ``window.currentWif``, a controllable
# ``loadWifStorage``, a pure ``wifToAddress`` stub, and a ``console`` no-op.

_STUBS = """
var __elements = {};
var document = {
    getElementById: function (id) {
        if (!Object.prototype.hasOwnProperty.call(__elements, id)) {
            __elements[id] = { value: '' };
        }
        return __elements[id];
    }
};
var console = { log: function () {}, warn: function () {}, error: function () {} };
var window = { currentWif: null };
var loadWifStorage = function () { return null; };
var wifToAddress = function () { return 'stubbed-address'; };
"""

_HARNESS = """
function __runUpdate() {
    try {
        window.updateWifDisplay();
        return { threw: false, message: null };
    } catch (e) {
        return { threw: true, message: e.message };
    }
}

function __getValue(id) {
    return Object.prototype.hasOwnProperty.call(__elements, id) ? __elements[id].value : null;
}

function __getAllValues() {
    var out = {};
    for (var k in __elements) {
        if (Object.prototype.hasOwnProperty.call(__elements, k)) {
            out[k] = __elements[k].value;
        }
    }
    return out;
}
"""


def _build_context() -> quickjs.Context:
    """Create a QuickJS context with DOM stubs and the real updateWifDisplay."""
    ctx = quickjs.Context()
    ctx.eval(_STUBS)
    # The function is module-scoped in the template; attach it to window so it is
    # directly callable without modifying production code.
    ctx.eval("window.updateWifDisplay = " + _extract_update_wif_display_source())
    ctx.eval(_HARNESS)
    return ctx


def _set_current_wif(ctx: quickjs.Context, wif: str | None) -> None:
    ctx.eval("window.currentWif = " + json.dumps(wif))


def _set_storage(ctx: quickjs.Context, storage: dict | None) -> None:
    if storage is None:
        ctx.eval("loadWifStorage = function () { return null; };")
    else:
        ctx.eval("loadWifStorage = function () { return " + json.dumps(storage) + "; };")


def _run_update(ctx: quickjs.Context) -> dict:
    return json.loads(ctx.eval("JSON.stringify(__runUpdate())"))


def _get_value(ctx: quickjs.Context, element_id: str) -> str | None:
    return json.loads(ctx.eval("JSON.stringify(__getValue(" + json.dumps(element_id) + "))"))


def _get_all_values(ctx: quickjs.Context) -> dict:
    return json.loads(ctx.eval("JSON.stringify(__getAllValues())"))


def _assert_no_key_material(values: dict, wif: str) -> None:
    joined = "\n".join(values.values())
    assert wif not in joined
    assert wif[:10] not in joined
    assert wif[-10:] not in joined


# --- Runtime tests -----------------------------------------------------------
# These execute the shipped ``updateWifDisplay()`` and assert on the stubbed DOM
# state it produces, covering all three branches.


def test_update_wif_display_runtime_unlocked_hides_key_material():
    ctx = _build_context()
    _set_current_wif(ctx, _VALID_WIF)
    _set_storage(ctx, None)
    result = _run_update(ctx)
    assert result["threw"] is False, result["message"]
    assert _get_value(ctx, "wif-key-display") == HIDDEN_PLACEHOLDER
    assert _get_value(ctx, "current-address") == "stubbed-address"
    _assert_no_key_material(_get_all_values(ctx), _VALID_WIF)


def test_update_wif_display_runtime_encrypted_shows_placeholder():
    ctx = _build_context()
    _set_current_wif(ctx, None)
    _set_storage(ctx, {"encrypted": True})
    result = _run_update(ctx)
    assert result["threw"] is False, result["message"]
    assert _get_value(ctx, "wif-key-display") == "(encrypted)"
    assert _get_value(ctx, "current-address") == "(encrypted - unlock to view)"


def test_update_wif_display_runtime_no_key_shows_configured():
    ctx = _build_context()
    _set_current_wif(ctx, None)
    _set_storage(ctx, None)
    result = _run_update(ctx)
    assert result["threw"] is False, result["message"]
    assert _get_value(ctx, "wif-key-display") == ""
    assert _get_value(ctx, "current-address") == "No key configured"

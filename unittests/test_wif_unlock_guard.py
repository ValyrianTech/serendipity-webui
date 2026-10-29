"""Tests for the WIF-unlock guard around signed API calls.

Several pages used to call the signed mutation helpers (``saveToolset``,
``saveLLMConfig``, ``editAgent`` ...) with the global ``window.currentWif``
without first checking that the user had unlocked their key. When locked,
``window.currentWif`` is ``null`` and ``signData()`` blew up deep inside
``base58Decode`` with a ``TypeError`` ("Cannot read properties of null").

The fix has two parts:

* ``signData()`` in ``static/js/bitcoin.js`` now throws a clear
  ``Error('WIF key is locked')`` when ``wif`` is falsy (defense-in-depth).
* ``requireWif()`` in ``static/js/api.js`` centralises the "please unlock"
  guard. Every template handler that signs now calls it first and bails out
  when it returns ``null``.

These tests execute the real ``bitcoin.js`` and ``api.js`` sources inside an
embedded QuickJS engine and additionally lock in the template-side guard via
source inspection.
"""

import json
import re
from pathlib import Path

import pytest
import quickjs

REPO_ROOT = Path(__file__).resolve().parent.parent
BITCOIN_JS_PATH = REPO_ROOT / "static" / "js" / "bitcoin.js"
API_JS_PATH = REPO_ROOT / "static" / "js" / "api.js"
TEMPLATES_DIR = REPO_ROOT / "app" / "templates"
SHA256_JS_PATH = REPO_ROOT / "static" / "js" / "vendor" / "sha256.js"

_EXPORT_BLOCK_RE = re.compile(r"export\{([^}]*)\};")


def _strip_module_syntax(source: str) -> str:
    """Turn ES-module source into a classic script quickjs can ``eval``."""
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


# Browser stubs for loading bitcoin.js. The real sha256 is loaded separately;
# secp256k1 / ripemd160 / storage are inert because the locked-WIF path never
# reaches them.
_BITCOIN_STUBS = """
var console = { log: function () {}, error: function () {} };
var secp256k1 = {
    getPublicKey: function (pk, compressed) {
        var out = new Uint8Array(compressed ? 33 : 65);
        return out;
    }
};
var ripemd160 = function () { return new Uint8Array(20); };
var window = {
    crypto: {
        getRandomValues: function (arr) { return arr; }
    }
};
var localStorage = { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {} };
var sessionStorage = { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {} };
var TextEncoder = function () {
    this.encode = function (str) {
        var bytes = new Uint8Array(str.length);
        for (var i = 0; i < str.length; i++) { bytes[i] = str.charCodeAt(i) & 0xff; }
        return bytes;
    };
};
"""

# Harness that calls signData and reports the settlement synchronously via
# try/catch for the immediate throw and a promise chain otherwise.
_BITCOIN_HARNESS = """
var __signResult = null;

function __runSignData(data, wif) {
    __signResult = { status: 'pending', message: null };
    signData(data, wif).then(
        function () { __signResult = { status: 'fulfilled', message: null }; },
        function (e) { __signResult = { status: 'rejected', message: e.message }; }
    );
    return __signResult;
}
"""


def _build_bitcoin_context() -> quickjs.Context:
    ctx = quickjs.Context()
    ctx.eval(_load_sha256_source())
    ctx.eval(_BITCOIN_STUBS)
    ctx.eval(_strip_module_syntax(BITCOIN_JS_PATH.read_text(encoding="utf-8")))
    ctx.eval(_BITCOIN_HARNESS)
    return ctx


def _drain(ctx: quickjs.Context, limit: int = 1000) -> None:
    for _ in range(limit):
        if not ctx.execute_pending_job():
            return
    raise AssertionError("too many pending jobs; possible infinite loop")


def _run_sign_data(ctx: quickjs.Context, data, wif_js: str) -> dict:
    ctx.eval("__runSignData(" + json.dumps(data) + ", " + wif_js + ")")
    _drain(ctx)
    return json.loads(ctx.eval("JSON.stringify(__signResult)"))


# --- signData rejects a locked (null/undefined) WIF with a clear message -------


def test_sign_data_throws_when_wif_is_null():
    ctx = _build_bitcoin_context()
    result = _run_sign_data(ctx, {"a": 1}, "null")
    assert result["status"] == "rejected"
    assert result["message"] == "WIF key is locked"


def test_sign_data_throws_when_wif_is_undefined():
    ctx = _build_bitcoin_context()
    result = _run_sign_data(ctx, {"a": 1}, "undefined")
    assert result["status"] == "rejected"
    assert result["message"] == "WIF key is locked"


def test_sign_data_throws_when_wif_is_empty_string():
    ctx = _build_bitcoin_context()
    result = _run_sign_data(ctx, {"a": 1}, '""')
    assert result["status"] == "rejected"
    assert result["message"] == "WIF key is locked"


# --- requireWif() central helper -------------------------------------------

# Reuse the api.js loader but with a fake window so requireWif can be driven.
_API_STUBS = """
var console = { log: function () {}, error: function () {} };
var localStorage = { getItem: function () { return null; }, setItem: function () {}, removeItem: function () {} };
var signData = function (data, wif) { return Promise.resolve(data); };
var wifToAddress = function () { return 'addr'; };
var __toasts = [];
var window = {
    currentWif: null,
    showToast: function (message, type) { __toasts.push({ message: message, type: type }); }
};
"""


def _build_api_context() -> quickjs.Context:
    ctx = quickjs.Context()
    ctx.eval(_API_STUBS)
    ctx.eval(_strip_module_syntax(API_JS_PATH.read_text(encoding="utf-8")))
    return ctx


def test_require_wif_returns_the_unlocked_key():
    ctx = _build_api_context()
    ctx.eval("window.currentWif = 'WIF123'")
    result = json.loads(ctx.eval("JSON.stringify(requireWif())"))
    assert result == "WIF123"
    assert json.loads(ctx.eval("JSON.stringify(__toasts)")) == []


def test_require_wif_returns_null_and_toasts_when_locked():
    ctx = _build_api_context()
    ctx.eval("window.currentWif = null")
    result = json.loads(ctx.eval("JSON.stringify(requireWif())"))
    assert result is None
    toasts = json.loads(ctx.eval("JSON.stringify(__toasts)"))
    assert toasts == [{"message": "Please unlock your WIF key first", "type": "error"}]


def test_require_wif_tolerates_missing_window():
    ctx = _build_api_context()
    # Simulate a non-browser context (window undefined) - must not throw.
    ctx.eval("window = undefined")
    result = json.loads(ctx.eval("JSON.stringify(requireWif())"))
    assert result is None


# --- template handlers guard before signing ---------------------------------

# (template, handler-name, signed-helper-name) triples that must guard.
_GUARDED_HANDLERS = [
    ("toolsets.html", "window.saveToolset = async function", "saveToolsetApi"),
    ("toolsets.html", "window.deleteToolsetConfig = async function", "deleteToolset"),
    ("llms.html", "window.saveConfig = async function", "saveLLMConfig"),
    ("llms.html", "window.deleteConfig = async function", "deleteLLMConfig"),
    ("llms.html", "window.saveDefaultLLM = async function", "saveDefaultLLMApi"),
    ("edit_agent.html", "window.saveAgent = async function", "editAgent"),
    ("edit_agent.html", "window.saveRole = async function", "editAgentRole"),
    ("edit_agent.html", "window.removeRole = async function", "editAgentRole"),
    ("mcps.html", "window.saveConfig = async function", "saveMCPServerConfig"),
    ("mcps.html", "window.deleteConfig = async function", "deleteMCPServerConfig"),
    ("skills.html", "window.saveSkill = async function", "saveSkillApi"),
    ("skills.html", "window.deleteSkillConfig = async function", "deleteSkill"),
    ("workflows.html", "window.saveNodeChanges = async function", "editWorkflowStep"),
    ("workflows.html", "window.generateNewWorkflow = async function", "generateWorkflow"),
    ("workflows.html", "window.runSelectedWorkflow = async function", "startWorkflow"),
]


def _extract_function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    open_idx = source.index("{", start)
    depth = 0
    for i in range(open_idx, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_idx + 1:i]
    raise AssertionError("unbalanced braces in " + signature)



@pytest.mark.parametrize("template,handler,signed_helper", _GUARDED_HANDLERS)
def test_handler_guards_wif_before_signing(template, handler, signed_helper):
    source = (TEMPLATES_DIR / template).read_text(encoding="utf-8")
    body = _extract_function_body(source, handler)
    assert "requireWif()" in body
    assert "if (!wif) return;" in body
    # The signed call must use the guarded local, never the raw global.
    assert signed_helper + "(window.currentWif" not in body
    assert signed_helper + "(wif" in body


def test_require_wif_is_exported_from_api():
    source = API_JS_PATH.read_text(encoding="utf-8")
    assert "export function requireWif()" in source

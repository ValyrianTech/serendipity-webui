"""Regression tests pinning the conversation-list rendering fix (PR #38, change request 3).

``app/templates/conversations.html`` renders each conversation card via
``createConversationItem(conv)`` and appends it inside a per-item ``try``/``catch``
loop in ``loadConversations``. Before the fix, a conversation whose
``conversation_id`` was missing, ``null``, ``undefined``, or non-string (numeric)
caused the unguarded ``conv.conversation_id.substring(0, 8)`` call to throw a
``TypeError``, which aborted rendering of every remaining item on the page.

The fix has two parts, both of which these tests pin so they stay fixed:

1. ``createConversationItem`` normalises the id once with
   ``const id = String(conv.conversation_id ?? '');``, slices the *coerced* string
   with ``id.substring(0, 8)``, and appends the trailing ``'...'`` ellipsis only
   when ``id`` is non-empty. A missing id therefore renders as an empty string
   with no ellipsis and the card is disabled (non-clickable) rather than wired to
   a bad id.
2. ``loadConversations`` wraps each item's ``createConversationItem`` +
   ``appendChild`` in its own ``try``/``catch``, so a single malformed item is
   logged via ``console.error`` and skipped instead of aborting the whole list.

The static tests below assert the fix is present in the template source (they run
even without a browser). The behavioral tests execute the real ``escapeHtml`` and
``createConversationItem`` (and a replica of the per-item loop) in headless
Chromium to prove the edge cases behave correctly at runtime. Coverage is
measured only over Python under ``app/``, so these test-only checks add no new
``app/`` lines.
"""

import html
import json
import re
import subprocess

import pytest
from test_xss_sanitization import (
    _CHROME,
    _extract_from_sanitize_module,
    _extract_function,
    _read_template,
    _requires_chrome,
)

_LOAD_CONVERSATIONS_ASSIGNMENT = "window.loadConversations = async function"


def _create_conversation_item_body():
    """Return the full ``createConversationItem`` function text from conversations.html."""
    return _extract_function(_read_template("conversations.html"), "createConversationItem")


def _load_conversations_body():
    """Return the body of the ``loadConversations`` assignment from conversations.html.

    ``loadConversations`` is assigned via
    ``window.loadConversations = async function(page = currentPage) { ... }``. The
    shared ``_extract_function`` regex (``function\\s+NAME\\s*\\``) cannot match
    that form, so we rewrite ``window.loadConversations = async function(`` into
    ``async function loadConversations(`` on a local copy and reuse
    ``_extract_function`` verbatim.
    """
    source = _read_template("conversations.html")
    transformed = source.replace(
        _LOAD_CONVERSATIONS_ASSIGNMENT + "(",
        "async function loadConversations(",
    )
    return _extract_function(transformed, "loadConversations")


# --- Static backstops (no browser needed) ------------------------------------


def test_create_conversation_item_coerces_id():
    """The id is coerced to a string before slicing; the raw id is never sliced."""
    body = _create_conversation_item_body()
    assert "const id = String(conv.conversation_id ?? '');" in body
    assert "conv.conversation_id.substring(" not in body
    assert "escapeHtml(id.substring(0, 8))" in body


def test_create_conversation_item_appends_ellipsis_only_with_id():
    """The trailing ``...`` is only rendered when the (coerced) id is non-empty."""
    body = _create_conversation_item_body()
    assert "${id ? '...' : ''}" in body


def test_load_conversations_wraps_each_item_in_try_catch():
    """Each item render is wrapped in its own try/catch, with the create call inside."""
    body = _load_conversations_body()
    assert "try {" in body
    assert "createConversationItem(conv)" in body
    assert "list.appendChild(item)" in body
    assert "catch (e)" in body
    assert "console.error('Failed to render conversation item:', e);" in body

    try_idx = body.index("try {")
    create_idx = body.index("createConversationItem(conv)")
    catch_idx = body.index("catch (e)")
    assert try_idx < create_idx < catch_idx


# --- Behavioral: run the real functions in headless Chromium ------------------


def _build_harness(driver_script):
    """Assemble an HTML page with the real escapeHtml/createConversationItem plus driver."""
    escape_html_src = _extract_from_sanitize_module("escapeHtml")
    create_item_src = _create_conversation_item_body()
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
        '<div id="out"></div><div id="list"></div>\n'
        "<script>\n"
        "function openConversation(id) { window.__opened = id; }\n"
        + escape_html_src
        + "\n"
        + create_item_src
        + "\n"
        + driver_script
        + "\n</script></body></html>"
    )


def _run_driver(driver_script, tmp_path, name):
    """Run a driver script against the real functions and return the #out JSON."""
    doc = _build_harness(driver_script)
    html_path = tmp_path / name
    html_path.write_text(doc, encoding="utf-8")
    proc = subprocess.run(
        [_CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--dump-dom", html_path.as_uri()],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    match = re.search(r'<div id="out">(.*?)</div>', proc.stdout, re.DOTALL)
    assert match, f"headless DOM dump did not contain #out: {proc.stderr[-500:]!r}"
    return json.loads(html.unescape(match.group(1)))


_ITEM_CASES_DRIVER = (
    "var cases = [\n"
    "    { label: 'missing', conv: { conversation_id: undefined, title: 'T' } },\n"
    "    { label: 'null', conv: { conversation_id: null, title: 'T' } },\n"
    "    { label: 'numeric', conv: { conversation_id: 12345678901, title: 'Numeric' } },\n"
    "    { label: 'wellformed', conv: { conversation_id: 'abcdef123456', title: 'Well formed' } }\n"
    "];\n"
    "var results = [];\n"
    "cases.forEach(function (c) {\n"
    "    var entry = { label: c.label };\n"
    "    try {\n"
    "        var item = createConversationItem(c.conv);\n"
    "        entry.threw = false;\n"
    "        entry.innerHTML = item.innerHTML;\n"
    "        entry.hasOnclick = !!item.onclick;\n"
    "        entry.className = item.className;\n"
    "    } catch (e) {\n"
    "        entry.threw = true;\n"
    "        entry.error = String(e && e.message ? e.message : e);\n"
    "    }\n"
    "    results.push(entry);\n"
    "});\n"
    "document.getElementById('out').textContent = JSON.stringify(results);"
)


@pytest.fixture(scope="module")
def _item_case_results(tmp_path_factory):
    """Render the four malformed/edge conversation cases once and cache the results."""
    if _CHROME is None:
        pytest.skip("No usable headless Chromium/Chrome binary found")
    results = _run_driver(_ITEM_CASES_DRIVER, tmp_path_factory.mktemp("convitem"), "harness_item_cases.html")
    return {r["label"]: r for r in results}


@_requires_chrome
def test_missing_id_renders_non_clickable_disabled_item(_item_case_results):
    """A missing (undefined) id never throws and renders a disabled, non-clickable card."""
    r = _item_case_results["missing"]
    assert r["threw"] is False
    assert r["hasOnclick"] is False
    assert "opacity-50" in r["className"]
    assert "cursor-not-allowed" in r["className"]
    assert "..." not in r["innerHTML"]


@_requires_chrome
def test_null_id_renders_non_clickable_disabled_item(_item_case_results):
    """A null id never throws and renders a disabled, non-clickable card."""
    r = _item_case_results["null"]
    assert r["threw"] is False
    assert r["hasOnclick"] is False
    assert "opacity-50" in r["className"]
    assert "cursor-not-allowed" in r["className"]
    assert "..." not in r["innerHTML"]


@_requires_chrome
def test_numeric_id_is_coerced_and_clickable(_item_case_results):
    """A numeric id is coerced to a string, sliced, and wired to a click handler."""
    r = _item_case_results["numeric"]
    assert r["threw"] is False
    assert r["hasOnclick"] is True
    assert "12345678" in r["innerHTML"]
    assert "..." in r["innerHTML"]
    assert "cursor-not-allowed" not in r["className"]


@_requires_chrome
def test_wellformed_id_renders_prefix_and_ellipsis(_item_case_results):
    """A well-formed id renders its 8-char prefix plus the ellipsis, and is clickable."""
    r = _item_case_results["wellformed"]
    assert r["threw"] is False
    assert r["hasOnclick"] is True
    assert "abcdef12" in r["innerHTML"]
    assert "..." in r["innerHTML"]
    assert "cursor-not-allowed" not in r["className"]


_TRY_CATCH_DRIVER = (
    "var loggedErrors = [];\n"
    "window.__loggedErrors = loggedErrors;\n"
    "console.error = function () {\n"
    "    loggedErrors.push(Array.prototype.slice.call(arguments).join(' '));\n"
    "};\n"
    "var list = document.getElementById('list');\n"
    "var convs = [\n"
    "    { title: 'good-one', conversation_id: 'aaa111' },\n"
    "    { title: 'poison', get conversation_id() { throw new Error('boom'); } },\n"
    "    { title: 'good-two', conversation_id: 'bbb222' }\n"
    "];\n"
    "convs.forEach(function (conv) {\n"
    "    try {\n"
    "        var item = createConversationItem(conv);\n"
    "        list.appendChild(item);\n"
    "    } catch (e) {\n"
    "        console.error('Failed to render conversation item:', e);\n"
    "    }\n"
    "});\n"
    "var result = {\n"
    "    loggedErrorsLength: window.__loggedErrors.length,\n"
    "    childrenLength: list.children.length,\n"
    "    titles: []\n"
    "};\n"
    "var children = list.children;\n"
    "for (var i = 0; i < children.length; i++) {\n"
    "    result.titles.push(children[i].textContent);\n"
    "}\n"
    "document.getElementById('out').textContent = JSON.stringify(result);"
)


@_requires_chrome
def test_per_item_try_catch_skips_poisoned_item(tmp_path):
    """A single throwing item is caught and skipped; its siblings still render."""
    result = _run_driver(_TRY_CATCH_DRIVER, tmp_path, "harness_try_catch.html")
    assert result["loggedErrorsLength"] == 1
    assert result["childrenLength"] == 2
    titles = result["titles"]
    assert len(titles) == 2
    assert "good-one" in titles[0]
    assert "good-two" in titles[1]
    assert all("poison" not in t for t in titles)

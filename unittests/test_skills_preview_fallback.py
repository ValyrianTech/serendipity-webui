"""Regression tests for the raw-content DOM-XSS fallback gap in skills.html.

``updateInstructionsPreview`` and ``setResourceTab`` render skill/resource
instructions into a preview pane. The ``marked`` branch sanitizes its output via
``sanitizeHtml(marked.parse(...))``, but the fallback branch (taken when the
vendored ``marked`` library is absent) concatenated raw user/agent-authored
``content`` straight into ``innerHTML``. A payload such as
``<img src=x onerror=alert(1)>`` would therefore execute when ``marked`` failed
to load.

The fix routes both fallback branches through the shared ``escapeHtml`` helper
from ``static/js/sanitize.js``. These tests pin that fix statically (the raw
concatenation is gone and ``escapeHtml`` wraps the content) and behaviorally
(running the real ``updateInstructionsPreview`` and ``escapeHtml`` in headless
Chromium with ``marked`` undefined). Coverage is measured only over Python under
``app/``, so no new Python paths are added.
"""

import html
import json
import re
import subprocess

from test_xss_sanitization import (
    _CHROME,
    _extract_from_sanitize_module,
    _extract_function,
    _read_template,
    _requires_chrome,
)


def _extract_assigned_function(source, name):
    """Extract the full text of ``window.name = function(...) {...}`` from ``source``.

    Mirrors ``_extract_function`` but anchors on an assignment target (the
    skills.html resource-preview helper is declared as
    ``window.setResourceTab = function(tab) {...}``) and brace-matches to the
    closing ``}`` so nested braces are handled correctly.
    """
    match = re.search(re.escape(name) + r"\s*=\s*function\s*\([^)]*\)\s*\{", source)
    assert match, f"assignment {name} not found in template source"
    start = match.start()
    depth = 0
    i = match.end() - 1
    while i < len(source):
        char = source[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1
    raise AssertionError(f"could not find closing brace for {name}")


# --- Static: the fallback branches escape their content ---------------------


def test_update_instructions_preview_fallback_escapes_content():
    """The instructions fallback wraps content in escapeHtml, not raw concatenation."""
    body = _extract_function(_read_template("skills.html"), "updateInstructionsPreview")
    assert "'<pre>' + (content || 'No instructions')" not in body
    assert "escapeHtml(content || 'No instructions')" in body


def test_set_resource_tab_fallback_escapes_content():
    """The resource-preview fallback wraps content in escapeHtml too."""
    body = _extract_assigned_function(_read_template("skills.html"), "setResourceTab")
    assert "'<pre>' + (content || 'No content')" not in body
    assert "escapeHtml(content || 'No content')" in body


# --- Behavioral: run the real functions in headless Chromium -----------------


def _run_update_preview(tmp_path, driver):
    """Run the real ``updateInstructionsPreview`` + ``escapeHtml`` in Chromium.

    Injects the shared ``escapeHtml`` and the template's real
    ``updateInstructionsPreview`` into a generated page, drives Chromium to
    ``--dump-dom`` it, and returns the JSON-encoded preview ``innerHTML`` that the
    driver script wrote into the ``#out`` element.
    """
    escape_src = _extract_from_sanitize_module("escapeHtml")
    fn_src = _extract_function(_read_template("skills.html"), "updateInstructionsPreview")
    doc = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
        '<div id="out"></div><script>\n'
        + escape_src
        + "\n"
        + fn_src
        + "\n"
        + driver
        + "\n</script></body></html>"
    )
    path = tmp_path / "skills_preview.html"
    path.write_text(doc, encoding="utf-8")
    proc = subprocess.run(
        [_CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--dump-dom", path.as_uri()],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    match = re.search(r'<div id="out">(.*?)</div>', proc.stdout, re.DOTALL)
    assert match, f"headless DOM dump did not contain #out: {proc.stderr[-500:]!r}"
    return json.loads(html.unescape(match.group(1)))


_FALLBACK_DRIVER = (
    "var previewEl = { innerHTML: '' };\n"
    "var instructionsEl = { value: '<img src=x onerror=alert(1)>' };\n"
    "var realGetElementById = document.getElementById.bind(document);\n"
    "document.getElementById = function(id) {\n"
    "    if (id === 'skill-instructions') return instructionsEl;\n"
    "    if (id === 'instructions-preview') return previewEl;\n"
    "    return realGetElementById(id);\n"
    "};\n"
    "updateInstructionsPreview();\n"
    "document.getElementById('out').textContent = JSON.stringify(previewEl.innerHTML);\n"
)


@_requires_chrome
def test_update_instructions_preview_fallback_escapes_payload(tmp_path):
    """With ``marked`` undefined, the real fallback escapes a hostile payload.

    The payload's ``<`` and ``>`` are entity-encoded, so it cannot form an active
    ``<img onerror=...>`` element inside the ``<pre>`` wrapper.
    """
    result = _run_update_preview(tmp_path, _FALLBACK_DRIVER)
    assert result == "<pre>&lt;img src=x onerror=alert(1)&gt;</pre>"
    assert "<pre>" in result
    assert "</pre>" in result
    assert "&lt;img" in result
    assert "<img src=x onerror" not in result


_MARKED_DRIVER = (
    "var marked = { parse: function() { return '<p>hi</p>'; } };\n"
    "function sanitizeHtml(h) { return 'SANITIZED:' + h; }\n"
    "var previewEl = { innerHTML: '', querySelectorAll: function() { return []; } };\n"
    "var instructionsEl = { value: 'whatever' };\n"
    "var realGetElementById = document.getElementById.bind(document);\n"
    "document.getElementById = function(id) {\n"
    "    if (id === 'skill-instructions') return instructionsEl;\n"
    "    if (id === 'instructions-preview') return previewEl;\n"
    "    return realGetElementById(id);\n"
    "};\n"
    "updateInstructionsPreview();\n"
    "document.getElementById('out').textContent = JSON.stringify(previewEl.innerHTML);\n"
)


@_requires_chrome
def test_update_instructions_preview_marked_branch_unchanged(tmp_path):
    """With ``marked`` defined, the sanitized marked branch still runs.

    The fallback must not shadow the ``marked`` path: output is still routed
    through ``sanitizeHtml`` (stubbed here to a recognizable prefix).
    """
    result = _run_update_preview(tmp_path, _MARKED_DRIVER)
    assert result.startswith("SANITIZED:")
    assert "<p>hi</p>" in result

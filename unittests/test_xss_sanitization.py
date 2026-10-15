"""Tests for the stored-XSS fix that sanitizes markdown/mermaid HTML output.

The fix vendors DOMPurify, loads it from the base template, and wraps every
``marked.parse`` / ``mermaid.render`` result in a ``sanitizeHtml`` helper so
rendered content cannot inject raw HTML into the served pages. Coverage is
measured only over Python code under ``app/``, so these template-content tests
prove the fix is present in the served pages without adding new Python paths.
"""

import glob
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"


def _read_template(name):
    """Return the raw text of a template file under ``app/templates``."""
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def _find_chrome():
    """Return a usable headless Chromium/Chrome binary path, or ``None``.

    Candidates are collected in priority order (env var, ``which``, snap/abs
    locations) and each is sanity-checked before being accepted: running
    ``<bin> --version`` must print something mentioning "chrom", and the binary
    must be able to actually dump a real ``file://`` page in headless mode.
    This rejects snap stubs/wrappers that answer ``--version`` but cannot load
    local files under confinement.
    """
    candidates = []
    env_bin = os.environ.get("CHROME_BIN")
    if env_bin:
        candidates.append(env_bin)
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        which = shutil.which(name)
        if which:
            candidates.append(which)
    candidates.extend(sorted(glob.glob("/snap/chromium/*/usr/lib/chromium-browser/chrome")))
    candidates.append("/snap/chromium/current/usr/lib/chromium-browser/chrome")
    candidates.extend(("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"))

    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if _is_usable_chrome(candidate):
            return candidate
    return None


def _is_usable_chrome(binary):
    """Return True if ``binary`` is a real Chromium that can dump local pages."""
    if not os.path.isfile(binary):
        return False
    try:
        version_proc = subprocess.run([binary, "--version"], check=False, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    version_output = (version_proc.stdout or "") + (version_proc.stderr or "")
    if "chrom" not in version_output.lower():
        return False

    fd, probe_path = tempfile.mkstemp(suffix=".html")
    os.close(fd)
    probe = Path(probe_path)
    try:
        probe.write_text("<!DOCTYPE html><html><body>chromok</body></html>", encoding="utf-8")
        dump_proc = subprocess.run(
            [binary, "--headless=new", "--no-sandbox", "--disable-gpu", "--dump-dom", probe.as_uri()],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    finally:
        probe.unlink(missing_ok=True)
    return "chromok" in dump_proc.stdout


def _extract_function(source, name):
    """Extract the full text of ``function name(...) {...}`` from ``source``.

    Locates the function signature with a regex and then brace-matches to the
    matching closing ``}`` so the function body is captured verbatim (nested
    braces in object literals are handled correctly).
    """
    match = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", source)
    assert match, f"function {name} not found in template source"
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
    raise AssertionError(f"could not find closing brace for function {name}")


def _sanitize_with_template(template_name, payloads, tmp_path):
    """Run the template's real ``sanitizeHtml`` in headless Chromium.

    Extracts ``escapeHtml``/``sanitizeHtml`` from ``template_name``, injects
    them alongside the vendored DOMPurify into a generated HTML page, drives
    Chromium to ``--dump-dom`` the page, and returns the JSON payload of
    ``{payload: cleaned}`` produced by the driver script.
    """
    template = _read_template(template_name)
    escape_html_src = _extract_function(template, "escapeHtml")
    sanitize_html_src = _extract_function(template, "sanitizeHtml")

    purify_path = Path(__file__).resolve().parent.parent / "static" / "js" / "vendor" / "purify.min.js"
    purify_url = purify_path.as_uri()

    payloads_json = json.dumps(payloads).replace("</", "<\\/")
    driver = (
        "var payloads = " + payloads_json + ";\n"
        "var results = {};\n"
        "payloads.forEach(function (p) { results[p] = sanitizeHtml(p); });\n"
        'results["__dompurify_supported__"] = '
        '(typeof DOMPurify !== "undefined") && DOMPurify.isSupported === true;\n'
        'document.getElementById("out").textContent = JSON.stringify(results);'
    )

    doc = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><div id="out"></div>\n'
        f'<script src="{purify_url}"></script>\n'
        "<script>\n"
        + escape_html_src
        + "\n"
        + sanitize_html_src
        + "\n"
        + driver
        + "\n</script></body></html>"
    )

    html_path = tmp_path / ("harness_" + template_name)
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


_BEHAVIORAL_PAYLOADS = [
    "<img src=x onerror=alert(1)>",
    '<a href="javascript:alert(1)">x</a>',
    "<svg/onload=alert(1)>",
    "<p>Hello <strong>world</strong></p>",
]

_BEHAVIORAL_TEMPLATES = ("conversation.html", "skills.html")

_CHROME = _find_chrome()

_requires_chrome = pytest.mark.skipif(
    _CHROME is None,
    reason="No usable headless Chromium/Chrome binary found; behavioral XSS sanitization tests require a real browser",
)


@pytest.fixture(scope="module")
def _behavioral_results(tmp_path_factory):
    """Run the real sanitizer for both templates once and cache the results."""
    if _CHROME is None:
        pytest.skip("No usable headless Chromium/Chrome binary found")
    results = {}
    for name in _BEHAVIORAL_TEMPLATES:
        results[name] = _sanitize_with_template(name, _BEHAVIORAL_PAYLOADS, tmp_path_factory.mktemp("xss"))
    return results


def test_purify_vendor_asset_exists():
    """The DOMPurify vendor asset exists, is non-empty, and is real DOMPurify."""
    purify_path = Path(__file__).resolve().parent.parent / "static" / "js" / "vendor" / "purify.min.js"
    assert purify_path.exists(), "purify.min.js is missing"
    content = purify_path.read_text(encoding="utf-8")
    assert content.strip(), "purify.min.js is empty"
    assert "DOMPurify" in content


def test_base_template_loads_dompurify(client):
    """The base template loads the DOMPurify script on every page."""
    response = client.get("/")
    assert response.status_code == 200
    assert '<script src="/static/js/vendor/purify.min.js"></script>' in response.text


def test_conversation_template_has_sanitize_helper():
    """conversation.html defines a sanitizeHtml helper backed by DOMPurify."""
    content = _read_template("conversation.html")
    assert "function sanitizeHtml(" in content
    assert "DOMPurify.sanitize(" in content


def test_no_unwrapped_marked_parse_in_conversation():
    """Every marked.parse call in conversation.html is wrapped by sanitizeHtml."""
    content = _read_template("conversation.html")
    total = content.count("marked.parse(")
    wrapped = content.count("sanitizeHtml(marked.parse(")
    assert total > 0
    assert total == wrapped


def test_no_unwrapped_marked_parse_in_skills():
    """Every marked.parse call in skills.html is wrapped by sanitizeHtml."""
    content = _read_template("skills.html")
    total = content.count("marked.parse(")
    wrapped = content.count("sanitizeHtml(marked.parse(")
    assert total > 0
    assert total == wrapped


def test_mermaid_render_output_is_sanitized():
    """Both mermaid.render sites sanitize the SVG before inserting it."""
    content = _read_template("conversation.html")
    assert content.count("el.innerHTML = sanitizeHtml(svg);") == 2
    assert "el.innerHTML = svg;" not in content


def test_existing_escape_html_still_present():
    """The escapeHtml helper remains as a regression guard."""
    content = _read_template("conversation.html")
    assert "function escapeHtml(" in content
    assert len(re.findall(r"function escapeHtml\(", content)) == 1


def test_skills_sanitize_helper_falls_back_to_escaping():
    """skills.html's sanitizeHtml falls back to escaping, never returning raw HTML."""
    content = _read_template("skills.html")
    assert "function escapeHtml(" in content
    assert "function sanitizeHtml(" in content
    assert "DOMPurify.sanitize(" in content
    assert "return escapeHtml(html);" in content
    assert "return html;" not in content


def test_both_templates_sanitizer_fallbacks_are_consistent():
    """Both conversation.html and skills.html fail closed via escapeHtml."""
    for name in ("conversation.html", "skills.html"):
        content = _read_template(name)
        assert "return escapeHtml(html);" in content


@_requires_chrome
@pytest.mark.parametrize("template", _BEHAVIORAL_TEMPLATES)
def test_dompurify_loaded_and_supported_in_browser(template, _behavioral_results):
    """DOMPurify is actually loaded and reports isSupported inside the browser."""
    assert _behavioral_results[template]["__dompurify_supported__"] is True


@_requires_chrome
@pytest.mark.parametrize("template", _BEHAVIORAL_TEMPLATES)
def test_sanitize_html_neuters_img_onerror(template, _behavioral_results):
    """The onerror handler is stripped while the img tag is preserved."""
    payload = "<img src=x onerror=alert(1)>"
    cleaned = _behavioral_results[template][payload]
    assert "onerror" in payload.lower(), "positive control: raw payload must contain onerror"
    assert "onerror" not in cleaned.lower(), f"onerror handler survived sanitization: {cleaned!r}"
    assert "alert(1)" not in cleaned.lower(), f"alert payload survived sanitization: {cleaned!r}"
    assert "<img" in cleaned, f"img tag was dropped instead of just neutralized: {cleaned!r}"


@_requires_chrome
@pytest.mark.parametrize("template", _BEHAVIORAL_TEMPLATES)
def test_sanitize_html_strips_javascript_uri(template, _behavioral_results):
    """The javascript: URI scheme is removed but the link text survives."""
    payload = '<a href="javascript:alert(1)">x</a>'
    cleaned = _behavioral_results[template][payload]
    assert "javascript:" in payload.lower(), "positive control: raw payload must contain javascript:"
    assert "javascript:" not in cleaned.lower(), f"javascript: URI survived sanitization: {cleaned!r}"
    assert "x" in cleaned, f"link text was dropped: {cleaned!r}"


@_requires_chrome
@pytest.mark.parametrize("template", _BEHAVIORAL_TEMPLATES)
def test_sanitize_html_strips_svg_onload(template, _behavioral_results):
    """The svg onload handler is stripped."""
    payload = "<svg/onload=alert(1)>"
    cleaned = _behavioral_results[template][payload]
    assert "onload" in payload.lower(), "positive control: raw payload must contain onload"
    assert "onload" not in cleaned.lower(), f"onload handler survived sanitization: {cleaned!r}"


@_requires_chrome
@pytest.mark.parametrize("template", _BEHAVIORAL_TEMPLATES)
def test_sanitize_html_preserves_benign_html(template, _behavioral_results):
    """Benign formatting markup is preserved by the sanitizer."""
    payload = "<p>Hello <strong>world</strong></p>"
    cleaned = _behavioral_results[template][payload]
    assert "<p>" in cleaned, f"<p> tag was dropped: {cleaned!r}"
    assert "<strong>" in cleaned, f"<strong> tag was dropped: {cleaned!r}"

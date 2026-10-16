"""Tests for the ``getTextOnlyContent`` sanitization fix in conversation.html.

``getTextOnlyContent(content)`` parses arbitrary content on a detached ``div``
via ``innerHTML`` to strip markup before TTS playback. Parsing untrusted HTML --
even on a detached node -- lets resource-loading tags and event handlers fire
(e.g. an ``<img onerror=...>`` requests a URL when the element is created), so
the function must route its input through the existing ``sanitizeHtml`` helper
before assigning to ``div.innerHTML``.

These tests mirror the existing XSS suites: a static check proves the sanitizer
call is present in the served template, and a behavioral check runs the real
``getTextOnlyContent`` (backed by the vendored DOMPurify) in headless Chromium.
"""

import html as html_module
import json
import re
import subprocess
from pathlib import Path

from test_xss_sanitization import (
    _CHROME,
    _extract_function,
    _read_template,
    _requires_chrome,
)

_TTS_PAYLOADS = [
    "<img src=x onerror=alert(1)>Hello",
    "<svg/onload=alert(1)>World",
    "<p>Hi <strong>there</strong></p>",
    '<iframe src="javascript:alert(1)"></iframe>Text',
]


def test_gettextonlycontent_uses_sanitize_html():
    """getTextOnlyContent sanitizes its input before assigning to innerHTML."""
    content = _read_template("conversation.html")
    body = _extract_function(content, "getTextOnlyContent")
    assert "sanitizeHtml(content)" in body
    assert "div.innerHTML = content;" not in body
    assert "div.innerHTML = sanitizeHtml(content);" in body


@_requires_chrome
def test_gettextonlycontent_strips_active_markup(tmp_path):
    """The real getTextOnlyContent returns plain text with active markup neutered."""
    content = _read_template("conversation.html")
    escape_html_src = _extract_function(content, "escapeHtml")
    sanitize_html_src = _extract_function(content, "sanitizeHtml")
    get_text_only_src = _extract_function(content, "getTextOnlyContent")

    purify_path = Path(__file__).resolve().parent.parent / "static" / "js" / "vendor" / "purify.min.js"
    purify_url = purify_path.as_uri()

    payloads_json = json.dumps(_TTS_PAYLOADS).replace("</", "<\\/")
    driver = (
        "var payloads = " + payloads_json + ";\n"
        "var results = {};\n"
        "payloads.forEach(function (p) { results[p] = getTextOnlyContent(p); });\n"
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
        + get_text_only_src
        + "\n"
        + driver
        + "\n</script></body></html>"
    )

    html_path = tmp_path / "harness_gettextonlycontent.html"
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
    results = json.loads(html_module.unescape(match.group(1)))

    for payload in _TTS_PAYLOADS:
        text = results[payload]
        assert "<" not in text, (payload, text)
        assert ">" not in text, (payload, text)

    for payload, trailing in ((_TTS_PAYLOADS[0], "Hello"), (_TTS_PAYLOADS[1], "World")):
        text = results[payload]
        assert "onerror" not in text.lower(), (payload, text)
        assert "onload" not in text.lower(), (payload, text)
        assert trailing in text, (payload, text)

    benign = results["<p>Hi <strong>there</strong></p>"]
    assert "Hi" in benign
    assert "there" in benign

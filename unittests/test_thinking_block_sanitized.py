"""Regression tests for the thinking-block rendering fix.

The three message-rendering call sites (``createMessageElement`` raw branch,
``renderMessagePart`` text case, ``handleStreamingMessage``) previously split
``processThinkingTags()`` output on a fragile regex and injected the matched
``<div class="thinking-block ...">`` chunk verbatim into ``innerHTML`` WITHOUT
``sanitizeHtml``. That was safe only because ``processThinkingTags`` happened to
``escapeHtml`` the inner text and generate an alphanumeric id - a cross-function
invariant enforced nowhere at the injection site, duplicated three times.

The fix routes every fragment (thinking blocks included) through ``sanitizeHtml``
inside a single shared ``renderMarkdownWithThinking()`` helper, and moves the
collapse/expand button from an inline ``onclick`` to a ``data-thinking-toggle``
attribute plus ``bindThinkingToggles()`` listeners (because DOMPurify strips
inline event handlers).

Coverage is measured only over Python under ``app/``; these template/JS tests do
not add Python lines."""

import html
import json
import re
import subprocess
from pathlib import Path

import pytest
from test_xss_sanitization import (
    _CHROME,
    _extract_from_sanitize_module,
    _extract_function,
    _read_template,
    _requires_chrome,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
MARKED_PATH = REPO_ROOT / "static" / "js" / "vendor" / "marked.min.js"
PURIFY_PATH = REPO_ROOT / "static" / "js" / "vendor" / "purify.min.js"


_SPLIT_RE_LINE = re.compile(r"^\s*const THINKING_BLOCK_SPLIT_RE = .*$", re.MULTILINE)


def _extract_split_re_const():
    """Return the ``const THINKING_BLOCK_SPLIT_RE = /.../;`` declaration line."""
    match = _SPLIT_RE_LINE.search(_read_template("conversation.html"))
    assert match, "THINKING_BLOCK_SPLIT_RE declaration not found"
    return match.group(0)


# --- Static: the fix is present in conversation.html ------------------------


def test_no_duplicated_thinking_split_sites_remain():
    """The fragile inline split regex is gone; a single constant owns it."""
    content = _read_template("conversation.html")
    # The regex text now lives once, in the named constant.
    assert '<div class="thinking-block[\\s\\S]*?<\\/div>\\s*<\\/div>' in content
    # No call site splits on an inline regex literal any more.
    assert "split(/(<div class=\"thinking-block" not in content
    # Exactly one split call, inside the shared helper.
    assert content.count(".split(THINKING_BLOCK_SPLIT_RE)") == 1
    assert content.count("const THINKING_BLOCK_SPLIT_RE =") == 1


def test_three_sites_use_shared_helper():
    """All three former split sites now call renderMarkdownWithThinking."""
    content = _read_template("conversation.html")
    assert content.count("renderMarkdownWithThinking(processedContent)") >= 3
    # return (renderMessagePart), innerHTML assignment (handleStreamingMessage),
    # contentHtml assignment (createMessageElement).
    assert "return renderMarkdownWithThinking(processedContent);" in content
    assert "contentEl.innerHTML = renderMarkdownWithThinking(processedContent);" in content
    assert "contentHtml = renderMarkdownWithThinking(processedContent);" in content


def test_helper_sanitizes_thinking_fragment():
    """The shared helper passes the thinking-block branch through sanitizeHtml."""
    body = _extract_function(_read_template("conversation.html"), "renderMarkdownWithThinking")
    # The thinking-block branch must sanitize (not return p raw).
    assert "p.startsWith('<div class=\"thinking-block')" in body
    assert "return sanitizeHtml(p);" in body
    # The old raw-return of the thinking chunk must be gone.
    assert "return p;" not in body


def test_process_thinking_uses_data_attribute_not_inline_onclick():
    """processThinkingTags emits data-thinking-toggle instead of inline onclick."""
    body = _extract_function(_read_template("conversation.html"), "processThinkingTags")
    assert 'data-thinking-toggle="${uniqueId}"' in body
    assert "onclick=\"toggleThinking(" not in body


def test_bind_thinking_toggles_helper_present():
    """A helper binds data-thinking-toggle buttons via addEventListener."""
    body = _extract_function(_read_template("conversation.html"), "bindThinkingToggles")
    assert "querySelectorAll('[data-thinking-toggle]')" in body
    assert "addEventListener('click'" in body
    assert "toggleThinking(btn.dataset.thinkingToggle)" in body


def test_thinking_toggles_bound_after_render():
    """createMessageElement and handleStreamingMessage bind toggles after innerHTML."""
    content = _read_template("conversation.html")
    create_body = _extract_function(content, "createMessageElement")
    assert "bindThinkingToggles(div);" in create_body
    stream_body = _extract_function(content, "handleStreamingMessage")
    assert "bindThinkingToggles(contentEl);" in stream_body


def test_all_marked_parse_still_sanitized():
    """No bare marked.parse() was introduced by the refactor."""
    content = _read_template("conversation.html")
    assert content.count("marked.parse(") == content.count("sanitizeHtml(marked.parse(")


# --- Behavioral: run the real helper in headless Chromium -------------------


def _run_thinking_harness(tmp_path, processed_content):
    """Run the real renderMarkdownWithThinking in headless Chromium.

    Extracts the real ``renderMarkdownWithThinking`` / ``bindThinkingToggles``
    functions plus the shared ``escapeHtml``/``sanitizeHtml`` and loads the
    vendored DOMPurify + marked. Renders ``processed_content`` through the real
    helper and returns a dict describing the resulting DOM.
    """
    template = _read_template("conversation.html")
    render_src = _extract_function(template, "renderMarkdownWithThinking")
    split_const = _extract_split_re_const()
    bind_src = _extract_function(template, "bindThinkingToggles")
    escape_src = _extract_from_sanitize_module("escapeHtml")
    sanitize_src = _extract_from_sanitize_module("sanitizeHtml")

    payload_json = json.dumps(processed_content).replace("</", "<\\/")
    driver = (
        "var processedContent = " + payload_json + ";\n"
        "function toggleThinking(id) { window.__toggled = id; }\n"
        "var container = document.getElementById('out');\n"
        "container.innerHTML = renderMarkdownWithThinking(processedContent);\n"
        "bindThinkingToggles(container);\n"
        "var btn = container.querySelector('[data-thinking-toggle]');\n"
        "var result = {\n"
        "  html: container.innerHTML,\n"
        "  has_button: !!btn,\n"
        "  has_svg: !!container.querySelector('.thinking-block svg'),\n"
        "  has_live_onerror: !!container.querySelector('[onerror]'),\n"
        "  button_data: btn ? btn.getAttribute('data-thinking-toggle') : null\n"
        "};\n"
        "document.getElementById('result').textContent = JSON.stringify(result);"
    )

    doc = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
        '<div id="out"></div><div id="result"></div>'
        f'<script src="{PURIFY_PATH.as_uri()}"></script>'
        f'<script src="{MARKED_PATH.as_uri()}"></script>'
        "<script>\n"
        + escape_src
        + "\n"
        + sanitize_src
        + "\n"
        + split_const
        + "\n"
        + render_src
        + "\n"
        + bind_src
        + "\n"
        + driver
        + "\n</script></body></html>"
    )
    html_path = tmp_path / "thinking.html"
    html_path.write_text(doc, encoding="utf-8")
    proc = subprocess.run(
        [_CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--dump-dom", html_path.as_uri()],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    match = re.search(r'<div id="result">(.*?)</div>', proc.stdout, re.DOTALL)
    assert match, f"headless DOM dump did not contain #result: {proc.stderr[-500:]!r}"
    return json.loads(html.unescape(match.group(1)))


@_requires_chrome
def test_rendered_thinking_block_is_sanitized(tmp_path):
    """A hostile thinking-block-shaped fragment has its event handler stripped.

    Simulates a compromised/future ``processThinkingTags`` that emits unescaped
    markup inside a thinking block: the shared helper must still sanitize it.
    """
    hostile = (
        '<div class="thinking-block mb-3">'
        '<button data-thinking-toggle="abc123" class="flex">'
        '<svg class="w-4 h-4"><path d="M9 5l7 7"/></svg>Thinking...'
        '</button>'
        '<div id="abc123" class="thinking-content hidden">'
        '<img src=x onerror="alert(1)">danger</div>'
        '</div></div>'
    )
    result = _run_thinking_harness(tmp_path, hostile)
    # The onerror handler is stripped...
    assert result["has_live_onerror"] is False, result["html"]
    assert "onerror" not in result["html"].lower(), result["html"]
    # ...but the collapse/expand button and its svg survive.
    assert result["has_button"] is True, result["html"]
    assert result["has_svg"] is True, result["html"]
    assert result["button_data"] == "abc123", result


@_requires_chrome
def test_rendered_real_think_block_sanitized(tmp_path):
    """A real <think> tag renders a safe, still-collapsible thinking block."""
    template = _read_template("conversation.html")
    process_src = _extract_function(template, "processThinkingTags")
    # Run processThinkingTags in a tiny separate harness so its output feeds the
    # render helper exactly as the app does.
    escape_src = _extract_from_sanitize_module("escapeHtml")
    raw = "before <think>secret <img src=x onerror=alert(1)> plan</think> after"
    payload_json = json.dumps(raw).replace("</", "<\\/")
    # Reuse the render harness by pre-processing here is not possible (JS), so
    # build a combined harness.
    render_src = _extract_function(template, "renderMarkdownWithThinking")
    split_const = _extract_split_re_const()
    bind_src = _extract_function(template, "bindThinkingToggles")
    sanitize_src = _extract_from_sanitize_module("sanitizeHtml")
    driver = (
        "var rawContent = " + payload_json + ";\n"
        "function toggleThinking(id) { window.__toggled = id; }\n"
        "var container = document.getElementById('out');\n"
        "var processed = processThinkingTags(rawContent, false);\n"
        "container.innerHTML = renderMarkdownWithThinking(processed);\n"
        "bindThinkingToggles(container);\n"
        "var btn = container.querySelector('[data-thinking-toggle]');\n"
        "var result = {\n"
        "  html: container.innerHTML,\n"
        "  has_button: !!btn,\n"
        "  has_svg: !!container.querySelector('.thinking-block svg'),\n"
        "  has_live_onerror: !!container.querySelector('[onerror]'),\n"
        "  button_data: btn ? btn.getAttribute('data-thinking-toggle') : null\n"
        "};\n"
        "document.getElementById('result').textContent = JSON.stringify(result);"
    )
    doc = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
        '<div id="out"></div><div id="result"></div>'
        f'<script src="{PURIFY_PATH.as_uri()}"></script>'
        f'<script src="{MARKED_PATH.as_uri()}"></script>'
        "<script>\n"
        + escape_src + "\n"
        + sanitize_src + "\n"
        + split_const + "\n"
        + process_src + "\n"
        + render_src + "\n"
        + bind_src + "\n"
        + driver
        + "\n</script></body></html>"
    )
    html_path = tmp_path / "thinking_real.html"
    html_path.write_text(doc, encoding="utf-8")
    proc = subprocess.run(
        [_CHROME, "--headless=new", "--no-sandbox", "--disable-gpu", "--dump-dom", html_path.as_uri()],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    match = re.search(r'<div id="result">(.*?)</div>', proc.stdout, re.DOTALL)
    assert match, f"headless DOM dump did not contain #result: {proc.stderr[-500:]!r}"
    result = json.loads(html.unescape(match.group(1)))
    assert result["has_live_onerror"] is False, result["html"]
    assert result["has_button"] is True, result["html"]
    assert result["has_svg"] is True, result["html"]
    assert "secret" in result["html"], result["html"]

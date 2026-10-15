"""Tests for the stored-XSS fix that sanitizes markdown/mermaid HTML output.

The fix vendors DOMPurify, loads it from the base template, and wraps every
``marked.parse`` / ``mermaid.render`` result in a ``sanitizeHtml`` helper so
rendered content cannot inject raw HTML into the served pages. Coverage is
measured only over Python code under ``app/``, so these template-content tests
prove the fix is present in the served pages without adding new Python paths.
"""

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"


def _read_template(name):
    """Return the raw text of a template file under ``app/templates``."""
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


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

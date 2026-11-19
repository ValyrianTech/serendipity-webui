"""Static template-source tests for the conversation-list pagination render-error fix.

The conversations page renders each conversation item in a per-item ``try``
block. Previously, when *every* item on a page threw while rendering, the page
showed neither the (now empty) list nor the empty state nor the pagination, so a
user landing on a wholly-malformed page had no visible error and no way to page
to an earlier (valid) page. The fix adds a dedicated render-error notice and
keeps the pagination controls visible whenever the page actually had raw data
but rendered zero items, so the user can navigate away instead of being stuck.

These are frontend-only changes inside the inline ES module of
``app/templates/conversations.html``. Coverage is measured only over Python
under ``app/``, so these tests are pure static template-source checks (no
browser, no new Python paths).
"""

import re

from test_xss_sanitization import _extract_function, _read_template

_LOAD_CONVERSATIONS_ASSIGNMENT = "window.loadConversations = async function"


def _brace_match(source, open_index):
    """Return the index just past the ``}`` matching the ``{`` at ``open_index``.

    Naive brace counting is sufficient here: the ``loadConversations`` body
    contains no braces inside string/template literals.
    """
    assert source[open_index] == "{"
    depth = 0
    i = open_index
    while i < len(source):
        char = source[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise AssertionError("unbalanced braces in source")


def _load_conversations_body():
    """Return the body of the ``loadConversations`` assignment from conversations.html.

    ``loadConversations`` is assigned via
    ``window.loadConversations = async function(page = currentPage) { ... }``. The
    shared ``_extract_function`` regex (``function\\s+NAME\\s*\\``) cannot match
    that form because the name precedes ``=`` rather than following ``function``.
    We rewrite ``window.loadConversations = async function(`` into
    ``async function loadConversations(`` on a local copy and then reuse
    ``_extract_function`` verbatim.
    """
    source = _read_template("conversations.html")
    transformed = source.replace(
        _LOAD_CONVERSATIONS_ASSIGNMENT + "(",
        "async function loadConversations(",
    )
    return _extract_function(transformed, "loadConversations")


def _extract_div(source, element_id):
    """Return the ``<div id="...">...</div>`` text for ``element_id``, or ``None``."""
    match = re.search(
        r'<div id="' + re.escape(element_id) + r'"[^>]*>.*?</div>',
        source,
        re.DOTALL,
    )
    return match.group(0) if match else None


def test_render_error_notice_element_exists():
    """The render-error notice exists and is distinct from the empty state."""
    content = _read_template("conversations.html")
    assert 'id="conversations-render-error"' in content
    assert 'id="conversations-empty"' in content

    render_error = _extract_div(content, "conversations-render-error")
    assert render_error is not None
    assert "No conversations yet" not in render_error


def test_load_conversations_hides_render_error_on_start():
    """loadConversations grabs the notice and resets (hides) it on every load."""
    body = _load_conversations_body()
    assert "const renderError = document.getElementById('conversations-render-error');" in body
    assert "renderError.classList.add('hidden');" in body


def test_all_items_failed_shows_render_error_and_keeps_pagination():
    """When every item fails to render, show the notice but keep pagination.

    The all-failed ``else`` branch (the one paired with ``if (renderedCount > 0)``)
    must both reveal the render-error notice and re-render pagination, and it
    must NOT reveal the empty state (that belongs to the genuinely-empty page).
    """
    body = _load_conversations_body()
    assert "const hasRawItems = data.conversations_info && data.conversations_info.length > 0;" in body
    assert "const renderedCount = list.children.length;" in body

    if_start = body.index("if (renderedCount > 0) {")
    if_open = body.index("{", if_start)
    if_end = _brace_match(body, if_open)

    remainder = body[if_end:]
    else_idx = remainder.index("else")
    else_open = if_end + remainder.index("{", else_idx)
    else_end = _brace_match(body, else_open)
    else_block = body[else_open:else_end]

    assert "renderError.classList.remove('hidden');" in else_block
    assert "renderPagination(data.pagination);" in else_block
    assert "empty.classList.remove('hidden');" not in else_block


def test_empty_page_beyond_first_keeps_pagination():
    """A genuinely empty page shows the empty state and keeps multi-page pagination."""
    body = _load_conversations_body()
    assert "empty.classList.remove('hidden');" in body
    assert "if (data.pagination && data.pagination.total_pages > 1) {" in body
    assert "renderPagination(data.pagination);" in body


def test_render_pagination_still_hides_for_single_page():
    """renderPagination still hides the controls for single-page/no pagination."""
    body = _extract_function(_read_template("conversations.html"), "renderPagination")
    assert "if (!pagination || pagination.total_pages <= 1) {" in body
    assert "container.classList.add('hidden');" in body
    assert "return;" in body


def test_guardrail_no_unescaped_innerhtml_in_new_notice():
    """The render-error notice is static text with no ``${...}`` interpolation."""
    content = _read_template("conversations.html")
    match = re.search(
        r'<div id="conversations-render-error"[^>]*>.*?</div>',
        content,
        re.DOTALL,
    )
    assert match, "conversations-render-error div not found"
    assert "${" not in match.group(0)

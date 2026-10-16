"""Regression tests for the stored/DOM-XSS escaping fix on ``innerHTML`` sites.

Several templates build DOM via ``innerHTML`` template literals and (previously)
interpolated server-provided values (agent/conversation/task/tool/role/skill
names, descriptions, cron expressions, task ids, etc.) directly. A value such as
``<img src=x onerror=alert(1)>`` or ``');alert(1)//`` would execute when the page
rendered it, and inline ``onclick="fn('${value}')"`` handlers were doubly
dangerous (a quote broke out of the attribute/JS string).

The fix escapes every such value with the per-template ``escapeHtml`` helper and
replaces data-driven inline handlers with ``dataset`` + ``addEventListener``.

These tests are static-content checks (they prove the fix is present in the
served templates) plus a behavioral check that runs each template's real
``escapeHtml`` in headless Chromium. Coverage is measured only over Python under
``app/`` so no new Python paths are added."""

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

# --- Static: affected templates import the shared escapeHtml helper ---------

_TEMPLATES_WITH_HELPER = (
    "agents.html",
    "conversations.html",
    "scheduled_tasks.html",
    "conversation.html",
    "agent_home.html",
    "workflows.html",
    "edit_agent.html",
    "mcps.html",
    "toolsets.html",
    "skills.html",
    "llms.html",
)


@pytest.mark.parametrize("template", _TEMPLATES_WITH_HELPER)
def test_template_has_no_inline_escape_html(template):
    """Each affected template defines no inline escapeHtml and imports the shared module."""
    content = _read_template(template)
    assert len(re.findall(r"function escapeHtml\(", content)) == 0, template
    assert "import { escapeHtml, sanitizeHtml } from '/static/js/sanitize.js';" in content, template


# --- Static: specific sites escape their data -------------------------------


def _func_body(template, name):
    return _extract_function(_read_template(template), name)


def test_agents_card_escapes_agent_fields():
    """createAgentCard escapes name/description/product image and drops raw onerror."""
    body = _func_body("agents.html", "createAgentCard")
    assert "escapeHtml(agent.name)" in body
    assert "escapeHtml(agent.description || 'No description')" in body
    assert "escapeHtml(profileImage)" in body
    # The old inline-onerror string-injection must be gone.
    assert "onerror=" not in body


def test_conversations_item_escapes_title():
    """createConversationItem escapes the conversation title."""
    body = _func_body("conversations.html", "createConversationItem")
    assert "escapeHtml(conv.title || 'Untitled Conversation')" in body


def test_scheduled_task_item_escapes_fields_and_drops_inline_handlers():
    """createTaskItem escapes fields and no longer builds inline handlers from task_id."""
    body = _func_body("scheduled_tasks.html", "createTaskItem")
    assert "escapeHtml(task.name || 'Unnamed Task')" in body
    assert "escapeHtml(task.cron_expression)" in body
    assert "escapeHtml(task.description || 'No description')" in body
    assert "escapeHtml(task.agent)" in body
    # No inline handler interpolating the (server-provided) task id.
    assert "onclick=\"runTaskNow('" not in body
    assert "onclick=\"editTask('" not in body
    assert "onclick=\"showDeleteDialog('" not in body
    assert "addEventListener('click'" in body


def test_scheduled_task_id_never_in_raw_inline_handler():
    """scheduled_tasks.html no longer builds inline handlers from task.task_id."""
    content = _read_template("scheduled_tasks.html")
    assert "onclick=\"runTaskNow('${task.task_id}')\"" not in content
    assert "onclick=\"editTask('${task.task_id}')\"" not in content
    assert "onclick=\"showDeleteDialog('${task.task_id}')\"" not in content
    assert "onclick=\"toggleTaskEnabled('${task.task_id}'" not in content


def test_scheduled_selected_chips_escape_and_use_dataset():
    """renderSelected{Tools,Roles,Skills} escape names and use dataset listeners."""
    for func, data_attr in (
        ("renderSelectedTools", "data-tool-name"),
        ("renderSelectedRoles", "data-role-name"),
        ("renderSelectedSkills", "data-skill-name"),
    ):
        body = _func_body("scheduled_tasks.html", func)
        assert data_attr + '="${escapeHtml(' in body, func
        assert "addEventListener('click'" in body, func
        # No inline onclick built from the chip name.
        assert "onclick=\"remove" not in body, func


def test_conversation_lists_escape_ids_and_descriptions():
    """update{Tools,Roles,Skills}List escape values and the skill description."""
    content = _read_template("conversation.html")
    for func in ("updateToolsList", "updateRolesList", "updateSkillsList"):
        body = _extract_function(content, func)
        assert "escapeHtml(" in body, func
        # A bare unescaped value attribute must not remain.
        assert 'value="${tool}"' not in body, func
        assert 'value="${roleName}"' not in body, func
        assert 'value="${skillName}"' not in body, func
    skills_body = _extract_function(content, "updateSkillsList")
    assert "escapeHtml(description)" in skills_body


def test_conversation_message_sinks_are_escaped():
    """conversation.html escapes message-derived data and drops inline handlers."""
    content = _read_template("conversation.html")
    body = _extract_function(content, "createMessageElement")
    # Image URLs and avatar initials no longer injected raw.
    assert "escapeHtml(imageUrl)" in body
    assert "escapeHtml(imgUrl)" in body
    assert "escapeHtml(initials)" in body
    # Tool-request buttons no longer build inline handlers from message_id.
    assert "onclick=\"handleToolRequest('" not in body
    assert "data-tool-request=" in body
    assert "addEventListener('click'" in body
    # play-audio buttons use data-* attributes + addEventListener, no inline handlers.
    assert "onclick=\"event.stopPropagation(); playMessageAudio" not in body
    assert 'data-play-audio="1"' in body
    assert "btn.dataset.messageId" in body
    assert "btn.dataset.sender" in body


def test_conversation_staged_and_open_files_escape_file_names():
    """conversation.html escapes file names in staged and open file lists."""
    staged_body = _func_body("conversation.html", "updateStagedFilesDisplay")
    assert "escapeHtml(file.fileName || file)" in staged_body
    assert "${file.fileName || file}" not in staged_body

    open_body = _func_body("conversation.html", "updateOpenFilesList")
    assert "escapeHtml(file.name || file.filename" in open_body


def test_agent_home_escapes_tools_participants_voices():
    """agent_home.html escapes tools/participants/voices and drops raw onerror."""
    content = _read_template("agent_home.html")
    body = _extract_function(content, "loadAgentDetails")
    assert "escapeHtml(tool)" in body
    assert "escapeHtml(p)" in body
    assert "escapeHtml(voice)" in body
    assert "escapeHtml(imgUrl)" in body
    assert "onerror=" not in body


def test_workflows_checkboxes_escape_names():
    """The workflow step role/tool checkboxes escape their values."""
    content = _read_template("workflows.html")
    assert 'value="${escapeHtml(roleName)}"' in content
    assert '<span>${escapeHtml(roleName)}</span>' in content
    assert 'value="${escapeHtml(tool)}"' in content
    assert '<span>${escapeHtml(tool)}</span>' in content


def test_mcps_and_toolsets_escape_options():
    """mcps/toolsets escape their server/toolset option values."""
    mcps = _read_template("mcps.html")
    assert "${escapeHtml(s.name || s)}" in mcps
    toolsets = _read_template("toolsets.html")
    assert "${escapeHtml(t)}" in toolsets
    assert "escapeHtml(t.name)" in toolsets
    assert "escapeHtml(t.description || '')" in toolsets


def test_skills_dropdown_escapes_name():
    """skills.html escapes the skill name in its dropdown option."""
    content = _read_template("skills.html")
    assert "escapeHtml(s.name)" in content


def test_edit_agent_escapes_and_uses_dataset():
    """edit_agent.html escapes names and converts tool/role/skill toggles to dataset."""
    content = _read_template("edit_agent.html")
    assert "escapeHtml(tool)" in content
    assert "escapeHtml(toolset)" in content
    assert "escapeHtml(p)" in content
    assert "escapeHtml(v)" in content
    assert "escapeHtml(name)" in content
    # Data-driven inline onchange handlers were replaced by dataset listeners.
    assert "onchange=\"toggleToolAutoAllow('" not in content
    assert "onchange=\"toggleToolDefault('" not in content
    assert 'data-tool-auto-allow="' in content
    assert 'data-role-default="' in content
    assert 'data-skill-default="' in content


# --- Behavioral: run the real escapeHtml in headless Chromium ----------------

_HOSTILE_PAYLOADS = [
    "<img src=x onerror=alert(1)>",
    '\" onmouseover=\"alert(1)\" x=\"',
    "');alert(document.cookie)//",
    "</span><script>alert(1)</script>",
    "${alert(document.cookie)}",
]

_BEHAVIORAL_TEMPLATES = (
    "agents.html",
    "conversations.html",
    "scheduled_tasks.html",
    "conversation.html",
    "agent_home.html",
    "mcps.html",
    "toolsets.html",
    "skills.html",
)


def _run_escape_html(escape_src, payloads, tmp_path):
    """Run a template's real ``escapeHtml`` in headless Chromium over payloads."""
    payloads_json = json.dumps(payloads).replace("</", "<\\/")
    driver = (
        "var payloads = " + payloads_json + ";\n"
        "var results = {};\n"
        "payloads.forEach(function (p) { results[p] = escapeHtml(p); });\n"
        'document.getElementById("out").textContent = JSON.stringify(results);'
    )
    doc = (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
        '<div id="out"></div><script>\n'
        + escape_src
        + "\n"
        + driver
        + "\n</script></body></html>"
    )
    html_path = tmp_path / "esc.html"
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


@_requires_chrome
@pytest.mark.parametrize("template", _BEHAVIORAL_TEMPLATES)
def test_escape_html_neutralizes_hostile_payloads(template, tmp_path):
    """The real escapeHtml turns angle brackets and quotes into entities."""
    escape_src = _extract_from_sanitize_module("escapeHtml")
    results = _run_escape_html(escape_src, _HOSTILE_PAYLOADS, tmp_path)
    for payload in _HOSTILE_PAYLOADS:
        escaped = results[payload]
        # No raw tag/attribute delimiters survive.
        assert "<" not in escaped, (template, payload, escaped)
        assert ">" not in escaped, (template, payload, escaped)
        assert '"' not in escaped, (template, payload, escaped)
        assert "'" not in escaped, (template, payload, escaped)
        # Whatever delimiters the payload contained are entity-encoded.
        if "<" in payload:
            assert "&lt;" in escaped, (template, payload, escaped)
        if ">" in payload:
            assert "&gt;" in escaped, (template, payload, escaped)
        if chr(34) in payload:
            assert "&quot;" in escaped, (template, payload, escaped)
        if "'" in payload:
            assert "&#39;" in escaped, (template, payload, escaped)
        if "&" in payload:
            assert "&amp;" in escaped, (template, payload, escaped)


# --- Guardrail: no raw data interpolated into innerHTML template literals -----
#
# The static tests above pin down *known* sinks one-by-one, but they are easy to
# miss when a new sibling sink is added (e.g. a freshly staged list). This
# guardrail instead scans every template's source and flags ANY ``innerHTML``
# template-literal block whose ``${...}`` interpolation is not provably safe, so
# future unescaped sinks are caught automatically without a browser.

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"

# Identifiers built earlier in the template and already run through
# ``escapeHtml`` / ``sanitizeHtml`` before interpolation.
_PRESANITIZED_IDENTIFIERS = frozenset({"infoContentHtml", "contentHtml", "avatarHtml"})

# Numeric/boolean values that can never carry markup (indices, counts, pages).
# Note: string IDs (``messageId``, ``uniqueId``) are intentionally NOT here; they
# must be escaped and are only ever interpolated via ``escapeHtml(...)`` or in
# non-``innerHTML`` template literals (e.g. ``document.querySelector(...)``).
_NUMERIC_IDENTIFIERS = frozenset({
    "idx", "index", "i", "j", "k", "n", "length", "chunks", "fetchIndex",
    "currentPlayIndex", "currentTokenCount", "contextSize", "percentage",
    "totalCost", "pagination", "page", "total_pages", "total",
})

# Local class-name/text flags computed from a boolean (e.g.
# ``task.enabled ? 'bg-green-500' : 'bg-slate-500'``); their values are fixed
# string literals, never server/user text.
_CLASS_FLAG_IDENTIFIERS = frozenset({"enabledClass", "enabledText"})

# Method calls tolerated inside a ternary/coalesce interpolation whose branches
# are otherwise static strings (e.g. ``(server.toolFilter || []).join(', ')``).
_TINY_SAFE_CALLS = frozenset({
    "padStart", "parseInt", "toFixed", "substring", "charAt", "startsWith",
    "join", "finally",
})

# How far back (chars) before an opening backtick to look for ``innerHTML``.
_INNERHTML_LOOKBACK = 120


def _consume_nested_template(source, start):
    """Return the index just past the template literal beginning at ``start``.

    ``source[start]`` must be a backtick. Escaped backticks and ``${...}``
    interpolations (whose expressions may contain further nested template
    literals) are skipped so the matching closing backtick is found correctly.
    """
    i = start + 1
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "`":
            return i + 1
        if ch == "$" and i + 1 < n and source[i + 1] == "{":
            j = i + 2
            depth = 1
            while j < n and depth > 0:
                c = source[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "`":
                    j = _consume_nested_template(source, j)
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                j += 1
            i = j
            continue
        i += 1
    return n


def _extract_template_literals(source):
    """Yield ``(start_offset, literal_text)`` for every backtick template literal.

    Escaped backticks and ``${...}`` interpolations with balanced braces and
    nested template literals are handled so each literal is extracted verbatim.
    """
    literals = []
    i = 0
    n = len(source)
    while i < n:
        if source[i] != "`":
            i += 1
            continue
        start = i
        i += 1
        while i < n:
            ch = source[i]
            if ch == "\\":
                i += 2
                continue
            if ch == "`":
                break
            if ch == "$" and i + 1 < n and source[i + 1] == "{":
                j = i + 2
                depth = 1
                while j < n and depth > 0:
                    c = source[j]
                    if c == "\\":
                        j += 2
                        continue
                    if c == "`":
                        j = _consume_nested_template(source, j)
                        continue
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                    j += 1
                i = j
                continue
            i += 1
        if i < n and source[i] == "`":
            literals.append((start, source[start : i + 1]))
            i += 1
        else:
            break
    return literals


def _iter_interpolations(literal_text):
    """Yield the text of each top-level ``${...}`` expression in ``literal_text``.

    ``literal_text`` includes the surrounding backticks. Interpolations nested
    inside other interpolations' template literals are skipped, so only the
    top-level interpolations of this literal are returned.
    """
    body = literal_text
    body = body.removeprefix("`")
    body = body.removesuffix("`")
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "$" and i + 1 < n and body[i + 1] == "{":
            j = i + 2
            depth = 1
            start = j
            while j < n and depth > 0:
                c = body[j]
                if c == "\\":
                    j += 2
                    continue
                if c == "`":
                    j = _consume_nested_template(body, j)
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            yield body[start:j]
            i = j + 1
            continue
        i += 1


def _calls_in(text):
    """Return the identifier names of every call expression in ``text``."""
    return re.findall(r"([A-Za-z_$][\w$]*)\s*\(", text)


def _is_numeric_expression(text):
    """True if ``text`` is only identifiers/numbers/operators with safe tokens."""
    if not re.fullmatch(r"[\w$.\s+\-*()]+", text):
        return False
    tokens = re.findall(r"[A-Za-z_$][\w$]*", text)
    return all(token in _NUMERIC_IDENTIFIERS for token in tokens)


def _split_ternary(text):
    """Split ``text`` into ``(condition, true_branch, false_branch)`` at a ternary.

    Returns ``None`` when there is no top-level ternary ``? ... :``. Optional
    chaining (``?.``) and nullish coalescing (``??``) are never treated as the
    ternary ``?``. Parenthesized/bracketed/braced sub-expressions, quoted
    strings, and nested template literals are skipped so the ``?`` and its
    matching ``:`` are located at the top nesting depth.
    """
    q = None
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "`":
            i = _consume_nested_template(text, i)
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            while i < n:
                c = text[i]
                if c == "\\":
                    i += 2
                    continue
                if c == quote:
                    i += 1
                    break
                i += 1
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0:
            if ch == "?":
                nxt = text[i + 1] if i + 1 < n else ""
                if q is None and nxt not in (".", "?"):
                    q = i
            elif ch == ":" and q is not None:
                return text[:q], text[q + 1 : i], text[i + 1 :]
        i += 1
    return None


def _is_string_literal(text):
    """True if ``text`` is a single quoted string literal using one quote char."""
    return bool(re.fullmatch(r"(['\"]).*\1", text, re.DOTALL))


def _is_static_ternary_or_coalesce(text):
    """True if ``text`` is a ternary/coalesce whose branches are static strings.

    A ternary ``cond ? a : b`` is safe only when *both* branches are single
    string literals (e.g. ``t.passive ? ' (passive)' : ''``), so a data-carrying
    branch such as ``flag ? userSuppliedName : ''`` is rejected. A ``||``
    expression with no ternary is safe only when it is a ``join``-based chain
    that calls nothing but the tiny safe set (e.g.
    ``(server.toolFilter || []).join(', ')``); a bare ``x || 'y'`` is rejected.
    """
    parts = _split_ternary(text)
    if parts is not None:
        _, true_branch, false_branch = parts
        return _is_string_literal(true_branch.strip()) and _is_string_literal(false_branch.strip())
    if "||" in text:
        return ".join(" in text and all(call in _TINY_SAFE_CALLS for call in _calls_in(text))
    return False


def _is_sanitized_map_join(text):
    """True for a ``.map(...).join('')`` chain that escapes each element."""
    return ".join(" in text and ("escapeHtml(" in text or "sanitizeHtml(" in text)


def _is_safe_interpolation(expr):
    """Return True if a ``${...}`` interpolation expression is provably safe."""
    text = expr.strip()
    if not text:
        return True
    # 1. wrapped in the per-template escaping/sanitizing helper.
    if text.startswith(("escapeHtml(", "sanitizeHtml(")):
        return True
    # 2. known pre-sanitized/safe identifier.
    if text in _PRESANITIZED_IDENTIFIERS:
        return True
    # local boolean-derived class/text flags (fixed literal values).
    if text in _CLASS_FLAG_IDENTIFIERS:
        return True
    # 7. base58 address from wifToAddress (safe) and its substring slices.
    if text == "address" or text.startswith("address.substring("):
        return True
    # 6. Date.now().
    if text == "Date.now()":
        return True
    # 4. encodeURIComponent(...) is safe for URL/attribute contexts.
    if text.startswith("encodeURIComponent("):
        return True
    # 3. numeric/index expression built from allowlisted identifiers.
    if _is_numeric_expression(text):
        return True
    # 5. ternary / || expression whose branches are static strings.
    if _is_static_ternary_or_coalesce(text):
        return True
    # .map().join('') chain that escapes/sanitizes each element.
    return bool(_is_sanitized_map_join(text))


def test_no_raw_data_interpolated_into_innerhtml():
    """Guardrail: no unescaped ``${...}`` inside any ``innerHTML`` template literal.

    Scans every template for backtick template literals assigned (directly or
    via a map/ternary) to an ``innerHTML`` sink and asserts every top-level
    interpolation is wrapped in ``escapeHtml``/``sanitizeHtml`` or otherwise
    provably safe. This catches future sibling sinks automatically and runs
    statically (no browser required).
    """
    violations = []
    for name in sorted(p.name for p in _TEMPLATES_DIR.glob("*.html")):
        source = _read_template(name)
        for start, literal in _extract_template_literals(source):
            if "innerHTML" not in source[max(0, start - _INNERHTML_LOOKBACK):start]:
                continue
            line = source.count("\n", 0, start) + 1
            for expr in _iter_interpolations(literal):
                if not _is_safe_interpolation(expr):
                    violations.append((name, line, expr.strip()))

    if violations:
        details = "\n".join(
            f"  {name}:{line}  ${{{expr}}}\n"
            f"      hint: wrap with escapeHtml(...) or sanitizeHtml(...)"
            for name, line, expr in violations
        )
        raise AssertionError(
            f"found {len(violations)} unescaped innerHTML interpolation(s):\n{details}"
        )


# --- Guardrail helper behavior pins (no browser required) --------------------


def _unsafe_interpolations_in(source):
    """Return the unsafe ``${...}`` expressions found in ``source`` innerHTML sinks."""
    found = []
    for start, literal in _extract_template_literals(source):
        if "innerHTML" not in source[max(0, start - _INNERHTML_LOOKBACK):start]:
            continue
        for expr in _iter_interpolations(literal):
            if not _is_safe_interpolation(expr):
                found.append(expr.strip())
    return found


def test_guardrail_helper_rejects_unsafe_interpolations():
    """The guardrail must classify these interpolation expressions as UNSAFE.

    Pins the negative behavior so a future loosening (e.g. accepting a raw data
    identifier or a data-carrying ternary/coalesce branch) fails loudly.
    """
    for expr in (
        "resource",
        "script",
        "nodeId",
        "file.name",
        "task.name",
        "flag ? data : ''",
        "x || 'y'",
        "(a || '')",
    ):
        assert not _is_safe_interpolation(expr), f"should be unsafe: {expr!r}"


def test_guardrail_helper_accepts_safe_interpolations():
    """The guardrail must classify these interpolation expressions as SAFE.

    Pins the positive behavior so a future tightening does not break the
    legitimate static-branch/coalesce/numeric forms used across templates.
    """
    for expr in (
        "escapeHtml(x)",
        "sanitizeHtml(x)",
        "idx",
        "idx + 1",
        "encodeURIComponent(agentName)",
        "t.passive ? ' (passive)' : ''",
        "(server.toolFilter || []).join(', ')",
        "Date.now()",
        "address",
    ):
        assert _is_safe_interpolation(expr), f"should be safe: {expr!r}"


def test_guardrail_flags_synthetic_unescaped_innerhtml_sink():
    """End-to-end scanner detects a raw ``${data}`` inside an innerHTML literal."""
    source = "function render() {\n  list.innerHTML = `<li>${data}</li>`;\n}"
    assert _unsafe_interpolations_in(source) == ["data"]


def test_guardrail_ignores_synthetic_escaped_innerhtml_sink():
    """End-to-end scanner accepts ``${escapeHtml(data)}`` inside an innerHTML literal."""
    source = "function render() {\n  list.innerHTML = `<li>${escapeHtml(data)}</li>`;\n}"
    assert _unsafe_interpolations_in(source) == []

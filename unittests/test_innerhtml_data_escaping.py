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

import pytest

from test_xss_sanitization import (
    _CHROME,
    _extract_function,
    _read_template,
    _requires_chrome,
)


# --- Static: every affected template defines the escapeHtml helper ----------

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
def test_template_defines_single_escape_html(template):
    """Each affected template defines exactly one escapeHtml helper."""
    content = _read_template(template)
    assert len(re.findall(r"function escapeHtml\(", content)) == 1, template
    # The helper must escape quotes so attribute contexts are safe too.
    assert "&quot;" in content
    assert "&#39;" in content


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
    # playMessageAudio message ids are escaped inside the inline string.
    assert "playMessageAudio('${escapeHtml(msg.message_id || '')}'" in body


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
    escape_src = _extract_function(_read_template(template), "escapeHtml")
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

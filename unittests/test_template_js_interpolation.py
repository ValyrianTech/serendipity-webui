"""Regression tests for the Jinja2 ``tojson`` JS-interpolation fix.

Several templates previously inlined path/query values directly into
single-quoted JavaScript string literals (``const agentName = '{{ agent_name }}';``).
Jinja2's HTML autoescaping does not escape backslashes or newlines, so a value
ending in a backslash would terminate the JS string early, enabling JS injection
/ reflected XSS. The fix replaces those with the ``tojson`` filter, which emits
a safely-escaped JSON string literal (backslash -> ``\\``, newline -> ``\\n``,
``<`` -> ``\\u003c``).
"""

import urllib.parse
from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"

TEMPLATE_NAMES = (
    "workflows.html",
    "conversation.html",
    "agent_home.html",
    "conversations.html",
    "edit_agent.html",
)

SINGLE_QUOTED_PATTERNS = (
    "const agentName = '{{",
    "const initialWorkflowId = '{{",
    "const initialNodeId = '{{",
)


def _read_template(name):
    """Return the raw text of a template file under ``app/templates``."""
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def _workflows_path(agent_name):
    """Return the workflows route path with ``agent_name`` URL-encoded."""
    return "/agent/" + urllib.parse.quote(agent_name, safe="") + "/workflows"


@pytest.mark.parametrize("template_name", TEMPLATE_NAMES)
def test_templates_do_not_inline_single_quoted_js_interpolation(template_name):
    """No template inlines values into single-quoted JS string literals."""
    content = _read_template(template_name)
    for pattern in SINGLE_QUOTED_PATTERNS:
        assert pattern not in content


@pytest.mark.parametrize("template_name", TEMPLATE_NAMES)
def test_templates_use_tojson_for_agent_name(template_name):
    """Every affected template renders agent_name through the tojson filter."""
    content = _read_template(template_name)
    assert "{{ agent_name | tojson }}" in content
    if template_name == "workflows.html":
        assert "{{ workflow_id | tojson }}" in content
        assert "{{ node_id | tojson }}" in content


def test_backslash_agent_name_is_json_escaped(client):
    """A backslash in agent_name cannot terminate the JS string early."""
    agent_name = "foo" + chr(92) + "bar"
    response = client.get(_workflows_path(agent_name))
    assert response.status_code == 200
    assert "const agentName = '" not in response.text
    assert 'const agentName = "' in response.text
    assert 'const agentName = "foo\\\\bar";' in response.text


def test_newline_agent_name_is_json_escaped(client):
    """A newline in agent_name is rendered as the escape, not a raw newline."""
    agent_name = "a\nb"
    response = client.get(_workflows_path(agent_name))
    assert response.status_code == 200
    marker = 'const agentName = "'
    start = response.text.index(marker) + len(marker)
    end = response.text.index('"', start)
    literal = response.text[start:end]
    assert chr(10) not in literal
    assert 'const agentName = "a\\nb";' in response.text


def test_script_tag_breakout_is_escaped(client):
    """Angle-bracket breakout payloads are escaped to unicode escapes."""
    response = client.get(
        "/agent/MyAgent/workflows",
        params={"workflow_id": "</script><script>alert(1)</script>"},
    )
    assert response.status_code == 200
    assert "</script><script>alert(1)" not in response.text
    assert "\\u003c/script" in response.text
    assert 'const initialWorkflowId = "\\u003c' in response.text

    response = client.get(_workflows_path("<script>alert(1)"))
    assert response.status_code == 200
    assert 'const agentName = "<' not in response.text
    assert 'const agentName = "\\u003c' in response.text


def test_workflow_and_node_ids_are_json_encoded(client):
    """workflow_id and node_id are emitted as double-quoted JSON literals."""
    response = client.get(
        "/agent/MyAgent/workflows",
        params={"workflow_id": "x'];alert(1)//", "node_id": "n\n"},
    )
    assert response.status_code == 200
    assert "const initialWorkflowId = '" not in response.text
    assert "const initialNodeId = '" not in response.text
    assert 'const initialWorkflowId = "' in response.text
    assert 'const initialNodeId = "' in response.text

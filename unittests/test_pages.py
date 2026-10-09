"""Tests for the HTML page routes defined in app.main."""

import pytest

# Routes that render a template without any path parameters.
STATIC_PAGE_ROUTES = [
    "/",
    "/settings",
    "/mcps",
    "/toolsets",
    "/skills",
    "/llms",
    "/scheduled-tasks",
]

# Routes that require an {agent_name} path parameter.
AGENT_PAGE_ROUTES = [
    "/agent/Serendipity",
    "/agent/Serendipity/conversations",
    "/agent/Serendipity/conversation",
    "/agent/Serendipity/edit",
    "/agent/Serendipity/workflows",
]


@pytest.mark.parametrize("path", STATIC_PAGE_ROUTES)
def test_static_pages_render_html(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@pytest.mark.parametrize("path", AGENT_PAGE_ROUTES)
def test_agent_pages_render_html(client, path):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_home_page_lists_agents(client):
    response = client.get("/")
    assert "Select an Agent" in response.text


def test_agent_home_renders_agent_name(client):
    response = client.get("/agent/MyAgent")
    assert "MyAgent" in response.text


def test_conversation_route_accepts_conversation_id(client):
    response = client.get("/agent/MyAgent/conversation", params={"conversation_id": "abc-123"})
    assert response.status_code == 200


def test_workflows_route_renders_workflow_and_node_ids(client):
    response = client.get(
        "/agent/MyAgent/workflows",
        params={"workflow_id": "wf-1", "node_id": "node-9"},
    )
    assert response.status_code == 200
    assert "wf-1" in response.text
    assert "node-9" in response.text


def test_agent_name_is_html_escaped(client):
    response = client.get("/agent/Foo%3Cbar%3E")
    assert response.status_code == 200
    assert "Foo&lt;bar&gt;" in response.text
    assert "Foo<bar>" not in response.text


def test_unknown_route_returns_404(client):
    response = client.get("/this-route-does-not-exist")
    assert response.status_code == 404


def test_static_files_are_served(client):
    response = client.get("/static/js/api.js")
    assert response.status_code == 200

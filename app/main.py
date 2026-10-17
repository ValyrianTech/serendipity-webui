"""FastAPI web UI application for the Serendipity AI agent platform."""

import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="Serendipity Web UI")
logger = logging.getLogger(__name__)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")

MAX_TTS_RESPONSE_BYTES = 25 * 1024 * 1024
DEFAULT_TTS_SERVER = "primary"

WEBUI_HOST = os.environ.get("SERENDIPITY_WEBUI_HOST", "127.0.0.1")
WEBUI_PORT = int(os.environ.get("SERENDIPITY_WEBUI_PORT", "8080"))


def get_configured_tts_servers() -> dict[str, str]:
    """Return the trusted TTS server map (selector key -> base URL).

    The map is read from the ``SERENDIPITY_TTS_SERVERS`` environment variable,
    which must hold a JSON object mapping opaque selector keys to base URLs
    (e.g. ``{"primary": "http://192.168.1.50:8000/tts"}``). This is the only
    source of TTS server URLs; request-supplied values are never used to build
    an outbound request.

    :returns: A mapping of selector key to base URL. Empty when the variable is
        unset, not valid JSON, or not a JSON object.
    :rtype: dict[str, str]
    """
    raw = os.environ.get("SERENDIPITY_TTS_SERVERS", "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("SERENDIPITY_TTS_SERVERS is not valid JSON; ignoring")
        return {}
    if not isinstance(data, dict):
        logger.warning("SERENDIPITY_TTS_SERVERS must be a JSON object; ignoring")
        return {}
    return {
        key: value
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str) and key.strip() and value.strip()
    }


def resolve_tts_server(selector: str) -> str | None:
    """Resolve an opaque selector to a trusted TTS base URL, or None if unknown.

    :param selector: The opaque selector key supplied by the client.
    :type selector: str
    :returns: The configured base URL, or None when the selector is unknown.
    :rtype: str | None
    """
    return get_configured_tts_servers().get(selector)


def _origin_allowed(request: Request) -> bool:
    """Return True when the request is permitted to drive the TTS proxy.

    The ``Origin`` header is inspected first; when absent, the ``Referer``
    header is used instead. If neither header is present the request is treated
    as same-origin (non-browser clients, same-origin navigation, curl, and
    tests without those headers must still work). Otherwise the header's netloc
    is compared (case-insensitively) against the request's ``Host`` header,
    which may include a port.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: True when the request is same-origin, False otherwise.
    :rtype: bool
    """
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin is None:
        return True
    origin_netloc = urlparse(origin).netloc
    if not origin_netloc:
        return False
    host = request.headers.get("host", "")
    return origin_netloc.lower() == host.lower()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    """Render the home page listing all agents.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``agents.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse(request, "agents.html", {})


@app.get("/agent/{agent_name}", response_class=HTMLResponse)
async def agent_home(request: Request, agent_name: str) -> HTMLResponse:
    """Render the home page for a specific agent.

    :param request: The incoming HTTP request.
    :type request: Request
    :param agent_name: The name of the agent.
    :type agent_name: str
    :returns: The rendered ``agent_home.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse(request, "agent_home.html", {"agent_name": agent_name})


@app.get("/agent/{agent_name}/conversations", response_class=HTMLResponse)
async def conversations(request: Request, agent_name: str) -> HTMLResponse:
    """Render the conversations list page for an agent.

    :param request: The incoming HTTP request.
    :type request: Request
    :param agent_name: The name of the agent.
    :type agent_name: str
    :returns: The rendered ``conversations.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse(request, "conversations.html", {"agent_name": agent_name})


@app.get("/agent/{agent_name}/conversation", response_class=HTMLResponse)
async def conversation(request: Request, agent_name: str, conversation_id: str = "") -> HTMLResponse:
    """Render the conversation page for an agent.

    :param request: The incoming HTTP request.
    :type request: Request
    :param agent_name: The name of the agent.
    :type agent_name: str
    :param conversation_id: The optional ID of the conversation to load.
    :type conversation_id: str
    :returns: The rendered ``conversation.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse(request, "conversation.html", {
        "agent_name": agent_name,
        "conversation_id": conversation_id
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request) -> HTMLResponse:
    """Render the application settings page.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``settings.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse(request, "settings.html", {})


@app.get("/agent/{agent_name}/edit", response_class=HTMLResponse)
async def edit_agent(request: Request, agent_name: str) -> HTMLResponse:
    """Render the edit page for an agent.

    :param request: The incoming HTTP request.
    :type request: Request
    :param agent_name: The name of the agent to edit.
    :type agent_name: str
    :returns: The rendered ``edit_agent.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse(request, "edit_agent.html", {"agent_name": agent_name})


@app.get("/mcps", response_class=HTMLResponse)
async def mcps(request: Request) -> HTMLResponse:
    """Render the MCP servers management page.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``mcps.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse(request, "mcps.html", {})


@app.get("/toolsets", response_class=HTMLResponse)
async def toolsets(request: Request) -> HTMLResponse:
    """Render the toolsets management page.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``toolsets.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse(request, "toolsets.html", {})


@app.get("/skills", response_class=HTMLResponse)
async def skills(request: Request) -> HTMLResponse:
    """Render the skills management page.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``skills.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse(request, "skills.html", {})


@app.get("/llms", response_class=HTMLResponse)
async def llms(request: Request) -> HTMLResponse:
    """Render the LLM configurations page.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``llms.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse(request, "llms.html", {})


@app.get("/scheduled-tasks", response_class=HTMLResponse)
async def scheduled_tasks(request: Request) -> HTMLResponse:
    """Render the scheduled tasks page.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: The rendered ``scheduled_tasks.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse(request, "scheduled_tasks.html", {})


@app.get("/agent/{agent_name}/workflows", response_class=HTMLResponse)
async def workflows(request: Request, agent_name: str, workflow_id: str = "", node_id: str = "") -> HTMLResponse:
    """Render the workflows page for an agent.

    :param request: The incoming HTTP request.
    :type request: Request
    :param agent_name: The name of the agent.
    :type agent_name: str
    :param workflow_id: The optional ID of the workflow to load.
    :type workflow_id: str
    :param node_id: The optional ID of the workflow node to load.
    :type node_id: str
    :returns: The rendered ``workflows.html`` template.
    :rtype: TemplateResponse
    """
    return templates.TemplateResponse(request, "workflows.html", {
        "agent_name": agent_name,
        "workflow_id": workflow_id,
        "node_id": node_id
    })


@app.get("/api/tts-proxy")
async def tts_proxy(request: Request, voice: str, text: str, style: str = "default", language: str = "English", speed: float = 1.0, server: str = DEFAULT_TTS_SERVER) -> Response:
    """Proxy TTS requests to avoid browser timeouts on slow generation.

    The ``server`` parameter is an opaque selector key resolved against the
    server-side ``SERENDIPITY_TTS_SERVERS`` configuration. It is never a URL,
    and no request-supplied value is used to build the outbound request URL, so
    the endpoint cannot be used to fetch an attacker-chosen address.

    :param request: The incoming HTTP request.
    :type request: Request
    :param voice: The voice to synthesize with.
    :type voice: str
    :param text: The text to synthesize.
    :type text: str
    :param style: The speech style.
    :type style: str
    :param language: The language of the speech.
    :type language: str
    :param speed: The playback speed multiplier.
    :type speed: float
    :param server: The opaque TTS server selector key.
    :type server: str
    :returns: The synthesized audio, or a JSON error response.
    :rtype: Response
    """
    if not _origin_allowed(request):
        return JSONResponse({"error": "Cross-origin TTS proxy request rejected"}, status_code=403)

    base_url = resolve_tts_server(server)
    if base_url is None:
        return JSONResponse({"error": "Unknown TTS server"}, status_code=400)

    # The request URL is built solely from server-side configuration; the
    # selector is never concatenated into the URL.
    request_url = f"{base_url.rstrip('/')}/synthesize_speech/"
    params: dict[str, str | float] = {
        "voice": voice,
        "text": text,
        "style": style,
        "language": language,
    }
    if speed != 1.0:
        params["speed"] = speed

    # Use a long timeout for slow TTS generation (5 minutes)
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        try:
            response = await client.get(request_url, params=params)
            response.raise_for_status()

            # Ensure we return audio content type
            content_type = response.headers.get("content-type", "audio/wav")
            if not content_type.startswith("audio/"):
                return JSONResponse({"error": "TTS server returned non-audio content"}, status_code=502)

            # Reject oversized responses up front when the server advertises a
            # Content-Length, and again after reading as a backstop.
            declared_length = response.headers.get("content-length")
            if declared_length is not None:
                try:
                    if int(declared_length) > MAX_TTS_RESPONSE_BYTES:
                        return JSONResponse({"error": "TTS response too large"}, status_code=502)
                except ValueError:
                    pass

            body = response.content
            if len(body) > MAX_TTS_RESPONSE_BYTES:
                return JSONResponse({"error": "TTS response too large"}, status_code=502)

            return Response(
                content=body,
                media_type=content_type,
                headers={"Cache-Control": "no-cache"}
            )
        except httpx.TimeoutException:
            return JSONResponse({"error": "TTS generation timed out"}, status_code=504)
        except httpx.HTTPStatusError as e:
            return JSONResponse({"error": f"TTS server error: {e.response.status_code}"}, status_code=e.response.status_code)
        except Exception:
            logger.exception("TTS proxy request failed")
            return JSONResponse({"error": "Internal TTS proxy error"}, status_code=500)


@app.get("/api/tts-server")
async def tts_server(request: Request) -> JSONResponse:
    """Return the resolved default TTS server URL from server-side configuration.

    A read-only view of the configured default server, used by the UI for
    actions that call the Serendipity backend directly and therefore need a
    concrete server URL (e.g. voice initialization). The proxy endpoint itself
    never uses a client-supplied URL.

    Cross-origin requests are rejected with 403 to avoid leaking the configured
    server URL to unrelated origins.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: A JSON object with the resolved ``url``, or ``null`` when no
        default server is configured.
    :rtype: JSONResponse
    """
    if not _origin_allowed(request):
        return JSONResponse({"error": "Cross-origin TTS server request rejected"}, status_code=403)

    return JSONResponse({"url": resolve_tts_server(DEFAULT_TTS_SERVER)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=WEBUI_HOST, port=WEBUI_PORT)

"""FastAPI web UI application for the Serendipity AI agent platform."""

import ipaddress
import logging
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

app = FastAPI(title="Serendipity Web UI")
logger = logging.getLogger(__name__)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")

ALLOWED_TTS_SCHEMES = ("http", "https")
MAX_TTS_RESPONSE_BYTES = 25 * 1024 * 1024


def _get_allowed_tts_hosts() -> set[str]:
    """Return the set of operator-allowlisted TTS host names from the environment.

    Reads ``SERENDIPITY_TTS_ALLOWED_HOSTS`` (a comma-separated list of host
    names), stripping whitespace, lowercasing, and dropping empty entries.

    :returns: A set of lowercase, allowlisted host names.
    :rtype: set[str]
    """
    raw = os.environ.get("SERENDIPITY_TTS_ALLOWED_HOSTS", "")
    return {host.strip().lower() for host in raw.split(",") if host.strip()}


def _resolve_host(host: str) -> list[str]:
    """Resolve a host name to the list of IP address strings it maps to.

    :param host: The host name (or IP literal) to resolve.
    :type host: str
    :returns: A list of IP address strings the host resolves to.
    :rtype: list[str]
    :raises ValueError: If the host cannot be resolved.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, OSError) as exc:
        raise ValueError(f"could not resolve host {host!r}") from exc
    return [str(info[4][0]) for info in infos]


def _is_blocked_ip(ip_str: str) -> bool:
    """Return True if an IP address should be blocked for TTS proxying.

    :param ip_str: The IP address string to check.
    :type ip_str: str
    :returns: True if the address is private, loopback, link-local, reserved,
        multicast, or unspecified, or if it cannot be parsed.
    :rtype: bool
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _format_netloc_ip(ip: str) -> str:
    """Render an IP address for use in a URL netloc, bracketing IPv6 literals.

    :param ip: The IP address string (IPv4 or IPv6).
    :type ip: str
    :returns: The IP, wrapped in square brackets for IPv6 (e.g. ``[::1]``),
        or unchanged for IPv4.
    :rtype: str
    """
    if ipaddress.ip_address(ip).version == 6:
        return f"[{ip}]"
    return ip


def _validate_tts_server(server: str) -> tuple[str, str | None]:
    """Validate a TTS server URL to prevent SSRF to internal or non-HTTP resources.

    :param server: The TTS server base URL to validate.
    :type server: str
    :returns: A tuple of ``(server_url, validated_ip)``. ``server_url`` is the
        original ``server`` URL, and ``validated_ip`` is the single resolved and
        vetted IP address to connect to, or ``None`` when the host is
        operator-allowlisted (in which case the hostname is used directly).
    :rtype: tuple[str, str | None]
    :raises ValueError: If the scheme, host, or resolved IPs are disallowed.
    """
    parsed = urlparse(server)
    if parsed.scheme not in ALLOWED_TTS_SCHEMES:
        raise ValueError("invalid scheme")
    host = parsed.hostname
    if not host:
        raise ValueError("missing host")

    allowed = _get_allowed_tts_hosts()
    if allowed and host.lower() not in allowed:
        raise ValueError("host not allowed")

    if host.lower() in allowed:
        return server, None

    try:
        ipaddress.ip_address(host)
        host_is_ip_literal = True
    except ValueError:
        host_is_ip_literal = False
    if host_is_ip_literal:
        if _is_blocked_ip(host):
            raise ValueError("blocked IP")
        return server, host

    resolved = _resolve_host(host)
    for ip in resolved:
        if _is_blocked_ip(ip):
            raise ValueError("blocked IP")
    return server, resolved[0]


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
async def tts_proxy(request: Request, server: str, voice: str, text: str, style: str = "default", language: str = "English", speed: float = 1.0) -> Response:
    """Proxy TTS requests to avoid browser timeouts on slow generation.

    :param request: The incoming HTTP request.
    :type request: Request
    :param server: The TTS server base URL to forward to.
    :type server: str
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
    :returns: The synthesized audio, or a JSON error response.
    :rtype: Response
    """
    try:
        server_url, validated_ip = _validate_tts_server(server)
    except ValueError:
        return JSONResponse({"error": "Invalid or disallowed TTS server"}, status_code=400)

    # Build the TTS URL
    server_url = server_url.rstrip('/')
    parsed = urlparse(server_url)
    params: dict[str, str | float] = {
        "voice": voice,
        "text": text,
        "style": style,
        "language": language,
    }
    if speed != 1.0:
        params["speed"] = speed

    # Pin httpx to the exact vetted IP to avoid a second (TOCTOU) DNS resolution,
    # while preserving the original hostname in the Host header. The server URL's
    # path prefix (e.g. ``/tts``) must be preserved in front of ``/synthesize_speech/``.
    path = f"{parsed.path}/synthesize_speech/"
    host_headers: dict[str, str] = {}
    if validated_ip is not None:
        netloc = _format_netloc_ip(validated_ip)
        if parsed.port is not None:
            netloc = f"{netloc}:{parsed.port}"
        request_url = f"{parsed.scheme}://{netloc}{path}"
        host_header = parsed.hostname or ""
        if parsed.port is not None:
            host_header = f"{host_header}:{parsed.port}"
        host_headers["Host"] = host_header
    else:
        request_url = f"{server_url}/synthesize_speech/"

    # Use a long timeout for slow TTS generation (5 minutes)
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        try:
            async with client.stream("GET", request_url, params=params, headers=host_headers) as response:
                response.raise_for_status()

                # Ensure we return audio content type
                content_type = response.headers.get("content-type", "audio/wav")
                if not content_type.startswith("audio/"):
                    # TTS server returned non-audio content
                    return JSONResponse({"error": "TTS server returned non-audio content"}, status_code=502)

                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_TTS_RESPONSE_BYTES:
                        return JSONResponse({"error": "TTS response too large"}, status_code=502)
                    chunks.append(chunk)

                return StreamingResponse(
                    iter(chunks),
                    media_type=content_type,
                    headers={"Cache-Control": "no-cache"}
                )
        except httpx.TimeoutException:
            return JSONResponse({"error": "TTS generation timed out"}, status_code=504)
        except httpx.HTTPStatusError as e:
            return JSONResponse({"error": f"TTS server error: {e.response.status_code}"}, status_code=e.response.status_code)
        except Exception:  # noqa: BLE001
            logger.exception("TTS proxy request failed")
            return JSONResponse({"error": "Internal TTS proxy error"}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

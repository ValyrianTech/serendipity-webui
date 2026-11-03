"""FastAPI web UI application for the Serendipity AI agent platform."""

import ipaddress
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse, urlsplit

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)

MAX_TTS_RESPONSE_BYTES = 25 * 1024 * 1024
DEFAULT_TTS_SERVER = "primary"
DEFAULT_ALLOWED_ORIGINS_ENV = "SERENDIPITY_WEBUI_ALLOWED_ORIGINS"
TRUST_FORWARDED_PROTO_ENV = "SERENDIPITY_WEBUI_TRUST_FORWARDED_PROTO"

# Resolved startup default for the TTS allow-list, populated by
# populate_default_allowed_origins(). ``None`` means "not yet resolved"; an
# empty frozenset means "resolved, but no default (wildcard bind host)".
DEFAULT_ALLOWED_ORIGINS: frozenset[str] | None = None

WEBUI_HOST = os.environ.get("SERENDIPITY_WEBUI_HOST", "127.0.0.1")
WEBUI_PORT = int(os.environ.get("SERENDIPITY_WEBUI_PORT", "8080"))


def _is_wildcard_host(host: str) -> bool:
    """Return True when the bind host is a wildcard address.

    A wildcard bind host (``0.0.0.0`` or ``::``) makes the server listen on all
    interfaces, so it cannot be turned into a browser-reachable origin for the
    allow-list. Whitespace and the surrounding square brackets of an IPv6
    literal form (e.g. ``[::]``) are ignored before the comparison.

    :param host: The configured bind host string.
    :type host: str
    :returns: True when the host is ``0.0.0.0`` or ``::``, False otherwise.
    :rtype: bool
    """
    normalised = host.strip()
    if normalised.startswith("[") and normalised.endswith("]"):
        normalised = normalised[1:-1].strip()
    return normalised in {"0.0.0.0", "::"}


def _is_ip_literal(value: str) -> bool:
    """Return True when ``value`` parses as an IPv4 or IPv6 address.

    Uses the stdlib :mod:`ipaddress` module to determine whether ``value`` is a
    literal IP address (IPv4 or IPv6). A hostname that merely looks numeric but
    does not parse (e.g. an invalid octet) is not an IP literal.

    :param value: The host string to test (already stripped of port/brackets).
    :type value: str
    :returns: True when ``value`` is a valid IPv4 or IPv6 address, False
        otherwise.
    :rtype: bool
    """
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def populate_default_allowed_origins() -> None:
    """Resolve the default TTS allow-list and log the effective value.

    When ``SERENDIPITY_WEBUI_ALLOWED_ORIGINS`` is unset or blank and the bind
    host is not a wildcard address, the module-level :data:`DEFAULT_ALLOWED_ORIGINS`
    is set to the server's own origin (``http://{WEBUI_HOST}:{WEBUI_PORT}``) so
    the default configuration permits same-origin requests while still rejecting
    cross-origin ones.

    When the bind host is a wildcard address (``0.0.0.0`` or ``::``), the
    default is left empty: ``http://0.0.0.0:8080`` is not an origin any browser
    sends, so it would be an unreachable allow-list entry. An empty default
    keeps :func:`get_allowed_origins` returning ``frozenset()``, which makes
    :func:`_origin_allowed` fall back to deriving each request's own origin from
    its ``Host`` header at request time.

    When ``SERENDIPITY_WEBUI_ALLOWED_ORIGINS`` is set (non-blank), the explicit
    value wins via :func:`get_allowed_origins`, so no startup default is needed
    and :data:`DEFAULT_ALLOWED_ORIGINS` is left as ``None``.

    The effective allow-list is logged at INFO level whether or not the variable
    was already set, and the wildcard-host case additionally logs an informative
    INFO message explaining that the allow-list was left unset.

    This is intended to run once at application startup. It resolves the default
    into the module-level :data:`DEFAULT_ALLOWED_ORIGINS` attribute and does not
    mutate ``os.environ``.
    """
    global DEFAULT_ALLOWED_ORIGINS
    if os.environ.get(DEFAULT_ALLOWED_ORIGINS_ENV, "").strip():
        DEFAULT_ALLOWED_ORIGINS = None
    elif _is_wildcard_host(WEBUI_HOST):
        DEFAULT_ALLOWED_ORIGINS = frozenset()
        logger.info(
            "TTS allowed origins left unset (wildcard bind host %s); "
            "same-origin requests are derived from each request's Host header",
            WEBUI_HOST,
        )
    else:
        DEFAULT_ALLOWED_ORIGINS = frozenset({f"http://{WEBUI_HOST}:{WEBUI_PORT}"})
    logger.info("TTS allowed origins: %s", sorted(get_allowed_origins()))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: populate the default allow-list on startup."""
    populate_default_allowed_origins()
    yield


app = FastAPI(title="Serendipity Web UI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


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


def get_allowed_origins() -> frozenset[str]:
    """Return the set of origins allowed to drive the TTS proxy.

    Origins are read from the ``SERENDIPITY_WEBUI_ALLOWED_ORIGINS`` environment
    variable, a comma-separated list of full origins (scheme + netloc, e.g.
    ``http://127.0.0.1:8080`` or ``https://ui.example.com``; no path). Each
    entry is normalised by stripping surrounding whitespace, lowercasing, and
    stripping a single trailing slash; empty entries are ignored. The variable
    is read lazily on each call so tests (and deployments) can change it without
    restarting the process.

    When the variable is unset or blank, the module-level
    :data:`DEFAULT_ALLOWED_ORIGINS` (resolved at startup by
    :func:`populate_default_allowed_origins`) is returned if it has been set;
    otherwise (``None``) the empty set is returned.

    :returns: The normalised set of allowed origins.
    :rtype: frozenset[str]
    """
    raw = os.environ.get(DEFAULT_ALLOWED_ORIGINS_ENV, "")
    if raw.strip():
        allowed: set[str] = set()
        for entry in raw.split(","):
            entry = entry.strip().lower()
            if not entry:
                continue
            allowed.add(entry.removesuffix("/"))
        return frozenset(allowed)
    if DEFAULT_ALLOWED_ORIGINS is not None:
        return DEFAULT_ALLOWED_ORIGINS
    return frozenset()


def _trust_forwarded_proto() -> bool:
    """Return True when the forwarded ``X-Forwarded-Proto`` header should be trusted.

    This is an opt-in controlled by the ``SERENDIPITY_WEBUI_TRUST_FORWARDED_PROTO``
    environment variable. It must only be enabled when the app is reachable
    exclusively through a reverse proxy that overwrites/strips the
    ``X-Forwarded-Proto`` header; otherwise a client could spoof the header and
    influence the derived self-origin scheme. Enabling it only ever widens the
    derived *self* origin's scheme — never the host — so it cannot be used to
    allow a foreign host.

    The variable is treated as truthy (case-insensitive, whitespace-stripped)
    only when it is exactly ``1``, ``true``, ``yes``, or ``on``; any other value
    (including unset or blank) is treated as False.

    :returns: True when the variable is set to a truthy value, False otherwise.
    :rtype: bool
    """
    value = os.environ.get(TRUST_FORWARDED_PROTO_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _forwarded_scheme(request: Request) -> str | None:
    """Return the trusted forwarded scheme from ``X-Forwarded-Proto``, or None.

    When :func:`_trust_forwarded_proto` is False the header is never consulted
    and this returns None. When it is True, the ``X-Forwarded-Proto`` header is
    read, the first comma-separated value is taken (proxies may chain), it is
    stripped and lowercased, and it is returned only when it is exactly ``http``
    or ``https``; otherwise None is returned (missing or empty header, or any
    other value such as ``ftp`` or garbage).

    ``X-Forwarded-Host`` is never read or trusted: the host must keep coming
    from the ``Host`` header.

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: ``"http"`` or ``"https"`` when a valid forwarded scheme is present
        and trusted, None otherwise.
    :rtype: str | None
    """
    if not _trust_forwarded_proto():
        return None
    forwarded = request.headers.get("x-forwarded-proto")
    if not forwarded:
        return None
    first = forwarded.split(",")[0].strip().lower()
    if first in {"http", "https"}:
        return first
    return None


def _origin_allowed(request: Request) -> bool:
    """Return True when the request is permitted to drive the TTS proxy.

    The decision is made primarily from the ``Origin`` header, which is matched
    exactly (scheme + netloc, case-insensitive, trailing slash stripped) against
    the server-side allow-list from ``SERENDIPITY_WEBUI_ALLOWED_ORIGINS``. When
    that allow-list is empty (the default when the variable is unset), the check
    falls back to the request's own origin — derived from the request scheme and
    the ``Host`` header — so the default configuration rejects only cross-origin
    requests rather than every request that carries an ``Origin`` header. In
    that empty-allow-list fallback the ``Host`` header is used to derive the
    request's *own* origin, but ONLY when its netloc is an IP literal or
    ``localhost`` (``127.0.0.1``/``::1``); a DNS name is rejected because a DNS
    name can be rebound to ``127.0.0.1`` (DNS rebinding). It is therefore never
    used to match an arbitrary foreign ``Origin``; the ``Referer`` header is
    never used for the decision and never trusted (both ``Referer`` and ``Host``
    are spoofable).

    Because a TLS-terminating reverse proxy makes ``request.url.scheme`` ``http``
    while the browser sees ``https``, the empty-allow-list fallback accepts BOTH
    ``http://<host>`` and ``https://<host>`` for the request's own host — same
    host only, and only when that host is an IP literal or ``localhost``, so this
    does not permit any foreign origin and is rebinding-safe against
    attacker-controlled DNS names. ``X-Forwarded-Proto`` is honoured ONLY when
    ``SERENDIPITY_WEBUI_TRUST_FORWARDED_PROTO`` is set to a truthy value, and even
    then only to choose the scheme of the self-origin, never the host. For
    TLS-terminated deployments (and any deployment reachable via a real DNS
    hostname), operators should set ``SERENDIPITY_WEBUI_ALLOWED_ORIGINS``
    explicitly to the real https origin(s), which is the most explicit and
    recommended option.

    When ``Origin`` is absent the decision is based on ``Sec-Fetch-Site``, not
    ``Sec-Fetch-Mode``:

    * ``Sec-Fetch-Site: same-origin`` (case-insensitive, whitespace stripped) is
      the positive same-origin signal: browsers omit ``Origin`` on same-origin
      GETs but still send ``Sec-Fetch-Site: same-origin``, so such requests are
      allowed.
    * Any other ``Sec-Fetch-Site`` value (``same-site``, ``cross-site``,
      ``none``) is rejected: a browser that sends fetch metadata always sends a
      correct ``Sec-Fetch-Site``, so a cross-site/same-site fetch is exactly
      what must be blocked, and ``none`` (user-initiated navigation) has no
      business calling these JSON/audio APIs.
    * A request with no ``Sec-Fetch-Site`` at all is assumed to be a non-browser
      client (curl, server-to-server, tests) and is allowed — but only when it
      also lacks a ``Sec-Fetch-Mode`` header. ``Sec-Fetch-Mode`` is therefore
      only a secondary "does this look like a browser?" detector for the
      no-``Sec-Fetch-Site`` case; it is not the same-origin distinction and is
      never consulted when ``Sec-Fetch-Site`` is present.

    ``Origin: null`` is a serialized opaque origin. Browsers send the literal
    header value ``null`` for requests from contexts that have an opaque
    (non-hierarchical) origin and therefore cannot name themselves, most
    commonly: (a) a sandboxed iframe (``<iframe sandbox>`` without
    ``allow-same-origin``), (b) a document loaded from a ``file://`` URL (e.g. a
    locally-saved copy of an HTML page), (c) a ``data:`` or ``blob:`` URL used
    as a top-level document, and (d) after a cross-origin redirect that taints
    the origin. It is NOT the absence of an origin — it is a distinct,
    explicitly-sent value meaning "I cannot name my origin".

    ``Origin: null`` is rejected by default. The ``null`` origin is shared by
    every such opaque-origin context, so allow-listing it is all-or-nothing: it
    does NOT restrict access to one specific app or domain. Any sandboxed iframe
    on any site, any local ``file://`` page, and any ``data:``/``blob:``
    document in the user's browser would then be allowed to call the TTS proxy —
    a much broader grant than a normal named-origin entry, which is why it is off
    by default.

    Allow-listing the literal string ``null`` is legitimately needed only when
    the deployment genuinely serves this UI from an opaque-origin context: most
    realistically, when the UI is opened as a local ``file://`` HTML page rather
    than from the web server, or when the UI is embedded in a sandboxed iframe
    WITHOUT ``allow-same-origin`` and that embedder is trusted. In those setups
    requests carry ``Origin: null`` and would otherwise be rejected, breaking
    TTS/voice features.

    By adding ``null`` to ``SERENDIPITY_WEBUI_ALLOWED_ORIGINS``, operators accept
    that EVERY opaque-origin context in the user's browser (any sandboxed iframe,
    any ``file://`` page, any ``data:``/``blob:`` document) is allow-listed, not
    just the intended one. Only enable it when it is specifically needed, and
    combine it with the understanding that the allow-list is a coarse
    defence-in-depth control, not the primary SSRF defence (which is the selector
    design). Do NOT add ``null`` "just in case": prefer serving the UI from a
    normal named origin and, for sandboxed embeds, add ``allow-same-origin`` and
    use the real origin where possible. Only add ``null`` when the deployment
    truly runs from an opaque origin and the trade-off is understood.

    Trade-off: the ``Sec-Fetch-Site``/``Sec-Fetch-Mode`` headers are
    client-supplied and therefore spoofable by non-browser clients. This check
    is defence-in-depth only for browsers that honour the fetch-metadata spec; a
    non-browser client that simply omits all fetch-metadata headers is still
    allowed, which is why ``/api/tts-server`` still returns the configured URL
    to non-browser clients. Correctness and SSRF-safety do not depend on this
    check (SSRF is eliminated by the selector design).

    :param request: The incoming HTTP request.
    :type request: Request
    :returns: True when the request is allowed, False otherwise.
    :rtype: bool
    """
    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        if referer is not None:
            logger.info("TTS request has no Origin header (Referer=%s); treating as non-browser client", referer)
        sec_fetch_site = request.headers.get("sec-fetch-site")
        if sec_fetch_site is not None:
            return sec_fetch_site.strip().lower() == "same-origin"
        return request.headers.get("sec-fetch-mode") is None

    allowed = get_allowed_origins()
    if not allowed:
        host = request.headers.get("host")
        if host:
            host = host.strip().lower()
            try:
                hostname = urlsplit("//" + host).hostname
            except ValueError:
                return False
            if not hostname or (hostname not in {"localhost", "127.0.0.1", "::1"} and not _is_ip_literal(hostname)):
                return False
            scheme = _forwarded_scheme(request)
            if scheme is None:
                scheme = request.url.scheme
            entries: set[str] = {f"{scheme}://{host}".rstrip("/")}
            if scheme == "http":
                entries.add(f"https://{host}".rstrip("/"))
            elif scheme == "https":
                entries.add(f"http://{host}".rstrip("/"))
            allowed = frozenset(entries)

    if origin == "null":
        return "null" in allowed

    parsed = urlparse(origin)
    if not parsed.scheme or not parsed.netloc:
        return False

    normalised = f"{parsed.scheme}://{parsed.netloc}".lower().rstrip("/")
    return normalised in allowed


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

    Cross-origin requests are rejected before any outbound work: the request's
    ``Origin`` header (scheme + netloc) must match the server-side allow-list
    ``SERENDIPITY_WEBUI_ALLOWED_ORIGINS`` (see :func:`_origin_allowed`). When
    that allow-list is unset it defaults to the server's own origin, so the
    default configuration rejects only cross-origin requests; the request-time
    fallback accepts both the ``http`` and ``https`` forms of the request's own
    host only when that host is an IP literal or ``localhost`` (a DNS name is
    rejected to prevent DNS rebinding), so TLS-terminating reverse proxies work
    out of the box, and ``X-Forwarded-Proto`` is only honoured when explicitly
    trusted via ``SERENDIPITY_WEBUI_TRUST_FORWARDED_PROTO``. The ``Referer`` and
    ``Host`` headers are never used for that decision.

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
    server URL to unrelated origins: the ``Origin`` header (scheme + netloc) is
    matched against the server-side ``SERENDIPITY_WEBUI_ALLOWED_ORIGINS``
    allow-list (the ``Referer`` and ``Host`` headers are never used for the
    decision); when the allow-list is unset it defaults to the server's own
    origin, and the request-time fallback accepts both the ``http`` and ``https``
    forms of the request's own host only when that host is an IP literal or
    ``localhost`` (a DNS name is rejected to prevent DNS rebinding), so
    TLS-terminating reverse proxies work out of the box; ``X-Forwarded-Proto``
    is only honoured when explicitly trusted via
    ``SERENDIPITY_WEBUI_TRUST_FORWARDED_PROTO``. Requests without an
    ``Origin`` header are allowed only when
    ``Sec-Fetch-Site: same-origin`` (a same-origin browser fetch, since browsers
    omit ``Origin`` on same-origin GETs), and rejected for any other
    ``Sec-Fetch-Site`` value (``same-site``/``cross-site``/``none``). A request
    with no ``Sec-Fetch-Site`` and no ``Sec-Fetch-Mode`` is treated as a
    non-browser client and allowed; ``Sec-Fetch-Mode`` is only a fallback
    browser detector, not a trust signal on its own. Trade-off: these headers
    are client-supplied and spoofable, so this is defence-in-depth only; a
    non-browser client that omits them is still allowed, which is why this
    endpoint still returns the configured URL to non-browser clients.

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

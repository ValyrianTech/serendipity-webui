# Serendipity Web UI

> **⚠️ Note:** The Serendipity backend server has not been publicly released yet. This repository contains only the web UI frontend, which requires the backend to function. Please check back later for updates on the full release.

A standalone web interface for the Serendipity AI agent platform. This application provides a modern, responsive UI for managing AI agents, conversations, workflows, and system configurations.

![Serendipity](static/images/SERENDIPITY_icon.png)

## Features

- **Agent Management** - Select, configure, and customize AI agents
- **Conversations** - Real-time chat with AI agents via WebSocket streaming
- **Workflows** - Create and execute automated workflows
- **Voice Support** - Voice recording, transcription, and TTS playback
- **File Handling** - Upload and stage files for conversation context
- **LLM Configuration** - Configure multiple LLM providers (OpenAI, Anthropic, Groq, Ollama, vLLM, etc.)
- **MCP Servers** - Manage Model Context Protocol server connections
- **Toolsets** - Create collections of tools from MCP servers
- **Dark/Light Mode** - Toggle between themes
- **Bitcoin Authentication** - Secure API requests with Bitcoin message signing
- **Session Unlock** - Decrypted WIF is held in per-tab sessionStorage with a Lock button, avoiding repeated password prompts within a tab

## Tech Stack

- **Backend**: FastAPI (Python)
- **Frontend**: Vanilla JavaScript (ES Modules), TailwindCSS
- **Templating**: Jinja2
- **Authentication**: Bitcoin WIF key signing
- **Real-time**: WebSocket for streaming responses

## Prerequisites

- Python 3.12+ (for local development)
- Docker (for containerized deployment)
- A running Serendipity backend server

## Quick Start

### Using Docker (Recommended)

```bash
# Build the image
docker build -t serendipity-webui .

# Run the container
docker run -p 8080:8080 serendipity-webui
```

Access the application at `http://localhost:8080`

> **Note:** The server binds to loopback (`127.0.0.1`) by default, so in Docker you must set `SERENDIPITY_WEBUI_HOST=0.0.0.0` (and publish the port) to reach the UI from outside the container:
>
> ```bash
> docker run -e SERENDIPITY_WEBUI_HOST=0.0.0.0 -p 8080:8080 serendipity-webui
> ```

### Local Development

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run the development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

## Configuration

On first launch, you'll be prompted to configure:

1. **Server Connection**
   - Server Address (default: `localhost`)
   - Server Port (default: `42069`)
   - WebSocket Port (default: `8765`)

2. **WIF Key**
   - Enter an existing WIF key or generate a new one
   - A password is required; the WIF key is always stored encrypted in localStorage (never in plaintext). The encrypted blob uses PBKDF2-HMAC-SHA256 (600,000 iterations) with ChaCha20 + HMAC-SHA256 (encrypt-then-MAC) in pure JavaScript; the iteration count is stored in a self-describing blob header, and older stored blobs remain decryptable
   - Because key derivation at 600,000 PBKDF2 iterations takes a moment, the unlock, setup "Save & Continue", and change-key "Save" buttons show a brief "Unlocking…"/"Encrypting…" busy state (and are disabled) while the key is derived, so the UI does not appear frozen.
   - The decrypted key is held in per-tab sessionStorage, so you unlock once per tab session and page navigations within that tab no longer re-prompt for the password
   - The decrypted key is cleared when the tab closes and is never written to disk; use the sidebar **Lock** button to forget the unlocked key for the current session

All settings are stored in your browser's localStorage.

### Environment configuration

Server-side settings are read from environment variables. These can be provided via a `.env` file in the project root, which is loaded automatically at startup (`python-dotenv`). Copy the template and adjust the values:

```bash
cp .env.example .env
```

- `SERENDIPITY_WEBUI_HOST` — host address the web UI binds to (default `127.0.0.1`; set to `0.0.0.0` to expose the UI on all interfaces).
- `SERENDIPITY_WEBUI_PORT` — port the web UI listens on (default `8080`).
- `SERENDIPITY_TTS_ALLOWED_HOSTS` — comma-separated allowlist of TTS proxy host names (see [TTS proxy security](#tts-proxy-security)).
- `SERENDIPITY_TTS_ALLOW_ANY_PUBLIC_HOST` — opt-in flag (truthy: `1`/`true`/`yes`/`on`, case-insensitive) that permits arbitrary public hosts when `SERENDIPITY_TTS_ALLOWED_HOSTS` is unset (see [TTS proxy security](#tts-proxy-security)).

`.env` is gitignored; commit only `.env.example`.

### TTS proxy security

The `/api/tts-proxy` endpoint validates the `server` query parameter before forwarding to prevent SSRF to internal or non-HTTP resources:

- Cross-origin browser requests are rejected with HTTP 403 (`{"error": "Cross-origin TTS proxy request rejected"}`): the `Origin` header (falling back to `Referer`) is compared against the request `Host` header, and requests that do not match are refused. When neither header is present the request is treated as same-origin.
- Only `http` and `https` schemes are allowed.
- If the `SERENDIPITY_TTS_ALLOWED_HOSTS` environment variable is set (a comma-separated list of host names), the resolved host must be in that allowlist.
- When the host is not allowlisted, it is resolved via DNS and any resulting private, loopback, link-local, reserved, multicast, or unspecified IP is blocked (IP literals are checked as well).
- **Allowlist entries bypass all IP checks:** when a host matches an entry in `SERENDIPITY_TTS_ALLOWED_HOSTS`, DNS resolution and ALL private/loopback/link-local/reserved/multicast/unspecified IP checks are skipped entirely for that host.
- Invalid or disallowed servers return HTTP 400 with `{"error": "Invalid or disallowed TTS server"}`.
- Non-audio (`audio/*`) responses return HTTP 502 (`"TTS server returned non-audio content"`).
- Unexpected proxy failures return HTTP 500 with a generic `"Internal TTS proxy error"` message; details are logged server-side rather than returned to the client.
- The 25 MB response-size limit is enforced while streaming: the proxy aborts the transfer with HTTP 502 (`"TTS response too large"`) as soon as the accumulated body exceeds the cap, rather than downloading the full response first.
- For non-allowlisted hosts, the outbound connection is pinned to the single validated (non-blocked) IP resolved during validation — `httpx` is pointed at that IP (port preserved) while the original hostname and port are sent in the `Host` header — so the hostname is not re-resolved at connect time.

When `SERENDIPITY_TTS_ALLOWED_HOSTS` is unset, the proxy is **disabled by default**: a host is only permitted if the opt-in `SERENDIPITY_TTS_ALLOW_ANY_PUBLIC_HOST` flag is set to a truthy value (`1`/`true`/`yes`/`on`, case-insensitive). Otherwise the request is rejected with HTTP 400 (`"host not allowed"`), and no public host is permitted.

> **Note:** For non-allowlisted hosts, the outbound connection is pinned to the single IP that was resolved and validated, so a hostname whose DNS answer changes between validation and connection (a DNS-rebinding / TOCTOU attack) can no longer reach a different (internal) address — the previously open TOCTOU window is closed. Allowlisted hosts bypass DNS resolution and the IP checks entirely (see the warning below), so combine the allowlist with network-level egress controls for defense in depth.

> **Warning:** Because an allowlisted host bypasses DNS resolution and the private-IP checks entirely, a misconfigured allowlist entry (for example an internal hostname, `localhost`, or `127.0.0.1`) reopens the SSRF path to that host. Only ever add trusted, public hosts to `SERENDIPITY_TTS_ALLOWED_HOSTS`, and rely on network-level egress controls for defense in depth rather than on the allowlist to block internal destinations.

### Template-to-JavaScript interpolation

Server-side values interpolated into JavaScript in Jinja2 templates (e.g. `agent_name`, `workflow_id`, `node_id`) are rendered using Jinja2's `tojson` filter so they are safely encoded as JSON, preventing a crafted value from breaking out of a JavaScript string literal (JS injection / XSS).

### HTML escaping in templates

Dynamic values (e.g. agent names, descriptions, image URLs, message ids, sender names, tool/role/skill names) inserted into template `innerHTML` via JavaScript string interpolation are escaped with a shared `escapeHtml` helper covering `&`, `<`, `>`, `\"` and `'`, so values are safe in both element-text and quoted-attribute/inline-handler contexts. Inline `onclick` handlers that previously embedded untrusted values now use `data-*` attributes with `addEventListener` bindings. The TTS text-extraction helper (`getTextOnlyContent` in `conversation.html`) likewise sanitizes message content through `sanitizeHtml` (DOMPurify) before parsing it via `innerHTML`, so active markup is stripped as well as escaped. The same `escapeHtml` helper also guards the instruction/resource preview fallback in `skills.html` (`updateInstructionsPreview` and `setResourceTab`), which is used when the vendored `marked` library is unavailable, so unescaped content can no longer reach `innerHTML`.

## Project Structure

```
serendipity-webui/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application & routes
│   ├── vulture_whitelist.py # Vulture false-positive whitelist (wired via [tool.vulture]; excluded from coverage)
│   └── templates/           # Jinja2 HTML templates
│       ├── base.html        # Base template with sidebar
│       ├── agents.html      # Agent selection page
│       ├── agent_home.html  # Agent dashboard
│       ├── conversation.html # Chat interface
│       ├── conversations.html # Conversation list
│       ├── edit_agent.html  # Agent configuration
│       ├── settings.html    # App settings
│       ├── llms.html        # LLM configuration
│       ├── mcps.html        # MCP server config
│       ├── toolsets.html    # Toolset management
│       ├── skills.html      # Skills management
│       ├── scheduled_tasks.html # Scheduled tasks
│       └── workflows.html   # Workflow editor
├── static/
│   ├── css/
│   │   ├── style.css        # Custom styles
│   │   ├── tailwind.css     # TailwindCSS source
│   │   ├── tailwind.output.css # Compiled CSS
│   │   └── github-dark.min.css
│   ├── images/
│   │   └── SERENDIPITY_icon.png
│   └── js/
│       ├── api.js           # API client
│       ├── bitcoin.js       # Bitcoin signing utilities
│       └── vendor/          # Third-party libraries
├── unittests/               # Pytest test suite
│   ├── conftest.py          # Shared fixtures (FastAPI TestClient)
│   ├── vulture_whitelist.py # Vulture false-positive whitelist (wired via [tool.vulture]; excluded from coverage)
│   ├── test_pages.py        # Page route tests
│   ├── test_main_entrypoint.py # __main__ entrypoint test
│   ├── test_template_js_interpolation.py # JS interpolation (tojson) security tests
│   ├── test_tts_proxy.py    # /api/tts-proxy endpoint tests
│   ├── test_xss_sanitization.py # XSS sanitization tests
│   ├── test_session_wif.py  # Session WIF unlock tests (QuickJS)
│   ├── test_wif_busy_state.py # withWifBusy() busy-state UI helper tests
│   ├── test_wif_encryption.py # WIF at-rest encryption tests (SCv2/SCv3/legacy, KAT, tamper)
│   ├── test_wif_validation.py # WIF parse/validation tests
│   ├── test_innerhtml_data_escaping.py # innerHTML/data-escaping regression tests
│   └── test_api_json_error_handling.py # API client JSON/status error-handling tests
├── Dockerfile               # Multi-stage Docker build
├── entrypoint.sh           # Container entrypoint
├── requirements.txt        # Python dependencies
├── tailwind.config.js      # TailwindCSS configuration
└── build-css.sh            # CSS build script
```

## Pages

| Route | Description |
|-------|-------------|
| `/` | Agent selection |
| `/agent/{name}` | Agent dashboard |
| `/agent/{name}/conversations` | Conversation list |
| `/agent/{name}/conversation` | Chat interface |
| `/agent/{name}/edit` | Edit agent settings |
| `/agent/{name}/workflows` | Workflow editor |
| `/settings` | Application settings |
| `/llms` | LLM configurations |
| `/mcps` | MCP server configurations |
| `/toolsets` | Toolset management |
| `/skills` | Skills management |
| `/scheduled-tasks` | Scheduled tasks |
| `/api/tts-proxy` | TTS synthesis proxy endpoint (SSRF-protected; see [Configuration](#configuration)) |

## API Integration

The web UI connects to a Serendipity backend server. All POST requests are authenticated using Bitcoin message signing:

```javascript
// Request format for authenticated endpoints
{
  "message": "/sha256/{hash}",
  "signature": "<bitcoin_signature>",
  "address": "<bitcoin_address>",
  "data": { /* request payload */ }
}
```

Every API call routes its `fetch` response through a shared `parseJsonResponse()` helper (`static/js/api.js`), which checks the HTTP status and `Content-Type` before parsing. Failed (non-2xx) requests throw an `Error` carrying the server's JSON `error`/`detail` message when the body is `application/json` (falling back to the status/status text otherwise), and successful responses that are not `application/json` are reported clearly as a `non-JSON response` error rather than surfacing an unhelpful JSON parse failure.

## Development

### Rebuilding CSS

If you modify TailwindCSS classes:

```bash
./build-css.sh
```

### Adding New Pages

1. Create a new template in `app/templates/`
2. Add a route in `app/main.py`
3. Extend `base.html` for consistent navigation

## Testing

Tests live in `unittests/` and are run with pytest:

```bash
# Run the test suite
pytest

# Run with coverage
pytest --cov=app
```

The pytest configuration in `pyproject.toml` sets `testpaths = ["unittests"]` and `pythonpath = ["."]`. Coverage is collected from the `app` package (`source = ["app"]`), with `app/vulture_whitelist.py` and `unittests/vulture_whitelist.py` omitted from coverage as vulture whitelist files of false positives, wired into vulture via the `[tool.vulture]` `paths` list in `pyproject.toml`.

## Docker Details

The Dockerfile uses a multi-stage build for minimal image size:

- **Stage 1 (builder)**: Installs Python dependencies into a virtual environment
- **Stage 2 (runtime)**: Copies only the venv and application code

The container runs as a non-root user for security.

- **ENTRYPOINT**: Runs `uvicorn` in the foreground as PID 1 via `exec` in `entrypoint.sh`, so the server receives `SIGTERM`/`SIGINT` for graceful shutdown and the container exits with the application's status code (allowing restart policies to detect crashes).

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

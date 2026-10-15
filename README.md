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
   - Optionally encrypt with a password for secure storage

All settings are stored in your browser's localStorage.

### TTS proxy security

The `/api/tts-proxy` endpoint validates the `server` query parameter before forwarding to prevent SSRF to internal or non-HTTP resources:

- Only `http` and `https` schemes are allowed.
- If the `SERENDIPITY_TTS_ALLOWED_HOSTS` environment variable is set (a comma-separated list of host names), the resolved host must be in that allowlist.
- When the host is not allowlisted, it is resolved via DNS and any resulting private, loopback, link-local, reserved, multicast, or unspecified IP is blocked (IP literals are checked as well).
- **Allowlist entries bypass all IP checks:** when a host matches an entry in `SERENDIPITY_TTS_ALLOWED_HOSTS`, DNS resolution and ALL private/loopback/link-local/reserved/multicast/unspecified IP checks are skipped entirely for that host.
- Invalid or disallowed servers return HTTP 400 with `{"error": "Invalid or disallowed TTS server"}`.
- Non-audio (`audio/*`) responses return HTTP 502 (`"TTS server returned non-audio content"`).
- The 25 MB response-size limit is enforced while streaming: the proxy aborts the transfer with HTTP 502 (`"TTS response too large"`) as soon as the accumulated body exceeds the cap, rather than downloading the full response first.
- For non-allowlisted hosts, the outbound connection is pinned to the single validated (non-blocked) IP resolved during validation — `httpx` is pointed at that IP (port preserved) while the original hostname and port are sent in the `Host` header — so the hostname is not re-resolved at connect time.

When `SERENDIPITY_TTS_ALLOWED_HOSTS` is unset, only loopback/private/link-local/reserved/multicast/unspecified addresses are blocked; any public host is permitted.

> **Note:** For non-allowlisted hosts, the outbound connection is pinned to the single IP that was resolved and validated, so a hostname whose DNS answer changes between validation and connection (a DNS-rebinding / TOCTOU attack) can no longer reach a different (internal) address — the previously open TOCTOU window is closed. Allowlisted hosts bypass DNS resolution and the IP checks entirely (see the warning below), so combine the allowlist with network-level egress controls for defense in depth.

> **Warning:** Because an allowlisted host bypasses DNS resolution and the private-IP checks entirely, a misconfigured allowlist entry (for example an internal hostname, `localhost`, or `127.0.0.1`) reopens the SSRF path to that host. Only ever add trusted, public hosts to `SERENDIPITY_TTS_ALLOWED_HOSTS`, and rely on network-level egress controls for defense in depth rather than on the allowlist to block internal destinations.

### Template-to-JavaScript interpolation

Server-side values interpolated into JavaScript in Jinja2 templates (e.g. `agent_name`, `workflow_id`, `node_id`) are rendered using Jinja2's `tojson` filter so they are safely encoded as JSON, preventing a crafted value from breaking out of a JavaScript string literal (JS injection / XSS).

### HTML escaping in templates

Dynamic values (e.g. agent names, descriptions, image URLs, message ids, sender names, tool/role/skill names) inserted into template `innerHTML` via JavaScript string interpolation are escaped with a shared `escapeHtml` helper covering `&`, `<`, `>`, `\"` and `'`, so values are safe in both element-text and quoted-attribute/inline-handler contexts. Inline `onclick` handlers that previously embedded untrusted values now use `data-*` attributes with `addEventListener` bindings.

## Project Structure

```
serendipity-webui/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application & routes
│   ├── vulture_whitelist.py # Vulture false-positive whitelist (excluded from coverage)
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
│   ├── test_pages.py        # Page route tests
│   ├── test_main_entrypoint.py # __main__ entrypoint test
│   ├── test_template_js_interpolation.py # JS interpolation (tojson) security tests
│   ├── test_tts_proxy.py    # /api/tts-proxy endpoint tests
│   ├── test_xss_sanitization.py # XSS sanitization tests
│   └── test_innerhtml_data_escaping.py # innerHTML/data-escaping regression tests
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

The pytest configuration in `pyproject.toml` sets `testpaths = ["unittests"]` and `pythonpath = ["."]`. Coverage is collected from the `app` package (`source = ["app"]`), with `app/vulture_whitelist.py` omitted from coverage as a vulture whitelist file of false positives.

## Docker Details

The Dockerfile uses a multi-stage build for minimal image size:

- **Stage 1 (builder)**: Installs Python dependencies into a virtual environment
- **Stage 2 (runtime)**: Copies only the venv and application code

The container runs as a non-root user for security.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

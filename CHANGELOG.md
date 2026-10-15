# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Hardened the `/api/tts-proxy` endpoint against SSRF by validating the `server` query parameter before forwarding. Only `http`/`https` schemes are allowed; when `SERENDIPITY_TTS_ALLOWED_HOSTS` is set (comma-separated list of host names) the resolved host must be allowlisted, otherwise private, loopback, link-local, reserved, multicast, and unspecified IPs (including IP literals) are blocked. Invalid/disallowed servers return HTTP 400, non-audio responses return HTTP 502, and responses larger than 25 MB return HTTP 502.
- Closed a DNS-rebinding / TOCTOU bypass of the `/api/tts-proxy` SSRF filter: for non-allowlisted hosts, the outbound connection is now pinned to the single resolved, validated IP address (with the original hostname and port preserved in the `Host` header), so `httpx` no longer re-resolves the hostname at connect time.
- The 25 MB `/api/tts-proxy` response-size cap is enforced incrementally while reading the upstream response, so an oversized body is rejected with HTTP 502 before it is forwarded to the client. Note: the upstream body is still accumulated in memory (up to the cap plus one chunk) before being returned to the client; it is not streamed straight through.
- Stopped leaking raw exception text from the `/api/tts-proxy` catch-all handler. Unexpected failures now log details server-side (via `logger.exception`) and return a generic `{"error": "Internal TTS proxy error"}` with HTTP 500.
- Fixed a stored DOM XSS vulnerability: server/agent-supplied values interpolated into template `innerHTML` (including `src`/`alt`/`title`/`value` attributes and inline `onclick` handlers) are now escaped via a shared `escapeHtml` helper that encodes `&`, `<`, `>`, `\"` and `'`. Inline `onclick` handlers that embedded untrusted values were replaced with `data-*` attributes and `addEventListener` bindings. New regression tests were added in `unittests/test_innerhtml_data_escaping.py`.

### Fixed

- Template values interpolated into JavaScript string literals in several Jinja2 templates (`agent_home.html`, `conversation.html`, `conversations.html`, `edit_agent.html`, `workflows.html`) are now rendered via Jinja2's `tojson` filter, preventing JS injection/XSS from crafted template values.

### Changed

- Added type annotations and docstrings in `app/main.py` and refactored `app/vulture_whitelist.py` to use a string-literal list (no user-visible behavior change).
- Added type annotations and fixed mypy errors in `app/main.py`, and resolved a vulture dead-code false positive in the TTS proxy test (no user-visible behavior change).

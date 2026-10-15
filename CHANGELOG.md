# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- Hardened the `/api/tts-proxy` endpoint against SSRF by validating the `server` query parameter before forwarding. Only `http`/`https` schemes are allowed; when `SERENDIPITY_TTS_ALLOWED_HOSTS` is set (comma-separated list of host names) the resolved host must be allowlisted, otherwise private, loopback, link-local, reserved, multicast, and unspecified IPs (including IP literals) are blocked. Invalid/disallowed servers return HTTP 400, non-audio responses return HTTP 502, and responses larger than 25 MB return HTTP 502.

### Fixed

- Template values interpolated into JavaScript string literals in several Jinja2 templates (`agent_home.html`, `conversation.html`, `conversations.html`, `edit_agent.html`, `workflows.html`) are now rendered via Jinja2's `tojson` filter, preventing JS injection/XSS from crafted template values.

### Changed

- Added type annotations and docstrings in `app/main.py` and refactored `app/vulture_whitelist.py` to use a string-literal list (no user-visible behavior change).

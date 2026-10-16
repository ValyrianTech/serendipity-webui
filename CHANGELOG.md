# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Added a regression test (`unittests/test_save_wif.py`) that runs the real `static/js/bitcoin.js` `saveWif()` through QuickJS to verify it refuses to persist an unencrypted WIF and leaves `localStorage` untouched; `quickjs` was added as a dev/test dependency.
- Added sessionStorage-based session WIF unlock: the decrypted WIF is held in per-tab `sessionStorage` (under `wif_session_key`) so it is restored on page navigation without re-prompting for the password, cleared automatically when the tab closes, and never written to disk or shared across tabs. The encrypted WIF continues to live in `localStorage`. New `saveSessionWif`/`loadSessionWif`/`clearSessionWif` helpers were added to `static/js/bitcoin.js`.
- Added a new **Lock** button in the sidebar (and `lockSession()` function) that clears the session key and returns to the unlock (or setup) modal.
- Added regression tests (`unittests/test_session_wif.py`) that run the real `static/js/bitcoin.js` session helpers via QuickJS.

### Security

- Hardened the `/api/tts-proxy` endpoint against SSRF by validating the `server` query parameter before forwarding. Only `http`/`https` schemes are allowed; when `SERENDIPITY_TTS_ALLOWED_HOSTS` is set (comma-separated list of host names) the resolved host must be allowlisted, otherwise private, loopback, link-local, reserved, multicast, and unspecified IPs (including IP literals) are blocked. Invalid/disallowed servers return HTTP 400, non-audio responses return HTTP 502, and responses larger than 25 MB return HTTP 502.
- Closed a DNS-rebinding / TOCTOU bypass of the `/api/tts-proxy` SSRF filter: for non-allowlisted hosts, the outbound connection is now pinned to the single resolved, validated IP address (with the original hostname and port preserved in the `Host` header), so `httpx` no longer re-resolves the hostname at connect time.
- The 25 MB `/api/tts-proxy` response-size cap is enforced incrementally while reading the upstream response, so an oversized body is rejected with HTTP 502 before it is forwarded to the client. Note: the upstream body is still accumulated in memory (up to the cap plus one chunk) before being returned to the client; it is not streamed straight through.
- Stopped leaking raw exception text from the `/api/tts-proxy` catch-all handler. Unexpected failures now log details server-side (via `logger.exception`) and return a generic `{"error": "Internal TTS proxy error"}` with HTTP 500.
- Fixed a stored DOM XSS vulnerability: server/agent-supplied values interpolated into template `innerHTML` (including `src`/`alt`/`title`/`value` attributes and inline `onclick` handlers) are now escaped via a shared `escapeHtml` helper that encodes `&`, `<`, `>`, `\"` and `'`. Inline `onclick` handlers that embedded untrusted values were replaced with `data-*` attributes and `addEventListener` bindings. New regression tests were added in `unittests/test_innerhtml_data_escaping.py`.
- The WIF private key is no longer persisted to localStorage in plaintext: a password is now required and the key is always stored encrypted. `saveWif()` now refuses to store an unencrypted WIF, and the unlock modal is always shown when a stored key exists.

### Fixed

- Template values interpolated into JavaScript string literals in several Jinja2 templates (`agent_home.html`, `conversation.html`, `conversations.html`, `edit_agent.html`, `workflows.html`) are now rendered via Jinja2's `tojson` filter, preventing JS injection/XSS from crafted template values.
- Hardened WIF private-key parsing in `static/js/bitcoin.js`: `wifToPrivateKey(wif)` previously had a dead ternary and did not validate the decoded WIF structure, so it could return a wrong/truncated key for malformed input. It now rejects any decoded payload whose length is not exactly 37 or 38 bytes (`Invalid WIF: bad length`), verifies the 4-byte Base58Check checksum via double-SHA256 (`Invalid WIF: bad checksum`), requires the mainnet version byte `0x80` (`Invalid WIF: unsupported version byte`), and for compressed WIF requires the trailing flag byte to be `0x01` (`Invalid WIF: bad compression flag`), returning exactly the 32-byte private key following the version byte. The setup UI (`app/templates/base.html`) now calls `wifToAddress(wif)` before saving settings and refuses to save an invalid WIF with the toast `Invalid WIF key: please check for typos`; the `wifToAddress` catch block now logs `Invalid WIF:` plus the error message. Regression tests were added in `unittests/test_wif_validation.py`.
- As a consequence of the WIF parsing hardening above, `signMessage()` and `signData()` in `static/js/bitcoin.js` now propagate the `Invalid WIF` exception thrown by `wifToPrivateKey()` rather than swallowing it: neither function wraps the call in `try`/`catch`, so a malformed WIF rejects the returned promise. The ~30 signing helpers in `static/js/api.js` (which import `signData` from `./bitcoin.js`) likewise do not catch, so a malformed WIF now surfaces an `Invalid WIF` error at every signing call site instead of silently signing against a wrong/truncated key. Regression tests were added in `unittests/test_wif_signing_propagation.py`.

### Changed

- Legacy plaintext WIF entries in `localStorage` (the pre-encryption shape `{encrypted: false, data: "<raw WIF>", timestamp}`) are now detected on startup via a new `isLegacyPlaintextWif()` helper in `static/js/bitcoin.js`, cleared from `localStorage`, and the user is routed to the setup modal with an explicit message telling them their previously-stored plaintext key was removed for security and must be re-entered. Rationale: a plaintext WIF cannot be encrypted without a password and must never remain at rest, and `decryptWif()` can never succeed against a plaintext entry, so the old behaviour left such users permanently stuck on the unlock modal with no explanation.
- Changed `unittests/vulture_whitelist.py` to use a bare-name expression statement (matching `app/vulture_whitelist.py`) and wired both whitelist files into vulture via the `[tool.vulture]` `paths` list in `pyproject.toml` (no user-visible behavior change).
- Added type annotations and docstrings in `app/main.py` and refactored `app/vulture_whitelist.py` to use a string-literal list (no user-visible behavior change).
- Added type annotations and fixed mypy errors in `app/main.py`, and resolved a vulture dead-code false positive in the TTS proxy test (no user-visible behavior change).

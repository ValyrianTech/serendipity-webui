"""Tests for the /api/tts-proxy endpoint defined in app.main."""

import logging
from contextlib import contextmanager
from unittest.mock import patch

import httpx
import pytest

from app import main as app_main

_real_resolve_host = app_main._resolve_host


def make_response(content=b"", status_code=200, headers=None):
    """Build a real httpx.Response so raise_for_status() behaves normally."""
    request = httpx.Request("GET", "http://tts.local/synthesize_speech/")
    return httpx.Response(status_code, content=content, headers=headers or {}, request=request)


def _resolve_to(*ips):
    """Return a fake ``_resolve_host`` implementation returning the given IPs."""

    def _resolver(host):
        return list(ips)

    return _resolver


@pytest.fixture(autouse=True)  # noqa: V103
def _patch_resolve_host(monkeypatch):
    """Patch host resolution so happy-path tests resolve to a public IP."""
    monkeypatch.setattr("app.main._resolve_host", _resolve_to("93.184.216.34"))


STREAM_CHUNK_SIZE = 3


class MockStreamResponse:
    """Minimal stand-in for the streaming response returned by ``client.stream``."""

    def __init__(self, response):
        self._response = response
        self.headers = response.headers
        self.status_code = response.status_code

    def raise_for_status(self):
        self._response.raise_for_status()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        return False

    async def aiter_bytes(self):
        content = self._response.content
        for i in range(0, len(content), STREAM_CHUNK_SIZE):
            yield content[i:i + STREAM_CHUNK_SIZE]


class MockAsyncClient:
    """Minimal stand-in for httpx.AsyncClient used by the proxy endpoint."""

    def __init__(self, response=None, exc=None, capture=None):
        self._response = response
        self._exc = exc
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc_info):
        return False

    def stream(self, method, url, params=None, headers=None):  # noqa: V107
        if self._capture is not None:
            self._capture["url"] = url
            self._capture["params"] = params
            self._capture["headers"] = headers
        if self._exc is not None:
            raise self._exc
        return MockStreamResponse(self._response)


@contextmanager
def mock_async_client(response=None, exc=None, capture=None):
    """Patch httpx.AsyncClient inside app.main for the duration of the block."""

    def factory(*args, **kwargs):
        return MockAsyncClient(response=response, exc=exc, capture=capture)

    with patch("app.main.httpx.AsyncClient", factory):
        yield capture


BASE_PARAMS = {"server": "http://tts.local", "voice": "voice-1", "text": "hello world"}


def test_success_streams_audio(client):
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 200
    assert result.content == b"AUDIO"
    assert result.headers["content-type"] == "audio/wav"
    assert result.headers["cache-control"] == "no-cache"
    assert capture["url"] == "http://93.184.216.34/synthesize_speech/"
    assert capture["headers"] == {"Host": "tts.local"}
    assert capture["params"] == {
        "voice": "voice-1",
        "text": "hello world",
        "style": "default",
        "language": "English",
    }


def test_trailing_slash_in_server_is_stripped(client):
    capture = {}
    response = make_response(content=b"A", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        client.get("/api/tts-proxy", params={**BASE_PARAMS, "server": "http://tts.local/"})

    assert capture["url"] == "http://93.184.216.34/synthesize_speech/"


def test_speed_omitted_when_default(client):
    capture = {}
    response = make_response(content=b"A", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        client.get("/api/tts-proxy", params={**BASE_PARAMS, "speed": 1.0})

    assert "speed" not in capture["params"]


def test_speed_included_when_non_default(client):
    capture = {}
    response = make_response(content=b"A", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        client.get("/api/tts-proxy", params={**BASE_PARAMS, "speed": 1.5})

    assert capture["params"]["speed"] == 1.5


def test_custom_style_and_language_forwarded(client):
    capture = {}
    response = make_response(content=b"A", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        client.get("/api/tts-proxy", params={**BASE_PARAMS, "style": "cheerful", "language": "French"})

    assert capture["params"]["style"] == "cheerful"
    assert capture["params"]["language"] == "French"


def test_custom_content_type_is_preserved(client):
    response = make_response(content=b"A", headers={"content-type": "audio/mpeg"})
    with mock_async_client(response=response):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 200
    assert result.headers["content-type"] == "audio/mpeg"


def test_missing_content_type_defaults_to_wav(client):
    response = make_response(content=b"A", headers={})
    with mock_async_client(response=response):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 200
    assert result.headers["content-type"] == "audio/wav"


def test_non_audio_content_returns_502(client):
    response = make_response(content=b'{"error": "boom"}', headers={"content-type": "application/json"})
    with mock_async_client(response=response):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 502
    assert result.json() == {"error": "TTS server returned non-audio content"}


def test_timeout_returns_504(client):
    with mock_async_client(exc=httpx.TimeoutException("too slow")):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 504
    assert result.json() == {"error": "TTS generation timed out"}


def test_http_status_error_is_propagated(client):
    response = make_response(content=b"nope", status_code=502)
    with mock_async_client(response=response):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 502
    assert result.json() == {"error": "TTS server error: 502"}


def test_unexpected_error_returns_500(client, caplog):
    with caplog.at_level(logging.ERROR):
        with mock_async_client(exc=RuntimeError("kaboom")):
            result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 500
    assert result.json() == {"error": "Internal TTS proxy error"}
    assert any(r.message == "TTS proxy request failed" and r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.parametrize("missing", ["server", "voice", "text"])
def test_required_params_are_enforced(client, missing):
    params = {key: value for key, value in BASE_PARAMS.items() if key != missing}
    result = client.get("/api/tts-proxy", params=params)
    assert result.status_code == 422


@pytest.mark.parametrize("server", ["ftp://tts.local", "file:///etc/passwd"])
def test_invalid_scheme_rejected(client, server):
    result = client.get("/api/tts-proxy", params={**BASE_PARAMS, "server": server})
    assert result.status_code == 400
    assert result.json() == {"error": "Invalid or disallowed TTS server"}


def test_missing_host_rejected(client):
    result = client.get("/api/tts-proxy", params={**BASE_PARAMS, "server": "http://"})
    assert result.status_code == 400
    assert result.json() == {"error": "Invalid or disallowed TTS server"}


def test_private_ip_literal_rejected(client):
    result = client.get("/api/tts-proxy", params={**BASE_PARAMS, "server": "http://127.0.0.1"})
    assert result.status_code == 400
    assert result.json() == {"error": "Invalid or disallowed TTS server"}


def test_link_local_metadata_ip_rejected(client):
    result = client.get("/api/tts-proxy", params={**BASE_PARAMS, "server": "http://169.254.169.254"})
    assert result.status_code == 400
    assert result.json() == {"error": "Invalid or disallowed TTS server"}


def test_resolved_private_ip_rejected(client, monkeypatch):
    monkeypatch.setattr("app.main._resolve_host", _resolve_to("127.0.0.1"))
    result = client.get("/api/tts-proxy", params=BASE_PARAMS)
    assert result.status_code == 400
    assert result.json() == {"error": "Invalid or disallowed TTS server"}


def test_resolve_failure_rejected(client, monkeypatch):
    def _raise_resolve(host):
        raise ValueError("no such host")

    monkeypatch.setattr("app.main._resolve_host", _raise_resolve)
    result = client.get("/api/tts-proxy", params=BASE_PARAMS)
    assert result.status_code == 400
    assert result.json() == {"error": "Invalid or disallowed TTS server"}


def test_allowlist_permits_otherwise_blocked_host(client, monkeypatch):
    monkeypatch.setenv("SERENDIPITY_TTS_ALLOWED_HOSTS", "tts.local")
    monkeypatch.setattr("app.main._resolve_host", _resolve_to("127.0.0.1"))
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 200
    assert result.content == b"AUDIO"
    assert capture["url"] == "http://tts.local/synthesize_speech/"
    assert capture["headers"] == {}


def test_allowlist_rejects_unlisted_host(client, monkeypatch):
    monkeypatch.setenv("SERENDIPITY_TTS_ALLOWED_HOSTS", "other.local")
    result = client.get("/api/tts-proxy", params=BASE_PARAMS)
    assert result.status_code == 400
    assert result.json() == {"error": "Invalid or disallowed TTS server"}


def test_oversized_response_returns_502(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_TTS_RESPONSE_BYTES", 4)
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 502
    assert result.json() == {"error": "TTS response too large"}


def test_response_exactly_at_cap_is_allowed(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_TTS_RESPONSE_BYTES", 4)
    response = make_response(content=b"AUDI", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 200
    assert result.content == b"AUDI"


def test_public_ip_literal_is_used_directly(client):
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get("/api/tts-proxy", params={**BASE_PARAMS, "server": "http://93.184.216.34"})

    assert result.status_code == 200
    assert capture["url"] == "http://93.184.216.34/synthesize_speech/"
    assert capture["headers"] == {"Host": "93.184.216.34"}


def test_port_is_preserved_in_pinned_request(client):
    capture = {}
    response = make_response(content=b"A", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        client.get("/api/tts-proxy", params={**BASE_PARAMS, "server": "http://tts.local:8080"})

    assert capture["url"] == "http://93.184.216.34:8080/synthesize_speech/"
    assert capture["headers"] == {"Host": "tts.local:8080"}


def test_ipv6_ip_literal_is_bracketed(client):
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get(
            "/api/tts-proxy",
            params={**BASE_PARAMS, "server": "http://[2606:4700:4700::1111]"},
        )

    assert result.status_code == 200
    assert capture["url"] == "http://[2606:4700:4700::1111]/synthesize_speech/"
    assert capture["headers"] == {"Host": "2606:4700:4700::1111"}


def test_aaaa_resolving_hostname_bracketed(client, monkeypatch):
    monkeypatch.setattr("app.main._resolve_host", _resolve_to("2606:4700:4700::1111"))
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get(
            "/api/tts-proxy",
            params={**BASE_PARAMS, "server": "http://tts6.local"},
        )

    assert result.status_code == 200
    assert capture["url"] == "http://[2606:4700:4700::1111]/synthesize_speech/"
    assert capture["headers"] == {"Host": "tts6.local"}


def test_ipv6_ip_literal_with_port_is_bracketed(client):
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get(
            "/api/tts-proxy",
            params={**BASE_PARAMS, "server": "http://[2606:4700:4700::1111]:8080"},
        )

    assert result.status_code == 200
    assert capture["url"] == "http://[2606:4700:4700::1111]:8080/synthesize_speech/"
    assert capture["headers"] == {"Host": "2606:4700:4700::1111:8080"}


def test_format_netloc_ip_brackets_v6_and_not_v4():
    assert app_main._format_netloc_ip("2606:4700:4700::1111") == "[2606:4700:4700::1111]"
    assert app_main._format_netloc_ip("93.184.216.34") == "93.184.216.34"


def test_is_blocked_ip_malformed_input():
    assert app_main._is_blocked_ip("not-an-ip") is True


def test_is_blocked_ip_public_address():
    assert app_main._is_blocked_ip("93.184.216.34") is False


def test_resolve_host_returns_ips(monkeypatch):
    def _fake_getaddrinfo(host, _port):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr(app_main.socket, "getaddrinfo", _fake_getaddrinfo)
    assert _real_resolve_host("example.com") == ["93.184.216.34"]


def test_resolve_host_raises_on_failure(monkeypatch):
    def _fail_getaddrinfo(host, _port):
        raise app_main.socket.gaierror("boom")

    monkeypatch.setattr(app_main.socket, "getaddrinfo", _fail_getaddrinfo)
    with pytest.raises(ValueError):
        _real_resolve_host("nope.invalid")

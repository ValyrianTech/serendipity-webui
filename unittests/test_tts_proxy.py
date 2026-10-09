"""Tests for the /api/tts-proxy endpoint defined in app.main."""

from contextlib import contextmanager
from unittest.mock import patch

import httpx
import pytest


def make_response(content=b"", status_code=200, headers=None):
    """Build a real httpx.Response so raise_for_status() behaves normally."""
    request = httpx.Request("GET", "http://tts.local/synthesize_speech/")
    return httpx.Response(status_code, content=content, headers=headers or {}, request=request)


class MockAsyncClient:
    """Minimal stand-in for httpx.AsyncClient used by the proxy endpoint."""

    def __init__(self, response=None, exc=None, capture=None):
        self._response = response
        self._exc = exc
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get(self, url, params=None):
        if self._capture is not None:
            self._capture["url"] = url
            self._capture["params"] = params
        if self._exc is not None:
            raise self._exc
        return self._response


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
    assert capture["url"] == "http://tts.local/synthesize_speech/"
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

    assert capture["url"] == "http://tts.local/synthesize_speech/"


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


def test_json_error_response_becomes_500(client):
    response = make_response(content=b'{"error": "boom"}', headers={"content-type": "application/json"})
    with mock_async_client(response=response):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 500
    assert result.json() == {"error": "TTS server returned error"}


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


def test_unexpected_error_returns_500(client):
    with mock_async_client(exc=RuntimeError("kaboom")):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 500
    assert result.json() == {"error": "kaboom"}


@pytest.mark.parametrize("missing", ["server", "voice", "text"])
def test_required_params_are_enforced(client, missing):
    params = {key: value for key, value in BASE_PARAMS.items() if key != missing}
    result = client.get("/api/tts-proxy", params=params)
    assert result.status_code == 422

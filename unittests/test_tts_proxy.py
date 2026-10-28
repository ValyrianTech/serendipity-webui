"""Tests for the /api/tts-proxy endpoint defined in app.main."""

import logging
from contextlib import contextmanager
from typing import ClassVar
from unittest.mock import patch

import httpx
import pytest
from fastapi import Request

from app import main as app_main


def make_response(content=b"", status_code=200, headers=None):
    """Build a real httpx.Response so raise_for_status() behaves normally."""
    request = httpx.Request("GET", "http://tts.local/synthesize_speech/")
    return httpx.Response(status_code, content=content, headers=headers or {}, request=request)


@pytest.fixture(autouse=True)  # noqa: V103
def _configure_primary_server(monkeypatch):
    """Configure a single trusted 'primary' TTS server for the happy path."""
    monkeypatch.setenv("SERENDIPITY_TTS_SERVERS", '{"primary": "http://tts.local"}')


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

    async def get(self, url, params=None, headers=None):  # noqa: V107
        if self._capture is not None:
            self._capture["url"] = url
            self._capture["params"] = params
            self._capture["headers"] = headers
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


BASE_PARAMS = {"voice": "voice-1", "text": "hello world"}


def test_success_returns_audio(client):
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 200
    assert result.content == b"AUDIO"
    assert result.headers["content-type"] == "audio/wav"
    assert result.headers["cache-control"] == "no-cache"
    assert capture["url"] == "http://tts.local/synthesize_speech/"
    assert capture["headers"] is None
    assert capture["params"] == {
        "voice": "voice-1",
        "text": "hello world",
        "style": "default",
        "language": "English",
    }


def test_trailing_slash_in_server_is_stripped(client, monkeypatch):
    monkeypatch.setenv("SERENDIPITY_TTS_SERVERS", '{"primary": "http://tts.local/"}')
    capture = {}
    response = make_response(content=b"A", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        client.get("/api/tts-proxy", params=BASE_PARAMS)

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
    with caplog.at_level(logging.ERROR), mock_async_client(exc=RuntimeError("kaboom")):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 500
    assert result.json() == {"error": "Internal TTS proxy error"}
    assert any(r.message == "TTS proxy request failed" and r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.parametrize("missing", ["voice", "text"])
def test_required_params_are_enforced(client, missing):
    params = {key: value for key, value in BASE_PARAMS.items() if key != missing}
    result = client.get("/api/tts-proxy", params=params)
    assert result.status_code == 422


def test_unknown_selector_returns_400(client):
    result = client.get("/api/tts-proxy", params={**BASE_PARAMS, "server": "does-not-exist"})
    assert result.status_code == 400
    assert result.json() == {"error": "Unknown TTS server"}


def test_ssrf_url_as_selector_is_rejected_and_not_fetched(client):
    """A URL supplied as the selector is treated as an unknown key, never fetched."""
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get(
            "/api/tts-proxy",
            params={**BASE_PARAMS, "server": "http://169.254.169.254/latest/meta-data/"},
        )

    assert result.status_code == 400
    assert result.json() == {"error": "Unknown TTS server"}
    assert capture == {}


def test_selector_selects_configured_server(client, monkeypatch):
    monkeypatch.setenv(
        "SERENDIPITY_TTS_SERVERS",
        '{"primary": "http://tts.local", "secondary": "http://tts2.local/tts"}',
    )
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get("/api/tts-proxy", params={**BASE_PARAMS, "server": "secondary"})

    assert result.status_code == 200
    assert capture["url"] == "http://tts2.local/tts/synthesize_speech/"


def test_default_selector_is_primary(client, monkeypatch):
    monkeypatch.setenv("SERENDIPITY_TTS_SERVERS", '{"primary": "http://primary.local"}')
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 200
    assert capture["url"] == "http://primary.local/synthesize_speech/"


def test_no_servers_configured_returns_400(client, monkeypatch):
    monkeypatch.delenv("SERENDIPITY_TTS_SERVERS", raising=False)
    result = client.get("/api/tts-proxy", params=BASE_PARAMS)
    assert result.status_code == 400
    assert result.json() == {"error": "Unknown TTS server"}


def test_tts_server_returns_default_url(client):
    result = client.get("/api/tts-server")
    assert result.status_code == 200
    assert result.json() == {"url": "http://tts.local"}


def test_tts_server_returns_null_when_unconfigured(client, monkeypatch):
    monkeypatch.delenv("SERENDIPITY_TTS_SERVERS", raising=False)
    result = client.get("/api/tts-server")
    assert result.status_code == 200
    assert result.json() == {"url": None}


def test_tts_server_cross_origin_rejected(client):
    result = client.get("/api/tts-server", headers={"Origin": "http://evil.example.com"})
    assert result.status_code == 403
    assert result.json() == {"error": "Cross-origin TTS server request rejected"}


def test_tts_server_same_origin_allowed(client):
    result = client.get("/api/tts-server", headers={"Origin": "http://testserver"})
    assert result.status_code == 200
    assert result.json() == {"url": "http://tts.local"}


def test_tts_server_referer_ignored_when_origin_absent(client):
    result = client.get("/api/tts-server", headers={"Referer": "http://evil.example.com/page"})
    assert result.status_code == 200
    assert result.json() == {"url": "http://tts.local"}


def test_tts_server_sec_fetch_mode_without_origin_rejected(client):
    result = client.get(
        "/api/tts-server",
        headers={"Referer": "http://testserver/page", "Sec-Fetch-Mode": "no-cors"},
    )
    assert result.status_code == 403
    assert result.json() == {"error": "Cross-origin TTS server request rejected"}


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


def test_path_prefix_is_preserved(client, monkeypatch):
    monkeypatch.setenv("SERENDIPITY_TTS_SERVERS", '{"primary": "http://tts.local/tts"}')
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 200
    assert capture["url"] == "http://tts.local/tts/synthesize_speech/"


def test_declared_content_length_over_cap_rejected(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_TTS_RESPONSE_BYTES", 4)
    response = make_response(
        content=b"AUDIO",
        headers={"content-type": "audio/wav", "content-length": "9999999"},
    )
    with mock_async_client(response=response):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 502
    assert result.json() == {"error": "TTS response too large"}


def test_non_integer_content_length_is_ignored(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_TTS_RESPONSE_BYTES", 4)

    class StubResponse:
        headers: ClassVar[dict] = {"content-type": "audio/wav", "content-length": "unknown"}
        content = b"AUDI"
        status_code = 200

        def raise_for_status(self):
            pass

    with mock_async_client(response=StubResponse()):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 200
    assert result.content == b"AUDI"


def test_undeclared_oversized_body_rejected_by_backstop(client, monkeypatch):
    monkeypatch.setattr("app.main.MAX_TTS_RESPONSE_BYTES", 4)

    class StubResponse:
        headers: ClassVar[dict] = {"content-type": "audio/wav"}
        content = b"AUDIO"
        status_code = 200

        def raise_for_status(self):
            pass

    with mock_async_client(response=StubResponse()):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 502
    assert result.json() == {"error": "TTS response too large"}


def test_get_configured_tts_servers_parses_json(monkeypatch):
    monkeypatch.setenv("SERENDIPITY_TTS_SERVERS", '{"primary": "http://a", "b": "http://b"}')
    assert app_main.get_configured_tts_servers() == {"primary": "http://a", "b": "http://b"}


def test_get_configured_tts_servers_empty_when_unset(monkeypatch):
    monkeypatch.delenv("SERENDIPITY_TTS_SERVERS", raising=False)
    assert app_main.get_configured_tts_servers() == {}


def test_get_configured_tts_servers_empty_when_blank(monkeypatch):
    monkeypatch.setenv("SERENDIPITY_TTS_SERVERS", "   ")
    assert app_main.get_configured_tts_servers() == {}


def test_get_configured_tts_servers_invalid_json(monkeypatch, caplog):
    monkeypatch.setenv("SERENDIPITY_TTS_SERVERS", "not json")
    with caplog.at_level(logging.WARNING):
        assert app_main.get_configured_tts_servers() == {}
    assert any("not valid JSON" in r.message for r in caplog.records)


def test_get_configured_tts_servers_non_object(monkeypatch, caplog):
    monkeypatch.setenv("SERENDIPITY_TTS_SERVERS", '["a", "b"]')
    with caplog.at_level(logging.WARNING):
        assert app_main.get_configured_tts_servers() == {}
    assert any("must be a JSON object" in r.message for r in caplog.records)


def test_get_configured_tts_servers_filters_non_string_entries(monkeypatch):
    monkeypatch.setenv("SERENDIPITY_TTS_SERVERS", '{"ok": "http://a", "bad": 5, "": "http://c"}')
    assert app_main.get_configured_tts_servers() == {"ok": "http://a"}


def test_resolve_tts_server_known_and_unknown(monkeypatch):
    monkeypatch.setenv("SERENDIPITY_TTS_SERVERS", '{"primary": "http://a"}')
    assert app_main.resolve_tts_server("primary") == "http://a"
    assert app_main.resolve_tts_server("missing") is None


def test_same_origin_allowed(client):
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS, headers={"Origin": "http://testserver"})

    assert result.status_code == 200
    assert result.content == b"AUDIO"


def test_cross_origin_rejected(client):
    result = client.get("/api/tts-proxy", params=BASE_PARAMS, headers={"Origin": "http://evil.example.com"})
    assert result.status_code == 403
    assert result.json() == {"error": "Cross-origin TTS proxy request rejected"}


def test_cross_origin_referer_ignored_when_origin_absent(client):
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS, headers={"Referer": "http://evil.example.com/page"})

    assert result.status_code == 200


def test_same_origin_referer_ignored_when_origin_absent(client):
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS, headers={"Referer": "http://testserver/page"})

    assert result.status_code == 200


def test_absent_origin_and_referer_allowed(client):
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get("/api/tts-proxy", params=BASE_PARAMS)

    assert result.status_code == 200


def test_unparseable_origin_rejected(client):
    result = client.get("/api/tts-proxy", params=BASE_PARAMS, headers={"Origin": "not a url"})
    assert result.status_code == 403
    assert result.json() == {"error": "Cross-origin TTS proxy request rejected"}


def test_empty_origin_host_rejected(client):
    result = client.get("/api/tts-proxy", params=BASE_PARAMS, headers={"Origin": "http://"})
    assert result.status_code == 403
    assert result.json() == {"error": "Cross-origin TTS proxy request rejected"}


def test_origin_with_port_allowed_when_allowlisted(client, monkeypatch):
    monkeypatch.setenv("SERENDIPITY_WEBUI_ALLOWED_ORIGINS", "http://testserver:8080")
    capture = {}
    response = make_response(content=b"AUDIO", headers={"content-type": "audio/wav"})
    with mock_async_client(response=response, capture=capture):
        result = client.get(
            "http://testserver:8080/api/tts-proxy",
            params=BASE_PARAMS,
            headers={"Origin": "http://testserver:8080"},
        )

    assert result.status_code == 200
    assert capture["url"] == "http://tts.local/synthesize_speech/"


def _make_request(headers):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/tts-proxy",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
        "query_string": b"",
    }
    return Request(scope)


def test_origin_allowed_without_origin_or_referer():
    request = _make_request([("host", "testserver")])
    assert app_main._origin_allowed(request) is True


def test_origin_allowed_same_origin():
    request = _make_request([("host", "testserver"), ("origin", "http://testserver")])
    assert app_main._origin_allowed(request) is True


def test_origin_allowed_same_origin_case_insensitive():
    request = _make_request([("host", "testserver"), ("origin", "HTTP://TESTSERVER")])
    assert app_main._origin_allowed(request) is True


def test_origin_allowed_cross_origin():
    request = _make_request([("host", "testserver"), ("origin", "http://evil.example.com")])
    assert app_main._origin_allowed(request) is False


def test_origin_allowed_ignores_referer_same_origin():
    request = _make_request([("host", "testserver"), ("referer", "http://testserver/page")])
    assert app_main._origin_allowed(request) is True


def test_origin_allowed_ignores_referer_cross_origin():
    request = _make_request([("host", "testserver"), ("referer", "http://evil.example.com/page")])
    assert app_main._origin_allowed(request) is True


def test_origin_allowed_rejects_sec_fetch_mode_without_origin():
    request = _make_request([("referer", "http://testserver/page"), ("sec-fetch-mode", "no-cors")])
    assert app_main._origin_allowed(request) is False


def test_origin_allowed_rejects_sec_fetch_mode_without_origin_or_referer():
    request = _make_request([("sec-fetch-mode", "cors")])
    assert app_main._origin_allowed(request) is False


def test_origin_allowed_cross_origin_with_matching_host_rejected():
    request = _make_request([("host", "evil.example.com"), ("origin", "http://evil.example.com")])
    assert app_main._origin_allowed(request) is False


def test_origin_allowed_forged_origin_host_not_in_allowlist_rejected():
    request = _make_request([("host", "testserver"), ("origin", "http://forged.example.com")])
    assert app_main._origin_allowed(request) is False


def test_origin_allowed_matching_origin_accepted_with_spoofed_host():
    request = _make_request([("host", "evil.example.com"), ("origin", "http://testserver")])
    assert app_main._origin_allowed(request) is True


def test_origin_allowed_null_rejected():
    request = _make_request([("origin", "null")])
    assert app_main._origin_allowed(request) is False


def test_origin_allowed_null_allowed_when_allowlisted(monkeypatch):
    monkeypatch.setenv("SERENDIPITY_WEBUI_ALLOWED_ORIGINS", "http://testserver,null")
    request = _make_request([("origin", "null")])
    assert app_main._origin_allowed(request) is True


def test_origin_allowed_origin_with_path_normalised():
    request = _make_request([("origin", "http://testserver/some/path")])
    assert app_main._origin_allowed(request) is True


def test_origin_allowed_uppercase_scheme_host_accepted():
    request = _make_request([("origin", "HTTP://TESTSERVER")])
    assert app_main._origin_allowed(request) is True


def test_origin_allowed_trailing_slash_accepted():
    request = _make_request([("origin", "http://testserver/")])
    assert app_main._origin_allowed(request) is True


def test_origin_allowed_unparseable_origin():
    request = _make_request([("host", "testserver"), ("origin", "not a url")])
    assert app_main._origin_allowed(request) is False


def test_origin_allowed_empty_netloc_rejected():
    request = _make_request([("origin", "http://")])
    assert app_main._origin_allowed(request) is False


def test_origin_allowed_no_scheme_rejected():
    request = _make_request([("origin", "testserver")])
    assert app_main._origin_allowed(request) is False


def test_origin_allowed_with_port_allowed_when_allowlisted(monkeypatch):
    monkeypatch.setenv("SERENDIPITY_WEBUI_ALLOWED_ORIGINS", "http://testserver:8080")
    request = _make_request([("host", "testserver:8080"), ("origin", "http://testserver:8080")])
    assert app_main._origin_allowed(request) is True


def test_get_allowed_origins_empty_when_unset(monkeypatch):
    monkeypatch.delenv("SERENDIPITY_WEBUI_ALLOWED_ORIGINS", raising=False)
    assert app_main.get_allowed_origins() == frozenset()


def test_get_allowed_origins_empty_when_blank(monkeypatch):
    monkeypatch.setenv("SERENDIPITY_WEBUI_ALLOWED_ORIGINS", "   ")
    assert app_main.get_allowed_origins() == frozenset()


def test_get_allowed_origins_parses_list(monkeypatch):
    monkeypatch.setenv(
        "SERENDIPITY_WEBUI_ALLOWED_ORIGINS",
        "  http://testserver/ , HTTPS://ui.example.com ,, , http://a.b:9000",
    )
    assert app_main.get_allowed_origins() == frozenset(
        {"http://testserver", "https://ui.example.com", "http://a.b:9000"}
    )


def test_get_allowed_origins_ignores_empty_entries(monkeypatch):
    monkeypatch.setenv("SERENDIPITY_WEBUI_ALLOWED_ORIGINS", ",http://testserver,,https://x.example,")
    assert app_main.get_allowed_origins() == frozenset({"http://testserver", "https://x.example"})

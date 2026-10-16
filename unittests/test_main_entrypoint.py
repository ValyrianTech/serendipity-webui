"""Tests for the __main__ entrypoint of app.main."""

import runpy
from pathlib import Path
from unittest.mock import patch

MAIN_PATH = Path(__file__).resolve().parent.parent / "app" / "main.py"


def test_main_block_starts_uvicorn(monkeypatch):
    monkeypatch.delenv("SERENDIPITY_WEBUI_HOST", raising=False)
    monkeypatch.delenv("SERENDIPITY_WEBUI_PORT", raising=False)
    with patch("uvicorn.run") as mock_run:
        runpy.run_path(str(MAIN_PATH), run_name="__main__")

    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert len(args) == 1
    assert kwargs == {"host": "127.0.0.1", "port": 8080}


def test_main_block_uses_env_host_and_port(monkeypatch):
    monkeypatch.setenv("SERENDIPITY_WEBUI_HOST", "0.0.0.0")
    monkeypatch.setenv("SERENDIPITY_WEBUI_PORT", "9000")
    with patch("uvicorn.run") as mock_run:
        runpy.run_path(str(MAIN_PATH), run_name="__main__")

    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert len(args) == 1
    assert kwargs == {"host": "0.0.0.0", "port": 9000}

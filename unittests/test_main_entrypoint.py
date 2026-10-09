"""Tests for the __main__ entrypoint of app.main."""

import runpy
from pathlib import Path
from unittest.mock import patch

MAIN_PATH = Path(__file__).resolve().parent.parent / "app" / "main.py"


def test_main_block_starts_uvicorn():
    with patch("uvicorn.run") as mock_run:
        runpy.run_path(str(MAIN_PATH), run_name="__main__")

    mock_run.assert_called_once()
    args, kwargs = mock_run.call_args
    assert len(args) == 1
    assert kwargs == {"host": "0.0.0.0", "port": 8080}

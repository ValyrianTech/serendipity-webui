"""Tests for the container entrypoint script (entrypoint.sh).

These tests exercise the real entrypoint.sh by running it with a fake
``uvicorn`` on ``PATH`` that records the arguments and environment it was
invoked with, so we can verify the container bind defaults and the exported
``SERENDIPITY_WEBUI_HOST``/``SERENDIPITY_WEBUI_PORT`` variables without
actually starting a server.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "entrypoint.sh"

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="entrypoint.sh is a POSIX shell script"
)

_FAKE_UVICORN = """#!/bin/bash
# Record the arguments on one line, then the serialized environment.
printf '%s\\n' "$*" >> "$FAKE_UVICORN_OUTPUT"
env | sort >> "$FAKE_UVICORN_OUTPUT"
exit 0
"""


def _make_fake_uvicorn(tmp_path: Path) -> Path:
    """Create an executable fake ``uvicorn`` that records its invocation."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "uvicorn"
    fake.write_text(_FAKE_UVICORN)
    fake.chmod(stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH)
    return bin_dir


def _run_entrypoint(monkeypatch, tmp_path: Path, bin_dir: Path) -> tuple[int, list[str]]:
    """Run entrypoint.sh with the fake uvicorn and return (returncode, lines).

    The first recorded line is the uvicorn argument string; the remaining lines
    are the ``env | sort`` dump captured at ``exec`` time. A missing output file
    raises a clear ``AssertionError`` (rather than a confusing
    ``FileNotFoundError``) so a failure to ``exec`` the fake uvicorn is obvious.
    """
    output = tmp_path / "recorded.txt"
    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{original_path}")
    monkeypatch.setenv("FAKE_UVICORN_OUTPUT", str(output))
    result = subprocess.run(["bash", str(ENTRYPOINT)], check=False)
    assert output.exists(), "fake uvicorn was never exec'd by entrypoint.sh"
    return result.returncode, output.read_text().splitlines()


def test_entrypoint_defaults_to_wildcard_bind(monkeypatch, tmp_path):
    monkeypatch.delenv("SERENDIPITY_WEBUI_HOST", raising=False)
    monkeypatch.delenv("SERENDIPITY_WEBUI_PORT", raising=False)
    bin_dir = _make_fake_uvicorn(tmp_path)

    returncode, lines = _run_entrypoint(monkeypatch, tmp_path, bin_dir)

    assert returncode == 0
    assert lines[0] == "app.main:app --host 0.0.0.0 --port 8080"
    env_lines = lines[1:]
    assert "SERENDIPITY_WEBUI_HOST=0.0.0.0" in env_lines
    assert "SERENDIPITY_WEBUI_PORT=8080" in env_lines


def test_entrypoint_honours_operator_override(monkeypatch, tmp_path):
    monkeypatch.setenv("SERENDIPITY_WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("SERENDIPITY_WEBUI_PORT", "9000")
    bin_dir = _make_fake_uvicorn(tmp_path)

    returncode, lines = _run_entrypoint(monkeypatch, tmp_path, bin_dir)

    assert returncode == 0
    assert lines[0] == "app.main:app --host 127.0.0.1 --port 9000"
    env_lines = lines[1:]
    assert "SERENDIPITY_WEBUI_HOST=127.0.0.1" in env_lines
    assert "SERENDIPITY_WEBUI_PORT=9000" in env_lines

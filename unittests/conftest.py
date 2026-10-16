"""Shared pytest fixtures for the Serendipity Web UI test suite."""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep tests independent of any local .env / environment configuration."""
    monkeypatch.delenv("SERENDIPITY_TTS_ALLOWED_HOSTS", raising=False)


@pytest.fixture
def client():
    """A FastAPI TestClient bound to the application."""
    with TestClient(app) as test_client:
        yield test_client

"""Tests that the dependency manifests stay in sync with each other."""

import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
REQUIREMENTS_PATH = REPO_ROOT / "requirements.txt"


@pytest.fixture(scope="module")
def pyproject_dependencies():
    """Return the runtime dependencies declared in pyproject.toml."""
    with PYPROJECT_PATH.open("rb") as handle:
        data = tomllib.load(handle)
    return data["project"]["dependencies"]


@pytest.fixture(scope="module")
def requirements_lines():
    """Return the non-empty, non-comment lines of requirements.txt."""
    with REQUIREMENTS_PATH.open(encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")]


def test_python_dotenv_is_declared_in_pyproject(pyproject_dependencies):
    assert "python-dotenv==1.2.4" in pyproject_dependencies


def test_every_pyproject_pin_appears_in_requirements(pyproject_dependencies, requirements_lines):
    assert set(pyproject_dependencies) <= set(requirements_lines)


def test_every_requirements_pin_appears_in_pyproject(pyproject_dependencies, requirements_lines):
    assert set(requirements_lines) <= set(pyproject_dependencies)


def test_app_main_imports():
    import app.main  # noqa: F401

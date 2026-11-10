"""Vulture whitelist for unittests/conftest.py and unittests/test_build_docker.py.

These names are reported as unused by vulture but are actually live:
``_isolate_env`` and ``_default_allowed_origins`` are autouse pytest fixtures
that pytest invokes via framework magic, so they have no direct caller in the
source. The bare-name expression statements below mark the names as used so
vulture does not report them. ``pytestmark`` is a live pytest module-level
skip marker that pytest reads implicitly at collection time, so it has no
direct caller. ``from_docker`` is a required lambda-parameter name that
mirrors ``build_docker.build``'s signature (``def build(docker_repo, tag,
from_docker=None)``), so it is a live name even though the lambda body does
not use it.
"""

pytestmark  # type: ignore[name-defined]
_isolate_env  # type: ignore[name-defined]
_default_allowed_origins  # type: ignore[name-defined]
from_docker  # type: ignore[name-defined]

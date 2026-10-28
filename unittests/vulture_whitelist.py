"""Vulture whitelist for unittests/conftest.py.

These names are reported as unused by vulture but are actually live:
``_isolate_env`` and ``_default_allowed_origins`` are autouse pytest fixtures
that pytest invokes via framework magic, so they have no direct caller in the
source. The bare-name expression statements below mark the names as used so
vulture does not report them.
"""

_isolate_env  # type: ignore[name-defined]
_default_allowed_origins  # type: ignore[name-defined]

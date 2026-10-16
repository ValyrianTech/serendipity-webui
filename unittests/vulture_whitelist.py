"""Vulture whitelist for unittests/conftest.py.

These names are reported as unused by vulture but are actually live:
``_isolate_env`` is an autouse pytest fixture that pytest invokes via
framework magic, so it has no direct caller in the source. The bare-name
expression statement below marks the name as used so vulture does not
report it.
"""

_isolate_env  # type: ignore[name-defined]

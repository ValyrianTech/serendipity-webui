"""Vulture whitelist for app/main.py.

These names are reported as unused by vulture but are actually live:
they are FastAPI route handlers registered via ``@app.get(...)`` decorators
and exercised by the test suite in ``unittests/``. Each bare-name expression
statement below marks the name as used so vulture does not report it.
"""

home  # type: ignore[name-defined]
agent_home  # type: ignore[name-defined]
conversations  # type: ignore[name-defined]
conversation  # type: ignore[name-defined]
settings  # type: ignore[name-defined]
edit_agent  # type: ignore[name-defined]
mcps  # type: ignore[name-defined]
toolsets  # type: ignore[name-defined]
skills  # type: ignore[name-defined]
llms  # type: ignore[name-defined]
scheduled_tasks  # type: ignore[name-defined]
workflows  # type: ignore[name-defined]
tts_proxy  # type: ignore[name-defined]
tts_server  # type: ignore[name-defined]

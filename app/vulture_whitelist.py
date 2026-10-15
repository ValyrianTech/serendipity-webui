"""Vulture whitelist for app/main.py.

These names are reported as unused by vulture but are actually live:
they are FastAPI route handlers registered via ``@app.get(...)`` decorators
and exercised by the test suite in ``unittests/``. Referencing each name as
a string literal marks it as used so vulture does not report it.
"""

_whitelist: list[str] = [
    "home",
    "agent_home",
    "conversations",
    "conversation",
    "settings",
    "edit_agent",
    "mcps",
    "toolsets",
    "skills",
    "llms",
    "scheduled_tasks",
    "workflows",
    "tts_proxy",
]

"""Regression tests for the ``window.removeOpenFile`` signature fix.

``window.removeOpenFile`` in ``app/templates/conversation.html`` used to call
the signed API helper ``removeStagedFile(wif, data)`` with a single argument
(``removeStagedFile(stagedFiles[idx].id)``). The UI index/``id`` was passed as
the ``wif`` parameter and the ``data`` payload was omitted, so ``signData``
threw inside ``wifToPrivateKey``. Because ``removeOpenFile`` was neither
``async`` nor awaited, the rejection was swallowed as an unhandled promise
rejection: the file disappeared from the local ``stagedFiles`` array (the UI
lied) but was never un-staged on the backend, so it was still attached to the
next message.

The fix routes ``removeOpenFile`` through the already-correct
``window.removeStagedFileAt(index)`` helper, which guards on
``window.currentWif``, builds the ``{ agent, conversationId, fileName }``
payload, and awaits the call inside a ``try/catch`` that surfaces a toast.

These are static-content checks (they prove the fix is present in the served
template). Coverage is measured only over Python under ``app/``, so no new
Python paths are added."""

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"


def _read_template(name):
    """Return the raw text of a template file under ``app/templates``."""
    return (TEMPLATES_DIR / name).read_text(encoding="utf-8")


def _extract_assigned_function(source, name):
    """Extract ``window.<name> = [async] function(...) {...}`` verbatim.

    The handlers are anonymous function expressions assigned onto ``window``,
    so ``function <name>(`` never appears in the source; this brace-matches
    from the ``function`` keyword instead.
    """
    marker = re.search(r"window\." + re.escape(name) + r"\s*=\s*(?:async\s+)?function", source)
    assert marker, f"window.{name} assignment not found in template source"
    brace = source.index("{", marker.end())
    start = marker.start()
    depth = 0
    i = brace
    while i < len(source):
        char = source[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
        i += 1
    raise AssertionError(f"could not find closing brace for window.{name}")


def _remove_open_file_body():
    """Return the body of the ``window.removeOpenFile`` assignment."""
    return _extract_assigned_function(_read_template("conversation.html"), "removeOpenFile")


def test_remove_open_file_is_async():
    """``removeOpenFile`` is async so the removal promise is awaited, not dropped."""
    content = _read_template("conversation.html")
    assert "window.removeOpenFile = async function(idx)" in content
    assert re.search(r"async function\(\w+\)", _remove_open_file_body())


def test_remove_open_file_delegates_to_remove_staged_file_at():
    """``removeOpenFile`` awaits the shared, correctly-signed removal helper."""
    body = _remove_open_file_body()
    assert "await window.removeStagedFileAt(idx)" in body
    # The panel is refreshed once the helper has resolved.
    assert "updateOpenFilesList()" in body


def test_remove_open_file_never_calls_api_helper_directly():
    """The buggy single-argument ``removeStagedFile(...)`` call is gone.

    ``removeStagedFile`` has signature ``(wif, data)``; calling it with a UI id
    passed a bogus ``wif`` and omitted ``data``, so the rejection left the file
    staged on the backend.
    """
    body = _remove_open_file_body()
    assert "removeStagedFile(" not in body.replace("removeStagedFileAt(", "")
    # And the old ``.id`` misuse must not reappear anywhere in the body.
    assert "stagedFiles[idx].id" not in body


def test_remove_open_file_guards_missing_index():
    """``removeOpenFile`` is a no-op for an index that is no longer staged."""
    assert "if (!stagedFiles[idx]) return;" in _remove_open_file_body()


def test_only_one_signed_remove_staged_file_call_site():
    """Exactly one ``removeStagedFile(...)`` call remains, and it passes a payload.

    Both UI entry points (the staged-files pill and the Open Files panel) now
    funnel through ``removeStagedFileAt``, so there is a single code path that
    signs and sends the removal request.
    """
    content = _read_template("conversation.html")
    calls = [
        content[m.start() : m.start() + 200]
        for m in re.finditer(r"(?<!At)removeStagedFile\(", content)
        # Skip the import statement, which lists the name without calling it.
        if "from '/static/js/api.js'" not in content[max(0, m.start() - 600) : m.start()]
    ]
    assert len(calls) == 1, calls
    assert "removeStagedFile(window.currentWif, {" in calls[0]


def test_update_open_files_list_is_wired_up():
    """``updateOpenFilesList`` is no longer dead code; every mutation refreshes it.

    The panel is populated when a conversation loads, when a file is staged,
    and after any removal.
    """
    content = _read_template("conversation.html")
    assert len(re.findall(r"function updateOpenFilesList\(", content)) == 1
    invocations = len(re.findall(r"(?<!function )updateOpenFilesList\(\)", content))
    # loadConversation, handleFileSelect, removeStagedFileAt, removeOpenFile.
    assert invocations >= 4, invocations

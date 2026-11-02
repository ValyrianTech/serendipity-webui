"""Regression tests ensuring the Settings page never echoes WIF key material.

The Settings page's ``updateWifDisplay()`` function previously wrote the first
and last 10 characters of the unlocked WIF private key into the
``#wif-key-display`` textarea. A WIF is ~52 characters, so 20 characters of key
material leaked into the DOM. These tests pin the redaction so a future edit
cannot reintroduce that leak.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SETTINGS_HTML_PATH = REPO_ROOT / "app" / "templates" / "settings.html"


def _extract_js_function_body(content, signature):
    start = content.index(signature)
    brace = content.index("{", start)
    depth = 0
    for i in range(brace, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                return content[start:i + 1]
    raise AssertionError(f"Unbalanced braces in {signature}")


def test_template_never_slices_current_wif_into_dom():
    content = SETTINGS_HTML_PATH.read_text(encoding="utf-8")
    assert "window.currentWif.substring(" not in content
    assert "currentWif.substring" not in content
    assert "currentWif.slice" not in content
    assert "(key loaded — hidden)" in content


def test_update_wif_display_has_no_current_wif_slicing():
    content = SETTINGS_HTML_PATH.read_text(encoding="utf-8")
    fn_body = _extract_js_function_body(content, "function updateWifDisplay()")
    assert "'...'" not in fn_body
    assert "currentWif.substring" not in fn_body
    for unrelated in ("saveServerSettings", "saveNewWifKey", "withWifBusy"):
        assert unrelated not in fn_body
    assert len(fn_body) < 1500


def test_rendered_settings_page_does_not_echo_key_material(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "window.currentWif.substring(" not in resp.text
    assert 'id="wif-key-display"' in resp.text
    assert "(key loaded — hidden)" in resp.text


def test_rendered_settings_page_has_no_wif_looking_string(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    known_wifs = [
        "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ",
        "L1aW4aubDFB7yfras2S1mN3bqg9nwySY8nkoLmJebSLD5BWv3ENZ",
    ]
    assert not any(w in resp.text for w in known_wifs)

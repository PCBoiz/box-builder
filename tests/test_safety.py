"""Guard rails from the project's hard rules, enforced by the test run so they cannot erode quietly:
the server never talks to Scenario Manager, holds no SM credentials, and only calls the LLM host it is configured for."""
import glob
import os
import re

APP = os.path.join(os.path.dirname(__file__), "..", "app")


def _sources():
    for path in glob.glob(os.path.join(APP, "*.py")):
        with open(path, encoding="utf-8") as f:
            yield os.path.basename(path), f.read()


# images.py names the SM host only as a string: the allowed image host and the base of "open in SM" links.
# It makes no requests (the next test covers that), so the browser, not the server, is what loads those URLs.
HOST_MENTION_OK = {"images.py"}


def test_server_code_never_references_scenario_manager():
    for name, text in _sources():
        if name not in HOST_MENTION_OK:
            assert not re.search(r"sm\.config\.inc|scenario[\s_-]*manager\.", text, re.I), f"{name} mentions an SM host"
        assert not re.search(r"SM_(PASSWORD|TOKEN|USER|COOKIE)", text), f"{name} reads an SM credential"


def test_only_the_picker_makes_outgoing_http_calls():
    for name, text in _sources():
        if name == "picker.py":
            continue
        assert not re.search(r"urlopen|urllib\.request|import requests|import httpx|http\.client|aiohttp", text), \
            f"{name} makes outgoing HTTP calls; only picker.py (the LLM) may"


def test_picker_only_calls_the_configured_llm_base():
    text = dict(_sources())["picker.py"]
    assert len(re.findall(r"urllib\.request\.Request\(", text)) == 1
    assert "self.s.deepseek_base" in text


# ---- the Tampermonkey tool (integrations/box_builder_sm.user.js): the interlock that keeps it read-only
SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "..", "integrations", "box_builder_sm.user.js")


def _script():
    with open(SCRIPT_PATH, encoding="utf-8") as f:
        return f.read()


def _header(text):
    return re.search(r"^// ==UserScript==\n(.*?)^// ==/UserScript==", text, flags=re.S | re.M).group(1)


def _first_code(text, brace):
    """What the function opened by the `{` at `brace` does first: comments and blank space skipped."""
    return re.sub(r"^(\s|//[^\n]*\n|/\*.*?\*/)*", "", text[brace + 1:], flags=re.S)


def _first_code_of_function(text, name):
    m = re.search(rf"\bfunction\s+{name}\s*\([^)]*\)\s*\{{", text)
    return _first_code(text, m.end() - 1)


def _first_code_around(text, at):
    """The same for the function that holds position `at`: the last declaration before it."""
    last = list(re.finditer(r"\bfunction\s+\w+\s*\([^)]*\)\s*\{", text[:at]))[-1]
    return _first_code(text, last.end() - 1)


def test_the_tool_is_read_only_with_one_switch_set_to_false():
    text = _script()
    assert text.count("const WRITES_ENABLED = false;") == 1
    assert len(re.findall(r"\bconst WRITES_ENABLED\b", text)) == 1              # one declaration, and it is the false one
    assert not re.search(r"\bWRITES_ENABLED\s*=[^=]", text.replace("const WRITES_ENABLED = false;", ""))   # nothing assigns it later
    assert "Ghi vào SM:" in text


def test_the_only_create_request_sits_in_a_function_that_starts_with_the_guard():
    text = _script()
    posts = [m.start() for m in re.finditer(r'method: "POST"', text)]
    assert len(posts) == 1, "exactly one literal create request in the file"
    assert _first_code_around(text, posts[0]).startswith("if (!WRITES_ENABLED)")
    for banned in ('"PUT"', '"PATCH"', '"DELETE"', "/transition", "handover"):
        assert banned not in text


def test_the_create_flow_and_the_preview_are_guarded_too():
    text = _script()
    assert _first_code_of_function(text, "createBoxes").startswith("if (!WRITES_ENABLED)")
    assert _first_code_of_function(text, "smCreate").startswith("if (!WRITES_ENABLED)")
    view = text[text.index("async function view("):text.index("async function createBoxes(")]
    assert re.search(r"if \(!WRITES_ENABLED\) \{.*?return \{ ready: now\.boxes, create: \[\], skip \};\s*\}", view, flags=re.S)   # no plan, so no confirm button
    assert "Bản này chỉ đọc: xem được hộp sẽ tạo, chưa tạo được hộp trong SM." in text


def test_the_tool_never_updates_itself_and_keeps_its_identity():
    head = _header(_script())
    assert "@updateURL" not in head
    assert re.search(r"^// @name\s+Box builder: đồng bộ kho với SM$", head, flags=re.M)     # same @name and @namespace as 0.3.0, or Tampermonkey
    assert re.search(r"^// @namespace\s+box-builder$", head, flags=re.M)                    # installs a second script instead of an update
    version = tuple(int(x) for x in re.search(r"^// @version\s+(\d+\.\d+\.\d+)$", head, flags=re.M).group(1).split("."))
    assert version > (0, 3, 0)
    assert f'const VERSION = "{".".join(map(str, version))}";' in _script()

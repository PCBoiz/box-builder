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


def _allowlist(text):
    block = re.search(r"const SM_ALLOW = \[\n(.*?)\n  \];", text, flags=re.S).group(1)
    entries = re.findall(r'\["([A-Z]+)", /(\^.*?\$)/\]', block)
    assert len(entries) == len([l for l in block.splitlines() if l.strip()]), "every line of the list must be a [method, pattern] pair"
    return [(method, pattern.replace("\\/", "/")) for method, pattern in entries]


def test_every_request_to_sm_goes_through_one_wrapper_with_an_allowlist():
    text = _script()
    allow = _allowlist(text)
    assert allow == [("GET", r"^/api/boxes$"), ("GET", r"^/api/objects$"), ("POST", r"^/api/boxes$")]
    assert [m for m, _ in allow if m != "GET"] == ["POST"], "the only write on the list is the box create, which the wrapper also refuses while the switch is false"

    def allowed(method, path):
        return any(m == method and re.fullmatch(p, path) for m, p in allow)
    for method, path in (("GET", "/api/boxes"), ("GET", "/api/objects"), ("POST", "/api/boxes")):
        assert allowed(method, path), (method, path)
    for method, path in (("PUT", "/api/boxes"), ("PATCH", "/api/boxes"), ("DELETE", "/api/boxes/MBX-0001"), ("GET", "/api/boxes/MBX-0001"), ("POST", "/api/boxes/MBX-0001"),
                         ("POST", "/api/boxes/MBX-0001/transition"), ("GET", "/api/boxes/MBX-0001/transition"), ("GET", "/api/boxes/"), ("GET", "/api/boxes/a/b"),
                         ("GET", "/api/users"), ("GET", "/api/objects/1"), ("POST", "/api/objects"), ("GET", "/api/boxes\n"), ("DELETE", "/api/objects")):
        assert not allowed(method, path), (method, path)

    assert len(re.findall(r"\bfetch\(", text)) == 1, "one fetch call in the whole file, inside the wrapper"
    wrapper = text[text.index("async function smRequest("):text.index("const loginPage")]
    assert "fetch(" in wrapper
    assert "new URL(path, location.origin)" in wrapper and "u.origin === location.origin" in wrapper and "!u.username && !u.password" in wrapper
    assert "SM_ALLOW.some(([m, re]) => m === method && re.test(u.pathname))" in wrapper
    assert '(method === "GET" || WRITES_ENABLED)' in wrapper, "the wrapper refuses a write while the switch is false, even for a listed path"
    assert 'credentials: "same-origin"' in wrapper
    # nothing else in the file can reach the network or run text as code
    for banned in ("XMLHttpRequest", "sendBeacon", "WebSocket", "EventSource", "importScripts", "import(", "eval(", "new Function", "document.write", "innerHTML", "GM_openInTab", "unsafeWindow"):
        assert banned not in text, banned


def test_every_request_to_the_app_is_anonymous_and_refuses_redirects_and_goes_to_known_routes():
    text = _script()
    assert len(re.findall(r"GM_xmlhttpRequest\(", text)) == 1
    call = text[text.index("GM_xmlhttpRequest({"):]
    call = call[:call.index("headers:")]
    assert "anonymous: true" in call and 'redirect: "error"' in call
    routes = {(m, re.sub(r"\$\{[^}]*\}", "{}", p)) for m, _, p in re.findall(r'\bapp\("(GET|POST)", ([`"])(.*?)\2', text)}
    assert routes == {("GET", "/api/config"), ("GET", "/api/preflight"), ("GET", "/api/sync/check"), ("GET", "/api/taskboxes?pending=true"),
                      ("POST", "/api/sm/boxes/sync"), ("POST", "/api/catalog/sync"), ("POST", "/api/boxes/{}/pushed")}


def test_this_build_has_no_return_call_and_the_old_create_stays_as_it_was():
    text = _script()
    for banned in ("/transition", "returned_by", "returnable", "next_actions", "smReadBox"):
        assert banned not in text, banned
    assert 'ENDED = ["deactivated"]' in text, "SM has one ended status, and it is not guessed from other names"
    assert "note: `Created by Box builder #${b.id}`" in text, "the dead create keeps the 0.3.1 note"


def test_auto_refresh_only_reads_and_never_runs_over_another_run():
    text = _script()
    assert "const AUTO_BOXES_MS = 10 * 60 * 1000;" in text and "const AUTO_CATALOG_MS = 60 * 60 * 1000;" in text
    tick = text[text.index("async function autoTick()"):text.index("setInterval(autoTick, AUTO_TICK_MS)") if "setInterval(autoTick, AUTO_TICK_MS)" in text else None]
    assert tick.index("panelApi.busy()") < tick.index("panelApi.autoRun(kind)"), "the busy check comes before a run starts"
    assert "autoGoing" in tick and "!autoOn()" in tick and "panelApi.configured()" in tick
    run = text[text.index("const autoRun = (kind) =>"):text.index("auto.addEventListener(")]
    assert "await sync(s)" in run and "await syncSmBoxes(s)" in run
    for banned in ("createBoxes", "smCreate", "smRequest", "fetch(", "app(\"POST\""):
        assert banned not in run, f"auto refresh must go through the same reads as the buttons: {banned}"
    assert 'signedOut' in run and 'store.set("bb_auto", false)' in run, "it switches itself off when SM says nobody is signed in"
    assert len(re.findall(r"setInterval\(autoTick", text)) == 1, "one timer for the whole page"

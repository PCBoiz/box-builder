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


def _guard_body(code):
    """What the leading `if (!WRITES_ENABLED)` does: the braced block (braces counted) or the one statement up to its `;`.
    Only that, so a `throw` further down the function cannot stand in for the guard's own."""
    assert code.startswith("if (!WRITES_ENABLED)"), code[:60]
    rest = code[len("if (!WRITES_ENABLED)"):].lstrip()
    if not rest.startswith("{"):
        return rest[:rest.index(";") + 1]
    depth = 0
    for i, ch in enumerate(rest):
        depth += (ch == "{") - (ch == "}")
        if depth == 0:
            return rest[:i + 1]
    raise AssertionError("the guard's block never closes")


def test_the_guard_reader_stops_at_the_end_of_the_guard():
    assert "throw" not in _guard_body("if (!WRITES_ENABLED) { return null; }\n  throw new Error('later');")
    assert "throw" not in _guard_body("if (!WRITES_ENABLED) return null;\n  throw new Error('later');")
    assert "throw" in _guard_body("if (!WRITES_ENABLED) { const e = new Plain(X); throw e; }\n  more();")
    assert "throw" in _guard_body("if (!WRITES_ENABLED) throw new Plain(X);\n  more();")


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
    first = _first_code_around(text, posts[0])
    assert first.startswith("if (!WRITES_ENABLED)")
    assert re.search(r"\bthrow\b", _guard_body(first)), "the guard in front of the create request must throw"
    for banned in ('"PUT"', '"PATCH"', '"DELETE"', "/transition", "handover"):
        assert banned not in text


def test_the_create_flow_and_the_preview_are_guarded_too():
    text = _script()
    for name in ("createBoxes", "smCreate"):
        first = _first_code_of_function(text, name)
        assert first.startswith("if (!WRITES_ENABLED)"), name
        assert re.search(r"\bthrow\b", _guard_body(first)), f"the guard of {name} must stop the function with a throw, not just start with the condition"
    view = text[text.index("async function view("):text.index("async function createBoxes(")]
    assert re.search(r"if \(!WRITES_ENABLED\) \{.*?return \{ ready: now\.boxes, create: \[\], skip \};\s*\}", view, flags=re.S)   # no plan, so no confirm button
    assert "Bản này chỉ đọc: xem được hộp sẽ tạo, chưa tạo được hộp trong SM." in text


def test_the_tool_never_updates_itself_and_keeps_its_identity():
    head = _header(_script())
    assert "@updateURL" not in head
    assert re.search(r"^// @name\s+Box builder: đồng bộ kho với SM$", head, flags=re.M)     # same @name and @namespace as 0.3.0, or Tampermonkey
    assert re.search(r"^// @namespace\s+box-builder$", head, flags=re.M)                    # installs a second script instead of an update
    version = tuple(int(x) for x in re.search(r"^// @version\s+(\d+\.\d+\.\d+)$", head, flags=re.M).group(1).split("."))
    assert version >= (0, 4, 1)
    assert f'const VERSION = "{".".join(map(str, version))}";' in _script()


def test_the_header_asks_for_the_least_it_needs():
    """The header is what Tampermonkey shows the person and what decides the script's powers: top page only, no loaded code,
    no wildcard host, and a short list of grants."""
    head = _header(_script())
    keys = re.findall(r"^// @(\w[\w-]*)", head, flags=re.M)
    assert keys.count("noframes") == 1, "@noframes: a frame inside SM must get no panel and no timer"
    assert "require" not in keys and "resource" not in keys, "no code or file is loaded from anywhere else"
    connects = re.findall(r"^// @connect\s+(\S+)$", head, flags=re.M)
    assert all(re.fullmatch(r"[A-Za-z0-9.-]+", c) and c != "all" for c in connects), f"every @connect names one host, no wildcard: {connects}"
    assert "localhost" in connects and "127.0.0.1" in connects, "the served copy's header is built around the localhost line"
    grants = re.findall(r"^// @grant\s+(\S+)$", head, flags=re.M)
    assert len(grants) == len(set(grants)) and len(grants) == keys.count("grant"), "one grant per line, each named once"
    # the four powers the script uses, and GM_info, which only reads the header text for the panel's update line (0.4.0)
    assert set(grants) == {"GM_xmlhttpRequest", "GM_getValue", "GM_setValue", "GM_addStyle", "GM_info"}, grants


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
    assert 'const extra = method === "GET" ? {} : { headers: opts.headers, body: opts.body };' in wrapper and "...extra" in wrapper, "a GET never forwards a body or headers"
    assert wrapper.count("opts.headers") == 1 and wrapper.count("opts.body") == 1, "the caller's headers and body are read in that one place only"
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
    assert "await sync(s, { pace: true })" in run and "await syncSmBoxes(s)" in run, "the automatic catalog read is the paced one"
    assert "const AUTO_PAGE_PAUSE_MS = 250;" in text and "if (page <= pages && opts && opts.pace) await sleep(AUTO_PAGE_PAUSE_MS);" in text
    assert "nosave: true" in run and "if (!(opts && opts.nosave)) save();" in text, "an automatic run must not save the panel's boxes over the storage"
    for banned in ("createBoxes", "smCreate", "smRequest", "fetch(", "app(\"POST\""):
        assert banned not in run, f"auto refresh must go through the same reads as the buttons: {banned}"
    assert 'signedOut' in run and 'store.set("bb_auto", false)' in run, "it switches itself off when SM says nobody is signed in"
    sm_json = text[text.index("async function smJson("):text.index("const listOf")]
    assert "if (r.status === 401 || r.status === 403 || loginPage(r)) throw signedOut();" in sm_json, "only a 401, a 403 or a login redirect is 'signed out'"
    assert sm_json.index("if (!r.ok)") < sm_json.index("!/json/.test(r.type)"), "an error status is read before the page type, so a 502 page is not a sign-out"
    assert sm_json.count("signedOut()") == 1, "no other way in smJson ends as 'signed out'"
    assert len(re.findall(r"setInterval\(autoTick", text)) == 1, "one timer for the whole page"

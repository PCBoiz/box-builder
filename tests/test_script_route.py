"""The sync tool is served by the app so Tampermonkey installs it with one click, with the app's own address already in it."""
import re

from fastapi.testclient import TestClient

from app import picker
from app.main import create_app
from app.settings import Settings
from tests.test_automation import make_client
from tests.test_backend import env  # noqa: F401  (env is a fixture)


def update_lines(text):
    """The header lines that decide whether an installed copy polls this app for a newer version."""
    return [ln for ln in text.splitlines() if re.match(r"//\s*@(updateURL|downloadURL)\b", ln)]


def test_served_script_has_the_address_the_connect_line_and_no_update_link(env):
    client, s, _ = make_client(env)
    r = client.get("/box_builder_sm.user.js", headers={"host": "box-builder-guwc.onrender.com", "x-forwarded-proto": "https"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert r.headers["cache-control"] == "no-cache"
    t = r.text
    assert t.startswith("// ==UserScript==")                                           # Tampermonkey recognises it
    assert 'const DEFAULT_APP = "https://box-builder-guwc.onrender.com";' in t           # the address is filled in
    assert "// @connect      box-builder-guwc.onrender.com" in t
    assert not any(ln.startswith("// @updateURL") for ln in t.splitlines())             # an installed copy must not poll this app for a new version (header lines only: the tool may name the word in a comment)
    assert t.count("// @downloadURL none\n") == 1 and update_lines(t) == ["// @downloadURL none"]   # Tampermonkey's documented "no update check"
    assert "box-builder-guwc.onrender.com/box_builder_sm" not in t
    assert t.count("// @connect") >= 3 and "const DEFAULT_APP = \"\";" not in t
    assert t.count("// @connect      box-builder-guwc.onrender.com") == 1                    # listed once, even though the file already names it


def test_the_app_parameter_picks_the_address_and_bad_values_are_refused(env):
    client, s, _ = make_client(env)
    s.allowed_app_hosts = "192.0.2.10"                                                  # the owner's list: a machine on the office network
    r = client.get("/box_builder_sm.user.js?app=https://box-builder-guwc.onrender.com", headers={"host": "box-builder-guwc.onrender.com"})   # this server's own address
    assert 'const DEFAULT_APP = "https://box-builder-guwc.onrender.com";' in r.text
    assert update_lines(r.text) == ["// @downloadURL none"] and "?app=" not in r.text          # the parameter never reaches an update link
    lan = client.get("/box_builder_sm.user.js?app=http://192.0.2.10:8080/")             # a trailing slash is tidied
    assert 'const DEFAULT_APP = "http://192.0.2.10:8080";' in lan.text and "// @connect      192.0.2.10" in lan.text
    for bad in ('https://x.com";alert(1);//', "javascript:alert(1)", "https://a.com/path", "https://a b.com", "ftp://a.com",
                "https://a.com\nGM_xmlhttpRequest", "https://" + "a" * 5 + ".com:99999999", "//evil.com", "https://user@evil.com"):
        got = client.get("/box_builder_sm.user.js", params={"app": bad})
        assert got.status_code == 422, bad


def test_the_script_holds_no_secret_and_is_the_same_file_as_in_the_repo(env):
    s, cat = env
    s.sync_token, s.deepseek_key, s.llm_mode = "SYNC-secret-TOKEN-9z8y", "sk-leaktest-0011223344556677", "mock"
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    t = client.get("/box_builder_sm.user.js").text
    for secret in (s.sync_token, s.deepseek_key, s.db_path):
        assert secret not in t
    source = open("integrations/box_builder_sm.user.js", encoding="utf-8").read()
    for name in ("GM_xmlhttpRequest", "/api/objects?page=", "/api/catalog/sync", "/api/sync/check"):
        assert name in t and name in source
    assert t.replace("// @connect      localhost\n", "") != source                       # only the header and the default address differ
    body = re.sub(r"^// ==UserScript==.*?// ==/UserScript==\n", "", t, flags=re.S).replace(re.search(r'const DEFAULT_APP = "[^"]*";', t).group(0), 'const DEFAULT_APP = "";')
    assert body == re.sub(r"^// ==UserScript==.*?// ==/UserScript==\n", "", source, flags=re.S)


def test_the_same_script_is_also_served_as_plain_text_for_copy_and_paste(env):
    """When Tampermonkey does not open its install page the person copies the code instead, so the text page must match the script exactly."""
    client, s, _ = make_client(env)
    hdr = {"host": "box-builder-guwc.onrender.com", "x-forwarded-proto": "https"}
    js, txt = client.get("/box_builder_sm.user.js", headers=hdr), client.get("/box_builder_sm.txt", headers=hdr)
    assert txt.status_code == 200 and txt.headers["content-type"].startswith("text/plain") and "charset=utf-8" in txt.headers["content-type"]
    assert txt.headers["cache-control"] == "no-cache" and txt.text == js.text
    assert update_lines(txt.text) == ["// @downloadURL none"]                                     # the paste copy carries no update link either (header lines only)
    assert txt.text.startswith("// ==UserScript==") and 'const DEFAULT_APP = "https://box-builder-guwc.onrender.com";' in txt.text
    assert client.get("/box_builder_sm.txt", params={"app": "https://a.com/path"}).status_code == 422          # same address check
    s.allowed_app_hosts = "nas.example.com"
    assert client.get("/box_builder_sm.txt", params={"app": "https://nas.example.com"}).text.count("// @connect      nas.example.com") == 1


def test_the_app_parameter_only_names_this_server_localhost_or_a_listed_host(env):
    """The address in ?app= is where an installed tool sends the sync code. A link that names a stranger's machine must not produce a tool."""
    client, s, _ = make_client(env)
    here = {"host": "app.example.test:8443", "x-forwarded-proto": "https"}
    for js in ("/box_builder_sm.user.js", "/box_builder_sm.txt"):
        for ok in ("https://app.example.test", "http://APP.example.test:9999", "https://app.example.test:8443/", "http://localhost", "http://localhost:8080",
                   "http://127.0.0.1:9000", "https://LocalHost"):
            r = client.get(js, params={"app": ok}, headers=here)
            host = re.match(r"https?://([^/:]+)", ok).group(1).lower()
            assert r.status_code == 200 and f"// @connect      {host}\n" in r.text, (js, ok, r.status_code)
        for bad in ("https://evil.example", "https://app.example.test.evil.example", "https://evil-app.example.test", "https://example.test",
                    "https://xapp.example.test", "https://localhost.evil.example", "http://127.0.0.2", "http://127.0.0.1.evil.example", "http://0.0.0.0",
                    "http://10.0.0.5:8080", "https://nas.example.test"):
            r = client.get(js, params={"app": bad}, headers=here)
            assert r.status_code == 422 and "ALLOWED_APP_HOSTS" in r.json()["detail"] and "UserScript" not in r.text, (js, bad, r.status_code)
    assert "evil" not in client.get("/box_builder_sm.user.js", params={"app": "https://evil.example"}, headers=here).text    # the refusal does not echo the address back
    # no parameter, or an empty one: the server's own address, whatever host name the request used
    for params in ({}, {"app": ""}, {"app": "  "}):
        r = client.get("/box_builder_sm.user.js", params=params, headers=here)
        assert r.status_code == 200 and 'const DEFAULT_APP = "https://app.example.test:8443";' in r.text
    # the owner's list: comma, space or semicolon between entries, a port or scheme tolerated, case ignored, empty entries mean nothing
    s.allowed_app_hosts = " nas.example.test, Other.EXAMPLE.test:8080;https://third.example.test/x  ,,"
    for ok in ("https://nas.example.test", "http://other.example.test:1234", "https://THIRD.example.test"):
        assert client.get("/box_builder_sm.user.js", params={"app": ok}, headers=here).status_code == 200, ok
    for bad in ("https://fourth.example.test", "https://nas.example.test.evil.example", "https://evil.example"):
        assert client.get("/box_builder_sm.user.js", params={"app": bad}, headers=here).status_code == 422, bad
    s.allowed_app_hosts = " , ;,"                                                                  # a list with nothing in it allows nothing extra
    assert client.get("/box_builder_sm.user.js", params={"app": "https://evil.example"}, headers=here).status_code == 422
    assert client.get("/box_builder_sm.user.js", params={"app": "https://app.example.test"}, headers=here).status_code == 200


def test_allowed_app_hosts_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("ALLOWED_APP_HOSTS", raising=False)
    assert Settings.from_env().allowed_app_hosts == ""
    monkeypatch.setenv("ALLOWED_APP_HOSTS", "nas.example.test,other.example.test")
    assert Settings.from_env().allowed_app_hosts == "nas.example.test,other.example.test"

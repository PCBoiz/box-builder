"""Findings from the read-only security review (board task #11), each pinned by a test."""
import os

from fastapi.testclient import TestClient

from app import picker
from app.main import create_app
from tests.test_automation import make_client
from tests.test_backend import env, wait_settled  # noqa: F401  (env is a fixture)

# What a malicious web page can do from a visitor's browser: a plain form POST (no preflight, no custom header).
CROSS_SITE = {"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example"}


def draft(client, label="Box A1"):
    bid = client.post("/api/boxes", json={"module": "M8", "label": label, "theme": "t", "count": 2}).json()["id"]
    wait_settled(client, bid)
    return bid


def test_cross_site_writes_are_refused_even_without_an_access_code(env):
    client, s, cat = make_client(env)
    bid = draft(client)
    for method, path in (("post", f"/api/boxes/{bid}/approve"), ("post", f"/api/boxes/{bid}/regenerate"),
                         ("post", f"/api/boxes/{bid}/fill"), ("delete", f"/api/boxes/{bid}")):
        r = getattr(client, method)(path, headers=CROSS_SITE)
        assert r.status_code == 403, (method, path, r.status_code)
    assert client.post(f"/api/boxes/{bid}/approve", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403   # another port, same host
    assert client.post(f"/api/boxes/{bid}/approve", headers={"Origin": "null"}).status_code == 403                 # sandboxed frame or file://
    assert next(b for b in client.get("/api/state").json()["boxes"] if b["id"] == bid)["status"] == "draft"        # nothing happened


def test_same_origin_and_non_browser_writes_still_work(env):
    client, s, cat = make_client(env)
    bid = draft(client)
    assert client.post(f"/api/boxes/{bid}/lock", headers={"Sec-Fetch-Site": "same-origin"}, json={"locked": True}).status_code in (200, 404)
    ok = client.post(f"/api/boxes/{bid}/approve", headers={"Sec-Fetch-Site": "same-origin", "Origin": "http://testserver"})
    assert ok.status_code == 200                                         # the page itself
    behind_proxy = {"Sec-Fetch-Site": "same-origin", "Origin": "https://box.example.com"}   # the host was rewritten by a proxy
    bid3 = draft(client, "Box C1")
    assert client.post(f"/api/boxes/{bid3}/approve", headers=behind_proxy).status_code == 200  # the browser's own verdict wins
    bid2 = draft(client, "Box B1")
    assert client.post(f"/api/boxes/{bid2}/approve").status_code == 200   # curl, scripts, tests: no browser headers
    assert client.get("/api/state", headers=CROSS_SITE).status_code == 200  # reads are not blocked (the answer is unreadable cross-origin)


def test_token_routes_are_not_affected_by_the_browser_check(env):
    client, s, cat = make_client(env)                                    # sync token is "tok"
    rows = [{"id": f"{9000 + i:06d}", "name": f"T{i}", "vi": f"Đồ {i}", "type": "Obj", "status": "ACTIVE", "avail": 5, "qty": 5,
             "loc": "A", "sector": "Hanoi-GPI"} for i in range(60)]
    r = client.post("/api/catalog/sync", json={"objects": rows, "force": True}, headers={"X-Sync-Token": "tok", **CROSS_SITE})
    assert r.status_code == 200                                          # the userscript calls from the SM page, with its token
    assert client.post("/api/catalog/sync", json={"objects": rows}, headers=CROSS_SITE).status_code == 401   # still needs the token


def test_access_code_check(env):
    s, cat = env
    s.access_code = "ma-123"
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers={"X-Access-Code": "sai"}).status_code == 401
    assert client.get("/api/state", headers={"X-Access-Code": ""}).status_code == 401
    assert client.get("/api/state", headers={"X-Access-Code": "ma-12"}).status_code == 401            # a prefix is not enough
    assert client.get("/api/state?access_code=sai").status_code == 401
    assert client.get("/api/state?access_code=").status_code == 401
    assert client.get("/api/state", headers={"X-Access-Code": "ma-123"}).status_code == 200
    assert client.get("/api/state?access_code=ma-123").status_code == 200                              # download links pass it in the query
    assert client.get("/api/config").status_code == 200                  # public on purpose: the page needs it to show the gate


def test_every_route_is_guarded_except_the_two_public_ones(env):
    """A new endpoint that forgets its guard fails here. Token routes check the sync token inside the handler."""
    s, cat = env
    app = create_app(s, llm=picker.MockLLM(), catalog=cat)
    unguarded, token = set(), set()
    for r in app.routes:
        if not hasattr(r, "dependant") or not hasattr(r, "methods"):
            continue
        deps = [getattr(d.call, "__name__", "") for d in r.dependant.dependencies]
        if "auth" in deps:
            continue
        (token if r.path in ("/api/catalog/sync", "/api/sync/check", "/api/sm/boxes/sync") or r.path.endswith("/pushed") else unguarded).add(r.path)
    assert unguarded == {"/", "/api/config", "/box_builder_sm.user.js"}, unguarded
    assert token == {"/api/catalog/sync", "/api/sync/check", "/api/sm/boxes/sync", "/api/boxes/{box_id}/pushed"}, token


# ----------------------------------------------------------------------------- not a security finding, but a deploy one: no stale scripts
def test_page_links_carry_a_version_stamp_that_changes_with_the_files(env):
    import os
    import re
    from app import main as main_mod
    client, s, cat = make_client(env)
    html = client.get("/").text
    stamps = set(re.findall(r'/static/[\w.-]+\.(?:js|css)\?v=([0-9a-f]{10})"', html))
    assert len(stamps) == 1                                           # every script and style link has the same stamp
    assert re.search(r'/static/app\.js\?v=', html) and re.search(r'/static/thumbs\.js\?v=', html) and re.search(r'/static/app\.css\?v=', html)
    assert client.get("/").headers["cache-control"] == "no-cache"
    assert client.get("/").text == html                               # stable while nothing changes
    target = os.path.join(main_mod.STATIC, "app.css")
    before = os.stat(target)
    try:
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns + 5_000_000_000))
        assert set(re.findall(r'\?v=([0-9a-f]{10})"', client.get("/").text)) != stamps          # a changed file means a new stamp
    finally:
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert client.get("/static/app.js?v=anything").status_code == 200                  # the stamp is only a cache key


# ----------------------------------------------------------------------------- data leakage: secrets never show up in a response
def test_secrets_and_internals_never_appear_in_any_response(env, monkeypatch):
    """Plant secrets in the settings, read every endpoint that can be read, break things on purpose, and look for them."""
    from app import service
    s, cat = env
    s.sync_token = "SYNC-secret-TOKEN-7f3a9c"
    s.deepseek_key = "sk-leaktest-0123456789abcdef"
    s.llm_mode = "mock"                                            # the key is set but no model is called
    app = create_app(s, llm=picker.MockLLM(), catalog=cat)
    client = TestClient(app, raise_server_exceptions=False)
    secrets = [s.sync_token, s.deepseek_key, s.db_path, os.path.dirname(os.path.abspath(__file__))]
    bid = client.post("/api/boxes", json={"module": "M8", "label": "Box A1", "theme": "t", "count": 3}).json()["id"]
    wait_settled(client, bid)

    seen = []

    def look(resp, what):
        blob = resp.text + " " + " ".join(f"{k}: {v}" for k, v in resp.headers.items())
        seen.append(what)
        for secret in secrets:
            assert secret not in blob, f"{what} leaked {secret!r}"
        assert "Traceback" not in blob and "File \"" not in blob, f"{what} shows a stack trace"

    # every readable route, with path parameters filled in
    for r in app.routes:
        if "GET" not in getattr(r, "methods", set()) or r.path == "/static":
            continue
        path = r.path.replace("{box_id}", str(bid)).replace("{oid}", "000207").replace("{key}", "auto_approve").replace("{pos}", "1")
        look(client.get(path), f"GET {path}")
    # wrong ways to call things
    for what, resp in [
        ("unknown path", client.get("/api/nope")),
        ("wrong method", client.delete("/api/state")),
        ("not json", client.post("/api/boxes", content="{{{", headers={"Content-Type": "application/json"})),
        ("wrong types", client.post("/api/boxes", json={"module": 5, "label": [], "count": "x"})),
        ("missing box", client.post("/api/boxes/99999/approve")),
        ("bad sync token", client.post("/api/catalog/sync", json={"objects": []}, headers={"X-Sync-Token": "guess"})),
        ("sync with no token set is refused", client.post("/api/catalog/sync", json={"objects": []})),
        ("path trick", client.get("/static/../../etc/passwd")),
        ("encoded trick", client.get("/static/%2e%2e/%2e%2e/app/settings.py")),
    ]:
        look(resp, what)
    # a crash inside a handler: the answer must not repeat the message, which here contains a secret
    monkeypatch.setattr(service, "state", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(f"boom {s.sync_token} {s.deepseek_key}")))
    crashed = client.get("/api/state")
    assert crashed.status_code == 500
    look(crashed, "a crash")
    assert len(seen) > 20                                           # the loop really did visit the routes

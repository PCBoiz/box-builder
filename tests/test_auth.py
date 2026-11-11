"""Login by company ID (app/auth.py), tested on a small app that has one route of each class, so the rules do not depend on main.py."""
import time

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app import auth, db
from app.settings import Settings

SAME = {"Origin": "http://testserver"}


def mini(tmp_path, admin_ids="1001", admin_code="", base="http://testserver"):
    s = Settings(db_path=str(tmp_path / "a.db"), catalog_path="data/catalog.csv", admin_ids=admin_ids, admin_code=admin_code)
    db.init(s.db_path)
    app = FastAPI()
    auth.install(app, s)

    @app.get("/")
    def root():
        return HTMLResponse("admin page")

    @app.get("/dung")
    def dung():
        return HTMLResponse("user page")

    @app.get("/api/state")
    def state():
        return {"admin": "only"}

    @app.post("/api/boxes")
    def make():
        return {"made": True}

    @app.get("/api/my/ping")
    def ping():
        return {"user": "ok"}

    @app.get("/api/taskboxes")
    def taskboxes():
        return {"boxes": []}

    @app.get("/api/sheet-boxes")
    def sheet():
        return {"boxes": []}

    @app.post("/api/catalog/sync")
    def sync():
        return {"synced": True}

    @app.post("/api/sm/boxes/sync")
    def sm_sync():
        return {"synced": True}

    @app.get("/api/sync/check")
    def check():
        return {"ok": True}

    @app.get("/api/preflight")
    def preflight():
        return {"ok": True}

    @app.get("/api/sm/returnable")
    def returnable():
        return {"boxes": []}

    @app.post("/api/sheet/checks")
    def sheet_checks():
        return {"ok": True}

    @app.post("/api/boxes/{bid}/pushed")
    def pushed(bid: str):
        return {"ok": True}

    @app.delete("/api/boxes/{bid}/pushed")
    def unpushed(bid: str):
        return {"ok": True}

    @app.get("/api/config")
    def config():
        return {"c": 1}

    return TestClient(app, base_url=base, follow_redirects=False), s


def login(client, pid, code="", ip="10.0.0.1"):
    return client.post("/api/auth/login", json={"id": pid, "code": code}, headers={**SAME, "X-Forwarded-For": ip})


def add_people(client, text):
    r = client.post("/api/admin/people", json={"text": text}, headers=SAME)
    assert r.status_code == 200, r.text
    return r.json()


def as_admin(tmp_path, **kw):
    client, s = mini(tmp_path, **kw)
    assert login(client, "1001", kw.get("admin_code", "")).status_code == 200
    return client, s


# ----------------------------------------------------------------------------- off until the owner names an admin
def test_with_no_admin_named_the_app_stays_open_exactly_as_before(tmp_path):
    client, s = mini(tmp_path, admin_ids="")
    assert not auth.enforced(s)
    for method, path in [("get", "/"), ("get", "/api/state"), ("post", "/api/boxes"), ("get", "/dung"), ("get", "/api/my/ping"), ("get", "/api/admin/people")]:
        assert getattr(client, method)(path, headers=SAME).status_code == 200, path
    st = client.get("/api/auth/status").json()
    assert st == {"enforced": False, "person": None, "admin_code": False, "user_password": False}
    assert login(client, "1001").status_code == 409                            # nothing to log into yet, and it says why


def test_blank_and_junk_admin_settings_do_not_turn_login_on(tmp_path):
    for value in ("", "  ", " , ;", "x", "!!bad!!", "a b"):                     # an ID needs 2 to 20 letters, digits, dot, dash or underscore
        assert not auth.enforced(Settings(admin_ids=value)), value
    assert auth.enforced(Settings(admin_ids="ab"))
    assert auth.env_admins(Settings(admin_ids="1001, 2002;3003 ")) == {"1001", "2002", "3003"}


# ----------------------------------------------------------------------------- who may reach what
def test_without_a_session_pages_go_to_login_and_the_api_says_401(tmp_path):
    client, _ = mini(tmp_path)
    r = client.get("/")
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/"
    r = client.get("/dung")
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/dung"
    for path in ("/api/state", "/api/my/ping", "/api/admin/people", "/api/automations", "/api/backup.json"):
        assert client.get(path).status_code == 401, path
    assert client.get("/print/boxes").status_code == 302                                                    # a page a browser opens: sent to the login page, not shown JSON
    assert client.post("/api/boxes", headers=SAME).status_code == 401


def test_the_scripts_in_the_team_browser_tabs_keep_working_without_a_login(tmp_path):
    """The Tampermonkey tools and the sheet script cannot log in. Their routes must stay exactly as open as they were."""
    client, _ = mini(tmp_path)
    assert client.get("/api/taskboxes?status=approved").status_code == 200
    assert client.get("/api/sheet-boxes").status_code == 200
    assert client.get("/api/sync/check").status_code == 200
    assert client.get("/api/preflight").status_code == 200
    assert client.get("/api/sm/returnable").status_code == 200
    assert client.post("/api/catalog/sync").status_code == 200
    assert client.post("/api/sm/boxes/sync").status_code == 200
    assert client.post("/api/sheet/checks").status_code == 200                  # the sheet's own script reports the supporters' check: no login, its own token
    assert client.post("/api/boxes/7/pushed").status_code == 200
    assert client.delete("/api/boxes/7/pushed").status_code == 200
    assert client.get("/api/config").status_code == 200
    assert client.get("/login").status_code in (200, 404)                       # the page file is shipped by the app, not by this small test app
    assert auth.klass("GET", "/api/boxes/7/pushed") == "admin"                  # only POST and DELETE are the script's calls
    assert auth.klass("POST", "/api/boxes/7/approve") == "admin"
    assert auth.klass("POST", "/api/boxes/7/pushed/x") == "admin"
    assert auth.klass("GET", "/api/preflight") == auth.klass("GET", "/api/sm/returnable") == "machine"
    assert auth.klass("POST", "/api/sm/returnable/x") == "admin"
    assert auth.klass("POST", "/api/sheet/checks") == "machine"
    assert auth.klass("GET", "/api/sheet") == "admin" and auth.klass("PUT", "/api/sheet") == "admin" and auth.klass("GET", "/api/sheet/script") == "admin"   # the sheet box on the page stays admin only
    assert auth.klass("POST", "/api/sheet/checks/x") == "admin" and auth.klass("POST", "/api/sheet/check") == "admin"


def test_klass_is_default_deny():
    assert auth.klass("GET", "/api/anything/new") == "admin"
    assert auth.klass("GET", "/api/my/boxes") == "user" and auth.klass("GET", "/dung") == "user" and auth.klass("GET", "/my/print") == "user"
    assert auth.klass("GET", "/static/app.js") == "public" and auth.klass("GET", "/api/auth/login") == "public"
    assert auth.klass("GET", "/api/mybox") == "admin" and auth.klass("GET", "/dungx") == "admin"        # prefixes need the slash, no near-miss slips through


def test_a_user_reaches_the_user_routes_and_is_refused_the_admin_ones(tmp_path):
    client, _ = as_admin(tmp_path)
    add_people(client, "2002, Hải, M2")
    user, _ = mini(tmp_path)                                                    # same database file, new browser
    assert login(user, "2002").status_code == 200
    assert user.get("/api/my/ping").status_code == 200 and user.get("/dung").status_code == 200
    r = user.get("/")
    assert r.status_code == 302 and r.headers["location"] == "/dung"
    for method, path in [("get", "/api/state"), ("post", "/api/boxes"), ("get", "/api/admin/people"), ("get", "/api/automations")]:
        r = getattr(user, method)(path, headers=SAME)
        assert r.status_code == 403, (path, r.status_code)
    assert user.post("/api/admin/people", json={"text": "9999"}, headers=SAME).status_code == 403
    assert client.get("/").status_code == 200 and client.get("/api/state").status_code == 200       # the admin reaches everything


def test_a_login_names_where_to_go(tmp_path):
    client, _ = mini(tmp_path)
    add_people_client, _ = as_admin(tmp_path)
    add_people(add_people_client, "2002, Hải")
    assert login(client, "1001").json()["go"] == "/"
    other, _ = mini(tmp_path)
    assert login(other, "2002").json()["go"] == "/dung"


# ----------------------------------------------------------------------------- refusals
def test_an_unknown_id_is_refused_with_one_fixed_message_and_nothing_typed_is_stored(tmp_path):
    client, s = mini(tmp_path)
    r = login(client, "zq-typed-id")
    assert r.status_code == 401 and r.json()["detail"] == auth.GENERIC_REFUSAL
    conn = db.connect(s.db_path)
    assert "zq-typed-id" not in str([tuple(x) for x in conn.execute("SELECT * FROM login_fail").fetchall()])        # only hashes are kept
    rows = [tuple(x) for x in conn.execute("SELECT actor, action, detail FROM audit_log").fetchall()]
    assert rows == [("-", "login_fail", "")]                                     # the typed ID is not in the log
    for n, bad in enumerate(("", " ", "a", "x" * 41, "x" * 250, "id with space", "<script>", "../etc", None)):
        r = login(client, bad if bad is not None else "", ip=f"10.1.0.{n}")                                 # another address each time: no delay yet
        assert r.status_code == 401 and r.json()["detail"] == auth.GENERIC_REFUSAL, repr(bad)


def test_an_admin_code_is_asked_of_admins_only_and_a_wrong_one_looks_like_any_refusal(tmp_path):
    client, s = mini(tmp_path, admin_code="s3cret")
    wrong, none, unknown = login(client, "1001", "nope"), login(client, "1001", ""), login(client, "7777", "s3cret")
    assert {wrong.status_code, none.status_code, unknown.status_code} == {401}
    assert wrong.json() == none.json() == unknown.json()                         # nothing tells which part was wrong, or that 1001 is an admin
    assert login(client, "1001", "s3cret").status_code == 200
    add_people(client, "2002, Hải")
    user, _ = mini(tmp_path, admin_code="s3cret")
    assert login(user, "2002", "").status_code == 200                            # a user needs no code
    assert client.get("/api/auth/status").json()["admin_code"] is True


def test_the_status_route_tells_who_is_in_and_never_how_to_get_in(tmp_path):
    client, _ = mini(tmp_path)
    assert client.get("/api/auth/status").json() == {"enforced": True, "person": None, "admin_code": False, "user_password": False}
    login(client, "1001")
    st = client.get("/api/auth/status").json()
    assert st["person"] == {"id": "1001", "name": "", "module": "", "admin": True}


# ----------------------------------------------------------------------------- the cookie and the session
def test_the_cookie_is_http_only_lax_and_scoped_to_the_site_and_secure_on_https(tmp_path):
    client, _ = mini(tmp_path)
    c = login(client, "1001").headers["set-cookie"].lower()
    assert c.startswith("sid=") and "httponly" in c and "samesite=lax" in c and "path=/" in c and "secure" not in c and "domain" not in c
    https, _ = mini(tmp_path, base="https://testserver")
    c2 = login(https, "1001").headers["set-cookie"].lower()
    assert c2.startswith("__host-sid=") and "secure" in c2 and "httponly" in c2 and "samesite=lax" in c2 and "domain" not in c2
    behind_proxy = client.post("/api/auth/login", json={"id": "1001"}, headers={**SAME, "X-Forwarded-Proto": "https"})
    assert behind_proxy.headers["set-cookie"].lower().startswith("__host-sid=")      # Render ends TLS in front of the app


def test_only_a_hash_of_the_token_is_stored(tmp_path):
    client, s = mini(tmp_path)
    token = login(client, "1001").headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    assert len(token) >= 40
    stored = [tuple(x) for x in db.connect(s.db_path).execute("SELECT * FROM sessions").fetchall()]
    assert stored and all(token not in str(row) for row in stored) and stored[0][0] == auth._h_full(token)


def test_a_new_login_makes_a_new_token_and_logout_ends_it_everywhere(tmp_path):
    client, s = mini(tmp_path)
    t1 = login(client, "1001").headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    t2 = login(client, "1001").headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
    assert t1 != t2
    assert client.post("/api/auth/logout", headers=SAME).status_code == 200
    assert client.get("/api/state").status_code == 401
    # the first token was not logged out by the second login, but the one that logged out is gone from the table
    conn = db.connect(s.db_path)
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE token_hash=?", (auth._h_full(t2),)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE token_hash=?", (auth._h_full(t1),)).fetchone()[0] == 1


def test_a_session_ends_when_idle_too_long_or_too_old_or_when_the_person_leaves_the_list(tmp_path):
    client, s = as_admin(tmp_path)
    add_people(client, "2002, Hải")
    user, _ = mini(tmp_path)
    login(user, "2002")
    conn = db.connect(s.db_path)
    now = time.time()
    assert user.get("/api/my/ping").status_code == 200
    conn.execute("UPDATE sessions SET last_seen=? WHERE person_id='2002'", (now - auth.IDLE_SECONDS - 10,))
    assert user.get("/api/my/ping").status_code == 401                              # idle too long
    login(user, "2002")
    conn.execute("UPDATE sessions SET expires_at=? WHERE person_id='2002'", (now - 1,))
    assert user.get("/api/my/ping").status_code == 401                              # too old
    login(user, "2002")
    assert user.get("/api/my/ping").status_code == 200
    assert client.delete("/api/admin/people/2002", headers=SAME).status_code == 200
    assert user.get("/api/my/ping").status_code == 401                              # taken off the list: out at once
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE person_id='2002'").fetchone()[0] == 0


def test_last_seen_is_not_written_on_every_request(tmp_path):
    client, s = mini(tmp_path)
    login(client, "1001")
    conn = db.connect(s.db_path)
    before = conn.execute("SELECT last_seen FROM sessions").fetchone()[0]
    client.get("/api/state")
    assert conn.execute("SELECT last_seen FROM sessions").fetchone()[0] == before
    conn.execute("UPDATE sessions SET last_seen=?", (before - auth.TOUCH_EVERY - 5,))
    client.get("/api/state")
    assert conn.execute("SELECT last_seen FROM sessions").fetchone()[0] > before - 5


# ----------------------------------------------------------------------------- a request with the cookie must come from this site
def test_a_write_with_the_cookie_must_prove_it_comes_from_this_site(tmp_path):
    client, _ = as_admin(tmp_path)
    assert client.post("/api/boxes").status_code == 403                                                   # neither header: refused
    assert client.post("/api/boxes", headers={"Origin": "null"}).status_code == 403                       # null is not proof of anything
    assert client.post("/api/boxes", headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/boxes", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.post("/api/boxes", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403          # a sibling site is not this site
    assert client.post("/api/boxes", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.post("/api/boxes", headers={"Sec-Fetch-Site": "none"}).status_code == 200
    assert client.post("/api/boxes", headers=SAME).status_code == 200
    assert client.get("/api/state").status_code == 200                                                    # reading needs no proof


# ----------------------------------------------------------------------------- slowing down guessing
def test_one_address_that_fails_too_often_must_wait_and_another_address_does_not(tmp_path):
    client, _ = mini(tmp_path)
    for n in range(auth.IP_MAX):                                                                            # a different wrong ID each time
        assert login(client, f"70{n:02d}", ip="9.9.9.9").status_code == 401
    r = login(client, "7099", ip="9.9.9.9")
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and "giây" in r.json()["detail"]
    assert login(client, "1001", ip="9.9.9.9").status_code == 429                                           # even a right ID waits from that address
    assert login(client, "1001", ip="8.8.8.8").status_code == 200                                           # others are not locked out


def test_a_made_up_forwarded_address_does_not_help_because_the_last_entry_counts(tmp_path):
    client, _ = mini(tmp_path)
    for n in range(auth.IP_MAX):
        login(client, "7777", ip=f"6.6.6.{n}, 5.5.5.5")                                                     # the proxy appended the real address last
    assert login(client, "7777", ip="1.1.1.1, 5.5.5.5").status_code == 429


def test_guessing_an_admin_code_slows_that_pair_down_but_never_locks_the_admin_out(tmp_path):
    client, _ = mini(tmp_path, admin_code="s3cret")
    for _ in range(auth.KEY_MAX):
        assert login(client, "1001", "wrong", ip="7.7.7.7").status_code == 401
    r = login(client, "1001", "wrong", ip="7.7.7.7")
    assert r.status_code == 429 and int(r.headers["retry-after"]) <= 300                                    # a short wait, capped
    assert login(client, "2002", "x", ip="7.7.7.7").status_code == 401                                      # other IDs from that address are only subject to the address limit
    assert login(client, "1001", "s3cret", ip="8.8.8.8").status_code == 200                                 # the real admin, from another address, is not slowed by the guesses


def test_an_outsiders_guesses_at_the_admin_code_from_many_addresses_do_not_delay_the_admin(tmp_path):
    client, _ = mini(tmp_path, admin_code="s3cret")
    for n in range(auth.KEY_MAX * 3):
        login(client, "1001", "wrong", ip=f"7.7.8.{n}")                                                     # each address tries once
    assert login(client, "1001", "s3cret", ip="9.9.9.9").status_code == 200


def test_the_admin_code_is_trimmed_on_both_sides_and_blank_means_none(tmp_path):
    client, s = mini(tmp_path, admin_code="s3cret \n")                                                       # a stray space and newline from the Render value
    assert login(client, "1001", "s3cret").status_code == 200
    other, _ = mini(tmp_path, admin_code="s3cret")
    assert login(other, "1001", "  s3cret ").status_code == 200                                              # and the typed code
    assert Settings(admin_ids="1001", admin_code="   ").admin_code.strip() == "" and auth.admin_code_of(Settings(admin_code="  \n")) == ""
    blank, _ = mini(tmp_path, admin_code="  ")
    assert blank.get("/api/auth/status").json()["admin_code"] is False and login(blank, "1001", "").status_code == 200


def test_a_header_row_pasted_from_a_sheet_is_skipped_and_a_spaced_line_says_what_to_do():
    rows, bad = auth.parse_people("ID\tHọ tên\tModule\n1111\tHải\tM2\n2222\tHưng\tM3")
    assert [r["id"] for r in rows] == ["1111", "2222"] and bad == []
    assert [r["id"] for r in auth.parse_people("STT, Tên\n1001, A")[0]] == ["1001"]
    assert [r["id"] for r in auth.parse_people("Mã NV;Tên;Module\n1001;A;M1")[0]] == ["1001"]
    rows, bad = auth.parse_people("1001, A\nID, an id that is literally ID")
    assert [r["id"] for r in rows] == ["1001", "ID"]                                                         # only the first line can be a header
    rows, bad = auth.parse_people("1111 Hải M2")
    assert rows == [] and "dấu phẩy" in bad[0]["reason"] and "1111, Hải, M2" in bad[0]["reason"]
    assert "2 đến 20 ký tự" in auth.parse_people("a")[1][0]["reason"]


def test_pages_that_open_in_a_browser_send_a_visitor_to_login_and_keep_where_they_were_going(tmp_path):
    client, _ = mini(tmp_path)
    r = client.get("/my/print?ids=1,2")
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/my/print%3Fids%3D1%2C2"
    assert client.get("/print/boxes").headers["location"] == "/login?next=/print/boxes"
    assert client.get("/api/state").status_code == 401                                                        # the API still answers with JSON


def test_failures_are_counted_in_the_database_not_in_memory(tmp_path):
    client, s = mini(tmp_path)
    for _ in range(auth.IP_MAX):
        login(client, "7777", ip="3.3.3.3")
    fresh, _ = mini(tmp_path)                                                                               # a new process: nothing in memory
    assert login(fresh, "7777", ip="3.3.3.3").status_code == 429


def test_when_everyone_is_failing_addresses_that_already_failed_once_wait_but_a_first_try_goes_through(tmp_path):
    client, s = mini(tmp_path)
    conn = db.connect(s.db_path)
    now = time.time()
    conn.executemany("INSERT INTO login_fail(at, ip_hash, key_hash) VALUES(?,?,?)", [(now - 5, f"ip{n}", "") for n in range(auth.GLOBAL_MAX)])
    assert login(client, "1001", ip="4.4.4.4").status_code == 200                                           # never failed: goes through
    assert login(client, "7777", ip="2.2.2.2").status_code == 401                                           # a first failure
    assert login(client, "1001", ip="2.2.2.2").status_code == 429                                           # now it waits


def test_old_failures_are_forgotten(tmp_path):
    client, s = mini(tmp_path)
    conn = db.connect(s.db_path)
    old = time.time() - auth.FAIL_WINDOW - 60
    conn.executemany("INSERT INTO login_fail(at, ip_hash, key_hash) VALUES(?,?,?)", [(old, auth._h("testclient"), "") for _ in range(50)])
    assert login(client, "1001", ip="testclient").status_code == 200


# ----------------------------------------------------------------------------- the list of people
def test_the_admin_keeps_the_list_and_the_rules_for_a_line_are_plain():
    rows, bad = auth.parse_people("1111, Hải, M2\n2222;Hưng;M3;admin\n\n  4000  \n9, x\nthis is not an id\n1111, dup\nbad id, y")
    assert [r["id"] for r in rows] == ["1111", "2222", "4000"]
    assert rows[0] == {"id": "1111", "name": "Hải", "module": "M2", "admin": False}
    assert rows[1] == {"id": "2222", "name": "Hưng", "module": "M3", "admin": True}
    assert rows[2] == {"id": "4000", "name": "", "module": "", "admin": False}
    assert [b["line"] for b in bad] == [5, 6, 7, 8]
    assert all(b["reason"] for b in bad)
    assert auth.parse_people("1001, Admin, , ADMIN")[0][0]["admin"] is True                                 # the word admin in any case, anywhere after the ID
    rows, bad = auth.parse_people("\n".join(f"{1000 + n}" for n in range(auth.MAX_PEOPLE_LINES + 5)))
    assert len(rows) == auth.MAX_PEOPLE_LINES and bad and "tối đa" in bad[-1]["reason"]


def test_adding_updating_and_removing_people_is_for_admins_and_is_logged(tmp_path):
    client, s = as_admin(tmp_path)
    out = add_people(client, "2002, Hải, M2\n3003, Hưng, M3, admin\nxx yy")
    assert out["added"] == 2 and out["updated"] == 0 and len(out["bad"]) == 1
    out = add_people(client, "2002, Hải Mới, M5")
    assert out["added"] == 0 and out["updated"] == 1
    people = {p["id"]: p for p in client.get("/api/admin/people").json()["people"]}
    assert people["2002"]["name"] == "Hải Mới" and people["2002"]["module"] == "M5" and not people["2002"]["admin"]
    assert people["3003"]["admin"] and people["1001"]["from_setting"] and people["1001"]["admin"]
    assert client.post("/api/admin/people", json={"text": "  \n "}, headers=SAME).status_code == 422
    assert client.delete("/api/admin/people/1001", headers=SAME).status_code == 409                          # named in the setting: not removable here
    assert client.delete("/api/admin/people/9999", headers=SAME).status_code == 404
    assert client.delete("/api/admin/people/3003", headers=SAME).status_code == 200
    actions = [(e["actor"], e["action"]) for e in client.get("/api/admin/audit").json()["entries"]]
    assert ("1001", "people_save") in actions and ("1001", "people_remove") in actions and ("1001", "login") in actions


def test_an_admin_in_the_setting_stays_admin_whatever_the_pasted_list_says(tmp_path):
    client, _ = as_admin(tmp_path)
    add_people(client, "1001, Chủ, M1")                                                                       # no admin word on the line
    assert client.get("/api/admin/people", headers=SAME).status_code == 200
    me = client.get("/api/auth/status").json()["person"]
    assert me["admin"] is True and me["name"] == "Chủ"


def test_the_audit_log_keeps_the_ID_of_who_acted_and_no_secret(tmp_path):
    client, s = as_admin(tmp_path, admin_code="s3cret")
    login(client, "zq-typed-id", "s3cret", ip="1.2.3.4")
    conn = db.connect(s.db_path)
    dump = " ".join(str(tuple(r)) for r in conn.execute("SELECT * FROM audit_log").fetchall())
    assert "s3cret" not in dump and "zq-typed-id" not in dump and "1.2.3.4" not in dump                              # no code, no typed ID, no raw address


def test_people_ids_are_matched_exactly_and_whitespace_is_trimmed(tmp_path):
    client, _ = as_admin(tmp_path)
    add_people(client, "2002, Hải")
    other, _ = mini(tmp_path)
    assert login(other, " 2002 ").status_code == 200
    again, _ = mini(tmp_path)
    assert login(again, "20020").status_code == 401 and login(again, "200").status_code == 401


# ----------------------------------------------------------------------------- the next-address guard
@pytest.mark.parametrize("value, ok", [("/dung", True), ("/", True), ("/api/x?y=1", True), ("//evil.example", False), ("http://evil.example", False),
                                       ("/\\evil.example", False), ("javascript:alert(1)", False), ("", False), (None, False), ("/a\nb", False)])
def test_safe_next_only_allows_paths_on_this_site(value, ok):
    assert auth.safe_next(value) == (value if ok else "")


@pytest.mark.parametrize("value", ["/\t/evil.example", "/ /evil.example", "/%09/evil.example".replace("%09", "\t"), "/\x7f", "/a b"])
def test_safe_next_refuses_a_path_a_browser_would_read_as_another_site(value):
    """A browser drops a tab or a newline inside an address, so "/<tab>/evil.example" becomes "//evil.example" (found by the second review)."""
    assert auth.safe_next(value) == ""


def test_with_login_off_the_people_list_and_the_log_are_not_shown(tmp_path):
    """An ID is a login. A list read while the app is open (for example after the owner switched login off to get back in) would hand them out."""
    client, _ = as_admin(tmp_path)
    add_people(client, "2002, Hải")
    assert [p["id"] for p in client.get("/api/admin/people").json()["people"]] != [] and client.get("/api/admin/audit").json()["entries"] != []
    off, _ = mini(tmp_path, admin_ids="")
    body = off.get("/api/admin/people").json()
    assert body["enforced"] is False and body["people"] == []
    assert off.get("/api/admin/audit").json()["entries"] == []


def test_the_audit_log_keeps_the_last_lines_only(tmp_path):
    client, s = as_admin(tmp_path)
    conn = db.connect(s.db_path)
    for i in range(5030):
        auth.audit(conn, "-", "login_fail", detail="", ip="9.9.9.9", now=1000.0 + i)
    n = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    assert 4990 <= n <= 5001, n                                                         # a stranger's failed logins cannot grow the table without end
    assert conn.execute("SELECT MAX(at) FROM audit_log").fetchone()[0] == 1000.0 + 5029  # the newest line is kept


# ----------------------------------------------------------------------------- the password every ordinary user types (USER_PASSWORD) and USER_IDS
def mini_users(tmp_path, **kw):
    s = Settings(db_path=str(tmp_path / "a.db"), catalog_path="data/catalog.csv", admin_ids="1001", **kw)
    db.init(s.db_path)
    app = FastAPI()
    auth.install(app, s)
    return TestClient(app), s


def pw_login(client, pid, password="", code="", ip="10.0.0.1"):
    return client.post("/api/auth/login", json={"id": pid, "code": code, "password": password}, headers={**SAME, "X-Forwarded-For": ip})


def test_a_user_in_user_ids_signs_in_with_the_password_and_without_a_list(tmp_path):
    client, s = mini_users(tmp_path, user_ids="4001, 4002", user_password="test-pass")
    assert client.get("/api/auth/status").json()["user_password"] is True
    r = pw_login(client, "4001", "test-pass")
    assert r.status_code == 200 and r.json()["person"]["admin"] is False and r.json()["go"] == "/dung"
    other = TestClient(client.app)
    assert pw_login(other, "4002", "test-pass").status_code == 200                              # every listed ID shares the one password


def test_a_wrong_or_missing_password_looks_like_any_other_refusal(tmp_path):
    client, s = mini_users(tmp_path, user_ids="4001", user_password="test-pass")
    for bad in ("", "wrong", "TEST-PASS", "test-pass "[:-1] + "x"):
        r = pw_login(client, "4001", bad, ip="9.9.9.1")
        assert r.status_code == 401 and r.json()["detail"] == auth.REFUSAL_PASSWORD, repr(bad)
    assert pw_login(client, "9999", "test-pass", ip="9.9.9.2").json()["detail"] == auth.REFUSAL_PASSWORD     # an unknown ID gets the same sentence
    assert "test-pass" not in auth.REFUSAL_PASSWORD


def test_a_trailing_space_in_the_password_setting_does_not_lock_users_out(tmp_path):
    client, s = mini_users(tmp_path, user_ids="4001", user_password="test-pass \n")
    assert pw_login(client, "4001", "test-pass").status_code == 200


def test_the_password_is_for_users_only_admins_keep_their_own_code(tmp_path):
    client, s = mini_users(tmp_path, user_ids="4001", user_password="test-pass")
    assert pw_login(client, "1001").status_code == 200                                          # an admin without a code needs no user password
    again, _ = mini_users(tmp_path, user_ids="4001", user_password="test-pass", admin_code="s3cret")
    assert pw_login(again, "1001", password="test-pass", ip="9.9.9.3").status_code == 401        # the user password is not an admin's key
    assert pw_login(again, "1001", code="s3cret", ip="9.9.9.4").status_code == 200


def test_without_a_user_password_ids_alone_still_sign_in_and_user_ids_work(tmp_path):
    client, s = mini_users(tmp_path, user_ids="4001")
    assert client.get("/api/auth/status").json()["user_password"] is False
    assert pw_login(client, "4001").status_code == 200
    assert pw_login(TestClient(client.app), "4003", ip="9.9.9.5").json()["detail"] == auth.GENERIC_REFUSAL


def test_user_ids_cannot_be_removed_in_the_list_and_never_make_an_admin(tmp_path):
    client, s = mini_users(tmp_path, user_ids="4001", user_password="test-pass")
    assert pw_login(client, "1001").status_code == 200
    people = {p["id"]: p for p in client.get("/api/admin/people").json()["people"]}
    assert people["4001"]["admin"] is False and people["4001"]["from_setting"] is True
    assert client.delete("/api/admin/people/4001", headers=SAME).status_code == 409
    other = TestClient(client.app)
    assert pw_login(other, "4001", "test-pass").status_code == 200
    assert other.get("/api/admin/people").status_code == 403                                    # a user never reaches the admin routes


def test_user_ids_and_the_password_do_nothing_while_login_is_off(tmp_path):
    s = Settings(db_path=str(tmp_path / "a.db"), catalog_path="data/catalog.csv", admin_ids="", user_ids="4001", user_password="test-pass")
    assert auth.enforced(s) is False                                                            # login is on only when an admin is named


def test_setting_the_user_password_ends_every_session_made_before(tmp_path):
    """A stranger who got in while the ID was enough, or a password that leaked, must be out when the owner sets or changes USER_PASSWORD."""
    client, s = mini_users(tmp_path, user_ids="4001")
    assert pw_login(client, "4001").status_code == 200
    assert client.get("/api/auth/status").json()["person"]["id"] == "4001"
    set_pw, _ = mini_users(tmp_path, user_ids="4001", user_password="test-pass")
    set_pw.cookies.update(client.cookies)                                                       # the same browser, the app restarted with a password
    assert set_pw.get("/api/auth/status").json()["person"] is None
    assert pw_login(set_pw, "4001", "test-pass", ip="9.9.8.1").status_code == 200
    assert set_pw.get("/api/auth/status").json()["person"]["id"] == "4001"
    changed, _ = mini_users(tmp_path, user_ids="4001", user_password="test-pass-2")
    changed.cookies.update(set_pw.cookies)
    assert changed.get("/api/auth/status").json()["person"] is None                              # changing it ends them again
    assert set_pw.post("/api/auth/logout", headers=SAME).status_code == 200
    assert set_pw.get("/api/auth/status").json()["person"] is None                               # logout still works with a password set

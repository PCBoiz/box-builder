"""The routes the SM tool asks before it acts (preflight, returnable), the rules for reporting a pushed box, and the hardening around them:
no API docs, safe headers on every answer, and a log line with counts only on each token route."""
import json
import logging
import re
import time

import pytest
from fastapi.testclient import TestClient

from app import db, picker, service
from app.main import create_app, minutes_since
from app.settings import Settings
from tests.test_auth_app import SAME, app_client, sign_in
from tests.test_automation import live_rows, make_client, queue_on, sync
from tests.test_backend import _approved_box, conn_of, env, wait_settled  # noqa: F401  (env is a fixture)
from tests.test_reuse_old import box_with
from tests.test_sm_boxes import TOK, push_snapshot, sm_box, usable_ids

REPO_SCRIPT = "integrations/box_builder_sm.user.js"
DAY = 86400


def today():
    return service.vn_day(time.time())


def days_ago(n):
    return service.vn_day(time.time() - n * DAY)


def put_in_sm(s, box_id, sm_id):
    conn_of(s).execute("INSERT INTO sm_push(box_id, sm_box_id, pushed_at) VALUES(?,?,?)", (box_id, sm_id, time.time()))


def set_day(s, box_id, day):
    conn_of(s).execute("UPDATE boxes SET day=? WHERE id=?", (day, box_id))


def add_history(s, box_id, day, title, sm_box_id="", module="M5", label="Box B2", code="", anchor=""):
    conn_of(s).execute(
        "INSERT INTO box_history(box_id, module, label, code, anchor, theme, count, owner, title, sm_box_id, created_at, done_at, day, items) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (box_id, module, label, code, anchor, "", 3, "history-owner-81", title, sm_box_id, time.time(), time.time(), day, "[]"))


# ----------------------------------------------------------------------------- GET /api/preflight
def test_preflight_needs_the_sync_token(env):
    client, s, cat = make_client(env)
    for headers in ({}, {"X-Sync-Token": "wrong"}, {"X-Sync-Token": ""}):
        r = client.get("/api/preflight", headers=headers)
        assert r.status_code == 401 and r.json()["detail"] == "Sai mã đồng bộ."
    assert client.get("/api/preflight", headers=TOK).status_code == 200
    s.sync_token = ""
    blank = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    assert blank.get("/api/preflight", headers=TOK).status_code == 503                         # a server with no SYNC_TOKEN refuses


def test_preflight_reports_the_state_without_a_name(env):
    client, s, cat = make_client(env)
    r = client.get("/api/preflight", headers=TOK)
    version = re.search(r'const VERSION = "([^"]+)";', open(REPO_SCRIPT, encoding="utf-8").read()).group(1)
    assert r.json() == {"ok": True, "login_enforced": False, "sm_queue": False, "catalog_age_min": None, "sm_snapshot_age_min": None,
                        "ready_boxes": 0, "expected_script_version": version}                    # nothing synced yet, nothing built, queue off
    sync(client, live_rows(cat))
    push_snapshot(client, [sm_box("SM-1", "Someone else's", usable_ids(cat)[-3:])])
    bid = client.post("/api/boxes", json={"module": "M8", "label": "Box A1", "theme": "t", "count": 3, "owner": "owner-name-77"}).json()["id"]
    wait_settled(client, bid)
    client.post(f"/api/boxes/{bid}/approve")
    queue_on(client)
    r = client.get("/api/preflight", headers=TOK)
    got = r.json()
    assert got["ok"] is True and got["sm_queue"] is True and got["ready_boxes"] == 1
    assert got["catalog_age_min"] == 0 and got["sm_snapshot_age_min"] == 0                       # whole minutes since each snapshot
    assert set(got) == {"ok", "login_enforced", "sm_queue", "catalog_age_min", "sm_snapshot_age_min", "ready_boxes", "expected_script_version"}
    assert "owner-name-77" not in r.text and "owner" not in r.text


def test_preflight_counts_whole_minutes_and_reads_the_queue_that_the_tool_would_see(env):
    assert minutes_since(1000, 1000 + 185) == 3 and minutes_since(1000, 1000 + 59) == 0 and minutes_since(1000, 900) == 0       # never negative
    assert minutes_since(None, 5) is None and minutes_since("x", 5) is None
    client, s, cat = make_client(env)
    push_snapshot(client, [sm_box("SM-1", "X", usable_ids(cat)[:3])])
    c = conn_of(s)
    info = json.loads(c.execute("SELECT v FROM catalog_snapshot WHERE k='sm_boxes'").fetchone()[0])
    info["synced_at"] = time.time() - 7.5 * 60
    c.execute("UPDATE catalog_snapshot SET v=? WHERE k='sm_boxes'", (json.dumps(info),))
    assert client.get("/api/preflight", headers=TOK).json()["sm_snapshot_age_min"] == 7
    box = _approved_box(client, "Box A1")
    taken = next(b for b in client.get("/api/state").json()["boxes"] if b["id"] == box)["items"][0]["id"]
    push_snapshot(client, [sm_box("SM-2", "Holds one of ours", [taken])])
    assert client.get("/api/preflight", headers=TOK).json()["ready_boxes"] == 0                  # a blocked box is not counted as ready


def test_preflight_works_with_login_on_and_no_session(env):
    client, s, cat = app_client(env)                                                              # login enforced, as in production
    assert client.get("/api/preflight").status_code == 401                                        # the handler answered (its own message), not the gate
    assert client.get("/api/preflight").json()["detail"] == "Sai mã đồng bộ."
    got = client.get("/api/preflight", headers=TOK).json()
    assert got["ok"] is True and got["login_enforced"] is True


# ----------------------------------------------------------------------------- GET /api/sm/returnable
def seed_returnable(s):
    """A box of yesterday in SM, one of today in SM, one of yesterday never pushed, and two lines of history (one today)."""
    ids = [f"{n:06d}" for n in range(1, 60)]
    old = box_with(s, ids[0:3], label="Box B2", module="M8", status="approved")
    set_day(s, old, days_ago(1))
    conn_of(s).execute("UPDATE boxes SET code='M8-02', anchor='Bàn bếp', owner='owner-name-77' WHERE id=?", (old,))
    put_in_sm(s, old, "SM-OLD")
    new = box_with(s, ids[3:6], label="Box B3", module="M8", status="approved")
    conn_of(s).execute("UPDATE boxes SET owner='owner-name-77' WHERE id=?", (new,))
    put_in_sm(s, new, "SM-NEW")
    never = box_with(s, ids[6:9], label="Box A1", module="M8", status="approved")
    set_day(s, never, days_ago(1))
    add_history(s, 9001, days_ago(2), "M5 - Bàn học - B2", "")                                     # never heard back from the tool: empty SM id
    add_history(s, 9002, today(), "M5 - B3", "SM-HIST", label="Box B3")
    add_history(s, 9003, days_ago(3), "", "SM-H3", module="M6", label="Box A1", code="M6-01", anchor="")      # no stored title: built the same way
    return {"old": old, "new": new, "never": never}


def test_returnable_needs_the_sync_token(env):
    client, s, cat = make_client(env)
    for headers in ({}, {"X-Sync-Token": "wrong"}):
        assert client.get("/api/sm/returnable", headers=headers).status_code == 401
    assert client.get("/api/sm/returnable", headers=TOK).json() == {"boxes": []}
    s.sync_token = ""
    assert TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat)).get("/api/sm/returnable", headers=TOK).status_code == 503


def test_returnable_lists_live_pushed_boxes_and_history_before_today_only(env):
    s, cat = env
    s.sync_token, s.box_done_days = "tok", 0                                                      # 0: no housekeeping can move a box while this is checked
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    made = seed_returnable(s)
    r = client.get("/api/sm/returnable", headers=TOK)
    assert r.status_code == 200 and set(r.json()) == {"boxes"}
    rows = r.json()["boxes"]
    assert [(b["box_id"], b["day"], b["sm_box_id"]) for b in rows] == [(9003, days_ago(3), "SM-H3"), (9001, days_ago(2), ""), (made["old"], days_ago(1), "SM-OLD")]
    assert all(set(b) == {"box_id", "day", "title", "sm_box_id"} for b in rows)
    titles = {b["box_id"]: b["title"] for b in rows}
    assert titles[9001] == "M5 - Bàn học - B2" and titles[9003] == "M6-01 - A1"                  # the stored title; built when none is stored
    # a live box: exactly the title the tool sends to SM for it (the feed's `title`)
    feed = {b["builder"]["box_id"]: b["title"] for b in client.get("/api/taskboxes").json()["boxes"]}
    assert titles[made["old"]] == feed[made["old"]] == "M8-02 - Bàn bếp - B2"
    assert made["never"] not in titles and made["new"] not in titles                              # not pushed, and made today
    with_today = client.get("/api/sm/returnable?today=1", headers=TOK).json()["boxes"]
    assert [b["box_id"] for b in with_today] == [9003, 9001, made["old"], made["new"], 9002]     # oldest day first, then by box
    assert {b["box_id"]: b["sm_box_id"] for b in with_today}[9002] == "SM-HIST"
    assert client.get("/api/sm/returnable?today=true", headers=TOK).json() == {"boxes": with_today}
    assert client.get("/api/sm/returnable?today=0", headers=TOK).json()["boxes"] == rows
    assert client.get("/api/sm/returnable?today=banana", headers=TOK).status_code == 422


def test_returnable_has_no_owner_and_the_title_matches_every_kind_of_box(env):
    s, cat = env
    s.sync_token, s.box_done_days = "tok", 0
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    seed_returnable(s)
    for q in ("", "?today=1"):
        r = client.get("/api/sm/returnable" + q, headers=TOK)
        assert "owner" not in r.text and "owner-name-77" not in r.text and "history-owner-81" not in r.text
    # a box with a large-object name, one with an anchor ID, and one with neither
    ids = [f"{n:06d}" for n in range(100, 130)]
    rows = {}
    for n, (module, label, code, anchor) in enumerate((("M3", "Box B2", "M3-01", "Tủ lạnh"), ("M3", "Box B3", "", "004702"), ("M4", "Box A1", "M4-02", ""))):
        bid = box_with(s, ids[n * 3:n * 3 + 3], label=label, module=module, status="approved")
        conn_of(s).execute("UPDATE boxes SET code=?, anchor=?, day=? WHERE id=?", (code, anchor, days_ago(1), bid))
        put_in_sm(s, bid, f"SM-{n}")
        rows[bid] = n
    feed = {b["builder"]["box_id"]: b["title"] for b in client.get("/api/taskboxes").json()["boxes"]}
    got = {b["box_id"]: b["title"] for b in client.get("/api/sm/returnable", headers=TOK).json()["boxes"]}
    assert set(rows) <= set(feed) and all(got[b] == feed[b] for b in rows)
    assert sorted(got[b] for b in rows) == sorted(["M3-01 - Tủ lạnh - B2", "M3 - B3", "M4-02 - A1"])           # a name, an anchor ID (not in the title), neither


def test_returnable_and_preflight_never_run_housekeeping(env):
    """The tool polls these. A poll that retired a box or freed an object would be a write nobody asked for."""
    s, cat = env
    s.sync_token = "tok"                                                                          # box_done_days stays at its default of 1
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    ids = [f"{n:06d}" for n in range(1, 20)]
    finished = box_with(s, ids[0:3], label="Box B2", module="M8", status="approved")             # would be retired by housekeeping
    stale = box_with(s, ids[3:6], label="Box B3", module="M8", status="draft")                    # would be expired by housekeeping
    conn_of(s).execute("UPDATE boxes SET created_at=?, updated_at=?, day=? WHERE id=?", (time.time() - 5 * DAY, time.time() - 5 * DAY, days_ago(5), finished))
    conn_of(s).execute("UPDATE boxes SET updated_at=? WHERE id=?", (time.time() - 5 * DAY, stale))
    put_in_sm(s, finished, "SM-F")
    c = conn_of(s)
    before = (db.rev(c), c.execute("SELECT COUNT(*) FROM boxes").fetchone()[0], c.execute("SELECT COUNT(*) FROM box_history").fetchone()[0])
    for url in ("/api/preflight", "/api/sm/returnable", "/api/sm/returnable?today=1"):
        assert client.get(url, headers=TOK).status_code == 200
    assert (db.rev(c), c.execute("SELECT COUNT(*) FROM boxes").fetchone()[0], c.execute("SELECT COUNT(*) FROM box_history").fetchone()[0]) == before == (before[0], 2, 0)
    assert [b["box_id"] for b in client.get("/api/sm/returnable", headers=TOK).json()["boxes"]] == [finished]    # still a live row, found by the live read
    assert client.get("/api/state").status_code == 200                                            # while a page poll does clean up, for contrast
    assert conn_of(s).execute("SELECT COUNT(*) FROM boxes").fetchone()[0] == 0


def test_returnable_works_with_login_on_and_no_session(env):
    client, s, cat = app_client(env)
    assert client.get("/api/sm/returnable").json()["detail"] == "Sai mã đồng bộ."
    assert client.get("/api/sm/returnable", headers=TOK).json() == {"boxes": []}


# ----------------------------------------------------------------------------- POST /api/boxes/{id}/pushed
def pushed_row(s, bid):
    return conn_of(s).execute("SELECT sm_box_id, pushed_at FROM sm_push WHERE box_id=?", (bid,)).fetchone()


def test_the_same_sm_id_again_changes_nothing_and_another_id_is_refused(env):
    client, s, cat = make_client(env)
    bid = _approved_box(client)
    url = f"/api/boxes/{bid}/pushed"
    assert client.post(url, json={"sm_box_id": "SM-1"}, headers=TOK).json() == {"ok": True}
    first = tuple(pushed_row(s, bid))
    time.sleep(0.02)
    assert client.post(url, json={"sm_box_id": "SM-1"}, headers=TOK).status_code == 200            # same id: fine
    assert client.post(url, json={"sm_box_id": " SM-1 "}, headers=TOK).status_code == 200          # stray spaces do not make it another id
    assert tuple(pushed_row(s, bid)) == first                                                      # not rewritten: the time is the first one too
    r = client.post(url, json={"sm_box_id": "SM-2"}, headers=TOK)
    assert r.status_code == 409 and isinstance(r.json()["detail"], str) and "SM-1" in r.json()["detail"] and "SM-2" in r.json()["detail"]
    assert tuple(pushed_row(s, bid)) == first                                                      # the first id is kept
    assert next(b for b in client.get("/api/state").json()["boxes"] if b["id"] == bid)["pushed"]["sm_box_id"] == "SM-1"
    assert conn_of(s).execute("SELECT COUNT(*) FROM sm_push WHERE box_id=?", (bid,)).fetchone()[0] == 1
    # the admin escape hatch is how an id changes: forget it, then report the right one
    assert client.delete(url, headers=TOK).status_code == 200
    assert client.post(url, json={"sm_box_id": "SM-2"}, headers=TOK).status_code == 200
    assert pushed_row(s, bid)["sm_box_id"] == "SM-2"


@pytest.mark.parametrize("bad", ["", "   ", "SM 1", "SM.1", "SM/1", "SM-1\nSM-2", "é1", "a" * 65, "<b>1</b>", "1;DROP", "SM-1​"])
def test_an_sm_id_must_be_letters_digits_dash_or_underscore_up_to_64(env, bad):
    client, s, cat = make_client(env)
    bid = _approved_box(client)
    r = client.post(f"/api/boxes/{bid}/pushed", json={"sm_box_id": bad}, headers=TOK)
    assert r.status_code == 422 and isinstance(r.json()["detail"], str)                           # a plain sentence, like the other refusals
    assert pushed_row(s, bid) is None


@pytest.mark.parametrize("good", ["7", "SM-1", "MBX_0001", "a" * 64, "5f3e9c1a-77b2-4d1e-9a0b-0c1d2e3f4a5b"])
def test_ordinary_sm_ids_are_accepted(env, good):
    client, s, cat = make_client(env)
    bid = _approved_box(client)
    assert client.post(f"/api/boxes/{bid}/pushed", json={"sm_box_id": good}, headers=TOK).status_code == 200
    assert pushed_row(s, bid)["sm_box_id"] == good


def test_the_service_says_whether_it_wrote(env):
    client, s, cat = make_client(env)
    bid = _approved_box(client)
    c = conn_of(s)
    assert service.mark_pushed(c, bid, "SM-5") is True and service.mark_pushed(c, bid, "SM-5") is False
    with pytest.raises(service.Conflict):
        service.mark_pushed(c, bid, "SM-6")
    with pytest.raises(service.Invalid):
        service.mark_pushed(c, bid, "SM 5")


# ----------------------------------------------------------------------------- hardening: docs, headers, log lines
SAFE = {"x-content-type-options": "nosniff", "x-frame-options": "DENY", "referrer-policy": "no-referrer"}


def has_safe_headers(r):
    return all(r.headers.get(k) == v for k, v in SAFE.items())


def test_the_api_docs_are_off_unless_asked_for(env):
    client, s, cat = make_client(env)
    for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
        assert client.get(path).status_code == 404, path
    s.enable_docs = True
    on = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    assert on.get("/openapi.json").status_code == 200 and on.get("/docs").status_code == 200 and on.get("/redoc").status_code == 200


def test_enable_docs_comes_from_the_environment_and_only_the_value_1_turns_it_on(monkeypatch):
    monkeypatch.delenv("ENABLE_DOCS", raising=False)
    assert Settings.from_env().enable_docs is False
    for value, want in (("1", True), (" 1 ", True), ("0", False), ("", False), ("true", False), ("yes", False)):
        monkeypatch.setenv("ENABLE_DOCS", value)
        assert Settings.from_env().enable_docs is want, value


def test_every_answer_carries_the_safe_headers_and_api_answers_are_never_stored(env):
    client, s, cat = make_client(env)
    for path in ("/", "/dung", "/static/app.js", "/box_builder_sm.user.js", "/api/config", "/api/state", "/api/catalog", "/api/preflight",
                 "/no/such/page", "/api/no/such/route"):
        r = client.get(path)
        assert has_safe_headers(r), (path, dict(r.headers))
        if path.startswith("/api/"):
            assert r.headers["cache-control"] == "no-store", path
    assert client.get("/").headers["cache-control"] == "no-cache"                                  # pages and the script keep the rules they had
    assert client.get("/box_builder_sm.user.js").headers["cache-control"] == "no-cache"
    assert "no-store" not in client.get("/static/app.js").headers.get("cache-control", "")
    bad = client.post("/api/boxes", json={"module": ""})                                           # a 422 from validation
    assert bad.status_code == 422 and has_safe_headers(bad) and bad.headers["cache-control"] == "no-store"
    cross = client.post("/api/boxes", json={"module": "M1", "label": "Box A1"}, headers={"Sec-Fetch-Site": "cross-site"})   # refused by the cross-site check
    assert cross.status_code == 403 and has_safe_headers(cross) and cross.headers["cache-control"] == "no-store"
    assert client.get("/api/preflight", headers={"X-Sync-Token": "x"}).status_code == 401           # the token route's own refusal
    assert has_safe_headers(client.get("/api/preflight")) and client.get("/api/preflight").headers["cache-control"] == "no-store"
    assert client.get("/api/export.csv").headers["cache-control"] == "no-store"                    # a download of the boxes


def test_the_login_gate_answers_carry_the_safe_headers_too(env):
    """The gate answers without reaching a route (401, a redirect, 403). The header middleware must wrap it, so it is registered last."""
    client, s, cat = app_client(env)
    no_session = client.get("/api/state")
    assert no_session.status_code == 401 and has_safe_headers(no_session) and no_session.headers["cache-control"] == "no-store"
    page = client.get("/")
    assert page.status_code == 302 and has_safe_headers(page)                                      # sent to the login page
    posted = client.post("/api/boxes", json={"module": "M1", "label": "Box A1"}, headers=SAME)
    assert posted.status_code == 401 and has_safe_headers(posted) and posted.headers["cache-control"] == "no-store"
    assert has_safe_headers(client.get("/login")) and has_safe_headers(client.get("/api/auth/status"))
    sign_in(client, "1001")
    forged = client.post("/api/boxes", json={"module": "M1", "label": "Box A1"}, headers={"Origin": "https://evil.example"})
    assert forged.status_code == 403 and has_safe_headers(forged) and forged.headers["cache-control"] == "no-store"      # the gate's own cross-site refusal
    assert client.get("/api/state").status_code == 200 and has_safe_headers(client.get("/api/state"))
    assert has_safe_headers(client.get("/api/admin/people"))


def test_each_token_route_writes_one_log_line_with_counts_only(env, caplog):
    s, cat = env
    s.sync_token = "SYNC-secret-TOKEN-9z8y"
    tok = {"X-Sync-Token": s.sync_token}
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    caplog.set_level(logging.INFO, logger="box_builder")
    bid = _approved_box(client)
    r = client.post("/api/catalog/sync", json={"objects": live_rows(cat)}, headers=tok)
    assert r.status_code == 200
    assert client.post("/api/sm/boxes/sync", json={"boxes": [sm_box("SM-SECRET-ID", "Secret title", usable_ids(cat)[-3:])]}, headers=tok).status_code == 200
    assert client.post(f"/api/boxes/{bid}/pushed", json={"sm_box_id": "SM-SECRET-ID"}, headers=tok).status_code == 200
    assert client.post(f"/api/boxes/{bid}/pushed", json={"sm_box_id": "SM-SECRET-ID"}, headers=tok).status_code == 200
    assert client.get("/api/preflight", headers=tok).status_code == 200
    assert client.get("/api/sm/returnable", headers=tok).status_code == 200
    assert client.delete(f"/api/boxes/{bid}/pushed", headers=tok).status_code == 200
    lines = [rec.getMessage() for rec in caplog.records if rec.name == "box_builder"]
    events = [ln.split()[0] for ln in lines]
    assert events == ["catalog_sync", "sm_boxes_sync", "pushed", "pushed", "preflight", "returnable", "unpushed"], lines
    assert all(re.fullmatch(r"[a-z_]+( [a-z_]+=\d+)*", ln) for ln in lines), lines                 # an event name and whole numbers, nothing else
    assert lines[2] == "pushed new=1 same=0" and lines[3] == "pushed new=0 same=1"
    text = "\n".join(lines)
    for secret in (s.sync_token, "SM-SECRET-ID", "Secret title"):
        assert secret not in text


def test_a_refused_call_writes_no_log_line(env, caplog):
    client, s, cat = make_client(env)
    caplog.set_level(logging.INFO, logger="box_builder")
    assert client.get("/api/preflight", headers={"X-Sync-Token": "wrong-token-1"}).status_code == 401
    assert client.get("/api/sm/returnable").status_code == 401
    assert not [rec for rec in caplog.records if rec.name == "box_builder"]
    assert "wrong-token-1" not in caplog.text

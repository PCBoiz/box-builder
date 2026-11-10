"""The day of the person who makes boxes: a box is finished two Vietnam days after the day it was made (its objects come back), the plan of the day with
its end-of-day report, the boxes to hand back in the morning, and what was made lately."""
import datetime
import json
import time

import pytest

from app import db, service
from tests.test_auth_app import SAME, _own_box, _people, second_browser
from tests.test_automation import make_client
from tests.test_backend import conn_of, env  # noqa: F401  (env is a fixture)
from tests.test_reuse_old import box_with
from tests.test_sm_boxes import usable_ids

VN = service.VN


def at(day: str, hh=9, mm=0, ss=0) -> float:
    """Epoch of a Vietnam clock time."""
    y, m, d = map(int, day.split("-"))
    return datetime.datetime(y, m, d, hh, mm, ss, tzinfo=VN).timestamp()


def set_created(s, box_id, ts):
    conn_of(s).execute("UPDATE boxes SET created_at=?, updated_at=?, day=? WHERE id=?", (ts, ts, service.vn_day(ts), box_id))


# ----------------------------------------------------------------------------- a box is finished two Vietnam days after the day it was made
def test_a_box_is_finished_on_the_second_day_after_the_day_it_was_made(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    last_minute = box_with(s, ids[0:3], label="Box B2", module="M1", status="approved")               # made on the 11th, 23:59 Vietnam time
    next_midnight = box_with(s, ids[3:6], label="Box B3", module="M1", status="approved")             # made on the 12th, one second after midnight
    a_draft = box_with(s, ids[6:9], label="Box A1", module="M1", status="draft")                      # a draft is not finished by the calendar
    pushed = box_with(s, ids[9:12], label="Box B2", module="M2", status="approved")                   # an older one that is in SM
    set_created(s, last_minute, at("2026-11-11", 23, 59, 59))
    set_created(s, next_midnight, at("2026-11-12", 0, 0, 1))
    set_created(s, a_draft, at("2026-11-10"))
    set_created(s, pushed, at("2026-11-10", 8))
    conn_of(s).execute("INSERT INTO sm_push(box_id, sm_box_id, pushed_at) VALUES(?,?,?)", (pushed, "SM-55", at("2026-11-10", 9)))
    conn = conn_of(s)
    assert service.retire_done(conn, 2, now=at("2026-11-13", 2, 30)) == 2                              # the shift starts at 2:30 on the 13th
    left = {r[0] for r in conn.execute("SELECT id FROM boxes")}
    assert left == {next_midnight, a_draft}
    assert not {r[0] for r in conn.execute("SELECT object_id FROM box_items")} & set(ids[0:3] + ids[9:12])          # their objects are free again
    assert conn.execute("SELECT COUNT(*) FROM sm_push").fetchone()[0] == 0                                          # the record of the push went with the box
    hist = {r["box_id"]: r for r in conn.execute("SELECT * FROM box_history")}
    assert set(hist) == {last_minute, pushed} and hist[pushed]["sm_box_id"] == "SM-55" and hist[last_minute]["sm_box_id"] == ""
    assert hist[last_minute]["day"] == "2026-11-11" and hist[pushed]["day"] == "2026-11-10"
    assert json.loads(hist[last_minute]["items"]) == [{"id": i, "qty": 1} for i in ids[0:3]]
    assert hist[pushed]["title"] == "M2 - B2"
    assert service.retire_done(conn, 2, now=at("2026-11-13", 2, 30)) == 0                              # nothing twice
    assert service.retire_done(conn, 1, now=at("2026-11-13", 2, 30)) == 1                              # with one day it is the box of the 12th that goes
    assert {r[0] for r in conn.execute("SELECT id FROM boxes")} == {a_draft}


def test_zero_days_means_no_box_is_ever_finished_and_the_service_does_nothing_when_idle(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    old = box_with(s, ids[:3], label="Box B2", module="M1", status="approved")
    set_created(s, old, at("2026-01-01"))
    conn = conn_of(s)
    assert service.retire_done(conn, 0, now=at("2026-11-13")) == 0 and conn.execute("SELECT COUNT(*) FROM boxes").fetchone()[0] == 1
    before = db.rev(conn)
    service.housekeeping(conn, 12, 0)
    assert db.rev(conn) == before                                                                      # no work: no write, no new revision


def test_a_unit_makes_the_same_kind_of_box_again_the_next_day(env):
    """A name is used once per Vietnam day. A box lives two days after it is made, but the unit needs a new B2 every day: with the name taken for
    two days every unit got its B2 only every second day (found by the second review)."""
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    old = box_with(s, ids[:3], label="Box B2", module="M1", status="approved")
    conn = conn_of(s)
    with pytest.raises(service.Conflict):
        service.create_box(conn, "M1", "Box B2", "t", [], 3, "x")                                       # the same day: taken
    set_created(s, old, time.time() - 86400)                                                            # the box is from yesterday
    nxt = service.create_box(conn, "M1", "Box B2", "t", [], 3, "x")
    assert nxt > 0 and nxt != old                                                                       # the next day: the unit makes its B2 again
    with pytest.raises(service.Conflict):
        service.create_box(conn, "M1", "Box B2", "t", [], 3, "x")                                       # and today's is taken again
    two = service.create_batch(conn, [{"module": "M1", "label": "Box B3"}], 3, "x")                      # the batch route follows the same rule
    assert len(two) == 1
    with pytest.raises(service.Invalid):
        service.create_batch(conn, [{"module": "M1", "label": "Box B3"}], 3, "x")


def test_the_team_tools_get_the_newest_box_of_a_unit_and_no_owner(env):
    """Two approved boxes of one unit and kind on two days: the sheet gets only the newest. Nothing that needs no login carries a company ID."""
    client, s, cat = make_client(env)
    s.box_done_days = 2                                                                                # a box of yesterday is still alive (the default is 1 day)
    ids = usable_ids(cat)
    old = box_with(s, ids[:3], label="Box B2", module="M1", status="approved")
    conn = conn_of(s)
    conn.execute("UPDATE boxes SET code='M1-01', owner='2002' WHERE id=?", (old,))
    set_created(s, old, time.time() - 86400)
    new = box_with(s, ids[3:6], label="Box B2", module="M1", status="approved")
    conn.execute("UPDATE boxes SET code='M1-01', owner='2002' WHERE id=?", (new,))
    got = client.get("/api/sheet-boxes").json()["boxes"]
    assert [b["builder"]["box_id"] for b in got] == [new]
    both = client.get("/api/taskboxes?status=draft,generating,error,approved").json()["boxes"]       # the status parameter is ignored: approved only
    assert sorted(b["builder"]["box_id"] for b in both) == sorted([old, new])
    assert "owner" not in str(client.get("/api/taskboxes").json()) and "2002" not in str(client.get("/api/sheet-boxes").json())


def test_the_page_polls_finish_old_boxes_and_the_objects_show_as_free(env):
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    box_id, box = _own_box(hai, "Box A1")
    hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME)
    first = box["items"][0]["id"]
    obj = lambda: admin.get(f"/api/objects?q={first}&per=50").json()["items"][0]["status"]
    assert obj() == "approved"
    set_created(s, box_id, time.time() - 3 * 86400)                                                    # made three days ago
    mine = hai.get("/api/my/summary").json()                                                           # a person's page poll does the housekeeping
    assert mine["boxes"] == [] and obj() == "free"
    assert admin.get("/api/state").json()["boxes"] == []
    assert conn_of(s).execute("SELECT COUNT(*) FROM box_history").fetchone()[0] == 1


def test_the_admin_poll_does_it_too_and_the_number_of_days_is_a_setting(env):
    admin, s, cat = _people(env)
    s.box_done_days = 5
    from tests.test_auth_app import app_client, sign_in
    a5, _, _ = app_client(env)
    sign_in(a5, "1001")
    hai = second_browser(a5, "1001")
    box_id = hai.post("/api/my/boxes", json={"module": "M7", "label": "Box A1", "theme": "t"}, headers=SAME).json()["id"]
    from tests.test_auth_app import wait_box
    wait_box(hai, box_id)
    hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME)
    set_created(s, box_id, time.time() - 3 * 86400)
    assert len(a5.get("/api/state?rev=-1").json()["boxes"]) == 1                                       # five days: three days old is still alive
    s.box_done_days = 2
    a2, _, _ = app_client(env)
    sign_in(a2, "1001")
    assert a2.get("/api/state?rev=-1").json()["boxes"] == []


def test_old_history_is_dropped(env):
    client, s, cat = make_client(env)
    conn = conn_of(s)
    for n, age in enumerate((5, 29, 31, 60)):
        conn.execute("INSERT INTO box_history(box_id, module, label, count, created_at, done_at, day) VALUES(?,?,?,?,?,?,?)",
                     (n + 1, "M1", "Box B2", 3, time.time() - age * 86400, time.time() - age * 86400, "2026-01-01"))
    service.prune_history(conn)
    assert sorted(r[0] for r in conn.execute("SELECT box_id FROM box_history")) == [1, 2]


# ----------------------------------------------------------------------------- what was made lately
def test_the_themes_of_recent_boxes_for_a_module_and_a_section(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    now = at("2026-11-12", 10)
    conn = conn_of(s)
    for n, (module, label, code, theme, day) in enumerate([("M8", "M8-02 Box B2", "M8-02", "nấu ăn", "2026-11-11"), ("M8", "M8-02 Box B3", "M8-02", "giặt đồ", "2026-11-11"),
                                                          ("M8", "M8-03 Box B2", "M8-03", "dọn bếp", "2026-11-12"), ("m8", "M8-02 Box A1", "M8-02", "", "2026-11-12"),
                                                          ("M9", "M9-01 Box B2", "M9-01", "khác module", "2026-11-12"), ("M8", "M8-02 Box A2", "M8-02", "quá cũ", "2026-10-20")]):
        conn.execute("INSERT INTO boxes(module,label,theme,categories,code,anchor,count,status,message,owner,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (module, label, theme, "[]", code, "", 3, "approved", "", "x", at(day, 5), at(day, 5)))
    conn.execute("INSERT INTO box_history(box_id, module, label, code, theme, count, created_at, done_at, day) VALUES(?,?,?,?,?,?,?,?,?)",
                 (900, "M8", "M8-02 Box B2", "M8-02", "vệ sinh phòng", 3, at("2026-11-10", 5), at("2026-11-12", 1), "2026-11-10"))
    unit = service.recent_boxes(conn, "M8", "M8-02", now=now)
    assert [(i["days_ago"], i["kind"], i["theme"], i["state"]) for i in unit] == [(1, "Box B2", "nấu ăn", "đang có"), (1, "Box B3", "giặt đồ", "đang có"), (2, "Box B2", "vệ sinh phòng", "đã xong")]
    whole = service.recent_boxes(conn, "m8", now=now)                                                 # no code: the whole module, the case does not matter
    assert [i["theme"] for i in whole] == ["dọn bếp", "nấu ăn", "giặt đồ", "vệ sinh phòng"] or {i["theme"] for i in whole} == {"dọn bếp", "nấu ăn", "giặt đồ", "vệ sinh phòng"}
    assert all(i["theme"] for i in whole) and "quá cũ" not in {i["theme"] for i in whole} and "khác module" not in {i["theme"] for i in whole}
    assert service.recent_boxes(conn, "M8", "M8-02", now=now, limit=2).__len__() == 2


def test_the_recent_route_is_for_people_and_ignores_a_missing_module(env):
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    _own_box(hai, "Box A1")
    assert hai.get("/api/my/recent").json() == {"items": []}
    assert [i["theme"] for i in hai.get("/api/my/recent?module=M2").json()["items"]] == ["t"]            # the box made above, with its theme
    box_id = hai.post("/api/my/boxes", json={"module": "M2", "label": "Box B2", "theme": "nấu ăn", "code": "M2-01"}, headers=SAME).json()["id"]
    assert len(hai.get("/api/my/recent?module=M2").json()["items"]) == 2                                  # the whole module
    got = hai.get("/api/my/recent?module=M2&code=M2-01").json()["items"]
    assert len(got) == 1 and got[0]["theme"] == "nấu ăn" and got[0]["kind"] == "Box B2" and got[0]["days_ago"] == 0 and got[0]["state"] == "đang có"
    assert second_browser(admin, "3003").get("/api/my/recent?module=M2&code=M2-01").json()["items"][0]["theme"] == "nấu ăn"     # another person's themes help too
    anon = type(hai)(admin.app)
    assert anon.get("/api/my/recent?module=M2").status_code == 401


# ----------------------------------------------------------------------------- the plan of the day
UNITS = [{"code": "M8-01", "anchor": "Tủ lạnh", "shift": "full"}, {"code": "M8-02", "anchor": "Máy giặt", "shift": "off"},
         {"code": "M8-03", "anchor": "Giường", "shift": "first"}, {"code": "M8-04", "anchor": "Lò vi sóng", "shift": "second"},
         {"code": "M8-05", "anchor": "", "shift": "b1b2"}]


def test_the_boxes_a_unit_needs_follow_how_its_collector_works():
    kinds = lambda shift: [(k, opt) for k, opt, _ in service.NEEDS[shift]]
    assert kinds("full") == [("B2", False), ("B3", False), ("A1", False)]
    assert kinds("first") == [("B2", True), ("A1", False)]                                            # B2 may be skipped, A1 is still made
    assert kinds("second") == [("B3", False)]                                                         # B1 is pushed up to A1: no new A1
    assert kinds("b1b2") == [("B2", False), ("A1", False)] and "làm B3" in service.NEEDS["b1b2"][0][2]
    assert kinds("off") == []
    assert set(service.NEEDS) == set(service.SHIFTS)


def test_a_person_keeps_a_plan_for_the_day_and_sees_what_is_still_to_make(env):
    admin, s, cat = _people(env)
    hai, hung = second_browser(admin, "2002"), second_browser(admin, "3003")
    assert hai.get("/api/my/plan").json()["units"] == [] and hai.get("/api/my/plan").json()["report"] == ""
    r = hai.put("/api/my/plan", json={"module": "M8", "units": UNITS}, headers=SAME)
    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["module"] == "M8" and plan["saved"] is True and plan["day"] == service.vn_day(time.time())
    by = {u["code"]: u for u in plan["units"]}
    assert [n["kind"] for n in by["M8-01"]["needs"]] == ["B2", "B3", "A1"] and all(n["state"] == "none" for n in by["M8-01"]["needs"])
    assert by["M8-02"]["needs"] == [] and by["M8-02"]["shift_text"] == "Nghỉ"
    assert [n["optional"] for n in by["M8-03"]["needs"]] == [True, False] and by["M8-05"]["needs"][0]["note"].startswith("Dùng B2 làm B3")
    box_id = hai.post("/api/my/boxes", json={"module": "M8", "label": "Box B2", "theme": "t", "code": "M8-01"}, headers=SAME).json()["id"]
    wait_ok = __import__("tests.test_auth_app", fromlist=["wait_box"]).wait_box(hai, box_id)
    states = lambda: {n["kind"]: (n["state"], n["box_id"]) for n in {u["code"]: u for u in hai.get("/api/my/plan").json()["units"]}["M8-01"]["needs"]}
    assert states()["B2"] == ("draft", box_id) and states()["B3"] == ("none", None)
    hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME)
    assert states()["B2"] == ("approved", box_id)
    assert hung.get("/api/my/plan").json()["units"] == []                                             # each person has their own plan
    assert wait_ok["status"] in ("draft", "approved")


def test_the_report_names_the_units_that_did_not_work_a_full_day(env):
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    report = hai.put("/api/my/plan", json={"module": "M8", "units": UNITS}, headers=SAME).json()["report"]
    d = datetime.datetime.now(VN)
    assert report.splitlines()[0] == f"Báo cáo ngày {d.day:02d}/{d.month:02d}, module M8:"
    assert "Unit không đi làm: M8-02 (Máy giặt)." in report and "Unit làm nửa ca đầu: M8-03 (Giường)." in report
    assert "Unit làm nửa ca sau: M8-04 (Lò vi sóng)." in report and "Unit chỉ làm B1 và B2: M8-05." in report
    assert "M8-01" not in report                                                                      # a unit that works the whole day is not in a report of absences
    full = hai.put("/api/my/plan", json={"module": "M8", "units": [{"code": "M8-01", "anchor": "", "shift": "full"}]}, headers=SAME).json()["report"]
    assert full.endswith("tất cả unit đều đi làm cả ngày.")
    assert "—" not in report and "!" not in report


def test_the_next_day_starts_from_the_last_plan_with_everyone_back_to_a_full_day(env):
    client, s, cat = make_client(env)
    conn = conn_of(s)
    service.plan_save(conn, "2002", "2026-11-10", "M8", UNITS)
    nxt = service.plan_view(conn, "2002", "2026-11-11")
    assert nxt["saved"] is False and nxt["module"] == "M8" and [u["code"] for u in nxt["units"]] == [u["code"] for u in UNITS]
    assert {u["shift"] for u in nxt["units"]} == {"full"} and nxt["report"] == ""                      # no report until the day is saved: it would say "everyone worked" by mistake
    service.plan_save(conn, "2002", "2026-11-11", "M8", [{"code": "M8-01", "shift": "full"}])
    assert service.plan_view(conn, "2002", "2026-11-11")["report"].endswith("tất cả unit đều đi làm cả ngày.")
    assert service.plan_view(conn, "2002", "2026-11-10")["saved"] is True and {u["shift"] for u in service.plan_view(conn, "2002", "2026-11-10")["units"]} == {"full", "off", "first", "second", "b1b2"}
    assert service.plan_view(conn, "3003", "2026-11-11")["units"] == []                                # another person starts empty


@pytest.mark.parametrize("body, text", [
    ({"module": "M8", "units": [{"code": "", "shift": "full"}]}, "cần một mã"),
    ({"module": "M8", "units": [{"code": "M8-01", "shift": "half"}]}, "ca làm không đúng"),
    ({"module": "M8", "units": [{"code": "M8-01", "shift": "full"}, {"code": "m8-01", "shift": "off"}]}, "bị lặp"),
    ({"module": "M" * 21, "units": []}, "Module dài quá"),
    ({"module": "M8", "units": [{"code": "X" * 21, "shift": "full"}]}, "dài quá"),
])
def test_a_plan_with_a_mistake_says_what_to_fix(env, body, text):
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    r = hai.put("/api/my/plan", json=body, headers=SAME)
    assert r.status_code == 422 and text in r.json()["detail"], r.text
    assert hai.get("/api/my/plan").json()["saved"] is False                                           # a refused plan is not kept


def test_too_many_units_are_refused(env):
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    units = [{"code": f"M8-{n:02d}", "shift": "full"} for n in range(service.MAX_UNITS + 1)]
    assert hai.put("/api/my/plan", json={"module": "M8", "units": units}, headers=SAME).status_code == 422


def test_the_boxes_to_hand_back_are_those_of_yesterday_and_the_day_before(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    conn = conn_of(s)
    today = "2026-11-13"
    live = box_with(s, ids[:3], label="Box B3", module="M8", status="approved")
    conn.execute("UPDATE boxes SET owner='2002', code='M8-02', created_at=? WHERE id=?", (at("2026-11-12", 5), live))                # yesterday, still live
    conn.execute("INSERT INTO box_history(box_id, module, label, code, title, count, owner, created_at, done_at, day) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (901, "M8", "M8-02 Box B2", "M8-02", "M8-02 - B2", 3, "2002", at("2026-11-11", 5), at("2026-11-13", 0, 1), "2026-11-11"))      # the day before
    conn.execute("INSERT INTO box_history(box_id, module, label, code, title, count, owner, created_at, done_at, day) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (902, "M8", "M8-02 Box B2", "M8-02", "M8-02 - B2", 3, "2002", at("2026-11-10", 5), at("2026-11-12", 0, 1), "2026-11-10"))      # three days ago: already handed back
    conn.execute("INSERT INTO box_history(box_id, module, label, code, title, count, owner, created_at, done_at, day) VALUES(?,?,?,?,?,?,?,?,?,?)",
                 (903, "M8", "Box B2", "", "M8 - B2", 3, "3003", at("2026-11-12", 5), at("2026-11-14", 0, 1), "2026-11-12"))              # someone else's
    got = service.to_return(conn, "2002", today)
    assert [(r["days_ago"], r["kind"], r["title"]) for r in got] == [(1, "Box B3", "M8-02 - B3"), (2, "Box B2", "M8-02 - B2")]
    assert service.to_return(conn, "3003", today)[0]["days_ago"] == 1 and len(service.to_return(conn, "3003", today)) == 1
    assert service.to_return(conn, "2002", "2026-11-10") == []                                           # nothing was made before


def test_the_day_flow_routes_are_for_people_only(env):
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    anon = type(hai)(admin.app)
    assert anon.get("/api/my/plan").status_code == 401 and anon.put("/api/my/plan", json={"module": "M8", "units": []}, headers=SAME).status_code == 401
    assert hai.put("/api/my/plan", json={"module": "M8", "units": []}, headers={"Origin": "http://evil.example"}).status_code == 403          # a write must come from this site


# ----------------------------------------------------------------------------- text that can start markup is refused (the team script writes it as HTML)
@pytest.mark.parametrize("field, value", [("anchor", "<img src=x onerror=alert(1)>"), ("code", 'M8" onfocus="x'), ("label", "Box B2<script>"),
                                          ("module", "M8'"), ("anchor", "Tủ lạnh\u202e"), ("anchor", "a`b"), ("code", "M8\n02")])
def test_text_that_can_start_markup_is_refused_on_every_way_in(env, field, value):
    client, s, cat = make_client(env)
    body = {"module": "M8", "label": "Box B2", "theme": "t", "count": 3, "code": "M8-02", "anchor": "Tủ lạnh"}
    body[field] = value
    assert client.post("/api/boxes", json=body).status_code == 422
    assert client.post("/api/my/boxes", json=body, headers=SAME).status_code in (422, 401)           # the user route (open mode here: no session needed)
    assert conn_of(s).execute("SELECT COUNT(*) FROM boxes").fetchone()[0] == 0
    with pytest.raises(service.Invalid):
        service.create_box(conn_of(s), body["module"], body["label"], "t", [], 3, "x", body["code"], body["anchor"])
    ok = dict(body, module="M8", label="Box B2", code="M8-02", anchor="Tủ lạnh 2 cánh (Samsung) / AR-1")                   # ordinary names pass
    assert client.post("/api/boxes", json=ok).status_code == 201


# ----------------------------------------------------------------------------- a database made before this release is upgraded on start
def test_an_old_database_gets_the_day_column_and_the_per_day_name_index(tmp_path):
    """The live database (Neon) was made with a unique index on (module, label). Start the new code on such a file: the column appears with an
    empty value for the old rows, the old index is replaced, and the same name on another day is allowed while the same day is still refused."""
    import sqlite3
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE boxes(id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT NOT NULL, label TEXT NOT NULL, theme TEXT NOT NULL DEFAULT '',
            categories TEXT NOT NULL DEFAULT '[]', code TEXT NOT NULL DEFAULT '', anchor TEXT NOT NULL DEFAULT '', count INTEGER NOT NULL,
            status TEXT NOT NULL, message TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL, updated_at REAL NOT NULL);
        CREATE UNIQUE INDEX ux_box_name ON boxes(module, label);
        INSERT INTO boxes(module,label,count,status,created_at,updated_at) VALUES('M8','M8-02 Box B2',12,'approved',1.0,1.0);""")
    raw.commit()
    raw.close()
    db.init(path)
    db.init(path)                                                                                      # starting twice is harmless
    c = db.connect(path)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(boxes)")}
    assert "day" in cols
    assert c.execute("SELECT day FROM boxes").fetchone()[0] == service.vn_day(1.0)                    # old rows get their Vietnam day from created_at: the deploy day is guarded too
    names = {r["name"] for r in c.execute("PRAGMA index_list(boxes)")}
    assert "ux_box_name" not in names and "ux_box_name_day" in names
    ins = "INSERT INTO boxes(module,label,count,status,created_at,updated_at,day) VALUES('M8','M8-02 Box B2',12,'draft',2.0,2.0,?)"
    c.execute(ins, ("2026-11-12",))                                                                    # the same name on another day: allowed
    with pytest.raises(db.IntegrityError):
        c.execute(ins, ("2026-11-12",))                                                                # the same name the same day: refused
    c.close()


# ----------------------------------------------------------------------------- found by the third review
def test_only_the_newest_box_of_a_title_is_ready_for_the_sm_tool(env):
    """Yesterday's approved box that was never pushed must not be created in SM instead of today's (the tool takes the first of two equal titles)."""
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    conn = conn_of(s)
    old = box_with(s, ids[:3], label="Box B2", module="M1", status="approved")
    set_created(s, old, time.time() - 86400)
    new = box_with(s, ids[3:6], label="Box B2", module="M1", status="approved")
    for b in (old, new):
        conn.execute("UPDATE boxes SET code='M1-01' WHERE id=?", (b,))
    q = service.sm_queue(conn, cat, 3)
    assert [t["builder"]["box_id"] for t in q["ready"]] == [new]
    assert [b["box_id"] for b in q["blocked"]] == [old] and "mới hơn" in q["blocked"][0]["reasons"][0]


def test_the_paper_of_the_day_has_only_today_s_boxes_and_says_when_a_box_was_made(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    conn = conn_of(s)
    old = box_with(s, ids[:3], label="Box B2", module="M1", status="approved")
    set_created(s, old, time.time() - 86400)
    new = box_with(s, ids[3:6], label="Box B2", module="M1", status="approved")
    today = client.get("/print/boxes").text
    assert ids[3] in today and ids[0] not in today                                                    # yesterday's box is left out of the paper of the day
    both = client.get("/print/boxes?day=all").text
    assert ids[3] in both and ids[0] in both and "Làm ngày:" in both                                  # and the day is on the paper when asked for
    assert ids[0] in client.get(f"/print/boxes?ids={old}").text                                       # an explicit id always prints


@pytest.mark.parametrize("value", ["a­b", "a⁠b", "a⁦b", "a﻿b", "a؜b", "a b"])
def test_invisible_characters_are_refused_too(env, value):
    client, s, cat = make_client(env)
    with pytest.raises(service.Invalid):
        service.create_box(conn_of(s), "M8", "Box B2", "t", [], 3, "x", "M8-02", "Tủ" + value)
    with pytest.raises(service.Invalid):
        service.plan_save(conn_of(s), "2002", "2026-11-11", "M8", [{"code": "M8-01", "anchor": "x" + value, "shift": "full"}])
    with pytest.raises(service.Invalid):
        service.plan_save(conn_of(s), "2002", "2026-11-11", "M8", [{"code": 'M8"', "anchor": "", "shift": "full"}])

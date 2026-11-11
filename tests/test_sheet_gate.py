"""The supporters' check from the sheet (Chunk 5, server side): the route the sheet's own script calls, the reconcile rule, the switch
"Chờ người hỗ trợ kiểm trước khi lên SM" and the `sheet` part of every box. The server never reads the sheet: these tests play the script."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import automation, db, picker, service
from app.main import create_app
from tests.test_auth_app import SAME, app_client, second_browser, sign_in
from tests.test_backend import conn_of, env  # noqa: F401  (env is a fixture)
from tests.test_reuse_old import box_with, iso, snapshot
from tests.test_sm_boxes import sm_box, usable_ids

TOK = {"X-Sheet-Token": "sheet-tok"}
SHEET_KEYS = {"state", "missing", "diff", "read_at"}


def gate_client(env, token="sheet-tok"):
    s, cat = env
    s.sync_token, s.sheet_token = "tok", token
    return TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat)), s, cat


def switch(client, on=True):
    r = client.put("/api/automations/need_supporter_check", json={"enabled": on})
    assert r.status_code == 200, r.text


def chk(box_id, sc=True, sup=True, rows=()):
    """One report as the sheet script sends it. rows = [(id, qty)]."""
    return {"box_id": box_id, "sc_tick": sc, "supporter_tick": sup, "rows": [{"id": i, "qty": q} for i, q in rows]}


def post(client, *checks, headers=TOK):
    return client.post("/api/sheet/checks", json={"checks": list(checks)}, headers=headers)


def approved(s, oids, label="Box B2", module="M7"):
    return box_with(s, oids, label=label, module=module, status="approved")


def clear_post(client, *checks):
    """A normal day: the app first sees the supporters' tick off (the script reports right after it fills the box), then on."""
    post(client, *[{**c, "supporter_tick": False} for c in checks])
    return post(client, *checks)


def boxes_of(client):
    return {b["id"]: b for b in client.get("/api/state").json()["boxes"]}


def items_of(s, box_id):
    return [(r["object_id"], r["qty"]) for r in conn_of(s).execute("SELECT object_id, qty FROM box_items WHERE box_id=? ORDER BY pos", (box_id,)).fetchall()]


def used_rows(s):
    return {r["object_id"]: r for r in conn_of(s).execute("SELECT * FROM external_used").fetchall()}


def stocky(cat, n):
    """Objects with at least 3 in stock, so a quantity of 3 is allowed in a box."""
    return [i for i in usable_ids(cat) if cat.get(i).stock >= 3][:n]


def sheet_of(client, box_id):
    return boxes_of(client)[box_id]["sheet"]


def reasons(client):
    return {b["box_id"]: b["reasons"] for b in client.get("/api/queue").json()["blocked"]}


def ready_ids(client):
    return {b["box_id"] for b in client.get("/api/queue").json()["ready"]}


# ----------------------------------------------------------------------------- reconcile: the pure rule
def R(*pairs):
    return [{"id": i, "qty": q} for i, q in pairs]


ITEMS = R(("000101", 1), ("000102", 3), ("000103", 1), ("000104", 2), ("000105", 1))


def test_a_report_equal_to_the_box_is_clean_and_changes_nothing():
    g = service.reconcile(ITEMS, R(*[(i["id"], i["qty"]) for i in ITEMS]))
    assert g["diff"] == [] and g["removed"] == [] and g["lowered"] == []
    assert g["left"] == [i["id"] for i in ITEMS] and g["keep"] == ITEMS


def test_objects_the_supporters_blanked_are_accepted_as_removed_and_the_rest_is_kept_in_box_order():
    g = service.reconcile(ITEMS, R(("000105", 1), ("000101", 1), ("000103", 1)))      # a different order, 000102 and 000104 are blank
    assert g["diff"] == [] and g["removed"] == ["000102", "000104"] and g["lowered"] == []
    assert g["left"] == ["000101", "000103", "000105"]                                   # the box's order, not the sheet's
    assert g["keep"] == R(("000101", 1), ("000103", 1), ("000105", 1))


def test_a_smaller_quantity_is_accepted_and_a_blank_quantity_keeps_the_boxs_own():
    g = service.reconcile(ITEMS, R(("000101", 1), ("000102", 1), ("000103", None), ("000104", 2), ("000105", None)))
    assert g["diff"] == [] and g["removed"] == [] and g["lowered"] == ["000102"]
    assert g["keep"] == R(("000101", 1), ("000102", 1), ("000103", 1), ("000104", 2), ("000105", 1))


def test_a_quantity_of_zero_is_the_same_as_a_blank_cell():
    g = service.reconcile(ITEMS, R(("000101", 0), ("000102", 3), ("000103", 1), ("000104", 2), ("000105", 1)))
    assert g["diff"] == [] and g["removed"] == ["000101"] and "000101" not in g["left"]


def test_an_id_the_box_does_not_hold_is_a_difference_and_so_is_a_bigger_quantity():
    g = service.reconcile(ITEMS, R(("000101", 1), ("000102", 3), ("000103", 1), ("000104", 2), ("000105", 1), ("000999", 1)))
    assert g["diff"] == [{"id": "000999", "why": "extra"}] and g["removed"] == []
    g = service.reconcile(ITEMS, R(("000101", 2), ("000102", 3), ("000103", 1), ("000104", 2), ("000105", 1)))
    assert g["diff"] == [{"id": "000101", "why": "qty_up"}]
    assert "000101" in g["left"] and "000101" not in g["removed"]                           # it is there, it is just more than the box says
    g = service.reconcile(ITEMS, R(("000102", 4), ("000888", None), ("000777", 1)))
    assert g["diff"] == [{"id": "000777", "why": "extra"}, {"id": "000888", "why": "extra"}, {"id": "000102", "why": "qty_up"}]    # extras first, each sorted by ID


def test_a_difference_is_reported_even_when_something_was_also_removed():
    g = service.reconcile(ITEMS, R(("000101", 1), ("000999", 1)))
    assert g["diff"] == [{"id": "000999", "why": "extra"}] and g["removed"] == ["000102", "000103", "000104", "000105"]


def test_nothing_left_is_reported_as_nothing_left_and_never_as_a_difference():
    g = service.reconcile(ITEMS, [])
    assert g["diff"] == [] and g["left"] == [] and g["keep"] == [] and len(g["removed"]) == 5
    assert service.reconcile([], [])["left"] == []


def test_the_same_id_twice_adds_up_and_ids_are_read_with_their_zeros():
    g = service.reconcile(R(("000101", 2)), R(("101", 1), ("000101", 1)))                    # 101 is 000101: two rows of one each is the quantity 2
    assert g["diff"] == [] and g["keep"] == R(("000101", 2))
    g = service.reconcile(R(("000101", 1)), R(("101", 1), ("000101", 1)))
    assert g["diff"] == [{"id": "000101", "why": "qty_up"}]                                  # twice, but the box holds one
    g = service.reconcile(R(("000101", 1)), R(("000101", None), ("000101", None)))           # a blank quantity counts as one each
    assert g["diff"] == [{"id": "000101", "why": "qty_up"}]


def test_reconcile_does_not_change_what_it_was_given():
    items, rows = [dict(i) for i in ITEMS], R(("000101", 1), ("000999", 2))
    before = (json.dumps(items), json.dumps(rows))
    service.reconcile(items, rows)
    assert (json.dumps(items), json.dumps(rows)) == before


@pytest.mark.parametrize("raw,want", [
    ([{"id": "003076", "qty": 1}], [{"id": "003076", "qty": 1}]),
    ([{"id": 3076, "qty": 2}], [{"id": "003076", "qty": 2}]),                                # the sheet may send the ID as a number
    ([{"id": "3076"}], [{"id": "003076", "qty": None}]),                                     # no quantity = not said
    ([{"id": "003076", "qty": "2"}], [{"id": "003076", "qty": 2}]),
    ([{"id": "003076", "qty": 2.7}], [{"id": "003076", "qty": 2}]),                          # never rounded up
    ([{"id": "003076", "qty": 0}], [{"id": "003076", "qty": 0}]),
    ([{"id": "003076", "qty": -4}], [{"id": "003076", "qty": 0}]),
    ([{"id": "003076", "qty": 10 ** 9}], [{"id": "003076", "qty": 9999}]),
    ([{"id": "003076", "qty": float("nan")}], [{"id": "003076", "qty": None}]),
    ([{"id": "003076", "qty": float("inf")}], [{"id": "003076", "qty": None}]),
    ([{"id": "003076", "qty": True}], [{"id": "003076", "qty": None}]),
    ([{"id": "003076", "qty": "abc"}], [{"id": "003076", "qty": None}]),
    ([{"id": "003076", "qty": 10 ** 400}], [{"id": "003076", "qty": 9999}]),                 # no overflow on a huge number
    ([{"id": ""}, {"id": None}, {"id": "abc"}, {"id": "1234567"}, {"id": True}, {"id": 3076.0}, {"id": [1]}, "x", None, 5, {"qty": 1}], []),   # not an ID: dropped
    ("not a list", []), (None, []), ({"id": "003076"}, []),
])
def test_rows_are_cleaned_before_they_are_stored_or_compared(raw, want):
    assert service.sheet_rows(raw) == want


# ----------------------------------------------------------------------------- the sheet part of a box, as a function
def test_the_sheet_view_for_each_state():
    items = R(("000101", 1), ("000102", 2))
    rec = lambda sc, sup, rows=(), at=5.0, seen=True: {"sc": sc, "sup": sup, "seen": seen, "rows": R(*rows), "read_at": at}
    view = lambda **kw: service.sheet_view(kw.get("status", "approved"), kw.get("pushed", False), kw.get("gate_on", True), 2, items, kw.get("record"))
    off = {"state": "off", "missing": 0, "diff": [], "read_at": None}
    assert view(gate_on=False, record=rec(True, True, [("000101", 1), ("000102", 2)])) == off          # the switch is off: nothing shows
    assert view(status="draft", record=rec(True, True)) == off and view(status="generating") == off and view(status="error") == off
    assert view(pushed=True, record=rec(True, True)) == off                                          # already in SM: the gate no longer applies
    assert view() == {"state": "wait_sc", "missing": 0, "diff": [], "read_at": None}
    assert view(record=rec(None, True))["state"] == "wait_sc" and view(record=rec(False, True))["state"] == "wait_sc"
    assert view(record=rec(True, None)) == {"state": "wait_support", "missing": 0, "diff": [], "read_at": 5.0}
    assert view(record=rec(True, False))["state"] == "wait_support"
    assert view(record=rec(True, True, [("000101", 1), ("000102", 2)], seen=False)) == {"state": "wait_support", "missing": 0, "diff": [], "read_at": 5.0}   # on at the first look: not trusted
    assert view(record=rec(False, True, [("000101", 1), ("000102", 2)], seen=False))["state"] == "wait_sc"                                           # the SC's tick has no such rule
    assert view(record=rec(True, None, seen=True))["state"] == "wait_support" and view(record=rec(True, False, seen=False))["state"] == "wait_support"
    assert view(record=rec(True, True, [("000101", 1), ("000102", 2)])) == {"state": "ok", "missing": 0, "diff": [], "read_at": 5.0}
    assert view(record=rec(True, True, [("000101", 1)])) == {"state": "ok", "missing": 1, "diff": [], "read_at": 5.0}
    assert view(record=rec(True, True, [("000101", 1), ("000999", 1)])) == {"state": "diff", "missing": 1, "diff": [{"id": "000999", "why": "extra"}], "read_at": 5.0}
    assert view(record=rec(True, True, [("000101", 5), ("000102", 2)]))["diff"] == [{"id": "000101", "why": "qty_up"}]
    assert view(record=rec(True, True)) == {"state": "missing", "missing": 2, "diff": [], "read_at": 5.0}
    assert view(record=rec(True, True, [("000101", 0), ("000102", 0)]))["state"] == "missing"


# ----------------------------------------------------------------------------- the switch
def test_the_switch_exists_and_starts_off_so_a_deploy_changes_nothing(env):
    client, s, cat = gate_client(env)
    meta = automation.META["need_supporter_check"]
    assert meta["enabled"] is False and meta["title"] == "Chờ người hỗ trợ kiểm trước khi lên SM" and meta["config"] == {} and meta["limits"] == {}
    sw = {x["key"]: x for x in client.get("/api/automations").json()["switches"]}
    assert len(sw) == 9 and sw["need_supporter_check"]["enabled"] is False and sw["need_supporter_check"]["title"] == meta["title"]
    assert list(sw)[-1] == "need_supporter_check"                                                    # new switches go last: the others keep their place on the page
    assert sw["sheet_export"]["enabled"] and not sw["sm_queue"]["enabled"]                          # the queue starts OFF (reads-only stage): the gate also needs it switched on
    switch(client, True)
    assert {x["key"]: x for x in client.get("/api/automations").json()["switches"]}["need_supporter_check"]["enabled"] is True
    switch(client, False)
    assert "Chờ người hỗ trợ kiểm trước khi lên SM: đã bật" in [e["msg"] for e in client.get("/api/automations").json()["log"]]


# ----------------------------------------------------------------------------- the route: who may call it
def test_without_sheet_token_set_the_route_answers_503_in_plain_words_and_records_nothing(env):
    client, s, cat = gate_client(env, token="")
    box = approved(s, usable_ids(cat)[:3])
    for headers in ({}, TOK, {"X-Sheet-Token": ""}, {"X-Sync-Token": "tok"}):
        r = post(client, chk(box), headers=headers)
        assert r.status_code == 503, headers
        assert r.json()["detail"] == "Máy chủ chưa đặt SHEET_TOKEN nên chưa nhận kết quả kiểm từ bảng tính."
    assert conn_of(s).execute("SELECT COUNT(*) FROM sheet_checks").fetchone()[0] == 0


def test_a_wrong_or_missing_token_is_401_and_the_sync_token_is_not_the_sheet_token(env):
    client, s, cat = gate_client(env)
    box = approved(s, usable_ids(cat)[:3])
    for headers in ({}, {"X-Sheet-Token": ""}, {"X-Sheet-Token": "wrong"}, {"X-Sheet-Token": "sheet-tox"}, {"X-Sync-Token": "tok"}, {"X-Sheet-Token": "tok"}):
        r = post(client, chk(box), headers=headers)
        assert r.status_code == 401 and r.json()["detail"] == "Sai mã của bảng tính.", headers
    assert conn_of(s).execute("SELECT COUNT(*) FROM sheet_checks").fetchone()[0] == 0
    # and the other way round: the sheet token opens none of the sync routes
    assert client.get("/api/sync/check", headers={"X-Sync-Token": "sheet-tok"}).status_code == 401
    assert client.post("/api/sm/boxes/sync", json={"boxes": []}, headers={"X-Sync-Token": "sheet-tok"}).status_code == 401
    assert client.post(f"/api/boxes/{box}/pushed", json={"sm_box_id": "SM-1"}, headers={"X-Sync-Token": "sheet-tok"}).status_code == 401
    assert post(client, chk(box)).status_code == 200


def test_the_token_is_checked_before_the_body_is_looked_at(env):
    client, s, cat = gate_client(env)
    r = client.post("/api/sheet/checks", json={"checks": "oops"}, headers={"X-Sheet-Token": "wrong"})
    assert r.status_code == 401 and r.json()["detail"] == "Sai mã của bảng tính."                    # not a 422 about the body
    assert client.post("/api/sheet/checks", json={"checks": "oops"}, headers=TOK).status_code == 422


def test_the_route_works_from_the_sheet_with_login_on_and_from_a_script_with_cross_site_headers(env):
    admin, s, cat = app_client(env)                                                                   # login on
    s.sheet_token = "sheet-tok"
    box = approved(s, usable_ids(cat)[:3])
    visitor = TestClient(admin.app, follow_redirects=False)                                           # no session: the sheet script cannot log in
    assert visitor.post("/api/sheet/checks", json={"checks": [chk(box)]}).status_code == 401
    assert visitor.post("/api/sheet/checks", json={"checks": [chk(box)]}).json()["detail"] == "Sai mã của bảng tính."
    r = visitor.post("/api/sheet/checks", json={"checks": [chk(box)]}, headers={**TOK, "Sec-Fetch-Site": "cross-site", "Origin": "http://elsewhere.example"})
    assert r.status_code == 200 and r.json() == {"ok": True, "saved": 1, "skipped": 0}
    assert visitor.get("/api/sheet").status_code == 401                                               # the sheet box on the page is still admin only
    assert visitor.get("/api/sheet/script").status_code == 401


def test_nothing_that_names_a_person_comes_back_from_the_route_or_the_feeds(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:3])
    conn_of(s).execute("UPDATE boxes SET owner='7654' WHERE id=?", (box,))
    switch(client)
    r = clear_post(client, chk(box, rows=[(ids[0], 1)]))
    assert r.json() == {"ok": True, "saved": 1, "skipped": 0} and "7654" not in r.text
    for path in ("/api/taskboxes", "/api/taskboxes?pending=true", "/api/sheet-boxes"):
        assert "7654" not in client.get(path).text, path
    assert "7654" not in json.dumps(sheet_of(client, box))


# ----------------------------------------------------------------------------- the route: limits and what is skipped
def test_at_most_200_boxes_and_30_rows_each_and_a_refusal_writes_nothing(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:3])
    unknown = [chk(900000 + n) for n in range(200)]
    r = post(client, *unknown)
    assert r.status_code == 200 and r.json() == {"ok": True, "saved": 0, "skipped": 200}              # 200 is allowed
    r = post(client, chk(box), *unknown, chk(900500))
    assert r.status_code == 422 and "200" in r.json()["detail"]
    assert conn_of(s).execute("SELECT COUNT(*) FROM sheet_checks").fetchone()[0] == 0                # the good box in that request was not saved either
    ok = post(client, chk(box, rows=[(i, 1) for i in ids[:30]]))                                    # exactly 30 rows is allowed
    assert ok.status_code == 200 and ok.json()["saved"] == 1
    r = post(client, chk(box, sc=False), chk(900001, rows=[(i, 1) for i in ids[:31]]))
    assert r.status_code == 422 and "30" in r.json()["detail"]
    assert json.loads(conn_of(s).execute('SELECT "rows" FROM sheet_checks WHERE box_id=?', (box,)).fetchone()[0])[0]["id"] == ids[0]       # still the 30-row report
    assert conn_of(s).execute("SELECT sc_tick FROM sheet_checks WHERE box_id=?", (box,)).fetchone()[0] == 1                                # the sc=False line was not applied
    for bad in ({"checks": "oops"}, {"checks": {"box_id": 1}}, {"checks": 5}, [chk(box)], "text"):
        r = client.post("/api/sheet/checks", json=bad, headers=TOK)
        assert r.status_code == 422, bad
    assert client.post("/api/sheet/checks", content=b"not json", headers={**TOK, "Content-Type": "application/json"}).status_code == 422
    assert client.post("/api/sheet/checks", json={}, headers=TOK).json() == {"ok": True, "saved": 0, "skipped": 0}


def test_entries_that_are_not_reports_are_skipped_and_counted(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:3])
    junk = ["x", 5, None, [1], {}, {"box_id": box}, {"box_id": str(box), "rows": []}, {"box_id": True, "rows": []}, {"box_id": 1.5, "rows": []},
            {"box_id": -3, "rows": []}, {"box_id": 0, "rows": []}, {"box_id": 2 ** 70, "rows": []}, {"box_id": box, "rows": "nope"}, {"box_id": box, "rows": {"id": 1}}]
    r = client.post("/api/sheet/checks", json={"checks": junk + [chk(box)]}, headers=TOK)
    assert r.status_code == 200 and r.json() == {"ok": True, "saved": 1, "skipped": len(junk)}


def test_unknown_not_approved_and_pushed_boxes_are_skipped_and_counted(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    good = approved(s, ids[:3], "Box B2")
    draft = box_with(s, ids[3:6], label="Box B3", module="M7", status="draft")
    error = box_with(s, ids[6:9], label="Box A1", module="M7", status="error")
    done = approved(s, ids[9:12], "Box C1")
    service.mark_pushed(conn_of(s), done, "SM-1")
    r = post(client, chk(good), chk(draft), chk(error), chk(done), chk(424242))
    assert r.json() == {"ok": True, "saved": 1, "skipped": 4}
    assert [x[0] for x in conn_of(s).execute("SELECT box_id FROM sheet_checks").fetchall()] == [good]


def test_a_box_pushed_after_its_report_keeps_the_old_report_and_takes_no_new_one(env):
    client, s, cat = gate_client(env)
    box = approved(s, usable_ids(cat)[:3])
    post(client, chk(box, sc=True, sup=None))
    service.mark_pushed(conn_of(s), box, "SM-1")
    assert post(client, chk(box, sc=True, sup=True, rows=[])).json() == {"ok": True, "saved": 0, "skipped": 1}
    row = conn_of(s).execute("SELECT sc_tick, supporter_tick FROM sheet_checks WHERE box_id=?", (box,)).fetchone()
    assert (row[0], row[1]) == (1, None)


def test_the_latest_report_replaces_the_previous_one_and_a_repeat_changes_nothing_else(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:3])
    assert post(client, chk(box, sc=True, sup=None, rows=[(ids[0], 1), (ids[1], 1)])).json()["saved"] == 1
    first = conn_of(s).execute('SELECT sc_tick, supporter_tick, "rows", read_at FROM sheet_checks WHERE box_id=?', (box,)).fetchone()
    assert (first[0], first[1]) == (1, None) and [r["id"] for r in json.loads(first[2])] == [ids[0], ids[1]]
    time.sleep(0.02)
    assert post(client, chk(box, sc=False, sup=True, rows=[(ids[2], 2)])).json()["saved"] == 1
    rows = conn_of(s).execute('SELECT sc_tick, supporter_tick, "rows", read_at FROM sheet_checks WHERE box_id=?', (box,)).fetchall()
    assert len(rows) == 1 and (rows[0][0], rows[0][1]) == (0, 1) and json.loads(rows[0][2]) == [{"id": ids[2], "qty": 2}] and rows[0][3] > first[3]
    assert post(client, chk(box, sc=False, sup=True, rows=[(ids[2], 2)])).json()["saved"] == 1        # the same again: still one row
    assert conn_of(s).execute("SELECT COUNT(*) FROM sheet_checks").fetchone()[0] == 1
    # two lines for one box in one request: the later wins, the earlier one is counted as skipped
    r = post(client, chk(box, sc=True, sup=True, rows=[(ids[0], 1)]), chk(box, sc=True, sup=False, rows=[]))
    assert r.json() == {"ok": True, "saved": 1, "skipped": 1}
    row = conn_of(s).execute('SELECT sc_tick, supporter_tick, "rows" FROM sheet_checks WHERE box_id=?', (box,)).fetchone()
    assert (row[0], row[1], row[2]) == (1, 0, "[]")


def test_ticks_are_trusted_only_when_they_are_real_booleans_and_ids_are_cleaned_on_the_way_in(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:3])
    body = {"box_id": box, "sc_tick": "TRUE", "supporter_tick": 1, "rows": [{"id": ids[0], "qty": "2"}, {"id": "xx"}, {"id": 12345678}, {"id": ids[0].lstrip("0") or "0", "qty": 1}, "junk"]}
    assert client.post("/api/sheet/checks", json={"checks": [body]}, headers=TOK).json()["saved"] == 1
    row = conn_of(s).execute('SELECT sc_tick, supporter_tick, "rows" FROM sheet_checks WHERE box_id=?', (box,)).fetchone()
    assert (row[0], row[1]) == (None, None)                                                          # a text or a number is not a tick
    assert json.loads(row[2]) == [{"id": ids[0], "qty": 3}]                                           # the same ID twice added up (2 + 1), bad IDs dropped
    body["sc_tick"], body["supporter_tick"] = None, False
    client.post("/api/sheet/checks", json={"checks": [body]}, headers=TOK)
    row = conn_of(s).execute("SELECT sc_tick, supporter_tick FROM sheet_checks WHERE box_id=?", (box,)).fetchone()
    assert (row[0], row[1]) == (None, 0)


def test_a_box_deleted_takes_its_report_with_it(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    keep, gone, finished = approved(s, ids[:3], "Box B2"), approved(s, ids[3:6], "Box B3"), approved(s, ids[6:9], "Box A1")
    post(client, chk(keep), chk(gone), chk(finished))
    assert conn_of(s).execute("SELECT COUNT(*) FROM sheet_checks").fetchone()[0] == 3
    assert client.delete(f"/api/boxes/{gone}").status_code == 200
    assert [r[0] for r in conn_of(s).execute("SELECT box_id FROM sheet_checks ORDER BY box_id").fetchall()] == sorted([keep, finished])
    conn_of(s).execute("UPDATE boxes SET created_at=?, day=? WHERE id=?", (time.time() - 3 * 86400, service.vn_day(time.time() - 3 * 86400), finished))
    assert service.retire_done(conn_of(s), 1) == 1                                                    # a box finished by the calendar goes the same way
    assert [r[0] for r in conn_of(s).execute("SELECT box_id FROM sheet_checks").fetchall()] == [keep]


def test_the_panel_knows_when_the_sheet_last_reported_and_for_how_many_boxes(env):
    client, s, cat = gate_client(env)
    box = approved(s, usable_ids(cat)[:3])
    got = client.get("/api/sheet").json()
    assert got["last_check"] is None and got["last_fetch"] is None
    before = time.time()
    post(client, chk(box), chk(999999))
    last = client.get("/api/sheet").json()["last_check"]
    assert last["boxes"] == 1 and before - 1 <= last["at"] <= time.time() + 1 and set(last) == {"at", "boxes"}
    post(client)
    assert client.get("/api/sheet").json()["last_check"]["boxes"] == 0                                # an empty report still shows the script is alive
    assert client.get("/api/sheet").json()["last_fetch"] is None                                      # the other timestamp is not touched
    assert post(client, chk(box), headers={"X-Sheet-Token": "wrong"}).status_code == 401
    assert client.get("/api/sheet").json()["last_check"]["boxes"] == 0                                # a refused call leaves no trace


# ----------------------------------------------------------------------------- a clean report is applied to the box (switch on)
def test_removed_objects_leave_the_box_and_go_on_the_used_list_and_the_box_stays_approved(env):
    client, s, cat = gate_client(env)
    ids = stocky(cat, 5)
    box = approved(s, ids)
    conn_of(s).execute("UPDATE box_items SET qty=3 WHERE box_id=? AND object_id=?", (box, ids[1]))
    switch(client)
    rev = client.get("/api/state").json()["rev"]
    t0 = time.time()
    r = clear_post(client, chk(box, rows=[(ids[0], 1), (ids[1], 2), (ids[4], None)]))                          # ids[2] and ids[3] are blank, ids[1] went from 3 to 2
    assert r.json() == {"ok": True, "saved": 1, "skipped": 0}
    assert items_of(s, box) == [(ids[0], 1), (ids[1], 2), (ids[4], 1)]                                # the supporters' list
    st = boxes_of(client)[box]
    assert st["status"] == "approved" and st["count"] == 5 and len(st["items"]) == 3
    assert st["sheet"]["state"] == "ok" and st["sheet"]["missing"] == 2                               # 5 asked for, 3 left
    used = used_rows(s)
    assert set(used) == {ids[2], ids[3]} and all(u["note"] == "thiếu theo người hỗ trợ" and t0 - 1 <= u["added_at"] <= time.time() + 1 for u in used.values())
    assert {ids[2], ids[3]} <= service.taken_ids(conn_of(s))                                          # the picker leaves them alone
    assert service.owner_of(conn_of(s), ids[2]) == "danh sách đã dùng trong bảng tính"
    assert client.get(f"/api/state?rev={rev}").json().get("unchanged") is not True                    # open pages refresh
    assert client.get("/api/sm/boxes/report").json()["conflicts"] == []
    audit = conn_of(s).execute("SELECT actor, action, box_id, detail FROM audit_log WHERE action='sheet_apply'").fetchall()
    assert len(audit) == 1 and (audit[0][0], audit[0][1], audit[0][2]) == ("bảng tính", "sheet_apply", box)
    assert audit[0][3] == "theo bảng tính: bớt 2 món, giảm số lượng 1 món, còn 3 món"
    assert not any(i in audit[0][3] for i in ids)                                                      # counts only


def test_the_same_report_again_changes_nothing_and_writes_no_second_audit_line(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:4])
    switch(client)
    body = chk(box, rows=[(ids[0], 1), (ids[1], 1)])
    clear_post(client, body)
    first_used = {k: dict(zip(v.keys(), v)) for k, v in used_rows(s).items()}
    time.sleep(0.02)
    post(client, body)
    assert items_of(s, box) == [(ids[0], 1), (ids[1], 1)]
    assert {k: dict(zip(v.keys(), v)) for k, v in used_rows(s).items()} == first_used
    assert conn_of(s).execute("SELECT COUNT(*) FROM audit_log WHERE action='sheet_apply'").fetchone()[0] == 1
    assert sheet_of(client, box)["state"] == "ok" and sheet_of(client, box)["missing"] == 2


def test_an_object_already_on_the_used_list_keeps_its_note_and_never_gets_an_earlier_day(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:4])
    c = conn_of(s)
    c.execute("INSERT INTO external_used(object_id, note, added_at) VALUES(?,?,?)", (ids[1], "M3 box", 1000.0))               # an old day
    c.execute("INSERT INTO external_used(object_id, note, added_at) VALUES(?,?,?)", (ids[2], "no day known", 0.0))             # no day: never reused
    c.execute("INSERT INTO external_used(object_id, note, added_at) VALUES(?,?,?)", (ids[3], "from the future", time.time() + 5 * 86400))
    switch(client)
    clear_post(client, chk(box, rows=[(ids[0], 1)]))
    used = {k: (v["note"], v["added_at"]) for k, v in used_rows(s).items()}
    assert used[ids[1]][0] == "M3 box" and used[ids[1]][1] > time.time() - 60                                           # the later day replaces the earlier one
    assert used[ids[2]] == ("no day known", 0.0)                                                                       # still never reused
    assert used[ids[3]][0] == "from the future" and used[ids[3]][1] > time.time() + 4 * 86400                           # a later day is never moved back


def test_what_is_not_accepted_leaves_the_box_untouched(env):
    client, s, cat = gate_client(env)
    ids = stocky(cat, 12)
    extra = approved(s, ids[:3], "Box B2")
    up = approved(s, ids[3:6], "Box B3")
    nothing = approved(s, ids[6:9], "Box A1")
    one_tick = approved(s, ids[9:12], "Box C1")
    switch(client)
    clear_post(client, chk(extra, rows=[(ids[0], 1), (ids[1], 1), ("000999", 1)]),                           # one removed and one that is not in the box
         chk(up, rows=[(ids[3], 1), (ids[4], 2), (ids[5], 1)]),                                          # one quantity above the box's
         chk(nothing, rows=[]),                                                                          # the supporters have none of it
         chk(one_tick, sc=True, sup=False, rows=[(ids[9], 1)]))                                          # the supporters have not ticked
    assert items_of(s, extra) == [(i, 1) for i in ids[:3]] and items_of(s, up) == [(i, 1) for i in ids[3:6]]
    assert items_of(s, nothing) == [(i, 1) for i in ids[6:9]] and items_of(s, one_tick) == [(i, 1) for i in ids[9:12]]
    assert used_rows(s) == {} and conn_of(s).execute("SELECT COUNT(*) FROM audit_log WHERE action='sheet_apply'").fetchone()[0] == 0


def test_a_report_with_only_one_tick_does_not_change_the_box_even_if_it_is_clean(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    a, b = approved(s, ids[:3], "Box B2"), approved(s, ids[3:6], "Box B3")
    switch(client)
    clear_post(client, chk(a, sc=False, sup=True, rows=[(ids[0], 1)]), chk(b, sc=True, sup=None, rows=[(ids[3], 1)]))
    assert items_of(s, a) == [(i, 1) for i in ids[:3]] and items_of(s, b) == [(i, 1) for i in ids[3:6]] and used_rows(s) == {}
    post(client, chk(a, rows=[(ids[0], 1)]))                                                           # now SC ticks too: it is applied
    assert items_of(s, a) == [(ids[0], 1)] and set(used_rows(s)) == {ids[1], ids[2]}


def test_with_the_switch_off_a_report_is_kept_but_changes_nothing_and_turning_it_on_applies_it(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:4])
    clear_post(client, chk(box, rows=[(ids[0], 1), (ids[1], 1)]))                                            # the switch is off (the default)
    assert items_of(s, box) == [(i, 1) for i in ids[:4]] and used_rows(s) == {}
    assert conn_of(s).execute("SELECT COUNT(*) FROM sheet_checks").fetchone()[0] == 1                  # recorded for later
    assert sheet_of(client, box)["state"] == "off"
    switch(client)                                                                                     # the owner turns the gate on: the waiting report takes effect now
    assert items_of(s, box) == [(ids[0], 1), (ids[1], 1)] and set(used_rows(s)) == {ids[2], ids[3]}
    assert ready_ids(client) == {box}
    switch(client, False)                                                                              # off again: nothing is undone and nothing is gated
    assert items_of(s, box) == [(ids[0], 1), (ids[1], 1)]
    switch(client)                                                                                     # on again: nothing more to apply, no second audit line
    assert conn_of(s).execute("SELECT COUNT(*) FROM audit_log WHERE action='sheet_apply'").fetchone()[0] == 1


def test_a_box_that_reached_sm_is_never_rewritten_by_an_old_report(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:4])
    clear_post(client, chk(box, rows=[(ids[0], 1)]))                                                         # a report while the switch is off
    service.mark_pushed(conn_of(s), box, "SM-1")                                                       # the box is created in SM
    switch(client)
    assert items_of(s, box) == [(i, 1) for i in ids[:4]] and used_rows(s) == {}
    assert sheet_of(client, box) == {"state": "off", "missing": 0, "diff": [], "read_at": None}
    assert service.settle_sheet_checks(conn_of(s)) == 0


def test_a_box_taken_back_to_draft_is_not_rewritten_either(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:4])
    clear_post(client, chk(box, rows=[(ids[0], 1)]))
    assert client.post(f"/api/boxes/{box}/reopen").status_code == 200
    switch(client)
    assert items_of(s, box) == [(i, 1) for i in ids[:4]] and used_rows(s) == {}


def test_a_failure_while_applying_leaves_the_box_and_the_used_list_as_they_were(env, monkeypatch):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:4])
    switch(client)
    post(client, chk(box, sup=False, rows=[(ids[0], 1)]))                                              # the app has seen the supporters' tick off
    from app import auth

    def boom(*a, **k):
        raise RuntimeError("the audit write failed")
    monkeypatch.setattr(auth, "audit", boom)
    with pytest.raises(RuntimeError):
        service.save_sheet_checks(conn_of(s), [chk(box, rows=[(ids[0], 1)])], time.time(), settle=True)
    assert items_of(s, box) == [(i, 1) for i in ids[:4]] and used_rows(s) == {}                        # one transaction: all or nothing
    row = conn_of(s).execute("SELECT supporter_tick, support_seen_unticked FROM sheet_checks WHERE box_id=?", (box,)).fetchone()
    assert (row[0], row[1]) == (0, 1)                                                                  # still the earlier report


# ----------------------------------------------------------------------------- the gate in the queue
def test_with_the_switch_off_the_queue_and_every_feed_are_exactly_as_before(env):
    client, s, cat = gate_client(env)
    ids = stocky(cat, 15)
    boxes = [approved(s, ids[n * 3:n * 3 + 3], f"Box {k}") for n, k in enumerate(("B2", "B3", "A1", "C1", "D1"))]

    def look():
        taskboxes = client.get("/api/taskboxes?pending=true").json()
        for tb in taskboxes["boxes"]:
            assert "sheet" not in tb["builder"]                       # the open feeds never carry the supporters' check detail
        state = client.get("/api/state").json()
        for b in state["boxes"]:
            assert b.pop("sheet")["state"] == "off"
        auto = client.get("/api/automations").json()
        return {"queue": client.get("/api/queue").json(), "feed": taskboxes, "approved": [{**t, "builder": {k: v for k, v in t["builder"].items() if k != "sheet"}}
                                                                                          for t in client.get("/api/sheet-boxes").json()["boxes"]],
                "boxes": state["boxes"], "used": state["used"], "stats": state["stats"], "todo": auto["todo"], "alerts": auto["alerts"]}

    before = look()
    assert {b["box_id"] for b in before["queue"]["ready"]} == set(boxes) and before["queue"]["blocked"] == []
    clear_post(client, chk(boxes[0], rows=[(ids[0], 1)]),                                                    # a clean report with removals
         chk(boxes[1], rows=[(ids[3], 1), ("000999", 1)]),                                             # a difference
         chk(boxes[2], rows=[]),                                                                       # nothing left
         chk(boxes[3], sc=False, sup=None), chk(boxes[4], sc=True, sup=False))
    assert look() == before
    assert used_rows(s) == {} and conn_of(s).execute("SELECT COUNT(*) FROM audit_log WHERE action='sheet_apply'").fetchone()[0] == 0
    assert not [t for t in client.get("/api/automations").json()["todo"] if t["kind"] == "supporter"]


def test_every_reason_a_box_is_held_back_and_the_one_state_that_lets_it_through(env):
    client, s, cat = gate_client(env)
    assert client.put("/api/automations/sm_queue", json={"enabled": True}).status_code == 200
    ids = stocky(cat, 30)
    def new(n, label):
        return approved(s, ids[n * 3:n * 3 + 3], label), ids[n * 3:n * 3 + 3]
    nothing, _ = new(0, "Box B2")
    sc_off, a = new(1, "Box B3")
    sc_unread, b = new(2, "Box A1")
    sup_off, c = new(3, "Box C1")
    sup_unread, d = new(4, "Box D1")
    extra, e = new(5, "Box E1")
    up, f = new(6, "Box F1")
    empty, g = new(7, "Box G1")
    clean, h = new(8, "Box H1")
    cut, k = new(9, "Box J1")
    switch(client)
    clear_post(client, chk(sc_off, sc=False, sup=True, rows=[(i, 1) for i in a]), chk(sc_unread, sc=None, sup=True, rows=[(i, 1) for i in b]),
         chk(sup_off, sc=True, sup=False, rows=[(i, 1) for i in c]), chk(sup_unread, sc=True, sup=None, rows=[(i, 1) for i in d]),
         chk(extra, rows=[(e[0], 1), ("000999", 1), ("000998", 1), (e[1], 1), (e[2], 1)]), chk(up, rows=[(f[0], 1), (f[1], 2), (f[2], 1)]),
         chk(empty, rows=[]), chk(clean, rows=[(i, 1) for i in h]), chk(cut, rows=[(k[0], 1), (k[2], 1)]))
    got = reasons(client)
    assert got[nothing] == ["chờ SC tick"] and got[sc_off] == ["chờ SC tick"] and got[sc_unread] == ["chờ SC tick"]
    assert got[sup_off] == ["chờ người hỗ trợ kiểm"] and got[sup_unread] == ["chờ người hỗ trợ kiểm"]
    assert got[extra] == ["bảng tính khác hộp: thừa món 000998, 000999"]
    assert got[up] == [f"bảng tính khác hộp: tăng số lượng {f[1]}"]
    assert got[empty] == ["người hỗ trợ báo thiếu hết món"]
    assert clean not in got and cut not in got
    assert ready_ids(client) == {clean, cut}                                                           # both ticks, matches the box, applied
    assert [(x["object_id"], x["quantity"]) for x in next(t for t in client.get("/api/taskboxes?pending=true").json()["boxes"] if t["builder"]["box_id"] == cut)["contents"]] \
        == [(k[0], 1), (k[2], 1)]                                                                      # the push carries the supporters' list
    assert {t["builder"]["box_id"] for t in client.get("/api/taskboxes?pending=true").json()["boxes"]} == {clean, cut}
    states = {box: sheet_of(client, box)["state"] for box in (nothing, sc_off, sc_unread, sup_off, sup_unread, extra, up, empty, clean, cut)}
    assert states == {nothing: "wait_sc", sc_off: "wait_sc", sc_unread: "wait_sc", sup_off: "wait_support", sup_unread: "wait_support", extra: "diff",
                      up: "diff", empty: "missing", clean: "ok", cut: "ok"}
    assert items_of(s, empty) == [(i, 1) for i in g] and items_of(s, extra) == [(i, 1) for i in e]    # the blocked boxes were not changed


def test_a_clean_report_not_yet_applied_is_held_back_until_it_is(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:4])
    switch(client)
    conn_of(s).execute('INSERT INTO sheet_checks(box_id, sc_tick, supporter_tick, support_seen_unticked, "rows", read_at) VALUES(?,?,?,?,?,?)',
                       (box, 1, 1, 1, json.dumps([{"id": ids[0], "qty": 1}, {"id": ids[1], "qty": 1}]), time.time()))        # a report that arrived some other way
    assert reasons(client)[box] == ["chờ cập nhật hộp theo kết quả kiểm"] and ready_ids(client) == set()
    assert sheet_of(client, box)["state"] == "ok"
    assert service.settle_sheet_checks(conn_of(s)) == 1
    assert ready_ids(client) == {box} and items_of(s, box) == [(ids[0], 1), (ids[1], 1)]
    assert service.settle_sheet_checks(conn_of(s)) == 0


def test_a_lowered_quantity_is_applied_and_a_raised_one_is_held(env):
    client, s, cat = gate_client(env)
    ids = stocky(cat, 6)
    low, high = approved(s, ids[:3], "Box B2"), approved(s, ids[3:6], "Box B3")
    c = conn_of(s)
    c.execute("UPDATE box_items SET qty=3 WHERE box_id=? AND object_id=?", (low, ids[0]))
    c.execute("UPDATE box_items SET qty=2 WHERE box_id=? AND object_id=?", (high, ids[3]))
    switch(client)
    clear_post(client, chk(low, rows=[(ids[0], 1), (ids[1], 1), (ids[2], 1)]), chk(high, rows=[(ids[3], 3), (ids[4], 1), (ids[5], 1)]))
    assert items_of(s, low) == [(ids[0], 1), (ids[1], 1), (ids[2], 1)] and used_rows(s) == {} and ready_ids(client) == {low}
    assert items_of(s, high) == [(ids[3], 2), (ids[4], 1), (ids[5], 1)] and reasons(client)[high] == [f"bảng tính khác hộp: tăng số lượng {ids[3]}"]
    assert sheet_of(client, low)["missing"] == 0


def test_the_other_checks_of_the_queue_still_apply_on_top_of_the_gate(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    held, free = approved(s, ids[:3], "Box B2"), approved(s, ids[3:6], "Box B3")
    snapshot(client, sm_box("OLD", "Someone else's box", [ids[0]], created_at=iso(1)))                 # SM already holds one object of the first box
    switch(client)
    clear_post(client, chk(held, rows=[(i, 1) for i in ids[:3]]), chk(free, rows=[(i, 1) for i in ids[3:6]]))
    r = reasons(client)
    assert list(r) == [held] and len(r[held]) == 1 and r[held][0].endswith(f"{ids[0]} đã nằm trong hộp SM Someone else's box") and ready_ids(client) == {free}     # a cleared box is still stopped by the old check (the object's name now stands in front of its ID)
    post(client, chk(held, sc=False, rows=[(i, 1) for i in ids[:3]]))
    rs = reasons(client)[held]
    assert len(rs) == 2 and rs[0].endswith(f"{ids[0]} đã nằm trong hộp SM Someone else's box") and rs[1] == "chờ SC tick"      # both are said, the real problem first


def test_a_box_already_in_sm_is_not_gated_and_not_listed(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    waiting, pushed = approved(s, ids[:3], "Box B2"), approved(s, ids[3:6], "Box B3")
    service.mark_pushed(conn_of(s), pushed, "SM-1")
    switch(client)
    q = client.get("/api/queue").json()
    assert [b["box_id"] for b in q["blocked"]] == [waiting] and q["ready"] == []
    assert sheet_of(client, pushed)["state"] == "off" and sheet_of(client, waiting)["state"] == "wait_sc"


def test_the_feed_for_the_sm_tool_and_the_fake_push_obey_the_gate(env):
    s, cat = env
    s.sm_push_mode = "mock"
    client, s, cat = gate_client(env)
    assert client.put("/api/automations/sm_queue", json={"enabled": True}).status_code == 200
    ids = usable_ids(cat)
    cleared, waiting = approved(s, ids[:3], "Box B2"), approved(s, ids[3:6], "Box B3")
    switch(client)
    clear_post(client, chk(cleared, rows=[(i, 1) for i in ids[:3]]))
    feed = client.get("/api/taskboxes?pending=true").json()
    assert [t["builder"]["box_id"] for t in feed["boxes"]] == [cleared]
    assert {t["builder"]["box_id"] for t in client.get("/api/taskboxes").json()["boxes"]} == {cleared, waiting}      # the plain feed is not the queue (as before)
    out = client.post("/api/sm/push", json={}).json()
    assert out["created"] == 1 and {r["box_id"]: r["ok"] for r in out["results"]} == {cleared: True, waiting: False}
    assert [r for r in out["results"] if r["box_id"] == waiting][0]["code"] == "blocked" and "chờ SC tick" in out["results"][-1]["error"]
    switch(client, False)
    assert ready_ids(client) == {waiting}                                                              # switched off: back to the checks it had before


def test_the_todo_says_how_many_boxes_still_wait_for_the_supporters_and_only_with_the_switch_on(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    a, b, c, d = (approved(s, ids[n * 3:n * 3 + 3], f"Box {k}") for n, k in enumerate(("B2", "B3", "A1", "C1")))
    todo = lambda: {t["kind"]: t for t in client.get("/api/automations").json()["todo"]}
    assert "supporter" not in todo()                                                                   # switch off
    switch(client)
    t = todo()["supporter"]
    assert t["msg"].startswith("4 hộp đã duyệt chưa được người hỗ trợ kiểm") and t["where"] == "Bảng tính" and t["level"] == "info"
    assert "blocked" not in todo()                                                                     # a box that only waits for a tick is not "stuck"
    clear_post(client, chk(a, rows=[(i, 1) for i in ids[:3]]), chk(b, rows=[(ids[3], 1), ("000999", 1)]), chk(c, sc=True, sup=None))
    t = todo()
    assert t["supporter"]["msg"].startswith("2 hộp đã duyệt chưa được người hỗ trợ kiểm")             # c waits for the supporters, d for SC
    assert t["blocked"]["msg"].startswith("1 hộp đã duyệt bị chặn") and "bảng tính khác hộp: thừa món 000999" in t["blocked"]["msg"]
    clear_post(client, chk(c, rows=[(i, 1) for i in ids[6:9]]), chk(d, rows=[(i, 1) for i in ids[9:12]]), chk(b, rows=[(i, 1) for i in ids[3:6]]))
    assert "supporter" not in todo() and "blocked" not in todo()                                       # nothing waits: no line at all


# ----------------------------------------------------------------------------- the supporters' tick counts only after the app saw it off
def flag_of(s, box_id):
    r = conn_of(s).execute("SELECT supporter_tick, support_seen_unticked FROM sheet_checks WHERE box_id=?", (box_id,)).fetchone()
    return None if r is None else (r[0], r[1])


STALE = "tick có từ trước, chờ người hỗ trợ bỏ tick và tick lại"


def test_a_tick_that_was_already_on_the_first_time_is_not_trusted_until_it_was_seen_off_and_on_again(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:4])
    switch(client)
    rows = [(ids[0], 1), (ids[1], 1)]
    post(client, chk(box, sc=True, sup=True, rows=rows))                                               # left over from an earlier day's box in the same block
    assert flag_of(s, box) == (1, 0)
    assert sheet_of(client, box)["state"] == "wait_support" and reasons(client)[box] == [STALE] and ready_ids(client) == set()
    assert items_of(s, box) == [(i, 1) for i in ids[:4]] and used_rows(s) == {}                        # nothing was applied on the strength of it
    post(client, chk(box, sc=True, sup=True, rows=rows))                                               # the same stale tick again, any number of times
    post(client, chk(box, sc=True, sup=True, rows=rows))
    assert flag_of(s, box) == (1, 0) and reasons(client)[box] == [STALE] and items_of(s, box) == [(i, 1) for i in ids[:4]]
    post(client, chk(box, sc=True, sup=False, rows=rows))                                              # the box was filled and the tick shows off
    assert flag_of(s, box) == (0, 1)
    assert sheet_of(client, box)["state"] == "wait_support" and reasons(client)[box] == ["chờ người hỗ trợ kiểm"]     # now it waits for a real tick
    assert items_of(s, box) == [(i, 1) for i in ids[:4]]
    post(client, chk(box, sc=True, sup=True, rows=rows))                                               # the supporters tick it
    assert flag_of(s, box) == (1, 1)
    assert sheet_of(client, box)["state"] == "ok" and ready_ids(client) == {box} and reasons(client) == {}
    assert items_of(s, box) == [(i, 1) for i in ids[:2]] and set(used_rows(s)) == {ids[2], ids[3]}    # and only now is the list applied


def test_an_unreadable_tick_counts_as_seen_off_and_so_does_any_report_that_is_not_true(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    a, b, c = approved(s, ids[:3], "Box B2"), approved(s, ids[3:6], "Box B3"), approved(s, ids[6:9], "Box A1")
    post(client, chk(a, sup=None), chk(b, sup=False), chk(c, sup=True))
    assert (flag_of(s, a), flag_of(s, b), flag_of(s, c)) == ((None, 1), (0, 1), (1, 0))
    post(client, chk(a, sup=True), chk(b, sup=True), chk(c, sup=True))
    assert (flag_of(s, a), flag_of(s, b), flag_of(s, c)) == ((1, 1), (1, 1), (1, 0))


def test_the_flag_survives_later_reports_and_a_repeat_of_the_same_report_changes_nothing(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:3])
    post(client, chk(box, sup=False))
    for _ in range(3):                                                                                 # the same report again and again
        post(client, chk(box, sup=True, rows=[(i, 1) for i in ids[:3]]))
        assert flag_of(s, box) == (1, 1)
    post(client, chk(box, sup=True, sc=False, rows=[]))                                                # a later report with other content
    assert flag_of(s, box) == (1, 1)
    post(client, chk(box, sup=False))                                                                  # off again, then on: still seen
    post(client, chk(box, sup=True))
    assert flag_of(s, box) == (1, 1)
    assert conn_of(s).execute("SELECT COUNT(*) FROM sheet_checks").fetchone()[0] == 1


def test_the_flag_is_kept_while_the_switch_is_off_so_turning_it_on_uses_what_was_seen(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    fresh, stale = approved(s, ids[:4], "Box B2"), approved(s, ids[4:8], "Box B3")
    post(client, chk(fresh, sup=False, rows=[(ids[0], 1)]), chk(stale, sup=True, rows=[(ids[4], 1)]))   # the switch is off: the reports are only recorded
    post(client, chk(fresh, sup=True, rows=[(ids[0], 1)]), chk(stale, sup=True, rows=[(ids[4], 1)]))
    switch(client)
    assert items_of(s, fresh) == [(ids[0], 1)] and items_of(s, stale) == [(i, 1) for i in ids[4:8]]    # the one seen off is applied, the stale one is not
    assert reasons(client) == {stale: [STALE]} and ready_ids(client) == {fresh}
    assert service.settle_sheet_checks(conn_of(s)) == 0


def test_the_stale_tick_rule_is_for_the_supporters_only_the_scs_tick_may_come_first(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box = approved(s, ids[:3])
    switch(client)
    post(client, chk(box, sc=True, sup=False, rows=[(i, 1) for i in ids[:3]]))                         # SC ticked before the box was filled: fine
    assert reasons(client)[box] == ["chờ người hỗ trợ kiểm"]
    post(client, chk(box, sc=True, sup=True, rows=[(i, 1) for i in ids[:3]]))
    assert ready_ids(client) == {box}
    other = approved(s, ids[3:6], "Box B3")
    post(client, chk(other, sc=True, sup=True))                                                        # stale supporters' tick and SC ticked
    assert reasons(client)[other] == [STALE]
    post(client, chk(other, sc=False, sup=True))                                                       # SC not ticked: that is said first
    assert reasons(client)[other] == ["chờ SC tick"]


def test_a_stale_tick_box_is_counted_as_waiting_not_as_stuck_in_the_todo(env):
    client, s, cat = gate_client(env)
    box = approved(s, usable_ids(cat)[:3])
    switch(client)
    post(client, chk(box, sup=True, rows=[(i, 1) for i in usable_ids(cat)[:3]]))
    todo = {t["kind"]: t for t in client.get("/api/automations").json()["todo"]}
    assert todo["supporter"]["msg"].startswith("1 hộp đã duyệt chưa được người hỗ trợ kiểm") and "blocked" not in todo
    assert STALE in service.SHEET_WAIT_REASONS


def test_the_flag_column_is_in_the_table_on_both_engines(env):
    s, cat = env
    cols = {r["name"]: r for r in conn_of(s).execute("PRAGMA table_info(sheet_checks)").fetchall()} if not db.is_postgres(s.db_path) else {}
    if cols:
        assert cols["support_seen_unticked"]["notnull"] == 1 and cols["support_seen_unticked"]["dflt_value"] == "0" and "INT" in cols["support_seen_unticked"]["type"].upper()
    for pg in (False, True):                                                                            # the Postgres statement is checked as text: it is not run here
        ddl = next(x for x in db._schema(pg) if "CREATE TABLE IF NOT EXISTS sheet_checks" in x)
        flat = " ".join(ddl.split())
        assert "support_seen_unticked INTEGER NOT NULL DEFAULT 0" in flat
        assert "box_id BIGINT PRIMARY KEY REFERENCES boxes(id) ON DELETE CASCADE" in flat and '"rows" TEXT NOT NULL DEFAULT' in flat
        assert ("read_at DOUBLE PRECISION NOT NULL" if pg else "read_at REAL NOT NULL") in flat
    if db.is_postgres(s.db_path):
        c = conn_of(s)
        assert c.execute("SELECT 1 FROM information_schema.columns WHERE table_name='sheet_checks' AND column_name='support_seen_unticked'").fetchone()


# ----------------------------------------------------------------------------- the `sheet` part as the screens get it
def test_every_box_carries_the_sheet_part_in_the_state_the_user_page_and_the_task_boxes(env):
    client, s, cat = gate_client(env)
    ids = usable_ids(cat)
    box, draft = approved(s, ids[:3], "Box B2"), box_with(s, ids[3:6], label="Box B3", module="M7", status="draft")
    off = {"state": "off", "missing": 0, "diff": [], "read_at": None}
    def everywhere():
        mine = {b["id"]: b for b in client.get("/api/my/summary").json()["boxes"]}                      # login is off, so this page shows every box
        feed = {t["builder"]["box_id"]: t["builder"] for t in client.get("/api/taskboxes").json()["boxes"]}
        return boxes_of(client)[box]["sheet"], mine[box]["sheet"], feed[box]["sheet"], boxes_of(client)[draft]["sheet"], mine[draft]["sheet"]
    assert everywhere() == (off,) * 5                                                                  # switch off
    switch(client)
    wait = {"state": "wait_sc", "missing": 0, "diff": [], "read_at": None}
    assert everywhere() == (wait, wait, wait, off, off)                                                # a draft is not gated
    t0 = time.time()
    clear_post(client, chk(box, rows=[(ids[0], 1), (ids[1], 1), ("000999", 1)]))
    got = everywhere()
    assert got[0] == got[1] == got[2] and got[3] == off and got[4] == off
    assert set(got[0]) == SHEET_KEYS and got[0]["state"] == "diff" and got[0]["diff"] == [{"id": "000999", "why": "extra"}]
    assert got[0]["missing"] == 1 and isinstance(got[0]["read_at"], float) and t0 - 1 <= got[0]["read_at"] <= time.time() + 1
    assert [set(x) for x in got[0]["diff"]] == [{"id", "why"}]
    post(client, chk(box, rows=[(ids[0], 1), (ids[1], 1)]))
    got = everywhere()
    assert got[0] == got[1] == got[2] and (got[0]["state"], got[0]["missing"], got[0]["diff"]) == ("ok", 1, [])
    for old in ("id", "module", "label", "theme", "code", "anchor", "title", "categories", "count", "status", "message", "owner", "updated_at", "items", "pushed", "day"):
        assert old in boxes_of(client)[box]                                                            # the keys that were there stay
    for old in ("id", "module", "label", "code", "anchor", "theme", "title", "status", "status_text", "message", "count", "pushed", "updated_at", "day", "items"):
        assert old in client.get("/api/my/summary").json()["boxes"][0]
    for old in ("box_id", "module", "label", "status", "pushed", "day"):
        assert old in client.get("/api/taskboxes").json()["boxes"][0]["builder"]


def test_the_user_page_shows_a_person_the_sheet_part_of_their_own_boxes_only(env):
    admin, s, cat = app_client(env)
    s.sheet_token = "sheet-tok"
    sign_in(admin, "1001")
    admin.post("/api/admin/people", json={"text": "2002, Hải, M2\n3003, Hưng, M3"}, headers=SAME)
    ids = usable_ids(cat)
    mine, theirs = approved(s, ids[:3], "Box B2"), approved(s, ids[3:6], "Box B3")
    conn_of(s).execute("UPDATE boxes SET owner='2002' WHERE id=?", (mine,))
    conn_of(s).execute("UPDATE boxes SET owner='3003' WHERE id=?", (theirs,))
    admin.put("/api/automations/need_supporter_check", json={"enabled": True}, headers=SAME)
    script = TestClient(admin.app)                                                                    # the sheet's script: no session
    script.post("/api/sheet/checks", json={"checks": [chk(mine, sup=False, rows=[(ids[0], 1)])]}, headers=TOK)
    script.post("/api/sheet/checks", json={"checks": [chk(mine, rows=[(ids[0], 1)])]}, headers=TOK)
    hai = second_browser(admin, "2002")
    boxes = hai.get("/api/my/summary").json()["boxes"]
    assert [b["id"] for b in boxes] == [mine] and boxes[0]["sheet"]["state"] == "ok" and boxes[0]["sheet"]["missing"] == 2
    assert "3003" not in json.dumps(boxes) and f'"{ids[3]}"' not in json.dumps(boxes)
    assert second_browser(admin, "3003").get("/api/my/summary").json()["boxes"][0]["sheet"]["state"] == "wait_sc"
    assert hai.put("/api/automations/need_supporter_check", json={"enabled": False}, headers=SAME).status_code == 403          # the switch is the admin's


def test_the_state_reads_the_switches_once_with_the_gate_on_too(env, monkeypatch):
    client, s, cat = gate_client(env)
    box = approved(s, usable_ids(cat)[:3])
    switch(client)
    post(client, chk(box))
    seen = []
    real = db.Conn.execute
    monkeypatch.setattr(db.Conn, "execute", lambda self, sql, params=(): (seen.append(sql), real(self, sql, params))[1])
    assert client.get("/api/state?rev=-1").status_code == 200
    assert sum("FROM automations" in q for q in seen) == 1 and sum("FROM sheet_checks" in q for q in seen) == 1


def test_the_new_text_follows_the_writing_rules():
    texts = [automation.META["need_supporter_check"]["title"], automation.META["need_supporter_check"]["desc"], service.SHEET_NOTE, *service.SHEET_WAIT_REASONS]
    for t in texts:
        assert "—" not in t and "!" not in t and not any(w in t.lower() for w in ("toàn diện", "mạnh mẽ", "vui lòng", "hãy"))

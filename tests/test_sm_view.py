"""What SM holds, made visible: object status, ledger count, report of conflicts and double-booked objects, alerts, to-do."""
import time

from app import images, service
from app.catalog import Catalog, display_name
from tests.test_automation import live_rows, make_client, sync
from tests.test_backend import _approved_box, conn_of, env  # noqa: F401  (env is a fixture)
from tests.test_sm_boxes import TOK, push_snapshot, sm_box, usable_ids


def box_ids(client, bid):
    return [i["id"] for b in client.get("/api/state").json()["boxes"] if b["id"] == bid for i in b["items"]]


def test_held_objects_show_as_a_status_in_the_object_list_the_ledger_and_the_csv(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    push_snapshot(client, [sm_box("SM-1", "Tabletop 1", ids[:5])])
    held = client.get("/api/objects?status=sm&per=50").json()
    assert held["total"] == 5 and {o["id"] for o in held["items"]} == set(ids[:5])
    assert all(o["status"] == "sm" and o["holder"] == "hộp SM Tabletop 1" for o in held["items"])
    assert held["counts"]["sm"] == 5 and client.get("/api/objects?status=free").json()["total"] == len(ids) - 5
    assert client.get("/api/objects?status=bogus").status_code == 422
    st = client.get("/api/state").json()["stats"]
    assert st["in_sm"] == 5 and st["free"] == len(ids) - 5
    cov = client.get("/api/objects/coverage").json()
    assert sum(c["used"] for c in cov) == 5 and all(c["free"] + c["used"] == c["total"] for c in cov)
    csv_rows = client.get("/api/objects.csv?status=sm").content.decode("utf-8-sig").splitlines()
    assert len(csv_rows) == 6 and all(",sm," in r for r in csv_rows[1:])


def test_report_lists_conflicts_and_doubles_and_leaves_our_pushed_boxes_out_of_conflicts(env):
    client, s, cat = make_client(env)
    mine = _approved_box(client, "Box A1")
    pushed = _approved_box(client, "Box B1")
    client.post(f"/api/boxes/{pushed}/pushed", json={"sm_box_id": "SM-10"}, headers=TOK)
    mine_ids, pushed_ids = box_ids(client, mine), box_ids(client, pushed)
    other = usable_ids(cat)[-3:]
    rep0 = client.get("/api/sm/boxes/report").json()
    assert rep0 == {"synced_at": None, "boxes": 0, "objects_held": 0, "doubles": [], "conflicts": [],
                    "reuse": {"on": True, "days": 30, "old_boxes": 0, "undated_boxes": 0, "freed_objects": 0}}
    clash = mine_ids[0]
    push_snapshot(client, [sm_box("SM-10", "Ours", pushed_ids), sm_box("SM-20", "Tabletop 20", [clash, other[0]]),
                           sm_box("SM-30", "Tabletop 30", [other[0], other[1]])])
    rep = client.get("/api/sm/boxes/report").json()
    assert rep["boxes"] == 3 and rep["synced_at"] and rep["objects_held"] == 3                    # our own box is not counted as held
    assert [c["box"] for c in rep["conflicts"]] == ["M8 / Box A1"]                                  # the pushed box is never a conflict
    assert rep["conflicts"][0]["objects"][0]["id"] == clash and rep["conflicts"][0]["objects"][0]["sm_box"] == "Tabletop 20"
    assert [d["id"] for d in rep["doubles"]] == [other[0]] and rep["doubles"][0]["boxes"] == ["Tabletop 20", "Tabletop 30"]
    # a double that involves our own pushed box is still a double
    push_snapshot(client, [sm_box("SM-10", "Ours", pushed_ids), sm_box("SM-40", "Someone's copy", pushed_ids[:1])])
    assert [d["id"] for d in client.get("/api/sm/boxes/report").json()["doubles"]] == pushed_ids[:1]
    assert client.get("/api/sm/boxes/report").json()["conflicts"] == []


def test_alerts_and_to_do_follow_the_snapshot(env):
    client, s, cat = make_client(env)
    mine = _approved_box(client, "Box A1")
    mine_ids = box_ids(client, mine)
    kinds = lambda key: {x["kind"] for x in client.get("/api/automations").json()[key]}
    assert "sm_boxes" in kinds("todo")                                                              # never read: a gentle reminder
    todo = next(t for t in client.get("/api/automations").json()["todo"] if t["kind"] == "sm_boxes")
    assert todo["level"] == "info" and todo["where"] == "SM"
    push_snapshot(client, [sm_box("SM-5", "Tabletop 5", [mine_ids[0]])])
    assert "sm_boxes" not in kinds("todo")                                                          # fresh
    assert {"sm_conflict"} <= kinds("alerts") and {"sm_conflict"} <= kinds("todo")
    alert = next(a for a in client.get("/api/automations").json()["alerts"] if a["kind"] == "sm_conflict")
    assert alert["level"] == "bad" and mine_ids[0] in alert["msg"] and "M8 / Box A1" in alert["msg"]
    todo = next(t for t in client.get("/api/automations").json()["todo"] if t["kind"] == "sm_conflict")
    assert todo["level"] == "bad" and todo["where"] == "Hộp"
    client.put("/api/automations/catalog_watch", json={"enabled": False})
    assert "sm_conflict" not in kinds("alerts")                                                     # the watch switch silences alerts
    client.put("/api/automations/catalog_watch", json={"enabled": True, "config": {"stale_hours": 1}})
    service.save_sm_boxes(conn_of(s), [sm_box("SM-5", "Tabletop 5", [mine_ids[0]])], time.time() - 7200)   # last read two hours ago
    old = next(t for t in client.get("/api/automations").json()["todo"] if t["kind"] == "sm_boxes")
    assert old["level"] == "warn" and "đã cũ" in old["msg"]
    push_snapshot(client, [sm_box("SM-5", "Tabletop 5", [mine_ids[0]], "returned"), sm_box("SM-6", "Other", usable_ids(cat)[-1:])])
    assert "sm_conflict" not in kinds("alerts") | kinds("todo")                                     # returned: the conflict is gone


# ----------------------------------------------------------------------------- the shared-items view: names, picture, units against units that exist
def live_with(client, cat, changes):
    """Sync the kho again with some rows changed: id -> dict of fields (qty, avail, name, vi, loc, image_url ...)."""
    rows = live_rows(cat)
    for r in rows:
        r.update(changes.get(r["id"], {}))
    sync(client, rows)


def test_each_shared_object_carries_its_names_slot_picture_link_and_the_units_held_against_the_units_that_exist(env):
    client, s, cat = make_client(env)
    short, full, spare = usable_ids(cat)[:3]
    live_with(client, cat, {short: {"name": "Wine glass", "vi": "Ly rượu", "loc": "AM04", "qty": 2, "avail": 0, "image_url": "https://sm.config.inc/media/a.png"},
                            full: {"qty": 3, "avail": 1}, spare: {"qty": 5, "avail": 3}})
    push_snapshot(client, [sm_box("A", "Tabletop A", [short, full, spare], qty=2), sm_box("B", "Tabletop B", [short, full, spare], qty=1, status="floor")])
    rep = client.get("/api/sm/boxes/report").json()
    assert set(rep) == {"synced_at", "boxes", "objects_held", "doubles", "conflicts", "reuse"}              # the top level is what it always was
    by_id = {d["id"]: d for d in rep["doubles"]}
    d = by_id[short]
    assert (d["en"], d["vi"], d["name"], d["slot"]) == ("Wine glass", "Ly rượu", "Ly rượu", "AM04")
    assert d["img"] == "https://sm.config.inc/media/a.png" and d["detail"] == f"https://sm.config.inc/object_detail/{short}" and d["img_exp"] is None
    assert (d["units"], d["total"], d["free"]) == (3, 2, 0)                                              # 2 + 1 held, 2 exist: short by one
    assert d["boxes"] == ["Tabletop A", "Tabletop B"]                                                    # still plain titles
    assert [(b["title"], b["status"], b["units"]) for b in d["holders"]] == [("Tabletop A", "Assigned", 2), ("Tabletop B", "Floor", 1)]
    assert all(len(b["day"]) == 10 and b["day"][4] == "-" for b in d["holders"])                         # a day, in Vietnam time
    assert (by_id[full]["units"], by_id[full]["total"]) == (3, 3) and (by_id[spare]["units"], by_id[spare]["total"], by_id[spare]["free"]) == (3, 5, 3)
    assert next(i for i in cat.items().values() if i.id == full).stock == 1                              # the stock the picker sees is the free part; the view uses what exists


def test_an_object_with_no_stock_left_is_still_named_from_the_live_rows(env):
    client, s, cat = make_client(env)
    gone, other = usable_ids(cat)[:2]
    live_with(client, cat, {gone: {"name": "Gravy boat", "vi": "Thuyền nước sốt", "qty": 2, "avail": 0}})
    assert gone not in cat.items()                                                                       # every unit is borrowed out: not in the usable kho
    push_snapshot(client, [sm_box("A", "One", [gone, other]), sm_box("B", "Two", [gone, other])])
    d = next(x for x in client.get("/api/sm/boxes/report").json()["doubles"] if x["id"] == gone)
    assert (d["en"], d["vi"], d["name"], d["units"], d["total"], d["free"]) == ("Gravy boat", "Thuyền nước sốt", "Thuyền nước sốt", 2, 2, 0)
    assert cat.lookup(gone).qty == 2 and cat.lookup(gone).avail == 0


def test_when_sm_never_said_how_many_exist_the_report_leaves_total_and_free_out(env):
    client, s, cat = make_client(env)
    a, b, c = usable_ids(cat)[:3]
    push_snapshot(client, [sm_box("A", "One", [a, "999999"], qty=2), sm_box("B", "Two", [a, "999999"])])             # kho from the CSV; 999999 is in no list
    rep = client.get("/api/sm/boxes/report").json()
    for d in rep["doubles"]:
        assert d["units"] == 3 and "total" not in d and "free" not in d
    unknown = next(d for d in rep["doubles"] if d["id"] == "999999")
    assert (unknown["name"], unknown["en"], unknown["vi"], unknown["img"], unknown["img_exp"]) == ("", "", "", "", None)
    live_with(client, cat, {b: {"qty": None, "avail": 1}, c: {"qty": "a lot", "avail": None}})
    push_snapshot(client, [sm_box("A", "One", [b, c]), sm_box("B", "Two", [b, c])])
    assert all("total" not in d and "free" not in d and d["units"] == 2 for d in client.get("/api/sm/boxes/report").json()["doubles"])


def test_units_leave_out_old_boxes_and_conflicts_count_only_the_boxes_that_hold(env):
    client, s, cat = make_client(env)
    mine = _approved_box(client, "Box A1")
    ours = _approved_box(client, "Box B1")
    client.post(f"/api/boxes/{ours}/pushed", json={"sm_box_id": "SM-10"}, headers=TOK)
    clash, shared, other = box_ids(client, mine)[0], box_ids(client, ours)[0], usable_ids(cat)[-1]
    live_with(client, cat, {clash: {"qty": 4, "avail": 4}, shared: {"qty": 6, "avail": 6}, other: {"qty": 3, "avail": 3}})
    old = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 90 * 86400))
    push_snapshot(client, [sm_box("SM-10", "Ours", [clash, shared], qty=2), sm_box("SM-20", "Theirs", [clash, shared, other], qty=1),
                           sm_box("SM-30", "Old one", [other], qty=2, created_at=old), sm_box("SM-40", "Also theirs", [other], qty=1)])
    rep = client.get("/api/sm/boxes/report").json()
    doubles = {d["id"]: d for d in rep["doubles"]}
    assert set(doubles) == {clash, shared, other}
    assert doubles[shared]["units"] == 3 and [b["title"] for b in doubles[shared]["holders"]] == ["Ours", "Theirs"]      # our own pushed box holds units too
    assert doubles[other]["units"] == 2 and doubles[other]["boxes"] == ["Theirs", "Also theirs"]                           # the 90 day old box holds nothing
    objs = {o["id"]: o for o in rep["conflicts"][0]["objects"]}
    assert set(objs) == {clash}
    assert (objs[clash]["units"], objs[clash]["total"], objs[clash]["sheet"], objs[clash]["sm_box"]) == (1, 4, False, "Theirs")
    assert [b["title"] for b in objs[clash]["holders"]] == ["Theirs"] and doubles[clash]["units"] == 3                      # a conflict counts only boxes that are not ours
    listed = box_ids(client, mine)[1]
    conn_of(s).execute("INSERT INTO external_used(object_id, note) VALUES(?, 'sheet')", (listed,))                          # the sheet list got it after the box was made
    sheet_only = next(o for o in client.get("/api/sm/boxes/report").json()["conflicts"][0]["objects"] if o["id"] == listed)
    assert sheet_only["sheet"] is True and sheet_only["units"] == 0 and sheet_only["holders"] == [] and "bảng tính" in sheet_only["sm_box"]


def test_the_report_reads_no_snapshot_and_no_picture_rules_while_it_runs(env, monkeypatch):
    client, s, cat = make_client(env)
    a, b = usable_ids(cat)[:2]
    live_with(client, cat, {a: {"image_url": "https://sm.config.inc/media/a.png"}})
    push_snapshot(client, [sm_box("A", "One", [a, b]), sm_box("B", "Two", [a, b])])
    def boom(*args, **kw):
        raise AssertionError("the report went back to the raw rows")
    monkeypatch.setattr(images, "pick_image", boom)
    monkeypatch.setattr(service, "load_snapshot", boom)
    rep = client.get("/api/sm/boxes/report").json()
    assert next(d for d in rep["doubles"] if d["id"] == a)["img"].endswith("a.png")


def test_catalog_lookup_reads_every_live_row_and_falls_back_to_the_csv(env):
    s, cat = env
    csv_first = usable_ids(cat)[0]
    csv_name, csv_vi = cat.items()[csv_first].name, cat.items()[csv_first].vi
    assert cat.lookup(csv_first).en == csv_name and cat.lookup(csv_first).vi == csv_vi and cat.lookup(csv_first).qty is None      # before any sync: the CSV
    assert cat.lookup("999999") is None and cat.lookup("") is None
    rows = [{"id": "12", "name": "Cup", "vi": "", "type": "Obj", "status": "ACTIVE", "qty": 3, "avail": 0, "loc": "B07", "sector": "Hanoi-GPI"},
            {"id": csv_first, "name": "", "vi": "", "type": "Obj", "status": "INACTIVE", "qty": "x", "avail": 2, "sector": "Hanoi-GPI"},
            {"id": "13", "name": "Elsewhere", "vi": "Nơi khác", "type": "Obj", "qty": 1, "avail": 1, "sector": "Other"},
            {"id": "14", "name": "Room", "vi": "Phòng", "type": "Env", "qty": 1, "avail": 1, "sector": "Hanoi-GPI"},
            {"id": "no digits", "name": "x", "sector": "Hanoi-GPI"}]
    cat.sectors = ("Hanoi-GPI",)
    cat.set_live(rows, time.time())
    assert cat.items() == {} or "000012" not in cat.items()                                                                   # no stock: not usable
    cup = cat.lookup("000012")
    assert (cup.en, cup.slot, cup.qty, cup.avail, cup.img) == ("Cup", "B07", 3, 0, "")
    back = cat.lookup(csv_first)                                                                                              # the row has no names: the CSV supplies them
    assert (back.en, back.vi, back.qty, back.avail) == (csv_name, csv_vi, None, 2)
    assert cat.lookup("000013") is None and cat.lookup("000014") is None                                                      # other sector, not an object
    assert display_name(cup.en, cup.vi) == "Cup"


def test_sentences_name_the_object_in_front_of_its_id(env):
    client, s, cat = make_client(env)
    mine = _approved_box(client, "Box A1")
    ids = box_ids(client, mine)
    clash, gone = ids[0], ids[1]
    live_with(client, cat, {clash: {"name": "Wine glass", "vi": "Ly rượu"}, gone: {"name": "Gravy boat", "vi": "Thuyền sốt", "qty": 1, "avail": 0}})
    push_snapshot(client, [sm_box("SM-5", "Tabletop 5", [clash])])
    wine = "Wine glass (Ly rượu) " + clash
    alerts = {a["kind"]: a["msg"] for a in client.get("/api/automations").json()["alerts"]}
    assert wine in alerts["sm_conflict"] and "M8 / Box A1" in alerts["sm_conflict"] and clash in alerts["sm_conflict"]
    assert "Gravy boat (Thuyền sốt) " + gone in alerts["gone"]                                                               # out of stock, named from the live rows
    blocked = client.get("/api/queue").json()["blocked"][0]["reasons"]
    assert wine + " đã nằm trong hộp SM Tabletop 5" in blocked and "Gravy boat (Thuyền sốt) " + gone + " không còn tồn kho ở SM" in blocked
    other = client.post("/api/boxes", json={"module": "M8", "label": "Box C1", "theme": "t", "count": 3}).json()["id"]
    from tests.test_backend import wait_settled
    wait_settled(client, other)
    r = client.post(f"/api/boxes/{other}/items", json={"object_id": ids[2], "qty": 1})
    assert r.status_code == 409 and r.json()["detail"].endswith("đã nằm trong M8 / Box A1.") and ids[2] in r.json()["detail"]
    assert display_name(cat.lookup(ids[2]).en, cat.lookup(ids[2]).vi) in r.json()["detail"]

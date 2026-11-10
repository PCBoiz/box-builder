"""Automations, object browser, batch planning and the validated SM queue (mock LLM, real catalog)."""
import time

from fastapi.testclient import TestClient

from app import automation, db, picker, planner, service
from app.main import create_app
from tests.test_backend import _approved_box, conn_of, env, wait_settled  # noqa: F401  (env is a fixture)


def make_client(env):
    s, cat = env
    s.sync_token = "tok"
    return TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat)), s, cat


def wait_all_settled(client, ids, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        boxes = {b["id"]: b for b in client.get("/api/state").json()["boxes"]}
        if all(i in boxes and boxes[i]["status"] != "generating" for i in ids):
            return boxes
        time.sleep(0.1)
    raise AssertionError("batch did not finish")


# ----------------------------------------------------------------------------- switches
def test_switch_defaults_persist_and_validate(env):
    client, s, _ = make_client(env)
    sw = {x["key"]: x for x in client.get("/api/automations").json()["switches"]}
    assert set(sw) == set(automation.META)
    assert sw["auto_approve"]["enabled"] is False                  # the risky one starts off
    assert sw["sheet_export"]["enabled"] and sw["sm_queue"]["enabled"] and sw["ai_batch"]["enabled"]

    r = client.put("/api/automations/auto_approve", json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    # a brand new app instance on the same database sees the same switch (it lives in the database)
    s2 = env[0]
    again = TestClient(create_app(s2, llm=picker.MockLLM(), catalog=env[1]))
    assert {x["key"]: x["enabled"] for x in again.get("/api/automations").json()["switches"]}["auto_approve"] is True

    assert client.put("/api/automations/nope", json={"enabled": True}).status_code == 422
    bad = client.put("/api/automations/catalog_watch", json={"config": {"stale_hours": 0}})
    assert bad.status_code == 422
    assert client.put("/api/automations/catalog_watch", json={"config": {"nonsense": 5}}).status_code == 422
    assert client.put("/api/automations/catalog_watch", json={"config": {"stale_hours": True}}).status_code == 422
    ok = client.put("/api/automations/catalog_watch", json={"config": {"low_stock": 5}}).json()
    assert ok["config"] == {"stale_hours": 24, "low_stock": 5}
    kinds = [e["kind"] for e in client.get("/api/automations").json()["log"]]
    assert "switch" in kinds


# ----------------------------------------------------------------------------- object browser
def test_objects_view_status_filters_and_paging(env):
    client, s, cat = make_client(env)
    first = client.get("/api/objects?per=10").json()
    assert first["total"] == len(cat.items()) and len(first["items"]) == 10 and first["pages"] > 1
    assert first["counts"]["free"] == first["total"]
    assert all(o["status"] == "free" for o in first["items"])

    bid = _approved_box(client)
    held = {i["id"] for i in next(b for b in client.get("/api/state").json()["boxes"] if b["id"] == bid)["items"]}
    approved = client.get("/api/objects?status=approved&per=200").json()
    assert {o["id"] for o in approved["items"]} == held and approved["counts"]["approved"] == len(held)
    assert all(o["holder"] == "M8 / Box A1" for o in approved["items"])

    ext = next(o["id"] for o in client.get("/api/objects?status=free&per=5").json()["items"])
    assert client.post("/api/used", json={"text": ext, "note": "M17"}).status_code == 200
    assert client.get("/api/objects?status=external").json()["items"][0]["id"] == ext

    one = next(iter(held))
    hit = client.get(f"/api/objects?q={one}").json()
    assert [o["id"] for o in hit["items"]] == [one]
    cats = client.get("/api/objects/coverage").json()
    assert sum(c["total"] for c in cats) == len(cat.items())
    assert sum(c["used"] for c in cats) == len(held) + 1           # the box's objects plus the workbook one
    assert all(c["free"] + c["used"] == c["total"] for c in cats)
    assert client.get("/api/objects?status=bogus").status_code == 422
    assert client.get("/api/objects?sort=-stock&per=3").json()["items"][0]["stock"] >= \
        client.get("/api/objects?sort=-stock&per=3").json()["items"][2]["stock"]


def test_bulk_add_by_hand_skips_taken_objects(env):
    client, s, _ = make_client(env)
    a = client.post("/api/boxes", json={"module": "M8", "label": "Box A1", "theme": "t", "count": 5}).json()["id"]
    wait_settled(client, a)
    b = client.post("/api/boxes", json={"module": "M8", "label": "Box B1", "theme": "t", "count": 5}).json()["id"]
    wait_settled(client, b)
    assert client.delete(f"/api/boxes/{b}/items/1").status_code == 200     # make room: the box was full
    in_a = next(x for x in client.get("/api/state").json()["boxes"] if x["id"] == a)["items"][0]["id"]
    free = [o["id"] for o in client.get("/api/objects?status=free&per=3").json()["items"]]
    r = client.post(f"/api/boxes/{b}/items/bulk", json={"object_ids": [in_a, free[0], "999999", free[0]]})
    assert r.status_code == 200
    body = r.json()
    assert [x["id"] for x in body["added"]] == [free[0]]            # de-duplicated, only the free one went in
    assert {x["id"] for x in body["failed"]} == {in_a, "999999"}
    assert all_unique(s)


def all_unique(s):
    c = conn_of(s)
    ids = [r[0] for r in c.execute("SELECT object_id FROM box_items")]
    return len(ids) == len(set(ids))


# ----------------------------------------------------------------------------- AI batch
def test_plan_labels_and_line_splitting():
    assert planner.next_labels("Box A1", 3, set()) == ["Box A1", "Box B1", "Box C1"]
    assert planner.next_labels("Box A1", 3, {"Box B1"}) == ["Box A1", "Box C1", "Box D1"]
    assert planner.next_labels("Hộp", 2, set()) == ["Hộp", "Hộp 2"]
    assert len(planner.next_labels("Box Y1", 5, set())) == 2        # stops at Z instead of inventing letters

    class Mock:
        name = "mock"
    p = planner.plan(Mock(), "bếp\n- vườn\n3. thú cưng", "M8", "Box A1", 9, 21, set())
    assert p["planner"] == "lines" and [b["theme"] for b in p["boxes"]] == ["bếp", "vườn", "thú cưng"]


def test_plan_uses_model_json_and_survives_garbage():
    class Good:
        name = "deepseek"
        def complete(self, system, user, ctx):
            return 'Sure!\n["bàn bếp", "góc vườn"]'

    class Bad(Good):
        def complete(self, system, user, ctx):
            raise RuntimeError("down")

    class Junk(Good):
        def complete(self, system, user, ctx):
            return "I cannot do that"

    g = planner.plan(Good(), "nhiều cảnh", "M8", "Box A1", 3, 21, set())
    assert g["planner"] == "deepseek" and [b["theme"] for b in g["boxes"]] == ["bàn bếp", "góc vườn", "góc vườn"]
    for engine in (Bad(), Junk()):
        p = planner.plan(engine, "bếp\nvườn", "M8", "Box A1", 2, 21, set())
        assert p["planner"] == "lines" and len(p["boxes"]) == 2


def test_batch_builds_all_boxes_with_no_duplicates_and_is_all_or_nothing(env):
    client, s, _ = make_client(env)
    plan = client.post("/api/batches/plan", json={"brief": "bếp\nvườn\ndọn dẹp", "module": "M8", "start_label": "Box A1", "boxes": 3})
    assert plan.status_code == 200
    boxes = plan.json()["boxes"]
    assert [b["label"] for b in boxes] == ["Box A1", "Box B1", "Box C1"]
    # one bad spec -> nothing is created
    bad = client.post("/api/batches", json={"boxes": boxes + [dict(boxes[0])]})
    assert bad.status_code == 422 and client.get("/api/state").json()["boxes"] == []
    r = client.post("/api/batches", json={"boxes": boxes, "owner": "an"})
    assert r.status_code == 201
    done = wait_all_settled(client, r.json()["ids"])
    assert [len(b["items"]) for b in sorted(done.values(), key=lambda b: b["label"])] == [12, 21, 21]   # A1 12, B1 and C1 default 21
    assert all_unique(s)
    assert client.post("/api/batches", json={"boxes": [boxes[0]]}).status_code == 422     # name now exists
    log = client.get("/api/automations").json()["log"]
    assert any(e["kind"] == "batch" for e in log)

    client.put("/api/automations/ai_batch", json={"enabled": False})
    assert client.post("/api/batches/plan", json={"brief": "abc", "module": "M8"}).status_code == 409
    assert client.post("/api/batches", json={"boxes": [boxes[0]]}).status_code == 409


# ----------------------------------------------------------------------------- auto approve
def test_auto_approve_only_when_on_and_only_for_fresh_boxes(env):
    client, s, _ = make_client(env)
    a = client.post("/api/boxes", json={"module": "M8", "label": "Box A1", "theme": "t"}).json()["id"]
    assert wait_settled(client, a)["status"] == "draft"             # switch is off by default: a human approves

    client.put("/api/automations/auto_approve", json={"enabled": True})
    b = client.post("/api/boxes", json={"module": "M8", "label": "Box B1", "theme": "t"}).json()["id"]
    got = wait_settled(client, b)
    deadline = time.time() + 5
    while got["status"] != "approved" and time.time() < deadline:
        time.sleep(0.1)
        got = next(x for x in client.get("/api/state").json()["boxes"] if x["id"] == b)
    assert got["status"] == "approved" and len(got["items"]) == got["count"] == 21            # Box B1 has no stage size: the default 21
    assert any(e["kind"] == "auto_approve" for e in client.get("/api/automations").json()["log"])

    # hand edits on an existing draft are never auto-approved
    client.post(f"/api/boxes/{a}/regenerate")
    assert wait_settled(client, a)["status"] == "draft"


def test_auto_approve_waits_when_code_had_to_fill(env):
    client, s, cat = make_client(env)

    class Lazy(picker.MockLLM):
        def complete(self, system, user, ctx):
            return ""                                               # the model returns nothing usable

    app = create_app(s, llm=Lazy(), catalog=cat)
    c = TestClient(app)
    c.put("/api/automations/auto_approve", json={"enabled": True})
    bid = c.post("/api/boxes", json={"module": "M8", "label": "Box A1", "theme": "t"}).json()["id"]
    got = wait_settled(c, bid)
    assert got["status"] == "draft" and all(i["source"] == "fill" for i in got["items"])


# ----------------------------------------------------------------------------- sheet export and SM queue
def test_sheet_export_switch(env):
    client, s, _ = make_client(env)
    bid = _approved_box(client)
    assert [b["builder"]["box_id"] for b in client.get("/api/sheet-boxes").json()["boxes"]] == [bid]
    client.put("/api/automations/sheet_export", json={"enabled": False})
    r = client.get("/api/sheet-boxes").json()
    assert r["boxes"] == [] and r["disabled"] is True


def test_sm_queue_blocks_bad_boxes_and_switch_hides_everything(env):
    client, s, cat = make_client(env)
    good = _approved_box(client, "Box A1")
    bad = _approved_box(client, "Box B1")
    q = client.get("/api/queue").json()
    assert {b["box_id"] for b in q["ready"]} == {good, bad} and q["blocked"] == []

    # an object vanishes from SM's list after the box was approved -> that box must not reach the userscript
    gone = next(b for b in client.get("/api/state").json()["boxes"] if b["id"] == bad)["items"][0]["id"]
    live = [{"id": o.id, "name": o.name, "vi": o.vi, "type": "Obj", "status": "ACTIVE", "avail": o.stock, "qty": o.stock,
             "loc": o.slot, "sector": "Hanoi-GPI"} for o in cat.items().values() if o.id != gone]
    assert client.post("/api/catalog/sync", json={"objects": live}, headers={"X-Sync-Token": "tok"}).status_code == 200
    q = client.get("/api/queue").json()
    assert [b["box_id"] for b in q["ready"]] == [good]
    assert q["blocked"][0]["box_id"] == bad and gone in q["blocked"][0]["reasons"][0]
    pending = client.get("/api/taskboxes?pending=true").json()["boxes"]
    assert [b["builder"]["box_id"] for b in pending] == [good]
    alerts = client.get("/api/automations").json()["alerts"]
    assert any(a["kind"] == "gone" and gone in a["msg"] for a in alerts)

    client.put("/api/automations/sm_queue", json={"enabled": False})
    off = client.get("/api/taskboxes?pending=true").json()
    assert off["boxes"] == [] and off["disabled"] is True


def test_catalog_alerts_stale_and_low_stock(env):
    client, s, cat = make_client(env)
    a = client.get("/api/automations").json()["alerts"]
    assert any(x["kind"] == "stale" for x in a)                    # CSV only: never synced with SM
    biggest = client.get("/api/objects/coverage").json()[0]                  # sorted by size, largest first
    assert not any(x["kind"] == "low" for x in client.get("/api/automations").json()["alerts"])   # nothing taken yet
    one = client.get(f"/api/objects?category={biggest['name']}&per=1").json()["items"][0]["id"]
    client.post("/api/used", json={"text": one})
    client.put("/api/automations/catalog_watch", json={"config": {"low_stock": min(500, biggest["total"])}})
    low = [x for x in client.get("/api/automations").json()["alerts"] if x["kind"] == "low"]
    assert low and biggest["name"] in low[0]["msg"]
    client.put("/api/automations/catalog_watch", json={"enabled": False})
    assert client.get("/api/automations").json()["alerts"] == []


# ----------------------------------------------------------------------------- regression: stale page after a write mid-read
def test_state_revision_is_never_newer_than_its_data(env, monkeypatch):
    """A write that lands while state() is reading must not be hidden: the revision comes from before the read,
    so the page's next poll sees a different number and refetches."""
    s, cat = env
    make = conn_of(s)
    service.create_box(make, "M8", "Box A1", "t", [], 3, "x")
    reader, writer = conn_of(s), conn_of(s)
    before = db.rev(reader)
    real = service.box_items

    def box_items_then_write(conn, box_id):
        out = real(conn, box_id)
        with db.tx(writer):                     # another user writes right after we read this box
            pass
        return out

    monkeypatch.setattr(service, "box_items", box_items_then_write)
    st = service.state(reader, cat)
    assert st["rev"] == before                  # labelled with the revision from before the read
    assert db.rev(reader) > st["rev"]           # so the client will notice it is behind and refetch


# ----------------------------------------------------------------------------- auto heal, per-batch approve, to-do list
def live_rows(cat, drop=()):
    return [{"id": o.id, "name": o.name, "vi": o.vi, "type": "Obj", "status": "ACTIVE", "avail": o.stock, "qty": o.stock,
             "loc": o.slot, "sector": "Hanoi-GPI"} for o in cat.items().values() if o.id not in drop]


def sync(client, rows):
    r = client.post("/api/catalog/sync", json={"objects": rows}, headers={"X-Sync-Token": "tok"})
    assert r.status_code == 200, r.text
    return r.json()


def box_state(client, bid):
    return next(b for b in client.get("/api/state").json()["boxes"] if b["id"] == bid)


def wait_status(client, bid, want, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        b = box_state(client, bid)
        if b["status"] == want:
            return b
        time.sleep(0.1)
    raise AssertionError(f"box {bid} never reached {want}, is {box_state(client, bid)['status']}")


def test_auto_heal_replaces_vanished_objects_and_leaves_pushed_boxes_alone(env):
    client, s, cat = make_client(env)
    a = _approved_box(client, "Box A1")
    b = _approved_box(client, "Box B1")
    client.post(f"/api/boxes/{b}/pushed", json={"sm_box_id": "SM-1"}, headers={"X-Sync-Token": "tok"})
    ids_a = [i["id"] for i in box_state(client, a)["items"]]
    ids_b = [i["id"] for i in box_state(client, b)["items"]]
    gone_a, gone_b = ids_a[0], ids_b[0]

    # switch off (default): the sync reports the problem but changes nothing
    out = sync(client, live_rows(cat, drop={gone_a, gone_b}))
    assert out["healed_boxes"] == [] and gone_a in out["claimed_now_unavailable"]
    assert box_state(client, a)["status"] == "approved"

    client.put("/api/automations/auto_heal", json={"enabled": True})
    out = sync(client, live_rows(cat, drop={gone_a, gone_b}))
    assert [h["box_id"] for h in out["healed_boxes"]] == [a]            # B1 is already in SM: never touched
    healed = wait_status(client, a, "draft")                            # content changed, so a person approves again
    now = [i["id"] for i in healed["items"]]
    assert len(now) == healed["count"] and gone_a not in now
    assert set(ids_a[1:]) <= set(now)                                   # the other 20 stayed
    assert [i["id"] for i in box_state(client, b)["items"]] == ids_b and box_state(client, b)["status"] == "approved"
    assert all_unique(s)
    assert any(e["kind"] == "heal" for e in client.get("/api/automations").json()["log"])


def test_auto_heal_with_auto_approve_returns_the_box_to_approved(env):
    client, s, cat = make_client(env)
    a = _approved_box(client, "Box A1")
    gone = box_state(client, a)["items"][0]["id"]
    client.put("/api/automations/auto_heal", json={"enabled": True})
    client.put("/api/automations/auto_approve", json={"enabled": True})
    sync(client, live_rows(cat, drop={gone}))
    final = wait_status(client, a, "approved")
    assert len(final["items"]) == final["count"] and gone not in [i["id"] for i in final["items"]]


def test_batch_approve_flag_approves_without_the_global_switch(env):
    client, s, cat = make_client(env)
    boxes = client.post("/api/batches/plan", json={"brief": "bếp\nvườn", "module": "M8", "boxes": 2}).json()["boxes"]
    r = client.post("/api/batches", json={"boxes": boxes, "approve": True})
    assert r.status_code == 201
    for bid in r.json()["ids"]:
        assert wait_status(client, bid, "approved")["status"] == "approved"
    assert client.get("/api/automations").json()["switches"][1]["enabled"] is False          # global switch untouched
    log = [e["msg"] for e in client.get("/api/automations").json()["log"] if e["kind"] == "auto_approve"]
    assert log and all("theo yêu cầu của lô" in m for m in log)

    # a box the code had to fill is never approved, even when the batch asks for it
    class Lazy(picker.MockLLM):
        def complete(self, system, user, ctx):
            return ""

    lazy = TestClient(create_app(s, llm=Lazy(), catalog=cat))
    spec = [{"module": "M9", "label": "Box A1", "theme": "t"}]
    bid = lazy.post("/api/batches", json={"boxes": spec, "approve": True}).json()["ids"][0]
    assert wait_status(lazy, bid, "draft")["status"] == "draft"


def test_todo_names_the_steps_that_still_need_a_person(env):
    client, s, cat = make_client(env)
    kinds = lambda: {t["kind"] for t in client.get("/api/automations").json()["todo"]}
    assert kinds() == {"sync"}                                            # nothing built yet, catalog never synced
    sync(client, live_rows(cat))
    assert kinds() == set()
    d = client.post("/api/boxes", json={"module": "M8", "label": "Box A1", "theme": "t"}).json()["id"]
    wait_settled(client, d)
    assert kinds() == {"review"}
    client.post(f"/api/boxes/{d}/approve")
    assert kinds() == {"push", "sheet"}
    todo = {t["kind"]: t for t in client.get("/api/automations").json()["todo"]}
    assert todo["push"]["where"] == "SM" and todo["sheet"]["where"] == "Bảng tính"
    gone = box_state(client, d)["items"][0]["id"]
    sync(client, live_rows(cat, drop={gone}))
    assert "blocked" in kinds() and "push" not in kinds()
    client.put("/api/automations/sheet_export", json={"enabled": False})
    assert "sheet" not in kinds()


# ----------------------------------------------------------------------------- one section = Box B2, Box B3, Box A1 with a shared code
def test_planner_uses_exact_labels_and_the_section_code():
    class Mock:
        name = "mock"
    p = planner.plan(Mock(), "bếp\nvườn\ndọn dẹp", "M8", "Box A1", 9, 21, set(),
                     labels=["Box B2", " Box B3 ", "Box A1", "Box B2", ""], code=" M8-02 ")
    assert [b["label"] for b in p["boxes"]] == ["Box B2", "Box B3", "Box A1"]      # trimmed, no repeats, in the order typed
    assert {b["code"] for b in p["boxes"]} == {"M8-02"} and {b["module"] for b in p["boxes"]} == {"M8"}
    assert planner.plan(Mock(), "x", "M8", "Box A1", 2, 21, set())["boxes"][0]["code"] == ""


def test_batch_with_a_section_code_reaches_the_sheet_feed(env):
    client, s, cat = make_client(env)
    plan = client.post("/api/batches/plan", json={"brief": "bếp\nvườn\ndọn dẹp", "module": "M8", "labels": ["Box B2", "Box B3", "Box A1"],
                                                   "code": "M8-02"})
    assert plan.status_code == 200
    boxes = plan.json()["boxes"]
    r = client.post("/api/batches", json={"boxes": boxes, "approve": True})
    assert r.status_code == 201
    for bid in r.json()["ids"]:
        wait_status(client, bid, "approved")
    feed = client.get("/api/sheet-boxes").json()["boxes"]
    assert sorted((b["stage"], b["module"], b["builder"]["module"]) for b in feed) == [
        ("A1", "M8-02", "M8"), ("B2", "M8-02", "M8"), ("B3", "M8-02", "M8")]          # code = section, builder.module = tab
    assert {b["title"] for b in feed} == {"M8-02 - A1", "M8-02 - B2", "M8-02 - B3"}
    assert all_unique(s)
    assert client.post("/api/batches/plan", json={"brief": "abc", "module": "M8", "labels": ["x"] * 13}).status_code == 422


# ----------------------------------------------------------------------------- category limit on a batch
def test_batch_categories_limit_every_box_and_unknown_ones_are_refused(env):
    client, s, cat = make_client(env)
    top = client.get("/api/categories").json()[0]["name"]                 # the biggest category has room for two full boxes
    plan = client.post("/api/batches/plan", json={"brief": "a\nb", "module": "M8", "boxes": 2, "categories": [top]})
    assert plan.status_code == 200
    boxes = plan.json()["boxes"]
    assert len(boxes) == 2 and all(b["categories"] == [top] for b in boxes)
    nope = "Nhóm không có thật"
    assert client.post("/api/batches/plan", json={"brief": "abc", "module": "M8", "categories": [nope]}).status_code == 422
    assert client.post("/api/batches", json={"boxes": [{"module": "M8", "label": "X", "categories": [nope]}]}).status_code == 422
    assert client.get("/api/state").json()["boxes"] == []                  # the refused request created nothing
    r = client.post("/api/batches", json={"boxes": boxes})
    assert r.status_code == 201
    done = wait_all_settled(client, r.json()["ids"])
    assert all(len(b["items"]) == b["count"] and all(i["category"] == top for i in b["items"]) for b in done.values())
    assert all_unique(s)
    # without categories the plan carries an empty list, meaning every category
    free = client.post("/api/batches/plan", json={"brief": "abc", "module": "M9"}).json()["boxes"]
    assert all(b["categories"] == [] for b in free)

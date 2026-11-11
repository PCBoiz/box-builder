"""The snapshot of boxes that SM holds: objects in them are never picked, added or pushed again, and no person is stored."""
import time

import pytest
from fastapi.testclient import TestClient

from app import db, picker, service
from app.main import create_app
from tests.test_automation import make_client, queue_on, wait_all_settled, wait_status
from tests.test_backend import _approved_box, conn_of, env, wait_settled  # noqa: F401  (env is a fixture)

TOK = {"X-Sync-Token": "tok"}


def sm_box(box_id, title, ids, status="assigned", qty=1, **extra):
    return {"box_id": box_id, "title": title, "status": status, "status_label": status.title(), "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "contents": [{"object_id": i, "quantity": qty} for i in ids], **extra}


def push_snapshot(client, boxes, **extra):
    return client.post("/api/sm/boxes/sync", json={"boxes": boxes, **extra}, headers=TOK)


def usable_ids(cat):
    return sorted(cat.items())


def held(s):
    return service.sm_held(conn_of(s))


def test_token_cases_and_the_browser_check_exemption(env):
    client, s, cat = make_client(env)
    body = {"boxes": [sm_box("SM-1", "X", usable_ids(cat)[:3])]}
    assert client.post("/api/sm/boxes/sync", json=body).status_code == 401
    assert client.post("/api/sm/boxes/sync", json=body, headers={"X-Sync-Token": "wrong"}).status_code == 401
    cross = {**TOK, "Sec-Fetch-Site": "cross-site", "Origin": "https://sm.config.inc"}       # the userscript calls from the SM page
    assert client.post("/api/sm/boxes/sync", json=body, headers=cross).status_code == 200
    assert client.post("/api/sm/boxes/sync", json=body, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 401   # still needs the code
    s.sync_token = ""
    blank = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    assert blank.post("/api/sm/boxes/sync", json=body, headers=TOK).status_code == 503       # a server with no SYNC_TOKEN refuses


def test_validation_caps_and_the_empty_list_rule(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    assert client.post("/api/sm/boxes/sync", json={"boxes": "nope"}, headers=TOK).status_code == 422
    assert push_snapshot(client, []).status_code == 422                                      # an empty list never wipes the snapshot
    assert push_snapshot(client, [sm_box("SM-1", "A", ids[:2])]).status_code == 200
    assert push_snapshot(client, []).status_code == 422 and set(held(s)) == set(ids[:2])     # still there
    big = push_snapshot(client, [sm_box("SM-9", "Big", [f"{i:06d}" for i in range(1, 202)])])
    assert big.status_code == 422 and "200" in big.json()["detail"]                          # one box with too many objects refuses the lot
    assert set(held(s)) == set(ids[:2])
    ok = push_snapshot(client, [], allow_empty=True)
    assert ok.status_code == 200 and ok.json()["active"] == 0 and held(s) == {}              # a deliberate empty list is allowed
    messy = [sm_box("SM-2", "Messy", ids[:3] + ["abc", "1234567", None, ""]), "not a box", {"title": "no id"},
             {"box_id": "SM-3", "title": "Junk qty", "status": "assigned", "contents": [{"object_id": ids[5], "quantity": "lots"}, 7]}]
    r = push_snapshot(client, messy).json()
    assert r["active"] == 2 and r["skipped_invalid"] == 2 and r["objects_held"] == 4          # bad ids and bad rows are dropped, not stored
    conn = conn_of(s)
    assert conn.execute("SELECT qty FROM sm_box_items WHERE sm_box_id='SM-3'").fetchone()[0] == 1   # unreadable quantity falls back to 1


def test_snapshot_is_replaced_and_ended_boxes_are_ignored(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    r = push_snapshot(client, [sm_box("A", "Active", ids[:3], "assigned"), sm_box("B", "Returned", ids[3:6], "Returned"),
                               sm_box("C", "Unknown state", ids[6:8], "on_the_floor"), sm_box("D", "Gone", ids[8:9], "deactivated")]).json()
    assert r["active"] == 2 and r["ignored_ended"] == 2 and r["received"] == 4
    assert set(held(s)) == set(ids[:3]) | set(ids[6:8])                                      # an unknown status counts as holding
    push_snapshot(client, [sm_box("E", "New", ids[10:12])])
    assert set(held(s)) == set(ids[10:12])                                                   # the old snapshot is gone, not merged
    assert client.get("/api/sync/check", headers=TOK).json()["sm_boxes"]["active"] == 1


def test_held_objects_cannot_be_added_or_picked(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    free = ids[:40]
    rest = ids[40:]
    push_snapshot(client, [sm_box("SM-7", "Tabletop 7", rest[:150])] + [sm_box(f"SM-8{n}", f"Other {n}", rest[150 * n: 150 * (n + 1)])
                                                                       for n in range(1, len(rest) // 150 + 1)])   # SM holds everything except 40 objects
    st = client.get("/api/state").json()["stats"]
    assert st["free"] == 40                                                                  # the free count already knows
    box = client.post("/api/boxes", json={"module": "M8", "label": "Box B2", "theme": "t", "count": 3}).json()["id"]
    wait_settled(client, box)
    mine = [i["id"] for b in client.get("/api/state").json()["boxes"] for i in b["items"]]
    assert set(mine) <= set(free) and len(mine) == 3                                         # the picker never offers a held object
    r = client.post(f"/api/boxes/{box}/items", json={"object_id": ids[50], "qty": 1})
    assert r.status_code == 409 and "hộp SM Tabletop 7" in r.json()["detail"]
    client.delete(f"/api/boxes/{box}/items/1")
    bulk = client.post(f"/api/boxes/{box}/items/bulk", json={"object_ids": [ids[50], ids[51]]}).json()
    assert bulk["added"] == [] and {f["id"] for f in bulk["failed"]} == {ids[50], ids[51]}
    big = client.post("/api/boxes", json={"module": "M9", "label": "Box B3", "theme": "t", "count": 41}).json()["id"]
    got = wait_status(client, big, "error")                                                  # only 37 are left: it says so instead of taking held ones
    assert not set(i["id"] for i in got["items"]) & set(ids[40:])


def test_our_own_pushed_box_is_not_a_conflict_and_a_held_object_blocks_the_queue(env):
    client, s, cat = make_client(env)
    mine = _approved_box(client, "Box A1")
    other = _approved_box(client, "Box B1")
    boxes = {b["id"]: b for b in client.get("/api/state").json()["boxes"]}
    mine_ids = [i["id"] for i in boxes[mine]["items"]]
    other_ids = [i["id"] for i in boxes[other]["items"]]
    client.post(f"/api/boxes/{mine}/pushed", json={"sm_box_id": "SM-100"}, headers=TOK)
    clash = other_ids[0]
    push_snapshot(client, [sm_box("SM-100", "Ours", mine_ids), sm_box("SM-200", "Someone else's", [clash, usable_ids(cat)[-1]])])
    h = held(s)
    assert not set(mine_ids) & set(h)                                                        # our pushed box does not count against us
    assert clash in h and h[clash]["title"] == "Someone else's"
    q = client.get("/api/queue").json()
    assert [b["box_id"] for b in q["ready"]] == [] and q["blocked"][0]["box_id"] == other
    assert f"{clash} đã nằm trong hộp SM Someone else's" in q["blocked"][0]["reasons"]
    queue_on(client)                                                                         # the queue starts off; with it on, the userscript is still not offered it
    assert client.get("/api/taskboxes?pending=true").json()["boxes"] == []
    # SM returns the other box: the objects are free again on the next sync, and the queue clears
    push_snapshot(client, [sm_box("SM-100", "Ours", mine_ids), sm_box("SM-200", "Someone else's", [clash], "returned")])
    assert clash not in held(s)
    assert [b["box_id"] for b in client.get("/api/queue").json()["ready"]] == [other]
    assert [b["builder"]["box_id"] for b in client.get("/api/taskboxes?pending=true").json()["boxes"]] == [other]
    assert client.get("/api/state").json()["boxes"]                                           # nothing of ours was touched


def test_no_person_is_ever_stored(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    who = "Nguyen Test Person"
    planted = {"assignee": who, "assignee_name": who, "holder": who, "holder_name": who, "created_by": who, "created_by_name": who,
               "receiver": who, "people": [who], "history": [{"by": who}], "note": who, "updated_by": who}
    box = sm_box("SM-5", "Box with people", ids[:2], **planted)
    box["contents"][0].update({"name": "Secret Object Name", "name_vi": "Tên bí mật", "borrows": [{"by": who}], "holder": who})
    r = push_snapshot(client, [box])
    assert r.status_code == 200 and r.json()["active"] == 1
    conn = conn_of(s)
    dump = " | ".join(" ".join(str(v) for v in row) for table in ("sm_boxes", "sm_box_items", "catalog_snapshot")
                      for row in conn.execute(f"SELECT * FROM {table}").fetchall())
    for secret in (who, "Secret Object Name", "Tên bí mật"):
        assert secret not in dump, f"{secret!r} was stored"
    cols = {tuple(r.keys()) for r in conn.execute("SELECT * FROM sm_boxes").fetchall()}
    assert cols == {("sm_box_id", "title", "status", "status_label", "created_at", "seen_at")}      # nothing else exists to hold a person
    assert tuple(conn.execute("SELECT * FROM sm_box_items").fetchone().keys()) == ("sm_box_id", "object_id", "qty")
    assert who not in client.get("/api/sync/check", headers=TOK).text


# ----------------------------------------------------------------------------- big snapshots must not need thousands of round trips
def test_saving_hundreds_of_sm_boxes_takes_a_handful_of_statements(env, monkeypatch):
    """On a hosted database every statement is a network round trip. 329 boxes of about 13 objects once took one INSERT per row (over a
    minute, longer than the sync tool waits); the rows now go in batches, so the number of statements does not grow with the boxes."""
    s, cat = env
    ids = usable_ids(cat)
    boxes = [sm_box(str(2000 + n), f"A1 Tabletop {n}", [ids[(n * 7 + k) % len(ids)] for k in range(13)]) for n in range(329)]
    calls = []
    real_execute, real_many = db.Conn.execute, db.Conn.executemany
    monkeypatch.setattr(db.Conn, "execute", lambda self, sql, params=(): (calls.append(sql), real_execute(self, sql, params))[1])
    monkeypatch.setattr(db.Conn, "executemany", lambda self, sql, rows: (calls.append(sql), real_many(self, sql, rows))[1])
    info = service.save_sm_boxes(conn_of(s), boxes, 1000.0)
    assert info["active"] == 329 and info["received"] == 329
    assert len(calls) <= 12, calls                                                      # begin, lock, delete x2, insert x2, snapshot, rev, commit
    c = conn_of(s)
    assert c.execute("SELECT COUNT(*) FROM sm_boxes").fetchone()[0] == 329
    assert c.execute("SELECT COUNT(*) FROM sm_box_items").fetchone()[0] == sum(len({ids[(n * 7 + k) % len(ids)] for k in range(13)}) for n in range(329))
    again = service.save_sm_boxes(conn_of(s), boxes[:5], 2000.0)                         # a later snapshot replaces the whole thing
    assert again["active"] == 5 and c.execute("SELECT COUNT(*) FROM sm_boxes").fetchone()[0] == 5


def test_executemany_batches_rows_maps_integrity_errors_and_accepts_nothing(env):
    s, _ = env
    c = conn_of(s)
    c.executemany("INSERT INTO external_used(object_id, note) VALUES(?,?)", [])        # an empty batch is not an error
    c.executemany("INSERT INTO external_used(object_id, note) VALUES(?,?)", ((f"{n:06d}", "x") for n in range(1, 501)))   # any iterable
    assert c.execute("SELECT COUNT(*) FROM external_used").fetchone()[0] == 500
    with pytest.raises(db.IntegrityError):
        c.executemany("INSERT INTO external_used(object_id, note) VALUES(?,?)", [("000001", "again")])
    c.executemany("INSERT INTO external_used(object_id, note) VALUES(?,?) ON CONFLICT (object_id) DO NOTHING", [("000001", "again"), ("000777", "new")])
    assert c.execute("SELECT COUNT(*) FROM external_used").fetchone()[0] == 501
    assert c.execute("SELECT note FROM external_used WHERE object_id='000001'").fetchone()[0] == "x"          # nothing overwritten
    c.executemany("DELETE FROM external_used WHERE object_id=?", [("000001",), ("000777",)])
    assert c.execute("SELECT COUNT(*) FROM external_used").fetchone()[0] == 499


def test_pasting_many_used_ids_still_counts_added_and_skips_boxes_and_repeats(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    r = client.post("/api/used", json={"text": " ".join(ids[:300] + ids[:50] + ["abc"]), "note": "bảng"}).json()
    assert r["added"] == 300 and r["unreadable"] == ["abc"] and r["already_in_a_box"] == []
    again = client.post("/api/used", json={"text": " ".join(ids[:320])}).json()
    assert again["added"] == 20                                                          # the first 300 were listed already

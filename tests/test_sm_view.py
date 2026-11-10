"""What SM holds, made visible: object status, ledger count, report of conflicts and double-booked objects, alerts, to-do."""
import time

from app import service
from tests.test_automation import make_client
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
    assert rep0 == {"synced_at": None, "boxes": 0, "objects_held": 0, "doubles": [], "conflicts": []}
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

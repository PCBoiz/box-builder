"""Backup export and merge-only import: a round trip loses nothing, nothing is overwritten, bad files are refused kindly."""
import json
import shutil

from fastapi.testclient import TestClient

from app import db, picker
from app.catalog import Catalog
from app.main import create_app
from app.settings import Settings
from tests.test_automation import make_client, wait_status
from tests.test_backend import _approved_box, env, wait_settled  # noqa: F401  (env is a fixture)

TOK = {"X-Sync-Token": "tok"}


def other_app(tmp_path, name="b.db", sync_token="tok"):
    """A second, empty app on its own SQLite file, with the same catalog file."""
    s = Settings(db_path=str(tmp_path / name), catalog_path=str(tmp_path / "cat.csv"), llm_mode="mock", sync_token=sync_token)
    src = Settings.from_env().catalog_path if False else None
    shutil.copy("data/catalog.csv", s.catalog_path)
    db.init(s.db_path)
    return TestClient(create_app(s, llm=picker.MockLLM(), catalog=Catalog(s.catalog_path))), s


def boxes_of(client):
    out = {}
    for b in client.get("/api/state").json()["boxes"]:
        out[(b["module"], b["label"])] = (b["status"], b["count"], b["code"], b["theme"],
                                          [(i["pos"], i["id"], i["qty"], i["locked"], i["source"]) for i in b["items"]],
                                          (b["pushed"] or {}).get("sm_box_id"))
    return out


def switches_of(client):
    return {s["key"]: (s["enabled"], s["config"]) for s in client.get("/api/automations").json()["switches"]}


def build_a(client):
    a1 = _approved_box(client, "Box A1")
    client.post(f"/api/boxes/{a1}/pushed", json={"sm_box_id": "SM-1"}, headers=TOK)
    client.post("/api/boxes", json={"module": "M8", "label": "Box B2", "theme": "bếp", "count": 5, "code": "M8-02"})
    b2 = next(b["id"] for b in client.get("/api/state").json()["boxes"] if b["label"] == "Box B2")
    wait_settled(client, b2)
    client.post(f"/api/boxes/{b2}/items/1/lock", json={"locked": True})
    _approved_box(client, "Box A1", module="M9")
    free = [o["id"] for o in client.get("/api/objects?status=free&per=3").json()["items"]]
    client.post("/api/used", json={"text": " ".join(free), "note": "M17 Box A1"})
    client.put("/api/automations/auto_approve", json={"enabled": True})
    client.put("/api/automations/catalog_watch", json={"config": {"low_stock": 7}})
    return {"a1": a1, "b2": b2, "used": free}


def test_round_trip_into_an_empty_app_loses_nothing(env, tmp_path):
    client, s, _ = make_client(env)
    made = build_a(client)
    export = client.get("/api/backup.json")
    assert export.status_code == 200 and "attachment" in export.headers["content-disposition"]
    data = export.json()
    assert data["format"] == "box-builder-backup" and data["version"] == 1 and len(data["boxes"]) == 3
    b, bs = other_app(tmp_path)
    before = b.get("/api/state").json()["rev"]
    dry = b.post("/api/backup/import", json=data)                               # dry run is the default
    assert dry.status_code == 200 and dry.json()["dry_run"] is True and dry.json()["totals"]["boxes_added"] == 3
    assert b.get("/api/state").json()["rev"] == before and b.get("/api/state").json()["boxes"] == []   # nothing was written
    real = b.post("/api/backup/import?dry_run=false", json=data).json()
    assert real["totals"] == {"boxes_added": 3, "boxes_skipped": 0, "items_added": real["totals"]["items_added"], "items_skipped": 0}
    assert boxes_of(b) == boxes_of(client)                                       # same status, items, locks, sources, pushed record
    assert {u["id"]: u["note"] for u in b.get("/api/state").json()["used"]} == {u["id"]: u["note"] for u in client.get("/api/state").json()["used"]}
    assert switches_of(b) == switches_of(client)
    assert b.get("/api/state").json()["boxes"][0]["items"]                       # and the unique-object rule still holds
    ids = [i["id"] for bx in b.get("/api/state").json()["boxes"] for i in bx["items"]]
    assert len(ids) == len(set(ids))
    # importing the same file again adds nothing and changes nothing
    again = b.post("/api/backup/import?dry_run=false", json=data).json()
    assert again["totals"]["boxes_added"] == 0 and again["totals"]["boxes_skipped"] == 3
    assert {e["reason"] for e in again["boxes"]} == {"đã có hộp cùng tên"}
    assert again["used"]["added"] == 0 and again["automations"]["applied"] == []
    assert boxes_of(b) == boxes_of(client)


def test_conflicts_are_reported_and_the_box_comes_in_as_a_draft(env, tmp_path):
    client, s, cat = make_client(env)
    made = build_a(client)
    data = client.get("/api/backup.json").json()
    m9 = next(bx for bx in data["boxes"] if bx["module"] == "M9")
    taken, gone, held = m9["items"][0]["object_id"], m9["items"][1]["object_id"], m9["items"][2]["object_id"]
    data["boxes"][data["boxes"].index(m9)]["items"][1]["object_id"] = "999999"            # no longer in the kho
    b, bs = other_app(tmp_path)
    b.post("/api/boxes", json={"module": "M7", "label": "Box X", "theme": "t", "count": 3})
    bx = next(x for x in b.get("/api/state").json()["boxes"] if x["label"] == "Box X")
    wait_settled(b, bx["id"])
    assert b.post(f"/api/boxes/{bx['id']}/items", json={"object_id": taken, "qty": 1}).status_code in (200, 201, 409)
    b.post("/api/boxes/%d/items/1/lock" % bx["id"], json={"locked": False})
    b.delete(f"/api/boxes/{bx['id']}/items/1")
    assert b.post(f"/api/boxes/{bx['id']}/items", json={"object_id": taken, "qty": 1}).status_code == 201
    b.post("/api/sm/boxes/sync", headers=TOK, json={"boxes": [{"box_id": "SM-77", "title": "Tabletop 7", "status": "assigned",
                                                              "contents": [{"object_id": held, "quantity": 1}]}]})
    rep = b.post("/api/backup/import?dry_run=false", json=data).json()
    e = next(x for x in rep["boxes"] if x["module"] == "M9")
    reasons = {i["object_id"]: i["reason"] for i in e["items_skipped"]}
    assert reasons[taken] == "đã nằm trong M7 / Box X"
    assert reasons["999999"] == "không còn trong kho"
    assert reasons[held] == "đã nằm trong hộp SM Tabletop 7"
    assert e["result"] == "added" and e["final_status"] == "draft" and "Nháp" in e["reason"]     # incomplete: approved is not kept
    assert e["pushed_restored"] is False
    got = next(x for x in b.get("/api/state").json()["boxes"] if x["module"] == "M9")
    assert got["status"] == "draft" and not any(i["id"] in (taken, held) for i in got["items"])
    assert all(i["id"] != "999999" for i in got["items"])
    ids = [i["id"] for bb in b.get("/api/state").json()["boxes"] for i in bb["items"]]
    assert len(ids) == len(set(ids))                                                         # still no object in two boxes


def test_hostile_and_broken_files_are_refused_with_a_plain_message(env, tmp_path):
    client, s, _ = make_client(env)
    good = client.get("/api/backup.json").json()
    def post(body, **kw): return client.post("/api/backup/import?dry_run=false", content=body, headers={"Content-Type": "application/json"}, **kw)
    r = post("{{{ not json")
    assert r.status_code == 422 and "không phải JSON" in r.json()["detail"]
    assert post(json.dumps({"format": "something-else", "version": 1})).status_code == 422
    assert "không phải tệp sao lưu" in post(json.dumps([1, 2])).json()["detail"]
    assert "chưa được hỗ trợ" in post(json.dumps({**good, "version": 2})).json()["detail"]
    assert post(json.dumps({**good, "boxes": "x"})).status_code == 422
    assert post(json.dumps({**good, "boxes": [{}] * 501})).status_code == 422
    big = post(b" " * (10 * 1024 * 1024 + 10))
    assert big.status_code == 413 and "10 MB" in big.json()["detail"]
    evil = {**good, "boxes": [
        {"module": "M" * 5000, "label": "x", "count": 3, "items": []},
        {"module": "M1", "label": "", "count": 3},
        {"module": "M1", "label": "Box A1", "count": 999},
        {"module": "M1", "label": "Box A2", "count": "x"},
        "just a string",
        {"module": "M2", "label": "Box A1", "count": 3, "status": "generating", "assignee": "Someone", "__proto__": {"x": 1},
         "items": [{"pos": 0, "object_id": "000207"}, {"pos": 1, "object_id": "'; DROP TABLE boxes;--"}, {"pos": "1", "object_id": "000207"},
                   {"pos": 1, "object_id": "000207", "qty": 99, "source": "<script>", "locked": "yes"}, {"pos": 1, "object_id": "000209"}]},
    ], "used": [{"object_id": "'; DROP TABLE x;--"}, "x", {"object_id": "000210", "note": "n" * 500}],
        "automations": [{"key": "nope", "enabled": True}, {"key": "catalog_watch", "enabled": True, "config": {"stale_hours": -5}}]}
    rep = post(json.dumps(evil))
    assert rep.status_code == 200
    rows = rep.json()["boxes"]
    assert [r["result"] for r in rows] == ["skipped"] * 5 + ["added"]
    last = rows[-1]
    assert last["final_status"] == "draft" and last["items_added"] == 1                      # generating comes in as a draft; one valid line
    st = client.get("/api/state").json()
    box = next(b for b in st["boxes"] if b["module"] == "M2")
    assert [(i["pos"], i["id"], i["qty"], i["locked"], i["source"]) for i in box["items"]] == [(1, "000207", 3, False, "import")]
    assert len(st["boxes"]) == 1 and len(st["used"]) == 1 and len(st["used"][0]["note"]) == 60  # note is cut, junk ids dropped
    assert client.get("/api/automations").status_code == 200                                  # the tables are all still there
    assert rep.json()["automations"]["applied"] == [] or "nope" not in rep.json()["automations"]["applied"]


def test_export_holds_no_secrets_and_the_endpoints_are_guarded(env, tmp_path):
    s, cat = env
    s.sync_token, s.deepseek_key, s.llm_mode = "SYNC-secret-TOKEN-1a2b", "sk-leaktest-abcdef0123456789", "mock"
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    client.post("/api/boxes", json={"module": "M8", "label": "Box A1", "theme": "t", "count": 2})
    text = client.get("/api/backup.json").text
    for secret in (s.sync_token, s.deepseek_key, s.db_path):
        assert secret not in text
    assert "catalog" not in json.loads(text) and "sm_boxes" not in json.loads(text)
    cross = {"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example"}
    assert client.post("/api/backup/import?dry_run=false", json=json.loads(text), headers=cross).status_code == 403   # a web page cannot restore into the app
    s.access_code = "ma-123"
    guarded = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    assert guarded.get("/api/backup.json").status_code == 401
    assert guarded.post("/api/backup/import", json={}).status_code == 401
    assert guarded.get("/api/backup.json?access_code=ma-123").status_code == 200              # the download link carries the code

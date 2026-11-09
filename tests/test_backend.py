"""Backend tests on the REAL catalog with a mock LLM (no API key, no cost)."""
import os
import shutil
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import db, picker, service
from app.catalog import Catalog
from app.main import create_app
from app.settings import Settings

REAL_CSV = os.path.join(os.path.dirname(__file__), "..", "data", "catalog.csv")


PG_URL = os.getenv("TEST_DATABASE_URL")          # set it to also run every test on a real Postgres
_open_conns = []


@pytest.fixture(params=["sqlite"] + (["postgres"] if PG_URL else []))
def env(request, tmp_path):
    target = str(tmp_path / "t.db") if request.param == "sqlite" else PG_URL
    if request.param == "postgres":
        c = db.connect(target)
        c.execute("DROP TABLE IF EXISTS box_items, boxes, external_used, meta CASCADE")
        c.close()
    s = Settings(db_path=target, catalog_path=str(tmp_path / "catalog.csv"), llm_mode="mock")
    shutil.copy(REAL_CSV, s.catalog_path)
    db.init(s.db_path)
    yield s, Catalog(s.catalog_path)
    while _open_conns:
        _open_conns.pop().close()


def conn_of(s):
    c = db.connect(s.db_path)
    _open_conns.append(c)
    return c


def make_box(s, module, label, theme="", count=21, cats=()):
    c = conn_of(s)
    try:
        return service.create_box(c, module, label, theme, list(cats), count, "tester")
    finally:
        c.close()


def all_object_ids(s):
    c = conn_of(s)
    try:
        return [r[0] for r in c.execute("SELECT object_id FROM box_items")]
    finally:
        c.close()


# ----------------------------------------------------------------------------- parsing / validation
def test_parse_reply_variants():
    text = "```\n004702|1\n2. 004700|2\n- 4699|1\n* 004698\n\n999|x\nhello world\n```"
    got = picker.parse_reply(text)
    assert got[:4] == [("004702", 1), ("004700", 2), ("004699", 1), ("004698", 1)]
    assert ("000999", 1) in got            # unreadable quantity falls back to 1, ID is still padded
    assert all(len(i) == 6 for i, _ in got)


def test_validate_caps_and_dedupes(env):
    s, cat = env
    items = list(cat.items().values())
    pool = {i.id: i for i in items}
    one = next(i for i in items if i.stock == 1)
    many = next(i for i in items if i.stock >= 10)
    got = picker.validate([(one.id, 3), (many.id, 99), (many.id, 1), ("999999", 1)], pool, 10, max_qty=3)
    assert [(i.id, q) for i, q in got] == [(one.id, 1), (many.id, 3)]


def test_catalog_rules(env):
    _, cat = env
    assert len(cat.items()) == 1681                      # same rule as the Make blueprint
    assert cat.get("004702") is None                     # no Vietnamese name -> not usable
    assert cat.search("tỏi")                             # accent-insensitive search
    assert cat.search("toi")


# ----------------------------------------------------------------------------- the core promise
def test_many_simultaneous_boxes_never_share_an_object(env):
    s, cat = env
    class Counting(picker.MockLLM):
        calls = 0

        def complete(self, *a, **k):
            Counting.calls += 1
            return super().complete(*a, **k)

    llm = Counting()
    # 12 people press "generate" at the same time with the SAME theme -> maximum contention
    ids = [make_box(s, f"M{n}", "Box A1", theme="kitchen cup bowl spoon plate") for n in range(1, 13)]
    threads = [picker.start_generation(s, cat, llm, i) for i in ids]
    for t in threads:
        t.join(60)
    assert Counting.calls == 12, "boxes picked at the same moment must not waste LLM calls on clashing picks"
    objs = all_object_ids(s)
    assert len(objs) == 12 * 21
    assert len(set(objs)) == len(objs), "an object ended up in two boxes"
    c = conn_of(s)
    assert {r["status"] for r in c.execute("SELECT status FROM boxes")} == {"draft"}
    for r in c.execute("SELECT object_id, qty FROM box_items"):
        assert 1 <= r["qty"] <= s.max_qty
        assert r["qty"] <= cat.get(r["object_id"]).stock


def test_database_itself_refuses_a_duplicate(env):
    s, cat = env
    a, b = make_box(s, "M1", "Box A1"), make_box(s, "M2", "Box A1")
    c = conn_of(s)
    item = next(iter(cat.items().values()))
    assert service.claim(c, a, [(1, item, 1, "llm")]) == [1]
    assert service.claim(c, b, [(1, item, 1, "llm")]) == []          # skipped, not double-booked
    with pytest.raises(Exception):                                   # even a raw INSERT is refused
        c.execute("INSERT INTO box_items(box_id,pos,object_id) VALUES(?,?,?)", (b, 1, item.id))


class FlakyLLM:
    """Everything a real model might do wrong."""
    name = "flaky"

    def __init__(self):
        self.calls = 0

    def complete(self, system, user, ctx):
        self.calls += 1
        pool = ctx["pool"]
        if self.calls == 1:
            good = [p.id for p in pool[:5]]
            return "```\n" + "\n".join([f"{g}|1" for g in good] + [f"{good[0]}|1", "999999|1", "abc", "", f"{good[1]}|9"]) + "\n```"
        if self.calls == 2:
            return ""                                                # empty answer
        raise picker.LLMError("simulated outage")                    # third call fails


def test_bad_llm_output_is_repaired_by_code(env):
    s, cat = env
    box = make_box(s, "M8", "Box A1", count=21)
    picker.run_generation(s, cat, FlakyLLM(), box)                   # raises inside -> must be handled
    c = conn_of(s)
    b = service.get_box(c, box)
    items = service.box_items(c, box)
    # the outage on call 3 is reported to the page instead of crashing; items accepted so far are kept
    assert b["status"] == "error" and "simulated outage" in b["message"]
    assert len(items) == 5 and len({i["object_id"] for i in items}) == 5
    assert all(i["qty"] <= 3 for i in items)


class ShortLLM:
    name = "short"

    def complete(self, system, user, ctx):
        return "\n".join(f"{p.id}|1" for p in ctx["pool"][:3])      # always too few


def test_too_few_answers_are_filled_by_code(env):
    s, cat = env
    box = make_box(s, "M8", "Box A1", count=21)
    picker.run_generation(s, cat, ShortLLM(), box)
    c = conn_of(s)
    items = service.box_items(c, box)
    assert service.get_box(c, box)["status"] == "draft" and len(items) == 21
    assert {i["source"] for i in items} == {"llm", "fill"}
    assert len({i["object_id"] for i in items}) == 21


def test_workbook_ids_are_never_picked(env):
    s, cat = env
    c = conn_of(s)
    taken = list(cat.items())[:300]
    res = service.import_used(c, " ".join(taken) + " abc 12", "workbook", cat)
    assert res["added"] == 301 and res["not_in_catalog"] == ["000012"]
    assert res["unreadable"] == ["abc"]
    box = make_box(s, "M8", "Box A1")
    picker.run_generation(s, cat, picker.MockLLM(), box)
    got = {i["object_id"] for i in service.box_items(c, box)}
    assert len(got) == 21 and not got & set(taken)


def test_category_filter_is_respected(env):
    s, cat = env
    box = make_box(s, "M8", "Box A1", cats=["Đồ nhà bếp"])
    picker.run_generation(s, cat, picker.MockLLM(), box)
    c = conn_of(s)
    assert {cat.get(i["object_id"]).category for i in service.box_items(c, box)} == {"Đồ nhà bếp"}


def test_filters_too_narrow_gives_clear_error(env):
    s, cat = env
    box = make_box(s, "M8", "Box A1", cats=["Đồ ăn tươi sống"], count=40)
    picker.run_generation(s, cat, picker.MockLLM(), box)
    c = conn_of(s)
    b = service.get_box(c, box)
    assert b["status"] == "error" and "free objects match" in b["message"]


# ----------------------------------------------------------------------------- HTTP flow
def wait_settled(client, box_id, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        for b in client.get("/api/state").json()["boxes"]:
            if b["id"] == box_id and b["status"] != "generating":
                return b
        time.sleep(0.1)
    raise AssertionError("generation did not finish")


def test_http_flow_lock_regenerate_replace_approve_export(env):
    s, cat = env
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    r = client.post("/api/boxes", json={"module": "M8", "label": "Box A1", "theme": "kitchen cup bowl", "owner": "An"})
    assert r.status_code == 201
    box = wait_settled(client, r.json()["id"])
    bid = box["id"]
    assert box["status"] == "draft" and len(box["items"]) == 21 and box["owner"] == "An"

    # duplicate box name is refused
    assert client.post("/api/boxes", json={"module": "M8", "label": "Box A1"}).status_code == 409

    # lock 3, regenerate: locked stay, the rest change, nothing duplicates
    keep = [it["id"] for it in box["items"][:3]]
    for pos in (1, 2, 3):
        assert client.post(f"/api/boxes/{bid}/items/{pos}/lock", json={"locked": True}).status_code == 200
    before = {it["id"] for it in box["items"]}
    assert client.post(f"/api/boxes/{bid}/regenerate").status_code == 200
    box = wait_settled(client, bid)
    after = [it["id"] for it in box["items"]]
    assert after[:3] == keep and len(set(after)) == 21
    assert len(before & set(after[3:])) == 0, "regenerate must offer different objects"

    # replace one slot
    assert client.post(f"/api/boxes/{bid}/items/10/replace").status_code == 200
    box2 = wait_settled(client, bid)
    assert box2["items"][9]["id"] != box["items"][9]["id"] and len(box2["items"]) == 21

    # a locked slot cannot be replaced
    assert client.post(f"/api/boxes/{bid}/items/1/replace").status_code == 409

    # manual add: remove one, put in something taken by another box -> 409 naming the box
    other = client.post("/api/boxes", json={"module": "M17", "label": "Box A1", "theme": "desk pen paper"}).json()["id"]
    ob = wait_settled(client, other)
    taken_by_other = ob["items"][0]["id"]
    assert client.delete(f"/api/boxes/{bid}/items/21").status_code == 200
    r = client.post(f"/api/boxes/{bid}/items", json={"object_id": taken_by_other})
    assert r.status_code == 409 and "M17 / Box A1" in r.json()["detail"]
    assert client.post(f"/api/boxes/{bid}/approve").status_code == 422          # 20 of 21
    assert client.post(f"/api/boxes/{bid}/fill").status_code == 200
    box = wait_settled(client, bid)
    assert len(box["items"]) == 21

    # approve -> read-only until reopened
    assert client.post(f"/api/boxes/{bid}/approve").status_code == 200
    assert client.post(f"/api/boxes/{bid}/items/5/lock", json={"locked": False}).status_code == 409
    assert client.post(f"/api/boxes/{bid}/regenerate").status_code == 409
    assert client.post(f"/api/boxes/{bid}/reopen").status_code == 200

    # export
    csv_text = client.get("/api/export.csv").text
    assert csv_text.count("\n") >= 1 + 42 and "M8,Box A1" in csv_text

    # discarding frees the objects for others
    freed = {it["id"] for it in box["items"]}
    assert client.delete(f"/api/boxes/{bid}").status_code == 200
    st = client.get("/api/state").json()
    assert st["stats"]["in_boxes"] == 21 and st["stats"]["boxes"] == 1
    assert not freed & {it["id"] for b in st["boxes"] for it in b["items"]}


def test_state_polling_only_sends_changes(env):
    s, cat = env
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    st = client.get("/api/state").json()
    assert client.get(f"/api/state?rev={st['rev']}").json() == {"rev": st["rev"], "unchanged": True}
    client.post("/api/boxes", json={"module": "M1", "label": "Box A1"})
    assert "unchanged" not in client.get(f"/api/state?rev={st['rev']}").json()


def test_access_code(env):
    s, cat = env
    s.access_code = "secret"
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    assert client.get("/api/config").json()["needs_code"] is True
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers={"X-Access-Code": "secret"}).status_code == 200
    assert client.get("/api/export.csv?access_code=secret").status_code == 200


def test_abandoned_drafts_release_objects(env):
    s, cat = env
    box = make_box(s, "M8", "Box A1")
    picker.run_generation(s, cat, picker.MockLLM(), box)
    c = conn_of(s)
    assert service.expire(c, 12) == 0
    c.execute("UPDATE boxes SET updated_at = updated_at - 13*3600")
    assert service.expire(c, 12) == 1 and all_object_ids(s) == []
    # approved boxes never expire
    box = make_box(s, "M8", "Box A1")
    picker.run_generation(s, cat, picker.MockLLM(), box)
    service.approve(c, box)
    c.execute("UPDATE boxes SET updated_at = updated_at - 99*3600")
    assert service.expire(c, 12) == 0 and len(all_object_ids(s)) == 21


def test_stuck_generating_box_is_marked_failed(env):
    s, _ = env
    box = make_box(s, "M8", "Box A1")
    c = conn_of(s)
    c.execute("UPDATE boxes SET updated_at = updated_at - 700")
    service.expire(c, 12)
    assert service.get_box(c, box)["status"] == "error"


# ----------------------------------------------------------------------------- DeepSeek client against a fake API
def _fake_deepseek(handler_fn):
    import http.server
    import json as _json

    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            body = _json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append((self.path, self.headers.get("Authorization"), body))
            code, payload = handler_fn(body)
            raw = _json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


def test_deepseek_client_sends_the_right_request_and_reads_the_answer(env):
    s, cat = env

    def answer(body):
        ids = [ln.split("|")[0] for ln in body["messages"][1]["content"].splitlines() if ln[:6].isdigit()][:21]
        return 200, {"choices": [{"message": {"role": "assistant", "content": "\n".join(f"{i}|1" for i in ids)}}]}

    srv, seen = _fake_deepseek(answer)
    s.deepseek_key, s.deepseek_base, s.llm_mode = "sk-test", f"http://127.0.0.1:{srv.server_port}", "deepseek"
    llm = picker.make_llm(s)
    assert llm.name == "deepseek"
    box = make_box(s, "M8", "Box A1", theme="a kitchen table")
    picker.run_generation(s, cat, llm, box)
    srv.shutdown()

    path, auth, body = seen[0]
    assert path == "/chat/completions" and auth == "Bearer sk-test"
    assert body["model"] == "deepseek-flash" and body["thinking"] == {"type": "disabled"}
    assert "a kitchen table" in body["messages"][0]["content"] and "exactly 21 objects" in body["messages"][0]["content"]
    user = body["messages"][1]["content"]
    assert "Stock" not in user, "stock must not be sent, so the model cannot copy it into quantities"
    c = conn_of(s)
    assert service.get_box(c, box)["status"] == "draft" and len(service.box_items(c, box)) == 21
    assert len(seen) == 1


def test_deepseek_http_error_is_shown_on_the_box(env):
    s, cat = env
    srv, seen = _fake_deepseek(lambda body: (401, {"error": {"message": "Authentication Fails"}}))
    s.deepseek_key, s.deepseek_base, s.llm_mode = "bad", f"http://127.0.0.1:{srv.server_port}", "deepseek"
    box = make_box(s, "M8", "Box A1")
    picker.run_generation(s, cat, picker.make_llm(s), box)
    srv.shutdown()
    c = conn_of(s)
    b = service.get_box(c, box)
    assert b["status"] == "error" and "401" in b["message"]
    assert len(seen) == 1, "a 401 must not be retried"
    assert service.box_items(c, box) == []

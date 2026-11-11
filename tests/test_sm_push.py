"""The push pipeline against the fake SM: ready boxes become TaskBoxes there and are recorded as pushed. The real SM is never involved."""
from fastapi.testclient import TestClient

from app import picker
from app.main import create_app
from app.sm_client import MockSM, SMError
from tests.test_automation import live_rows, queue_on, sync
from tests.test_backend import conn_of, env, wait_settled  # noqa: F401  (env is a fixture)

TOK = {"X-Sync-Token": "tok"}
CROSS = {"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example"}


def client_for(env, mode="mock"):
    s, cat = env
    s.sync_token, s.sm_push_mode = "tok", mode
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    queue_on(client)                                                                       # off by default; these tests are about what happens when it is on
    return client, s, cat


def approved(client, label, module="M8", code="", anchor="", count=3):
    bid = client.post("/api/boxes", json={"module": module, "label": label, "theme": "t", "count": count, "code": code, "anchor": anchor}).json()["id"]
    wait_settled(client, bid)
    assert client.post(f"/api/boxes/{bid}/approve").status_code == 200
    return bid


def state_of(client):
    return {b["id"]: b for b in client.get("/api/state").json()["boxes"]}


def test_mode_off_refuses_and_says_how_to_turn_it_on(env):
    client, s, cat = client_for(env, "off")
    assert client.get("/api/config").json()["sm_push_mode"] == "off"
    for r in (client.post("/api/sm/push", json={}), client.get("/api/sm/mock")):
        assert r.status_code == 409 and "SM_PUSH_MODE=mock" in r.json()["detail"]
    from app.settings import Settings
    import os
    os.environ["SM_PUSH_MODE"] = "real"                                                # there is no real mode: anything else means off
    try:
        assert Settings.from_env().sm_push_mode == "off"
    finally:
        os.environ.pop("SM_PUSH_MODE")


def test_ready_boxes_become_taskboxes_in_the_fake_and_are_recorded(env):
    client, s, cat = client_for(env)
    assert client.get("/api/config").json()["sm_push_mode"] == "mock"
    a = approved(client, "Box A1", "M8", anchor=sorted(cat.items())[0])
    b = approved(client, "Box B2", "M9", code="M9-02")
    titles = {x["builder"]["box_id"]: x["title"] for x in client.get("/api/taskboxes?pending=true").json()["boxes"]}
    r = client.post("/api/sm/push", json={}).json()
    assert r["created"] == 2 and r["failed"] == 0 and all(x["ok"] for x in r["results"])
    assert sorted(x["sm_box_id"] for x in r["results"]) == ["MBX-0001", "MBX-0002"]
    st = state_of(client)
    assert st[a]["pushed"]["sm_box_id"] and st[b]["pushed"]["sm_box_id"]                  # recorded as pushed
    assert client.get("/api/queue").json()["ready"] == []                                  # nothing left to push
    fake = {x["title"]: x for x in client.get("/api/sm/mock").json()["boxes"]}
    assert set(fake) == set(titles.values())
    one = fake[titles[a]]
    assert one["note"] == f"Created by Box builder #{a}" and one["status"] == "created" and one["status_label"] == "Unassigned"
    assert one["anchor_object_id"] == sorted(cat.items())[0] and one["object_ids"] == [i["id"] for i in st[a]["items"]]
    assert fake[titles[b]]["anchor_object_id"] in (None, "")                              # no anchor given: none sent
    assert any(e["kind"] == "sm_push" for e in client.get("/api/automations").json()["log"])
    again = client.post("/api/sm/push", json={}).json()                                    # running it again creates nothing twice
    assert again == {"created": 0, "failed": 0, "results": []}
    assert len(client.get("/api/sm/mock").json()["boxes"]) == 2


def test_two_ready_boxes_with_one_title_are_caught_by_the_queue_before_the_push(env):
    client, s, cat = client_for(env)
    a = approved(client, "Box A1", "M8", code="X-1")
    b = approved(client, "Box A1", "M9", code="X-1")                                       # the same title as the first
    r = client.post("/api/sm/push", json={}).json()
    assert r["created"] == 1 and r["failed"] == 1
    bad = next(x for x in r["results"] if not x["ok"])
    assert bad["code"] == "blocked" and "trùng" in bad["error"]                            # stopped by the queue's own check, never sent
    assert len(client.get("/api/sm/mock").json()["boxes"]) == 1
    st = state_of(client)
    assert sum(1 for x in st.values() if x["pushed"]) == 1 and not st[bad["box_id"]]["pushed"]


def test_a_box_the_fake_refuses_does_not_stop_the_others_and_stays_ready(env):
    """The app can lose track (someone clears a pushed mark by hand) while the fake still holds the box: the fake refuses the copy."""
    client, s, cat = client_for(env)
    a = approved(client, "Box A1", "M8")
    assert client.post("/api/sm/push", json={}).json()["created"] == 1
    b = approved(client, "Box B2", "M9")
    assert client.delete(f"/api/boxes/{a}/pushed", headers=TOK).status_code == 200      # the admin escape hatch: forget that A was pushed
    r = client.post("/api/sm/push", json={}).json()
    assert r["created"] == 1 and r["failed"] == 1
    bad = next(x for x in r["results"] if not x["ok"])
    ok = next(x for x in r["results"] if x["ok"])
    assert bad["box_id"] == a and bad["code"] == "invalid" and bad["error"]               # a plain reason from the fake, not a crash
    assert ok["box_id"] == b                                                               # the other box still went through
    st = state_of(client)
    assert not st[a]["pushed"] and st[b]["pushed"]
    assert [x["box_id"] for x in client.get("/api/queue").json()["ready"]] == [a]          # the refused one is still ready, nothing was lost


def test_options_reach_the_payload_and_a_borrow_without_a_name_is_refused(env):
    client, s, cat = client_for(env)
    approved(client, "Box A1", "M8")
    r = client.post("/api/sm/push", json={"assignee": "An"}).json()
    assert r["created"] == 1
    box = client.get("/api/sm/mock").json()["boxes"][0]
    assert box["status"] == "assigned" and box["assignee"] == "An"
    approved(client, "Box B2", "M9")
    r = client.post("/api/sm/push", json={"borrow_now": True}).json()                      # borrow needs someone to borrow for
    assert r["created"] == 0 and r["failed"] == 1 and r["results"][0]["code"] == "invalid"
    r = client.post("/api/sm/push", json={"assignee": "Bình", "borrow_now": True}).json()
    assert r["created"] == 1
    got = {x["status"]: x for x in client.get("/api/sm/mock").json()["boxes"]}
    assert "collected" in got and got["collected"]["holder"] == "Bình"
    assert client.post("/api/sm/push", json={"assignee": "x" * 61}).status_code == 422     # the name length is capped


def test_blocked_boxes_are_reported_and_the_queue_switch_is_respected(env):
    client, s, cat = client_for(env)
    a = approved(client, "Box A1", "M8")
    gone = state_of(client)[a]["items"][0]["id"]
    sync(client, live_rows(cat, drop={gone}))                                              # SM no longer lists one of its objects
    r = client.post("/api/sm/push", json={}).json()
    assert r["created"] == 0 and r["results"][0]["code"] == "blocked" and gone in r["results"][0]["error"]
    assert client.get("/api/sm/mock").json()["boxes"] == []
    client.put("/api/automations/sm_queue", json={"enabled": False})
    off = client.post("/api/sm/push", json={})
    assert off.status_code == 409 and "Hàng chờ" in off.json()["detail"]


def test_the_queue_is_off_by_default_so_a_push_is_refused_until_it_is_switched_on(env):
    s, cat = env
    s.sync_token, s.sm_push_mode = "tok", "mock"
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))                  # the stock default: no queue_on here
    approved(client, "Box A1", "M8")
    off = client.post("/api/sm/push", json={})
    assert off.status_code == 409 and "Hàng chờ" in off.json()["detail"]
    assert client.get("/api/sm/mock").json()["boxes"] == []
    assert client.get("/api/taskboxes?pending=true").json() == {"boxes": [], "disabled": True}
    queue_on(client)
    assert client.post("/api/sm/push", json={}).json()["created"] == 1


def test_a_web_page_cannot_start_a_push(env):
    client, s, cat = client_for(env)
    approved(client, "Box A1", "M8")
    assert client.post("/api/sm/push", json={}, headers=CROSS).status_code == 403
    assert client.get("/api/queue").json()["ready"] != []                                  # nothing was pushed


def test_the_fake_learns_new_catalog_objects():
    sm = MockSM({"000001": 5})
    payload = {"title": "T", "contents": [{"object_id": "000002", "quantity": 1}]}
    try:
        sm.create_box(payload)
        raise AssertionError("an unknown object must be refused")
    except SMError as e:
        assert e.code == "invalid"
    sm.add_stock({"000002": 3, "000001": 99}, {"000002": "New"})
    assert sm.create_box(payload) == "MBX-0001"
    assert sm.available("000001") == 5                                                     # a known object keeps what it had

"""The other SM steps on the fake: assign, borrow, send to the floor, hand over, return the old boxes."""
from datetime import datetime, timedelta, timezone

from tests.test_backend import env  # noqa: F401  (env is a fixture)
from tests.test_sm_push import approved, client_for, state_of
from app import sm_push
from app.sm_client import MockSM, SMError

CROSS = {"Sec-Fetch-Site": "cross-site", "Origin": "http://evil.example"}


def fake(n=3, clock=None):
    sm = MockSM({f"{i:06d}": 10 for i in range(1, 30)}, clock=clock)
    ids = [sm.create_box({"title": f"Box {k}", "contents": [{"object_id": f"{k:06d}", "quantity": 1}]}) for k in range(1, n + 1)]
    return sm, ids


def test_handover_keeps_the_status_and_changes_who_holds_the_box():
    sm, (a, b, c) = fake()
    sm.transition(b, "assigned", assignee="An")
    assert sm.hand_over(b, "Bình")["assignee"] == "Bình" and sm.get_box(b)["status"] == "assigned"
    sm.transition(b, "collected", borrower="Bình")
    assert sm.hand_over(b, "Chi")["holder"] == "Chi" and sm.get_box(b)["status"] == "collected"
    for bad, code in ((lambda: sm.hand_over(a, "X"), "bad_move"),                     # a box nobody has yet cannot be handed over
                      (lambda: sm.hand_over(b, "  "), "invalid"), (lambda: sm.hand_over("nope", "X"), "missing")):
        try:
            bad()
            raise AssertionError("should have been refused")
        except SMError as e:
            assert e.code == code and e.message
    assert sm.get_box(b)["history"][-1]["receiver"] == "Chi"


def test_each_step_reports_what_the_fake_refuses_and_does_not_stop_the_rest():
    sm, (a, b, c) = fake()
    r = sm_push.assign(sm, [a, "ghost", b, a], "An")                                    # a repeated id is done once
    assert [x["ok"] for x in r] == [True, False, True] and r[1]["code"] == "missing" and r[1]["error"]
    assert sm_push.assign(sm, [c], "")[0]["code"] == "invalid"                          # no name given: refused and said so
    r = sm_push.borrow(sm, [a, c], "An")                                                # c was never assigned
    assert [x["ok"] for x in r] == [True, False] and r[1]["code"] == "bad_move" and "Unassigned" in r[1]["error"]
    assert sm.get_box(a)["holder"] == "An"
    r = sm_push.to_floor(sm, [a, b])                                                    # b is only assigned
    assert [x["ok"] for x in r] == [True, False] and sm.get_box(a)["status"] == "on_floor"
    r = sm_push.hand_over(sm, [a, c], "Bình")
    assert [x["ok"] for x in r] == [True, False] and sm.get_box(a)["holder"] == "Bình"


def test_returning_old_boxes_needs_confirm_and_only_takes_boxes_on_the_floor_that_are_old_enough():
    base = datetime(2026, 11, 1, tzinfo=timezone.utc)
    ticks = iter(range(1000))
    sm = MockSM({f"{i:06d}": 10 for i in range(1, 30)}, clock=lambda: (base + timedelta(days=next(ticks))).strftime("%Y-%m-%dT%H:%M:%SZ"))
    ids = [sm.create_box({"title": f"Box {k}", "contents": [{"object_id": f"{k:06d}", "quantity": 1}]}) for k in range(1, 5)]   # 1 Nov .. 4 Nov
    for bid in ids[:3]:
        sm.transition(bid, "assigned", assignee="An"); sm.transition(bid, "collected", borrower="An"); sm.transition(bid, "on_floor")
    now = base + timedelta(days=5)                                                      # box 4 stays Unassigned and must never be returned
    pre = sm_push.return_old(sm, 3, "An", now=now)
    assert pre["preview"] is True and [b["box_id"] for b in pre["boxes"]] == [ids[0], ids[1], ids[2]] and pre["results"] == []
    assert {b["status"] for b in sm.list_boxes()} == {"on_floor", "created"}            # a preview changes nothing
    assert [b["box_id"] for b in sm_push.return_old(sm, 4, "An", now=now)["boxes"]] == [ids[0], ids[1]]     # age filter
    done = sm_push.return_old(sm, 4, "An", confirm=True, now=now)
    assert done["preview"] is False and [r["ok"] for r in done["results"]] == [True, True]
    assert sm.get_box(ids[0])["status"] == "deactivated" and sm.get_box(ids[0])["holder"] is None
    assert sm.get_box(ids[2])["status"] == "on_floor" and sm.get_box(ids[3])["status"] == "created"
    nobody = sm_push.return_old(sm, 0, "", confirm=True, now=now)                       # SM wants to know who returns: refused and said so
    assert nobody["results"] and not any(r["ok"] for r in nobody["results"]) and nobody["results"][0]["code"] == "invalid"
    assert sm.get_box(ids[2])["status"] == "on_floor"
    assert sm_push.old_on_the_floor(sm, 0, now=now)[0]["days_old"] >= 0


def test_the_endpoint_walks_a_box_through_every_step_and_never_acts_without_confirm(env):
    client, s, cat = client_for(env)
    a = approved(client, "Box A1", "M8")
    assert client.post("/api/sm/push", json={}).json()["created"] == 1
    mock = lambda: client.get("/api/sm/mock").json()["boxes"]
    sid = mock()[0]["box_id"]
    move = lambda **kw: client.post("/api/sm/moves", json=kw)
    assert move(action="borrow", box_ids=[sid], person="An").json()["results"][0]["code"] == "bad_move"      # not assigned yet: reported
    assert move(action="assign", box_ids=[sid], person="An").json()["results"][0]["ok"]
    assert move(action="hand_over", box_ids=[sid], person="Bình").json()["results"][0]["ok"]
    assert mock()[0]["assignee"] == "Bình"
    assert move(action="borrow", box_ids=[sid], person="Bình").json()["results"][0]["status"] == "collected"
    assert move(action="floor", box_ids=[sid]).json()["results"][0]["status"] == "on_floor"
    pre = move(action="return_old", days=0, person="Bình").json()                         # no confirm: only a preview
    assert pre["preview"] is True and [b["box_id"] for b in pre["boxes"]] == [sid] and mock()[0]["status"] == "on_floor"
    assert move(action="return_old", days=2, person="Bình", confirm=True).json()["results"] == []   # not two days old yet: nothing to do
    done = move(action="return_old", days=0, person="Bình", confirm=True).json()
    assert done["preview"] is False and done["results"][0]["ok"] and mock()[0]["status"] == "deactivated"
    assert {"sm_push"} <= {e["kind"] for e in client.get("/api/automations").json()["log"]}
    assert move(action="explode", box_ids=[]).status_code == 422
    assert move(action="assign", box_ids=["x"] * 201).status_code == 422                  # a sane cap


def test_the_steps_exist_only_in_fake_mode_and_not_from_another_site(env):
    client, s, cat = client_for(env, "off")
    r = client.post("/api/sm/moves", json={"action": "assign", "box_ids": ["MBX-0001"], "person": "An"})
    assert r.status_code == 409 and "SM_PUSH_MODE=mock" in r.json()["detail"]
    client2, s2, cat2 = client_for(env, "mock")
    assert client2.post("/api/sm/moves", json={"action": "floor", "box_ids": ["MBX-0001"]}, headers=CROSS).status_code == 403

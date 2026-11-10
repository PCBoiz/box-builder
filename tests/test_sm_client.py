"""Tests for the fake SM (app/sm_client.py). No app fixtures, no network."""
import ast
import itertools
import os
import socket

import pytest

from app import sm_client
from app.sm_client import (ASSIGNED, COLLECTED, CREATED, DEACTIVATED, ON_FLOOR, STATUSES, MockSM, SMClient,
                           SMError)

CHAIR, TABLE, LAMP = "100001", "100002", "100003"
INVENTORY = {CHAIR: 5, TABLE: 1, LAMP: 10}
NAMES = {CHAIR: "Ghế", TABLE: "Bàn", LAMP: "Đèn"}

# Every move the fake allows, as (from, to).
ALLOWED = [(CREATED, ASSIGNED), (ASSIGNED, CREATED), (ASSIGNED, COLLECTED), (COLLECTED, ON_FLOOR),
           (ON_FLOOR, DEACTIVATED), (COLLECTED, DEACTIVATED)]
REFUSED = [(a, b) for a, b in itertools.product(STATUSES, STATUSES) if (a, b) not in ALLOWED]

# What the caller passes for each target status.
FIELDS = {ASSIGNED: {"assignee": "an"}, CREATED: {}, COLLECTED: {"borrower": "binh"}, ON_FLOOR: {},
          DEACTIVATED: {"returned_by": "chi"}}


def make():
    return MockSM(INVENTORY, NAMES)


def payload(**over):
    body = {"title": "Hộp A", "anchor_object_id": CHAIR, "note": "ghi chú",
            "contents": [{"object_id": CHAIR, "quantity": 2}, {"object_id": LAMP, "quantity": 1}]}
    body.update(over)
    return body


# Moves that bring a fresh box to each status, in order.
PATH = {
    CREATED: [],
    ASSIGNED: [ASSIGNED],
    COLLECTED: [ASSIGNED, COLLECTED],
    ON_FLOOR: [ASSIGNED, COLLECTED, ON_FLOOR],
    DEACTIVATED: [ASSIGNED, COLLECTED, ON_FLOOR, DEACTIVATED],
}


def box_in(sm, status, **over):
    box_id = sm.create_box(payload(**over))
    for step in PATH[status]:
        sm.transition(box_id, step, **FIELDS[step])
    assert sm.get_box(box_id)["status"] == status
    return box_id


def refusal(call, *args, **kwargs):
    with pytest.raises(SMError) as info:
        call(*args, **kwargs)
    return info.value


# Creating

def test_create_returns_deterministic_ids():
    sm = make()
    assert sm.create_box(payload(title="A")) == "MBX-0001"
    assert sm.create_box(payload(title="B")) == "MBX-0002"


def test_created_box_has_all_taskbox_fields():
    sm = make()
    box = sm.get_box(sm.create_box(payload()))
    assert set(box) == {"box_id", "title", "status", "status_label", "anchor_object_id", "note", "contents",
                        "holder", "assignee", "next_actions", "may_hand_over", "short_count", "created_at",
                        "history"}
    assert box["box_id"] == "MBX-0001"
    assert box["title"] == "Hộp A"
    assert box["status"] == CREATED
    assert box["status_label"] == "Unassigned"
    assert box["anchor_object_id"] == CHAIR
    assert box["note"] == "ghi chú"
    assert box["holder"] is None and box["assignee"] is None
    assert box["short_count"] == 0
    assert box["history"] == [{"to": CREATED, "note": "ghi chú"}]
    assert box["contents"] == [
        {"object_id": CHAIR, "quantity": 2, "name": "Ghế", "available": 5, "short": False},
        {"object_id": LAMP, "quantity": 1, "name": "Đèn", "available": 10, "short": False},
    ]


def test_anchor_and_note_are_optional():
    sm = make()
    box = sm.get_box(sm.create_box({"title": "A", "contents": [{"object_id": CHAIR, "quantity": 1}]}))
    assert box["anchor_object_id"] is None
    assert box["note"] == ""


def test_name_falls_back_to_object_id():
    sm = MockSM(INVENTORY)
    box = sm.get_box(sm.create_box(payload()))
    assert [c["name"] for c in box["contents"]] == [CHAIR, LAMP]


def test_create_with_assignee_starts_assigned():
    sm = make()
    box = sm.get_box(sm.create_box(payload(assignee="an")))
    assert box["status"] == ASSIGNED
    assert box["assignee"] == "an"
    assert [h["to"] for h in box["history"]] == [CREATED, ASSIGNED]
    assert box["history"][1]["assignee"] == "an"


def test_borrower_without_borrow_now_is_ignored():
    sm = make()
    box = sm.get_box(sm.create_box(payload(borrower="binh")))
    assert box["status"] == CREATED
    assert box["holder"] is None
    assert sm.available(CHAIR) == 5


# Moves

@pytest.mark.parametrize("start,to", ALLOWED)
def test_allowed_moves(start, to):
    sm = make()
    box_id = box_in(sm, start)
    box = sm.transition(box_id, to, **FIELDS[to])
    assert box["status"] == to
    assert sm.get_box(box_id)["status"] == to
    assert box["status_label"] == sm_client.STATUS_LABELS[to]


@pytest.mark.parametrize("start,to", REFUSED)
def test_refused_moves(start, to):
    sm = make()
    box_id = box_in(sm, start)
    before = sm.get_box(box_id)
    err = refusal(sm.transition, box_id, to, assignee="an", borrower="binh", returned_by="chi")
    assert err.code == "bad_move"
    assert err.message
    assert sm.get_box(box_id) == before


def test_unknown_target_status_is_bad_move():
    sm = make()
    box_id = sm.create_box(payload())
    assert refusal(sm.transition, box_id, "flying").code == "bad_move"
    assert refusal(sm.transition, box_id, None).code == "bad_move"


def test_bad_move_message_names_box_and_statuses():
    sm = make()
    box_id = box_in(sm, COLLECTED)
    msg = refusal(sm.transition, box_id, ASSIGNED, assignee="an").message
    assert box_id in msg and "Borrowed" in msg and "Assigned" in msg


@pytest.mark.parametrize("start,to,field", [
    (CREATED, ASSIGNED, "assignee"),
    (ASSIGNED, COLLECTED, "borrower"),
    (COLLECTED, DEACTIVATED, "returned_by"),
    (ON_FLOOR, DEACTIVATED, "returned_by"),
])
@pytest.mark.parametrize("value", ["missing", None, "", "   ", 7])
def test_required_fields(start, to, field, value):
    sm = make()
    box_id = box_in(sm, start)
    before = sm.get_box(box_id)
    fields = {} if value == "missing" else {field: value}
    err = refusal(sm.transition, box_id, to, **fields)
    assert err.code == "invalid"
    assert err.message
    assert sm.get_box(box_id) == before


@pytest.mark.parametrize("start,to", [(COLLECTED, ON_FLOOR), (ASSIGNED, CREATED)])
def test_moves_without_required_field_need_no_person(start, to):
    sm = make()
    box_id = box_in(sm, start)
    assert sm.transition(box_id, to)["status"] == to


def test_userscript_style_call_with_null_fields_works():
    sm = make()
    box_id = sm.create_box(payload())
    box = sm.transition(box_id, ASSIGNED, assignee="an", borrower=None, returned_by=None, note=None)
    assert box["status"] == ASSIGNED


def test_unknown_transition_field_is_refused():
    sm = make()
    box_id = sm.create_box(payload())
    err = refusal(sm.transition, box_id, ASSIGNED, assignee="an", assigned_to="x")
    assert err.code == "invalid" and "assigned_to" in err.message
    assert sm.get_box(box_id)["status"] == CREATED


def test_people_and_history_follow_the_moves():
    sm = make()
    box_id = sm.create_box(payload())
    box = sm.transition(box_id, ASSIGNED, assignee="an", note="giao")
    assert box["assignee"] == "an" and box["holder"] is None
    box = sm.transition(box_id, CREATED)
    assert box["assignee"] is None
    sm.transition(box_id, ASSIGNED, assignee="an")
    box = sm.transition(box_id, COLLECTED, borrower="binh")
    assert box["holder"] == "binh" and box["assignee"] == "an"
    box = sm.transition(box_id, ON_FLOOR)
    assert box["holder"] == "binh"
    box = sm.transition(box_id, DEACTIVATED, returned_by="chi", note="xong")
    assert box["holder"] is None
    assert box["history"] == [
        {"to": CREATED, "note": "ghi chú"},
        {"to": ASSIGNED, "note": "giao", "assignee": "an"},
        {"to": CREATED, "note": ""},
        {"to": ASSIGNED, "note": "", "assignee": "an"},
        {"to": COLLECTED, "note": "", "borrower": "binh"},
        {"to": ON_FLOOR, "note": ""},
        {"to": DEACTIVATED, "note": "xong", "returned_by": "chi"},
    ]


def test_history_has_no_name_the_caller_did_not_pass():
    sm = make()
    box_id = sm.create_box(payload())
    sm.transition(box_id, ASSIGNED, assignee="an", borrower="khong-ai-dung-den")
    history = sm.get_box(box_id)["history"]
    assert "khong-ai-dung-den" not in repr(history)


@pytest.mark.parametrize("status,actions,hand_over", [
    (CREATED, [ASSIGNED], False),
    (ASSIGNED, [CREATED, COLLECTED], True),
    (COLLECTED, [ON_FLOOR, DEACTIVATED], True),
    (ON_FLOOR, [DEACTIVATED], True),
    (DEACTIVATED, [], False),
])
def test_next_actions_and_may_hand_over(status, actions, hand_over):
    sm = make()
    box = sm.get_box(box_in(sm, status))
    assert box["next_actions"] == actions
    assert box["may_hand_over"] is hand_over
    # Every listed action is a move the fake really accepts.
    for to in actions:
        probe = make()
        probe_id = box_in(probe, status)
        assert probe.transition(probe_id, to, **FIELDS[to])["status"] == to


# Borrow now

def test_borrow_now_borrows_in_one_step():
    sm = make()
    box = sm.get_box(sm.create_box(payload(borrow_now=True, borrower="binh")))
    assert box["status"] == COLLECTED
    assert box["status_label"] == "Borrowed"
    assert box["holder"] == "binh"
    assert box["short_count"] == 0
    assert [h["to"] for h in box["history"]] == [CREATED, COLLECTED]
    assert sm.available(CHAIR) == 3 and sm.available(LAMP) == 9


def test_borrow_now_with_assignee_ends_collected_and_keeps_assignee():
    sm = make()
    box = sm.get_box(sm.create_box(payload(borrow_now=True, borrower="binh", assignee="an")))
    assert box["status"] == COLLECTED and box["assignee"] == "an" and box["holder"] == "binh"
    assert [h["to"] for h in box["history"]] == [CREATED, ASSIGNED, COLLECTED]


def test_borrow_now_needs_a_borrower_and_makes_no_box():
    sm = make()
    err = refusal(sm.create_box, payload(borrow_now=True))
    assert err.code == "invalid"
    assert sm.list_boxes() == []
    assert sm.create_box(payload()) == "MBX-0001"


def test_borrow_now_with_short_object_makes_box_but_borrows_nothing():
    sm = make()
    box = sm.get_box(sm.create_box(payload(borrow_now=True, borrower="binh",
                                           contents=[{"object_id": CHAIR, "quantity": 2},
                                                     {"object_id": TABLE, "quantity": 3}])))
    assert box["status"] == CREATED
    assert box["holder"] is None
    assert [(c["object_id"], c["short"]) for c in box["contents"]] == [(CHAIR, False), (TABLE, True)]
    assert box["short_count"] == 1
    assert box["history"] == [{"to": CREATED, "note": "ghi chú"}]
    assert sm.available(CHAIR) == 5 and sm.available(TABLE) == 1


def test_borrow_now_with_short_object_and_assignee_stays_assigned():
    sm = make()
    box = sm.get_box(sm.create_box(payload(borrow_now=True, borrower="binh", assignee="an",
                                           contents=[{"object_id": TABLE, "quantity": 2}])))
    assert box["status"] == ASSIGNED
    assert box["holder"] is None
    assert box["short_count"] == 1
    assert sm.available(TABLE) == 1


def test_borrow_move_is_refused_while_short_and_names_the_short_object():
    sm = make()
    box_id = sm.create_box(payload(title="Cần bàn", contents=[{"object_id": CHAIR, "quantity": 1},
                                                              {"object_id": TABLE, "quantity": 3}]))
    sm.transition(box_id, ASSIGNED, assignee="an")
    err = refusal(sm.transition, box_id, COLLECTED, borrower="binh")
    assert err.code == "short"
    assert "Bàn" in err.message and TABLE in err.message and "cần 3" in err.message and "còn 1" in err.message
    assert "Ghế" not in err.message
    box = sm.get_box(box_id)
    assert box["status"] == ASSIGNED and sm.available(CHAIR) == 5


def test_borrow_move_works_once_stock_is_back():
    sm = make()
    first = box_in(sm, COLLECTED, title="Một", contents=[{"object_id": TABLE, "quantity": 1}])
    second = sm.create_box(payload(title="Hai", contents=[{"object_id": TABLE, "quantity": 1}]))
    sm.transition(second, ASSIGNED, assignee="an")
    assert refusal(sm.transition, second, COLLECTED, borrower="binh").code == "short"
    sm.transition(first, DEACTIVATED, returned_by="chi")
    assert sm.transition(second, COLLECTED, borrower="binh")["status"] == COLLECTED


# Availability

def test_availability_drops_when_borrowed_and_returns_when_returned():
    sm = make()
    first = box_in(sm, ASSIGNED, title="Một", contents=[{"object_id": CHAIR, "quantity": 3}])
    # Not borrowed yet: nothing is held.
    assert sm.available(CHAIR) == 5
    sm.transition(first, COLLECTED, borrower="binh")
    assert sm.available(CHAIR) == 2
    wants_more = sm.get_box(sm.create_box(payload(title="Hai", contents=[{"object_id": CHAIR, "quantity": 3}])))
    assert wants_more["contents"][0]["available"] == 2
    assert wants_more["contents"][0]["short"] is True
    sm.transition(first, ON_FLOOR)
    assert sm.available(CHAIR) == 2
    sm.transition(first, DEACTIVATED, returned_by="chi")
    assert sm.available(CHAIR) == 5
    refreshed = sm.get_box(wants_more["box_id"])
    assert refreshed["contents"][0]["available"] == 5
    assert refreshed["contents"][0]["short"] is False
    assert refreshed["short_count"] == 0


def test_returning_straight_from_borrowed_frees_stock():
    sm = make()
    box_id = box_in(sm, COLLECTED)
    assert sm.available(LAMP) == 9
    sm.transition(box_id, DEACTIVATED, returned_by="chi")
    assert sm.available(LAMP) == 10


def test_short_flags_apply_only_before_borrowing():
    sm = make()
    box_id = sm.create_box(payload(title="Bàn", contents=[{"object_id": TABLE, "quantity": 1}]))
    sm.transition(box_id, ASSIGNED, assignee="an")
    sm.transition(box_id, COLLECTED, borrower="binh")
    box = sm.get_box(box_id)
    assert box["contents"][0]["available"] == 0
    assert box["contents"][0]["short"] is False
    assert box["short_count"] == 0


def test_available_of_unknown_object_is_zero():
    assert make().available("999999") == 0


# Duplicate titles

def test_duplicate_title_refused_until_first_is_returned():
    sm = make()
    first = sm.create_box(payload(title="Hộp trùng"))
    for status in (CREATED, ASSIGNED, COLLECTED, ON_FLOOR):
        err = refusal(sm.create_box, payload(title="Hộp trùng"))
        assert err.code == "invalid" and "Hộp trùng" in err.message
        nxt = {CREATED: ASSIGNED, ASSIGNED: COLLECTED, COLLECTED: ON_FLOOR}.get(status)
        if nxt:
            sm.transition(first, nxt, **FIELDS[nxt])
    assert refusal(sm.create_box, payload(title="Hộp trùng")).code == "invalid"
    sm.transition(first, DEACTIVATED, returned_by="chi")
    assert sm.create_box(payload(title="Hộp trùng")) == "MBX-0002"
    assert refusal(sm.create_box, payload(title="Hộp trùng")).code == "invalid"


def test_duplicate_title_ignores_case_and_outer_spaces():
    sm = make()
    sm.create_box(payload(title="Hộp A"))
    assert refusal(sm.create_box, payload(title="  hộp a ")).code == "invalid"


def test_refused_create_does_not_use_up_an_id():
    sm = make()
    sm.create_box(payload(title="A"))
    refusal(sm.create_box, payload(title="A"))
    assert sm.create_box(payload(title="B")) == "MBX-0002"


# Payload validation

@pytest.mark.parametrize("body", [
    None,
    [],
    "hộp",
    payload(title=""),
    payload(title="   "),
    payload(title=None),
    payload(title=5),
    payload(contents=[]),
    payload(contents=None),
    payload(contents="100001"),
    payload(contents=["100001"]),
    payload(contents=[{"object_id": CHAIR}]),
    payload(contents=[{"quantity": 1}]),
    payload(contents=[{"object_id": CHAIR, "quantity": 0}]),
    payload(contents=[{"object_id": CHAIR, "quantity": -2}]),
    payload(contents=[{"object_id": CHAIR, "quantity": 1.5}]),
    payload(contents=[{"object_id": CHAIR, "quantity": "2"}]),
    payload(contents=[{"object_id": CHAIR, "quantity": True}]),
    payload(contents=[{"object_id": "12345", "quantity": 1}]),
    payload(contents=[{"object_id": "1234567", "quantity": 1}]),
    payload(contents=[{"object_id": "12a456", "quantity": 1}]),
    payload(contents=[{"object_id": 100001, "quantity": 1}]),
    payload(contents=[{"object_id": "999999", "quantity": 1}]),
    payload(contents=[{"object_id": CHAIR, "quantity": 1}, {"object_id": CHAIR, "quantity": 1}]),
    payload(contents=[{"object_id": CHAIR, "quantity": 1, "name": "Ghế"}]),
    payload(anchor_object_id="123"),
    payload(anchor_object_id="999999"),
    payload(anchor_object_id=100001),
    payload(note=5),
    payload(borrow_now="yes", borrower="binh"),
    payload(assignee=5),
    payload(assignee=["an"]),
    payload(borrow_now=True, borrower=7),
    payload(colour="red"),
])
def test_invalid_payloads_are_refused_and_nothing_is_stored(body):
    sm = make()
    err = refusal(sm.create_box, body)
    assert err.code == "invalid"
    assert err.message and str(err) == err.message
    assert sm.list_boxes() == []


def test_blank_anchor_and_blank_assignee_count_as_missing():
    sm = make()
    box = sm.get_box(sm.create_box(payload(anchor_object_id="", assignee="  ")))
    assert box["anchor_object_id"] is None
    assert box["status"] == CREATED


def test_title_is_stored_trimmed():
    sm = make()
    assert sm.get_box(sm.create_box(payload(title="  Hộp A  ")))["title"] == "Hộp A"


# Lookup and listing

def test_list_boxes_filters_by_status_in_creation_order():
    sm = make()
    a = box_in(sm, CREATED, title="A")
    b = box_in(sm, ASSIGNED, title="B")
    c = box_in(sm, ASSIGNED, title="C")
    d = box_in(sm, DEACTIVATED, title="D", contents=[{"object_id": LAMP, "quantity": 1}])
    ids = lambda boxes: [x["box_id"] for x in boxes]
    assert ids(sm.list_boxes()) == [a, b, c, d]
    assert ids(sm.list_boxes(None)) == [a, b, c, d]
    assert ids(sm.list_boxes(ASSIGNED)) == [b, c]
    assert ids(sm.list_boxes(DEACTIVATED)) == [d]
    assert sm.list_boxes(ON_FLOOR) == []


def test_list_boxes_unknown_status_is_invalid():
    assert refusal(make().list_boxes, "flying").code == "invalid"


@pytest.mark.parametrize("box_id", ["MBX-0099", "", None, 5, ["MBX-0001"]])
def test_unknown_box_id_is_missing(box_id):
    sm = make()
    sm.create_box(payload())
    assert refusal(sm.get_box, box_id).code == "missing"
    assert refusal(sm.transition, box_id, ASSIGNED, assignee="an").code == "missing"


def test_missing_message_names_the_box():
    assert "MBX-0099" in refusal(make().get_box, "MBX-0099").message


# Copies and separate instances

def test_returned_boxes_are_deep_copies():
    sm = make()
    box_id = sm.create_box(payload())
    box = sm.get_box(box_id)
    box["title"] = "đổi"
    box["status"] = DEACTIVATED
    box["contents"][0]["quantity"] = 99
    box["contents"].append({"object_id": TABLE, "quantity": 1})
    box["history"].append({"to": "x", "note": "x"})
    box["next_actions"].append("lạ")
    listed = sm.list_boxes()
    listed[0]["contents"][0]["name"] = "đổi"
    listed.clear()
    moved = sm.transition(box_id, ASSIGNED, assignee="an")
    moved["history"].clear()
    moved["contents"].clear()
    fresh = sm.get_box(box_id)
    assert fresh["title"] == "Hộp A" and fresh["status"] == ASSIGNED
    assert fresh["contents"][0]["quantity"] == 2 and len(fresh["contents"]) == 2
    assert fresh["contents"][0]["name"] == "Ghế"
    assert len(fresh["history"]) == 2
    assert fresh["next_actions"] == [CREATED, COLLECTED]
    assert len(sm.list_boxes()) == 1


def test_changing_the_payload_or_inventory_afterwards_changes_nothing():
    inventory = dict(INVENTORY)
    sm = MockSM(inventory, NAMES)
    body = payload()
    box_id = sm.create_box(body)
    body["title"] = "đổi"
    body["contents"][0]["quantity"] = 99
    inventory[CHAIR] = 0
    box = sm.get_box(box_id)
    assert box["title"] == "Hộp A"
    assert box["contents"][0]["quantity"] == 2
    assert sm.available(CHAIR) == 5


def test_two_instances_do_not_share_state():
    one, two = make(), make()
    box_id = one.create_box(payload(borrow_now=True, borrower="binh"))
    assert two.list_boxes() == []
    assert refusal(two.get_box, box_id).code == "missing"
    assert one.available(CHAIR) == 3 and two.available(CHAIR) == 5
    assert two.create_box(payload()) == "MBX-0001"
    assert len(one.list_boxes()) == 1


# Timestamps and determinism

def _flow(sm):
    out = []
    a = sm.create_box(payload(title="A"))
    b = sm.create_box(payload(title="B", assignee="an", borrow_now=True, borrower="binh"))
    sm.transition(a, ASSIGNED, assignee="an")
    sm.transition(a, COLLECTED, borrower="binh")
    sm.transition(b, ON_FLOOR)
    out.append(sm.list_boxes())
    refusal(sm.transition, a, CREATED)
    out.append(sm.transition(a, DEACTIVATED, returned_by="chi"))
    out.append(sm.list_boxes(DEACTIVATED))
    return out


def test_same_calls_give_same_ids_and_timestamps():
    assert _flow(make()) == _flow(make())


def test_default_created_at_is_a_counter_not_a_clock():
    sm = make()
    first = sm.get_box(sm.create_box(payload(title="A")))["created_at"]
    second = sm.get_box(sm.create_box(payload(title="B")))["created_at"]
    assert first == "2026-01-01T00:00:01Z"
    assert second == "2026-01-01T00:00:02Z"


def test_reading_and_moving_do_not_advance_the_timestamp_counter():
    sm = make()
    first = sm.create_box(payload(title="A"))
    sm.get_box(first)
    sm.list_boxes()
    sm.transition(first, ASSIGNED, assignee="an")
    assert sm.get_box(sm.create_box(payload(title="B")))["created_at"] == "2026-01-01T00:00:02Z"


def test_clock_callable_is_used_for_created_at():
    ticks = iter(["t1", "t2"])
    sm = MockSM(INVENTORY, NAMES, clock=lambda: next(ticks))
    a = sm.create_box(payload(title="A"))
    b = sm.create_box(payload(title="B"))
    assert sm.get_box(a)["created_at"] == "t1"
    assert sm.get_box(b)["created_at"] == "t2"


# The abstract client and the error type

def test_mock_is_an_sm_client_and_the_base_cannot_be_built():
    assert isinstance(make(), SMClient)
    with pytest.raises(TypeError):
        SMClient()

    class Partial(SMClient):
        def create_box(self, payload):
            return "x"

    with pytest.raises(TypeError):
        Partial()


def test_sm_error_has_code_and_plain_message():
    err = SMError("bad_move")
    assert err.code == "bad_move" and err.message and str(err) == err.message
    custom = SMError("missing", "Không có hộp.")
    assert custom.code == "missing" and custom.message == "Không có hộp." and str(custom) == "Không có hộp."
    assert SMError("lạ").message


def test_every_message_follows_the_writing_rules():
    """No em dashes, no exclamation marks (docs/WRITING.md, rule 9). Collects messages from real refusals."""
    sm = make()
    taken = sm.create_box(payload(title="Có rồi", contents=[{"object_id": TABLE, "quantity": 1}]))
    sm.transition(taken, ASSIGNED, assignee="an")
    sm.transition(taken, COLLECTED, borrower="binh")
    short = sm.create_box(payload(title="Thiếu", contents=[{"object_id": TABLE, "quantity": 2}]))
    sm.transition(short, ASSIGNED, assignee="an")
    calls = [
        lambda: sm.create_box(None), lambda: sm.create_box(payload(colour="x")),
        lambda: sm.create_box(payload(title="")), lambda: sm.create_box(payload(contents=[])),
        lambda: sm.create_box(payload(contents=[{"object_id": CHAIR, "quantity": 0}])),
        lambda: sm.create_box(payload(contents=[{"object_id": "1", "quantity": 1}])),
        lambda: sm.create_box(payload(contents=[{"object_id": "999999", "quantity": 1}])),
        lambda: sm.create_box(payload(contents=[{"object_id": CHAIR, "quantity": 1}] * 2)),
        lambda: sm.create_box(payload(title="Có rồi")),
        lambda: sm.create_box(payload(borrow_now=True)),
        lambda: sm.create_box(payload(note=1)),
        lambda: sm.get_box("MBX-9999"),
        lambda: sm.list_boxes("x"),
        lambda: sm.transition(short, COLLECTED, borrower="binh"),
        lambda: sm.transition(short, ON_FLOOR),
        lambda: sm.transition(short, ASSIGNED, wrong="x"),
        lambda: sm.transition(taken, DEACTIVATED),
        lambda: sm.transition(short, CREATED, note=5),
    ]
    messages = [refusal(call).message for call in calls]
    assert len(messages) == len(calls)
    for message in messages:
        assert message and "\u2014" not in message and "!" not in message
    for code in ("bad_move", "missing", "invalid", "short", "other"):
        assert "\u2014" not in SMError(code).message and "!" not in SMError(code).message


# No network

def test_full_flow_never_opens_a_socket(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the fake SM tried to use the network")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket, "gethostbyname", forbidden)

    sm = make()
    a = sm.create_box(payload(title="A", borrow_now=True, borrower="binh"))
    b = sm.create_box(payload(title="B", contents=[{"object_id": TABLE, "quantity": 2}], assignee="an",
                              borrow_now=True, borrower="binh"))
    sm.transition(a, ON_FLOOR)
    sm.transition(a, DEACTIVATED, returned_by="chi")
    sm.transition(b, CREATED)
    sm.transition(b, ASSIGNED, assignee="an")
    refusal(sm.transition, b, COLLECTED, borrower="binh")
    sm.list_boxes()
    sm.get_box(a)
    assert sm.get_box(a)["status"] == DEACTIVATED
    with pytest.raises(AssertionError):  # the guard itself works
        socket.socket()


def test_module_imports_only_pure_standard_library():
    path = os.path.join(os.path.dirname(sm_client.__file__), "sm_client.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= {"__future__", "copy", "re", "abc", "datetime", "typing"}, imported
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"open", "input", "exec", "eval", "__import__"}

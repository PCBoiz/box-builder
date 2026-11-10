"""How old the SM boxes are, and what each choice of days for the reuse switch would free (service.sm_ages, /api/sm/boxes/report?ages=1)."""
import time

from app import service
from tests.test_automation import make_client
from tests.test_backend import conn_of, env  # noqa: F401  (env is a fixture)
from tests.test_reuse_old import box_with, day, iso, set_reuse, snapshot
from tests.test_sm_boxes import sm_box, usable_ids

DAY = 86400


def ages(client):
    r = client.get("/api/sm/boxes/report?ages=1")
    assert r.status_code == 200, r.text
    return r.json()["ages"]


def option(a, days):
    return next(o for o in a["options"] if o["days"] == days)


def test_the_report_has_no_ages_unless_asked_for(env):
    client, s, cat = make_client(env)
    assert "ages" not in client.get("/api/sm/boxes/report").json()
    assert "ages" not in client.get("/api/sm/boxes/report?ages=0").json()
    assert "ages" in client.get("/api/sm/boxes/report?ages=1").json()
    assert client.get("/api/sm/boxes/report?ages=maybe").status_code == 422


def test_boxes_fall_into_the_bands_by_age_and_unreadable_dates_are_counted_apart(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    snapshot(client,
             sm_box("D3", "3 days", [ids[0]], created_at=iso(3)), sm_box("D5", "5 days", [ids[1]], created_at=iso(5)),
             sm_box("D10", "10 days", [ids[2]], created_at=iso(10)),
             sm_box("D20", "20 days", [ids[3]], created_at=iso(20)), sm_box("D25", "25 days", [ids[4]], created_at=iso(25)), sm_box("D28", "28 days", [ids[5]], created_at=iso(28)),
             sm_box("D45", "45 days", [ids[6]], created_at=iso(45)),
             sm_box("D75", "75 days", [ids[7]], created_at=iso(75)),
             sm_box("D200", "200 days", [ids[8]], created_at=iso(200)), sm_box("D400", "400 days", [ids[9]], created_at=iso(400)),
             sm_box("ODD", "odd date", [ids[10]], created_at="hôm kia"), sm_box("NONE", "no date", [ids[11]], created_at=""),
             sm_box("FUT", "a day ahead of the clock", [ids[12]], created_at=iso(-1)))
    a = ages(client)
    assert [(b["from"], b["to"], b["boxes"]) for b in a["bands"]] == [(0, 7, 3), (7, 14, 1), (14, 30, 3), (30, 60, 1), (60, 90, 1), (90, None, 2)]
    assert a["boxes"] == 13 and a["undated"] == 2


def test_boxes_with_no_object_and_boxes_made_by_the_app_are_not_counted(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    snapshot(client, sm_box("OLD", "old", [ids[0]], created_at=iso(50)), sm_box("EMPTY", "holds nothing", [], created_at=iso(50)),
             sm_box("OURS", "made by the app", [ids[1]], created_at=iso(50)))
    pushed = box_with(s, [ids[1]], label="Box P", status="approved")
    conn_of(s).execute("INSERT INTO sm_push(box_id, sm_box_id, pushed_at) VALUES(?,?,?)", (pushed, "OURS", time.time()))
    a = ages(client)
    assert a["boxes"] == 1 and sum(b["boxes"] for b in a["bands"]) == 1 and a["undated"] == 0


def test_the_table_lists_the_five_choices_and_the_one_in_use_when_it_is_not_among_them(env):
    client, s, cat = make_client(env)
    snapshot(client, sm_box("A", "a", usable_ids(cat)[:2], created_at=iso(10)))
    a = ages(client)
    assert [o["days"] for o in a["options"]] == [7, 14, 30, 60, 90] and [o["days"] for o in a["options"] if o["current"]] == [30]
    set_reuse(client, days=45)
    a = ages(client)
    assert [o["days"] for o in a["options"]] == [7, 14, 30, 45, 60, 90] and [o["days"] for o in a["options"] if o["current"]] == [45]
    set_reuse(client, enabled=False)
    off = ages(client)
    assert not any(o["current"] for o in off["options"]) and [o["days"] for o in off["options"]] == [7, 14, 30, 45, 60, 90]   # still shown, none in use


def test_each_row_says_how_many_boxes_stop_holding_and_how_many_objects_come_free(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    snapshot(client, sm_box("W2", "20 days", ids[0:5], created_at=iso(20)),          # older than 7 and 14 days
             sm_box("M2", "45 days", ids[5:12], created_at=iso(45)),                  # also older than 30
             sm_box("N", "3 days", ids[12:15], created_at=iso(3)))                    # never old enough
    a = ages(client)
    assert (option(a, 7)["old_boxes"], option(a, 14)["old_boxes"], option(a, 30)["old_boxes"], option(a, 60)["old_boxes"]) == (2, 2, 1, 0)
    assert option(a, 7)["extra_free"] == 12 and option(a, 30)["extra_free"] == 7 and option(a, 60)["extra_free"] == 0
    assert option(a, 7)["free_objects"] == a["free_without_reuse"] + 12
    assert option(a, 90)["free_objects"] == a["free_without_reuse"]                 # nothing is that old, so nothing is gained


def test_an_object_in_one_old_and_one_recent_box_is_not_freed_by_any_choice(env):
    client, s, cat = make_client(env)
    both, only_old = usable_ids(cat)[:2]
    snapshot(client, sm_box("OLD", "old", [both, only_old], created_at=iso(80)), sm_box("NEW", "new", [both], created_at=iso(2)))
    a = ages(client)
    assert option(a, 7)["extra_free"] == 1 and option(a, 7)["old_boxes"] == 1       # only_old comes free, `both` is still in the recent box
    assert option(a, 90)["extra_free"] == 0


def test_unreadable_dates_keep_their_objects_for_every_choice(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    snapshot(client, sm_box("ODD", "odd", [ids[0]], created_at="hôm kia"), sm_box("NONE", "none", [ids[1]], created_at=""),
             sm_box("PLACE", "placeholder", [ids[2]], created_at="1970-01-01T00:00:00Z"))
    a = ages(client)
    assert all(o["extra_free"] == 0 and o["old_boxes"] == 0 for o in a["options"]) and a["undated"] == 3


def test_every_row_matches_what_the_real_rule_gives_with_the_switch_set_to_that_many_days(env):
    """The table is worked out in one pass over the data. This test pins it to taken_ids(), which the page and the picker really use,
    on a mixture of everything that can hold an object: old and recent SM boxes, an undated box, a box of this app, a box this app
    pushed to SM, sheet IDs with a day, sheet IDs with none."""
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    snapshot(client,
             sm_box("A", "a", ids[0:6], created_at=iso(9)), sm_box("B", "b", ids[4:10], created_at=iso(21)), sm_box("C", "c", ids[10:16], created_at=iso(40)),
             sm_box("D", "d", ids[16:22], created_at=iso(70)), sm_box("E", "e", ids[20:26], created_at=iso(130)),
             sm_box("U", "undated", ids[26:29], created_at="?"), sm_box("OURS", "ours", ids[29:31], created_at=iso(60)))
    box_with(s, ids[2:3] + ids[12:13] + ids[31:33])                                                    # held by a draft in this app
    pushed = box_with(s, ids[29:30], label="Box P", status="approved")
    conn_of(s).execute("INSERT INTO sm_push(box_id, sm_box_id, pushed_at) VALUES(?,?,?)", (pushed, "OURS", time.time()))
    client.post("/api/used", json={"text": " ".join(ids[33:38]), "since": day(50)})                  # the sheet, with a day
    client.post("/api/used", json={"text": " ".join(ids[38:42]), "since": day(8)})
    client.post("/api/used", json={"text": " ".join(ids[42:45])})                                      # today
    conn_of(s).execute("UPDATE external_used SET added_at=0 WHERE object_id IN (?,?)", (ids[45], ids[45]))   # no day known
    client.post("/api/used", json={"text": ids[45]})
    conn_of(s).execute("UPDATE external_used SET added_at=0 WHERE object_id=?", (ids[45],))
    a = ages(client)
    usable = set(cat.items())
    assert a["free_without_reuse"] == len(usable - service.taken_ids(conn_of(s), None))
    for o in a["options"]:
        c = conn_of(s)
        set_reuse(client, enabled=True, days=o["days"])
        cutoff = service.reuse_cutoff(c)
        assert o["free_objects"] == len(usable - service.taken_ids(c, cutoff)), o
        rep = client.get("/api/sm/boxes/report").json()
        assert o["old_boxes"] == rep["reuse"]["old_boxes"], o
        assert o["extra_free"] == o["free_objects"] - a["free_without_reuse"], o


def test_the_gain_is_the_same_number_the_reuse_line_gives_when_the_sheet_holds_nothing(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    snapshot(client, sm_box("A", "a", ids[:4], created_at=iso(60)), sm_box("B", "b", ids[2:6], created_at=iso(45)), sm_box("C", "c", ids[6:8], created_at=iso(2)))
    rep = client.get("/api/sm/boxes/report?ages=1").json()
    assert option(rep["ages"], 30)["extra_free"] == rep["reuse"]["freed_objects"] == 6
    assert option(rep["ages"], 30)["old_boxes"] == rep["reuse"]["old_boxes"] == 2


def test_an_empty_snapshot_gives_empty_bands_and_the_sheet_alone_still_moves_the_numbers(env):
    client, s, cat = make_client(env)
    a = ages(client)
    assert a["boxes"] == 0 and a["undated"] == 0 and all(b["boxes"] == 0 for b in a["bands"]) and all(o["old_boxes"] == 0 for o in a["options"])
    client.post("/api/used", json={"text": " ".join(usable_ids(cat)[:3]), "since": day(20)})
    a = ages(client)
    assert option(a, 14)["extra_free"] == 3 and option(a, 30)["extra_free"] == 0         # sheet IDs 20 days old: free at 14 days, held at 30


def test_the_table_does_not_change_anything(env):
    client, s, cat = make_client(env)
    snapshot(client, sm_box("A", "a", usable_ids(cat)[:3], created_at=iso(60)))
    before = client.get("/api/state").json()
    ages(client)
    ages(client)
    after = client.get("/api/state").json()
    assert before["rev"] == after["rev"] and before["stats"] == after["stats"]

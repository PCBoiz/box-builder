"""A used object is free again when the box that used it is far enough in the past (the "Dùng lại món của hộp cũ" switch)."""
import time

import pytest

from app import service
from tests.test_automation import make_client
from tests.test_backend import conn_of, env  # noqa: F401  (env is a fixture)
from tests.test_backup import other_app
from tests.test_sm_boxes import TOK, sm_box, usable_ids

DAY = 86400


def iso(days_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * DAY))


def day(days_ago):
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days_ago * DAY + 7 * 3600))      # the calendar day in Vietnam time, as the app shows it


def snapshot(client, *boxes):
    r = client.post("/api/sm/boxes/sync", json={"boxes": list(boxes)}, headers=TOK)
    assert r.status_code == 200, r.text
    return r.json()


def status_of(client, oid):
    return next(o["status"] for o in client.get(f"/api/objects?q={oid}&per=50").json()["items"] if o["id"] == oid)


def set_reuse(client, enabled=None, days=None):
    body = {}
    if enabled is not None:
        body["enabled"] = enabled
    if days is not None:
        body["config"] = {"days": days}
    r = client.put("/api/automations/reuse_old", json=body)
    assert r.status_code == 200, r.text


def test_the_switch_exists_is_on_by_default_with_thirty_days_and_takes_one_to_365(env):
    client, s, _ = make_client(env)
    sw = {x["key"]: x for x in client.get("/api/automations").json()["switches"]}["reuse_old"]
    assert sw["enabled"] is True and sw["config"] == {"days": 30} and sw["limits"] == {"days": [1, 365]}
    assert client.put("/api/automations/reuse_old", json={"config": {"days": 0}}).status_code == 422
    assert client.put("/api/automations/reuse_old", json={"config": {"days": 366}}).status_code == 422
    set_reuse(client, days=7)
    assert {x["key"]: x for x in client.get("/api/automations").json()["switches"]}["reuse_old"]["config"] == {"days": 7}


def test_a_date_is_read_in_the_ways_sm_writes_it_and_anything_else_is_unknown():
    for text in ("2026-11-10T08:00:00Z", "2026-11-10T08:00:00.123456Z", "2026-11-10T08:00:00+07:00", "2026-11-10 08:00:00", "2026-11-10"):
        assert isinstance(service.when_of(text), float), text
    assert service.when_of("2026-11-10T08:00:00Z") == service.when_of("2026-11-10T15:00:00+07:00")
    assert service.when_of("2026-11-10") == service.when_of("2026-11-10T00:00:00+07:00")             # no zone: Vietnam local time, as the real SM writes created_at
    assert service.when_of("2026-11-11 17:13") == service.when_of("2026-11-11T10:13:00Z")            # the real SM shape: date and minutes, no seconds, no zone
    for bad in ("", None, "11-10", "hôm qua", "10/11/2026", "2026-13-40", 12345):
        assert service.when_of(bad) is None, bad


def test_objects_of_an_old_sm_box_are_free_and_those_of_a_recent_box_are_not(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    old_ids, new_ids = ids[:3], ids[3:6]
    snapshot(client, sm_box("OLD", "A1 old", old_ids, created_at=iso(40)), sm_box("NEW", "A1 new", new_ids, created_at=iso(5)))
    for oid in old_ids:
        assert status_of(client, oid) == "free"                    # a 40 day old box, the default is 30 days
    for oid in new_ids:
        assert status_of(client, oid) == "sm"
    st = client.get("/api/state").json()["stats"]
    assert st["in_sm"] == 3
    # the picker is told the same thing: nothing from the recent box, the old objects may come back
    held = service.sm_held(conn_of(s))
    assert set(held) == set(new_ids)
    assert set(new_ids) <= service.taken_ids(conn_of(s)) and not set(old_ids) & service.taken_ids(conn_of(s))


def test_the_number_of_days_is_the_limit_and_switching_off_keeps_everything_held(env):
    client, s, cat = make_client(env)
    a, b, c = usable_ids(cat)[:3], usable_ids(cat)[3:6], usable_ids(cat)[6:9]
    snapshot(client, sm_box("W", "week old", a, created_at=iso(8)), sm_box("M", "month old", b, created_at=iso(31)), sm_box("T", "three days", c, created_at=iso(3)))
    assert [status_of(client, o) for o in (a[0], b[0], c[0])] == ["sm", "free", "sm"]               # 30 days
    set_reuse(client, days=7)
    assert [status_of(client, o) for o in (a[0], b[0], c[0])] == ["free", "free", "sm"]             # one week
    set_reuse(client, days=1)
    assert [status_of(client, o) for o in (a[0], b[0], c[0])] == ["free", "free", "free"]
    set_reuse(client, days=365)
    assert [status_of(client, o) for o in (a[0], b[0], c[0])] == ["sm", "sm", "sm"]
    set_reuse(client, enabled=False, days=1)
    assert [status_of(client, o) for o in (a[0], b[0], c[0])] == ["sm", "sm", "sm"]                 # off: nothing is reused, whatever the days
    set_reuse(client, enabled=True)
    assert [status_of(client, o) for o in (a[0], b[0], c[0])] == ["free", "free", "free"]


def test_an_object_in_one_old_and_one_recent_box_stays_held_and_an_unreadable_date_stays_held(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    both, odd = ids[0], ids[1]
    snapshot(client, sm_box("OLD", "old", [both], created_at=iso(90)), sm_box("NEW", "new", [both], created_at=iso(1)),
             sm_box("ODD", "odd date", [odd], created_at="hôm kia"), sm_box("NONE", "no date", [ids[2]], created_at=""))
    assert status_of(client, both) == "sm" and status_of(client, odd) == "sm" and status_of(client, ids[2]) == "sm"
    rep = client.get("/api/sm/boxes/report").json()["reuse"]
    assert rep == {"on": True, "days": 30, "old_boxes": 1, "undated_boxes": 2, "freed_objects": 0}      # `both` is still held by the recent box


def test_the_sm_report_counts_what_the_rule_frees_and_old_boxes_make_no_doubles(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    snapshot(client, sm_box("A", "a", ids[:4], created_at=iso(60)), sm_box("B", "b", ids[2:6], created_at=iso(45)), sm_box("C", "c", ids[6:8], created_at=iso(2)))
    rep = client.get("/api/sm/boxes/report").json()
    assert rep["reuse"] == {"on": True, "days": 30, "old_boxes": 2, "undated_boxes": 0, "freed_objects": 6}
    assert rep["objects_held"] == 2 and rep["doubles"] == []                       # ids[2:4] sat in two old boxes: not a double any more
    set_reuse(client, enabled=False)
    off = client.get("/api/sm/boxes/report").json()
    assert off["reuse"]["on"] is False and off["reuse"]["freed_objects"] == 0 and off["objects_held"] == 8 and len(off["doubles"]) == 2


def _make_box(client, label, count=21):
    bid = client.post("/api/boxes", json={"module": "M7", "label": label, "theme": "test", "count": count}).json()["id"]
    for _ in range(100):
        b = next(x for x in client.get("/api/state").json()["boxes"] if x["id"] == bid)
        if b["status"] != "generating":
            return [i["id"] for i in b["items"]]
        time.sleep(0.1)
    raise AssertionError("the box never finished")


def test_a_box_in_the_app_can_take_objects_of_an_old_sm_box_but_never_of_a_new_one(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    spare, new, old = ids[-10:], ids[:60], ids[60:-10]       # old boxes hold nearly everything: a 21-object box cannot be filled from the spare ten alone
    snapshot(client, sm_box("NEW", "new", new, created_at=iso(2)),
             *[sm_box(f"OLD{n}", f"old {n}", old[n * 150:(n + 1) * 150], created_at=iso(50)) for n in range((len(old) + 149) // 150)])
    with_rule = _make_box(client, "Box B2")
    assert len(with_rule) == 21 and not set(with_rule) & set(new)                         # never an object of the recent box
    assert len(set(with_rule) & set(old)) >= 11, "objects of the old boxes should be available again"
    set_reuse(client, enabled=False)                                                      # switched off, the old boxes hold their objects again
    without_rule = _make_box(client, "Box B3")
    assert len(without_rule) <= 10 and set(without_rule) <= set(spare)


def test_sheet_ids_with_a_date_expire_and_ids_without_one_never_do(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    old, fresh, unknown = ids[:3], ids[3:6], ids[6:9]
    client.post("/api/used", json={"text": " ".join(old), "note": "M3", "since": day(45)})
    client.post("/api/used", json={"text": " ".join(fresh), "note": "M9"})                       # today
    conn = conn_of(s)
    for oid in unknown:
        conn.execute("INSERT INTO external_used(object_id, note) VALUES(?,?)", (oid, "old row, no date"))   # added_at stays 0
    assert [status_of(client, o) for o in old] == ["free"] * 3
    assert [status_of(client, o) for o in fresh] == ["external"] * 3
    assert [status_of(client, o) for o in unknown] == ["external"] * 3
    used = {u["id"]: u for u in client.get("/api/state").json()["used"]}
    assert used[old[0]]["since"] == day(45) and used[old[0]]["reusable"] is True
    assert used[fresh[0]]["since"] == day(0) and used[fresh[0]]["reusable"] is False
    assert used[unknown[0]]["since"] == "" and used[unknown[0]]["reusable"] is False
    assert client.get("/api/state").json()["stats"]["external"] == 6                 # only the ones that still hold back
    set_reuse(client, enabled=False)
    assert [status_of(client, o) for o in old] == ["external"] * 3                    # off: even the 45 day old ones are held again


def test_pasting_an_id_again_with_a_later_day_refreshes_its_date_and_never_goes_backwards(env):
    client, s, cat = make_client(env)
    oid = usable_ids(cat)[0]
    client.post("/api/used", json={"text": oid, "since": day(60)})
    assert status_of(client, oid) == "free"
    r = client.post("/api/used", json={"text": oid, "since": day(2), "note": "again"}).json()
    assert r["added"] == 0 and r["refreshed"] == 1 and status_of(client, oid) == "external"
    r = client.post("/api/used", json={"text": oid, "since": day(90)}).json()                 # an older day does not move it back
    assert r["refreshed"] == 0 and status_of(client, oid) == "external"
    assert {u["id"]: u for u in client.get("/api/state").json()["used"]}[oid]["since"] == day(2)


def test_bad_dates_are_refused_and_nothing_is_written(env):
    client, s, cat = make_client(env)
    oid = usable_ids(cat)[0]
    for bad in ("15/10/2026", "2026-13-01", "yesterday", "2099-01-01"):
        r = client.post("/api/used", json={"text": oid, "since": bad})
        assert r.status_code == 422, bad
    assert client.post("/api/used", json={"text": oid, "since": "x" * 11}).status_code == 422
    assert client.get("/api/state").json()["used"] == []


def test_the_date_survives_a_backup_and_an_older_backup_without_dates_is_held(env, tmp_path):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    client.post("/api/used", json={"text": ids[0], "since": day(45), "note": "old"})
    data = client.get("/api/backup.json").json()
    assert data["used"][0]["added_at"] > 0
    b, bs = other_app(tmp_path)
    b.post("/api/backup/import?dry_run=false", json=data)
    assert {u["id"]: u for u in b.get("/api/state").json()["used"]}[ids[0]]["since"] == day(45)
    assert status_of(b, ids[0]) == "free"
    legacy = {"format": "box-builder-backup", "version": 1, "boxes": [], "automations": [], "used": [{"object_id": ids[1], "note": "no date"}]}
    c2, _ = other_app(tmp_path, name="c.db")
    c2.post("/api/backup/import?dry_run=false", json=legacy)
    assert status_of(c2, ids[1]) == "external"                                         # no date: always held
    assert {u["id"]: u for u in c2.get("/api/state").json()["used"]}[ids[1]]["since"] == ""


def test_a_box_pushed_to_sm_is_not_blocked_by_an_old_sm_box_holding_the_same_object(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    snapshot(client, sm_box("OLD", "old box", ids[:21], created_at=iso(70)))
    rep = client.get("/api/sm/boxes/report").json()
    assert rep["conflicts"] == [] and rep["objects_held"] == 0 and rep["reuse"]["freed_objects"] == 21


def test_an_older_database_gets_the_date_column_and_its_rows_stay_held(env):
    """The live database already has external_used without added_at: starting the new version adds it, old rows get 0 (unknown, never reused)."""
    from app import db
    s, cat = env
    first, second = usable_ids(cat)[:2]
    c = conn_of(s)
    c.execute("DROP TABLE external_used")
    c.execute("CREATE TABLE external_used(object_id TEXT PRIMARY KEY, note TEXT NOT NULL DEFAULT '')")        # the previous release's table
    c.execute("INSERT INTO external_used(object_id, note) VALUES(?, 'from before')", (first,))
    db.init(s.db_path)
    db.init(s.db_path)                                                                                      # starting twice changes nothing
    row = c.execute("SELECT object_id, note, added_at FROM external_used").fetchone()
    assert (row["object_id"], row["note"], row["added_at"]) == (first, "from before", 0)
    if db.is_postgres(s.db_path):                                                                           # REAL would be 4 bytes: epoch seconds need a double
        assert c.execute("SELECT data_type FROM information_schema.columns WHERE table_name='external_used' AND column_name='added_at'").fetchone()[0] == "double precision"
    client, s, cat = make_client(env)
    assert status_of(client, first) == "external"                                                           # unknown day: held, with the switch on
    client.post("/api/used", json={"text": second, "since": day(45)})
    assert status_of(client, second) == "free" and status_of(client, first) == "external"


# ----------------------------------------------------------------------------- problems found by the independent review
def box_with(s, oids, label="Box X", module="M7", status="draft"):
    """A box of this app holding exactly these objects (put in directly, so the test decides which)."""
    c, now = conn_of(s), time.time()
    bid = c.execute("INSERT INTO boxes(module,label,theme,categories,code,anchor,count,status,message,owner,created_at,updated_at,day) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id", (module, label, "", "[]", "", "", len(oids), status, "", "", now, now,
                                                                       service.vn_day(now))).fetchone()[0]
    for n, oid in enumerate(oids, 1):
        c.execute("INSERT INTO box_items(box_id,pos,object_id,qty,locked,source) VALUES(?,?,?,?,0,'manual')", (bid, n, oid, 1))
    return bid


def used_row(client, oid):
    return next((u for u in client.get("/api/state").json()["used"] if u["id"] == oid), None)


def test_placeholder_sm_dates_do_not_free_anything(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    snapshot(client, sm_box("A", "a", [ids[0]], created_at="0001-01-01T00:00:00Z"), sm_box("B", "b", [ids[1]], created_at="1970-01-01T00:00:00Z"),
             sm_box("C", "c", [ids[2]], created_at="1999-12-31T23:59:59Z"), sm_box("D", "d", [ids[3]], created_at="2000-01-01T00:00:00Z"))
    assert [status_of(client, o) for o in ids[:4]] == ["sm", "sm", "sm", "free"]                       # only a real date, however old, frees
    for bad in ("0001-01-01T00:00:00Z", "1970-01-01T00:00:00Z", "1999-12-31T23:59:59Z"):
        assert service.when_of(bad) is None
    assert client.get("/api/sm/boxes/report").json()["reuse"]["undated_boxes"] == 3


def test_a_year_that_cannot_be_right_is_refused_with_a_message_that_says_what_to_do(env):
    client, s, cat = make_client(env)
    oid = usable_ids(cat)[0]
    for bad in ("0026-10-15", "1969-12-31", "1999-12-31"):
        r = client.post("/api/used", json={"text": oid, "since": bad})
        assert r.status_code == 422 and "4 số" in r.json()["detail"], bad
    assert client.get("/api/state").json()["used"] == []
    assert client.post("/api/used", json={"text": oid, "since": "2000-01-01"}).status_code == 200


def test_a_newer_sheet_day_is_kept_even_while_the_id_sits_in_a_box(env):
    client, s, cat = make_client(env)
    x = usable_ids(cat)[0]
    client.post("/api/used", json={"text": x, "since": day(60)})
    assert status_of(client, x) == "free"
    bid = box_with(s, [x])                                                                              # the picker used it while the old row said free
    r = client.post("/api/used", json={"text": x, "since": day(0)}).json()
    assert r["already_in_a_box"] == [x] and r["refreshed"] == 1 and used_row(client, x)["since"] == day(0)
    conn_of(s).execute("DELETE FROM boxes WHERE id=?", (bid,))                                           # the box goes away
    assert status_of(client, x) == "external"                                                           # the sheet's newer use still holds it


def test_an_id_without_a_day_takes_the_one_given_and_a_blank_paste_never_changes_a_listed_id(env):
    client, s, cat = make_client(env)
    x = usable_ids(cat)[0]
    conn_of(s).execute("INSERT INTO external_used(object_id, note) VALUES(?, 'M3 sheet row 5')", (x,))      # an old row: no day (0)
    blank = client.post("/api/used", json={"text": x}).json()
    assert blank["kept"] == 1 and blank["added"] == 0 and blank["refreshed"] == 0 and used_row(client, x)["since"] == ""
    dated = client.post("/api/used", json={"text": x, "since": day(45)}).json()
    assert dated["refreshed"] == 1 and used_row(client, x)["since"] == day(45) and status_of(client, x) == "free"
    assert used_row(client, x)["note"] == "M3 sheet row 5"                                              # an empty note leaves the old one alone
    again = client.post("/api/used", json={"text": x}).json()                                           # blank again: nothing moves
    assert again["kept"] == 1 and used_row(client, x)["since"] == day(45)
    older = client.post("/api/used", json={"text": x, "since": day(90)}).json()                         # an older day never moves it back
    assert older["kept"] == 1 and older["refreshed"] == 0 and used_row(client, x)["since"] == day(45)
    newer = client.post("/api/used", json={"text": x, "since": day(3), "note": "M9"}).json()
    assert newer["refreshed"] == 1 and used_row(client, x)["note"] == "M9" and status_of(client, x) == "external"


def test_the_answer_says_when_the_ids_can_be_used_again(env):
    client, s, cat = make_client(env)
    a, b = usable_ids(cat)[:2]
    r = client.post("/api/used", json={"text": a, "since": day(10)}).json()
    assert r["reusable_on"] == time.strftime("%Y-%m-%d", time.gmtime(time.time() - 10 * DAY + 30 * DAY))   # ten days ago plus thirty
    assert client.post("/api/used", json={"text": b}).json()["reusable_on"] == time.strftime("%Y-%m-%d", time.gmtime(time.time() + 30 * DAY))
    set_reuse(client, enabled=False)
    assert client.post("/api/used", json={"text": usable_ids(cat)[2]}).json()["reusable_on"] == ""      # off: never reusable, so no date


def test_backup_used_rows_refresh_to_a_later_day_and_an_implausible_day_means_unknown(env, tmp_path):
    client, s, cat = make_client(env)
    x, y, z, w = usable_ids(cat)[:4]
    client.post("/api/used", json={"text": x, "since": day(60)})
    assert status_of(client, x) == "free"
    backup = {"format": "box-builder-backup", "version": 1, "boxes": [], "automations": [], "used": [
        {"object_id": x, "note": "", "added_at": time.time() - 2 * DAY},                               # a later day for a listed ID
        {"object_id": y, "note": "bogus", "added_at": 1}, {"object_id": z, "note": "bogus", "added_at": 0.5},   # cannot be real: unknown, held
        {"object_id": w, "note": "ok", "added_at": time.time() - 45 * DAY}]}
    dry = client.post("/api/backup/import?dry_run=true", json=backup).json()["used"]
    assert dry == {"added": 3, "skipped": 0, "refreshed": 1}
    assert status_of(client, x) == "free"                                                              # a dry run changes nothing
    real = client.post("/api/backup/import?dry_run=false", json=backup).json()["used"]
    assert real == {"added": 3, "skipped": 0, "refreshed": 1}
    assert status_of(client, x) == "external" and status_of(client, y) == "external" and status_of(client, z) == "external"
    assert used_row(client, y)["since"] == "" and status_of(client, w) == "free"
    assert client.post("/api/backup/import?dry_run=false", json=backup).json()["used"] == {"added": 0, "skipped": 4, "refreshed": 0}   # twice: nothing moves


def test_the_freed_number_counts_only_objects_nothing_else_holds(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    a, b, c, d = ids[:4]
    snapshot(client, sm_box("OLD", "old", [a, b, c, d, "999999"], created_at=iso(60)),
             sm_box("OURS", "made by the app", [ids[4], ids[5]], created_at=iso(60)))
    box_with(s, [a])                                                                                   # in a box of this app
    client.post("/api/used", json={"text": b})                                                         # used in the sheet today
    pushed = box_with(s, [ids[4]], label="Box P", status="approved")
    conn_of(s).execute("INSERT INTO sm_push(box_id, sm_box_id, pushed_at) VALUES(?,?,?)", (pushed, "OURS", time.time()))
    rep = client.get("/api/sm/boxes/report").json()["reuse"]
    assert rep["freed_objects"] == 2                                  # c and d only: a is in a box here, b was used in the sheet today, 999999 is not in the kho,
    assert rep["old_boxes"] == 1 and rep["undated_boxes"] == 0       # and the app's own box in SM (ids[4], ids[5]) is left out of the count altogether


def test_state_numbers_add_up_with_the_object_list_and_reusable_means_really_free(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    x, y, z = ids[:3]
    client.post("/api/used", json={"text": f"{x} {y}", "since": day(60)})
    client.post("/api/used", json={"text": z})                                                         # today
    box_with(s, [z])                                                                                   # z is both in a box and listed
    snapshot(client, sm_box("NEW", "recent", [y], created_at=iso(2)))                                  # y is held again by a recent SM box
    st = client.get("/api/state").json()
    counts = client.get("/api/objects?per=1").json()["counts"]
    assert st["stats"]["external"] == counts["external"] and st["stats"]["in_sm"] == counts["sm"]
    assert len(cat.items()) - st["stats"]["free"] == sum(v for k, v in counts.items() if k != "free")
    used = {u["id"]: u for u in st["used"]}
    assert used[x]["reusable"] is True                                # old and nothing else holds it
    assert used[y]["reusable"] is False                               # old, but a recent SM box holds it
    assert used[z]["reusable"] is False                               # recent


def test_a_box_made_under_the_rule_is_flagged_when_the_rule_gets_stricter(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    x, y = ids[:2]
    snapshot(client, sm_box("OLD", "Old SM box", [x], created_at=iso(40)))
    client.post("/api/used", json={"text": y, "since": day(40)})
    bid = box_with(s, [x, y], label="Box Q", status="approved")                                        # built while both were free
    assert client.get("/api/sm/boxes/report").json()["conflicts"] == []
    assert service.sm_queue(conn_of(s), cat, 3)["blocked"] == [] and len(service.sm_queue(conn_of(s), cat, 3)["ready"]) == 1
    set_reuse(client, days=90)                                                                         # stricter: both are held again
    conflicts = client.get("/api/sm/boxes/report").json()["conflicts"]
    assert len(conflicts) == 1 and {o["id"] for o in conflicts[0]["objects"]} == {x, y}
    by_id = {o["id"]: o["sm_box"] for o in conflicts[0]["objects"]}
    assert by_id[x] == "Old SM box" and "bảng tính" in by_id[y]
    blocked = service.sm_queue(conn_of(s), cat, 3)["blocked"]
    assert len(blocked) == 1 and any("hộp SM Old SM box" in r for r in blocked[0]["reasons"]) and any("bảng tính" in r for r in blocked[0]["reasons"])
    alerts = client.get("/api/automations").json()["alerts"]
    assert any("trong SM hoặc bảng tính" in a["msg"] for a in alerts)


def test_the_switch_is_read_once_per_request_not_once_per_helper(env, monkeypatch):
    from app import db
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    snapshot(client, sm_box("OLD", "old", ids[:20], created_at=iso(60)), sm_box("NEW", "new", ids[20:40], created_at=iso(1)))
    client.post("/api/used", json={"text": " ".join(ids[40:60]), "since": day(50)})
    seen = []
    real = db.Conn.execute
    monkeypatch.setattr(db.Conn, "execute", lambda self, sql, params=(): (seen.append(sql), real(self, sql, params))[1])
    for path in ("/api/state?rev=-1", "/api/objects?per=50", "/api/objects/coverage"):
        seen.clear()
        assert client.get(path).status_code == 200, path
        assert sum("FROM automations" in q for q in seen) <= 1, (path, [q for q in seen if "FROM automations" in q])


def test_the_sm_date_text_is_parsed_once_per_distinct_date(env, monkeypatch):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    when = iso(5)
    snapshot(client, sm_box("A", "a", ids[:100], created_at=when), sm_box("B", "b", ids[100:200], created_at=when))
    calls = []
    real = service.when_of
    monkeypatch.setattr(service, "when_of", lambda t: (calls.append(t), real(t))[1])
    assert len(service.sm_held(conn_of(s))) == 200
    assert len(calls) == 1                                                                             # 200 object rows, one date text, one parse

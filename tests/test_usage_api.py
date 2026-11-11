"""Borrow counts through the app: the sync fields, the saved snapshot, GET /api/objects (uses, age, weight, sort, band), GET /api/usage,
the switch "Giảm món đã dùng nhiều" and its alert. Dates are always "now minus some days"."""
import time

import pytest
from fastapi.testclient import TestClient

from app import automation, db, picker, service, usage
from app.catalog import Catalog
from app.main import create_app
from tests.test_automation import live_rows, make_client, sync
from tests.test_backend import conn_of, env  # noqa: F401  (env is a fixture)

DAY = 86400
TOK = {"X-Sync-Token": "tok"}


def iso(days_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - days_ago * DAY))


def day(days_ago):
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days_ago * DAY + 7 * 3600))      # the calendar day in Vietnam time, as the app shows it


def synced(client, cat, table, **extra):
    """Sync the whole catalog; `table` is id -> a dict of the fields to add to that row (uses, created)."""
    rows = live_rows(cat)
    for r in rows:
        r.update(table.get(r["id"], {}))
    for r in rows:
        r.update({k: v for k, v in extra.items()})
    return sync(client, rows)


def obj(client, oid):
    return next(o for o in client.get(f"/api/objects?q={oid}&per=200").json()["items"] if o["id"] == oid)


def usage_of(client):
    r = client.get("/api/usage")
    assert r.status_code == 200, r.text
    return r.json()


def put(client, key, **body):
    return client.put(f"/api/automations/{key}", json=body)


def heavy(client):
    return [a for a in client.get("/api/automations").json()["alerts"] if a["kind"] == "heavy"]


def first_ids(cat, n):
    return sorted(cat.items())[:n]


# ----------------------------------------------------------------------------- reading the sync rows
def test_sync_reads_uses_and_created_in_every_shape_and_unknown_stays_unknown(env):
    client, s, cat = make_client(env)
    ids = first_ids(cat, 24)
    shapes = {
        ids[0]: {"uses": 1587, "created": iso(120)},
        ids[1]: {"uses": "700", "created": day(60)},                       # numeric string, date without a time
        ids[2]: {"uses": " 42 ", "created": time.time() - 100 * DAY},      # epoch seconds as a number
        ids[3]: {"uses": 12.0},                                            # a whole number written with a point
        ids[4]: {"uses": True, "created": True},                           # booleans are not numbers
        ids[5]: {"uses": -5, "created": -5},
        ids[6]: {"uses": "abc", "created": "yesterday"},
        ids[7]: {"uses": None, "created": None},
        ids[8]: {},                                                        # missing
        ids[9]: {"uses": 10 ** 15},                                        # absurd: clamped
        ids[10]: {"uses": "9" * 400},
        ids[11]: {"uses": 0, "created": iso(5)},                           # zero is a count, not unknown
        ids[12]: {"uses": 12.5, "created": "0001-01-01T00:00:00Z"},        # a fraction and a placeholder date
        ids[13]: {"uses": [1, 2], "created": {"a": 1}},
        ids[14]: {"uses": "-1", "created": "1999-12-31"},
        ids[15]: {"uses": "1e3", "created": "2026-13-40"},
    }
    out = synced(client, cat, shapes)
    assert out["usable"] > 1600
    assert out["with_uses"] == 7                                           # ids 0 1 2 3 9 10 11
    got = {i: obj(client, i) for i in ids[:16]}
    assert got[ids[0]]["uses"] == 1587 and got[ids[0]]["created"] == day(120) and got[ids[0]]["age_days"] == 120
    assert got[ids[1]]["uses"] == 700 and got[ids[1]]["created"] == day(60) and got[ids[1]]["age_days"] in (59, 60)
    assert got[ids[2]]["uses"] == 42 and got[ids[2]]["age_days"] == 100
    assert got[ids[3]]["uses"] == 12
    assert got[ids[9]]["uses"] == 10_000_000 and got[ids[10]]["uses"] == 10_000_000
    assert got[ids[11]]["uses"] == 0 and got[ids[11]]["weight"] == 1.0
    for i in (ids[4], ids[5], ids[6], ids[7], ids[8], ids[12], ids[13], ids[14], ids[15]):
        assert got[i]["uses"] is None and got[i]["weight"] is None, i      # never turned into 0
    for i in (ids[4], ids[5], ids[6], ids[7], ids[8], ids[12], ids[13], ids[14], ids[15]):
        assert got[i]["created"] is None and got[i]["age_days"] is None, i


def test_an_object_with_a_date_and_no_count_shows_its_age_but_no_weight(env):
    client, s, cat = make_client(env)
    i = first_ids(cat, 1)[0]
    synced(client, cat, {i: {"created": iso(30)}})
    o = obj(client, i)
    assert o["uses"] is None and o["weight"] is None and o["age_days"] == 30 and o["created"] == day(30)


def test_the_snapshot_keeps_both_fields_and_a_restart_loads_them(env):
    client, s, cat = make_client(env)
    a, b, c = first_ids(cat, 3)
    synced(client, cat, {a: {"uses": "900", "created": iso(200)}, b: {"uses": 3, "created": time.time() - 50 * DAY}, c: {"uses": "junk"}})
    conn = conn_of(s)
    rows, _ = service.load_snapshot(conn)
    saved = {r["id"]: r for r in rows}
    assert saved[a]["uses"] == 900 and isinstance(saved[a]["created"], float)       # saved clean, not as sent
    assert saved[b]["uses"] == 3 and saved[c]["uses"] is None and saved[c]["created"] is None
    # a new app on the same database and a new Catalog: the saved snapshot is the catalog and still has the fields
    again = TestClient(create_app(s, llm=picker.MockLLM(), catalog=Catalog(s.catalog_path)))
    got = obj(again, a)
    assert got["uses"] == 900 and got["age_days"] == 200 and got["weight"] is not None and 0 < got["weight"] < 1
    assert obj(again, b)["uses"] == 3 and obj(again, c)["uses"] is None
    assert usage_of(again)["known"] == 2


def test_a_snapshot_saved_before_this_change_still_loads_and_nothing_is_reduced(env):
    client, s, cat = make_client(env)
    conn = conn_of(s)
    service.save_snapshot(conn, live_rows(cat), time.time())                          # rows with no uses and no created, as saved by the old code
    app2 = TestClient(create_app(s, llm=picker.MockLLM(), catalog=Catalog(s.catalog_path)))
    assert app2.get("/api/catalog").json()["source"] == "live"
    u = usage_of(app2)
    assert u["known"] == 0 and u["total"] == len(cat.items()) and u["over_start"] == 0 and u["excluded"] == 0 and u["reduced"] == 0
    assert all(o["uses"] is None and o["weight"] is None and o["age_days"] is None for o in app2.get("/api/objects?per=50").json()["items"])
    assert not [a for a in app2.get("/api/automations").json()["alerts"] if a["kind"] == "heavy"]


def test_a_snapshot_row_with_raw_values_is_read_again_by_the_same_rules(env):
    """Rows kept as SM sent them (a string count, an ISO date) must load too: the catalog reads them, not only the sync handler."""
    client, s, cat = make_client(env)
    rows = live_rows(cat)
    rows[0].update({"uses": "1100", "created": iso(90)})
    rows[1].update({"uses": True, "created": "not a date"})
    conn = conn_of(s)
    service.save_snapshot(conn, rows, time.time())
    app2 = TestClient(create_app(s, llm=picker.MockLLM(), catalog=Catalog(s.catalog_path)))
    assert obj(app2, rows[0]["id"])["uses"] == 1100 and obj(app2, rows[0]["id"])["age_days"] == 90
    assert obj(app2, rows[1]["id"])["uses"] is None and obj(app2, rows[1]["id"])["created"] is None


def test_the_csv_fallback_has_no_usage_data_and_everything_still_works(env):
    client, s, cat = make_client(env)
    assert client.get("/api/catalog").json()["source"] == "csv"
    u = usage_of(client)
    assert u["enabled"] is True and u["known"] == 0 and u["total"] == len(cat.items()) and u["unknown"] == u["total"]
    assert u["over_start"] == u["excluded"] == u["reduced"] == u["free_over_start"] == 0 and u["top"] == []
    assert [b["count"] for b in u["bands"]] == [0, 0, 0, 0, 0]
    page = client.get("/api/objects?sort=uses&per=5").json()
    assert [o["id"] for o in page["items"]] == first_ids(cat, 5) and all(o["uses"] is None and o["weight"] is None for o in page["items"])
    assert client.get("/api/objects?band=unknown").json()["total"] == len(cat.items())
    assert client.get("/api/objects?band=500-999").json()["total"] == 0
    assert not heavy(client)
    box = client.post("/api/boxes", json={"module": "M8", "label": "Box A1", "theme": "test", "count": 12}).json()["id"]
    from tests.test_backend import wait_settled
    assert len(wait_settled(client, box)["items"]) == 12


# ----------------------------------------------------------------------------- GET /api/objects
LADDER = [0, 99, 100, 249, 250, 499, 500, 600, 892, 1083, 1587, 5000]


def ladder(client, cat):
    ids = first_ids(cat, len(LADDER))
    synced(client, cat, {i: {"uses": u, "created": iso(300)} for i, u in zip(ids, LADDER)})
    return dict(zip(ids, LADDER))


def test_objects_keep_every_old_field_and_gain_uses_created_age_and_weight(env):
    client, s, cat = make_client(env)
    table = ladder(client, cat)
    r = client.get("/api/objects?per=5").json()
    assert set(r) == {"total", "page", "per", "pages", "counts", "items", "now"}
    assert {"id", "name", "vi", "category", "slot", "stock", "status", "holder", "img", "detail", "img_exp"} <= set(r["items"][0])
    assert {"uses", "created", "age_days", "weight"} <= set(r["items"][0])
    first = r["items"][0]
    assert first["uses"] == 0 and first["weight"] == 1.0 and first["created"] == day(300) and first["age_days"] == 300
    top_id = max(table, key=table.get)
    top = obj(client, top_id)
    assert top["uses"] == 5000 and top["weight"] == 0.05                               # the floor, hard_limit is 0
    mid = obj(client, next(i for i, u in table.items() if u == 600))
    assert 0 < mid["weight"] < 1
    assert mid["weight"] == pytest.approx(usage.weight(600, time.time() - 300 * DAY, time.time(), {}), abs=1e-3)


def test_weight_is_null_when_the_switch_is_off_and_back_when_it_is_on(env):
    client, s, cat = make_client(env)
    table = ladder(client, cat)
    heavy_id = next(i for i, u in table.items() if u == 1083)
    assert obj(client, heavy_id)["weight"] is not None
    assert put(client, "usage_penalty", enabled=False).status_code == 200
    o = obj(client, heavy_id)
    assert o["weight"] is None and o["uses"] == 1083 and o["age_days"] == 300           # the facts stay, only the weight goes
    assert put(client, "usage_penalty", enabled=True).status_code == 200
    assert obj(client, heavy_id)["weight"] is not None


def test_sort_by_uses_is_most_borrowed_first_with_unknown_last(env):
    client, s, cat = make_client(env)
    table = ladder(client, cat)
    page = client.get("/api/objects?sort=uses&per=200").json()["items"]
    known = [o for o in page if o["uses"] is not None]
    assert [o["uses"] for o in known] == sorted(LADDER, reverse=True)
    assert all(o["uses"] is None for o in page[len(known):]) and len(known) == len(LADDER)
    unknown_ids = [o["id"] for o in page[len(known):]]
    assert unknown_ids == sorted(unknown_ids)                                           # a steady order for the rest
    asc = client.get("/api/objects?sort=-uses&per=200").json()["items"]
    assert [o["uses"] for o in asc[:len(LADDER)]] == sorted(LADDER) and asc[len(LADDER)]["uses"] is None
    assert [o["id"] for o in client.get("/api/objects?sort=id&per=3").json()["items"]] == first_ids(cat, 3)    # the old sorts still work
    assert client.get("/api/objects?sort=stock&per=3").status_code == 200


def test_sort_by_uses_breaks_ties_by_id(env):
    client, s, cat = make_client(env)
    ids = first_ids(cat, 6)
    synced(client, cat, {i: {"uses": 40} for i in ids})
    got = [o["id"] for o in client.get("/api/objects?sort=uses&per=6").json()["items"]]
    assert got == ids


@pytest.mark.parametrize("band, want", [("0-99", [0, 99]), ("100-249", [100, 249]), ("250-499", [250, 499]), ("500-999", [500, 600, 892]),
                                        ("1000+", [1083, 1587, 5000])])
def test_the_band_filter_gives_exactly_the_objects_in_that_band(env, band, want):
    client, s, cat = make_client(env)
    ladder(client, cat)
    r = client.get("/api/objects", params={"band": band, "sort": "uses", "per": 200}).json()
    assert sorted((o["uses"] for o in r["items"])) == sorted(want) and r["total"] == len(want)
    assert r["counts"]["free"] == len(want)                                             # the status chips follow the band


def test_band_unknown_and_empty_band_and_combined_filters(env):
    client, s, cat = make_client(env)
    ladder(client, cat)
    n = len(cat.items())
    assert client.get("/api/objects?band=unknown").json()["total"] == n - len(LADDER)
    assert client.get("/api/objects?band=").json()["total"] == n
    assert client.get("/api/objects").json()["total"] == n
    r = client.get("/api/objects?band=1000%2B&status=free").json()
    assert r["total"] == 3
    assert client.get("/api/objects?band=1000%2B&status=approved").json()["total"] == 0
    csv_text = client.get("/api/objects.csv?sort=uses").text                              # the CSV route still answers and keeps its columns
    assert csv_text.lstrip("﻿").splitlines()[0] == "id,name,vietnamese_name,category,stock,status,holder,img"


@pytest.mark.parametrize("bad", ["abc", "500-1000", "1000", "0-99 ", "UNKNOWN", "-1", "100 - 249", "500-999,1000+", "null"])
def test_a_band_that_is_not_in_the_list_is_refused_with_422(env, bad):
    client, s, cat = make_client(env)
    r = client.get("/api/objects", params={"band": bad})
    assert r.status_code == 422, bad
    assert "unknown" in r.json()["detail"] and "1000+" in r.json()["detail"]             # it says which values are allowed


# ----------------------------------------------------------------------------- GET /api/usage
def story(client, cat):
    """Twenty objects with counts: 1587 and 1083 are over any limit near 1000, 892 and 600 are reduced, 500 is at the start."""
    ids = first_ids(cat, 20)
    counts = [1587, 1083, 892, 600, 500, 499, 300, 260, 200, 150, 120, 100, 80, 60, 40, 30, 20, 10, 5, 0]
    ages = [120, 850, 200, 100, 60, 60, None, None, None, None, None, None, None, None, None, None, None, None, None, None]
    table = {}
    for i, u, a in zip(ids, counts, ages):
        table[i] = {"uses": u} if a is None else {"uses": u, "created": iso(a)}
    synced(client, cat, table)
    return ids, counts


def test_usage_report_has_exactly_the_agreed_keys_and_the_right_numbers(env):
    client, s, cat = make_client(env)
    ids, counts = story(client, cat)
    assert put(client, "usage_penalty", config={"hard_limit": 1000}).status_code == 200
    u = usage_of(client)
    assert set(u) == {"enabled", "known", "total", "unknown", "soft_start", "hard_limit", "half_life", "age_effect", "min_pool",
                      "over_start", "excluded", "reduced", "bands", "top", "free_over_start",
                      "usage_at"}                      # when SM itself last sent the counts (None before the first one); added with the carry-over of counts
    assert u["enabled"] is True and u["known"] == 20 and u["total"] == len(cat.items()) and u["unknown"] == u["total"] - 20
    assert (u["soft_start"], u["hard_limit"], u["half_life"], u["age_effect"], u["min_pool"]) == (500, 1000, 300, 50, 100)
    assert u["over_start"] == 5                      # 1587 1083 892 600 and 500 itself
    assert u["excluded"] == 2                        # 1587 and 1083
    assert u["reduced"] == 2                         # 892 and 600; 500 has weight 1
    assert u["free_over_start"] == 5
    assert [(b["from"], b["to"]) for b in u["bands"]] == [(0, 99), (100, 249), (250, 499), (500, 999), (1000, None)]
    assert [b["count"] for b in u["bands"]] == [8, 4, 3, 3, 2] and set(u["bands"][0]) == {"from", "to", "count"}
    assert sum(b["count"] for b in u["bands"]) == u["known"]
    top = u["top"]
    assert [t["uses"] for t in top] == counts[:15] and len(top) == 15
    assert set(top[0]) == {"id", "name", "vi", "uses", "age_days", "weight"}
    assert top[0]["id"] == ids[0] and top[0]["weight"] == 0.0 and top[1]["weight"] == 0.0 and top[0]["age_days"] == 120
    assert 0 < top[2]["weight"] < 1 and top[4]["weight"] == 1.0 and top[6]["age_days"] is None and top[6]["weight"] == 1.0
    assert top[0]["name"] and top[0]["vi"]


def test_free_over_start_counts_only_objects_nobody_holds(env):
    client, s, cat = make_client(env)
    ids, counts = story(client, cat)
    assert usage_of(client)["free_over_start"] == 5
    assert client.post("/api/used", json={"text": ids[2], "note": "M5"}).status_code == 200            # held by a sheet list
    assert usage_of(client)["free_over_start"] == 4
    box = client.post("/api/sm/boxes/sync", json={"boxes": [{"box_id": "SM-9", "title": "x", "status": "assigned", "status_label": "Assigned",
                                                              "created_at": iso(1), "contents": [{"object_id": ids[3], "quantity": 1}]}]}, headers=TOK)
    assert box.status_code == 200
    assert usage_of(client)["free_over_start"] == 3                                      # held by an SM box
    c = conn_of(s)
    b = service.create_box(c, "M7", "Box A1", "t", [], 12, "tester")                     # no generation job: the box stays as it is
    service.claim(c, b, [(1, cat.get(ids[0]), 1, "manual")])                             # taken by one of our boxes
    assert usage_of(client)["free_over_start"] == 2
    assert usage_of(client)["over_start"] == 5                                           # the others do not change


def test_usage_report_follows_the_settings_and_reads_nothing_it_writes_nothing(env):
    client, s, cat = make_client(env)
    story(client, cat)
    conn = conn_of(s)
    rev, log = db.rev(conn), automation.recent_log(conn, 50)
    for _ in range(3):
        usage_of(client)
    client.get("/api/objects?sort=uses&band=500-999")
    assert db.rev(conn) == rev and automation.recent_log(conn, 50) == log               # reading changes nothing
    assert put(client, "usage_penalty", config={"soft_start": 800}).status_code == 200
    u = usage_of(client)
    assert u["soft_start"] == 800 and u["over_start"] == 3 and u["reduced"] == 3 and u["excluded"] == 0
    assert put(client, "usage_penalty", config={"soft_start": 1000, "hard_limit": 1500}).status_code == 200
    u = usage_of(client)
    assert u["over_start"] == 2 and u["excluded"] == 1 and u["reduced"] == 1             # 1587 is out, 1083 is reduced


def test_usage_report_with_the_switch_off_shows_the_facts_but_reduces_nothing(env):
    client, s, cat = make_client(env)
    story(client, cat)
    assert put(client, "usage_penalty", enabled=False, config={"hard_limit": 1000}).status_code == 200
    u = usage_of(client)
    assert u["enabled"] is False and u["known"] == 20 and u["over_start"] == 5 and u["free_over_start"] == 5
    assert u["excluded"] == 0 and u["reduced"] == 0 and u["hard_limit"] == 1000          # the saved settings are still shown
    assert all(t["weight"] is None for t in u["top"]) and u["top"][0]["uses"] == 1587
    assert sum(b["count"] for b in u["bands"]) == 20


# ----------------------------------------------------------------------------- the switch
def test_the_switch_exists_is_on_by_default_and_has_the_agreed_numbers(env):
    client, s, cat = make_client(env)
    sw = {x["key"]: x for x in client.get("/api/automations").json()["switches"]}["usage_penalty"]
    assert sw["title"] == "Giảm món đã dùng nhiều" and sw["enabled"] is True
    assert sw["config"] == {"soft_start": 500, "half_life": 300, "hard_limit": 0, "age_effect": 50, "min_pool": 100}
    assert sw["limits"] == {"soft_start": [1, 100000], "half_life": [1, 5000], "hard_limit": [0, 100000], "age_effect": [0, 100], "min_pool": [1, 1000]}
    assert "lượt mượn" in sw["desc"] and "500" in sw["desc"] and "300" in sw["desc"] and "5%" in sw["desc"]
    assert not any(ch in sw["title"] + sw["desc"] for ch in "!—")


@pytest.mark.parametrize("key, low, high", [("soft_start", 1, 100000), ("half_life", 1, 5000), ("hard_limit", 0, 100000),
                                            ("age_effect", 0, 100), ("min_pool", 1, 1000)])
def test_each_number_takes_its_limits_and_nothing_else(env, key, low, high):
    client, s, cat = make_client(env)
    if key == "soft_start":
        assert put(client, "usage_penalty", config={"hard_limit": 0}).status_code == 200
    for ok in (low, high):
        cfg = {key: ok}
        if key == "soft_start" and ok > 500:
            cfg["hard_limit"] = 0
        assert put(client, "usage_penalty", config=cfg).status_code == 200, (key, ok)
    for bad in (low - 1, high + 1, True, 3.5, "7", None):
        r = put(client, "usage_penalty", config={key: bad})
        assert r.status_code == 422, (key, bad)
    assert put(client, "usage_penalty", config={"nonsense": 5}).status_code == 422


def test_a_hard_limit_under_the_start_is_refused_in_plain_words_and_changes_nothing(env):
    client, s, cat = make_client(env)
    r = put(client, "usage_penalty", config={"hard_limit": 400})
    assert r.status_code == 422
    msg = r.json()["detail"]
    assert "400" in msg and "500" in msg and "lượt mượn" in msg and "0" in msg           # both numbers, the unit, and the way out
    assert "!" not in msg and "—" not in msg
    cfg = {x["key"]: x for x in client.get("/api/automations").json()["switches"]}["usage_penalty"]["config"]
    assert cfg["hard_limit"] == 0                                                         # nothing was saved
    assert put(client, "usage_penalty", config={"hard_limit": 500}).status_code == 200    # equal to the start is allowed
    assert put(client, "usage_penalty", config={"hard_limit": 0}).status_code == 200      # 0 is "never leave out"
    assert put(client, "usage_penalty", config={"hard_limit": 1000}).status_code == 200
    # raising the start above the saved limit is refused too, and so are both at once
    assert put(client, "usage_penalty", config={"soft_start": 1500}).status_code == 422
    assert put(client, "usage_penalty", config={"soft_start": 1500, "hard_limit": 1200}).status_code == 422
    assert put(client, "usage_penalty", config={"soft_start": 1500, "hard_limit": 2000}).status_code == 200
    assert put(client, "usage_penalty", config={"hard_limit": 0}).status_code == 200
    assert put(client, "usage_penalty", config={"soft_start": 50}).status_code == 200
    # the check lives in set_switch itself, not only in the route
    conn = conn_of(s)
    with pytest.raises(automation.BadSetting):
        automation.set_switch(conn, "usage_penalty", config={"hard_limit": 10})
    with pytest.raises(automation.BadSetting):
        automation.set_switch(conn, "usage_penalty", config={"soft_start": 300, "hard_limit": 299})
    assert automation.set_switch(conn, "usage_penalty", config={"soft_start": 300, "hard_limit": 300})["config"]["hard_limit"] == 300
    # turning it off or on does not trip the check
    assert put(client, "usage_penalty", enabled=False).status_code == 200 and put(client, "usage_penalty", enabled=True).status_code == 200


def test_switching_off_and_on_is_written_in_the_log(env):
    client, s, cat = make_client(env)
    put(client, "usage_penalty", enabled=False)
    put(client, "usage_penalty", enabled=True)
    log = [e["msg"] for e in client.get("/api/automations").json()["log"]]
    assert "Giảm món đã dùng nhiều: đã tắt" in log and "Giảm món đã dùng nhiều: đã bật" in log


# ----------------------------------------------------------------------------- the alert
def test_heavy_alert_names_the_numbers_when_objects_are_over_the_start(env):
    client, s, cat = make_client(env)
    story(client, cat)
    put(client, "usage_penalty", config={"hard_limit": 1000})
    a = heavy(client)
    assert len(a) == 1 and a[0]["level"] == "warn" and a[0]["kind"] == "heavy"
    msg = a[0]["msg"]
    assert msg.startswith("5 món có từ 500 lượt mượn trở lên") and "2 món được chọn ít hơn" in msg and "2 món bị loại" in msg
    assert "!" not in msg and "—" not in msg
    put(client, "usage_penalty", config={"soft_start": 800, "hard_limit": 0})
    assert "3 món có từ 800 lượt mượn trở lên" in heavy(client)[0]["msg"] and "0 món bị loại" in heavy(client)[0]["msg"]


def test_no_heavy_alert_when_off_or_when_nothing_is_over_or_when_nothing_is_known(env):
    client, s, cat = make_client(env)
    assert not heavy(client)                                                              # the CSV kho: no usage data
    ids = first_ids(cat, 5)
    synced(client, cat, {i: {"uses": 10 * n} for n, i in enumerate(ids)})
    assert not heavy(client)                                                              # data, but nothing at 500
    synced(client, cat, {ids[0]: {"uses": 700}})
    assert len(heavy(client)) == 1
    put(client, "usage_penalty", enabled=False)
    assert not heavy(client)
    put(client, "usage_penalty", enabled=True)
    put(client, "catalog_watch", enabled=False)
    assert client.get("/api/automations").json()["alerts"] == []                          # the watch switch still silences every alert


def test_the_alert_function_works_without_http(env):
    client, s, cat = make_client(env)
    story(client, cat)
    conn = conn_of(s)
    out = automation.alerts(conn, cat, service.taken_ids(conn))
    assert [a["kind"] for a in out].count("heavy") == 1


def test_the_switches_are_read_once_per_request(env, monkeypatch):
    client, s, cat = make_client(env)
    story(client, cat)
    seen = []
    real = db.Conn.execute
    monkeypatch.setattr(db.Conn, "execute", lambda self, sql, params=(): (seen.append(sql), real(self, sql, params))[1])
    for path in ("/api/usage", "/api/objects?per=50&sort=uses", "/api/objects?band=500-999"):
        seen.clear()
        assert client.get(path).status_code == 200, path
        assert sum("FROM automations" in q for q in seen) == 1, (path, [q for q in seen if "FROM automations" in q])

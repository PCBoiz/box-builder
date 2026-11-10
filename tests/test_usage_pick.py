"""The picker with borrow counts: weight 0 objects are never offered to the model and never filled by code, the others are offered and drawn by
weight, the pool floor keeps a box fillable, and with the switch off (or no usage data) the picker does exactly what it did before."""
import random
import time

import pytest

from app import automation, picker, service, usage
from tests.test_anchor_picking import RecordingLLM, catalog_ids, make_anchor_box
from tests.test_automation import live_rows, make_client
from tests.test_backend import conn_of, env  # noqa: F401  (env is a fixture)

DAY = 86400


class EmptyLLM:
    """A model that never gives a usable answer: everything is filled by code."""
    name = "empty"

    def complete(self, system, user, ctx):
        return ""


def go_live(cat, table):
    """Make the catalog a live snapshot in which `table` (id -> (uses, days since creation or None)) carries borrow counts."""
    rows = live_rows(cat)
    now = time.time()
    for r in rows:
        if r["id"] in table:
            uses, age = table[r["id"]]
            r["uses"] = uses
            if age is not None:
                r["created"] = now - age * DAY
    cat.set_live(rows, now)


def switch(s, enabled=None, **config):
    return automation.set_switch(conn_of(s), "usage_penalty", enabled, config or None)


def box_ids(s, box):
    return [r["object_id"] for r in service.box_items(conn_of(s), box)]


def sources(s, box):
    return {r["source"] for r in service.box_items(conn_of(s), box)}


def run(s, cat, llm, count=12, label="Box A1", categories=()):
    c = conn_of(s)
    box = service.create_box(c, "M8", label, "", list(categories), count, "tester", code="M8-02")
    picker.run_generation(s, cat, llm, box)
    return box


def ids_of(cat):
    return sorted(cat.items())


# ----------------------------------------------------------------------------- weight 0 never gets in
def test_objects_left_out_are_never_offered_to_the_model_nor_accepted_from_it(env):
    s, cat = env
    ids = ids_of(cat)
    out = set(ids[:700])                                              # 700 objects over the hard limit
    go_live(cat, {i: (2000, 400) for i in out})
    switch(s, hard_limit=1000)
    llm = RecordingLLM(first=sorted(out)[:5])                          # the model also names objects that were left out
    box = run(s, cat, llm)
    offered = catalog_ids(llm.calls[0][1])
    assert offered and not out & set(offered)
    assert set(offered) == set(ids) - out                              # everything else is offered (weights are all 1)
    got = box_ids(s, box)
    assert len(got) == 12 and not out & set(got)
    assert service.get_box(conn_of(s), box)["status"] == "draft"


def test_code_that_fills_the_rest_never_takes_an_object_that_is_left_out(env):
    s, cat = env
    ids = ids_of(cat)
    out = set(ids[::2])                                                # every other object is left out
    go_live(cat, {i: (5000, 100) for i in out})
    switch(s, hard_limit=1000)
    for n in range(6):
        box = run(s, cat, EmptyLLM(), count=21, label=f"Box {n}")
        got = box_ids(s, box)
        assert len(got) == 21 and sources(s, box) == {"fill"} and not out & set(got)


def test_a_slot_the_model_leaves_empty_is_filled_from_objects_that_are_not_left_out(env):
    s, cat = env

    class Half:
        name = "half"

        def complete(self, system, user, ctx):                         # answers one slot per call, so code has to fill the rest
            lines = user.split("Catalog, one object per line: ID|name|category\n", 1)[1].splitlines()
            return "\n".join(f"{ln.split('|')[0]}|1" for ln in lines[:1])

    ids = ids_of(cat)
    out = set(ids[:900])
    go_live(cat, {i: (3000, 50) for i in out})
    switch(s, hard_limit=1500)
    box = run(s, cat, Half(), count=12)
    got = box_ids(s, box)
    assert len(got) == 12 and not out & set(got) and sources(s, box) == {"llm", "fill"}


def test_people_can_still_add_a_left_out_object_by_hand(env):
    client, s, cat = make_client(env)
    ids = ids_of(cat)
    go_live(cat, {ids[0]: (9000, 100)})
    switch(s, hard_limit=1000)
    c = conn_of(s)
    box = service.create_box(c, "M8", "Box B2", "", [], 12, "tester")
    service.set_status(c, box, "draft")
    r = client.post(f"/api/boxes/{box}/items", json={"object_id": ids[0], "qty": 1})
    assert r.status_code == 201, r.text
    assert ids[0] in box_ids(s, box) and sources(s, box) == {"manual"}
    assert any(o.id == ids[0] for o in cat.search(cat.get(ids[0]).name, limit=50))               # the search still finds it
    assert client.get("/api/objects", params={"q": ids[0]}).json()["items"][0]["weight"] == 0.0


# ----------------------------------------------------------------------------- the pool floor
def test_when_few_objects_are_free_the_least_used_left_out_objects_come_back(env):
    s, cat = env
    ids = ids_of(cat)
    normal, rest = ids[:5], ids[5:]
    table = {i: (10, None) for i in normal}
    table.update({i: (2000 + n, 300) for n, i in enumerate(rest)})      # all the others are over the limit, the lower ids are the less used
    go_live(cat, table)
    switch(s, hard_limit=1000)                                          # min_pool is 100
    llm = RecordingLLM()
    box = run(s, cat, llm, count=21, label="Box B2")
    offered = catalog_ids(llm.calls[0][1])
    assert set(offered) == set(normal) | set(rest[:95]) and len(offered) == 100          # 5 normal + the 95 least used ones put back
    got = box_ids(s, box)
    assert len(got) == 21 and set(got) <= set(offered)
    assert service.get_box(conn_of(s), box)["status"] == "draft"


def test_a_box_bigger_than_min_pool_still_finds_enough_objects(env):
    s, cat = env
    ids = ids_of(cat)
    table = {i: (10, None) for i in ids[:3]}
    table.update({i: (5000, 300) for i in ids[3:]})
    go_live(cat, table)
    switch(s, hard_limit=1000, min_pool=5)                              # 5 is smaller than the 21 slots: the slots count
    llm = RecordingLLM()
    box = run(s, cat, llm, count=21, label="Box B3")
    assert len(catalog_ids(llm.calls[0][1])) == 21 and len(box_ids(s, box)) == 21
    box2 = run(s, cat, EmptyLLM(), count=12, label="Box A1")             # the filler has the same guarantee
    assert len(box_ids(s, box2)) == 12 and sources(s, box2) == {"fill"}


def test_a_category_smaller_than_min_pool_keeps_all_its_objects(env):
    s, cat = env
    name = next(c for c, n in cat.categories() if 30 <= n <= 90)
    in_cat = [i for i, it in cat.items().items() if it.category == name]
    go_live(cat, {i: (4000, 200) for i in cat.items()})                 # every object in the kho is over the limit
    switch(s, hard_limit=1000)
    llm = RecordingLLM()
    box = run(s, cat, llm, count=21, label="Box B2", categories=[name])
    assert set(catalog_ids(llm.calls[0][1])) == set(in_cat)             # fewer than min_pool: every one of them stays
    assert len(box_ids(s, box)) == 21
    box2 = run(s, cat, EmptyLLM(), count=12, label="Box A1", categories=[name])
    assert len(box_ids(s, box2)) == 12 and set(box_ids(s, box2)) <= set(in_cat)


def test_the_shortlist_is_cut_by_weight_when_it_is_longer_than_the_limit(env, monkeypatch):
    s, cat = env
    ids = ids_of(cat)
    out = set(ids[:300])
    go_live(cat, {i: (4000, 100) for i in out})
    switch(s, hard_limit=1000)
    monkeypatch.setattr(picker, "POOL_LIMIT", 50)
    llm = RecordingLLM()
    box = run(s, cat, llm)
    offered = catalog_ids(llm.calls[0][1])
    assert len(offered) == 50 and not out & set(offered) and len(set(offered)) == 50
    assert len(box_ids(s, box)) == 12


# ----------------------------------------------------------------------------- reduced, not left out
def test_heavily_used_objects_are_filled_far_less_often_than_their_share(env):
    """10 percent of the objects have weight 0.05 (no hard limit). Unweighted, they would be about 10 percent of the picks."""
    s, cat = env
    ids = ids_of(cat)
    heavy = set(ids[::10])
    go_live(cat, {i: (1500, 400) for i in heavy})
    assert 0 < usage.weight(1500, time.time() - 400 * DAY, time.time(), {}) < 0.1
    picks = []
    for n in range(16):
        picks += box_ids(s, run(s, cat, EmptyLLM(), count=12, label=f"Box {n}"))
    assert len(picks) == 16 * 12
    on = sum(1 for p in picks if p in heavy)
    assert on <= 6, on                                                  # expected near 1; a plain shuffle would give near 19
    switch(s, enabled=False)
    picks_off = []
    for n in range(16, 32):
        picks_off += box_ids(s, run(s, cat, EmptyLLM(), count=12, label=f"Box {n}"))
    assert sum(1 for p in picks_off if p in heavy) >= 8                 # the same data with the switch off: no reduction


def test_the_shortlist_offers_an_object_with_weight_w_about_w_of_the_time():
    from app.catalog import Item
    pool = [Item(f"{i:06d}", f"n{i}", f"v{i}", "C", "", 5, "", "", 10, None) for i in range(400)]
    weights = {it.id: 1.0 for it in pool}
    quarter, gone = pool[:100], pool[100:110]
    weights.update({it.id: 0.25 for it in quarter})
    weights.update({it.id: 0.0 for it in gone})
    seen = {it.id: 0 for it in pool}
    n = 200
    for seed in range(n):
        short = picker.usage_shortlist(pool, weights, 21, 2500, random.Random(seed))
        ids = [it.id for it in short]
        assert ids == sorted(ids)                                       # the catalog order is kept
        for i in ids:
            seen[i] += 1
    assert all(seen[it.id] == n for it in pool[110:])                   # weight 1: always offered
    assert all(seen[it.id] == 0 for it in gone)                         # weight 0: never
    rate = sum(seen[it.id] for it in quarter) / (n * len(quarter))
    assert 0.22 < rate < 0.28


def test_the_shortlist_never_drops_below_what_the_box_needs():
    from app.catalog import Item
    pool = [Item(f"{i:06d}", "n", "v", "C", "", 5, "", "", 900, None) for i in range(30)]
    weights = {it.id: 0.05 for it in pool}
    for seed in range(20):
        short = picker.usage_shortlist(pool, weights, 21, 2500, random.Random(seed))
        assert len(short) == 21                                          # thinning would leave one or two: the rest come back
    assert len(picker.usage_shortlist(pool, weights, 100, 2500, random.Random(1))) == 30     # fewer objects than the minimum: all of them
    assert len(picker.usage_shortlist(pool, {it.id: 1.0 for it in pool}, 5, 10, random.Random(1))) == 10   # the limit still cuts
    assert picker.usage_shortlist([], {}, 5, 10, random.Random(1)) == []


# ----------------------------------------------------------------------------- nothing changes when the rule does not apply
def old_shortlist(cat, conn):
    taken = service.taken_ids(conn)
    return [i for i in cat.items() if i not in taken]


def old_fill(cat, conn, box_id, need):
    pool = picker.available_pool(conn, cat, [], ())
    random.Random(box_id).shuffle(pool)
    return [it.id for it in pool[:need]]


def same_as_before(s, cat, label):
    """Run one box with the model path and one with the fill path and compare each with the old behaviour."""
    conn = conn_of(s)
    want_list = old_shortlist(cat, conn)
    llm = RecordingLLM()
    run(s, cat, llm, count=12, label=label + " m")
    assert catalog_ids(llm.calls[0][1]) == want_list                    # the same lines in the same order
    c = conn_of(s)
    box = service.create_box(c, "M8", label + " f", "", [], 12, "tester", code="M8-02")
    want_fill = old_fill(cat, c, box, 12)
    picker.run_generation(s, cat, EmptyLLM(), box)
    got = [r["object_id"] for r in service.box_items(conn_of(s), box)]
    assert got == want_fill


def test_with_the_switch_off_the_picker_is_exactly_what_it_was(env):
    s, cat = env
    ids = ids_of(cat)
    go_live(cat, {i: (3000, 100) for i in ids[:600]})                   # lots of heavy objects and a limit that would leave them out
    switch(s, enabled=False, hard_limit=1000)
    same_as_before(s, cat, "Off")


def test_with_no_usage_data_the_picker_is_exactly_what_it_was(env):
    s, cat = env
    go_live(cat, {})                                                    # a live catalog from an older tool: no borrow counts
    switch(s, hard_limit=1000)
    same_as_before(s, cat, "None")
    assert picker.usage_weights(conn_of(s), list(cat.items().values()), 12) is None


def test_with_every_object_below_the_start_the_picker_is_exactly_what_it_was(env):
    s, cat = env
    go_live(cat, {i: (499, 30) for i in ids_of(cat)})                   # counts are known but nobody is past 500
    same_as_before(s, cat, "Low")


def test_usage_weights_says_none_unless_something_is_reduced(env):
    s, cat = env
    conn = conn_of(s)
    pool = list(cat.items().values())
    assert picker.usage_weights(conn, pool, 12) is None                  # the CSV kho has no counts
    ids = ids_of(cat)
    go_live(cat, {ids[0]: (800, 100)})
    pool = list(cat.items().values())
    got = picker.usage_weights(conn, pool, 12)
    assert got is not None and got[1] == 100 and 0 < got[0][ids[0]] < 1 and got[0][ids[1]] == 1.0
    assert picker.usage_weights(conn, pool, 150)[1] == 150               # the slots can raise the minimum
    switch(s, enabled=False)
    assert picker.usage_weights(conn, pool, 12) is None
    assert picker.usage_weights(conn, [], 12) is None


def test_a_pick_with_the_rule_on_is_repeatable_for_the_same_box(env):
    """The shortlist is drawn from a seed made of the box and the attempt, as before: asking twice gives the same list."""
    s, cat = env
    ids = ids_of(cat)
    go_live(cat, {i: (900, 100) for i in ids[:400]})
    conn = conn_of(s)
    pool = picker.available_pool(conn, cat, [], ())
    w = picker.usage_weights(conn, pool, 12)
    a = picker.usage_shortlist(pool, w[0], w[1], picker.POOL_LIMIT, random.Random(5001))
    b = picker.usage_shortlist(pool, w[0], w[1], picker.POOL_LIMIT, random.Random(5001))
    c = picker.usage_shortlist(pool, w[0], w[1], picker.POOL_LIMIT, random.Random(5002))
    assert [x.id for x in a] == [x.id for x in b] and [x.id for x in a] != [x.id for x in c]

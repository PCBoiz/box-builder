"""The borrow-count rule as pure functions (app/usage.py): reading the two fields, the weight, bands, top, the pool floor and the weighted draw.
No database, no real clock: every date is `now` minus some days, every random draw has a seed."""
import math
import random

import pytest

from app import usage
from app.catalog import Item

NOW = 1_800_000_000.0                      # any fixed moment: the functions take `now`, they never read the clock
DAY = 86400


def ago(days):
    return NOW - days * DAY


def item(oid, uses, created=None):
    return Item(oid, f"Name {oid}", f"Tên {oid}", "Cat", "A1", 5, "", "", uses, created)


# ----------------------------------------------------------------------------- reading uses
@pytest.mark.parametrize("raw, want", [
    (0, 0), (1587, 1587), ("1587", 1587), (" 1587 ", 1587), ("+12", 12), ("0012", 12), ("000", 0), (12.0, 12), ("12.0", 12),
    (None, None), (True, None), (False, None), (-1, None), ("-1", None), (-0.5, None), (12.5, None), ("12.5", None),
    ("", None), ("  ", None), ("abc", None), ("12abc", None), ("1e3", None), ("1_000", None), ("1,587", None), ("٣", None),
    (float("nan"), None), (float("inf"), None), ([5], None), ({"n": 5}, None), (b"5", None),
    (10**7, 10**7), (10**7 + 1, 10**7), (10**30, 10**7), ("9" * 50, 10**7), ("9" * 5000, 10**7), (1e30, 10**7),
])
def test_uses_are_read_as_a_whole_number_or_unknown(raw, want):
    assert usage.parse_uses(raw) == want


def test_zero_stays_zero_and_unknown_never_becomes_zero():
    assert usage.parse_uses(0) == 0 and usage.parse_uses("0") == 0
    assert usage.parse_uses(None) is None and usage.parse_uses("") is None


# ----------------------------------------------------------------------------- reading the creation date
def test_created_reads_iso_dates_the_way_when_of_does_and_epoch_numbers():
    from app.service import when_of
    for text in ("2026-11-10T08:00:00Z", "2026-11-10 08:00:00", "2026-11-10", "2026-11-10T08:00:00+07:00"):
        assert usage.parse_created(text) == when_of(text) and isinstance(usage.parse_created(text), float)
    assert usage.parse_created(1_700_000_000) == 1_700_000_000.0
    assert usage.parse_created(1_700_000_000.5) == 1_700_000_000.5
    assert usage.parse_created(1_700_000_000_000) == 1_700_000_000.0           # JavaScript milliseconds


@pytest.mark.parametrize("raw", [None, True, False, "", "  ", "yesterday", "10/11/2026", "2026-13-40", "0001-01-01", "1970-01-01T00:00:00Z",
                                 "1999-12-31", 0, 12345, -5, 946684799, float("nan"), float("inf"), 5e15, [1], {"a": 1}])
def test_created_that_cannot_be_read_is_unknown(raw):
    assert usage.parse_created(raw) is None


# ----------------------------------------------------------------------------- the weight
def test_weight_is_one_for_unknown_uses_whatever_the_rest():
    assert usage.weight(None, None, NOW, {}) == 1.0
    assert usage.weight(None, ago(10), NOW, {"hard_limit": 100}) == 1.0           # unknown is never left out
    assert usage.weight(None, ago(10), NOW, {"soft_start": 1}) == 1.0


def test_weight_is_one_up_to_the_start_and_continuous_there():
    for age in (None, 5, 100, 2000):
        created = None if age is None else ago(age)
        assert usage.weight(0, created, NOW, {}) == 1.0
        assert usage.weight(499, created, NOW, {}) == 1.0
        assert usage.weight(500, created, NOW, {}) == 1.0                         # "from 500 up": 500 itself is still full weight
        just_over = usage.weight(501, created, NOW, {})
        assert 0.99 < just_over < 1.0                                             # no cliff
    for start in (1, 50, 500, 5000):
        assert usage.weight(start, ago(100), NOW, {"soft_start": start}) == 1.0
        assert usage.weight(start + 1, ago(100), NOW, {"soft_start": start}) < 1.0


def test_the_chance_halves_every_half_life_when_the_age_is_unknown_or_the_effect_is_off():
    for cfg in ({"age_effect": 0}, {}):
        created = None
        assert usage.weight(800, created, NOW, cfg) == pytest.approx(0.5)
        assert usage.weight(1100, created, NOW, cfg) == pytest.approx(0.25)
        assert usage.weight(650, created, NOW, {**cfg, "half_life": 300}) == pytest.approx(0.5 ** 0.5)
    assert usage.weight(800, ago(400), NOW, {"age_effect": 0}) == pytest.approx(0.5)      # age_effect 0: age changes nothing
    assert usage.weight(800, ago(30), NOW, {"age_effect": 0}) == pytest.approx(0.5)
    assert usage.weight(700, None, NOW, {"half_life": 100}) == pytest.approx(0.25)


@pytest.mark.parametrize("age", [None, 0, 10, 30, 120, 400, 900, 4000])
@pytest.mark.parametrize("cfg", [{}, {"age_effect": 0}, {"age_effect": 100}, {"soft_start": 100, "half_life": 50}, {"half_life": 5000}])
def test_weight_never_rises_when_uses_rise_and_stays_between_floor_and_one(age, cfg):
    created = None if age is None else ago(age)
    last = 1.0
    for uses in range(0, 4000, 7):
        w = usage.weight(uses, created, NOW, cfg)
        assert usage.FLOOR <= w <= 1.0
        assert w <= last + 1e-12, (uses, w, last)
        last = w


def test_the_floor_is_five_percent_and_only_the_hard_limit_goes_below():
    assert usage.FLOOR == 0.05
    for age in (None, 30, 3000):
        created = None if age is None else ago(age)
        assert usage.weight(100_000, created, NOW, {}) == 0.05
        assert usage.weight(10_000_000, created, NOW, {"age_effect": 100}) == 0.05
    assert usage.weight(3000, ago(1000), NOW, {"hard_limit": 0}) == 0.05           # 0 = never leave out


def test_hard_limit_leaves_out_from_that_number_and_zero_means_never():
    cfg = {"hard_limit": 1000}
    assert usage.weight(999, ago(100), NOW, cfg) > 0
    assert usage.weight(1000, ago(100), NOW, cfg) == 0.0
    assert usage.weight(1000, None, NOW, cfg) == 0.0
    assert usage.weight(50_000, ago(5000), NOW, cfg) == 0.0
    assert usage.weight(1000, ago(100), NOW, {"hard_limit": 0}) > 0
    assert usage.weight(1000, ago(100), NOW, {}) > 0
    assert usage.weight(500, None, NOW, {"soft_start": 500, "hard_limit": 500}) == 0.0   # equal to the start is allowed: left out at once


def test_young_objects_that_got_there_fast_are_reduced_more_and_old_slow_ones_less():
    full = {"age_effect": 100}
    count_only = usage.weight(600, None, NOW, full)
    young, old = usage.weight(600, ago(100), NOW, full), usage.weight(600, ago(800), NOW, full)
    assert young < count_only < old
    assert young == pytest.approx(0.63, abs=0.002) and old == pytest.approx(0.843, abs=0.002)      # the worked example of the research notes
    # the other examples of the notes, at full effect
    assert usage.weight(1083, ago(850), NOW, full) == pytest.approx(0.273, abs=0.002)
    assert usage.weight(892, ago(200), NOW, full) == pytest.approx(0.195, abs=0.002)
    assert usage.weight(1587, ago(120), NOW, full) == 0.05
    # half effect lies between none and full
    half = usage.weight(600, ago(100), NOW, {"age_effect": 50})
    assert young < half < count_only and half == pytest.approx(0.5 ** (100 * 1.5 / 300))


def test_the_speed_multiplier_is_kept_between_half_and_double_and_a_new_object_counts_as_30_days_old():
    cfg = usage.clean_cfg({"age_effect": 100})
    assert usage.speed_multiplier(5000, ago(1), NOW, cfg) == 2.0               # absurdly fast: capped
    assert usage.speed_multiplier(501, ago(5000), NOW, cfg) == 0.5             # very slow: capped
    assert usage.speed_multiplier(600, None, NOW, cfg) == 1.0                  # no creation date: no effect
    assert usage.speed_multiplier(60, ago(1), NOW, cfg) == usage.speed_multiplier(60, ago(30), NOW, cfg)         # younger than 30 days is read as 30
    assert usage.speed_multiplier(60, ago(30), NOW, cfg) != usage.speed_multiplier(60, ago(31), NOW, cfg)
    for uses in (501, 600, 2000):
        for age in (-5, 0, 29, 30, 365, 100_000):
            assert 0.5 <= usage.speed_multiplier(uses, ago(age), NOW, cfg) <= 2.0
    assert usage.speed_multiplier(600, ago(100), NOW, usage.clean_cfg({"age_effect": 0})) == 1.0


def test_a_creation_date_in_the_future_does_not_break_the_weight():
    w = usage.weight(900, NOW + 5 * DAY, NOW, {"age_effect": 100})
    assert usage.FLOOR <= w < 1.0
    assert usage.age_days_int(NOW + 5 * DAY, NOW) == 0 and usage.age_days_int(None, NOW) is None
    assert usage.age_days_int(ago(10.9), NOW) == 10


def test_the_weight_reads_damaged_settings_as_the_defaults():
    assert usage.clean_cfg(None) == usage.DEFAULTS
    assert usage.clean_cfg({}) == usage.DEFAULTS
    assert usage.clean_cfg({"soft_start": "x", "half_life": None, "hard_limit": True, "age_effect": -4, "min_pool": 2.5}) == usage.DEFAULTS
    assert usage.clean_cfg({"soft_start": 0, "half_life": 0, "min_pool": 0})["soft_start"] == 1
    assert usage.clean_cfg({"age_effect": 900})["age_effect"] == 100
    assert usage.clean_cfg("junk") == usage.DEFAULTS
    assert 0 <= usage.weight(900, ago(100), NOW, {"half_life": "oops", "soft_start": None}) <= 1


def test_summary_counts_known_over_start_reduced_and_excluded():
    items = [item("1", None), item("2", 10), item("3", 499), item("4", 500, ago(100)), item("5", 501, ago(100)), item("6", 1000, ago(500)),
             item("7", 2000, ago(500))]
    cfg = {"hard_limit": 1000}
    s = usage.summary(items, NOW, cfg)
    assert s == {"known": 6, "total": 7, "over_start": 4, "excluded": 2, "reduced": 1}      # 500 counts as over the start but has weight 1
    assert usage.summary(items, NOW, {}) == {"known": 6, "total": 7, "over_start": 4, "excluded": 0, "reduced": 3}
    assert usage.summary([], NOW, {}) == {"known": 0, "total": 0, "over_start": 0, "excluded": 0, "reduced": 0}
    assert usage.has_data(items) and not usage.has_data([item("1", None)]) and not usage.has_data([])
    w = usage.weights_of(items, NOW, cfg)
    assert w["1"] == w["2"] == w["3"] == w["4"] == 1.0 and w["6"] == w["7"] == 0.0 and 0 < w["5"] < 1


# ----------------------------------------------------------------------------- bands
def test_bands_have_fixed_edges_and_count_unknown_apart():
    counts = [0, 1, 99, 100, 249, 250, 499, 500, 999, 1000, 1587, 10**7, None, None, None]
    b, unknown = usage.bands(counts)
    assert unknown == 3
    assert [(x["from"], x["to"]) for x in b] == [(0, 99), (100, 249), (250, 499), (500, 999), (1000, None)]
    assert [x["count"] for x in b] == [3, 2, 2, 2, 3]
    assert sum(x["count"] for x in b) + unknown == len(counts)
    again, none_known = usage.bands([item("a", None), item("b", 5)])                  # objects work as well as plain counts
    assert none_known == 1 and again[0]["count"] == 1
    empty, zero = usage.bands([])
    assert zero == 0 and [x["count"] for x in empty] == [0] * 5
    assert [usage.band_key(x) for x in (0, 99, 100, 249, 250, 499, 500, 999, 1000, 10**6, None)] == \
        ["0-99", "0-99", "100-249", "100-249", "250-499", "250-499", "500-999", "500-999", "1000+", "1000+", "unknown"]


# ----------------------------------------------------------------------------- top
def test_top_lists_the_most_borrowed_with_weights_and_ages():
    items = [item("a", 10, ago(50)), item("b", 900, ago(100)), item("c", None, ago(5)), item("d", 900, None), item("e", 2000, ago(30)), item("f", 0)]
    w = usage.weights_of(items, NOW, {"hard_limit": 1500})
    t = usage.top(items, 3, w, NOW)
    assert [x["id"] for x in t] == ["e", "b", "d"]                         # unknown left out, ties by id
    assert set(t[0]) == {"id", "name", "vi", "uses", "age_days", "weight"}
    assert t[0] == {"id": "e", "name": "Name e", "vi": "Tên e", "uses": 2000, "age_days": 30, "weight": 0.0}
    assert t[1]["age_days"] == 100 and 0 < t[1]["weight"] < 1 and t[2]["age_days"] is None
    assert [x["id"] for x in usage.top(items, 99, w, NOW)] == ["e", "b", "d", "a", "f"]
    assert usage.top(items, 0, w, NOW) == [] and usage.top([], 5, w, NOW) == []
    bare = usage.top(items, 2)
    assert bare[0]["weight"] is None and bare[0]["age_days"] is None


# ----------------------------------------------------------------------------- the pool floor
def long_tail(n=1533, seed=2026):
    """1,533 objects: a long-tailed borrow count, a few very heavy ones (the six top values of the research notes), random ages."""
    rng = random.Random(seed)
    top = [1587, 1083, 892, 825, 811, 786]
    rows = []
    while len(rows) < n - len(top):
        age = rng.randint(30, 900)
        uses = round(rng.lognormvariate(-1.95, 1.05) * age)
        if uses < 786:
            rows.append((uses, age))
    rows += [(u, rng.randint(60, 900)) for u in top]
    rng.shuffle(rows)
    return [item(f"{i:06d}", u, ago(a)) for i, (u, a) in enumerate(rows)]


def test_the_synthetic_pool_looks_like_the_notes():
    items = long_tail()
    assert len(items) == 1533 and sum(1 for i in items if i.uses > 500) < 80 and max(i.uses for i in items) == 1587
    s = usage.summary(items, NOW, {"hard_limit": 1000})
    assert s["excluded"] == 2 and s["reduced"] > 0 and s["known"] == 1533


def test_the_floor_leaves_a_normal_pool_alone():
    items = long_tail()
    free = random.Random(7).sample(items, 396)
    w = usage.weights_of(free, NOW, {"hard_limit": 1000})
    out = usage.apply_pool_floor(free, w, 100)
    assert out == w and sum(1 for v in out.values() if v > 0) >= 394               # only the weight-0 ones may be missing


def test_the_floor_puts_left_out_objects_back_least_borrowed_first_until_min_pool():
    items = long_tail()
    free = random.Random(7).sample(items, 396)
    cfg = {"soft_start": 5, "hard_limit": 15}      # most of a pool with a median of 54 borrows is left out
    w = usage.weights_of(free, NOW, cfg)
    dead = sorted((i for i in free if w[i.id] == 0), key=lambda i: (i.uses, i.id))
    assert len(dead) > 100 and sum(1 for v in w.values() if v > 0) < 100          # the setup: most of the pool is left out
    out = usage.apply_pool_floor(free, w, 100)
    assert sum(1 for v in out.values() if v > 0) == 100                           # exactly the minimum, no more
    back = [i for i in dead if out[i.id] > 0]
    assert back == dead[:len(back)] and all(out[i.id] == usage.FLOOR for i in back)       # the least borrowed came back, at the floor weight
    assert all(out[i.id] == 0 for i in dead[len(back):])
    assert all(out[i.id] == w[i.id] for i in free if w[i.id] > 0)                 # the others keep their weight
    assert w == usage.weights_of(free, NOW, cfg)                                  # the input was not changed
    full = usage.apply_pool_floor(free, w, 396)
    assert all(v > 0 for v in full.values())                                      # min_pool as big as the pool: everything is back
    assert usage.apply_pool_floor(free, w, 5000) == usage.apply_pool_floor(free, w, 396)


def test_a_free_set_smaller_than_min_pool_keeps_every_object():
    items = long_tail()
    free = items[:30]
    w = {i.id: 0.0 for i in free}                                                 # all of them left out
    out = usage.apply_pool_floor(free, w, 100)
    assert len(out) == 30 and all(v == usage.FLOOR for v in out.values())


def test_the_pool_is_never_emptied_even_when_min_pool_is_zero_or_negative():
    free = [item("a", 5000), item("b", 4000), item("c", 3000)]
    w = {"a": 0.0, "b": 0.0, "c": 0.0}
    for floor in (0, -3, 1):
        out = usage.apply_pool_floor(free, w, floor)
        assert [i for i, v in out.items() if v > 0] == ["c"]                      # one object comes back: the least borrowed
    assert usage.apply_pool_floor([], {}, 100) == {}
    assert usage.apply_pool_floor(free, {}, 2) == {"a": 1.0, "b": 1.0, "c": 1.0}  # a missing weight is full weight


def test_the_worst_case_of_the_notes_still_leaves_a_pool():
    items = sorted(long_tail(), key=lambda i: -i.uses)[:396]                      # the free set is the 396 most borrowed objects
    for hard, want in ((1000, 394), (1500, 395), (0, 396)):
        w = usage.weights_of(items, NOW, {"hard_limit": hard})
        out = usage.apply_pool_floor(items, w, 100)
        assert sum(1 for v in out.values() if v > 0) == want
    w = usage.weights_of(items, NOW, {"soft_start": 50, "hard_limit": 100})
    assert sum(1 for v in w.values() if v > 0) < 100 and sum(1 for v in usage.apply_pool_floor(items, w, 100).values() if v > 0) == 100


# ----------------------------------------------------------------------------- the weighted draw
def test_weighted_sample_is_the_same_for_the_same_seed_and_has_no_repeats():
    items = [item(f"{i}", 0) for i in range(50)]
    w = {i.id: 0.1 + (n % 5) / 5 for n, i in enumerate(items)}
    a = usage.weighted_sample(items, w, rng=random.Random(3))
    b = usage.weighted_sample(items, w, rng=random.Random(3))
    c = usage.weighted_sample(items, w, rng=random.Random(4))
    assert [x.id for x in a] == [x.id for x in b] and [x.id for x in a] != [x.id for x in c]
    assert len(a) == 50 and len({x.id for x in a}) == 50
    assert [x.id for x in usage.weighted_sample(items, w, 7, random.Random(3))] == [x.id for x in a[:7]]
    assert usage.weighted_sample(items, w, 0, random.Random(3)) == [] and usage.weighted_sample([], w) == []


def test_weighted_sample_skips_weight_zero_and_junk_weights():
    items = [item(x, 0) for x in "abcdef"]
    w = {"a": 0.0, "b": 1.0, "c": -1.0, "d": float("nan"), "e": float("inf"), "f": True}
    got = [x.id for x in usage.weighted_sample(items, w, rng=random.Random(1))]
    assert got == ["b"] and "a" not in got
    assert usage.weighted_sample(items, [0] * 6, rng=random.Random(1)) == []
    parallel = usage.weighted_sample(items, [0, 1, 1, 0, 0, 0], rng=random.Random(1))
    assert sorted(x.id for x in parallel) == ["b", "c"]
    with pytest.raises(ValueError):
        usage.weighted_sample(items, [1, 1])
    for seed in range(200):                                                        # a weight 0 is never in the result, whatever the draw
        assert all(x.id != "a" for x in usage.weighted_sample(items, {"a": 0, "b": 1, "c": 1}, k=2, rng=random.Random(seed)))


def test_the_first_draw_follows_the_weights():
    items = [item("full", 0), item("half", 0), item("floor", 0), item("none", 0)]
    w = {"full": 1.0, "half": 0.5, "floor": 0.05, "none": 0.0}
    rng = random.Random(11)
    first = {"full": 0, "half": 0, "floor": 0}
    n = 20000
    for _ in range(n):
        first[usage.weighted_sample(items, w, 1, rng)[0].id] += 1
    total = 1.55
    assert first["full"] / n == pytest.approx(1 / total, abs=0.02)
    assert first["half"] / n == pytest.approx(0.5 / total, abs=0.02)
    assert first["floor"] / n == pytest.approx(0.05 / total, abs=0.01)


def test_a_tiny_weight_does_not_collapse_into_ties():
    items = [item(f"{i}", 0) for i in range(300)]
    w = {i.id: usage.FLOOR for i in items}
    order = [x.id for x in usage.weighted_sample(items, w, rng=random.Random(9))]
    assert len(set(order)) == 300 and order != [i.id for i in items]              # all kept, a real shuffle
    class Zero:                                                                    # a generator that returns 0.0 must not crash the logarithm
        def random(self):
            return 0.0
    assert len(usage.weighted_sample(items[:5], {i.id: 0.5 for i in items[:5]}, rng=Zero())) == 5
    assert math.isfinite(usage.weight(10**7, ago(1), NOW, {}))

"""How much an object has been borrowed (SM's "lifetime use") and what that does to the chance of picking it.

Pure functions: no database, no clock (callers pass `now`), and the random generator is passed in as `rng` (the random module when none
is given), so every result can be checked in a test. Unknown stays unknown: an object with no usage data is never turned into 0 and is never reduced.

The rule: an object borrowed `soft_start` times or fewer keeps weight 1. Past that, the weight halves for every `half_life` borrows,
and never falls under FLOOR. A young object that got there fast is reduced up to twice as hard, an old one down to half as hard
(`age_effect` sets how much of that applies, 0 to 100 percent). From `hard_limit` borrows the weight is 0 (0 = never leave out).
The age effect is a design choice, not a documented industry rule: with age_effect 0 only the count matters."""
import math
import random
import re
from collections.abc import Mapping

DAY = 86400
FLOOR = 0.05                       # lowest weight of an object that is still allowed (0 only comes from hard_limit)
MAX_USES = 10_000_000              # a count above this is a data error: clamped, not trusted
MIN_AGE_DAYS = 30                  # a younger object is treated as 30 days old, so a brand-new one does not get an absurd rate
SPEED_MIN, SPEED_MAX = 0.5, 2.0    # the speed multiplier is kept inside this range

DEFAULTS = {"soft_start": 500, "half_life": 300, "hard_limit": 0, "age_effect": 50, "min_pool": 100}

# the fixed bands of the chart (from, to inclusive; None = open)
BAND_EDGES = ((0, 99), (100, 249), (250, 499), (500, 999), (1000, None))
BAND_KEYS = ("0-99", "100-249", "250-499", "500-999", "1000+")
UNKNOWN_BAND = "unknown"

_DIGITS = re.compile(r"^\+?([0-9]+)(?:\.0*)?$", re.ASCII)


# ----------------------------------------------------------------------------- reading the two fields SM sends
def parse_uses(value) -> int | None:
    """A borrow count from what the sender wrote: a non-negative integer or a numeric string ('1587', ' 1587 ').
    Missing, null, booleans, negatives, fractions and anything else that is not a number are unknown (None), never 0.
    A count above MAX_USES is clamped to MAX_USES."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return None if value < 0 else min(value, MAX_USES)
    if isinstance(value, float):
        if not math.isfinite(value) or value < 0 or value != int(value):
            return None
        return min(int(value), MAX_USES)
    if isinstance(value, str):
        m = _DIGITS.match(value.strip())
        if m is None:
            return None
        digits = m.group(1).lstrip("0") or "0"
        return MAX_USES if len(digits) > 12 else min(int(digits), MAX_USES)     # a very long run of digits is a huge count, not unknown
    return None


_MIN_EPOCH = 946684800             # 2000-01-01: an earlier date is a placeholder, the same rule as service.when_of
_MAX_EPOCH = 4102444800            # 2100-01-01


def parse_created(value) -> float | None:
    """When an object was created, as epoch seconds, from an ISO-8601 date string as SM writes it (read by service.when_of, so a date
    before the year 2000 is unreadable) or from a number of epoch seconds (a number in milliseconds is read too). Anything else is unknown."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, int) and abs(value) > _MAX_EPOCH * 1000:      # an absurdly large whole number: unknown, and never turned into a float (that raises)
            return None
        if not math.isfinite(value):
            return None
        v = float(value)
        if _MIN_EPOCH * 1000 <= v <= _MAX_EPOCH * 1000 and v > _MAX_EPOCH:       # milliseconds, as JavaScript counts them
            v /= 1000.0
        return v if _MIN_EPOCH <= v <= _MAX_EPOCH else None
    if isinstance(value, str):
        from .service import when_of                      # one place decides which dates are readable; imported here because service imports this module
        return when_of(value)
    return None


# ----------------------------------------------------------------------------- the weight
def clean_cfg(cfg=None) -> dict:
    """The settings as whole numbers inside their limits. Missing or unreadable values fall back to the defaults, so a damaged setting
    cannot break a pick."""
    out = {}
    for key, default in DEFAULTS.items():
        v = (cfg or {}).get(key, default) if isinstance(cfg, Mapping) or cfg is None else default
        out[key] = v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else default
    out["soft_start"] = max(1, out["soft_start"])
    out["half_life"] = max(1, out["half_life"])
    out["age_effect"] = min(100, out["age_effect"])
    out["min_pool"] = max(1, out["min_pool"])
    return out


def age_days(created, now) -> float | None:
    """Days since creation, or None when the creation date is unknown."""
    return None if created is None else (now - created) / DAY


def speed_multiplier(uses, created, now, cfg) -> float:
    """1 when the age is unknown. Otherwise how fast the object was borrowed compared with `soft_start` borrows in a year, as a square root
    kept between 0.5 and 2, then scaled by age_effect (0 gives 1, 100 gives the full value)."""
    age = age_days(created, now)
    if age is None or uses is None:
        return 1.0
    rate = uses / max(age, MIN_AGE_DAYS)
    raw = min(SPEED_MAX, max(SPEED_MIN, math.sqrt(rate / (cfg["soft_start"] / 365.0))))
    return 1.0 + (raw - 1.0) * cfg["age_effect"] / 100.0


def weight(uses, created, now, cfg=None) -> float:
    """The weight of one object, from 0 to 1. 1 when `uses` is unknown. See the module text for the rule."""
    if uses is None:
        return 1.0
    cfg = clean_cfg(cfg)
    if cfg["hard_limit"] > 0 and uses >= cfg["hard_limit"]:
        return 0.0
    if uses <= cfg["soft_start"]:
        return 1.0
    m = speed_multiplier(uses, created, now, cfg)
    return max(FLOOR, 0.5 ** ((uses - cfg["soft_start"]) * m / cfg["half_life"]))


def weights_of(items, now, cfg=None) -> dict[str, float]:
    """id -> weight for every item (anything with .id, .uses and .created)."""
    cfg = clean_cfg(cfg)
    return {it.id: weight(it.uses, it.created, now, cfg) for it in items}


def has_data(items) -> bool:
    return any(it.uses is not None for it in items)


# ----------------------------------------------------------------------------- bands, top, counts
def band_key(uses) -> str:
    """'0-99' ... '1000+', or 'unknown' when the count is unknown."""
    if uses is None:
        return UNKNOWN_BAND
    for key, (lo, hi) in zip(BAND_KEYS, BAND_EDGES):
        if uses >= lo and (hi is None or uses <= hi):
            return key
    return BAND_KEYS[0]


def _uses_of(x):
    return getattr(x, "uses", x)


def bands(items) -> tuple[list[dict], int]:
    """(the fixed bands with a count each, how many have unknown uses). `items` are objects with .uses, or plain counts."""
    counts = [0] * len(BAND_EDGES)
    unknown = 0
    for x in items:
        u = _uses_of(x)
        if u is None:
            unknown += 1
            continue
        counts[BAND_KEYS.index(band_key(u))] += 1
    return [{"from": lo, "to": hi, "count": n} for (lo, hi), n in zip(BAND_EDGES, counts)], unknown


def age_days_int(created, now) -> int | None:
    a = age_days(created, now)
    return None if a is None else max(0, int(a))


def top(items, n, weights=None, now=None) -> list[dict]:
    """The n most borrowed objects (unknown counts are left out), most borrowed first, ties by id. One dict per object:
    id, name, vi, uses, age_days (whole days, None without `now` or a creation date) and weight (None when `weights` has none)."""
    known = [it for it in items if it.uses is not None]
    known.sort(key=lambda it: (-it.uses, it.id))
    out = []
    for it in known[:max(0, n)]:
        w = None if weights is None else weights.get(it.id)
        out.append({"id": it.id, "name": it.name, "vi": it.vi, "uses": it.uses,
                    "age_days": None if now is None else age_days_int(it.created, now),
                    "weight": None if w is None else round(w, 4)})
    return out


def summary(items, now, cfg=None) -> dict:
    """Counts over a set of objects: known (has usage data), total, over_start (uses >= soft_start), excluded (weight 0),
    reduced (0 < weight < 1)."""
    cfg = clean_cfg(cfg)
    known = over = excluded = reduced = total = 0
    for it in items:
        total += 1
        if it.uses is None:
            continue
        known += 1
        if it.uses >= cfg["soft_start"]:
            over += 1
        w = weight(it.uses, it.created, now, cfg)
        if w <= 0:
            excluded += 1
        elif w < 1:
            reduced += 1
    return {"known": known, "total": total, "over_start": over, "excluded": excluded, "reduced": reduced}


# ----------------------------------------------------------------------------- choosing
def apply_pool_floor(candidates, weights, min_pool) -> dict[str, float]:
    """A copy of `weights` (id -> weight) for the candidates, with the weight-0 objects put back when too few are left.
    If the objects with weight above 0 number fewer than min_pool, weight-0 objects come back at FLOOR, least borrowed first
    (then by id), until min_pool is reached. The pool is never emptied. If there are fewer candidates than min_pool, all of them are kept."""
    cands = list(candidates)
    out = {it.id: float(weights.get(it.id, 1.0)) for it in cands}
    need = min(max(1, int(min_pool)), len(cands))
    alive = sum(1 for w in out.values() if w > 0)
    if alive >= need:
        return out
    dead = sorted((it for it in cands if out[it.id] <= 0), key=lambda it: (it.uses if it.uses is not None else 0, it.id))
    for it in dead:
        if alive >= need:
            break
        out[it.id] = FLOOR
        alive += 1
    return out


def weighted_sample(items, weights, k=None, rng=None) -> list:
    """The items in a random order where heavier ones tend to come first, without repeats, weight 0 skipped (Efraimidis and Spirakis:
    each item gets the key u ** (1 / w) and the larger keys come first; the logarithm of that key is used so a small weight cannot
    underflow to a tie). `weights` is a mapping from item.id, or a list in the order of `items`. Returns at most k items (all when k is None)."""
    rng = rng or random
    rows = list(items)
    if isinstance(weights, Mapping):
        ws = [weights.get(getattr(it, "id", it), 1.0) for it in rows]
    else:
        ws = list(weights)
        if len(ws) != len(rows):
            raise ValueError("weights must be as long as items")
    keyed = []
    for i, (it, w) in enumerate(zip(rows, ws)):
        if not isinstance(w, (int, float)) or isinstance(w, bool) or not math.isfinite(w) or w <= 0:
            continue
        u = 1.0 - rng.random()                       # in (0, 1], so the logarithm exists
        keyed.append((math.log(u) / w, i, it))
    keyed.sort(key=lambda t: (-t[0], t[1]))
    out = [it for _, _, it in keyed]
    return out if k is None else out[:max(0, k)]

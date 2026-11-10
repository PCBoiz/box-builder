"""The team's own sync script (v2.23.0) sends the catalog without borrow counts, and every sync replaces the whole catalog. A sync that carries no
counts must therefore keep the counts the last sync with counts brought, or one run of that script would erase them."""
import time

from fastapi.testclient import TestClient

from app import picker
from app.main import create_app
from tests.test_automation import make_client
from tests.test_backend import env  # noqa: F401  (env is a fixture)

TOK = {"X-Sync-Token": "tok"}


def rows_of(cat, uses=None, created=None, only=None):
    out = []
    for n, (oid, it) in enumerate(sorted(cat.items().items())):
        if only is not None and oid not in only:
            continue
        r = {"id": oid, "name": it.name, "vi": it.vi, "type": "Obj", "status": "ACTIVE", "avail": it.stock, "qty": it.stock, "loc": it.slot, "sector": "Hanoi-GPI"}
        if uses is not None:
            u = uses(n, oid)
            if u is not None:
                r["uses"] = u
                r["created"] = created(n) if created else None
        out.append(r)
    return out


def sync(client, rows):
    r = client.post("/api/catalog/sync", json={"objects": rows}, headers=TOK)
    assert r.status_code == 200, r.text
    return r.json()


def usage(client):
    r = client.get("/api/usage")
    assert r.status_code == 200
    return r.json()


def by_id(client):
    items = client.get("/api/objects?per=50&sort=uses").json()["items"]
    return {o["id"]: o for o in items}


def test_a_sync_without_counts_keeps_the_counts_the_last_sync_with_counts_brought(env):
    client, s, cat = make_client(env)
    full = rows_of(cat, uses=lambda n, oid: 600 + n if n < 5 else (n % 400), created=lambda n: "2025-01-%02dT00:00:00Z" % (1 + n % 27))
    before = time.time()
    first = sync(client, full)
    assert first["with_uses"] >= len(full) - 1 and first["uses_carried_over"] is False
    u1 = usage(client)
    assert u1["known"] >= len(full) - 1 and u1["over_start"] >= 5 and before - 1 <= u1["usage_at"] <= time.time() + 1

    second = sync(client, rows_of(cat))                                       # the team script: no counts at all
    assert second["uses_carried_over"] is True and second["with_uses"] == first["with_uses"]
    u2 = usage(client)
    assert u2["known"] == u1["known"] and u2["over_start"] == u1["over_start"] and u2["top"] == u1["top"]
    assert u2["usage_at"] == u1["usage_at"]                                    # still says when SM itself last sent them, not when the carry happened
    top_id = u1["top"][0]["id"]
    assert client.get(f"/api/objects?q={top_id}&per=50").json()["items"][0]["uses"] == u1["top"][0]["uses"]
    assert client.get(f"/api/objects?q={top_id}&per=50").json()["items"][0]["created"] == "2025-01-05"        # the most borrowed is the fifth row, 600 + 4


def test_a_sync_with_any_count_is_taken_as_it_is(env):
    client, s, cat = make_client(env)
    sync(client, rows_of(cat, uses=lambda n, oid: 700, created=lambda n: "2025-01-01T00:00:00Z"))
    assert usage(client)["over_start"] > 50
    ids = sorted(cat.items())
    sync(client, rows_of(cat, uses=lambda n, oid: 10 if oid == ids[0] else None))                 # one count only
    u = usage(client)
    assert u["known"] == 1 and u["over_start"] == 0                                                 # the rest is unknown now, not carried


def test_an_object_that_is_new_in_a_sync_without_counts_stays_unknown_and_a_gone_one_is_dropped(env):
    client, s, cat = make_client(env)
    every = rows_of(cat)                                          # taken once: the live catalog changes with every sync, so do not ask it again
    ids = [r["id"] for r in every]
    half, rest = ids[:900], ids[900:]                              # 900 is more than half of the catalog, so the sync check lets the first one through
    sync(client, [{**r, "uses": 800, "created": "2025-01-01T00:00:00Z"} for r in every if r["id"] in set(half)])
    assert usage(client)["known"] == len(half)
    sync(client, every)                                            # everything, with no counts at all
    u = usage(client)
    assert u["known"] == len(half) and u["unknown"] == len(rest)
    sync(client, [r for r in every if r["id"] in set(rest) | set(half[:60])])                       # most of the first group is gone from SM
    assert usage(client)["known"] == 60


def test_an_object_that_is_out_of_stock_for_one_sync_without_counts_keeps_its_count(env):
    """The most borrowed objects are often borrowed out (stock 0): they are not in the usable catalog at that moment, but their count must survive."""
    client, s, cat = make_client(env)
    every = rows_of(cat)
    x = every[3]["id"]
    sync(client, [{**r, "uses": 1500 if r["id"] == x else 10, "created": "2025-02-01T00:00:00Z"} for r in every])
    assert client.get(f"/api/objects?q={x}&per=50").json()["items"][0]["uses"] == 1500
    sync(client, [{**r, "avail": 0, "qty": 0} if r["id"] == x else r for r in every])                    # the team script, and x is borrowed out
    assert x not in {o["id"] for o in client.get("/api/objects?per=50&q=" + x).json()["items"]}          # it is not usable right now
    sync(client, every)                                                                                  # and back in stock, still no counts
    o = client.get(f"/api/objects?q={x}&per=50").json()["items"][0]
    assert o["uses"] == 1500 and o["created"] == "2025-02-01" and usage(client)["known"] >= len(every) - 1


def test_with_no_counts_ever_sent_nothing_is_invented(env):
    client, s, cat = make_client(env)
    r = sync(client, rows_of(cat))
    assert r["with_uses"] == 0 and r["uses_carried_over"] is False
    u = usage(client)
    assert u["known"] == 0 and u["usage_at"] is None and u["over_start"] == 0


def test_the_carried_counts_survive_a_restart(env):
    client, s, cat = make_client(env)
    sync(client, rows_of(cat, uses=lambda n, oid: 900 + n, created=lambda n: "2025-03-01T00:00:00Z"))
    sync(client, rows_of(cat))
    top = usage(client)["top"]
    again = TestClient(create_app(s, llm=picker.MockLLM(), catalog=type(cat)(s.catalog_path)))        # a new process reading the saved snapshot
    u = again.get("/api/usage").json()
    assert u["top"] == top and u["known"] > 0 and u["usage_at"] is not None


def test_a_wrong_token_or_a_short_list_changes_nothing(env):
    client, s, cat = make_client(env)
    sync(client, rows_of(cat, uses=lambda n, oid: 650, created=lambda n: "2025-01-01T00:00:00Z"))
    before = usage(client)
    assert client.post("/api/catalog/sync", json={"objects": rows_of(cat)}, headers={"X-Sync-Token": "x"}).status_code == 401
    assert client.post("/api/catalog/sync", json={"objects": rows_of(cat)[:10]}, headers=TOK).status_code == 422
    assert usage(client) == before

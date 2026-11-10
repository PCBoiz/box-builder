"""The catalog sync must never leave the app with an empty or half-empty kho, and the sync tool needs a way to test its settings."""
from tests.test_automation import live_rows, make_client, sync
from tests.test_backend import env  # noqa: F401  (env is a fixture)

TOK = {"X-Sync-Token": "tok"}


def post(client, rows, **extra):
    return client.post("/api/catalog/sync", json={"objects": rows, **extra}, headers=TOK)


def test_check_endpoint_tells_what_is_wrong(env):
    client, s, cat = make_client(env)
    assert client.get("/api/sync/check").status_code == 401                              # no code sent
    assert client.get("/api/sync/check", headers={"X-Sync-Token": "wrong"}).status_code == 401
    ok = client.get("/api/sync/check", headers=TOK)
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert ok.json()["catalog"]["source"] == "csv" and ok.json()["vi_fallback"] is True and ok.json()["min_usable"] == 50
    s.sync_token = ""                                                                    # a server with no SYNC_TOKEN says so plainly
    from fastapi.testclient import TestClient
    from app import picker
    from app.main import create_app
    blank = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    assert blank.get("/api/sync/check", headers=TOK).status_code == 503


def test_rows_without_vietnamese_names_use_the_csv_names(env):
    """SM's object list has no Vietnamese name. Objects that the CSV knows keep the CSV's Vietnamese name."""
    client, s, cat = make_client(env)
    rows = live_rows(cat)
    expect = {r["id"]: r["vi"] for r in rows}
    for r in rows:
        r["vi"] = ""                                                                     # what SM really sends
    out = sync(client, rows)
    assert out["usable"] == len(rows) and out["skipped"]["no_vietnamese_name"] == 0
    got = {o["id"]: o["vi"] for o in client.get("/api/objects?per=200&page=1").json()["items"]}
    assert all(got[i] == expect[i] for i in got)
    assert client.get("/api/state").json()["catalog"]["source"] == "live"


def test_a_sync_that_would_empty_the_kho_is_refused_and_changes_nothing(env):
    client, s, cat = make_client(env)
    before = client.get("/api/state").json()["catalog"]
    unknown = [{"id": f"{800000 + i}", "name": f"New {i}", "vi": "", "type": "Obj", "status": "ACTIVE", "avail": 5, "qty": 5,
                "loc": "A", "sector": "Hanoi-GPI"} for i in range(200)]                  # not in the CSV, and no Vietnamese name
    r = post(client, unknown)
    assert r.status_code == 422
    assert "Kho không bị thay đổi" in r.json()["detail"] and "tiếng Việt: 200" in r.json()["detail"]
    after = client.get("/api/state").json()["catalog"]
    assert after == before and after["source"] == "csv"                                  # nothing was replaced
    # half the kho missing is refused too, unless the caller says it is deliberate
    half = live_rows(cat)[: len(cat.items()) // 3]
    assert post(client, half).status_code == 422
    assert post(client, half, force=True).status_code == 200
    # a normal full list is accepted
    assert post(client, live_rows(cat), force=False).status_code == 200


def test_other_sectors_do_not_count_towards_the_minimum(env):
    client, s, cat = make_client(env)
    rows = live_rows(cat)
    for r in rows:
        r["sector"] = "Hanoi-4F"                                                         # a list that is entirely another sector
    r = post(client, rows)
    assert r.status_code == 422 and "khác khu" in r.json()["detail"]

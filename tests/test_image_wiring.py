"""Picture URLs: stored only when they pass app/images.py, survive a restart, never fetched by the server."""
from fastapi.testclient import TestClient

from app import picker
from app.main import create_app
from tests.test_automation import live_rows, make_client, sync
from tests.test_backend import env, wait_settled  # noqa: F401  (env is a fixture)


def with_images(cat, picks):
    rows = live_rows(cat)
    ids = [r["id"] for r in rows[:len(picks)]]
    for row, extra in zip(rows, picks):
        row.update(extra)
    return rows, ids


def obj(client, oid):
    return client.get(f"/api/objects?q={oid}").json()["items"][0]


def test_only_clean_urls_are_kept_and_detail_link_is_always_there(env):
    client, s, cat = make_client(env)
    rows, ids = with_images(cat, [
        {"image_url": "https://sm.config.inc/media/a.jpg"},
        {"img": "/uploads/b.png"},
        {"thumbnail": "https://evil.example.com/x.png"},
        {"image": "javascript:alert(1)"},
        {"images": [{"url": "http://sm.config.inc/insecure.png"}, {"src": "https://sm.config.inc/ok.webp"}]},
        {"image_url": "https://sm.config.inc@evil.example.com/y.png"},
    ])
    sync(client, rows)
    got = [obj(client, i) for i in ids]
    assert got[0]["img"] == "https://sm.config.inc/media/a.jpg"
    assert got[1]["img"] == "https://sm.config.inc/uploads/b.png"             # relative path resolved against the SM base
    assert got[2]["img"] == "" and got[3]["img"] == "" and got[5]["img"] == ""
    assert got[4]["img"] == "https://sm.config.inc/ok.webp"                   # first usable entry wins, http is skipped
    assert all(o["detail"] == f"https://sm.config.inc/object_detail/{o['id']}" for o in got)
    assert obj(client, rows[10]["id"])["img"] == ""                           # no picture sent: empty, not an error


def test_pictures_survive_a_restart_and_reach_box_items_and_search(env):
    client, s, cat = make_client(env)
    rows, ids = with_images(cat, [{"image_url": "https://sm.config.inc/media/keep.jpg"}])
    sync(client, rows)
    again = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))      # new app on the same database
    assert obj(again, ids[0])["img"] == "https://sm.config.inc/media/keep.jpg"
    hit = again.get(f"/api/search?q={ids[0]}").json()
    assert hit and hit[0]["img"].endswith("keep.jpg") and hit[0]["detail"].endswith(ids[0])
    r = again.post("/api/boxes", json={"module": "M8", "label": "Box A1", "theme": "t", "count": 1}).json()["id"]
    wait_settled(again, r)
    again.post(f"/api/boxes/{r}/items", json={"object_id": ids[0], "qty": 1})
    box = next(b for b in again.get("/api/state").json()["boxes"] if b["id"] == r)
    item = next((i for i in box["items"] if i["id"] == ids[0]), None) or box["items"][0]
    assert "img" in item and item["detail"].endswith(item["id"])


def test_image_hosts_setting_controls_which_hosts_are_accepted(env):
    s, cat = env
    s.sync_token = "tok"
    s.image_hosts = "cdn.example.com"
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    rows, ids = with_images(cat, [{"image_url": "https://cdn.example.com/p/1.jpg"}, {"image_url": "https://sm.config.inc/p/2.jpg"}])
    sync(client, rows)
    assert obj(client, ids[0])["img"] == "https://cdn.example.com/p/1.jpg"
    assert obj(client, ids[1])["img"] == ""                                   # not in the configured list

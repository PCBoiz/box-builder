"""Box sizes follow the stage (docs/SC_PROCESS.md): A1 holds 12, B2 and B3 hold 21, anything else the default."""
import time

from fastapi.testclient import TestClient

from app import picker
from app.main import create_app
from app.settings import DEFAULT_STAGE_COUNTS, Settings, parse_stage_counts
from tests.test_automation import make_client, wait_all_settled
from tests.test_backend import env, wait_settled  # noqa: F401  (env is a fixture)


def test_parse_stage_counts_keeps_defaults_ignores_junk_and_clamps():
    assert parse_stage_counts(None) == DEFAULT_STAGE_COUNTS == {"A1": 12, "B2": 21, "B3": 21}
    assert parse_stage_counts("a1=10, B2=18") == {"A1": 10, "B2": 18, "B3": 21}
    assert parse_stage_counts("A1=0,B3=999,X=abc,,=5,C4=7")["A1"] == 1                # clamped up
    assert parse_stage_counts("B3=999")["B3"] == 60                                   # clamped down
    out = parse_stage_counts("A1=0,B3=999,X=abc,,=5,C4=7")
    assert "X" not in out and out["C4"] == 7                                          # junk ignored, a new stage can be added


def test_settings_read_the_env_value(monkeypatch):
    monkeypatch.setenv("STAGE_COUNTS", "A1=14,B2=20")
    s = Settings.from_env()
    assert s.stage_counts["A1"] == 14 and s.stage_counts["B2"] == 20 and s.stage_counts["B3"] == 21


def test_a_new_box_gets_the_size_of_its_stage(env):
    client, s, _ = make_client(env)
    cfg = client.get("/api/config").json()
    assert cfg["stage_counts"] == {"A1": 12, "B2": 21, "B3": 21} and cfg["default_count"] == 21
    made = {}
    for label in ("Box A1", "Box B2", "Box B3", "Box C9", "Phiếu tự đặt tên"):
        made[label] = client.post("/api/boxes", json={"module": "M8", "label": label, "theme": "t"}).json()["id"]
    boxes = wait_all_settled(client, list(made.values()))
    got = {b["label"]: (b["count"], len(b["items"])) for b in boxes.values()}
    assert got == {"Box A1": (12, 12), "Box B2": (21, 21), "Box B3": (21, 21), "Box C9": (21, 21), "Phiếu tự đặt tên": (21, 21)}


def test_an_explicit_count_beats_the_stage(env):
    client, s, _ = make_client(env)
    bid = client.post("/api/boxes", json={"module": "M8", "label": "Box A1", "theme": "t", "count": 5}).json()["id"]
    assert wait_settled(client, bid)["count"] == 5


def test_the_env_setting_changes_the_size(env):
    s, cat = env
    s.sync_token = "tok"
    s.stage_counts = parse_stage_counts("A1=10")
    client = TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat))
    bid = client.post("/api/boxes", json={"module": "M8", "label": "Box A1", "theme": "t"}).json()["id"]
    assert wait_settled(client, bid)["count"] == 10


def test_a_section_batch_sizes_each_box_and_all_can_be_approved(env):
    """One section = Box B2, Box B3, Box A1 sharing a code. The plan carries the stage sizes, and a 12-object A1 is complete."""
    client, s, _ = make_client(env)
    plan = client.post("/api/batches/plan", json={"brief": "bếp\nvườn\ndọn dẹp", "module": "M8", "labels": ["Box B2", "Box B3", "Box A1"], "code": "M8-02"}).json()
    assert {b["label"]: b["count"] for b in plan["boxes"]} == {"Box B2": 21, "Box B3": 21, "Box A1": 12}
    r = client.post("/api/batches", json={"boxes": plan["boxes"]})
    assert r.status_code == 201
    done = wait_all_settled(client, r.json()["ids"])
    assert {b["label"]: len(b["items"]) for b in done.values()} == {"Box B2": 21, "Box B3": 21, "Box A1": 12}
    for bid in done:
        assert client.post(f"/api/boxes/{bid}/approve").status_code == 200           # 12 of 12 is complete for A1
    feed = {b["stage"]: b["item_count"] for b in client.get("/api/sheet-boxes").json()["boxes"]}
    assert feed == {"B2": 21, "B3": 21, "A1": 12}
    # a batch spec with no count at all (an API client) also gets its stage size
    spec = [{"module": "M9", "label": "Box A1", "theme": "t"}]
    bid = client.post("/api/batches", json={"boxes": spec}).json()["ids"][0]
    assert wait_settled(client, bid)["count"] == 12

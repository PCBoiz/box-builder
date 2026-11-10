"""What the admin watches (app/overview.py): boxes per person and state, and what looks wrong. The admin approves nothing here."""
import time

from app import service
from tests.test_auth_app import SAME, _own_box, _people, second_browser
from tests.test_backend import conn_of, env  # noqa: F401  (env is a fixture)


def test_the_admin_sees_boxes_per_person_and_state_and_where_something_looks_wrong(env):
    admin, s, cat = _people(env)
    hai, hung = second_browser(admin, "2002"), second_browser(admin, "3003")
    a1, _ = _own_box(hai, "Box A1")                                                 # Hải: a draft
    b2, _ = _own_box(hai, "Box B2")
    hai.post(f"/api/my/boxes/{b2}/approve", headers=SAME)                         # and an approved box
    near, _ = _own_box(hung, "Box A1", module="M5")                                            # Hưng: a draft that is about to expire
    failed, _ = _own_box(hung, "Box B3", module="M5")
    old, _ = _own_box(hung, "Box A1", module="M3")
    gone, _ = _own_box(hung, "Box B2", module="M3")
    for bid in (old, gone):
        hung.post(f"/api/my/boxes/{bid}/approve", headers=SAME)
    service.mark_pushed(conn_of(s), gone, "SM-9")                                  # one of them reached SM
    c = conn_of(s)
    now = time.time()
    c.execute("UPDATE boxes SET updated_at=? WHERE id=?", (now - (s.draft_ttl_hours - 1) * 3600, near))        # one hour left
    c.execute("UPDATE boxes SET status='error', message='x' WHERE id=?", (failed,))
    c.execute("UPDATE boxes SET updated_at=? WHERE id=?", (now - 30 * 3600, old))                               # approved 30 hours ago, not in SM

    o = admin.get("/api/admin/overview").json()
    assert o["totals"] == {"generating": 0, "draft": 2, "approved": 2, "pushed": 1, "error": 1}
    rows = {r["owner"]: r for r in o["owners"]}
    assert rows["2002"]["name"] == "Hải" and (rows["2002"]["boxes"], rows["2002"]["draft"], rows["2002"]["approved"]) == (2, 1, 1)
    assert (rows["3003"]["boxes"], rows["3003"]["draft"], rows["3003"]["error"], rows["3003"]["approved"], rows["3003"]["pushed"]) == (4, 1, 1, 1, 1)
    text = " | ".join(p["msg"] for p in o["problems"])
    assert "sắp tự mất" in text and "M5 Box A1 (Hưng)" in text                                                  # the draft with one hour left
    assert "chưa chọn được đồ" in text and "M5 Box B3 (Hưng)" in text
    assert "chưa lên SM" in text and "M3 Box A1 (Hưng)" in text and "M3 Box B2" not in text.split("chưa lên SM")[0][-60:]
    assert {p["level"] for p in o["problems"]} == {"warn", "bad"}


def test_a_quiet_process_has_no_problems_and_only_the_admin_can_look(env):
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    _own_box(hai, "Box A1")
    o = admin.get("/api/admin/overview").json()
    assert o["problems"] == [] and o["totals"]["draft"] == 1 and o["draft_ttl_hours"] == s.draft_ttl_hours
    assert hai.get("/api/admin/overview").status_code == 403


def test_with_nothing_made_yet_the_overview_is_empty_and_never_fails(env):
    admin, s, cat = _people(env)
    o = admin.get("/api/admin/overview").json()
    assert o["owners"] == [] and o["problems"] == [] and sum(o["totals"].values()) == 0


def test_a_box_with_no_known_owner_is_listed_as_such(env):
    admin, s, cat = _people(env)
    admin.post("/api/boxes", json={"module": "M7", "label": "Box A1", "theme": "t", "count": 3, "owner": ""}, headers=SAME)
    conn_of(s).execute("UPDATE boxes SET owner=''")
    o = admin.get("/api/admin/overview").json()
    assert o["owners"][0]["owner"] == "" and o["owners"][0]["boxes"] == 1

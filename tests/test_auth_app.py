"""Login on the real app: which routes are open, which need a session, and the whole path of a person who makes a box."""
import re
import time

from fastapi.testclient import TestClient

from app import auth, picker
from app.main import create_app
from tests.test_backend import env  # noqa: F401  (env is a fixture)

SAME = {"Origin": "http://testserver"}
TOK = {"X-Sync-Token": "tok"}

PUBLIC = {"/login", "/api/config", "/api/auth/status", "/api/auth/login", "/api/auth/logout", "/box_builder_sm.user.js", "/box_builder_sm.txt"}
MACHINE = {("GET", "/api/taskboxes"), ("GET", "/api/sheet-boxes"), ("POST", "/api/catalog/sync"), ("POST", "/api/sm/boxes/sync"),
           ("GET", "/api/sync/check"), ("POST", "/api/boxes/1/pushed"), ("DELETE", "/api/boxes/1/pushed")}
USER = {("GET", "/dung"), ("GET", "/my/print"), ("GET", "/api/my/summary"), ("POST", "/api/my/boxes"), ("DELETE", "/api/my/boxes/1"),
        ("POST", "/api/my/boxes/1/regenerate"), ("POST", "/api/my/boxes/1/items/1/replace"), ("POST", "/api/my/boxes/1/approve"), ("POST", "/api/my/boxes/1/reopen"),
        ("POST", "/api/my/boxes/1/fill"), ("GET", "/api/my/plan"), ("PUT", "/api/my/plan"), ("GET", "/api/my/recent")}


def app_client(env, admin_ids="1001", admin_code=""):
    s, cat = env
    s.sync_token, s.admin_ids, s.admin_code = "tok", admin_ids, admin_code
    return TestClient(create_app(s, llm=picker.MockLLM(), catalog=cat), follow_redirects=False), s, cat


def sign_in(client, pid, code=""):
    r = client.post("/api/auth/login", json={"id": pid, "code": code}, headers={**SAME, "X-Forwarded-For": f"10.9.{int(time.time()) % 250}.{abs(hash(pid)) % 250}"})
    assert r.status_code == 200, r.text
    return r.json()


def second_browser(client, pid):
    other = TestClient(client.app, follow_redirects=False)
    sign_in(other, pid)
    return other


def _walk(rs):
    """Routes of the app, including those added with include_router (this FastAPI wraps them, with no prefix used here)."""
    for r in rs:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _walk(inner.routes)
        else:
            yield r


def routes(client):
    out = set()
    for r in _walk(client.app.routes):
        for m in getattr(r, "methods", None) or ():
            if m not in ("HEAD", "OPTIONS"):
                out.add((m, re.sub(r"\{[^}]+\}", "1", r.path)))
    return out


def wait_box(client, box_id, path="/api/my/summary", timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        boxes = {b["id"]: b for b in client.get(path).json()["boxes"]}
        if box_id in boxes and boxes[box_id]["status"] != "generating":
            return boxes[box_id]
        time.sleep(0.1)
    raise AssertionError("the box never finished")


# ----------------------------------------------------------------------------- the route inventory
def test_every_route_is_either_named_here_as_open_or_needs_a_session(env):
    """If someone adds a route it is admin-only until they list it in app/auth.py AND here. If someone renames a route the scripts use,
    this fails instead of silently leaving the old name open or the new one shut."""
    client, s, cat = app_client(env)
    found = routes(client)
    static = {p for m, p in found if p.startswith("/static")}
    public = {p for m, p in found if auth.klass(m, p) == "public"} - static
    machine = {(m, p) for m, p in found if auth.klass(m, p) == "machine"}
    user = {(m, p) for m, p in found if auth.klass(m, p) == "user"}
    assert public == PUBLIC
    assert machine == MACHINE
    assert user == USER
    for kind, wanted in (("public", PUBLIC), ("user", {p for _, p in USER}), ("machine", {p for _, p in MACHINE})):    # nothing listed in auth.py is stale
        assert wanted <= {p for _, p in found}, kind


def test_every_other_route_refuses_a_visitor_with_no_session(env):
    client, s, cat = app_client(env)
    open_ = PUBLIC | {p for _, p in MACHINE}
    checked = 0
    for method, path in sorted(routes(client)):
        if path in open_ or path.startswith("/static") or (method, path) in MACHINE:
            continue
        r = client.request(method, path, headers=SAME)
        assert r.status_code in (401, 302), (method, path, r.status_code)
        checked += 1
    assert checked >= 40                                                  # the whole admin API was looked at, not a short list


def test_pages_send_a_visitor_to_login_and_each_role_to_its_own_page(env):
    client, s, cat = app_client(env)
    r = client.get("/")
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/"
    assert client.get("/login").status_code == 200 and "Đăng nhập" in client.get("/login").text
    sign_in(client, "1001")
    assert client.get("/").status_code == 200 and "Box builder" in client.get("/").text
    admin_add = client.post("/api/admin/people", json={"text": "2002, Hải, M2"}, headers=SAME)
    assert admin_add.status_code == 200
    user = second_browser(client, "2002")
    r = user.get("/")
    assert r.status_code == 302 and r.headers["location"] == "/dung"
    assert user.get("/dung").status_code == 200


# ----------------------------------------------------------------------------- what the team's scripts rely on
def test_the_scripts_in_the_browser_tabs_still_get_through_with_login_on(env):
    client, s, cat = app_client(env)
    assert client.get("/api/taskboxes?status=approved").status_code == 200
    assert client.get("/api/taskboxes?status=approved&pending=true").status_code == 200
    assert client.get("/api/sheet-boxes").status_code == 200
    assert client.get("/api/sync/check", headers=TOK).json()["ok"] is True
    r = client.post("/api/catalog/sync", json={"objects": []}, headers={"X-Sync-Token": "wrong"})
    assert r.status_code == 401 and r.json()["detail"] == "Sai mã đồng bộ."                # the handler answered, not the login gate
    r = client.post("/api/sm/boxes/sync", json={"boxes": []}, headers={"X-Sync-Token": "wrong"})
    assert r.status_code == 401 and r.json()["detail"] == "Sai mã đồng bộ."
    r = client.post("/api/boxes/999/pushed", json={"sm_box_id": "SM-1"}, headers=TOK)
    assert r.status_code == 404                                                            # reached the handler: there is no such box
    r = client.post("/api/boxes/999/pushed", json={"sm_box_id": "SM-1"})
    assert r.status_code == 401 and r.json()["detail"] == "Sai mã đồng bộ."
    assert client.get("/box_builder_sm.user.js").status_code == 200 and client.get("/box_builder_sm.txt").status_code == 200
    assert client.get("/api/config").status_code == 200




def test_with_no_admin_named_the_whole_app_works_as_before_and_the_user_page_works_on_every_box(env):
    client, s, cat = app_client(env, admin_ids="")
    assert client.get("/").status_code == 200 and client.get("/api/state").status_code == 200 and client.get("/dung").status_code == 200
    sm = client.get("/api/my/summary").json()
    assert sm["person"] is None and sm["open_mode"] is True and sm["boxes"] == []
    box_id = client.post("/api/boxes", json={"module": "M7", "label": "Box B2", "theme": "t", "count": 3}, headers=SAME).json()["id"]
    assert [b["id"] for b in client.get("/api/my/summary").json()["boxes"]] == [box_id]


# ----------------------------------------------------------------------------- a person who makes a box
def test_a_user_makes_a_box_the_admin_approves_it_and_every_step_is_on_record(env):
    admin, s, cat = app_client(env)
    sign_in(admin, "1001")
    assert admin.post("/api/admin/people", json={"text": "2002, Hải, M2\n3003, Hưng, M3"}, headers=SAME).status_code == 200
    hai, hung = second_browser(admin, "2002"), second_browser(admin, "3003")

    sm = hai.get("/api/my/summary").json()
    assert sm["person"] == {"id": "2002", "name": "Hải", "module": "M2", "admin": False} and sm["open_mode"] is False
    assert [x["label"] for x in sm["stages"]] == ["Box A1", "Box B2", "Box B3"] or {x["label"] for x in sm["stages"]} == {"Box A1", "Box B2", "Box B3"}
    r = hai.post("/api/my/boxes", json={"module": "M2", "label": "Box B2", "theme": "nấu ăn"}, headers=SAME)
    assert r.status_code == 201, r.text
    box_id = r.json()["id"]
    box = wait_box(hai, box_id)
    assert box["status"] == "draft" and box["status_text"] == "Chờ duyệt" and len(box["items"]) == box["count"] == 21
    assert {"pos", "id", "qty", "name", "vi", "category", "slot"} <= set(box["items"][0])

    assert hung.get("/api/my/summary").json()["boxes"] == []                                      # someone else's box is not shown
    assert hung.delete(f"/api/my/boxes/{box_id}", headers=SAME).status_code == 404                # nor can it be reached by guessing the number
    assert hung.post(f"/api/my/boxes/{box_id}/regenerate", headers=SAME).status_code == 404
    assert hung.post(f"/api/my/boxes/{box_id}/items/1/replace", headers=SAME).status_code == 404
    other_print = hung.get(f"/my/print?ids={box_id}")
    assert other_print.status_code == 200 and box["items"][0]["id"] not in other_print.text
    assert box["items"][0]["id"] in hai.get(f"/my/print?ids={box_id}").text

    assert hai.post(f"/api/boxes/{box_id}/approve", headers=SAME).status_code == 403              # approving is the admin's
    assert hai.get("/api/state").status_code == 403

    admin_view = {b["id"]: b for b in admin.get("/api/state").json()["boxes"]}
    assert admin_view[box_id]["owner"] == "2002"
    assert admin.post(f"/api/boxes/{box_id}/approve", headers=SAME).status_code == 200
    mine = {b["id"]: b for b in hai.get("/api/my/summary").json()["boxes"]}[box_id]
    assert mine["status"] == "approved" and mine["status_text"] == "Đã duyệt"
    assert hai.delete(f"/api/my/boxes/{box_id}", headers=SAME).status_code == 409                 # approved: only the admin changes it
    assert hai.post(f"/api/my/boxes/{box_id}/regenerate", headers=SAME).status_code == 409

    entries = admin.get("/api/admin/audit?n=100").json()["entries"]
    seen = {(e["actor"], e["action"], e["box_id"]) for e in entries}
    assert ("2002", "box_create", box_id) in seen and ("1001", "box_approve", box_id) in seen and ("2002", "login", None) in seen


def test_while_login_is_off_nobody_can_plant_an_admin_for_the_day_it_is_switched_on(env):
    client, s, cat = app_client(env, admin_ids="")
    r = client.post("/api/admin/people", json={"text": "9999, x, M1, admin"}, headers=SAME)
    assert r.status_code == 409 and "bật đăng nhập" in r.json()["detail"]
    assert client.delete("/api/admin/people/9999", headers=SAME).status_code == 409
    assert client.get("/api/admin/people").json()["people"] == []
    again, _, _ = app_client(env, admin_ids="1111")                                                    # the owner sets ADMIN_IDS later
    assert again.post("/api/auth/login", json={"id": "9999"}, headers=SAME).status_code == 401           # nothing was kept, so the planted ID is not on the list






def _people(env, text="2002, Hải\n3003, Hưng"):
    admin, s, cat = app_client(env)
    sign_in(admin, "1001")
    admin.post("/api/admin/people", json={"text": text}, headers=SAME)
    return admin, s, cat


def _own_box(person, label="Box A1", module="M2"):
    box_id = person.post("/api/my/boxes", json={"module": module, "label": label, "theme": "t"}, headers=SAME).json()["id"]
    return box_id, wait_box(person, box_id)


def test_a_person_approves_their_own_box_and_the_team_script_can_then_read_it(env):
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    box_id, box = _own_box(hai)
    assert box["status"] == "draft" and box["status_text"] == "Chờ duyệt"
    assert admin.get("/api/taskboxes?status=approved").json()["boxes"] == []                          # nothing for the team script yet
    assert hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME).status_code == 200
    mine = {b["id"]: b for b in hai.get("/api/my/summary").json()["boxes"]}[box_id]
    assert mine["status"] == "approved" and mine["status_text"] == "Đã duyệt"
    seen = TestClient(admin.app).get("/api/taskboxes?status=approved").json()["boxes"]                 # the team's script has no login
    assert len(seen) == 1 and len(seen[0]["contents"]) == box["count"]
    assert hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME).status_code == 200               # pressing twice does no harm
    entries = {(e["actor"], e["action"], e["box_id"]) for e in admin.get("/api/admin/audit?n=100").json()["entries"]}
    assert ("2002", "box_approve", box_id) in entries


def test_nobody_approves_or_reopens_someone_elses_box_through_the_user_routes(env):
    admin, s, cat = _people(env)
    hai, hung = second_browser(admin, "2002"), second_browser(admin, "3003")
    box_id, _ = _own_box(hai)
    assert hung.post(f"/api/my/boxes/{box_id}/approve", headers=SAME).status_code == 404
    hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME)
    assert hung.post(f"/api/my/boxes/{box_id}/reopen", headers=SAME).status_code == 404
    assert {b["id"]: b for b in admin.get("/api/state").json()["boxes"]}[box_id]["status"] == "approved"


def test_a_box_that_is_not_full_or_still_being_picked_is_not_approved(env):
    from tests.test_backend import conn_of
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    box_id, box = _own_box(hai)
    conn_of(s).execute("DELETE FROM box_items WHERE box_id=? AND pos=1", (box_id,))
    r = hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME)
    assert r.status_code == 422 and "Điền đủ món" in r.json()["detail"]
    conn_of(s).execute("UPDATE boxes SET status='generating' WHERE id=?", (box_id,))
    r = hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME)
    assert r.status_code == 409 and "chưa chọn xong" in r.json()["detail"]
    conn_of(s).execute("UPDATE boxes SET status='error' WHERE id=?", (box_id,))
    assert hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME).status_code == 409


def test_a_person_takes_the_approval_back_until_the_box_is_in_sm(env):
    from app import service
    from tests.test_backend import conn_of
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    box_id, _ = _own_box(hai)
    assert hai.post(f"/api/my/boxes/{box_id}/reopen", headers=SAME).status_code == 409                  # a draft has nothing to take back
    hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME)
    assert hai.delete(f"/api/my/boxes/{box_id}", headers=SAME).status_code == 409                      # approved: take the approval back first
    assert "Bỏ duyệt" in hai.delete(f"/api/my/boxes/{box_id}", headers=SAME).json()["detail"]
    assert hai.post(f"/api/my/boxes/{box_id}/reopen", headers=SAME).status_code == 200
    assert {b["id"]: b for b in hai.get("/api/my/summary").json()["boxes"]}[box_id]["status"] == "draft"
    hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME)
    service.mark_pushed(conn_of(s), box_id, "SM-77")                                                  # the team script put it in SM
    r = hai.post(f"/api/my/boxes/{box_id}/reopen", headers=SAME)
    assert r.status_code == 409 and "SM" in r.json()["detail"]
    assert {b["id"]: b for b in hai.get("/api/my/summary").json()["boxes"]}[box_id]["pushed"] is True
    entries = {(e["actor"], e["action"]) for e in admin.get("/api/admin/audit?n=100").json()["entries"]}
    assert ("2002", "box_reopen") in entries


def test_the_auto_approve_switch_approves_a_full_box_for_the_person_when_the_team_turns_it_on(env):
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    first_id, first = _own_box(hai, "Box A1")
    assert first["status"] == "draft"                                                                 # off by default: the person approves
    assert admin.put("/api/automations/auto_approve", json={"enabled": True}, headers=SAME).status_code == 200
    second_id, second = _own_box(hai, "Box B3")
    assert second["status"] == "approved"                                                             # on: the same switch the admin's boxes follow


def test_the_admin_sees_unapproved_boxes_as_information_not_as_a_task(env):
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    _own_box(hai)
    todo = [t for t in admin.get("/api/automations").json()["todo"] if t["kind"] == "review"]
    assert len(todo) == 1 and todo[0]["level"] == "info" and "tự duyệt" in todo[0]["msg"] and "chờ bạn" not in todo[0]["msg"]


def test_the_summary_is_cheap_when_nothing_changed_and_says_how_long_a_draft_has_left(env):
    admin, s, cat = app_client(env)
    sign_in(admin, "1001")
    admin.post("/api/admin/people", json={"text": "2002, Hải"}, headers=SAME)
    hai = second_browser(admin, "2002")
    full = hai.get("/api/my/summary").json()
    assert {"rev", "now", "ttl_hours", "person", "boxes", "stages", "free_objects", "open_mode"} <= set(full) and full["ttl_hours"] == s.draft_ttl_hours
    assert hai.get(f"/api/my/summary?rev={full['rev']}").json() == {"rev": full["rev"], "unchanged": True}
    box_id = hai.post("/api/my/boxes", json={"module": "M2", "label": "Box A1", "theme": "t"}, headers=SAME).json()["id"]
    box = wait_box(hai, box_id)
    after = hai.get(f"/api/my/summary?rev={full['rev']}").json()
    assert "unchanged" not in after and after["rev"] > full["rev"] and {b["id"] for b in after["boxes"]} == {box_id}
    left = box["updated_at"] + after["ttl_hours"] * 3600 - after["now"]
    assert 0 < left <= after["ttl_hours"] * 3600                                                   # the page can show the time that is left


def test_an_untouched_draft_is_deleted_after_the_time_limit_but_an_approved_box_is_not(env):
    """The page must warn about this: only the admin approves, so a user's draft can be deleted while it waits."""
    admin, s, cat = app_client(env)
    sign_in(admin, "1001")
    admin.post("/api/admin/people", json={"text": "2002, Hải"}, headers=SAME)
    hai = second_browser(admin, "2002")
    draft = hai.post("/api/my/boxes", json={"module": "M2", "label": "Box A1", "theme": "t"}, headers=SAME).json()["id"]
    kept = hai.post("/api/my/boxes", json={"module": "M2", "label": "Box B2", "theme": "t"}, headers=SAME).json()["id"]
    wait_box(hai, draft)
    wait_box(hai, kept)
    assert admin.post(f"/api/boxes/{kept}/approve", headers=SAME).status_code == 200
    from app import db
    conn = db.connect(s.db_path)
    old = time.time() - (s.draft_ttl_hours + 1) * 3600
    conn.execute("UPDATE boxes SET updated_at=?", (old,))
    left = {b["id"] for b in hai.get("/api/my/summary").json()["boxes"]}
    assert left == {kept}


def test_a_section_code_keeps_two_sections_of_a_module_apart_and_a_clash_is_explained(env):
    """The stored box name is unique per module, so the section code goes in front of it; the person still sees the kind ("Box B2")."""
    admin, s, cat = app_client(env)
    sign_in(admin, "1001")
    admin.post("/api/admin/people", json={"text": "2002, Hải\n3003, Hưng"}, headers=SAME)
    hai, hung = second_browser(admin, "2002"), second_browser(admin, "3003")
    first = hai.post("/api/my/boxes", json={"module": "M8", "label": "Box B2", "theme": "t", "code": "M8-02"}, headers=SAME)
    assert first.status_code == 201, first.text
    box = wait_box(hai, first.json()["id"])
    assert box["label"] == "Box B2" and box["code"] == "M8-02" and box["count"] == 21                # the stage is still found, so the size is the stage's
    raw = {b["id"]: b for b in admin.get("/api/state").json()["boxes"]}[box["id"]]
    assert raw["label"] == "M8-02 Box B2" and raw["title"] == "M8-02 - B2" and raw["owner"] == "2002"
    other = hung.post("/api/my/boxes", json={"module": "M8", "label": "Box B2", "theme": "t", "code": "M8-03"}, headers=SAME)
    plain = hung.post("/api/my/boxes", json={"module": "M8", "label": "Box B2", "theme": "t"}, headers=SAME)
    assert other.status_code == 201 and plain.status_code == 201                                       # another section, and no section at all
    again = hai.post("/api/my/boxes", json={"module": "M8", "label": "Box B2", "theme": "t", "code": "M8-02"}, headers=SAME)
    assert again.status_code == 409 and "Mã section riêng" in again.json()["detail"] and "M8 / M8-02 Box B2" in again.json()["detail"]
    assert hai.post("/api/my/boxes", json={"module": "M8", "label": "Box A1", "code": "M8-" + "0" * 20}, headers=SAME).status_code == 422
    paper = hai.get(f"/my/print?ids={box['id']}").text
    assert "M8-02 M8-02" not in paper and "Box B2" in paper                                           # the paper shows the clean kind


def test_a_person_is_told_only_what_they_can_act_on_when_picking_failed(env):
    from app import service
    from tests.test_backend import conn_of
    admin, s, cat = app_client(env)
    sign_in(admin, "1001")
    admin.post("/api/admin/people", json={"text": "2002, Hải"}, headers=SAME)
    hai = second_browser(admin, "2002")
    box_id = hai.post("/api/my/boxes", json={"module": "M2", "label": "Box A1", "theme": "t"}, headers=SAME).json()["id"]
    wait_box(hai, box_id)
    service.set_status(conn_of(s), box_id, "error", "mock lần 1: nhận 3 món, không đủ")
    mine = {b["id"]: b for b in hai.get("/api/my/summary").json()["boxes"]}[box_id]
    assert mine["status"] == "error" and mine["message"] == "Chưa chọn được đủ món."
    service.set_status(conn_of(s), box_id, "error", "Kho chỉ còn 3 món trống hợp nhóm đồ đã chọn, mà hộp cần 12. Chọn thêm nhóm đồ.")
    mine = {b["id"]: b for b in hai.get("/api/my/summary").json()["boxes"]}[box_id]
    assert mine["message"].startswith("Kho chỉ còn 3 món trống")                                           # a shortage is something a person can act on
    raw = {b["id"]: b for b in admin.get("/api/state").json()["boxes"]}[box_id]
    assert raw["message"].startswith("Kho chỉ còn")                                                       # the admin page keeps the picker's own note


def test_a_user_can_replace_one_object_and_delete_a_draft(env):
    admin, s, cat = app_client(env)
    sign_in(admin, "1001")
    admin.post("/api/admin/people", json={"text": "2002, Hải"}, headers=SAME)
    hai = second_browser(admin, "2002")
    box_id = hai.post("/api/my/boxes", json={"module": "M2", "label": "Box A1", "theme": "t"}, headers=SAME).json()["id"]
    box = wait_box(hai, box_id)
    first = box["items"][0]
    assert hai.post(f"/api/my/boxes/{box_id}/items/{first['pos']}/replace", headers=SAME).status_code == 200
    after = wait_box(hai, box_id)
    assert len(after["items"]) == box["count"]
    assert hai.delete(f"/api/my/boxes/{box_id}", headers=SAME).status_code == 200
    assert hai.get("/api/my/summary").json()["boxes"] == []


def test_a_user_cannot_use_admin_endpoints_by_any_door(env):
    admin, s, cat = app_client(env)
    sign_in(admin, "1001")
    admin.post("/api/admin/people", json={"text": "2002, Hải"}, headers=SAME)
    hai = second_browser(admin, "2002")
    for method, path in [("get", "/api/state"), ("post", "/api/boxes"), ("put", "/api/automations/reuse_old"), ("get", "/api/backup.json"),
                         ("get", "/api/export.csv"), ("post", "/api/used"), ("get", "/api/objects"), ("get", "/api/admin/people"),
                         ("post", "/api/admin/people"), ("get", "/api/sm/boxes/report"), ("post", "/api/batches")]:
        r = getattr(hai, method)(path, headers=SAME)
        assert r.status_code == 403, (method, path, r.status_code)
    r = hai.get("/print/boxes")                                                                       # the admin's paper: a user is sent to their own page
    assert r.status_code == 302 and r.headers["location"] == "/dung"


def test_the_owner_of_an_admin_box_is_the_admin_id_unless_a_name_was_typed(env):
    admin, s, cat = app_client(env)
    sign_in(admin, "1001")
    a = admin.post("/api/boxes", json={"module": "M7", "label": "Box A1", "theme": "t", "count": 3}, headers=SAME).json()["id"]
    b = admin.post("/api/boxes", json={"module": "M7", "label": "Box B2", "theme": "t", "count": 3, "owner": "Chủ"}, headers=SAME).json()["id"]
    owners = {x["id"]: x["owner"] for x in admin.get("/api/state").json()["boxes"]}
    assert owners[a] == "1001" and owners[b] == "Chủ"


def test_an_admin_code_set_in_the_environment_is_needed_for_every_admin_and_never_for_users(env):
    admin, s, cat = app_client(env, admin_code="s3cret")
    assert admin.post("/api/auth/login", json={"id": "1001"}, headers=SAME).status_code == 401
    sign_in(admin, "1001", "s3cret")
    admin.post("/api/admin/people", json={"text": "2002, Hải"}, headers=SAME)
    user = TestClient(admin.app, follow_redirects=False)
    sign_in(user, "2002")


def test_a_box_whose_pick_stopped_half_way_can_be_filled_without_losing_what_it_has(env):
    from tests.test_backend import conn_of
    admin, s, cat = _people(env)
    hai, hung = second_browser(admin, "2002"), second_browser(admin, "3003")
    box_id, box = _own_box(hai)
    keep = [i["id"] for i in box["items"][:5]]
    conn_of(s).execute("DELETE FROM box_items WHERE box_id=? AND pos > 5", (box_id,))
    conn_of(s).execute("UPDATE boxes SET status='error', message='x' WHERE id=?", (box_id,))
    assert hung.post(f"/api/my/boxes/{box_id}/fill", headers=SAME).status_code == 404                  # not theirs
    assert hai.post(f"/api/my/boxes/{box_id}/fill", headers=SAME).status_code == 200
    again = wait_box(hai, box_id)
    assert again["status"] == "draft" and len(again["items"]) == again["count"] and [i["id"] for i in again["items"][:5]] == keep
    hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME)
    assert hai.post(f"/api/my/boxes/{box_id}/fill", headers=SAME).status_code == 409                    # an approved box is not changed


def test_a_box_with_a_section_code_in_front_of_its_name_reads_right_for_the_team_script_and_the_sheet(env):
    """The team script and the sheet script read the stage and the title, not the stored name: a person's "M8-02 Box B2" must come out as B2 and M8-02 - B2."""
    admin, s, cat = _people(env)
    hai = second_browser(admin, "2002")
    r = hai.post("/api/my/boxes", json={"module": "M8", "label": "Box B2", "theme": "t", "code": "M8-02", "anchor": "Tủ lạnh"}, headers=SAME)
    box_id = r.json()["id"]
    wait_box(hai, box_id)
    assert hai.post(f"/api/my/boxes/{box_id}/approve", headers=SAME).status_code == 200
    anon = TestClient(admin.app)                                                                       # the scripts have no login
    for path in ("/api/taskboxes?status=approved", "/api/sheet-boxes"):
        boxes = anon.get(path).json()["boxes"]
        assert len(boxes) == 1, path
        b = boxes[0]
        assert b["title"] == "M8-02 - Tủ lạnh - B2" and b["stage"] == "B2" and b["module"] == "M8-02" and len(b["contents"]) == 21, (path, b["title"], b.get("stage"))
        assert b["builder"]["module"] == "M8" and b["builder"]["label"] == "M8-02 Box B2"
    paper = hai.get(f"/my/print?ids={box_id}").text
    assert "M8-02" in paper and "Tủ lạnh" in paper and "M8-02 M8-02" not in paper

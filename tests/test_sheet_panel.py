"""The Google Sheet box on the Automations tab (app/sheet_panel.py): the link is kept only when login is on, the script comes with this app's address."""
import time

import pytest
from fastapi.testclient import TestClient

from app import db, sheet_panel
from tests.test_auth_app import SAME, app_client, second_browser, sign_in
from tests.test_backend import env  # noqa: F401  (env is a fixture)

ID = "TestSheetIdOnlyForTests_0123456789-abcdef"          # made up: the shape of a sheet ID (long, letters, digits, dash, underscore). Never put a real one here.
LINK = f"https://docs.google.com/spreadsheets/d/{ID}/edit"


@pytest.mark.parametrize("raw", [LINK, f"  {LINK}  ", f"{LINK}?usp=drivesdk&ouid=123456", f"{LINK}#gid=0", f"https://docs.google.com/spreadsheets/d/{ID}",
                                 f"https://docs.google.com/spreadsheets/d/{ID}/edit?gid=12"])
def test_a_google_sheets_link_is_kept_in_one_plain_form_with_no_query_or_markers(raw):
    assert sheet_panel.normalize(raw) == LINK


@pytest.mark.parametrize("raw", ["", " ", "docs.google.com/spreadsheets/d/" + ID, "http://docs.google.com/spreadsheets/d/" + ID,
                                 f"https://docs.google.com.evil.example/spreadsheets/d/{ID}/edit", f"https://docs.google.com@evil.example/spreadsheets/d/{ID}/edit",
                                 f"https://evil.example/https://docs.google.com/spreadsheets/d/{ID}", "https://docs.google.com/document/d/" + ID,
                                 "https://docs.google.com/spreadsheets/d/short/edit", "javascript:alert(1)", f"https://docs.google.com/spreadsheets/d/{ID}/edit\n<script>"])
def test_anything_else_is_refused(raw):
    assert sheet_panel.normalize(raw) is None


def test_with_login_off_the_link_is_neither_stored_nor_shown(env):
    client, s, cat = app_client(env, admin_ids="")
    r = client.put("/api/sheet", json={"url": LINK}, headers=SAME)
    assert r.status_code == 409 and "đăng nhập" in r.json()["detail"]
    got = client.get("/api/sheet").json()
    assert got["enforced"] is False and got["url"] == "" and got["last_fetch"] is None
    conn = db.connect(s.db_path)
    assert conn.execute("SELECT COUNT(*) FROM catalog_snapshot WHERE k='sheet_url'").fetchone()[0] == 0


def test_an_admin_saves_changes_and_clears_the_link_and_the_log_never_holds_it(env):
    client, s, cat = app_client(env)
    sign_in(client, "1001")
    assert client.get("/api/sheet").json()["url"] == ""
    r = client.put("/api/sheet", json={"url": f"{LINK}?usp=drivesdk&ouid=999"}, headers=SAME)
    assert r.status_code == 200 and r.json() == {"url": LINK}
    assert client.get("/api/sheet").json()["url"] == LINK
    assert client.put("/api/sheet", json={"url": "https://example.com/x"}, headers=SAME).status_code == 422
    assert client.get("/api/sheet").json()["url"] == LINK                               # a refused change leaves the old link
    assert client.put("/api/sheet", json={"url": ""}, headers=SAME).json() == {"url": ""}
    assert client.get("/api/sheet").json()["url"] == ""
    conn = db.connect(s.db_path)
    log = " ".join(str(tuple(x)) for x in conn.execute("SELECT * FROM audit_log").fetchall())
    assert ID not in log and "sheet_link_save" in log and "sheet_link_clear" in log


def test_a_user_cannot_see_or_change_the_sheet_link(env):
    admin, s, cat = app_client(env)
    sign_in(admin, "1001")
    admin.post("/api/admin/people", json={"text": "2002, Hải"}, headers=SAME)
    admin.put("/api/sheet", json={"url": LINK}, headers=SAME)
    user = second_browser(admin, "2002")
    assert user.get("/api/sheet").status_code == 403
    assert user.put("/api/sheet", json={"url": ""}, headers=SAME).status_code == 403
    assert user.get("/api/sheet/script").status_code == 403
    visitor = TestClient(admin.app, follow_redirects=False)
    assert visitor.get("/api/sheet").status_code == 401


def test_the_page_can_say_when_the_sheet_script_last_asked_and_how_many_boxes_it_got(env):
    client, s, cat = app_client(env)
    sign_in(client, "1001")
    assert client.get("/api/sheet").json()["last_fetch"] is None
    before = time.time()
    assert client.get("/api/sheet-boxes").json() == {"boxes": []}                    # the script's call needs no login
    last = client.get("/api/sheet").json()["last_fetch"]
    assert last["boxes"] == 0 and before - 1 <= last["at"] <= time.time() + 1
    client.put("/api/automations/sheet_export", json={"enabled": False}, headers=SAME)
    assert client.get("/api/sheet-boxes").json()["disabled"] is True
    got = client.get("/api/sheet").json()
    assert got["export_on"] is False and got["last_fetch"]["boxes"] == 0


def test_the_script_comes_with_this_apps_address_and_nothing_else_changed(env):
    client, s, cat = app_client(env)
    sign_in(client, "1001")
    r = client.get("/api/sheet/script", headers={"Host": "box.example.org", "X-Forwarded-Proto": "https"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain") and r.headers["cache-control"] == "no-store"
    text = r.text
    assert "APP_URL: 'https://box.example.org'" in text and "onrender.com" not in text
    original = open(sheet_panel.SCRIPT_FILE, encoding="utf-8").read()
    assert text.replace("https://box.example.org", "https://box-builder-guwc.onrender.com", 1) == original                   # only the address differs
    assert client.get("/api/sheet/script", headers={"Host": "bad_host.example"}).status_code == 422                          # an address that cannot be one is refused

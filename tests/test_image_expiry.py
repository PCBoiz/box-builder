"""Signed SM picture links run out after about an hour: the app reads when from the link, tells the page, and stops asking for them."""
import time

import pytest

from app import images
from app.settings import Settings
from tests.test_automation import live_rows, make_client, sync
from tests.test_backend import _approved_box, env  # noqa: F401  (env is a fixture)

HOST = "https://sm.config.inc/media/x.png"


def stamp(t):
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(t))


def test_the_expiry_is_read_from_either_way_of_signing_and_nonsense_is_ignored():
    t = 1_794_324_000
    assert images.link_expiry(f"{HOST}?Expires={t}&Signature=a") == t
    assert images.link_expiry(f"{HOST}?X-Amz-Date={stamp(t - 3600)}&X-Amz-Expires=3600&X-Amz-Signature=a") == t
    assert images.link_expiry(f"{HOST}?EXPIRES={t}&signature=a") == t                                      # key case does not matter
    both = f"{HOST}?Expires={t}&X-Amz-Date={stamp(t - 3600)}&X-Amz-Expires=600"
    assert images.link_expiry(both) == t - 3600 + 600                                                       # the earlier one wins
    for bad in (f"{HOST}?Expires=abc", f"{HOST}?Expires=3600", f"{HOST}?Expires=99999999999", f"{HOST}?Expires=-5", f"{HOST}?Expires=",
                f"{HOST}?X-Amz-Date=2026-11-10&X-Amz-Expires=3600", f"{HOST}?X-Amz-Date={stamp(t)}&X-Amz-Expires=0",
                f"{HOST}?X-Amz-Date={stamp(t)}&X-Amz-Expires=9999999", f"{HOST}?X-Amz-Date={stamp(t)}", f"{HOST}?X-Amz-Expires=3600",
                HOST, "", None, 12345, "https://[bad"):
        assert images.link_expiry(bad) is None, bad


def test_digits_that_int_cannot_read_never_raise_and_mean_no_expiry():
    """Found by the review: Expires=%C2%B2 (a superscript two) passed isdigit() and then crashed int(), taking the whole object list down."""
    t = 1_794_324_000
    for bad in (f"{HOST}?Expires=%C2%B2&Signature=a", f"{HOST}?Expires=%E2%91%A0", f"{HOST}?Expires=%D9%A3%D9%A4",       # superscript, circled one, Arabic-Indic digits
                f"{HOST}?X-Amz-Date={stamp(t)}&X-Amz-Expires=%C2%B2", f"{HOST}?Expires=" + "9" * 5000,
                f"{HOST}?Expires={t}%00", f"{HOST}?Expires=+{t}", f"{HOST}?Expires={t}.5", f"{HOST}?Expires=1e9"):
        assert images.link_expiry(bad) is None, bad
        images.expiry_for(bad, 1.0, 3300)                                                                   # must not raise either
    from app.catalog import Catalog
    assert Catalog("data/catalog.csv").image_expiry(f"{HOST}?Expires=%C2%B2&Signature=a") is None


def test_one_odd_picture_link_does_not_break_the_object_list_or_the_print_page(env):
    client, s, cat = make_client(env)
    rows = live_rows(cat)
    rows[0]["image_url"] = "https://configint-main.s3.amazonaws.com/p/1.jpg?Expires=%C2%B2&Signature=a"
    rows[1]["image_url"] = "https://configint-main.s3.amazonaws.com/p/2.jpg?X-Amz-Date=20261110T000000Z&X-Amz-Expires=%E2%91%A0&X-Amz-Signature=a"
    sync(client, rows)
    for path in ("/api/objects?per=50", "/api/objects.csv", "/api/state", "/api/automations", "/api/export.csv"):
        assert client.get(path).status_code == 200, path
    assert obj(client, rows[0]["id"])["img"] and obj(client, rows[0]["id"])["img_exp"] is not None        # the picture is kept; signed but unreadable, so the catalog age decides
    bid = _approved_box(client, "Box B2")
    assert client.get(f"/print/boxes?status=approved&ids={bid}").status_code == 200


def test_which_links_count_as_signed():
    assert images.is_signed(f"{HOST}?Signature=a") and images.is_signed(f"{HOST}?AWSAccessKeyId=K&Expires=1") and images.is_signed(f"{HOST}?X-Amz-Signature=a")
    assert images.is_signed(f"{HOST}?X-Amz-Credential=a%2Fb")
    assert not images.is_signed(HOST) and not images.is_signed(f"{HOST}?size=big") and not images.is_signed(None) and not images.is_signed(5)


def test_the_moment_to_stop_asking_is_a_little_early_and_falls_back_to_the_read_time():
    t, synced, ttl = 1_794_324_000, 1_794_320_000, 3300
    assert images.expiry_for(f"{HOST}?Expires={t}&Signature=a", synced, ttl) == t - images.EXPIRY_MARGIN_S       # the link's own time wins over the fallback
    assert images.expiry_for(f"{HOST}?Signature=a", synced, ttl) == synced + ttl - images.EXPIRY_MARGIN_S         # signed but silent: read time plus the usual life
    assert images.expiry_for(f"{HOST}?Signature=a", None, ttl) is None and images.expiry_for(f"{HOST}?Signature=a", 0, ttl) is None   # nothing known: keep it
    assert images.expiry_for(HOST, synced, ttl) is None and images.expiry_for(f"{HOST}?size=big", synced, ttl) is None              # not signed: never expires
    assert images.expiry_for("", synced, ttl) is None


def test_only_a_real_number_in_the_past_counts_as_expired():
    now = 1_000.0
    assert images.is_expired(999.0, now) and images.is_expired(1000.0, now) and not images.is_expired(1000.1, now)
    for odd in (None, "", "999", True, False, [], {}, float("nan")):
        assert images.is_expired(odd, now) is False, odd


def test_the_ttl_setting_has_a_default_and_is_kept_in_range(monkeypatch):
    assert Settings().image_ttl_minutes == 55
    for given, want in (("30", 30), ("0", 1), ("-4", 1), ("99999", 1440), ("abc", 55)):
        monkeypatch.setenv("IMAGE_TTL_MINUTES", given)
        assert Settings.from_env().image_ttl_minutes == want, given
    monkeypatch.delenv("IMAGE_TTL_MINUTES")
    assert Settings.from_env().image_ttl_minutes == 55


def obj(client, oid):
    return client.get(f"/api/objects?q={oid}&per=5").json()["items"][0]


def test_objects_and_box_items_carry_img_exp_fresh_stale_and_missing(env):
    client, s, cat = make_client(env)
    now = int(time.time())
    rows = live_rows(cat)
    a, b, c, d, e = (r["id"] for r in rows[:5])
    rows[0]["image_url"] = f"https://sm.config.inc/media/a.png?Expires={now + 3000}&Signature=x"          # fresh
    rows[1]["image_url"] = f"https://sm.config.inc/media/b.png?Expires={now - 600}&Signature=x"           # ran out ten minutes ago
    rows[2]["image_url"] = "https://sm.config.inc/media/c.png?Signature=x"                                 # signed, says nothing: read time plus 55 minutes
    rows[3]["image_url"] = "https://sm.config.inc/media/d.png"                                             # not signed: never expires
    sync(client, rows)                                                                                     # e has no picture at all
    ea, eb, ec, ed, ee = (obj(client, o) for o in (a, b, c, d, e))
    assert ea["img_exp"] == now + 3000 - images.EXPIRY_MARGIN_S and not images.is_expired(ea["img_exp"])
    assert eb["img_exp"] == now - 600 - images.EXPIRY_MARGIN_S and images.is_expired(eb["img_exp"])
    synced = client.get("/api/state").json()["catalog"]["synced_at"]
    assert ec["img_exp"] == pytest.approx(synced + 55 * 60 - images.EXPIRY_MARGIN_S, abs=1) and not images.is_expired(ec["img_exp"])
    assert ed["img_exp"] is None and ee["img_exp"] is None and ee["img"] == ""
    assert all(o["img_exp"] is None for o in client.get("/api/objects?per=50").json()["items"] if not o["img"])
    # a box item carries it too
    bid = _approved_box(client, "Box B2")
    items = next(x for x in client.get("/api/state").json()["boxes"] if x["id"] == bid)["items"]
    assert all("img_exp" in i for i in items)


def test_a_signed_link_without_a_time_expires_with_the_catalog_age(env, monkeypatch):
    client, s, cat = make_client(env)
    rows = live_rows(cat)
    rows[0]["image_url"] = "https://sm.config.inc/media/c.png?Signature=x"
    sync(client, rows)
    cat.image_ttl_s = 600                                                                                  # ten minutes of trust
    first = obj(client, rows[0]["id"])["img_exp"]
    assert not images.is_expired(first)
    monkeypatch.setattr(time, "time", lambda: first + 1000)                                                # the catalog is now old
    assert images.is_expired(first)
    # a catalog from the CSV (never synced) has no read time: a signed link is kept, not guessed about
    from app.catalog import Catalog
    assert Catalog("data/catalog.csv").image_expiry("https://sm.config.inc/media/c.png?Signature=x") is None


def print_page(client, bid):
    r = client.get(f"/print/boxes?status=approved&ids={bid}")
    assert r.status_code == 200
    return r.text


def test_the_pick_list_leaves_out_expired_pictures_and_keeps_fresh_ones(env):
    client, s, cat = make_client(env)
    now = int(time.time())
    rows = live_rows(cat)
    for r in rows:
        r["image_url"] = f"https://sm.config.inc/media/{r['id']}.png?Expires={now - 900}&Signature=x"      # every picture has run out
    sync(client, rows)
    bid = _approved_box(client, "Box B2")
    assert "<img" not in print_page(client, bid)                                                           # nothing broken on the paper
    for r in rows:
        r["image_url"] = f"https://sm.config.inc/media/{r['id']}.png?Expires={now + 3000}&Signature=x"
    sync(client, rows)
    page = print_page(client, bid)
    assert page.count("<img") >= 10 and "Expires=" in page


def test_search_results_and_only_the_shown_page_carry_img_exp_and_the_server_sends_its_clock(env):
    client, s, cat = make_client(env)
    now = int(time.time())
    rows = live_rows(cat)
    for r in rows[:5]:
        r["image_url"] = f"https://sm.config.inc/media/{r['id']}.png?Expires={now - 600}&Signature=x"
    sync(client, rows)
    before = time.time()
    state = client.get("/api/state").json()
    assert before - 5 <= state["now"] <= time.time() + 5
    page = client.get("/api/objects?per=3").json()
    assert before - 5 <= page["now"] <= time.time() + 5 and all("img_exp" in o for o in page["items"]) and len(page["items"]) == 3
    first = obj(client, rows[0]["id"])                                                              # one that has a picture
    assert first["img"] and images.is_expired(first["img_exp"])
    found = client.get("/api/search?q=" + (first["vi"] or first["name"]).split()[0]).json()
    assert found and all("img_exp" in r for r in found)
    mine = next((r for r in found if r["id"] == first["id"]), None)
    assert mine is None or images.is_expired(mine["img_exp"])                                       # the 'add an object' list is told too
    # the CSV export is the full list and does not need the end time
    assert client.get("/api/objects.csv").status_code == 200

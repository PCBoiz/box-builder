"""GET /api/objects.csv: same filters as the page, every page, Excel-safe."""
import csv
import io

from tests.test_automation import live_rows, make_client, sync
from tests.test_backend import _approved_box, env  # noqa: F401  (env is a fixture)


def parse(resp):
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"] and "objects.csv" in resp.headers["content-disposition"]
    text = resp.content.decode("utf-8")
    assert text.startswith("﻿")                                  # BOM, so Excel shows the Vietnamese accents
    return list(csv.reader(io.StringIO(text[1:])))


def test_csv_matches_the_page_filters_and_covers_every_page(env):
    client, s, cat = make_client(env)
    rows = parse(client.get("/api/objects.csv"))
    assert rows[0] == ["id", "name", "vietnamese_name", "category", "stock", "status", "holder", "img"]
    page = client.get("/api/objects?per=50").json()
    assert len(rows) - 1 == page["total"] > 50                        # all pages, not just the first 50
    assert [r[0] for r in rows[1:6]] == [o["id"] for o in page["items"][:5]]

    bid = _approved_box(client)
    held = {i["id"] for i in next(b for b in client.get("/api/state").json()["boxes"] if b["id"] == bid)["items"]}
    got = parse(client.get("/api/objects.csv?status=approved"))
    assert {r[0] for r in got[1:]} == held and {r[5] for r in got[1:]} == {"approved"} and {r[6] for r in got[1:]} == {"M8 / Box A1"}

    top = client.get("/api/objects/coverage").json()[0]["name"]
    by_cat = parse(client.get("/api/objects.csv", params={"category": top, "sort": "-stock"}))
    assert {r[3] for r in by_cat[1:]} == {top}
    stocks = [int(r[4]) for r in by_cat[1:]]
    assert stocks == sorted(stocks, reverse=True)
    assert len(parse(client.get("/api/objects.csv?q=zzzznothing"))) == 1          # header only
    assert client.get("/api/objects.csv?status=bogus").status_code == 422


def test_csv_marks_formula_looking_text_and_keeps_ids_and_urls(env):
    client, s, cat = make_client(env)
    rows = live_rows(cat)
    rows[0].update(name="=HYPERLINK(\"http://evil\",\"x\")", vi="+cmd", image_url="https://sm.config.inc/media/a.png")
    rows[1].update(name="@SUM(1)", vi="-1+1")
    rows[2].update(name="Cốc, có dấu phẩy và \"dấu nháy\"")
    sync(client, rows)
    out = {r[0]: r for r in parse(client.get("/api/objects.csv"))[1:]}
    a, b, c = out[rows[0]["id"]], out[rows[1]["id"]], out[rows[2]["id"]]
    assert a[1].startswith("'=") and a[2] == "'+cmd"                    # shown as text, never run as a formula
    assert b[1] == "'@SUM(1)" and b[2] == "'-1+1"
    assert c[1] == "Cốc, có dấu phẩy và \"dấu nháy\""                   # commas and quotes survive the round trip
    assert a[0] == rows[0]["id"] and len(a[0]) == 6                      # IDs are untouched
    assert a[7] == "https://sm.config.inc/media/a.png"

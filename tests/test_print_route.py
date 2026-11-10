"""GET /print/boxes: the page a supporter takes to the shelves."""
import html.parser
import re

from fastapi.testclient import TestClient

from app import picker
from app.main import create_app
from app.printview import sort_key_for_slot
from tests.test_automation import make_client
from tests.test_backend import _approved_box, conn_of, env, wait_settled  # noqa: F401  (env is a fixture)


class Cells(html.parser.HTMLParser):
    """Rows of every table, as lists of cell text."""
    def __init__(self):
        super().__init__(); self.tables, self.row, self.cell, self.in_td = [], None, None, False
    def handle_starttag(self, tag, attrs):
        if tag == "table": self.tables.append([])
        elif tag == "tr": self.row = []
        elif tag in ("td", "th"): self.cell, self.in_td = [], True
    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None: self.row.append("".join(self.cell).strip()); self.cell, self.in_td = None, False
        elif tag == "tr" and self.row is not None and self.tables: self.tables[-1].append(self.row); self.row = None
    def handle_data(self, data):
        if self.in_td: self.cell.append(data)


def tables(text):
    p = Cells(); p.feed(text); return p.tables


def test_only_approved_boxes_print_with_a_combined_list_sorted_by_shelf(env):
    client, s, cat = make_client(env)
    a = _approved_box(client, "Box A1", module="M8")
    b = _approved_box(client, "Box B2", module="M9")
    d = client.post("/api/boxes", json={"module": "M7", "label": "Box Z9", "theme": "t", "count": 3}).json()["id"]
    wait_settled(client, d)
    r = client.get("/print/boxes")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html") and r.headers["cache-control"] == "no-store"
    page = r.text
    assert "M8" in page and "M9" in page and "Box Z9" not in page                          # the draft is not on the paper
    assert page.count("Danh sách gộp theo kệ") >= 1
    t = tables(page)
    combined = t[0]
    assert combined[0][0].startswith("Vị trí") and len(t) == 3                              # one combined table plus one per box
    slots = [row[0] for row in combined[1:]]
    assert slots == sorted(slots, key=sort_key_for_slot)                                    # a supporter walks the shelves once
    state = {x["id"]: x for x in client.get("/api/state").json()["boxes"]}
    assert len(combined) - 1 == len(state[a]["items"]) + len(state[b]["items"])
    for tbl, bid in ((t[1], a), (t[2], b)):
        ids_in_table = {row[1] for row in tbl[1:]}
        assert ids_in_table == {i["id"] for i in state[bid]["items"]}


def test_filters_and_the_empty_page(env):
    client, s, cat = make_client(env)
    a = _approved_box(client, "Box A1", module="M8")
    b = _approved_box(client, "Box B2", module="M9")
    only_m9 = client.get("/print/boxes?module=M9").text
    assert "Box B2" in only_m9 and "Box A1" not in only_m9 and "Danh sách gộp theo kệ" not in only_m9      # one box: no combined list
    only_a = client.get(f"/print/boxes?ids={a}").text
    assert "Box A1" in only_a and "Box B2" not in only_a
    d = client.post("/api/boxes", json={"module": "M7", "label": "Box Z9", "theme": "t", "count": 3}).json()["id"]
    wait_settled(client, d)
    assert "Box Z9" in client.get("/print/boxes?status=draft,approved").text
    assert "Danh sách gộp theo kệ" not in client.get("/print/boxes?combined=false").text
    nothing = client.get("/print/boxes?module=M404").text
    assert "Chưa có hộp đã duyệt để in." in nothing and "<table" not in nothing


def test_names_from_people_and_the_anchor_are_escaped_and_ids_are_looked_up(env):
    client, s, cat = make_client(env)
    first = sorted(cat.items())[0]
    evil = '<img src=x onerror=alert(1)>'
    bid = client.post("/api/boxes", json={"module": "M8", "label": "Box B2", "theme": "t", "count": 3, "anchor": first}).json()["id"]
    wait_settled(client, bid)
    assert client.post("/api/boxes", json={"module": "M8", "label": evil[:40], "theme": "t", "count": 3}).status_code == 422      # refused at the door now
    conn_of(s).execute("UPDATE boxes SET label=? WHERE id=?", (evil[:40], bid))                                                    # the paper still escapes what is already stored
    client.post(f"/api/boxes/{bid}/approve")
    page = client.get("/print/boxes").text
    assert "<img src=x" not in page and "&lt;img src=x" in page                              # the box name is text, never markup
    assert cat.items()[first].name in page                                                  # an anchor given as an ID prints as its name
    free = client.post("/api/boxes", json={"module": "M9", "label": "Box B1", "theme": "t", "count": 3, "anchor": "Tủ lạnh"}).json()["id"]
    wait_settled(client, free)
    conn_of(s).execute("UPDATE boxes SET anchor=? WHERE id=?", ("<b>Tủ lạnh</b>", free))
    client.post(f"/api/boxes/{free}/approve")
    page = client.get("/print/boxes").text
    assert "<b>Tủ lạnh</b>" not in page and "&lt;b&gt;Tủ lạnh&lt;/b&gt;" in page


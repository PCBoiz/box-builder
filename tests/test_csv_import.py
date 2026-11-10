"""Import boxes from a CSV: draft boxes only, nothing overwritten, taken objects skipped, bad rows listed with their line number."""
import csv
import io

import pytest

from app import service
from app.catalog import Catalog
from tests.test_automation import make_client
from tests.test_backend import _approved_box, env  # noqa: F401  (env is a fixture)
from tests.test_backup import boxes_of, other_app

URL = "/api/import/boxes.csv"


def free_ids(client, n):
    return [o["id"] for o in client.get(f"/api/objects?status=free&per={n}").json()["items"]]


def post(client, text, dry_run=True, raw=None):
    return client.post(f"{URL}?dry_run={'true' if dry_run else 'false'}", content=raw if raw is not None else text.encode("utf-8"),
                       headers={"content-type": "text/csv; charset=utf-8"})


def make_csv(rows, header=("module", "box", "stt", "object_id", "qty"), delim=","):
    out = io.StringIO()
    w = csv.writer(out, delimiter=delim)
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue()


def test_the_apps_own_export_comes_back_as_draft_boxes(env, tmp_path):
    client, s, _ = make_client(env)
    a1 = _approved_box(client, "Box A1")
    exported = client.get("/api/export.csv").text
    assert exported.startswith("﻿") and "object_id" in exported                     # the real file has a BOM and extra columns
    b, _ = other_app(tmp_path)
    rev = b.get("/api/state").json()["rev"]
    dry = post(b, exported)
    assert dry.status_code == 200 and dry.json()["dry_run"] is True and dry.json()["totals"]["boxes_added"] == 1
    assert b.get("/api/state").json()["rev"] == rev and b.get("/api/state").json()["boxes"] == []   # a dry run writes nothing
    real = post(b, exported, dry_run=False).json()
    assert real["totals"]["boxes_added"] == 1 and real["totals"]["items_skipped"] == 0 and real["csv"]["bad_total"] == 0
    got = boxes_of(b)[("M8", "Box A1")]
    want = boxes_of(client)[("M8", "Box A1")]
    assert got[0] == "draft" and want[0] == "approved"                                    # always a draft, even if the file said approved
    assert got[1] == want[1] == 21                                                        # the box keeps the 21 items it had (more than A1's 12)
    assert [(p, i, q) for p, i, q, _l, _s in got[4]] == [(p, i, q) for p, i, q, _l, _s in want[4]]
    assert {src for *_x, src in got[4]} == {"import"}
    box = b.get("/api/state").json()["boxes"][0]
    assert "CSV" in box["message"]
    assert any(e["kind"] == "import" for e in b.get("/api/automations").json()["log"])


def test_a_hand_made_file_bom_quotes_tabs_semicolons_and_other_header_names(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 12)
    rows = [("M5", 'Box "A1"', "", i, "") for i in ids[:3]]
    for delim in (",", ";", "\t"):
        text = "﻿" + make_csv(rows, header=("Module", "Hộp", "STT", "ID", "Số lượng"), delim=delim)
        r = post(client, text)
        assert r.status_code == 200, delim
        body = r.json()
        assert body["totals"]["boxes_added"] == 1 and body["totals"]["items_added"] == 3 and body["csv"]["bad_total"] == 0, delim
        assert body["boxes"][0]["label"] == 'Box "A1"'
    real = post(client, make_csv(rows, header=("module", "box", "stt", "object_id", "qty")), dry_run=False).json()
    box = boxes_of(client)[("M5", 'Box "A1"')]
    assert [p for p, *_x in box[4]] == [1, 2, 3] and real["boxes"][0]["items_added"] == 3        # no stt column: numbered in file order
    assert box[1] == 21                                                                        # 'Box "A1"' is not a stage name, so the default size
    assert "3/21" in real["boxes"][0]["reason"]                                                # three items make a short draft, and it says so
    a1 = post(client, make_csv([("M5", "Box A1", "", i, "") for i in ids[3:6]]), dry_run=False).json()
    assert boxes_of(client)[("M5", "Box A1")][1] == 12 and "3/12" in a1["boxes"][0]["reason"]   # a real A1 is 12 big


def test_code_anchor_and_quantity_are_read_and_quantity_is_capped(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 6)
    text = make_csv([("M6", "Box B2", 1, ids[0], 99, "M6-02", "bàn bếp"), ("M6", "Box B2", 2, ids[1], 2, "", "")],
                    header=("module", "box", "stt", "object_id", "qty", "code", "anchor"))
    post(client, text, dry_run=False)
    st = client.get("/api/state").json()["boxes"][0]
    assert st["code"] == "M6-02" and st["anchor"] == "bàn bếp" and st["count"] == 21
    qty = {i["pos"]: i["qty"] for i in st["items"]}
    assert 1 <= qty[1] <= s.max_qty and qty[2] <= 2


def test_bad_rows_are_listed_with_their_line_and_the_good_rows_still_come_in(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 8)
    rows = [("M5", "Box A1", 1, ids[0], 1),
            ("", "Box A1", 2, ids[1], 1),                 # line 3: no module
            ("M5", "Box A1", 3, "abc", 1),                # line 4: not an ID
            ("M5", "Box A1", 4, "1234567", 1),            # line 5: 7 digits
            ("M5", "Box A1", "x", ids[2], 1),             # line 6: bad stt
            ("M5", "Box A1", 99, ids[2], 1),              # line 7: stt too big
            ("M5", "Box A1", 5, ids[2], "two"),           # line 8: bad qty
            ("M5", "Box A1", 1, ids[3], 1),               # line 9: stt 1 again
            ("M5", "Box A1", 6, ids[0], 1),               # line 10: same ID twice in one box
            ("M5", "Box A1", 7, ids[4], 1)]
    body = post(client, make_csv(rows)).json()
    assert body["csv"]["rows"] == 10 and body["csv"]["bad_total"] == 8
    by_line = {x["line"]: x["reason"] for x in body["csv"]["bad_rows"]}
    assert sorted(by_line) == [3, 4, 5, 6, 7, 8, 9, 10]
    assert "Module" in by_line[3] and "ID" in by_line[4] and "ID" in by_line[5] and "STT" in by_line[6] and "STT" in by_line[7]
    assert "số lượng" in by_line[8] and "STT 1" in by_line[9] and "lặp" in by_line[10]
    assert body["totals"]["items_added"] == 2 and body["boxes"][0]["items_total"] == 2          # ids[0] and ids[4]


def test_objects_taken_elsewhere_or_unknown_are_skipped_and_named(env):
    client, s, _ = make_client(env)
    a1 = _approved_box(client, "Box A1", module="M8")
    taken = client.get("/api/state").json()["boxes"][0]["items"][0]["id"]
    ids = [i for i in free_ids(client, 10) if i != taken]
    text = make_csv([("M9", "Box A1", 1, taken, 1), ("M9", "Box A1", 2, "999999", 1), ("M9", "Box A1", 3, ids[0], 1),
                     ("M9", "Box B2", 1, ids[0], 1), ("M9", "Box B2", 2, ids[1], 1)])
    body = post(client, text, dry_run=False).json()
    a, b = body["boxes"]
    reasons = {i["object_id"]: i["reason"] for i in a["items_skipped"]}
    assert "M8" in reasons[taken] and "kho" in reasons["999999"] and a["items_added"] == 1
    assert [i["reason"] for i in b["items_skipped"]] == [f"đã nằm trong M9 / Box A1"] and b["items_added"] == 1   # a later box cannot take it again
    held = [i["id"] for bx in client.get("/api/state").json()["boxes"] for i in bx["items"]]
    assert len(held) == len(set(held))                                                          # no object ended up in two boxes


def test_a_box_with_the_same_name_is_left_alone_and_a_second_import_adds_nothing(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 4)
    text = make_csv([("M5", "Box A1", 1, ids[0], 1), ("M5", "Box A1", 2, ids[1], 1)])
    assert post(client, text, dry_run=False).json()["totals"]["boxes_added"] == 1
    again = post(client, text, dry_run=False).json()
    assert again["totals"]["boxes_added"] == 0 and again["boxes"][0]["reason"] == "đã có hộp cùng tên"
    assert len(client.get("/api/state").json()["boxes"]) == 1


def test_files_that_cannot_be_used_are_refused_kindly(env):
    client, s, _ = make_client(env)
    r = post(client, "module,box\nM5,Box A1\n")
    assert r.status_code == 422 and "object_id" in r.json()["detail"]
    assert post(client, "").status_code == 422 and post(client, "\n \n").status_code == 422
    r = post(client, "", raw=b"module,box,object_id\nM5,Box \xff\xfe,000001\n")
    assert r.status_code == 422 and "UTF-8" in r.json()["detail"]
    assert post(client, "", raw=b"module,box,object_id\n" + b"x" * (5 * 1024 * 1024 + 1)).status_code == 413
    many = make_csv([("M5", "Box A1", "", "000001", 1)] * (service.CSV_MAX_ROWS + 1))
    assert post(client, many).status_code == 422
    boxes = make_csv([("M5", f"Box {n}", 1, "000001", 1) for n in range(service.CSV_MAX_BOXES + 1)])
    assert post(client, boxes).status_code == 422
    assert client.get("/api/state").json()["boxes"] == []                                        # nothing was written by any of these


def test_a_cross_site_post_is_refused_like_every_other_write(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 2)
    r = client.post(f"{URL}?dry_run=false", content=make_csv([("M5", "Box A1", 1, ids[0], 1)]).encode(),
                    headers={"content-type": "text/csv", "sec-fetch-site": "cross-site", "origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.get("/api/state").json()["boxes"] == []


def test_boxes_from_csv_alone_reads_sizes_from_the_stage_and_never_trusts_the_status_column():
    text = make_csv([("M5", "Box A1", 1, "000001", 1, "approved"), ("M5", "Box B3", 1, "000002", 1, "approved")],
                    header=("module", "box", "stt", "object_id", "qty", "status"))
    data, bad, rows = service.boxes_from_csv(text, lambda label: 12 if label.endswith("A1") else 21)
    assert rows == 2 and bad == []
    assert [(b["label"], b["count"], b["status"]) for b in data["boxes"]] == [("Box A1", 12, "draft"), ("Box B3", 21, "draft")]
    assert data["format"] == service.BACKUP_FORMAT and data["version"] == service.BACKUP_VERSION and data["used"] == []


# ----------------------------------------------------------------------------- problems found by the independent review
def test_the_exact_column_name_wins_over_a_looser_spelling(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 4)
    text = make_csv([(555555, "M5", "Box A1", 1, ids[0], 1), (555556, "M5", "Box A1", 2, ids[1], 1)], header=("id", "module", "box", "stt", "object_id", "qty"))
    client.post(f"{URL}?dry_run=false", content=text.encode(), headers={"content-type": "text/csv"})
    assert [i["id"] for i in client.get("/api/state").json()["boxes"][0]["items"]] == ids[:2]            # the row number column "id" was ignored


def test_odd_digits_and_decimals_are_listed_as_bad_rows_never_a_500(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 8)
    rows = [("M5", "Box A1", "²", ids[0], 1), ("M5", "Box A1", 1, ids[1], "①"), ("M5", "Box A1", "9" * 5000, ids[2], 1),
            ("M5", "Box A1", 2, ids[3], "9" * 5000), ("M5", "Box A1", 3, "4702.0", 1), ("M5", "Box A1", 4, "12 34", 1),
            ("M5", "Box A1", 5, "٣", 1), ("M5", "Box A1", 6, ids[4], 0), ("M5", "Box A1", 7, ids[5], 1)]
    r = post(client, make_csv(rows))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["csv"]["rows"] == 9 and body["csv"]["bad_total"] == 8 and body["totals"]["items_added"] == 1
    assert "số lượng" in {x["line"]: x["reason"] for x in body["csv"]["bad_rows"]}[9]                         # quantity 0 is refused, not turned into 1


def test_a_huge_cell_and_odd_line_endings_get_a_kind_answer_or_are_read(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 4)
    r = post(client, "module,box,object_id\nM5," + "x" * 200000 + f",{ids[0]}\n")
    assert r.status_code == 422 and "Hãy" in r.json()["detail"]
    old_mac = f"module,box,stt,object_id\rM5,Box A1,1,{ids[0]}\rM5,Box A1,2,{ids[1]}\r"
    ok = post(client, old_mac).json()
    assert ok["totals"]["items_added"] == 2 and ok["csv"]["bad_total"] == 0
    crlf = post(client, old_mac.replace("\r", "\r\n")).json()
    assert crlf["totals"]["items_added"] == 2


def test_more_than_sixty_rows_in_a_box_are_listed_and_the_totals_add_up(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 70)
    body = post(client, make_csv([("M5", "Box B2", "", i, 1) for i in ids], header=("module", "box", "stt", "object_id", "qty"))).json()
    assert body["boxes"][0]["items_total"] == 60 and body["boxes"][0]["items_added"] == 60            # the box is full at 60
    assert body["csv"]["rows"] == 70 and body["csv"]["bad_total"] == 10 and "60" in body["csv"]["bad_rows"][0]["reason"]
    assert body["boxes"][0]["items_total"] + body["csv"]["bad_total"] == body["csv"]["rows"]


def test_a_box_whose_every_row_is_bad_is_not_created_so_the_fixed_file_can_come_in(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 4)
    bad = make_csv([("M5", "Box A1", 1, "abc", 1), ("M5", "Box A1", 2, "", 1), ("M6", "Box A1", 1, ids[0], 1)])
    body = post(client, bad, dry_run=False).json()
    assert [(b["module"], b["label"]) for b in body["boxes"]] == [("M6", "Box A1")]                   # M5 / Box A1 has nothing usable
    assert body["csv"]["bad_total"] == 2
    fixed = post(client, make_csv([("M5", "Box A1", 1, ids[1], 1)]), dry_run=False).json()
    assert fixed["totals"]["boxes_added"] == 1                                                         # no empty box was left in the way


def test_line_numbers_point_at_where_the_record_starts_even_with_multi_line_cells(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 4)
    text = (f'module,box,stt,object_id,qty,anchor\nM5,Box A1,1,{ids[0]},1,"bàn\nbếp\nlớn"\n'          # record on lines 2-4
            'M5,Box A1,2,abc,1,x\n'                                                                       # line 5
            'M5,Box A1,3,"zz\nzz",1,y\n')                                                                 # line 6 (spans 6-7)
    body = post(client, text).json()
    assert [(x["line"]) for x in body["csv"]["bad_rows"]] == [5, 6]


def test_quantities_that_are_cut_down_are_said_and_modules_are_tidied_and_nul_is_dropped(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 4)
    text = make_csv([("m7", "Box\x00 A1", 1, ids[0], 99), ("m7", "Box\x00 A1", 2, ids[1], 1)])
    body = post(client, text, dry_run=False)
    assert body.status_code == 200
    got = body.json()["boxes"][0]
    assert (got["module"], got["label"]) == ("M7", "Box A1") and "đổi số lượng" in got["reason"]        # m7 -> M7, NUL gone, the cut is said
    assert ("M7", "Box A1") in boxes_of(client)


def test_a_box_skipped_for_its_name_still_says_how_many_items_it_had(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 4)
    text = make_csv([("M5", "Box A1", 1, ids[0], 1), ("M5", "Box A1", 2, ids[1], 1)])
    post(client, text, dry_run=False)
    again = post(client, text).json()["boxes"][0]
    assert again["result"] == "skipped" and again["items_total"] == 2 and again["items_added"] == 0


def test_a_file_with_twenty_thousand_rows_in_one_box_is_quick(env):
    import time
    client, s, _ = make_client(env)
    text = make_csv([("M5", "Box A1", "", f"{n % 999999 + 1:06d}", 1) for n in range(service.CSV_MAX_ROWS)], header=("module", "box", "stt", "object_id", "qty"))
    t = time.time()
    r = post(client, text)
    assert r.status_code == 200 and time.time() - t < 5, time.time() - t
    assert r.json()["csv"]["bad_total"] >= service.CSV_MAX_ROWS - 60 - 1                              # all but the first 60 are listed as refused (or repeated)


def test_the_boxes_export_marks_formula_looking_text_as_text(env):
    client, s, _ = make_client(env)
    ids = free_ids(client, 2)
    post(client, make_csv([("M5", "=SUM(1+1)", 1, ids[0], 1)]), dry_run=False)
    exported = client.get("/api/export.csv").text
    assert "'=SUM(1+1)" in exported and ",=SUM(1+1)," not in exported

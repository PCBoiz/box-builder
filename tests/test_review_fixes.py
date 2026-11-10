"""Fixes that came out of the independent review of 2026-11-11."""
import csv
import io
import time

import pytest

from app import service, usage
from tests.test_automation import make_client
from tests.test_backend import conn_of, env  # noqa: F401  (env is a fixture)
from tests.test_reuse_old import box_with
from tests.test_sm_boxes import usable_ids
from tests.test_usage_carry import rows_of, sync


@pytest.mark.parametrize("value", [10 ** 400, -10 ** 400, 10 ** 310, 10 ** 20, -1, 0, 4_102_444_801 * 1000 + 1])
def test_a_huge_whole_number_for_the_creation_date_is_unknown_and_never_raises(value):
    assert usage.parse_created(value) is None


def test_one_absurd_creation_date_does_not_make_the_whole_catalog_sync_answer_500(env):
    client, s, cat = make_client(env)
    rows = rows_of(cat)
    rows[0]["created"] = 10 ** 400
    rows[0]["uses"] = 700
    r = client.post("/api/catalog/sync", json={"objects": rows}, headers={"X-Sync-Token": "tok"})
    assert r.status_code == 200, r.text
    item = client.get(f"/api/objects?q={rows[0]['id']}&per=50").json()["items"][0]
    assert item["uses"] == 700 and item["created"] is None                                  # the count is kept, the date is simply unknown


def test_the_csv_export_follows_the_band_filter_like_the_list(env):
    client, s, cat = make_client(env)
    rows = rows_of(cat)
    for n, r in enumerate(rows):
        r["uses"], r["created"] = (1200 if n < 3 else 600 if n < 9 else 5), "2025-01-01T00:00:00Z"
    sync(client, rows)
    for band, expected in (("1000%2B", 3), ("500-999", 6), ("unknown", 0)):
        listed = client.get(f"/api/objects?band={band}&per=50").json()
        got = list(csv.reader(io.StringIO(client.get(f"/api/objects.csv?band={band}").text.lstrip("﻿"))))
        assert len(got) - 1 == listed["total"] == expected, band
    assert client.get("/api/objects.csv?band=nonsense").status_code == 422


class _Counting:
    """Wraps a connection and counts the statements sent through it."""
    def __init__(self, conn):
        self.conn, self.n = conn, 0

    def execute(self, *a, **k):
        self.n += 1
        return self.conn.execute(*a, **k)

    def __getattr__(self, name):
        return getattr(self.conn, name)


def test_the_page_state_reads_the_items_of_all_boxes_in_one_query(env):
    """The admin page and the user page ask for the whole state all the time: the number of queries must not grow with the number of boxes."""
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    box_with(s, ids[:3], label="Box A1", module="M1")
    few = _Counting(conn_of(s))
    service.state(few, cat)
    for n in range(40):
        box_with(s, ids[10 + n * 3: 13 + n * 3], label=f"Box B{n}", module="M9")
    many = _Counting(conn_of(s))
    st = service.state(many, cat)
    assert len(st["boxes"]) == 41 and all(len(b["items"]) == 3 for b in st["boxes"])
    assert many.n == few.n, (few.n, many.n)


def test_the_state_keeps_the_items_in_position_order_for_every_box(env):
    client, s, cat = make_client(env)
    ids = usable_ids(cat)
    a = box_with(s, ids[:4], label="Box A1", module="M1")
    b = box_with(s, ids[4:9], label="Box B2", module="M1")
    conn_of(s).execute("UPDATE box_items SET pos = pos + 100 WHERE box_id=? AND pos IN (1, 2)", (a,))      # positions 101, 102, 3, 4 sort as 3, 4, 101, 102
    st = {x["id"]: x for x in service.state(conn_of(s), cat)["boxes"]}
    assert [i["pos"] for i in st[a]["items"]] == [3, 4, 101, 102] and [i["pos"] for i in st[b]["items"]] == [1, 2, 3, 4, 5]
    assert [i["id"] for i in st[b]["items"]] == ids[4:9]

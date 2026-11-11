"""Domain operations on boxes and the claim ledger. Every write runs inside one BEGIN IMMEDIATE
transaction, so "check it is free, then take it" can never interleave with another user."""
import datetime
import itertools
import json
import math
import re
import time

from . import images, usage
from .catalog import Catalog, Item, display_name, pad_id
from .db import IntegrityError, rev, tx


class Conflict(Exception):
    """The request clashes with existing data (name taken, object taken, ...). Maps to HTTP 409."""


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


# ----------------------------------------------------------------------------- reads
# SM statuses in which a box no longer holds its objects. Anything else, including a status we have never seen, counts as
# holding them: wrongly treating an object as taken costs a pick, wrongly treating it as free costs a duplicate.
SM_ENDED = ("deactivated", "returned", "cancelled", "canceled", "discarded")


_AUTO = object()                        # "work the cutoff out from the switch"


def when_of(text) -> float | None:
    """Epoch seconds from a date as SM writes it ('2026-11-10T08:00:00Z', '2026-11-10 08:00:00', '2026-11-10'), or None when it cannot be read.
    A date without a zone is taken as UTC."""
    t = str(text if text is not None else "").strip()
    if not t:
        return None
    try:
        d = datetime.datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    if d.year < 2000:                       # a placeholder such as 0001-01-01 or 1970-01-01 is not a real box date: unreadable, so the box keeps holding
        return None
    return d.timestamp()


def reuse_cutoff(conn, now: float | None = None, switches: dict | None = None) -> float | None:
    """Boxes made before this moment no longer keep their objects (the "Dùng lại món của hộp cũ" switch: N days back from now).
    None when the switch is off, so nothing is ever reused. `switches` is the result of automation.switches(conn) when the caller
    already has it, so a request reads the switches once."""
    from . import automation
    sw = (switches if switches is not None else automation.switches(conn))["reuse_old"]
    if not sw["enabled"]:
        return None
    return (time.time() if now is None else now) - sw["config"]["days"] * 86400


def external_binding(conn, cutoff=_AUTO) -> set[str]:
    """IDs the sheet list still keeps back: every one, unless the reuse switch is on and the boxes that used the ID were made before
    the cutoff. An ID with no date (added_at 0) is never reused: wrongly treating it as free would cost a duplicate."""
    if cutoff is _AUTO:
        cutoff = reuse_cutoff(conn)
    if cutoff is None:
        return {r[0] for r in conn.execute("SELECT object_id FROM external_used")}
    return {r[0] for r in conn.execute("SELECT object_id FROM external_used WHERE added_at <= 0 OR added_at >= ?", (cutoff,))}


def taken_ids(conn, cutoff=_AUTO) -> set[str]:
    if cutoff is _AUTO:
        cutoff = reuse_cutoff(conn)
    a = {r[0] for r in conn.execute("SELECT object_id FROM box_items")}
    return a | external_binding(conn, cutoff) | set(sm_held(conn, cutoff))


def sm_held(conn, cutoff=_AUTO) -> dict[str, dict]:
    """Objects that a box in SM holds, as last read from SM: object id -> {sm_box_id, title, status}.
    Boxes this app pushed itself are left out: their objects already belong to our own box. With the reuse switch on, a box made
    before the cutoff holds nothing any more (a date that cannot be read counts as recent: it keeps its objects)."""
    if cutoff is _AUTO:
        cutoff = reuse_cutoff(conn)
    ours = {str(r[0]) for r in conn.execute("SELECT sm_box_id FROM sm_push")}
    out: dict[str, dict] = {}
    dates: dict[str, float | None] = {}                   # each distinct date text is parsed once, not once per object row
    for r in conn.execute(
        "SELECT i.object_id, b.sm_box_id, b.title, b.status, b.status_label, b.created_at FROM sm_box_items i "
        "JOIN sm_boxes b ON b.sm_box_id = i.sm_box_id ORDER BY b.created_at, b.sm_box_id"
    ):
        if r["sm_box_id"] in ours:
            continue
        if cutoff is not None:
            if r["created_at"] not in dates:
                dates[r["created_at"]] = when_of(r["created_at"])
            when = dates[r["created_at"]]
            if when is not None and when < cutoff:
                continue
        out.setdefault(r["object_id"], {"sm_box_id": r["sm_box_id"], "title": r["title"], "status": r["status_label"] or r["status"]})
    return out


def sm_boxes_info(conn) -> dict | None:
    r = conn.execute("SELECT v FROM catalog_snapshot WHERE k='sm_boxes'").fetchone()
    return json.loads(r[0]) if r else None


def save_sm_boxes(conn, boxes: list, now: float, allow_empty: bool = False) -> dict:
    """Replace the snapshot of SM boxes. Only active boxes are kept, and only the fields below: never an assignee, holder or any
    person's name, even if the sender includes them. Boxes with no usable id are skipped and counted."""
    if not boxes and not allow_empty:
        raise Invalid("Danh sách hộp SM trống nên bị bỏ qua (để tránh xoá nhầm). Nếu thật sự SM không có hộp nào, gửi allow_empty.")
    keep, ignored_ended, invalid = {}, 0, 0
    for b in boxes:
        if not isinstance(b, dict):
            invalid += 1
            continue
        bid = str(b.get("box_id") if b.get("box_id") is not None else "").strip()[:64]
        status = str(b.get("status") or "").strip().lower()[:40]
        if not bid:
            invalid += 1
            continue
        if status in SM_ENDED:
            ignored_ended += 1
            continue
        contents = b.get("contents") if isinstance(b.get("contents"), list) else []
        if len(contents) > 200:
            raise Invalid(f"Hộp SM {bid} có {len(contents)} món, nhiều hơn mức cho phép (200).")
        items: dict[str, int] = {}
        for c in contents:
            if not isinstance(c, dict):
                continue
            oid = pad_id(c.get("object_id", ""))
            if oid is None:
                continue
            try:
                q = max(1, min(9999, int(c.get("quantity") or 1)))
            except (TypeError, ValueError):
                q = 1
            items[oid] = max(items.get(oid, 0), q)
        keep[bid] = ({"title": str(b.get("title") or "").strip()[:200], "status": status,
                      "label": str(b.get("status_label") or "").strip()[:60], "created": str(b.get("created_at") or "").strip()[:40]}, items)
    held = {oid for _, items in keep.values() for oid in items}
    with tx(conn):
        conn.execute("DELETE FROM sm_box_items")
        conn.execute("DELETE FROM sm_boxes")
        conn.executemany("INSERT INTO sm_boxes(sm_box_id, title, status, status_label, created_at, seen_at) VALUES(?,?,?,?,?,?)",
                         [(bid, meta["title"], meta["status"], meta["label"], meta["created"], now) for bid, (meta, _) in keep.items()])
        conn.executemany("INSERT INTO sm_box_items(sm_box_id, object_id, qty) VALUES(?,?,?)",
                         [(bid, oid, q) for bid, (_, items) in keep.items() for oid, q in items.items()])
        info = {"synced_at": now, "received": len(boxes), "active": len(keep), "objects_held": len(held),
                "ignored_ended": ignored_ended, "skipped_invalid": invalid}
        conn.execute("INSERT INTO catalog_snapshot(k, v) VALUES('sm_boxes', ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v",
                     (json.dumps(info),))
    return info


def get_box(conn, box_id: int) -> object:
    row = conn.execute("SELECT * FROM boxes WHERE id=?", (box_id,)).fetchone()
    if row is None:
        raise NotFound("Không thấy hộp này. Có thể nó đã tự mất vì quá lâu chưa duyệt.")
    return row


def box_items(conn, box_id: int) -> list[object]:
    return conn.execute("SELECT * FROM box_items WHERE box_id=? ORDER BY pos", (box_id,)).fetchall()


def owner_of(conn, oid: str) -> str:
    r = conn.execute(
        "SELECT b.module, b.label FROM box_items i JOIN boxes b ON b.id=i.box_id WHERE i.object_id=?", (oid,)
    ).fetchone()
    if r:
        return f"{r['module']} / {r['label']}"
    if oid in external_binding(conn):
        return "danh sách đã dùng trong bảng tính"
    held = sm_held(conn).get(oid)
    if held:
        return f"hộp SM {held['title'] or held['sm_box_id']}"
    return ""


def state(conn, catalog: Catalog) -> dict:
    # Read the revision BEFORE the data. If a write lands while we read, the page holds data that is at least as new as
    # its revision, so the next poll sees a different number and refetches. (Reading it last could label old data with a
    # newer revision and leave the page stale until something else changed.)
    from . import automation
    rev_first = rev(conn)
    sw = automation.switches(conn)                     # read once: reuse_cutoff below gets it too
    gate_on = sw["need_supporter_check"]["enabled"]
    records = sheet_records(conn) if gate_on else {}
    cat = catalog.items()
    boxes = []
    pushed = {r["box_id"]: {"sm_box_id": r["sm_box_id"], "at": r["pushed_at"]}
              for r in conn.execute("SELECT * FROM sm_push")}
    items_of: dict[int, list] = {}                      # every box's items in one query (not one query per box: the page asks often)
    for it in conn.execute("SELECT * FROM box_items ORDER BY box_id, pos").fetchall():
        items_of.setdefault(it["box_id"], []).append(it)
    for b in conn.execute("SELECT * FROM boxes ORDER BY module, label, id").fetchall():
        items = []
        for it in items_of.get(b["id"], []):
            c = cat.get(it["object_id"])
            items.append({
                "pos": it["pos"], "id": it["object_id"], "qty": it["qty"],
                "locked": bool(it["locked"]), "source": it["source"],
                "name": c.name if c else "(không còn trong kho)",
                "vi": c.vi if c else "", "category": c.category if c else "",
                "slot": c.slot if c else "", "stock": c.stock if c else 0,
                "img": c.img if c else "", "img_exp": catalog.image_expiry(c.img) if c and c.img else None, "detail": images.detail_url(it["object_id"]),
            })
        boxes.append({
            "id": b["id"], "module": b["module"], "label": b["label"], "theme": b["theme"],
            "code": b["code"], "anchor": b["anchor"], "title": taskbox_title(b["code"] or b["module"], b["anchor"], b["label"]),
            "categories": json.loads(b["categories"]), "count": b["count"], "status": b["status"],
            "message": b["message"], "owner": b["owner"], "updated_at": b["updated_at"], "items": items,
            "pushed": pushed.get(b["id"]), "day": b["day"],
            "sheet": sheet_view(b["status"], bool(pushed.get(b["id"])), gate_on, b["count"], [{"id": i["id"], "qty": i["qty"]} for i in items], records.get(b["id"])),
        })
    in_boxes = conn.execute("SELECT COUNT(*) FROM box_items").fetchone()[0]
    cutoff = reuse_cutoff(conn, switches=sw)
    claimed = {r[0] for r in conn.execute("SELECT object_id FROM box_items")}
    catset = set(cat)
    ext = external_binding(conn, cutoff)
    sm_ids = set(sm_held(conn, cutoff))
    # an object in a box counts as that box's, as in the object list; the sheet list and SM only count what no box has
    external = len((ext - claimed) & catset)
    used = [{"id": r["object_id"], "note": r["note"], "added_at": r["added_at"] if r["added_at"] > 0 else 0,
             "since": time.strftime("%Y-%m-%d", time.gmtime(r["added_at"])) if r["added_at"] > 0 else "",
             "reusable": cutoff is not None and 0 < r["added_at"] < cutoff and r["object_id"] in catset
                         and r["object_id"] not in claimed and r["object_id"] not in sm_ids}
            for r in conn.execute("SELECT * FROM external_used ORDER BY object_id")]
    return {
        "rev": rev_first,
        "now": time.time(),
        "catalog": catalog.info(),
        "stats": {"catalog": len(cat), "in_boxes": in_boxes, "external": external, "in_sm": len((sm_ids - claimed) & catset),
                  "free": max(0, len(cat) - len(taken_ids(conn, cutoff) & catset)), "boxes": len(boxes),
                  "gone": len(claimed - catset)},     # claimed objects SM no longer lists as available
        "boxes": boxes,
        "used": used,
    }


# ----------------------------------------------------------------------------- export for the SM TaskBox userscript
def stage_of(label: str) -> str:
    """'Box A1' -> 'A1', 'Box B2' -> 'B2'; anything else is used as typed."""
    m = re.search(r"(?:^|[\s_-])([A-Za-z]\d+)\s*$", label.strip())
    return m.group(1).upper() if m else label.strip()


def split_anchor(anchor: str) -> tuple[str, str]:
    """Large object field: a 5-6 digit number is an anchor Object ID, anything else is the large object's name."""
    a = anchor.strip()
    if re.fullmatch(r"\d{5,6}", a):
        return a.zfill(6), ""
    return "", a


def taskbox_title(code: str, anchor: str, label: str) -> str:
    _, lo_name = split_anchor(anchor)
    stage = stage_of(label)
    return f"{code} - {lo_name} - {stage}" if lo_name else f"{code} - {stage}"


def taskboxes(conn, catalog: Catalog, statuses=("approved",), pending_only: bool = False, st: dict | None = None) -> list[dict]:
    """Boxes in exactly the shape the userscript's sheet parser produces, so it can use them directly."""
    out = []
    for b in (st or state(conn, catalog))["boxes"]:
        if b["status"] not in statuses or not b["items"]:
            continue
        if pending_only and b["pushed"]:
            continue
        code = b["code"] or b["module"]
        anchor_id, lo_name = split_anchor(b["anchor"])
        contents = [{"object_id": i["id"], "quantity": i["qty"]} for i in b["items"]]
        out.append({
            "module": code, "lo_name": lo_name, "stage": stage_of(b["label"]),
            "title": taskbox_title(code, b["anchor"], b["label"]), "anchor_object_id": anchor_id,
            "is_active": True, "checkbox": "TRUE", "item_count": len(contents),
            "total_qty": sum(c["quantity"] for c in contents), "contents": contents,
            "itemsDetail": [{"object_id": i["id"], "name": display_name(i["name"], i["vi"]), "floor": "",
                             "location": i["slot"], "quantity": i["qty"]} for i in b["items"]],
            "builder": {"box_id": b["id"], "module": b["module"], "label": b["label"],       # never the owner: this route needs no login and an ID is a login
                        "status": b["status"], "pushed": b["pushed"], "day": b["day"], "sheet": b["sheet"]},
        })
    return out


def label_of(catalog: Catalog, oid: str) -> str:
    """'English (Tiếng Việt) 004702' as the sheet names an object, or just the ID when nobody knows its name. For sentences people read."""
    seen = catalog.lookup(oid)
    name = display_name(seen.en, seen.vi) if seen else ""
    return f"{name} {oid}" if name else oid


def sm_queue(conn, catalog: Catalog, max_qty: int) -> dict:
    """Approved boxes that are not in SM yet, split into READY and BLOCKED (with the reasons).
    Pure read: this only decides what the userscript is shown; it never touches SM."""
    st = state(conn, catalog)
    pushed_titles = {(b["title"], b["day"]) for b in st["boxes"] if b["pushed"]}       # a title is used once per day (a unit has the same B2 every day)
    ready, blocked, seen = [], [], set()
    cat = catalog.items()
    cutoff = reuse_cutoff(conn)
    held = sm_held(conn, cutoff)
    sheet = external_binding(conn, cutoff)
    pending = taskboxes(conn, catalog, ("approved",), pending_only=True, st=st)
    newest_day: dict[str, str] = {}
    for tb in pending:
        newest_day[tb["title"]] = max(newest_day.get(tb["title"], ""), tb["builder"]["day"])
    records = None                                       # the sheet's reports, read only when the supporter gate is on
    for tb in pending:
        reasons = []
        if tb["builder"]["day"] < newest_day[tb["title"]]:
            reasons.append("có hộp mới hơn cùng tên")                       # yesterday's box that was never pushed must not be created instead of today's
                                                                            # (two boxes of the same day with one title are caught by the duplicate check below)
        for c in tb["contents"]:
            it = cat.get(c["object_id"])
            if it is None:
                reasons.append(f"{label_of(catalog, c['object_id'])} không còn tồn kho ở SM")
            elif c["object_id"] in held:
                h = held[c["object_id"]]
                reasons.append(f"{label_of(catalog, c['object_id'])} đã nằm trong hộp SM {h['title'] or h['sm_box_id']}")
            elif c["object_id"] in sheet:
                reasons.append(f"{label_of(catalog, c['object_id'])} đã có trong danh sách đã dùng ở bảng tính")
            elif c["quantity"] > min(max_qty, it.stock) or c["quantity"] < 1:
                reasons.append(f"{label_of(catalog, c['object_id'])}: số lượng {c['quantity']} vượt mức cho phép (tối đa {min(max_qty, it.stock)})")
        if not tb["title"].strip():
            reasons.append("thiếu tiêu đề")
        elif (tb["title"], tb["builder"]["day"]) in pushed_titles or (tb["title"], tb["builder"]["day"]) in seen:
            reasons.append(f"tiêu đề \"{tb['title']}\" trùng với một hộp khác")
        seen.add((tb["title"], tb["builder"]["day"]))
        if tb["builder"]["sheet"]["state"] != "off":     # an approved box that is not in SM shows "off" only while the supporter gate is off
            records = sheet_records(conn) if records is None else records
            reasons.extend(_gate_reasons(tb, records.get(tb["builder"]["box_id"])))
        if reasons:
            blocked.append({"box_id": tb["builder"]["box_id"], "title": tb["title"], "reasons": reasons})
        else:
            ready.append(tb)
    return {"ready": ready, "blocked": blocked}


# ----------------------------------------------------------------------------- the supporters' check, reported by the sheet's own script
# For each approved box the sheet's script reports the SC's tick ("this box is needed this shift"), the supporters' tick ("IDs and stock checked")
# and the objects left in the block (a supporter blanks the cell of an object that is missing). The server only records that and compares it with
# the box; it never reads the sheet. While the switch "Chờ người hỗ trợ kiểm trước khi lên SM" is off, none of it changes a box or the queue.
SHEET_MAX_CHECKS = 200                                   # boxes in one report
SHEET_MAX_ROWS = 30                                      # object rows of one box in one report (a block in the sheet has 21)
SHEET_NOTE = "thiếu theo người hỗ trợ"                   # note of an object put on the "used in the sheet" list because the supporters did not have it
SHEET_STALE_TICK = "tick có từ trước, chờ người hỗ trợ bỏ tick và tick lại"          # the supporters' tick was already on the first time the app looked
SHEET_WAIT_REASONS = ("chờ SC tick", "chờ người hỗ trợ kiểm", SHEET_STALE_TICK)


def _tick(v) -> int | None:
    """A tick is trusted only when it is a real true or false; anything else (a text, a number, nothing) is unknown."""
    return 1 if v is True else 0 if v is False else None


def _sheet_qty(v) -> int | None:
    """A quantity as the sheet says it: None = not said or not a number, 0 = none left, otherwise 1 to 9999."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        n = v
    elif isinstance(v, float):
        if not math.isfinite(v):
            return None
        n = math.floor(v)
    elif isinstance(v, str) and re.fullmatch(r"\s*\d{1,6}\s*", v):
        n = int(v)
    else:
        return None
    return 0 if n < 1 else min(9999, n)


def sheet_rows(raw) -> list[dict]:
    """The objects the supporters left in a box's block, as [{"id": "003076", "qty": 1 or None}] in the order given. An entry that is not an
    object or has no valid ID is dropped. The same ID twice becomes one entry with the quantities added up (a blank quantity counts as 1)."""
    out: dict[str, int | None] = {}
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict) or isinstance(r.get("id"), bool) or not isinstance(r.get("id"), (int, str)):
            continue                                     # pad_id would read digits out of anything (a list, a float 3076.0), so only a number or a text goes through it
        oid = pad_id(r["id"])
        if oid is None:
            continue
        q = _sheet_qty(r.get("qty"))
        out[oid] = q if oid not in out else (1 if out[oid] is None else out[oid]) + (1 if q is None else q)
    return [{"id": o, "qty": q} for o, q in out.items()]


def reconcile(items: list[dict], rows: list[dict]) -> dict:
    """Compare what the box holds, items [{"id", "qty"}], with what the supporters left, rows [{"id", "qty"}] (qty None = not said).
    Accepted without asking: an object that is gone (a blank cell, or a quantity of 0) and a smaller quantity.
    Not accepted: an ID the box does not hold ("extra") and a quantity above the box's ("qty_up").
    Returns diff (what is not accepted, empty when the report is clean), left (the box's objects the supporters still have, in box order),
    removed (the ones they do not), lowered (kept with a smaller quantity) and keep ([{"id", "qty"}], the list to apply when diff is empty)."""
    have = {i["id"]: int(i["qty"]) for i in items}
    extra, up, keep = [], [], {}
    for r in sheet_rows(rows):
        oid, q = r["id"], r["qty"]
        if oid not in have:
            extra.append(oid)
        elif q is None:
            keep[oid] = have[oid]
        elif q > have[oid]:
            up.append(oid)
        elif q >= 1:
            keep[oid] = q
    order = [i["id"] for i in items]
    present = set(keep) | set(up)
    return {"diff": [{"id": o, "why": "extra"} for o in sorted(extra)] + [{"id": o, "why": "qty_up"} for o in sorted(up)],
            "left": [o for o in order if o in present], "removed": [o for o in order if o not in present],
            "lowered": [o for o in order if o in keep and keep[o] < have[o]], "keep": [{"id": o, "qty": keep[o]} for o in order if o in keep]}


def sheet_view(status: str, pushed: bool, gate_on: bool, count: int, items: list[dict], record: dict | None) -> dict:
    """The `sheet` part of a box as the screens and the tools see it: {"state", "missing", "diff", "read_at"}.
    off: the gate does not apply (the switch is off, or the box is not an approved box still waiting for SM).
    wait_sc: the SC has not ticked. wait_support: the supporters' tick is off, unreadable, or was already on the first time the app looked
    (record["seen"] is False): that tick may be left over from an earlier day's box in the same block, so it counts only after a report showed it
    off. The SC's tick has no such rule, because an SC may tick before the box is filled.
    ok: both ticks and the report matches the box. diff: an object that is not in the box, or a bigger quantity (listed in diff).
    missing: no object of the box is left. `missing` counts the objects the supporters did not have, also after the box was changed to match."""
    if not gate_on or status != "approved" or pushed:
        return {"state": "off", "missing": 0, "diff": [], "read_at": None}
    out = {"state": "wait_sc", "missing": 0, "diff": [], "read_at": record["read_at"] if record else None}
    if record is None or record["sc"] is not True:
        return out
    if not _support_ticked(record):
        out["state"] = "wait_support"
        return out
    g = reconcile(items, record["rows"])
    out["missing"] = max(0, count - len(g["left"]))
    out["diff"] = g["diff"]
    out["state"] = "diff" if g["diff"] else "missing" if not g["left"] else "ok"
    return out


def _support_ticked(record: dict) -> bool:
    """The supporters' tick, as the gate trusts it: on now, and seen off in an earlier (or this) report."""
    return record["sup"] is True and bool(record["seen"])


def _stored_rows(text) -> list[dict]:
    try:
        return sheet_rows(json.loads(text))
    except (TypeError, ValueError):
        return []                                        # unreadable rows count as an empty block: the box stays blocked, never cleared


def sheet_records(conn, box_id: int | None = None) -> dict[int, dict]:
    """The last report of each box (or of one box): {"sc", "sup" (True, False or None), "seen" (the supporters' tick has been seen off), "rows", "read_at"}."""
    q, args = 'SELECT box_id, sc_tick, supporter_tick, support_seen_unticked, "rows", read_at FROM sheet_checks', ()
    if box_id is not None:
        q, args = q + " WHERE box_id=?", (box_id,)
    return {r["box_id"]: {"sc": None if r["sc_tick"] is None else r["sc_tick"] == 1, "sup": None if r["supporter_tick"] is None else r["supporter_tick"] == 1,
                          "seen": r["support_seen_unticked"] == 1, "rows": _stored_rows(r["rows"]), "read_at": float(r["read_at"])} for r in conn.execute(q, args)}


def _diff_text(diff: list[dict]) -> str:
    parts = []
    for why, word in (("extra", "thừa món"), ("qty_up", "tăng số lượng")):
        ids = [d["id"] for d in diff if d["why"] == why]
        if ids:
            parts.append(f"{word} {', '.join(ids[:3])}" + (" ..." if len(ids) > 3 else ""))
    return "; ".join(parts)


def _gate_reasons(tb: dict, record: dict | None) -> list[str]:
    """Why a box is not ready while the supporter gate is on. Empty only when both ticks are in, the report matches the box and the box
    already equals the supporters' list. Anything unexpected stays blocked."""
    sheet = tb["builder"]["sheet"]
    st = sheet["state"]
    if st == "wait_sc":
        return ["chờ SC tick"]
    if st == "wait_support" and record and record["sup"] is True and not record["seen"]:
        return [SHEET_STALE_TICK]
    if st == "diff":
        return ["bảng tính khác hộp: " + _diff_text(sheet["diff"])]
    if st == "missing":
        return ["người hỗ trợ báo thiếu hết món"]
    if st == "ok" and record and record["sc"] is True and _support_ticked(record):
        g = reconcile([{"id": c["object_id"], "qty": c["quantity"]} for c in tb["contents"]], record["rows"])
        if g["diff"] or not g["left"]:
            return ["bảng tính khác hộp"]
        return ["chờ cập nhật hộp theo kết quả kiểm"] if g["removed"] or g["lowered"] else []         # a clean report not applied yet (a race, or the switch was off)
    return ["chờ người hỗ trợ kiểm"]


def _settle_plan(conn, box_id: int) -> dict | None:
    """What a clean report changes in an approved box that is not in SM: the objects to drop and the quantities to lower. None when there is
    nothing to do, or the report is not both ticks, not clean, or leaves no object. Read only."""
    b = conn.execute("SELECT status FROM boxes WHERE id=?", (box_id,)).fetchone()
    if b is None or b["status"] != "approved" or conn.execute("SELECT 1 FROM sm_push WHERE box_id=?", (box_id,)).fetchone():
        return None
    rec = sheet_records(conn, box_id).get(box_id)
    if rec is None or rec["sc"] is not True or not _support_ticked(rec):
        return None
    rows = box_items(conn, box_id)
    g = reconcile([{"id": r["object_id"], "qty": r["qty"]} for r in rows], rec["rows"])
    if g["diff"] or not g["left"] or not (g["removed"] or g["lowered"]):
        return None
    pos, lowered = {r["object_id"]: r["pos"] for r in rows}, set(g["lowered"])
    return {"removed": [(pos[o], o) for o in g["removed"]], "lowered": [(pos[k["id"]], k["qty"]) for k in g["keep"] if k["id"] in lowered], "left": len(g["left"])}


def _settle_in_tx(conn, box_id: int, now: float) -> bool:
    """Make an approved box equal the supporters' list: drop the objects they did not have (each goes on the "used in the sheet" list so the
    picker leaves it alone until the reuse rule frees it) and lower the quantities they lowered. Call inside tx(). The box stays approved.
    remove_item and set_qty refuse an approved box, so this does their two statements itself; the UNIQUE index on object_id is not involved
    because nothing is added."""
    plan = _settle_plan(conn, box_id)
    if plan is None:
        return False
    for pos, _ in plan["removed"]:
        conn.execute("DELETE FROM box_items WHERE box_id=? AND pos=?", (box_id, pos))
    for pos, q in plan["lowered"]:
        conn.execute("UPDATE box_items SET qty=? WHERE box_id=? AND pos=?", (q, box_id, pos))
    conn.execute("UPDATE boxes SET updated_at=? WHERE id=?", (now, box_id))
    conn.executemany(
        "INSERT INTO external_used(object_id, note, added_at) VALUES(?,?,?) ON CONFLICT (object_id) DO UPDATE SET added_at = CASE "
        "WHEN external_used.added_at > 0 AND external_used.added_at < excluded.added_at THEN excluded.added_at ELSE external_used.added_at END",
        [(oid, SHEET_NOTE, now) for _, oid in plan["removed"]])
    from . import auth
    auth.audit(conn, "bảng tính", "sheet_apply", box_id,                                    # counts only: no object, no person
               f"theo bảng tính: bớt {len(plan['removed'])} món, giảm số lượng {len(plan['lowered'])} món, còn {plan['left']} món", now=now)
    return True


def settle_sheet_checks(conn, now: float | None = None) -> int:
    """Apply every clean report that was not applied yet (it came while the switch was off). Returns the number of boxes changed."""
    now = time.time() if now is None else now
    n = 0
    for (box_id,) in conn.execute("SELECT box_id FROM sheet_checks WHERE sc_tick=1 AND supporter_tick=1 AND support_seen_unticked=1 ORDER BY box_id").fetchall():
        if _settle_plan(conn, box_id) is None:
            continue
        with tx(conn):
            n += _settle_in_tx(conn, box_id, now)
    return n


def save_sheet_checks(conn, checks: list, now: float, settle: bool = False) -> dict:
    """Record what the sheet's script reported: one latest report per box (a new one replaces the old). A box that is not known, not approved or
    already in SM is skipped and counted, and so is an entry that is not a report or is replaced by a later one in the same request. Too many boxes
    or rows in a request is refused as a whole before anything is written. With `settle` (the supporter gate is on) a clean report is also applied
    to the box. Returns {"saved", "skipped"}."""
    if len(checks) > SHEET_MAX_CHECKS:
        raise Invalid(f"Một lần gửi nhận tối đa {SHEET_MAX_CHECKS} hộp, lần này có {len(checks)} hộp. Chia nhỏ rồi gửi lại.")
    parsed: dict[int, tuple] = {}
    skipped = 0
    for ch in checks:
        if not isinstance(ch, dict):
            skipped += 1
            continue
        raw = ch.get("rows")
        if isinstance(raw, list) and len(raw) > SHEET_MAX_ROWS:
            raise Invalid(f"Một hộp nhận tối đa {SHEET_MAX_ROWS} dòng món, lần này có hộp có {len(raw)} dòng. Kiểm tra lại cách bảng tính đọc ô.")
        bid = ch.get("box_id")
        if isinstance(bid, bool) or not isinstance(bid, int) or not 1 <= bid <= 2 ** 53 or not isinstance(raw, list):
            skipped += 1
            continue
        if bid in parsed:
            skipped += 1                                                  # the later line for the same box wins
        parsed[bid] = (_tick(ch.get("sc_tick")), _tick(ch.get("supporter_tick")), json.dumps(sheet_rows(raw), ensure_ascii=False, separators=(",", ":")))
    status, pushed = {}, set()
    if parsed:
        marks = ",".join("?" * len(parsed))
        status = {r["id"]: r["status"] for r in conn.execute(f"SELECT id, status FROM boxes WHERE id IN ({marks})", tuple(parsed)).fetchall()}
        pushed = {r[0] for r in conn.execute(f"SELECT box_id FROM sm_push WHERE box_id IN ({marks})", tuple(parsed)).fetchall()}
    saved = 0
    for bid, (sc, sup, rows) in parsed.items():
        if status.get(bid) != "approved" or bid in pushed:
            skipped += 1
            continue
        with tx(conn):                                                    # one box at a time: the write lock is not held for the whole request
            b = conn.execute("SELECT status FROM boxes WHERE id=?", (bid,)).fetchone()
            if b is None or b["status"] != "approved" or conn.execute("SELECT 1 FROM sm_push WHERE box_id=?", (bid,)).fetchone():
                skipped += 1                                              # changed since the check above
                continue
            conn.execute('INSERT INTO sheet_checks(box_id, sc_tick, supporter_tick, support_seen_unticked, "rows", read_at) VALUES(?,?,?,?,?,?) '
                         'ON CONFLICT (box_id) DO UPDATE SET sc_tick = excluded.sc_tick, supporter_tick = excluded.supporter_tick, "rows" = excluded."rows", '
                         'read_at = excluded.read_at, support_seen_unticked = CASE WHEN excluded.supporter_tick = 1 THEN sheet_checks.support_seen_unticked ELSE 1 END',
                         (bid, sc, sup, 0 if sup == 1 else 1, rows, now))        # off or unreadable sets the flag; it is never cleared again
            if settle:
                _settle_in_tx(conn, bid, now)
            saved += 1
    return {"saved": saved, "skipped": skipped}


# ----------------------------------------------------------------------------- object browser
OBJECT_STATUSES = ("free", "draft", "approved", "pushed", "external", "sm")


def _holders(conn, cutoff=_AUTO) -> tuple[dict, set]:
    """object_id -> {box_id, box, status, pushed}; plus the set of IDs marked as used in the workbook."""
    held = {}
    pushed = {r["box_id"] for r in conn.execute("SELECT box_id FROM sm_push")}
    for r in conn.execute("SELECT i.object_id, b.id, b.module, b.label, b.status FROM box_items i JOIN boxes b ON b.id=i.box_id"):
        status = "pushed" if r["id"] in pushed else ("approved" if r["status"] == "approved" else "draft")
        held[r["object_id"]] = {"box_id": r["id"], "box": f"{r['module']} / {r['label']}", "status": status}
    return held, external_binding(conn, cutoff)


def object_status(oid: str, held: dict, external: set, sm: dict | None = None) -> tuple[str, str]:
    if oid in held:
        return held[oid]["status"], held[oid]["box"]
    if oid in external:
        return "external", "bảng tính"
    if sm and oid in sm:
        return "sm", f"hộp SM {sm[oid]['title'] or sm[oid]['sm_box_id']}"
    return "free", ""


BAND_FILTERS = usage.BAND_KEYS + (usage.UNKNOWN_BAND,)          # the values the band filter of the object list takes


def usage_cfg(conn, switches: dict | None = None) -> dict | None:
    """The settings of the "Giảm món đã dùng nhiều" switch, or None when it is off (then no object is reduced or left out).
    `switches` is the result of automation.switches(conn) when the caller already has it."""
    from . import automation
    sw = (switches if switches is not None else automation.switches(conn))["usage_penalty"]
    return usage.clean_cfg(sw["config"]) if sw["enabled"] else None


def day_text(epoch) -> str | None:
    """'2026-11-10' (UTC) from epoch seconds, or None when unknown."""
    return None if epoch is None else time.strftime("%Y-%m-%d", time.gmtime(epoch))


def object_rows(conn, catalog: Catalog, q: str = "", category: str = "", status: str = "", sort: str = "id",
                band: str = "") -> tuple[list[dict], dict]:
    """Every catalog object that matches the filters, in the requested order, plus a count per status
    (the counts ignore the status filter so the filter chips can show all of them; they follow the band filter, like the search and the category).
    band: '0-99', '100-249', '250-499', '500-999', '1000+' or 'unknown' (by borrow count); empty = all. sort 'uses' is most borrowed first, '-uses' least
    borrowed first, unknown counts last in both."""
    from .catalog import fold
    if band and band not in BAND_FILTERS:
        raise Invalid("Nhóm số lượt mượn không hợp lệ. Chọn một trong: " + ", ".join(BAND_FILTERS) + ".")
    from . import automation
    sw = automation.switches(conn)                       # read once: the reuse switch and the borrow-count switch
    cutoff = reuse_cutoff(conn, switches=sw)
    held, external = _holders(conn, cutoff)
    sm = sm_held(conn, cutoff)
    cfg, now = usage_cfg(conn, sw), time.time()
    words = [w for w in fold(q).split() if w]
    rows, counts = [], {s: 0 for s in OBJECT_STATUSES}
    for it in catalog.items().values():
        if category and (it.category or "(chưa phân loại)") != category:
            continue
        if words and not all(w in it.blob or w in it.id for w in words):
            continue
        if band and usage.band_key(it.uses) != band:
            continue
        st, holder = object_status(it.id, held, external, sm)
        counts[st] += 1
        if status and st != status:
            continue
        w = usage.weight(it.uses, it.created, now, cfg) if cfg is not None and it.uses is not None else None
        rows.append({"id": it.id, "name": it.name, "vi": it.vi, "category": it.category or "(chưa phân loại)",
                     "slot": it.slot, "stock": it.stock, "status": st, "holder": holder,
                     "img": it.img, "detail": images.detail_url(it.id),
                     "uses": it.uses, "created": day_text(it.created), "age_days": usage.age_days_int(it.created, now),
                     "weight": None if w is None else round(w, 4)})
    keys = {"id": lambda r: r["id"], "name": lambda r: (r["vi"] or r["name"]).lower(), "category": lambda r: (r["category"], r["id"])}
    if sort in ("stock", "-stock"):
        rows.sort(key=lambda r: (r["stock"], r["id"]), reverse=sort == "-stock")
    elif sort in ("uses", "-uses"):
        known = sorted((r for r in rows if r["uses"] is not None), key=lambda r: (-r["uses"] if sort == "uses" else r["uses"], r["id"]))
        rows = known + sorted((r for r in rows if r["uses"] is None), key=lambda r: r["id"])
    else:
        rows.sort(key=keys.get(sort, keys["id"]))
    return rows, counts


def usage_report(conn, catalog: Catalog, now: float | None = None) -> dict:
    """What the borrow counts do to the catalog right now (read only). With the switch off nothing is reduced or left out, so excluded and
    reduced are 0 and the top list carries no weights; the counts and the bands are still shown."""
    from . import automation
    now = time.time() if now is None else now
    all_switches = automation.switches(conn)                    # read once for the whole report
    sw = all_switches["usage_penalty"]
    shown = usage.clean_cfg(sw["config"])                       # the settings as saved, also while the switch is off
    cfg = shown if sw["enabled"] else None
    items = list(catalog.items().values())
    st = usage.summary(items, now, shown)
    if cfg is None:
        st["excluded"] = st["reduced"] = 0
    weights = usage.weights_of(items, now, cfg) if cfg is not None else None
    taken = taken_ids(conn, reuse_cutoff(conn, switches=all_switches))
    band_list, unknown = usage.bands(items)
    return {"enabled": cfg is not None, "known": st["known"], "total": st["total"],
            "soft_start": shown["soft_start"], "hard_limit": shown["hard_limit"], "half_life": shown["half_life"],
            "age_effect": shown["age_effect"], "min_pool": shown["min_pool"],
            "over_start": st["over_start"], "excluded": st["excluded"], "reduced": st["reduced"],
            "bands": band_list, "unknown": unknown, "top": usage.top(items, 15, weights, now),
            "free_over_start": sum(1 for it in items if it.uses is not None and it.uses >= shown["soft_start"] and it.id not in taken)}


def objects_view(conn, catalog: Catalog, q: str = "", category: str = "", status: str = "", sort: str = "id",
                 page: int = 1, per: int = 50, band: str = "") -> dict:
    """The catalog with a status per object (free / in a draft / approved / pushed to SM / used in the workbook)."""
    rows, counts = object_rows(conn, catalog, q, category, status, sort, band)
    per = max(1, min(200, per))
    page = max(1, page)
    total = len(rows)
    shown = rows[(page - 1) * per: page * per]
    for r in shown:                                      # only the rows that are sent: the end time of a picture link is worked out for these
        r["img_exp"] = catalog.image_expiry(r["img"]) if r["img"] else None
    return {"total": total, "page": page, "per": per, "pages": max(1, -(-total // per)),
            "counts": counts, "items": shown, "now": time.time()}              # now: img_exp is a server time, the page corrects for its own clock


def coverage(conn, catalog: Catalog) -> list[dict]:
    """Per category: how many usable objects exist, how many are still free, how many are taken."""
    cutoff = reuse_cutoff(conn)
    held, external = _holders(conn, cutoff)
    sm = sm_held(conn, cutoff)
    by: dict[str, dict] = {}
    for it in catalog.items().values():
        c = it.category or "(chưa phân loại)"
        e = by.setdefault(c, {"name": c, "total": 0, "free": 0, "used": 0})
        e["total"] += 1
        if it.id in held or it.id in external or it.id in sm:
            e["used"] += 1
        else:
            e["free"] += 1
    return sorted(by.values(), key=lambda e: (-e["total"], e["name"]))


# ----------------------------------------------------------------------------- housekeeping
def expire(conn, ttl_hours: int) -> int:
    """Abandoned drafts/errors release their objects; a 'generating' box stuck for 10 min is marked failed.
    Called on every page poll, so it only takes the write lock (and bumps the revision) when there is work."""
    now = time.time()
    stale_gen, stale_draft = now - 600, now - ttl_hours * 3600
    if conn.execute(
        "SELECT 1 FROM boxes WHERE (status='generating' AND updated_at < ?) "
        "OR (status IN ('draft','error') AND updated_at < ?) LIMIT 1", (stale_gen, stale_draft)
    ).fetchone() is None:
        return 0
    with tx(conn):
        conn.execute(
            "UPDATE boxes SET status='error', message=?, updated_at=? WHERE status='generating' AND updated_at < ?",
            ("Việc chọn đồ bị dừng giữa chừng. Bấm Chọn lại.", now, stale_gen))
        cur = conn.execute(
            "DELETE FROM boxes WHERE status IN ('draft','error') AND updated_at < ?", (stale_draft,))
        return cur.rowcount


# ----------------------------------------------------------------------------- the life of a box, and the day of the person who makes them
VN = datetime.timezone(datetime.timedelta(hours=7))              # the team works in Vietnam time, and a "day" is a Vietnam day


def vn_now_text() -> str:
    """The time on the printed paper: Vietnam time, whatever the clock of the server says (Render runs on UTC)."""
    return datetime.datetime.now(VN).strftime("%d/%m/%Y %H:%M")


def vn_day(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, VN).strftime("%Y-%m-%d")


def _day_gap(later: str, earlier: str) -> int:
    return (datetime.date.fromisoformat(later) - datetime.date.fromisoformat(earlier)).days


def retire_done(conn, days: int, now: float | None = None) -> int:
    """An approved box is finished `days` days after the day it was made (Vietnam days: made on the 11th, finished on the 13th for days=2). Its
    objects become free again and it leaves the list; what stays is a line in box_history (so the page can still say what was done on recent
    days). Nothing is done for drafts (they expire on their own) or when days is 0. Takes the write lock only when there is something to retire."""
    if days <= 0:
        return 0
    now = time.time() if now is None else now
    limit = datetime.datetime.fromtimestamp(now, VN).date() - datetime.timedelta(days=days)
    boundary = datetime.datetime.combine(limit + datetime.timedelta(days=1), datetime.time(0), tzinfo=VN).timestamp()       # midnight after the last day that is finished
    if conn.execute("SELECT 1 FROM boxes WHERE status='approved' AND created_at < ? LIMIT 1", (boundary,)).fetchone() is None:
        return 0
    n = 0
    with tx(conn):
        for b in conn.execute("SELECT * FROM boxes WHERE status='approved' AND created_at < ?", (boundary,)).fetchall():
            items = [{"id": i["object_id"], "qty": i["qty"]} for i in box_items(conn, b["id"])]
            sm = conn.execute("SELECT sm_box_id FROM sm_push WHERE box_id=?", (b["id"],)).fetchone()
            conn.execute(
                "INSERT INTO box_history(box_id, module, label, code, anchor, theme, count, owner, title, sm_box_id, created_at, done_at, day, items) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT (box_id) DO NOTHING",
                (b["id"], b["module"], b["label"], b["code"], b["anchor"], b["theme"], b["count"], b["owner"],
                 taskbox_title(b["code"] or b["module"], b["anchor"], b["label"]), str(sm[0]) if sm else "", b["created_at"], now,
                 vn_day(b["created_at"]), json.dumps(items)))
            conn.execute("DELETE FROM boxes WHERE id=?", (b["id"],))         # frees the name for the next day and, by cascade, the objects and the push record
            n += 1
    return n


HISTORY_DAYS = 30


def prune_history(conn, now: float | None = None) -> None:
    now = time.time() if now is None else now
    old = now - HISTORY_DAYS * 86400
    if conn.execute("SELECT 1 FROM box_history WHERE done_at < ? LIMIT 1", (old,)).fetchone():
        conn.execute("DELETE FROM box_history WHERE done_at < ?", (old,))


def housekeeping(conn, ttl_hours: int, done_days: int) -> None:
    """What every page poll does first: abandoned drafts go, finished boxes are retired, old history is dropped. Cheap when there is nothing to do."""
    expire(conn, ttl_hours)
    retire_done(conn, done_days)
    prune_history(conn)


def kind_of(label: str, code: str = "") -> str:
    """'Box B2' or 'M8-02 Box B2' -> 'B2'."""
    return stage_of(label)


def shown_kind(label: str, code: str) -> str:
    """The kind as the person chose it ('Box B2'): the stored name has the section code in front."""
    return label[len(code) + 1:] if code and label.startswith(code + " ") and len(label) > len(code) + 1 else label


def recent_boxes(conn, module: str, code: str = "", days: int = 7, now: float | None = None, limit: int = 12) -> list[dict]:
    """What was made lately for this module (and this section, when a code is given): the theme of each box, newest first, from live boxes and from
    the history of finished ones. Boxes without a theme are left out: the point is to avoid doing the same scene again."""
    now = time.time() if now is None else now
    since = now - (days + 1) * 86400
    m, c = module.strip().lower(), code.strip().lower()
    today = vn_day(now)
    rows = [("đang có", r) for r in conn.execute("SELECT module, label, code, theme, created_at FROM boxes WHERE created_at >= ?", (since,)).fetchall()]
    rows += [("đã xong", r) for r in conn.execute("SELECT module, label, code, theme, created_at FROM box_history WHERE created_at >= ?", (since,)).fetchall()]
    out = []
    for state, r in sorted(rows, key=lambda x: -x[1]["created_at"]):
        if r["module"].strip().lower() != m or (c and r["code"].strip().lower() != c) or not r["theme"].strip():
            continue
        d = vn_day(r["created_at"])
        if _day_gap(today, d) > days:
            continue
        out.append({"day": d, "days_ago": _day_gap(today, d), "kind": shown_kind(r["label"], r["code"]), "code": r["code"], "theme": r["theme"].strip(), "state": state})
        if len(out) >= limit:
            break
    return out


SHIFTS = {"full": "Cả ngày", "first": "Nửa ca đầu", "second": "Nửa ca sau", "b1b2": "Chỉ làm B1 và B2", "off": "Nghỉ"}
# the boxes a unit needs today, by how its collector works (docs/SC_PROCESS.md): kind, optional, a note
NEEDS = {
    "full": [("B2", False, ""), ("B3", False, ""), ("A1", False, "")],
    "first": [("B2", True, "Có thể bỏ B2."), ("A1", False, "")],
    "second": [("B3", False, "")],
    "b1b2": [("B2", False, "Dùng B2 làm B3, không làm B3 mới."), ("A1", False, "")],
    "off": [],
}
MAX_UNITS = 40


def _clean_units(units) -> list[dict]:
    out, seen = [], set()
    for u in units or []:
        code = str((u.get("code") if isinstance(u, dict) else getattr(u, "code", "")) or "").strip()
        anchor = str((u.get("anchor") if isinstance(u, dict) else getattr(u, "anchor", "")) or "").strip()
        shift = str((u.get("shift") if isinstance(u, dict) else getattr(u, "shift", "full")) or "full").strip()
        if not code:
            raise Invalid("Mỗi unit cần một mã, ví dụ M8-02.")
        if len(code) > 20 or len(anchor) > 120:
            raise Invalid(f"Unit {code[:20]}: mã hoặc tên đồ lớn dài quá.")
        if has_markup(code) or has_markup(anchor):
            raise Invalid("Một unit có mã hoặc đồ lớn với ký tự không dùng được.")
        if shift not in SHIFTS:
            raise Invalid(f"Unit {code}: ca làm không đúng. Chọn một trong: " + ", ".join(SHIFTS.values()) + ".")
        if code.lower() in seen:
            raise Invalid(f"Mã unit {code} bị lặp.")
        seen.add(code.lower())
        out.append({"code": code, "anchor": anchor, "shift": shift})
    if len(out) > MAX_UNITS:
        raise Invalid(f"Tối đa {MAX_UNITS} unit.")
    return out


def plan_save(conn, owner: str, day: str, module: str, units) -> None:
    module = module.strip()
    if len(module) > 20:
        raise Invalid("Module dài quá.")
    data = json.dumps({"module": module, "units": _clean_units(units)}, ensure_ascii=False)
    now = time.time()
    conn.execute("INSERT INTO plans(owner, day, data, updated_at) VALUES(?,?,?,?) ON CONFLICT (owner, day) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at",
                 (owner, day, data, now))
    conn.execute("DELETE FROM plans WHERE day < ?", (vn_day(now - 45 * 86400),))


def build_report(day: str, module: str, units: list[dict]) -> str:
    """The end-of-day note about the units that did not work a full day, ready to copy and send. Empty when no unit is listed."""
    if not units:
        return ""
    d = datetime.date.fromisoformat(day)
    head = f"Báo cáo ngày {d.day:02d}/{d.month:02d}" + (f", module {module}" if module else "")
    name = lambda u: u["code"] + (f" ({u['anchor']})" if u["anchor"] else "")
    groups = [("Unit không đi làm", "off"), ("Unit làm nửa ca đầu", "first"), ("Unit làm nửa ca sau", "second"), ("Unit chỉ làm B1 và B2", "b1b2")]
    lines = [f"{title}: " + ", ".join(name(u) for u in units if u["shift"] == key) + "." for title, key in groups if any(u["shift"] == key for u in units)]
    return head + (":\n" + "\n".join(lines) if lines else ": tất cả unit đều đi làm cả ngày.")


def plan_view(conn, owner: str, today: str) -> dict:
    """Today's plan of this person. With none saved for today it starts from the last one (same units, everyone back to a full day), because the
    module and its units stay the same for a week. For every unit: the boxes it needs by its shift, and whether each exists yet."""
    row = conn.execute("SELECT data FROM plans WHERE owner=? AND day=?", (owner, today)).fetchone()
    saved = row is not None
    if row is None:
        row = conn.execute("SELECT data FROM plans WHERE owner=? AND day < ? ORDER BY day DESC LIMIT 1", (owner, today)).fetchone()
    data = json.loads(row[0]) if row else {"module": "", "units": []}
    units = [{**u, "shift": "full" if not saved else u["shift"]} for u in data.get("units", [])]
    midnight = datetime.datetime.combine(datetime.date.fromisoformat(today), datetime.time(0), tzinfo=VN).timestamp()
    mine = conn.execute("SELECT id, label, code, status, created_at FROM boxes WHERE owner=? AND created_at >= ?", (owner, midnight)).fetchall()
    out = []
    for u in units:
        needs = []
        for kind, optional, note in NEEDS[u["shift"]]:
            hit = next((b for b in mine if b["code"].strip().lower() == u["code"].lower() and stage_of(b["label"]).upper() == kind), None)
            needs.append({"kind": kind, "optional": optional, "note": note, "state": hit["status"] if hit else "none", "box_id": hit["id"] if hit else None})
        out.append({**u, "shift_text": SHIFTS[u["shift"]], "needs": needs})
    # no report until the day's attendance is saved: from yesterday's units it would say "everyone worked a full day" to the manager
    return {"day": today, "module": data.get("module", ""), "saved": saved, "units": out, "report": build_report(today, data.get("module", ""), units) if saved else "",
            "shifts": [{"key": k, "text": v} for k, v in SHIFTS.items()], "to_return": to_return(conn, owner, today)}


def to_return(conn, owner: str, today: str) -> list[dict]:
    """The boxes of yesterday and the day before: the ones to hand back in SM at the start of the day (the app only reminds, it never touches SM)."""
    out = []
    for b in conn.execute("SELECT module, label, code, anchor, created_at FROM boxes WHERE owner=? AND status='approved'", (owner,)).fetchall():
        out.append((b["created_at"], b["module"], b["label"], b["code"], taskbox_title(b["code"] or b["module"], b["anchor"], b["label"])))
    for b in conn.execute("SELECT module, label, code, title, created_at FROM box_history WHERE owner=?", (owner,)).fetchall():
        out.append((b["created_at"], b["module"], b["label"], b["code"], b["title"]))
    rows = []
    for created, module, label, code, title in sorted(out, reverse=True):
        gap = _day_gap(today, vn_day(created))
        if gap in (1, 2):
            rows.append({"day": vn_day(created), "days_ago": gap, "module": module, "kind": shown_kind(label, code), "title": title})
    return rows


# ----------------------------------------------------------------------------- boxes
# Text a person types ends up in the team script's table on the SM page, which writes it as HTML. Characters that can start markup are refused here,
# because the script is not part of this repo and the server is the one place that can close this.
_MARKUP = re.compile("[<>\"'`\x00-\x1f\x7f\u00ad\u061c\u180e\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]")
MARKUP_TEXT = "Tên, mã hoặc đồ lớn có ký tự không dùng được: < > \" ' `. Bỏ chúng rồi làm lại."


def has_markup(text: str) -> bool:
    return bool(_MARKUP.search(text or ""))


def create_box(conn, module: str, label: str, theme: str, categories: list[str], count: int, owner: str,
               code: str = "", anchor: str = "") -> int:
    module, label = module.strip(), label.strip()
    if not module or not label:
        raise Invalid("Cần nhập Module và tên hộp (ví dụ M8 và Box A1).")
    if not 1 <= count <= 60:
        raise Invalid("Số món trong hộp phải từ 1 đến 60.")
    if any(has_markup(x) for x in (module, label, code, anchor)):
        raise Invalid(MARKUP_TEXT)
    now = time.time()
    try:
        with tx(conn):
            cur = conn.execute(
                "INSERT INTO boxes(module,label,theme,categories,code,anchor,count,status,owner,created_at,updated_at,day) "
                "VALUES(?,?,?,?,?,?,?,'generating',?,?,?,?) RETURNING id",
                (module, label, theme.strip(), json.dumps(categories), code.strip(), anchor.strip(), count,
                 owner.strip(), now, now, vn_day(now)))
            return cur.fetchone()[0]
    except IntegrityError:
        raise Conflict(f"{module} / {label} đã có. Mở hộp đó bên dưới hoặc đặt tên khác.")


def set_status(conn, box_id: int, status: str, message: str = "") -> None:
    with tx(conn):
        conn.execute("UPDATE boxes SET status=?, message=?, updated_at=? WHERE id=?",
                     (status, message, time.time(), box_id))


def _not_pushed(conn, box_id: int) -> None:
    r = conn.execute("SELECT sm_box_id FROM sm_push WHERE box_id=?", (box_id,)).fetchone()
    if r:
        raise Conflict("Hộp đã lên SM nên không sửa hay xoá được ở đây. Cần đổi thì báo admin.")


def delete_box(conn, box_id: int) -> None:
    with tx(conn):
        _not_pushed(conn, box_id)
        conn.execute("DELETE FROM boxes WHERE id=?", (box_id,))


SM_BOX_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def mark_pushed(conn, box_id: int, sm_box_id: str) -> bool:
    """Record that an approved box now exists in SM; refuses boxes that are not approved. The same SM id again changes nothing (False). A
    different id for a box that already has one is refused and the first stays: two ids would mean two boxes made in SM for one box here,
    and the first record is the one the "already in SM" checks rely on. Returns True when a record was written."""
    sm_box_id = str(sm_box_id).strip()
    if not sm_box_id:
        raise Invalid("Thiếu Box ID của SM.")
    if not SM_BOX_ID.fullmatch(sm_box_id):
        raise Invalid("Box ID của SM chỉ gồm chữ, số, dấu gạch ngang và gạch dưới, dài tối đa 64 ký tự.")
    with tx(conn):
        b = get_box(conn, box_id)
        if b["status"] != "approved":
            raise Conflict("Chỉ hộp đã duyệt mới được đánh dấu là đã lên SM.")
        have = conn.execute("SELECT sm_box_id FROM sm_push WHERE box_id=?", (box_id,)).fetchone()
        if have:
            if have["sm_box_id"] == sm_box_id:
                return False
            raise Conflict(f"Hộp này đã được ghi là có trong SM với mã {have['sm_box_id']}. Mã {sm_box_id} không thay được mã đó. "
                           "Mở SM xem có hai hộp trùng không, rồi báo admin.")
        conn.execute("INSERT INTO sm_push(box_id, sm_box_id, pushed_at) VALUES(?,?,?)", (box_id, sm_box_id, time.time()))
        return True


def returnable(conn, include_today: bool = False, now: float | None = None) -> list[dict]:
    """Boxes that are in SM or were (a live sm_push row, or a line in box_history) and were made before today in Vietnam time, so the tool can
    look for them there and show what it would return. Today's boxes only with include_today. `sm_box_id` is '' when the app never heard
    back from the tool. The title is the one the tool sent to SM. No owner: the route that serves this needs only the sync token."""
    today = vn_day(time.time() if now is None else now)
    out, seen = [], set()
    for b in conn.execute("SELECT b.id, b.day, b.module, b.code, b.anchor, b.label, p.sm_box_id FROM sm_push p JOIN boxes b ON b.id = p.box_id").fetchall():
        seen.add(b["id"])
        out.append({"box_id": b["id"], "day": b["day"], "title": taskbox_title(b["code"] or b["module"], b["anchor"], b["label"]), "sm_box_id": b["sm_box_id"]})
    for h in conn.execute("SELECT box_id, day, module, code, anchor, label, title, sm_box_id FROM box_history").fetchall():
        if h["box_id"] in seen:                          # a box retired between the two reads: the live row already told it
            continue
        out.append({"box_id": h["box_id"], "day": h["day"], "sm_box_id": h["sm_box_id"],
                    "title": h["title"] or taskbox_title(h["code"] or h["module"], h["anchor"], h["label"])})
    return sorted((r for r in out if include_today or r["day"] < today), key=lambda r: (r["day"], r["box_id"]))


def unmark_pushed(conn, box_id: int) -> None:
    with tx(conn):
        get_box(conn, box_id)
        conn.execute("DELETE FROM sm_push WHERE box_id=?", (box_id,))


def approve(conn, box_id: int) -> None:
    with tx(conn):
        b = get_box(conn, box_id)
        if b["status"] == "generating":
            raise Conflict("Hộp này vẫn đang được chọn đồ.")
        n = conn.execute("SELECT COUNT(*) FROM box_items WHERE box_id=?", (box_id,)).fetchone()[0]
        if n != b["count"]:
            raise Invalid(f"Hộp mới có {n}/{b['count']} món. Điền đủ món trước khi duyệt.")
        conn.execute("UPDATE boxes SET status='approved', message='', updated_at=? WHERE id=?", (time.time(), box_id))


def reopen(conn, box_id: int) -> None:
    with tx(conn):
        get_box(conn, box_id)
        _not_pushed(conn, box_id)
        conn.execute("UPDATE boxes SET status='draft', updated_at=? WHERE id=?", (time.time(), box_id))


def _editable(conn, box_id: int) -> object:
    b = get_box(conn, box_id)
    if b["status"] == "approved":
        raise Conflict("Hộp này đã duyệt nên chưa sửa được. Bấm Bỏ duyệt nếu cần sửa lại.")
    if b["status"] == "generating":
        raise Conflict("Hộp này vẫn đang được chọn đồ.")
    return b


# ----------------------------------------------------------------------------- items
def claim(conn, box_id: int, assignments: list[tuple[int, Item, int, str]]) -> list[int]:
    """Atomically give objects to a box. assignments = [(pos, item, qty, source)].
    Objects that are taken in the meantime are skipped, never double-booked. Returns the positions filled."""
    filled = []
    with tx(conn):
        if conn.execute("SELECT 1 FROM boxes WHERE id=?", (box_id,)).fetchone() is None:
            return []
        taken = taken_ids(conn)
        used_pos = {r["pos"] for r in box_items(conn, box_id)}
        for pos, item, qty, source in assignments:
            if item.id in taken or pos in used_pos:
                continue
            conn.execute("INSERT INTO box_items(box_id,pos,object_id,qty,locked,source) VALUES(?,?,?,?,0,?)",
                         (box_id, pos, item.id, qty, source))
            taken.add(item.id)
            used_pos.add(pos)
            filled.append(pos)
        conn.execute("UPDATE boxes SET updated_at=? WHERE id=?", (time.time(), box_id))
    return filled


def release_unlocked(conn, box_id: int, positions: list[int] | None = None) -> list[str]:
    """Free the unlocked objects of a box (all, or just the given positions). Returns the released object IDs."""
    with tx(conn):
        _editable(conn, box_id)
        q = "SELECT pos, object_id FROM box_items WHERE box_id=? AND locked=0"
        rows = conn.execute(q, (box_id,)).fetchall()
        rows = [r for r in rows if positions is None or r["pos"] in positions]
        for r in rows:
            conn.execute("DELETE FROM box_items WHERE box_id=? AND pos=?", (box_id, r["pos"]))
        conn.execute("UPDATE boxes SET status='generating', message='', updated_at=? WHERE id=?", (time.time(), box_id))
        return [r["object_id"] for r in rows]


def heal_box(conn, box_id: int, catalog: Catalog) -> list[int]:
    """Remove the objects SM no longer lists from a box that is not in SM yet, and mark it for a refill.
    Returns the emptied positions (none when there is nothing to fix, the box is busy, or it was already pushed)."""
    with tx(conn):
        b = get_box(conn, box_id)
        if b["status"] == "generating" or conn.execute("SELECT 1 FROM sm_push WHERE box_id=?", (box_id,)).fetchone():
            return []
        live = catalog.items()
        gone = [r for r in box_items(conn, box_id) if r["object_id"] not in live]
        if not gone:
            return []
        for r in gone:
            conn.execute("DELETE FROM box_items WHERE box_id=? AND pos=?", (box_id, r["pos"]))
        ids = ", ".join(r["object_id"] for r in gone[:6])
        conn.execute("UPDATE boxes SET status='generating', message=?, updated_at=? WHERE id=?",
                     (f"SM không còn {len(gone)} món ({ids}). Đang thay bằng món khác.", time.time(), box_id))
        return [r["pos"] for r in gone]


def set_locked(conn, box_id: int, pos: int, locked: bool) -> None:
    with tx(conn):
        _editable(conn, box_id)
        cur = conn.execute("UPDATE box_items SET locked=? WHERE box_id=? AND pos=?", (int(locked), box_id, pos))
        if cur.rowcount == 0:
            raise NotFound("Ô này đang trống.")


def set_qty(conn, box_id: int, pos: int, qty: int, catalog: Catalog, max_qty: int) -> None:
    with tx(conn):
        _editable(conn, box_id)
        row = conn.execute("SELECT object_id FROM box_items WHERE box_id=? AND pos=?", (box_id, pos)).fetchone()
        if row is None:
            raise NotFound("Ô này đang trống.")
        it = catalog.get(row["object_id"])
        cap = min(max_qty, it.stock) if it else max_qty
        conn.execute("UPDATE box_items SET qty=? WHERE box_id=? AND pos=?", (max(1, min(cap, qty)), box_id, pos))


def remove_item(conn, box_id: int, pos: int) -> None:
    with tx(conn):
        _editable(conn, box_id)
        conn.execute("DELETE FROM box_items WHERE box_id=? AND pos=?", (box_id, pos))
        conn.execute("UPDATE boxes SET updated_at=? WHERE id=?", (time.time(), box_id))


def add_item(conn, box_id: int, object_id: str, qty: int, catalog: Catalog, max_qty: int, pos: int | None = None) -> int:
    oid = pad_id(object_id)
    item = catalog.get(oid) if oid else None
    if item is None:
        raise Invalid(f"Không thấy món {object_id} trong kho.")
    with tx(conn):
        b = _editable(conn, box_id)
        taken_by = owner_of(conn, item.id)
        if taken_by:
            raise Conflict(f"{label_of(catalog, item.id)} đã nằm trong {taken_by}.")
        used = {r["pos"] for r in box_items(conn, box_id)}
        if pos is None:
            free = [p for p in range(1, b["count"] + 1) if p not in used]
            if not free:
                raise Conflict("Hộp đã đầy. Hãy bỏ bớt một món trước.")
            pos = free[0]
        elif pos in used or not 1 <= pos <= b["count"]:
            raise Conflict("Ô này không còn trống.")
        q = max(1, min(max_qty, item.stock, qty))
        conn.execute("INSERT INTO box_items(box_id,pos,object_id,qty,locked,source) VALUES(?,?,?,?,1,'manual')",
                     (box_id, pos, item.id, q))
        conn.execute("UPDATE boxes SET updated_at=? WHERE id=?", (time.time(), box_id))
        return pos


def add_items(conn, box_id: int, object_ids: list[str], catalog: Catalog, max_qty: int) -> dict:
    """Put several hand-picked objects into one box. Each is its own atomic claim, so a clash with another
    user skips only that object. Returns what was added and why the rest were not."""
    added, failed = [], []
    for raw in dict.fromkeys(object_ids):
        try:
            added.append({"id": pad_id(raw) or raw, "pos": add_item(conn, box_id, raw, 1, catalog, max_qty)})
        except (Conflict, Invalid) as e:
            failed.append({"id": raw, "reason": str(e)})
    return {"added": added, "failed": failed}


def create_batch(conn, specs: list[dict], default_count: int, owner: str) -> list[int]:
    """Create several boxes in one go. All-or-nothing on validation: if any spec is bad, nothing is created."""
    if not specs:
        raise Invalid("Chưa có hộp nào để tạo.")
    if len(specs) > 12:
        raise Invalid("Mỗi lần chỉ tạo tối đa 12 hộp.")
    problems, names = [], set()
    existing = {(r["module"], r["label"]) for r in conn.execute("SELECT module, label FROM boxes WHERE day=?", (vn_day(time.time()),))}
    for i, sp in enumerate(specs, 1):
        module, label = str(sp.get("module", "")).strip(), str(sp.get("label", "")).strip()
        count = sp.get("count") or default_count
        if not module or not label:
            problems.append(f"Hộp {i}: thiếu Module hoặc tên hộp.")
        elif (module, label) in names:
            problems.append(f"Hộp {i}: {module} / {label} bị trùng trong danh sách.")
        elif (module, label) in existing:
            problems.append(f"Hộp {i}: {module} / {label} đã có.")
        if not isinstance(count, int) or not 1 <= count <= 60:
            problems.append(f"Hộp {i}: số món phải từ 1 đến 60.")
        names.add((module, label))
    if problems:
        raise Invalid(" ".join(problems))
    ids = []
    try:
        for sp in specs:
            ids.append(create_box(conn, str(sp["module"]), str(sp["label"]), str(sp.get("theme", ""))[:300],
                                  list(sp.get("categories") or []), sp.get("count") or default_count, owner,
                                  str(sp.get("code") or "")))
    except Exception:                       # someone created the same name a moment ago: undo, so it stays all-or-nothing
        for bid in ids:
            with tx(conn):
                conn.execute("DELETE FROM boxes WHERE id=?", (bid,))
        raise
    return ids


# ----------------------------------------------------------------------------- IDs already used in the workbook
def parse_since(since: str | None, now: float | None = None) -> float:
    """'2026-10-15' -> epoch seconds (noon UTC of that day). Empty -> now. A date in the future or one that cannot be read is refused."""
    now = time.time() if now is None else now
    t = (since or "").strip()
    if not t:
        return now
    try:
        d = datetime.datetime.strptime(t, "%Y-%m-%d").replace(hour=12, tzinfo=datetime.timezone.utc)
    except ValueError:
        raise Invalid("Ngày phải có dạng năm-tháng-ngày, ví dụ 2026-10-15.")
    if d.year < 2000:
        raise Invalid("Năm chưa đúng. Hãy nhập đủ 4 số, ví dụ 2026-10-15.")
    if d.timestamp() > now + 86400:
        raise Invalid("Ngày tạo hộp không thể ở tương lai.")
    return min(d.timestamp(), now)


def import_used(conn, text: str, note: str, catalog: Catalog, since: str | None = None) -> dict:
    """Mark IDs as used in the sheet. `since` is the day the boxes that used them were made (default today): with the reuse
    switch on, they are free again once that day is more than N days back.
    An ID that is already listed keeps what it has, unless a day is given: then an ID with no day takes it, and a later day replaces an
    earlier one (never the other way round). That also holds for an ID that sits in a box here, so removing it from the box cannot free
    an ID the sheet used more recently. Leaving the day empty never changes a listed ID."""
    from . import automation
    given = bool((since or "").strip())
    when = parse_since(since)
    ids, bad = [], []
    for tok in re.split(r"[\s,;]+", text.strip()):
        if not tok:
            continue
        oid = pad_id(tok)
        (ids if oid else bad).append(oid or tok)
    in_box = []
    with tx(conn):
        in_boxes = {r[0] for r in conn.execute("SELECT object_id FROM box_items")}
        listed = {r[0]: r["added_at"] for r in conn.execute("SELECT object_id, added_at FROM external_used")}
        new, later, kept = [], [], 0
        for oid in dict.fromkeys(ids):
            if oid in in_boxes:
                in_box.append(oid)
            if oid in listed:
                if given and (listed[oid] <= 0 or when > listed[oid]):
                    later.append((when, note.strip(), oid))
                else:
                    kept += 1
            elif oid not in in_boxes:
                new.append((oid, note.strip(), when))
        conn.executemany("INSERT INTO external_used(object_id, note, added_at) VALUES(?,?,?) ON CONFLICT (object_id) DO NOTHING", new)
        conn.executemany("UPDATE external_used SET added_at=?, note=COALESCE(NULLIF(?, ''), note) WHERE object_id=?", later)
        sw = automation.switches(conn)["reuse_old"]
    unknown = [o for o in ids if catalog.get(o) is None]
    reusable_on = time.strftime("%Y-%m-%d", time.gmtime(when + sw["config"]["days"] * 86400)) if sw["enabled"] else ""
    return {"added": len(new), "refreshed": len(later), "kept": kept, "reusable_on": reusable_on,
            "already_in_a_box": in_box, "not_in_catalog": unknown, "unreadable": bad}


def remove_used(conn, oid: str) -> None:
    with tx(conn):
        conn.execute("DELETE FROM external_used WHERE object_id=?", (oid,))


# ----------------------------------------------------------------------------- live catalog snapshot (pushed from Scenario Manager)
def save_snapshot(conn, rows: list[dict], synced_at: float) -> None:
    blob = json.dumps({"synced_at": synced_at, "rows": rows}, ensure_ascii=False, separators=(",", ":"))
    with tx(conn):
        conn.execute("INSERT INTO catalog_snapshot(k, v) VALUES('live', ?) "
                     "ON CONFLICT (k) DO UPDATE SET v = excluded.v", (blob,))


def load_snapshot(conn) -> tuple[list[dict], float] | None:
    r = conn.execute("SELECT v FROM catalog_snapshot WHERE k='live'").fetchone()
    if r is None:
        return None
    d = json.loads(r[0])
    return d["rows"], d["synced_at"]


# ----------------------------------------------------------------------------- backup: export everything the app owns, import without overwriting
BACKUP_FORMAT = "box-builder-backup"
BACKUP_VERSION = 1
_SOURCES = ("llm", "manual", "fill", "import")


def export_backup(conn) -> dict:
    """Boxes with their items, pushed records, used-in-sheet IDs and automation switches. Never secrets, the catalog or the SM snapshot."""
    from . import automation
    pushed = {r["box_id"]: {"sm_box_id": r["sm_box_id"], "pushed_at": r["pushed_at"]} for r in conn.execute("SELECT * FROM sm_push")}
    boxes = []
    for b in conn.execute("SELECT * FROM boxes ORDER BY module, label, id").fetchall():
        boxes.append({
            "module": b["module"], "label": b["label"], "theme": b["theme"], "categories": json.loads(b["categories"]),
            "code": b["code"], "anchor": b["anchor"], "count": b["count"], "status": b["status"], "owner": b["owner"],
            "items": [{"pos": i["pos"], "object_id": i["object_id"], "qty": i["qty"], "locked": bool(i["locked"]), "source": i["source"]}
                      for i in box_items(conn, b["id"])],
            "pushed": pushed.get(b["id"]),
        })
    used = [{"object_id": r["object_id"], "note": r["note"], "added_at": r["added_at"]} for r in conn.execute("SELECT * FROM external_used ORDER BY object_id")]
    switches = [{"key": k, "enabled": v["enabled"], "config": v["config"]} for k, v in automation.switches(conn).items()]
    return {"format": BACKUP_FORMAT, "version": BACKUP_VERSION, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "boxes": boxes, "used": used, "automations": switches}


def _text(v, limit: int) -> str:
    return str(v).replace("\x00", "").strip()[:limit] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def import_backup(conn, data, catalog: Catalog, max_qty: int, dry_run: bool = True, note: str = "Khôi phục từ bản sao lưu") -> dict:
    """Merge a backup into this app. Nothing is overwritten: a box whose module and name exist is skipped, an object that is already
    taken (another box, the sheet list, an SM box) or no longer in the kho is skipped, and a pushed record is restored only for a box
    that came in complete. Each box is written atomically. With dry_run nothing is written: the report is what would happen."""
    from . import automation
    if not isinstance(data, dict) or data.get("format") != BACKUP_FORMAT:
        raise Invalid("Đây không phải tệp sao lưu của Box builder.")
    if data.get("version") != BACKUP_VERSION:
        raise Invalid(f"Phiên bản sao lưu {_text(data.get('version'), 10) or '?'} chưa được hỗ trợ (cần {BACKUP_VERSION}).")
    boxes, used, switches = data.get("boxes"), data.get("used", []), data.get("automations", [])
    if not isinstance(boxes, list) or len(boxes) > 500 or not isinstance(used, list) or len(used) > 20000 or not isinstance(switches, list):
        raise Invalid("Tệp sao lưu sai cấu trúc hoặc quá lớn.")
    live = catalog.items()
    holders = {r["object_id"]: f"{r['module']} / {r['label']}" for r in conn.execute(
        "SELECT i.object_id, b.module, b.label FROM box_items i JOIN boxes b ON b.id = i.box_id")}
    for oid in external_binding(conn):
        holders.setdefault(oid, "danh sách đã dùng trong bảng tính")
    for oid, h in sm_held(conn).items():
        holders.setdefault(oid, f"hộp SM {h['title'] or h['sm_box_id']}")
    existing = {(r["module"], r["label"]) for r in conn.execute("SELECT module, label FROM boxes WHERE day=?", (vn_day(time.time()),))}
    taken_now: set[str] = set()                                       # objects this file has already given to a box
    out = []
    for raw in boxes:
        entry = {"module": "", "label": "", "result": "skipped", "reason": "", "items_total": 0, "items_added": 0, "items_skipped": [],
                 "final_status": "", "pushed_restored": False}
        out.append(entry)
        if not isinstance(raw, dict):
            entry["reason"] = "mục này không phải một hộp"
            continue
        module, label = _text(raw.get("module"), 21), _text(raw.get("label"), 41)
        entry["module"], entry["label"] = module[:20], label[:40]
        count = raw.get("count")
        if not module or not label or len(module) > 20 or len(label) > 40:
            entry["reason"] = "thiếu hoặc sai Module hay tên hộp"
            continue
        if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 60:
            entry["reason"] = "số món phải từ 1 đến 60"
            continue
        if any(has_markup(_text(raw.get(k), 120)) for k in ("module", "label", "code", "anchor")):
            entry["reason"] = "tên, mã hoặc đồ lớn có ký tự không dùng được"
            continue
        raw_items = raw.get("items") if isinstance(raw.get("items"), list) else []
        entry["items_total"] = len(raw_items)
        if (module, label) in existing:
            entry["reason"] = "đã có hộp cùng tên"
            continue
        rows, seen_pos, qty_cut = [], set(), 0
        for it in raw_items[:60]:
            oid = pad_id(it.get("object_id", "")) if isinstance(it, dict) else None
            pos = it.get("pos") if isinstance(it, dict) else None
            if oid is None or isinstance(pos, bool) or not isinstance(pos, int) or not 1 <= pos <= count or pos in seen_pos:
                entry["items_skipped"].append({"object_id": oid or "?", "reason": "dòng không hợp lệ"})
                continue
            if oid not in live:
                entry["items_skipped"].append({"object_id": oid, "reason": "không còn trong kho"})
                continue
            if oid in holders:
                entry["items_skipped"].append({"object_id": oid, "reason": f"đã nằm trong {holders[oid]}"})
                continue
            seen_pos.add(pos)
            holders[oid] = f"{module} / {label}"                      # later boxes in this same file cannot take it again
            taken_now.add(oid)
            q = it.get("qty")
            want = q if isinstance(q, int) and not isinstance(q, bool) else 1
            q = max(1, min(max_qty, live[oid].stock, want))
            qty_cut += q != want
            src = it.get("source") if it.get("source") in _SOURCES else "import"
            rows.append((pos, oid, q, 1 if it.get("locked") is True else 0, src))
        entry["items_added"] = len(rows)
        complete = len(rows) == count
        status = "approved" if raw.get("status") == "approved" and complete else "draft"
        entry["final_status"] = status
        pushed = raw.get("pushed") if isinstance(raw.get("pushed"), dict) else None
        sm_id = _text(pushed.get("sm_box_id"), 64) if pushed else ""
        restore_push = bool(sm_id) and status == "approved"
        entry["pushed_restored"] = restore_push
        cats = raw.get("categories") if isinstance(raw.get("categories"), list) else []
        cats = [_text(c, 60) for c in cats[:200] if _text(c, 60)]
        entry["result"] = "added"
        notes = [] if complete else [f"mới có {len(rows)}/{count} món nên vào ở trạng thái Nháp"]
        if qty_cut:
            notes.append(f"{qty_cut} món đổi số lượng cho vừa tồn kho hoặc mức tối đa")
        entry["reason"] = "; ".join(notes)
        existing.add((module, label))
        if dry_run:
            continue
        now = time.time()
        try:
            with tx(conn):
                cur = conn.execute(
                    "INSERT INTO boxes(module,label,theme,categories,code,anchor,count,status,message,owner,created_at,updated_at,day) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
                    (module, label, _text(raw.get("theme"), 300), json.dumps(cats), _text(raw.get("code"), 40), _text(raw.get("anchor"), 120),
                     count, status, note if status == "draft" else "", _text(raw.get("owner"), 40), now, now, vn_day(now)))
                bid = cur.fetchone()[0]
                conn.executemany("INSERT INTO box_items(box_id,pos,object_id,qty,locked,source) VALUES(?,?,?,?,?,?)",
                                 [(bid, pos, oid, q, locked, src) for pos, oid, q, locked, src in rows])
                if restore_push:
                    conn.execute("INSERT INTO sm_push(box_id, sm_box_id, pushed_at) VALUES(?,?,?)", (bid, sm_id, now))
        except IntegrityError:                                         # someone took an object a moment ago: this box is left out whole
            entry.update(result="skipped", reason="một món vừa bị hộp khác lấy, hộp này được bỏ qua", items_added=0, pushed_restored=False)
    # IDs already used in the sheet: skipped when a box holds them (now or from this file), when already listed, or repeated in the file
    in_boxes = {r[0] for r in conn.execute("SELECT object_id FROM box_items")} | taken_now
    already = {r[0]: r["added_at"] for r in conn.execute("SELECT object_id, added_at FROM external_used")}
    fresh, later, skipped, seen = [], [], 0, set()
    for u in used:
        oid = pad_id(u.get("object_id", "")) if isinstance(u, dict) else None
        if oid is None or oid in seen:
            skipped += 1
            continue
        seen.add(oid)
        at = u.get("added_at")
        # only a day that can be real counts (2000 or later, not in the future); anything else means "no day known", which never expires
        at = float(at) if isinstance(at, (int, float)) and not isinstance(at, bool) and 946684800 <= at < time.time() + 86400 else 0.0
        if oid in already:                                           # a later day in the backup refreshes it, as pasting with a day does
            if at > 0 and (already[oid] <= 0 or at > already[oid]):
                later.append((at, _text(u.get("note"), 60), oid))
            else:
                skipped += 1
        elif oid in in_boxes:
            skipped += 1
        else:
            fresh.append((oid, _text(u.get("note"), 60), at))
    if not dry_run and (fresh or later):
        with tx(conn):
            conn.executemany("INSERT INTO external_used(object_id, note, added_at) VALUES(?,?,?) ON CONFLICT (object_id) DO NOTHING", fresh)
            conn.executemany("UPDATE external_used SET added_at=?, note=COALESCE(NULLIF(?, ''), note) WHERE object_id=?", later)
    # automation switches: only those that were never changed here, so a restore does not undo someone's choice
    stored = {r[0] for r in conn.execute("SELECT k FROM automations")}
    applied, kept = [], []
    for sw in switches:
        key = _text(sw.get("key"), 40) if isinstance(sw, dict) else ""
        if key not in automation.META or key in stored:
            kept.append(key or "?")
            continue
        cfg = sw.get("config") if isinstance(sw.get("config"), dict) else {}
        cfg = {k: v for k, v in cfg.items() if k in automation.META[key]["limits"]}
        try:
            if not dry_run:
                automation.set_switch(conn, key, bool(sw.get("enabled")), cfg or None)
            applied.append(key)
        except automation.BadSetting:
            kept.append(key)
    return {"dry_run": dry_run, "boxes": out,
            "totals": {"boxes_added": sum(1 for e in out if e["result"] == "added"), "boxes_skipped": sum(1 for e in out if e["result"] != "added"),
                       "items_added": sum(e["items_added"] for e in out), "items_skipped": sum(len(e["items_skipped"]) for e in out)},
            "used": {"added": len(fresh), "skipped": skipped, "refreshed": len(later)}, "automations": {"applied": applied, "kept": kept}}


# ----------------------------------------------------------------------------- import boxes from a CSV
CSV_MAX_ROWS = 20000
CSV_MAX_BOXES = 500
CSV_BOX_MAX_ITEMS = 60
_CSV_HEADERS = {                        # header names that mean each column (compared lower case, spaces, dashes and underscores as one space)
    "module": ("module", "tab", "mo dun", "mô đun"),
    "box": ("box", "hop", "hộp", "box name", "label"),
    "object_id": ("object id", "id", "ma do", "mã đồ"),                   # the exact name "object_id" is taken first, see _csv_columns
    "stt": ("stt", "pos", "position", "#"),
    "qty": ("qty", "quantity", "so luong", "số lượng"),
    "code": ("code", "section", "ma section", "mã section"),
    "anchor": ("anchor", "boi canh", "bối cảnh", "large object"),
}
_CSV_DIGITS = re.compile(r"[0-9]{1,9}")                                  # ASCII only: str.isdigit() also accepts characters int() cannot read
_CSV_ID = re.compile(r"[0-9]{1,6}")                                      # an object ID is plain digits; "4702.0" or "12 34" is not one


def _csv_norm(name: str) -> str:
    return re.sub(r"[\s_-]+", " ", name.replace("\ufeff", "").replace("\x00", "").strip().lower())


def _csv_columns(header: list[str]) -> dict[str, int]:
    """Column of each field. The exact field name wins over looser spellings, so a file that has both "id" and "object_id" uses object_id."""
    names = [_csv_norm(h) for h in header]
    cols: dict[str, int] = {}
    for key, aliases in _CSV_HEADERS.items():
        exact = _csv_norm(key)
        if exact in names:
            cols[key] = names.index(exact)
            continue
        for alias in aliases:
            if _csv_norm(alias) in names:
                cols[key] = names.index(_csv_norm(alias))
                break
    return cols


def _csv_module(v: str) -> str:
    return v.upper() if re.fullmatch(r"m\d+", v, re.IGNORECASE) else v          # m8 -> M8, like the new-box form; other tab names as typed


def boxes_from_csv(text: str, size_for) -> tuple[dict, list[dict], int]:
    """Read /api/export.csv (or a hand-made file with the same columns) into the structure import_backup takes. Every box comes in as a
    draft with its items marked 'import'. Returns (data, bad_rows, rows_read). size_for(label) is the box size for that stage.
    Rows that cannot be used are listed with the line they start on; a box with no usable row is not created."""
    import csv
    import io
    text = text.lstrip("\ufeff").replace("\x00", "")
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    if not first:
        raise Invalid("Tệp CSV trống. Hãy chọn một tệp có dữ liệu.")
    delim = max(",;\t", key=first.count)                             # the sheet may save with ; or a tab
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)    # newline="" lets the reader see CR, LF and CRLF endings itself
    too_long = Invalid("Có ô trong tệp CSV dài quá mức hoặc tệp bị lỗi định dạng. Hãy mở tệp, kiểm tra các ô rồi chọn lại.")
    last = 0
    try:
        header = None
        for r in reader:
            last = reader.line_num
            if any(c.strip() for c in r):
                header = r
                break
        if header is None:
            raise Invalid("Tệp CSV trống. Hãy chọn một tệp có dữ liệu.")
        cols = _csv_columns(header)
        missing = [k for k in ("module", "box", "object_id") if k not in cols]
        if missing:
            raise Invalid("Tệp CSV cần có các cột module, box và object_id (có thể thêm stt, qty, code, anchor). "
                          f"Chưa thấy cột: {', '.join(missing)}. Hãy thêm cột đó vào dòng đầu tiên rồi chọn lại tệp.")
        cell = lambda row, key: row[cols[key]].strip() if key in cols and cols[key] < len(row) else ""
        boxes: dict[tuple[str, str], dict] = {}
        bad: list[dict] = []
        rows_read = 0

        def refuse(line: int, why: str, oid: str = "") -> None:
            bad.append({"line": line, "object_id": oid, "reason": why})

        while True:
            try:
                row = next(reader)
            except StopIteration:
                break
            line, last = last + 1, reader.line_num                   # the line this record starts on (a quoted cell may span several lines)
            if not any(c.strip() for c in row):
                continue
            rows_read += 1
            if rows_read > CSV_MAX_ROWS:
                raise Invalid(f"Tệp CSV có hơn {CSV_MAX_ROWS} dòng. Hãy tách thành các tệp nhỏ hơn.")
            module, label = _csv_module(cell(row, "module")), cell(row, "box")
            if not module or not label:
                refuse(line, "thiếu Module hoặc tên hộp")
                continue
            raw_id = cell(row, "object_id")
            if not _CSV_ID.fullmatch(raw_id):
                refuse(line, "ID không hợp lệ (cần 1 đến 6 chữ số)", raw_id[:20])
                continue
            oid = raw_id.zfill(6)
            box = boxes.get((module, label)) or {"module": module, "label": label, "status": "draft", "code": "", "anchor": "", "items": [],
                                                  "_ids": set(), "_pos": set(), "_next": 1}
            stt_raw = cell(row, "stt")
            if stt_raw:
                if not _CSV_DIGITS.fullmatch(stt_raw) or not 1 <= int(stt_raw) <= CSV_BOX_MAX_ITEMS:
                    refuse(line, f"STT phải từ 1 đến {CSV_BOX_MAX_ITEMS}", oid)
                    continue
                pos = int(stt_raw)
            else:
                pos = box["_next"]
                while pos in box["_pos"]:
                    pos += 1
                if pos > CSV_BOX_MAX_ITEMS:
                    refuse(line, f"hộp này đã đủ {CSV_BOX_MAX_ITEMS} món", oid)
                    continue
            qty_raw = cell(row, "qty")
            if qty_raw and (not _CSV_DIGITS.fullmatch(qty_raw) or int(qty_raw) < 1):
                refuse(line, "số lượng phải là số nguyên từ 1 trở lên", oid)
                continue
            if pos in box["_pos"]:
                refuse(line, f"STT {pos} bị lặp trong hộp này", oid)
                continue
            if oid in box["_ids"]:
                refuse(line, "ID bị lặp trong hộp này", oid)
                continue
            box["_ids"].add(oid)
            box["_pos"].add(pos)
            box["_next"] = max(box["_next"], pos + 1)
            box["items"].append({"pos": pos, "object_id": oid, "qty": int(qty_raw) if qty_raw else 1, "source": "import"})
            box["code"] = box["code"] or cell(row, "code")
            box["anchor"] = box["anchor"] or cell(row, "anchor")
            boxes[(module, label)] = box                                # only a box with at least one usable row is kept
    except csv.Error:
        raise too_long
    if len(boxes) > CSV_MAX_BOXES:
        raise Invalid(f"Tệp CSV có hơn {CSV_MAX_BOXES} hộp. Hãy tách thành các tệp nhỏ hơn.")
    out = []
    for box in boxes.values():
        for k in ("_ids", "_pos", "_next"):
            box.pop(k)
        box["count"] = min(60, max([size_for(box["label"])] + [i["pos"] for i in box["items"]]))
        out.append(box)
    return {"format": BACKUP_FORMAT, "version": BACKUP_VERSION, "boxes": out, "used": [], "automations": []}, bad, rows_read


def import_boxes_csv(conn, text: str, catalog: Catalog, max_qty: int, size_for, dry_run: bool = True) -> dict:
    """Same rules as a backup restore (nothing overwritten, taken or unknown objects skipped, one write per box) but every box
    comes in as a draft to be checked and approved by a person. Rows that could not be read are listed with their line number."""
    data, bad, rows = boxes_from_csv(text, size_for)
    report = import_backup(conn, data, catalog, max_qty, dry_run, note="Nhập từ tệp CSV, hãy xem lại rồi duyệt")
    report["csv"] = {"rows": rows, "bad_rows": bad[:200], "bad_total": len(bad)}
    return report

AGE_EDGES = (7, 14, 30, 60, 90)         # days: the limits of the age bands, and the day counts the "what if" table offers


def sm_ages(conn, catalog: Catalog, now: float | None = None) -> dict:
    """How old the SM boxes that hold objects are, and what each choice of days for the reuse switch would do, so the number can be
    picked with the figures in sight. Boxes this app made in SM are left out (they hold nothing through SM) and so are boxes with no
    object. A box whose date cannot be read is counted apart and never counts as old.

    `bands` splits the boxes by age (older than `from` days, up to `to` days; `to` None = no upper limit). `options` has one row per
    day count: how many boxes would stop holding (older than that many days), how many objects would be free in total, and how many
    more than with the switch off. The rule is the same one taken_ids() applies: an object stays taken when this app's own box holds it,
    when the sheet list holds it with no day or a day inside the limit, or when any SM box holding it is recent or has no readable date."""
    from . import automation
    now = time.time() if now is None else now
    sw = automation.switches(conn)["reuse_old"]
    ours = {str(r[0]) for r in conn.execute("SELECT sm_box_id FROM sm_push")}
    dates: dict[str, float | None] = {}                       # each distinct date text is parsed once, not once per object row
    box_when: dict[str, float | None] = {}
    newest: dict[str, float] = {}                             # object id -> the newest box holding it (inf when a holder has no readable date)
    for r in conn.execute("SELECT i.object_id, b.sm_box_id, b.created_at FROM sm_box_items i JOIN sm_boxes b ON b.sm_box_id = i.sm_box_id"):
        if r["sm_box_id"] in ours:
            continue
        if r["created_at"] not in dates:
            dates[r["created_at"]] = when_of(r["created_at"])
        when = dates[r["created_at"]]
        box_when[r["sm_box_id"]] = when
        newest[r["object_id"]] = max(newest.get(r["object_id"], -math.inf), math.inf if when is None else when)
    app_taken = {r[0] for r in conn.execute("SELECT object_id FROM box_items")}
    sheet = {r[0]: r[1] for r in conn.execute("SELECT object_id, added_at FROM external_used")}
    usable = set(catalog.items())
    free_off = len(usable - (app_taken | set(sheet) | set(newest)))               # switch off: everything ever used stays held

    counts = [0] * (len(AGE_EDGES) + 1)
    for when in box_when.values():
        if when is None:
            continue
        i = 0
        while i < len(AGE_EDGES) and when < now - AGE_EDGES[i] * 86400:             # older than this edge: look at the next band
            i += 1
        counts[i] += 1
    bands = [{"from": AGE_EDGES[i - 1] if i else 0, "to": AGE_EDGES[i] if i < len(AGE_EDGES) else None, "boxes": counts[i]}
             for i in range(len(counts))]

    options = []
    for days in sorted({*AGE_EDGES, sw["config"]["days"]}):
        cutoff = now - days * 86400
        taken = app_taken | {o for o, added in sheet.items() if added <= 0 or added >= cutoff} | {o for o, w in newest.items() if w >= cutoff}
        free = len(usable - taken)
        options.append({"days": days, "current": sw["enabled"] and days == sw["config"]["days"],
                        "old_boxes": sum(1 for w in box_when.values() if w is not None and w < cutoff),
                        "free_objects": free, "extra_free": free - free_off})
    return {"boxes": len(box_when), "undated": sum(1 for w in box_when.values() if w is None), "bands": bands, "options": options,
            "free_without_reuse": free_off}


def _shared_object(catalog: Catalog, oid: str, boxes: list[dict]) -> dict:
    """One object as the shared-items view draws it: SM's English name, the Vietnamese one, slot, picture, and the units the boxes hold
    against the units that exist. `boxes` = {title, status, day, units} of the SM boxes holding it. `total` and `free` are left out when SM
    never said how many exist, so the page can say "giữ N cái" without a meter. The units that exist are `qty`, never `avail`: a unit that is
    borrowed out is not free, but it is still there."""
    seen = catalog.lookup(oid)
    out = {"id": oid, "name": (seen.vi or seen.en) if seen else "", "en": seen.en if seen else "", "vi": seen.vi if seen else "",
           "slot": seen.slot if seen else "", "img": seen.img if seen else "", "img_exp": catalog.image_expiry(seen.img) if seen and seen.img else None,
           "detail": images.detail_url(oid), "units": sum(b["units"] for b in boxes), "holders": boxes}
    if seen and seen.qty is not None:
        out["total"] = seen.qty
        if seen.avail is not None:
            out["free"] = seen.avail
    return out


def sm_report(conn, catalog: Catalog, ages: bool = False) -> dict:
    """What the last read of SM says, for the people who plan boxes: how many boxes and objects SM holds, this app's own unpushed
    boxes that contain an object an SM box holds (conflicts), and objects that two or more SM boxes hold at once (doubles).
    With the reuse switch on, boxes made before the cutoff hold nothing; `reuse` says how many boxes and objects that frees.
    Each object in `doubles` and in a conflict also carries what the shared-items view draws (_shared_object): names, slot, picture, the units
    the boxes hold and the units that exist, and `holders` (title, status, day, units per box). `boxes` stays the plain list of titles.
    `ages` adds the age bands and the "what if" table (sm_ages); it costs a few more queries, so only the page that shows it asks."""
    info = sm_boxes_info(conn)
    cutoff = reuse_cutoff(conn)
    held = sm_held(conn, cutoff)
    sheet = external_binding(conn, cutoff)
    cat = catalog.items()
    ours = {str(r[0]) for r in conn.execute("SELECT sm_box_id FROM sm_push")}          # boxes this app made in SM: they never held anything through SM
    by_obj: dict[str, list[dict]] = {}                                                  # every box that holds an object (ours too): the doubles
    theirs: dict[str, list[dict]] = {}                                                  # the same without ours: what a conflict is measured against
    old_boxes, undated, old_objects = set(), set(), set()
    for r in conn.execute("SELECT i.object_id, i.qty, b.sm_box_id, b.title, b.status, b.status_label, b.created_at FROM sm_box_items i "
                          "JOIN sm_boxes b ON b.sm_box_id = i.sm_box_id ORDER BY i.object_id, b.created_at, b.sm_box_id"):
        when = when_of(r["created_at"])
        if r["sm_box_id"] not in ours:
            if when is None:
                undated.add(r["sm_box_id"])
            elif cutoff is not None and when < cutoff:
                old_boxes.add(r["sm_box_id"])
                old_objects.add(r["object_id"])
                continue                                                     # an old box holds nothing, so it makes no double
        box = {"title": r["title"] or r["sm_box_id"], "status": r["status_label"] or r["status"],
               "day": vn_day(when) if when is not None else "", "units": r["qty"]}
        by_obj.setdefault(r["object_id"], []).append(box)
        if r["sm_box_id"] not in ours:
            theirs.setdefault(r["object_id"], []).append(box)
    doubles = [{**_shared_object(catalog, oid, bs), "boxes": [b["title"] for b in bs]}
               for oid, bs in itertools.islice(((o, bs) for o, bs in by_obj.items() if len(bs) > 1), 200)]
    pushed = {r[0] for r in conn.execute("SELECT box_id FROM sm_push")}
    conflicts = []
    for b in conn.execute("SELECT id, module, label, status FROM boxes ORDER BY module, label").fetchall():
        if b["id"] in pushed:
            continue
        clash = [{**_shared_object(catalog, i["object_id"], theirs.get(i["object_id"], [])), "sheet": i["object_id"] in sheet,
                  "sm_box": (held[i["object_id"]]["title"] or held[i["object_id"]]["sm_box_id"]) if i["object_id"] in held else "danh sách đã dùng trong bảng tính"}
                 for i in box_items(conn, b["id"]) if i["object_id"] in held or i["object_id"] in sheet]
        if clash:
            conflicts.append({"box_id": b["id"], "box": f"{b['module']} / {b['label']}", "status": b["status"], "objects": clash})
    from . import automation
    sw = automation.switches(conn)["reuse_old"]
    reuse = {"on": cutoff is not None, "days": sw["config"]["days"], "old_boxes": len(old_boxes), "undated_boxes": len(undated),
             "freed_objects": len({o for o in old_objects if o in cat} - taken_ids(conn, cutoff))}      # only objects nothing else still holds
    out = {"synced_at": info["synced_at"] if info else None, "boxes": info["active"] if info else 0, "objects_held": len(held),
           "doubles": doubles, "conflicts": conflicts, "reuse": reuse}
    if ages:
        out["ages"] = sm_ages(conn, catalog)
    return out

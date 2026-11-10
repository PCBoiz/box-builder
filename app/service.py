"""Domain operations on boxes and the claim ledger. Every write runs inside one BEGIN IMMEDIATE
transaction, so "check it is free, then take it" can never interleave with another user."""
import json
import re
import time

from . import images
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


def taken_ids(conn) -> set[str]:
    a = {r[0] for r in conn.execute("SELECT object_id FROM box_items")}
    b = {r[0] for r in conn.execute("SELECT object_id FROM external_used")}
    return a | b | set(sm_held(conn))


def sm_held(conn) -> dict[str, dict]:
    """Objects that a box in SM holds, as last read from SM: object id -> {sm_box_id, title, status}.
    Boxes this app pushed itself are left out: their objects already belong to our own box."""
    ours = {str(r[0]) for r in conn.execute("SELECT sm_box_id FROM sm_push")}
    out: dict[str, dict] = {}
    for r in conn.execute(
        "SELECT i.object_id, b.sm_box_id, b.title, b.status, b.status_label FROM sm_box_items i "
        "JOIN sm_boxes b ON b.sm_box_id = i.sm_box_id ORDER BY b.created_at, b.sm_box_id"
    ):
        if r["sm_box_id"] in ours:
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
        for bid, (meta, items) in keep.items():
            conn.execute("INSERT INTO sm_boxes(sm_box_id, title, status, status_label, created_at, seen_at) VALUES(?,?,?,?,?,?)",
                         (bid, meta["title"], meta["status"], meta["label"], meta["created"], now))
            for oid, q in items.items():
                conn.execute("INSERT INTO sm_box_items(sm_box_id, object_id, qty) VALUES(?,?,?)", (bid, oid, q))
        info = {"synced_at": now, "received": len(boxes), "active": len(keep), "objects_held": len(held),
                "ignored_ended": ignored_ended, "skipped_invalid": invalid}
        conn.execute("INSERT INTO catalog_snapshot(k, v) VALUES('sm_boxes', ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v",
                     (json.dumps(info),))
    return info


def get_box(conn, box_id: int) -> object:
    row = conn.execute("SELECT * FROM boxes WHERE id=?", (box_id,)).fetchone()
    if row is None:
        raise NotFound(f"Hộp {box_id} không tồn tại (có thể đã bị huỷ).")
    return row


def box_items(conn, box_id: int) -> list[object]:
    return conn.execute("SELECT * FROM box_items WHERE box_id=? ORDER BY pos", (box_id,)).fetchall()


def owner_of(conn, oid: str) -> str:
    r = conn.execute(
        "SELECT b.module, b.label FROM box_items i JOIN boxes b ON b.id=i.box_id WHERE i.object_id=?", (oid,)
    ).fetchone()
    if r:
        return f"{r['module']} / {r['label']}"
    if conn.execute("SELECT 1 FROM external_used WHERE object_id=?", (oid,)).fetchone():
        return "danh sách đã dùng trong bảng tính"
    held = sm_held(conn).get(oid)
    if held:
        return f"hộp SM {held['title'] or held['sm_box_id']}"
    return ""


def state(conn, catalog: Catalog) -> dict:
    # Read the revision BEFORE the data. If a write lands while we read, the page holds data that is at least as new as
    # its revision, so the next poll sees a different number and refetches. (Reading it last could label old data with a
    # newer revision and leave the page stale until something else changed.)
    rev_first = rev(conn)
    cat = catalog.items()
    boxes = []
    pushed = {r["box_id"]: {"sm_box_id": r["sm_box_id"], "at": r["pushed_at"]}
              for r in conn.execute("SELECT * FROM sm_push")}
    for b in conn.execute("SELECT * FROM boxes ORDER BY module, label, id").fetchall():
        items = []
        for it in box_items(conn, b["id"]):
            c = cat.get(it["object_id"])
            items.append({
                "pos": it["pos"], "id": it["object_id"], "qty": it["qty"],
                "locked": bool(it["locked"]), "source": it["source"],
                "name": c.name if c else "(không còn trong kho)",
                "vi": c.vi if c else "", "category": c.category if c else "",
                "slot": c.slot if c else "", "stock": c.stock if c else 0,
                "img": c.img if c else "", "detail": images.detail_url(it["object_id"]),
            })
        boxes.append({
            "id": b["id"], "module": b["module"], "label": b["label"], "theme": b["theme"],
            "code": b["code"], "anchor": b["anchor"], "title": taskbox_title(b["code"] or b["module"], b["anchor"], b["label"]),
            "categories": json.loads(b["categories"]), "count": b["count"], "status": b["status"],
            "message": b["message"], "owner": b["owner"], "updated_at": b["updated_at"], "items": items,
            "pushed": pushed.get(b["id"]),
        })
    in_boxes = conn.execute("SELECT COUNT(*) FROM box_items").fetchone()[0]
    ext_ids = {r[0] for r in conn.execute("SELECT object_id FROM external_used")}
    external = len(ext_ids & set(cat))            # only IDs that are usable objects change the free count
    used = [{"id": r["object_id"], "note": r["note"]}
            for r in conn.execute("SELECT * FROM external_used ORDER BY object_id")]
    claimed = {r[0] for r in conn.execute("SELECT object_id FROM box_items")}
    return {
        "rev": rev_first,
        "catalog": catalog.info(),
        "stats": {"catalog": len(cat), "in_boxes": in_boxes, "external": external, "in_sm": len(set(sm_held(conn)) & set(cat)),
                  "free": max(0, len(cat) - len(taken_ids(conn) & set(cat))), "boxes": len(boxes),
                  "gone": len(claimed - set(cat))},     # claimed objects SM no longer lists as available
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
            "builder": {"box_id": b["id"], "module": b["module"], "label": b["label"], "owner": b["owner"],
                        "status": b["status"], "pushed": b["pushed"]},
        })
    return out


def sm_queue(conn, catalog: Catalog, max_qty: int) -> dict:
    """Approved boxes that are not in SM yet, split into READY and BLOCKED (with the reasons).
    Pure read: this only decides what the userscript is shown; it never touches SM."""
    st = state(conn, catalog)
    pushed_titles = {b["title"] for b in st["boxes"] if b["pushed"]}
    ready, blocked, seen = [], [], set()
    cat = catalog.items()
    held = sm_held(conn)
    for tb in taskboxes(conn, catalog, ("approved",), pending_only=True, st=st):
        reasons = []
        for c in tb["contents"]:
            it = cat.get(c["object_id"])
            if it is None:
                reasons.append(f"{c['object_id']} không còn tồn kho ở SM")
            elif c["object_id"] in held:
                h = held[c["object_id"]]
                reasons.append(f"{c['object_id']} đã nằm trong hộp SM {h['title'] or h['sm_box_id']}")
            elif c["quantity"] > min(max_qty, it.stock) or c["quantity"] < 1:
                reasons.append(f"{c['object_id']}: số lượng {c['quantity']} vượt mức cho phép (tối đa {min(max_qty, it.stock)})")
        if not tb["title"].strip():
            reasons.append("thiếu tiêu đề")
        elif tb["title"] in pushed_titles or tb["title"] in seen:
            reasons.append(f"tiêu đề \"{tb['title']}\" trùng với một hộp khác")
        seen.add(tb["title"])
        if reasons:
            blocked.append({"box_id": tb["builder"]["box_id"], "title": tb["title"], "reasons": reasons})
        else:
            ready.append(tb)
    return {"ready": ready, "blocked": blocked}


# ----------------------------------------------------------------------------- object browser
OBJECT_STATUSES = ("free", "draft", "approved", "pushed", "external", "sm")


def _holders(conn) -> tuple[dict, set]:
    """object_id -> {box_id, box, status, pushed}; plus the set of IDs marked as used in the workbook."""
    held = {}
    pushed = {r["box_id"] for r in conn.execute("SELECT box_id FROM sm_push")}
    for r in conn.execute("SELECT i.object_id, b.id, b.module, b.label, b.status FROM box_items i JOIN boxes b ON b.id=i.box_id"):
        status = "pushed" if r["id"] in pushed else ("approved" if r["status"] == "approved" else "draft")
        held[r["object_id"]] = {"box_id": r["id"], "box": f"{r['module']} / {r['label']}", "status": status}
    external = {r[0] for r in conn.execute("SELECT object_id FROM external_used")}
    return held, external


def object_status(oid: str, held: dict, external: set, sm: dict | None = None) -> tuple[str, str]:
    if oid in held:
        return held[oid]["status"], held[oid]["box"]
    if oid in external:
        return "external", "bảng tính"
    if sm and oid in sm:
        return "sm", f"hộp SM {sm[oid]['title'] or sm[oid]['sm_box_id']}"
    return "free", ""


def object_rows(conn, catalog: Catalog, q: str = "", category: str = "", status: str = "", sort: str = "id") -> tuple[list[dict], dict]:
    """Every catalog object that matches the filters, in the requested order, plus a count per status
    (the counts ignore the status filter so the filter chips can show all of them)."""
    from .catalog import fold
    held, external = _holders(conn)
    sm = sm_held(conn)
    words = [w for w in fold(q).split() if w]
    rows, counts = [], {s: 0 for s in OBJECT_STATUSES}
    for it in catalog.items().values():
        if category and (it.category or "(chưa phân loại)") != category:
            continue
        if words and not all(w in it.blob or w in it.id for w in words):
            continue
        st, holder = object_status(it.id, held, external, sm)
        counts[st] += 1
        if status and st != status:
            continue
        rows.append({"id": it.id, "name": it.name, "vi": it.vi, "category": it.category or "(chưa phân loại)",
                     "slot": it.slot, "stock": it.stock, "status": st, "holder": holder,
                     "img": it.img, "detail": images.detail_url(it.id)})
    keys = {"id": lambda r: r["id"], "name": lambda r: (r["vi"] or r["name"]).lower(), "category": lambda r: (r["category"], r["id"])}
    if sort in ("stock", "-stock"):
        rows.sort(key=lambda r: (r["stock"], r["id"]), reverse=sort == "-stock")
    else:
        rows.sort(key=keys.get(sort, keys["id"]))
    return rows, counts


def objects_view(conn, catalog: Catalog, q: str = "", category: str = "", status: str = "", sort: str = "id",
                 page: int = 1, per: int = 50) -> dict:
    """The catalog with a status per object (free / in a draft / approved / pushed to SM / used in the workbook)."""
    rows, counts = object_rows(conn, catalog, q, category, status, sort)
    per = max(1, min(200, per))
    page = max(1, page)
    total = len(rows)
    return {"total": total, "page": page, "per": per, "pages": max(1, -(-total // per)),
            "counts": counts, "items": rows[(page - 1) * per: page * per]}


def coverage(conn, catalog: Catalog) -> list[dict]:
    """Per category: how many usable objects exist, how many are still free, how many are taken."""
    held, external = _holders(conn)
    sm = sm_held(conn)
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
            ("Việc chọn đồ chưa hoàn tất (máy chủ vừa khởi động lại?). Bấm Chọn lại.", now, stale_gen))
        cur = conn.execute(
            "DELETE FROM boxes WHERE status IN ('draft','error') AND updated_at < ?", (stale_draft,))
        return cur.rowcount


# ----------------------------------------------------------------------------- boxes
def create_box(conn, module: str, label: str, theme: str, categories: list[str], count: int, owner: str,
               code: str = "", anchor: str = "") -> int:
    module, label = module.strip(), label.strip()
    if not module or not label:
        raise Invalid("Cần nhập Module và tên hộp (ví dụ M8 và Box A1).")
    if not 1 <= count <= 60:
        raise Invalid("Số đồ trong hộp phải từ 1 đến 60.")
    now = time.time()
    try:
        with tx(conn):
            cur = conn.execute(
                "INSERT INTO boxes(module,label,theme,categories,code,anchor,count,status,owner,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,'generating',?,?,?) RETURNING id",
                (module, label, theme.strip(), json.dumps(categories), code.strip(), anchor.strip(), count,
                 owner.strip(), now, now))
            return cur.fetchone()[0]
    except IntegrityError:
        raise Conflict(f"{module} / {label} đã tồn tại. Hãy mở hộp đó bên dưới hoặc đặt tên khác.")


def set_status(conn, box_id: int, status: str, message: str = "") -> None:
    with tx(conn):
        conn.execute("UPDATE boxes SET status=?, message=?, updated_at=? WHERE id=?",
                     (status, message, time.time(), box_id))


def _not_pushed(conn, box_id: int) -> None:
    r = conn.execute("SELECT sm_box_id FROM sm_push WHERE box_id=?", (box_id,)).fetchone()
    if r:
        raise Conflict(f"Hộp này đã được đẩy lên SM (Box ID {r['sm_box_id']}). Hãy sửa trong SM, "
                       "hoặc nhờ quản trị viên gỡ dấu \"đã đẩy\" nếu thật sự cần làm lại.")


def delete_box(conn, box_id: int) -> None:
    with tx(conn):
        _not_pushed(conn, box_id)
        conn.execute("DELETE FROM boxes WHERE id=?", (box_id,))


def mark_pushed(conn, box_id: int, sm_box_id: str) -> None:
    """Record that an approved box now exists in SM. Idempotent; refuses boxes that are not approved."""
    sm_box_id = str(sm_box_id).strip()
    if not sm_box_id:
        raise Invalid("Thiếu Box ID của SM.")
    with tx(conn):
        b = get_box(conn, box_id)
        if b["status"] != "approved":
            raise Conflict("Chỉ hộp đã duyệt mới được đánh dấu là đã đẩy lên SM.")
        conn.execute("DELETE FROM sm_push WHERE box_id=?", (box_id,))
        conn.execute("INSERT INTO sm_push(box_id, sm_box_id, pushed_at) VALUES(?,?,?)", (box_id, sm_box_id, time.time()))


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
            raise Invalid(f"Hộp mới có {n}/{b['count']} món. Hãy điền đủ trước khi duyệt.")
        conn.execute("UPDATE boxes SET status='approved', message='', updated_at=? WHERE id=?", (time.time(), box_id))


def reopen(conn, box_id: int) -> None:
    with tx(conn):
        get_box(conn, box_id)
        _not_pushed(conn, box_id)
        conn.execute("UPDATE boxes SET status='draft', updated_at=? WHERE id=?", (time.time(), box_id))


def _editable(conn, box_id: int) -> object:
    b = get_box(conn, box_id)
    if b["status"] == "approved":
        raise Conflict("Hộp này đã duyệt. Bấm Mở lại nếu cần sửa.")
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
        raise Invalid(f"'{object_id}' không phải là đồ có thể dùng trong kho.")
    with tx(conn):
        b = _editable(conn, box_id)
        taken_by = owner_of(conn, item.id)
        if taken_by:
            raise Conflict(f"{item.id} ({item.name}) đã nằm trong {taken_by}.")
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
    existing = {(r["module"], r["label"]) for r in conn.execute("SELECT module, label FROM boxes")}
    for i, sp in enumerate(specs, 1):
        module, label = str(sp.get("module", "")).strip(), str(sp.get("label", "")).strip()
        count = sp.get("count") or default_count
        if not module or not label:
            problems.append(f"Hộp {i}: thiếu Module hoặc tên hộp.")
        elif (module, label) in names:
            problems.append(f"Hộp {i}: {module} / {label} bị trùng trong danh sách.")
        elif (module, label) in existing:
            problems.append(f"Hộp {i}: {module} / {label} đã tồn tại.")
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
def import_used(conn, text: str, note: str, catalog: Catalog) -> dict:
    ids, bad = [], []
    for tok in re.split(r"[\s,;]+", text.strip()):
        if not tok:
            continue
        oid = pad_id(tok)
        (ids if oid else bad).append(oid or tok)
    added, in_box = 0, []
    with tx(conn):
        in_boxes = {r[0] for r in conn.execute("SELECT object_id FROM box_items")}
        for oid in dict.fromkeys(ids):
            if oid in in_boxes:
                in_box.append(oid)
                continue
            cur = conn.execute("INSERT INTO external_used(object_id, note) VALUES(?,?) ON CONFLICT (object_id) DO NOTHING",
                               (oid, note.strip()))
            added += cur.rowcount
    unknown = [o for o in ids if catalog.get(o) is None]
    return {"added": added, "already_in_a_box": in_box, "not_in_catalog": unknown, "unreadable": bad}


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
    used = [{"object_id": r["object_id"], "note": r["note"]} for r in conn.execute("SELECT * FROM external_used ORDER BY object_id")]
    switches = [{"key": k, "enabled": v["enabled"], "config": v["config"]} for k, v in automation.switches(conn).items()]
    return {"format": BACKUP_FORMAT, "version": BACKUP_VERSION, "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "boxes": boxes, "used": used, "automations": switches}


def _text(v, limit: int) -> str:
    return str(v).strip()[:limit] if isinstance(v, (str, int, float)) and not isinstance(v, bool) else ""


def import_backup(conn, data, catalog: Catalog, max_qty: int, dry_run: bool = True) -> dict:
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
    for r in conn.execute("SELECT object_id FROM external_used"):
        holders.setdefault(r[0], "danh sách đã dùng trong bảng tính")
    for oid, h in sm_held(conn).items():
        holders.setdefault(oid, f"hộp SM {h['title'] or h['sm_box_id']}")
    existing = {(r["module"], r["label"]) for r in conn.execute("SELECT module, label FROM boxes")}
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
        if (module, label) in existing:
            entry["reason"] = "đã có hộp cùng tên"
            continue
        rows, seen_pos = [], set()
        raw_items = raw.get("items") if isinstance(raw.get("items"), list) else []
        entry["items_total"] = len(raw_items)
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
            q = max(1, min(max_qty, live[oid].stock, q if isinstance(q, int) and not isinstance(q, bool) else 1))
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
        entry["reason"] = "" if complete else f"mới có {len(rows)}/{count} món nên vào ở trạng thái Nháp"
        existing.add((module, label))
        if dry_run:
            continue
        now = time.time()
        try:
            with tx(conn):
                cur = conn.execute(
                    "INSERT INTO boxes(module,label,theme,categories,code,anchor,count,status,message,owner,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?) RETURNING id",
                    (module, label, _text(raw.get("theme"), 300), json.dumps(cats), _text(raw.get("code"), 40), _text(raw.get("anchor"), 120),
                     count, status, "Khôi phục từ bản sao lưu" if status == "draft" else "", _text(raw.get("owner"), 40), now, now))
                bid = cur.fetchone()[0]
                for pos, oid, q, locked, src in rows:
                    conn.execute("INSERT INTO box_items(box_id,pos,object_id,qty,locked,source) VALUES(?,?,?,?,?,?)", (bid, pos, oid, q, locked, src))
                if restore_push:
                    conn.execute("INSERT INTO sm_push(box_id, sm_box_id, pushed_at) VALUES(?,?,?)", (bid, sm_id, now))
        except IntegrityError:                                         # someone took an object a moment ago: this box is left out whole
            entry.update(result="skipped", reason="một món vừa bị hộp khác lấy, hộp này được bỏ qua", items_added=0, pushed_restored=False)
    # IDs already used in the sheet: skipped when a box holds them (now or from this file), when already listed, or repeated in the file
    in_boxes = {r[0] for r in conn.execute("SELECT object_id FROM box_items")} | taken_now
    already = {r[0] for r in conn.execute("SELECT object_id FROM external_used")}
    fresh, skipped, seen = [], 0, set()
    for u in used:
        oid = pad_id(u.get("object_id", "")) if isinstance(u, dict) else None
        if oid is None or oid in in_boxes or oid in already or oid in seen:
            skipped += 1
            continue
        seen.add(oid)
        fresh.append((oid, _text(u.get("note"), 60)))
    if not dry_run and fresh:
        with tx(conn):
            for oid, note in fresh:
                conn.execute("INSERT INTO external_used(object_id, note) VALUES(?,?) ON CONFLICT (object_id) DO NOTHING", (oid, note))
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
            "used": {"added": len(fresh), "skipped": skipped}, "automations": {"applied": applied, "kept": kept}}


def sm_report(conn, catalog: Catalog) -> dict:
    """What the last read of SM says, for the people who plan boxes: how many boxes and objects SM holds, this app's own unpushed
    boxes that contain an object an SM box holds (conflicts), and objects that two or more SM boxes hold at once (doubles)."""
    info = sm_boxes_info(conn)
    held = sm_held(conn)
    cat = catalog.items()
    name = lambda oid: (cat[oid].vi or cat[oid].name) if oid in cat else ""
    by_obj: dict[str, list[dict]] = {}
    for r in conn.execute("SELECT i.object_id, b.sm_box_id, b.title FROM sm_box_items i JOIN sm_boxes b ON b.sm_box_id = i.sm_box_id "
                          "ORDER BY i.object_id, b.created_at, b.sm_box_id"):
        by_obj.setdefault(r["object_id"], []).append({"sm_box_id": r["sm_box_id"], "title": r["title"] or r["sm_box_id"]})
    doubles = [{"id": oid, "name": name(oid), "boxes": [b["title"] for b in bs]} for oid, bs in by_obj.items() if len(bs) > 1]
    pushed = {r[0] for r in conn.execute("SELECT box_id FROM sm_push")}
    conflicts = []
    for b in conn.execute("SELECT id, module, label, status FROM boxes ORDER BY module, label").fetchall():
        if b["id"] in pushed:
            continue
        clash = [{"id": i["object_id"], "name": name(i["object_id"]), "sm_box": held[i["object_id"]]["title"] or held[i["object_id"]]["sm_box_id"]}
                 for i in box_items(conn, b["id"]) if i["object_id"] in held]
        if clash:
            conflicts.append({"box_id": b["id"], "box": f"{b['module']} / {b['label']}", "status": b["status"], "objects": clash})
    return {"synced_at": info["synced_at"] if info else None, "boxes": info["active"] if info else 0, "objects_held": len(held),
            "doubles": doubles[:200], "conflicts": conflicts}


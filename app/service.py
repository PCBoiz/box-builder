"""Domain operations on boxes and the claim ledger. Every write runs inside one BEGIN IMMEDIATE
transaction, so "check it is free, then take it" can never interleave with another user."""
import json
import re
import time

from .catalog import Catalog, Item, display_name, pad_id
from .db import IntegrityError, rev, tx


class Conflict(Exception):
    """The request clashes with existing data (name taken, object taken, ...). Maps to HTTP 409."""


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


# ----------------------------------------------------------------------------- reads
def taken_ids(conn) -> set[str]:
    a = {r[0] for r in conn.execute("SELECT object_id FROM box_items")}
    b = {r[0] for r in conn.execute("SELECT object_id FROM external_used")}
    return a | b


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
    return ""


def state(conn, catalog: Catalog) -> dict:
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
        "rev": rev(conn),
        "catalog": catalog.info(),
        "stats": {"catalog": len(cat), "in_boxes": in_boxes, "external": external,
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


def taskboxes(conn, catalog: Catalog, statuses=("approved",), pending_only: bool = False) -> list[dict]:
    """Boxes in exactly the shape the userscript's sheet parser produces, so it can use them directly."""
    out = []
    for b in state(conn, catalog)["boxes"]:
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

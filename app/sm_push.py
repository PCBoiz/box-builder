"""Push the ready boxes to a FAKE SM (app/sm_client.py). This is how the whole chain is developed and tried without touching the
real SM. There is no real client here and no setting that turns one on: real boxes are created by the userscript in the
person's own signed-in SM tab, create-only, after an explicit confirm click."""
from datetime import datetime, timedelta, timezone

from . import automation, service
from .catalog import Catalog
from .sm_client import SMClient, SMError


def build_payload(tb: dict, options: dict) -> dict:
    """The fields SM's create call takes, from a ready box in the userscript's shape (service.taskboxes)."""
    payload = {
        "title": tb["title"],
        "note": f"Created by Box builder #{tb['builder']['box_id']}",
        "contents": [{"object_id": c["object_id"], "quantity": c["quantity"]} for c in tb["contents"]],
        "borrow_now": bool(options.get("borrow_now")),
    }
    if tb.get("anchor_object_id"):
        payload["anchor_object_id"] = tb["anchor_object_id"]
    who = str(options.get("assignee") or "").strip()[:60]
    if who:
        payload["assignee"] = who
    if payload["borrow_now"]:
        payload["borrower"] = who                  # SM needs someone to borrow for; an empty name makes the fake refuse it
    return payload


def push_ready(conn, catalog: Catalog, client: SMClient, max_qty: int, options: dict | None = None) -> dict:
    """Create every ready box in `client` and record it as pushed. One box failing never stops the others, and running again
    creates nothing twice because a pushed box is no longer ready."""
    options = options or {}
    queue = service.sm_queue(conn, catalog, max_qty)
    results = []
    for tb in queue["ready"]:
        box_id, title = tb["builder"]["box_id"], tb["title"]
        try:
            sm_id = client.create_box(build_payload(tb, options))
        except SMError as e:
            results.append({"box_id": box_id, "title": title, "ok": False, "code": e.code, "error": e.message})
            continue
        try:
            service.mark_pushed(conn, box_id, sm_id)
        except (service.Conflict, service.Invalid, service.NotFound) as e:
            results.append({"box_id": box_id, "title": title, "ok": False, "code": "record",
                            "error": f"Đã tạo trong SM giả (mã {sm_id}) nhưng không ghi nhận được: {e}"})
            continue
        results.append({"box_id": box_id, "title": title, "ok": True, "sm_box_id": sm_id})
    for b in queue["blocked"]:
        results.append({"box_id": b["box_id"], "title": b["title"], "ok": False, "code": "blocked", "error": "Bị chặn: " + "; ".join(b["reasons"][:2])})
    ok = sum(1 for r in results if r["ok"])
    if results:
        automation.log(conn, "sm_push", f"Lên SM giả: {ok} hộp tạo được, {len(results) - ok} không tạo được")
    return {"created": ok, "failed": len(results) - ok, "results": results}


# ----------------------------------------------------------------------------- the other steps: assign, borrow, floor, hand over, return old boxes
def _each(client: SMClient, box_ids: list[str], move) -> list[dict]:
    """Run `move(box_id)` on each box. A move SM refuses is reported with its reason, never hidden, and never stops the rest."""
    out = []
    for bid in dict.fromkeys(str(b) for b in box_ids):
        try:
            view = move(bid)
            out.append({"box_id": bid, "title": view["title"], "ok": True, "status": view["status"], "status_label": view["status_label"]})
        except SMError as e:
            out.append({"box_id": bid, "title": "", "ok": False, "code": e.code, "error": e.message})
    return out


def assign(client: SMClient, box_ids: list[str], person: str) -> list[dict]:
    return _each(client, box_ids, lambda b: client.transition(b, "assigned", assignee=person, note="Giao từ Box builder"))


def borrow(client: SMClient, box_ids: list[str], person: str) -> list[dict]:
    return _each(client, box_ids, lambda b: client.transition(b, "collected", borrower=person, note="Mượn từ Box builder"))


def to_floor(client: SMClient, box_ids: list[str]) -> list[dict]:
    return _each(client, box_ids, lambda b: client.transition(b, "on_floor", note="Đưa ra sàn từ Box builder"))


def hand_over(client: SMClient, box_ids: list[str], receiver: str) -> list[dict]:
    return _each(client, box_ids, lambda b: client.hand_over(b, receiver, note="Bàn giao từ Box builder"))


def _age_days(created_at: str, now: datetime) -> float | None:
    try:
        made = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    if made.tzinfo is None:
        made = made.replace(tzinfo=timezone.utc)
    return (now - made) / timedelta(days=1)


def old_on_the_floor(client: SMClient, days: int, now: datetime | None = None) -> list[dict]:
    """Boxes On the floor that were created at least `days` days ago (the process: return the old A3 and B3 boxes at the start of the day)."""
    now = now or datetime.now(timezone.utc)
    out = []
    for b in client.list_boxes("on_floor"):
        age = _age_days(b["created_at"], now)
        if age is not None and age >= days:
            out.append({"box_id": b["box_id"], "title": b["title"], "created_at": b["created_at"], "days_old": round(age, 1)})
    return out


def return_old(client: SMClient, days: int, returned_by: str, confirm: bool = False, now: datetime | None = None) -> dict:
    """Return the old boxes. Without confirm nothing happens: the answer lists what would be returned."""
    old = old_on_the_floor(client, days, now)
    if not confirm:
        return {"preview": True, "boxes": old, "results": []}
    results = _each(client, [b["box_id"] for b in old], lambda b: client.transition(b, "deactivated", returned_by=returned_by, note="Trả từ Box builder"))
    return {"preview": False, "boxes": old, "results": results}


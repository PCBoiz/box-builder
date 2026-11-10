"""What the admin looks at: how the process is going, not a queue of work. People make and approve their own boxes; the admin sees who has how many
boxes in which state and where something looks wrong (a draft about to expire, a box whose pick failed, an approved box that has not reached SM)."""
import time

from fastapi import APIRouter, Depends

from . import db, service

SOON_HOURS = 2            # a draft with less than this left is about to be deleted
STUCK_HOURS = 4           # an approved box that is still not in SM after this long (a box lives one day, so 24 hours would never fire)


def install(app, s, cat) -> None:
    router = APIRouter()

    def conn_dep():
        c = db.connect(s.db_path)
        try:
            yield c
        finally:
            c.close()

    @router.get("/api/admin/overview")
    def overview(c=Depends(conn_dep)):
        now = time.time()
        names = {r["person_id"]: r["name"] for r in c.execute("SELECT person_id, name FROM people")}
        owners: dict[str, dict] = {}
        soon, errors, stuck = [], [], []
        totals = {"generating": 0, "draft": 0, "approved": 0, "pushed": 0, "error": 0}
        for b in service.state(c, cat)["boxes"]:
            state = "pushed" if b["pushed"] else b["status"]
            totals[state] = totals.get(state, 0) + 1
            key = b["owner"] or ""
            o = owners.setdefault(key, {"owner": key, "name": names.get(key, ""), "boxes": 0, "generating": 0, "draft": 0, "approved": 0, "pushed": 0, "error": 0})
            o["boxes"] += 1
            o[state] = o.get(state, 0) + 1
            who = names.get(key) or key or "chưa rõ người làm"
            label = f"{b['module']} {b['label']}"
            hours_left = (b["updated_at"] + s.draft_ttl_hours * 3600 - now) / 3600
            if b["status"] in ("draft", "error") and hours_left < SOON_HOURS:
                soon.append(f"{label} ({who})")
            if b["status"] == "error":
                errors.append(f"{label} ({who})")
            if b["status"] == "approved" and not b["pushed"] and (now - b["updated_at"]) / 3600 > STUCK_HOURS:
                stuck.append(f"{label} ({who})")
        problems = []
        if soon:
            problems.append({"level": "warn", "msg": f"{len(soon)} hộp sắp tự mất vì chưa ai duyệt hoặc sửa (còn dưới {SOON_HOURS} giờ): " + ", ".join(soon[:6]) + (" ..." if len(soon) > 6 else "")})
        if errors:
            problems.append({"level": "bad", "msg": f"{len(errors)} hộp chưa chọn được đồ: " + ", ".join(errors[:6]) + (" ..." if len(errors) > 6 else "")})
        if stuck:
            problems.append({"level": "warn", "msg": f"{len(stuck)} hộp đã duyệt hơn {STUCK_HOURS} giờ mà chưa lên SM: " + ", ".join(stuck[:6]) +
                             (" ..." if len(stuck) > 6 else "") + ". Mở SM, bấm Quét dữ liệu ở khung Box builder, rồi Đồng bộ."})
        rows = sorted(owners.values(), key=lambda o: (-o["boxes"], o["owner"]))
        return {"totals": totals, "owners": rows, "problems": problems, "draft_ttl_hours": s.draft_ttl_hours}

    app.include_router(router)

"""The page and the API for the people who make boxes. They create boxes, look at them, change an object, approve their own box (and take the
approval back while it is not in SM yet), copy the list and print it. The admin page only watches the process and can step in.

A person sees and changes only the boxes that carry their own company ID as owner. With login switched off (ADMIN_IDS empty) there is no
identity, so the page works on every box, as the admin page does today."""
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from . import auth, db, picker, printview, service

STATUS_TEXT = {"generating": "Đang chọn đồ", "draft": "Chờ bạn duyệt", "approved": "Đã duyệt", "error": "Chưa chọn được đồ"}


class PlanUnit(BaseModel):
    code: str = Field(default="", max_length=40)
    anchor: str = Field(default="", max_length=200)
    shift: str = Field(default="full", max_length=20)


class PlanBody(BaseModel):
    module: str = Field(default="", max_length=40)
    units: list[PlanUnit] = Field(default=[], max_length=60)


class MyBox(BaseModel):
    module: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=1, max_length=40)
    theme: str = Field(default="", max_length=300)
    count: int | None = None
    code: str = Field(default="", max_length=40)
    anchor: str = Field(default="", max_length=120)


def install(app, s, cat, engine, count_for) -> None:
    router = APIRouter()

    def conn_dep():
        c = db.connect(s.db_path)
        try:
            yield c
        finally:
            c.close()

    def me(request: Request) -> tuple[str, bool]:
        """(the owner ID this person's boxes carry, whether the list is limited to them)"""
        p = getattr(request.state, "person", None)
        return (p.id, True) if p else ("", False)

    def own(c, request: Request, box_id: int):
        row = service.get_box(c, box_id)
        owner, limited = me(request)
        if limited and row["owner"] != owner:
            raise service.NotFound("Không thấy hộp này. Có thể nó đã tự mất vì quá lâu chưa duyệt.")        # the same words as a missing box: no hint it exists
        return row

    def plain_message(b: dict) -> str:
        """The picker's own notes can carry technical detail (which attempt, what the model sent). A person is told only what they can act on."""
        m = b["message"] or ""
        if b["status"] != "error":
            return m
        return m if m.startswith("Kho chỉ còn") else "Chưa chọn được đủ món."

    def shown_label(b: dict) -> str:
        """The kind of box as the person chose it ("Box B2"): the stored name carries the section code in front so it stays unique, the page shows the code on its own line."""
        code, label = b["code"], b["label"]
        return label[len(code) + 1:] if code and label.startswith(code + " ") and len(label) > len(code) + 1 else label

    def shape(b: dict) -> dict:
        return {"id": b["id"], "module": b["module"], "label": shown_label(b), "code": b["code"], "anchor": service.split_anchor(b["anchor"])[1] or b["anchor"],
                "theme": b["theme"], "title": b["title"], "status": b["status"], "status_text": STATUS_TEXT.get(b["status"], b["status"]),
                "message": plain_message(b), "count": b["count"], "pushed": bool(b["pushed"]), "updated_at": b["updated_at"], "day": b["day"],
                "items": [{"pos": i["pos"], "id": i["id"], "qty": i["qty"], "name": i["name"], "vi": i["vi"], "category": i["category"], "slot": i["slot"],
                           "filled": i["source"] == "fill"}                         # put in by the app when the AI could not (the person should look at these)
                          for i in b["items"]]}

    @router.get("/dung")
    def page():
        return auth.static_page("dung.html")

    @router.get("/api/my/summary")
    def summary(request: Request, rev: int = -1, c=Depends(conn_dep)):
        """Pass the `rev` of the last answer: when nothing has changed since, the reply is just {"rev", "unchanged": true}, which is cheap,
        so many people can keep the page open. A draft nobody touches for `ttl_hours` is deleted by service.expire (the same rule as the admin page),
        so every box carries `updated_at` and the answer carries `now` and `ttl_hours` for the page to show how long a draft has left."""
        service.housekeeping(c, s.draft_ttl_hours, s.box_done_days)
        if rev == db.rev(c):
            return {"rev": rev, "unchanged": True}
        owner, limited = me(request)
        st = service.state(c, cat)
        person = request.state.person
        return {"rev": st["rev"], "now": st["now"], "today": service.vn_day(st["now"]), "ttl_hours": s.draft_ttl_hours,
                "person": person.public() if person else None, "open_mode": not limited,
                "free_objects": st["stats"]["free"],
                "stages": [{"label": f"Box {k}", "count": n} for k, n in s.stage_counts.items()],
                "boxes": [shape(b) for b in st["boxes"] if not limited or b["owner"] == owner]}

    @router.post("/api/my/boxes", status_code=201)
    def create(b: MyBox, request: Request, c=Depends(conn_dep)):
        owner, _ = me(request)
        label, code = b.label.strip(), b.code.strip()
        if len(code) > 20:
            raise service.Invalid("Mã section dài quá. Ví dụ M8-02.")
        if code and code.lower() not in label.lower():
            label = f"{code} {label}"             # a box name is used once per module: the section code keeps two sections apart. It still ends with the kind (B2), which the sheet reads.
        try:
            box_id = service.create_box(c, b.module, label, b.theme, [], count_for(label, b.count), owner, code, b.anchor)
        except service.Conflict:
            raise service.Conflict(f"{b.module.strip()} / {label} đã có, có thể của người khác. "
                                   "Hộp của bạn nằm ở bước 2. Nếu không thấy ở đó, điền Mã section riêng (ví dụ M8-02) rồi tạo lại.")
        auth.audit(c, auth.who(request), "box_create", box_id, f"{b.module} / {b.label}", auth.client_ip(request))
        picker.start_generation(s, cat, engine, box_id, auto=True)       # auto: the team's switch "Tự duyệt hộp" approves a full box for the person when it is on
        return {"id": box_id}

    def editable(c, request: Request, box_id: int):
        row = own(c, request, box_id)
        if row["status"] == "approved":
            raise service.Conflict("Hộp này đã duyệt nên chưa sửa được. Bấm Bỏ duyệt nếu cần sửa lại.")
        return row

    @router.get("/api/my/plan")
    def get_plan(request: Request, c=Depends(conn_dep)):
        """Today's plan (Vietnam day), the report for the end of the day, and the boxes to hand back this morning."""
        service.housekeeping(c, s.draft_ttl_hours, s.box_done_days)
        return service.plan_view(c, me(request)[0], service.vn_day(time.time()))

    @router.put("/api/my/plan")
    def put_plan(body: PlanBody, request: Request, c=Depends(conn_dep)):
        owner, _ = me(request)
        today = service.vn_day(time.time())
        service.plan_save(c, owner, today, body.module, [u.model_dump() for u in body.units])
        return service.plan_view(c, owner, today)

    @router.get("/api/my/recent")
    def recent(module: str = "", code: str = "", c=Depends(conn_dep)):
        """The themes of the boxes made lately for this module (and section): to avoid doing the same scene twice."""
        if not module.strip():
            return {"items": []}
        return {"items": service.recent_boxes(c, module[:40], code[:40])}

    @router.post("/api/my/boxes/{box_id}/approve")
    def approve(box_id: int, request: Request, c=Depends(conn_dep)):
        """The person approves their own box. After that the team's script can push it to SM."""
        row = own(c, request, box_id)
        if row["status"] == "approved":
            return {"ok": True}
        if row["status"] != "draft":
            raise service.Conflict("Hộp này chưa chọn xong đồ nên chưa duyệt được.")
        service.approve(c, box_id)                                             # refuses a box that is not full, in plain words
        auth.audit(c, auth.who(request), "box_approve", box_id, f"{row['module']} / {row['label']}", auth.client_ip(request))
        return {"ok": True}

    @router.post("/api/my/boxes/{box_id}/reopen")
    def reopen(box_id: int, request: Request, c=Depends(conn_dep)):
        """Take the approval back to change the box. Refused once the box is in SM (the service says so)."""
        row = own(c, request, box_id)
        if row["status"] != "approved":
            raise service.Conflict("Hộp này chưa được duyệt.")
        service.reopen(c, box_id)
        auth.audit(c, auth.who(request), "box_reopen", box_id, f"{row['module']} / {row['label']}", auth.client_ip(request))
        return {"ok": True}

    @router.post("/api/my/boxes/{box_id}/regenerate")
    def regenerate(box_id: int, request: Request, c=Depends(conn_dep)):
        editable(c, request, box_id)
        released = service.release_unlocked(c, box_id)
        picker.start_generation(s, cat, engine, box_id, avoid=set(released))
        return {"released": len(released)}

    @router.post("/api/my/boxes/{box_id}/fill")
    def fill(box_id: int, request: Request, c=Depends(conn_dep)):
        """Pick again only the empty places (a box whose pick stopped half way keeps what it has)."""
        editable(c, request, box_id)
        service.release_unlocked(c, box_id, [])                  # status -> generating, nothing released
        picker.start_generation(s, cat, engine, box_id)
        return {"ok": True}

    @router.post("/api/my/boxes/{box_id}/items/{pos}/replace")
    def replace(box_id: int, pos: int, request: Request, c=Depends(conn_dep)):
        editable(c, request, box_id)
        row = c.execute("SELECT locked FROM box_items WHERE box_id=? AND pos=?", (box_id, pos)).fetchone()
        if row and row["locked"]:
            raise service.Conflict("Món này đang được khoá, hãy nhờ admin mở khoá.")
        released = service.release_unlocked(c, box_id, [pos])
        picker.start_generation(s, cat, engine, box_id, avoid=set(released), positions=[pos])
        return {"released": len(released)}

    @router.delete("/api/my/boxes/{box_id}")
    def delete(box_id: int, request: Request, c=Depends(conn_dep)):
        row = editable(c, request, box_id)
        if row["status"] == "generating":
            raise service.Conflict("Hộp đang chọn đồ. Đợi một chút rồi xoá.")
        service.delete_box(c, box_id)
        auth.audit(c, auth.who(request), "box_delete", box_id, f"{row['module']} / {row['label']}", auth.client_ip(request))
        return {"ok": True}

    @router.get("/my/print")
    def print_mine(request: Request, ids: str = "", c=Depends(conn_dep)):
        """The paper a supporter takes to the shelves, for this person's boxes (draft or approved) only."""
        owner, limited = me(request)
        id_set = {int(x) for x in ids.split(",") if x.strip().isdigit()}
        today = service.vn_day(time.time())
        boxes = []
        for b in service.state(c, cat)["boxes"]:
            if b["status"] not in ("draft", "approved") or not b["items"] or (limited and b["owner"] != owner) or (id_set and b["id"] not in id_set):
                continue
            if not id_set and b["day"] and b["day"] != today:
                continue                                    # the paper of the day: yesterday's box is already in SM, its objects were taken yesterday
            anchor_id, name = service.split_anchor(b["anchor"])
            if anchor_id:
                item = cat.get(anchor_id)
                name = item.name if item else ""
            boxes.append({**b, "anchor": name, "label": shown_label(b)})
        html = printview.render_pick_list(boxes, generated_at=service.vn_now_text(), combined=True)
        return HTMLResponse(html, headers={"Cache-Control": "no-store"})

    app.include_router(router)

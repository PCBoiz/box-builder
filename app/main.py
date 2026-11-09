"""FastAPI app: JSON API + the single static page. Run: uvicorn app.main:app --host 0.0.0.0 --port 8080"""
import contextlib
import csv
import hmac
import io
import os
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, picker, service
from .catalog import Catalog
from .settings import Settings

STATIC = os.path.join(os.path.dirname(__file__), "static")


class NewBox(BaseModel):
    module: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=1, max_length=40)
    theme: str = Field(default="", max_length=300)
    categories: list[str] = []
    count: int | None = None
    owner: str = Field(default="", max_length=40)
    code: str = Field(default="", max_length=40)       # SM section code, for example M8-01 (defaults to the module)
    anchor: str = Field(default="", max_length=120)    # large object name, or its 5-6 digit anchor Object ID


class PushedBody(BaseModel):
    sm_box_id: str


class SyncBody(BaseModel):
    objects: list[dict] = Field(max_length=20000)     # rows exactly as the userscript compacts them from /api/objects


class Qty(BaseModel):
    qty: int


class Lock(BaseModel):
    locked: bool


class AddItem(BaseModel):
    object_id: str
    qty: int = 1


class UsedIds(BaseModel):
    text: str
    note: str = ""


def create_app(settings: Settings | None = None, llm=None, catalog: Catalog | None = None) -> FastAPI:
    s = settings or Settings.from_env()
    db.init(s.db_path)
    cat = catalog or Catalog(s.catalog_path)
    engine = llm or picker.make_llm(s)
    cat.sectors = s.sector_list
    boot = db.connect(s.db_path)                  # a live snapshot saved earlier survives restarts (it lives in the database)
    try:
        snap = service.load_snapshot(boot)
    finally:
        boot.close()
    if snap:
        cat.set_live(*snap)

    @contextlib.asynccontextmanager
    async def lifespan(_):
        yield
        db.close_pools()                      # hand Neon its connections back politely

    app = FastAPI(title="Box builder", lifespan=lifespan)

    def conn_dep():
        c = db.connect(s.db_path)
        try:
            yield c
        finally:
            c.close()

    def auth(x_access_code: str = Header(default=""), access_code: str = ""):
        # header for the page's fetch calls, query string for plain download links
        if s.access_code and s.access_code not in (x_access_code, access_code):
            raise HTTPException(401, "Cần nhập mã truy cập")

    @app.exception_handler(service.Conflict)
    async def _conflict(_: Request, e):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(service.Invalid)
    async def _invalid(_: Request, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(service.NotFound)
    async def _notfound(_: Request, e):
        return JSONResponse({"detail": str(e)}, status_code=404)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ------------------------------------------------------------------ public bits
    @app.get("/")
    def index():
        return FileResponse(os.path.join(STATIC, "index.html"))

    @app.get("/api/config")
    def config():
        return {"llm": engine.name, "model": s.deepseek_model if engine.name == "deepseek" else "mock",
                "default_count": s.default_count, "max_qty": s.max_qty,
                "needs_code": bool(s.access_code), "draft_ttl_hours": s.draft_ttl_hours}

    # ------------------------------------------------------------------ everything below needs the access code (if set)
    @app.get("/api/state", dependencies=[Depends(auth)])
    def get_state(rev: int = -1, c=Depends(conn_dep)):
        service.expire(c, s.draft_ttl_hours)
        if rev == db.rev(c):
            return {"rev": rev, "unchanged": True}
        return service.state(c, cat)

    @app.post("/api/catalog/sync")
    def catalog_sync(body: SyncBody, x_sync_token: str = Header(default=""), c=Depends(conn_dep)):
        """The userscript (signed in to Scenario Manager) pushes the current object list here.
        Protected by its own secret, never by the shared access code, because it rewrites the catalog."""
        if not s.sync_token:
            raise HTTPException(503, "Máy chủ chưa đặt SYNC_TOKEN nên chưa nhận đồng bộ kho.")
        if not hmac.compare_digest(x_sync_token.encode(), s.sync_token.encode()):
            raise HTTPException(401, "Sai mã đồng bộ.")
        if len(body.objects) < 50:                # a short list means the fetch half-failed; never wipe the catalog with it
            raise service.Invalid(f"Danh sách chỉ có {len(body.objects)} món, quá ngắn nên bị bỏ qua.")
        rows = [{"id": o.get("id"), "name": o.get("name"), "vi": o.get("vi"), "type": o.get("type"),
                 "status": o.get("status"), "avail": o.get("avail"), "qty": o.get("qty"), "loc": o.get("loc"),
                 "sector": o.get("sector")} for o in body.objects]
        now = time.time()
        summary = cat.set_live(rows, now)
        # Only keep what this app is allowed to use. Objects from other sectors are counted, then dropped, never stored.
        service.save_snapshot(c, [r for r in rows if cat.allowed_sector(r)], now)
        claimed = {r[0] for r in c.execute("SELECT object_id FROM box_items")}
        summary["claimed_now_unavailable"] = sorted(claimed - set(cat.items()))
        return summary

    @app.get("/api/catalog", dependencies=[Depends(auth)])
    def catalog_info():
        return cat.info()

    @app.get("/api/categories", dependencies=[Depends(auth)])
    def categories():
        return [{"name": n, "count": k} for n, k in cat.categories()]

    @app.get("/api/search", dependencies=[Depends(auth)])
    def search(q: str, c=Depends(conn_dep)):
        taken = service.taken_ids(c)
        return [{"id": i.id, "name": i.name, "vi": i.vi, "category": i.category, "stock": i.stock}
                for i in cat.search(q, exclude=taken, limit=15)]

    @app.post("/api/boxes", dependencies=[Depends(auth)], status_code=201)
    def create_box(b: NewBox, c=Depends(conn_dep)):
        known = {n for n, _ in cat.categories()}
        bad = [x for x in b.categories if x not in known]
        if bad:
            raise service.Invalid(f"Nhóm không tồn tại: {', '.join(bad)}")
        box_id = service.create_box(c, b.module, b.label, b.theme, b.categories, b.count or s.default_count, b.owner,
                                    b.code, b.anchor)
        picker.start_generation(s, cat, engine, box_id)
        return {"id": box_id}

    @app.post("/api/boxes/{box_id}/regenerate", dependencies=[Depends(auth)])
    def regenerate(box_id: int, c=Depends(conn_dep)):
        released = service.release_unlocked(c, box_id)
        picker.start_generation(s, cat, engine, box_id, avoid=set(released))
        return {"released": len(released)}

    @app.post("/api/boxes/{box_id}/items/{pos}/replace", dependencies=[Depends(auth)])
    def replace(box_id: int, pos: int, c=Depends(conn_dep)):
        row = c.execute("SELECT locked FROM box_items WHERE box_id=? AND pos=?", (box_id, pos)).fetchone()
        if row and row["locked"]:
            raise service.Conflict("Hãy mở khoá món này trước khi thay.")
        released = service.release_unlocked(c, box_id, [pos])
        picker.start_generation(s, cat, engine, box_id, avoid=set(released), positions=[pos])
        return {"released": len(released)}

    @app.post("/api/boxes/{box_id}/items", dependencies=[Depends(auth)], status_code=201)
    def add_item(box_id: int, a: AddItem, c=Depends(conn_dep)):
        return {"pos": service.add_item(c, box_id, a.object_id, a.qty, cat, s.max_qty)}

    @app.post("/api/boxes/{box_id}/items/{pos}/lock", dependencies=[Depends(auth)])
    def lock(box_id: int, pos: int, body: Lock, c=Depends(conn_dep)):
        service.set_locked(c, box_id, pos, body.locked)
        return {"ok": True}

    @app.patch("/api/boxes/{box_id}/items/{pos}", dependencies=[Depends(auth)])
    def patch_item(box_id: int, pos: int, body: Qty, c=Depends(conn_dep)):
        service.set_qty(c, box_id, pos, body.qty, cat, s.max_qty)
        return {"ok": True}

    @app.delete("/api/boxes/{box_id}/items/{pos}", dependencies=[Depends(auth)])
    def del_item(box_id: int, pos: int, c=Depends(conn_dep)):
        service.remove_item(c, box_id, pos)
        return {"ok": True}

    @app.post("/api/boxes/{box_id}/fill", dependencies=[Depends(auth)])
    def fill(box_id: int, c=Depends(conn_dep)):
        """Fill the empty slots (after removing objects by hand) without touching the rest."""
        service.release_unlocked(c, box_id, [])       # status -> generating, nothing released
        picker.start_generation(s, cat, engine, box_id)
        return {"ok": True}

    @app.post("/api/boxes/{box_id}/approve", dependencies=[Depends(auth)])
    def approve(box_id: int, c=Depends(conn_dep)):
        service.approve(c, box_id)
        return {"ok": True}

    @app.post("/api/boxes/{box_id}/reopen", dependencies=[Depends(auth)])
    def reopen(box_id: int, c=Depends(conn_dep)):
        service.reopen(c, box_id)
        return {"ok": True}

    @app.delete("/api/boxes/{box_id}", dependencies=[Depends(auth)])
    def delete_box(box_id: int, c=Depends(conn_dep)):
        service.delete_box(c, box_id)
        return {"ok": True}

    @app.post("/api/used", dependencies=[Depends(auth)])
    def import_used(body: UsedIds, c=Depends(conn_dep)):
        return service.import_used(c, body.text, body.note, cat)

    @app.delete("/api/used/{oid}", dependencies=[Depends(auth)])
    def remove_used(oid: str, c=Depends(conn_dep)):
        service.remove_used(c, oid)
        return {"ok": True}

    @app.get("/api/taskboxes", dependencies=[Depends(auth)])
    def taskboxes(status: str = "approved", pending: bool = False, c=Depends(conn_dep)):
        """Boxes in the SM TaskBox userscript's own format. Default: approved boxes only.
        pending=true hides boxes that were already pushed to SM (what a Google Sheet script wants)."""
        wanted = tuple(x.strip() for x in status.split(",") if x.strip()) or ("approved",)
        return {"boxes": service.taskboxes(c, cat, wanted, pending_only=pending)}

    def sync_auth(x_sync_token: str):
        if not s.sync_token:
            raise HTTPException(503, "Máy chủ chưa đặt SYNC_TOKEN.")
        if not hmac.compare_digest(x_sync_token.encode(), s.sync_token.encode()):
            raise HTTPException(401, "Sai mã đồng bộ.")

    @app.post("/api/boxes/{box_id}/pushed")
    def box_pushed(box_id: int, body: PushedBody, x_sync_token: str = Header(default=""), c=Depends(conn_dep)):
        """The userscript reports a box it just created in SM, so it is never pushed twice. Needs the sync token
        (not the shared access code, which may be switched off)."""
        sync_auth(x_sync_token)
        service.mark_pushed(c, box_id, body.sm_box_id)
        return {"ok": True}

    @app.delete("/api/boxes/{box_id}/pushed")
    def box_unpushed(box_id: int, x_sync_token: str = Header(default=""), c=Depends(conn_dep)):
        """Admin escape hatch: forget that a box was pushed (e.g. someone deleted it in SM by hand)."""
        sync_auth(x_sync_token)
        service.unmark_pushed(c, box_id)
        return {"ok": True}

    @app.get("/api/export.csv", dependencies=[Depends(auth)])
    def export(approved_only: bool = False, c=Depends(conn_dep)):
        st = service.state(c, cat)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["module", "box", "status", "stt", "object_id", "qty", "name", "vietnamese_name", "category"])
        for b in st["boxes"]:
            if approved_only and b["status"] != "approved":
                continue
            for it in b["items"]:
                w.writerow([b["module"], b["label"], b["status"], it["pos"], it["id"], it["qty"], it["name"], it["vi"], it["category"]])
        return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=boxes.csv"})

    return app


_app = None


def __getattr__(name):            # lets `uvicorn app.main:app` build the app lazily from env
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)

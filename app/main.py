"""FastAPI app: JSON API + the single static page. Run: uvicorn app.main:app --host 0.0.0.0 --port 8080"""
import contextlib
import csv
import hashlib
import hmac
import io
import json
import os
import re
import threading
import time
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import automation, db, images, picker, planner, printview, service, sm_push
from .catalog import Catalog
from .settings import Settings
from .sm_client import STATUS_LABELS, MockSM

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
    force: bool = False                               # accept a list that leaves far fewer usable objects than the catalog has now


class SmBoxesBody(BaseModel):
    boxes: list = Field(max_length=20000)             # what the userscript read from SM; entries that are not boxes are skipped and counted
    allow_empty: bool = False


class SmPushBody(BaseModel):
    assignee: str = Field(default="", max_length=60)
    borrow_now: bool = False


class SmMoveBody(BaseModel):
    action: str = Field(max_length=20)                    # assign | borrow | floor | hand_over | return_old
    box_ids: list[str] = Field(default=[], max_length=200)
    person: str = Field(default="", max_length=60)
    days: int = Field(default=2, ge=0, le=365)            # return_old: boxes at least this many days old
    confirm: bool = False                                 # return_old only acts when this is true


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


class PlanBody(BaseModel):
    brief: str = Field(min_length=3, max_length=2000)
    module: str = Field(min_length=1, max_length=20)
    start_label: str = Field(default="Box A1", max_length=40)
    boxes: int = Field(default=4, ge=1, le=12)
    count: int | None = None
    labels: list[str] = Field(default=[], max_length=12)   # exact box names, e.g. Box B2, Box B3, Box A1 (replaces start_label and boxes)
    code: str = Field(default="", max_length=40)           # SM section code shared by the boxes, e.g. M8-02
    categories: list[str] = []                             # object categories every box may use (empty = all)


class BatchSpec(BaseModel):
    module: str = Field(min_length=1, max_length=20)
    label: str = Field(min_length=1, max_length=40)
    theme: str = Field(default="", max_length=300)
    count: int | None = None
    categories: list[str] = []
    code: str = Field(default="", max_length=40)


class BatchBody(BaseModel):
    boxes: list[BatchSpec] = Field(max_length=12)
    owner: str = Field(default="", max_length=40)
    approve: bool = False             # approve each box as soon as the model has filled it (this batch only)


class SwitchBody(BaseModel):
    enabled: bool | None = None
    config: dict | None = None


class BulkItems(BaseModel):
    object_ids: list[str] = Field(max_length=60)


def create_app(settings: Settings | None = None, llm=None, catalog: Catalog | None = None) -> FastAPI:
    s = settings or Settings.from_env()
    db.init(s.db_path)
    cat = catalog or Catalog(s.catalog_path)
    engine = llm or picker.make_llm(s)
    cat.sectors = s.sector_list
    cat.image_hosts = images.hosts_from_env(s.image_hosts)
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
        # header for the page's fetch calls, query string for plain download links (compared in constant time, like the sync token)
        if not s.access_code:
            return
        want = s.access_code.encode("utf-8", "replace")
        if not any(hmac.compare_digest(got.encode("utf-8", "replace"), want) for got in (x_access_code, access_code)):
            raise HTTPException(401, "Cần nhập mã truy cập")

    # A browser sends a plain form POST from any web page without asking first. With no access code set, that would let a page the
    # visitor opens approve, regenerate or reopen boxes here. Refuse unsafe requests that a browser marks as coming from another
    # site. The two token routes are exempt: the userscript calls them from the SM page and they carry their own secret.
    TOKEN_ROUTES = ("/api/catalog/sync", "/api/sm/boxes/sync")

    @app.middleware("http")
    async def refuse_cross_site_writes(request: Request, call_next):
        path = request.url.path
        if request.method not in ("GET", "HEAD", "OPTIONS") and path not in TOKEN_ROUTES and not (
                path.startswith("/api/boxes/") and path.endswith("/pushed")):
            site = request.headers.get("sec-fetch-site", "")
            origin = request.headers.get("origin")
            if site:                    # every current browser sends this, and it cannot be forged by a web page
                foreign = site not in ("same-origin", "none")
            else:                       # older browsers: compare the Origin the browser sent with the Host it asked for
                foreign = origin is not None and urlsplit(origin).netloc != request.headers.get("host", "")
            if foreign:
                return JSONResponse({"detail": "Yêu cầu từ một trang khác bị chặn."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(service.Conflict)
    async def _conflict(_: Request, e):
        return JSONResponse({"detail": str(e)}, status_code=409)

    @app.exception_handler(service.Invalid)
    async def _invalid(_: Request, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(automation.BadSetting)
    async def _badsetting(_: Request, e):
        return JSONResponse({"detail": str(e)}, status_code=422)

    @app.exception_handler(service.NotFound)
    async def _notfound(_: Request, e):
        return JSONResponse({"detail": str(e)}, status_code=404)

    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    # ------------------------------------------------------------------ public bits
    def static_stamp() -> str:
        """Changes whenever a script or style file changes, so a browser never mixes a new page with cached old scripts."""
        h = hashlib.sha1()
        for name in sorted(os.listdir(STATIC)):
            if name.endswith((".js", ".css")):
                st = os.stat(os.path.join(STATIC, name))
                h.update(f"{name}:{st.st_mtime_ns}:{st.st_size}".encode())
        return h.hexdigest()[:10]

    @app.get("/")
    def index():
        with open(os.path.join(STATIC, "index.html"), encoding="utf-8") as f:
            html = f.read()
        html = re.sub(r'(/static/[A-Za-z0-9_.-]+\.(?:js|css))"', rf'\1?v={static_stamp()}"', html)
        return HTMLResponse(html, headers={"Cache-Control": "no-cache"})   # the page is always checked; its assets are named by stamp

    def count_for(label: str, explicit: int | None = None) -> int:
        """How many objects a box gets: what was asked for, else the size of its stage (A1, B2, B3), else the default."""
        return explicit or s.stage_counts.get(service.stage_of(label), s.default_count)

    SCRIPT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "integrations", "box_builder_sm.user.js")
    APP_URL_OK = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$")

    def script_text(request: Request, app_url: str) -> str:
        """The SM sync tool with this app's own address written into it (and into its @connect line); it checks for newer versions here."""
        proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        origin = f"{proto}://{request.headers.get('host', request.url.netloc)}"
        target = app_url.strip().rstrip("/") or origin
        if not APP_URL_OK.match(target):
            raise service.Invalid("Tham số app phải là một địa chỉ dạng https://tên-máy hoặc http://tên-máy:cổng.")
        with open(SCRIPT_FILE, encoding="utf-8") as f:
            text = f.read()
        host = urlsplit(target).hostname
        self_url = f"{origin}/box_builder_sm.user.js" + (f"?app={target}" if app_url.strip() else "")
        text = text.replace('const DEFAULT_APP = "";', f"const DEFAULT_APP = {json.dumps(target)};", 1)
        connect = "" if f"// @connect      {host}\n" in text else f"// @connect      {host}\n"       # not twice if the file already lists it
        return text.replace("// @connect      localhost\n", f"// @connect      localhost\n{connect}// @downloadURL  {self_url}\n// @updateURL    {self_url}\n", 1)

    @app.get("/box_builder_sm.user.js")
    def sync_script(request: Request, app_url: str = Query("", alias="app")):
        """Served so Tampermonkey can offer to install it with one click. It holds no secret: the sync code is typed by the person."""
        return Response(script_text(request, app_url), media_type="text/javascript; charset=utf-8", headers={"Cache-Control": "no-cache"})

    @app.get("/box_builder_sm.txt")
    def sync_script_text(request: Request, app_url: str = Query("", alias="app")):
        """The same script as plain text, for people whose browser does not open Tampermonkey's install page: open it, copy it, paste it
        into Tampermonkey's "Create a new script". A normal text page, so no extension has to catch the link."""
        return Response(script_text(request, app_url), media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-cache"})

    fake_sm = MockSM({}, {}, clock=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())) if s.sm_push_mode == "mock" else None          # in memory only: the real SM is never reached from here
    fake_lock = threading.Lock()

    def need_fake_sm() -> MockSM:
        if fake_sm is None:
            raise service.Conflict("Chế độ thử với SM giả đang tắt. Đặt SM_PUSH_MODE=mock rồi chạy lại máy chủ để dùng.")
        return fake_sm

    @app.get("/api/config")
    def config():
        return {"llm": engine.name, "model": s.deepseek_model if engine.name == "deepseek" else "mock", "sm_push_mode": s.sm_push_mode,
                "default_count": s.default_count, "stage_counts": s.stage_counts, "max_qty": s.max_qty,
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
        # A sync that would leave almost nothing usable (for example SM sent no Vietnamese names) must not replace a good catalog.
        # The check runs on the compact rows below, so build them first.
        rows = [{"id": o.get("id"), "name": o.get("name"), "vi": o.get("vi"), "type": o.get("type"),
                 "status": o.get("status"), "avail": o.get("avail"), "qty": o.get("qty"), "loc": o.get("loc"),
                 "sector": o.get("sector"), "img": images.pick_image(o, hosts=cat.image_hosts)} for o in body.objects]
        preview = cat.preview_live(rows)
        have = len(cat.items())
        if not body.force and preview["usable"] < max(50, have // 2):
            sk = preview["skipped"]
            raise service.Invalid(
                f"Chỉ {preview['usable']} món dùng được trong {len(rows)} món nhận về (đang có {have}). "
                f"Thiếu tên tiếng Việt: {sk['no_vietnamese_name']}, hết hàng: {sk['out_of_stock']}, khác khu: {sk['other_sector']}. "
                "Kho không bị thay đổi.")
        now = time.time()
        summary = cat.set_live(rows, now)
        # Only keep what this app is allowed to use. Objects from other sectors are counted, then dropped, never stored.
        service.save_snapshot(c, [r for r in rows if cat.allowed_sector(r)], now)
        claimed = {r[0] for r in c.execute("SELECT object_id FROM box_items")}
        summary["claimed_now_unavailable"] = sorted(claimed - set(cat.items()))
        summary["healed_boxes"] = heal_boxes(c) if automation.is_on(c, "auto_heal") else []
        return summary

    def heal_boxes(c) -> list[dict]:
        """Auto-heal: swap out objects SM no longer lists, in boxes that are not in SM yet. Pushed boxes are never touched."""
        done = []
        for r in c.execute("SELECT id, module, label FROM boxes ORDER BY id").fetchall():
            gone = {i["object_id"] for i in service.box_items(c, r["id"])} - set(cat.items())
            if not gone:
                continue
            positions = service.heal_box(c, r["id"], cat)
            if positions:
                picker.start_generation(s, cat, engine, r["id"], positions=set(positions), auto=True)
                automation.log(c, "heal", f"{r['module']} / {r['label']}: thay {len(positions)} món SM không còn ({', '.join(sorted(gone)[:4])})")
                done.append({"box_id": r["id"], "replaced": len(positions)})
        return done

    @app.get("/api/catalog", dependencies=[Depends(auth)])
    def catalog_info():
        return cat.info()

    @app.get("/api/categories", dependencies=[Depends(auth)])
    def categories():
        return [{"name": n, "count": k} for n, k in cat.categories()]

    @app.get("/api/search", dependencies=[Depends(auth)])
    def search(q: str, c=Depends(conn_dep)):
        taken = service.taken_ids(c)
        return [{"id": i.id, "name": i.name, "vi": i.vi, "category": i.category, "stock": i.stock, "img": i.img,
                 "detail": images.detail_url(i.id)} for i in cat.search(q, exclude=taken, limit=15)]

    @app.post("/api/boxes", dependencies=[Depends(auth)], status_code=201)
    def create_box(b: NewBox, c=Depends(conn_dep)):
        known = {n for n, _ in cat.categories()}
        bad = [x for x in b.categories if x not in known]
        if bad:
            raise service.Invalid(f"Nhóm không tồn tại: {', '.join(bad)}")
        box_id = service.create_box(c, b.module, b.label, b.theme, b.categories, count_for(b.label, b.count), b.owner,
                                    b.code, b.anchor)
        picker.start_generation(s, cat, engine, box_id, auto=True)
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
        if pending and wanted == ("approved",):
            # what the SM userscript asks for: only boxes that passed the checks, and nothing when the queue is switched off
            if not automation.is_on(c, "sm_queue"):
                return {"boxes": [], "disabled": True}
            return {"boxes": service.sm_queue(c, cat, s.max_qty)["ready"]}
        return {"boxes": service.taskboxes(c, cat, wanted, pending_only=pending)}

    @app.get("/api/sheet-boxes", dependencies=[Depends(auth)])
    def sheet_boxes(c=Depends(conn_dep)):
        """What the Google Sheet script reads. Same shape as /api/taskboxes; empty when its switch is off."""
        if not automation.is_on(c, "sheet_export"):
            return {"boxes": [], "disabled": True}
        return {"boxes": service.taskboxes(c, cat, ("approved",))}

    # ------------------------------------------------------------------ object browser
    @app.get("/api/objects", dependencies=[Depends(auth)])
    def objects(q: str = "", category: str = "", status: str = "", sort: str = "id", page: int = 1, per: int = 50,
                c=Depends(conn_dep)):
        if status and status not in service.OBJECT_STATUSES:
            raise service.Invalid("Trạng thái không hợp lệ.")
        return service.objects_view(c, cat, q, category, status, sort, page, per)

    def csv_safe(v) -> str:
        """Text from SM goes into a spreadsheet: a cell that starts with = + - or @ would run as a formula, so mark it as text."""
        v = "" if v is None else str(v)
        return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v

    @app.get("/api/objects.csv", dependencies=[Depends(auth)])
    def objects_csv(q: str = "", category: str = "", status: str = "", sort: str = "id", c=Depends(conn_dep)):
        """The same list and filters as /api/objects, all pages, as a UTF-8 CSV (with BOM so Excel reads the accents)."""
        if status and status not in service.OBJECT_STATUSES:
            raise service.Invalid("Trạng thái không hợp lệ.")
        rows, _ = service.object_rows(c, cat, q, category, status, sort)
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["id", "name", "vietnamese_name", "category", "stock", "status", "holder", "img"])
        for r in rows:
            w.writerow([r["id"], csv_safe(r["name"]), csv_safe(r["vi"]), csv_safe(r["category"]), r["stock"], r["status"],
                        csv_safe(r["holder"]), csv_safe(r["img"])])
        return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=objects.csv"})

    # ------------------------------------------------------------------ backup and restore
    BACKUP_MAX = 10 * 1024 * 1024

    @app.get("/api/backup.json", dependencies=[Depends(auth)])
    def backup(c=Depends(conn_dep)):
        """Everything this app owns, as one JSON file: boxes and items, pushed records, used-in-sheet IDs, automation switches.
        No secrets, no catalog, no SM snapshot."""
        body = json.dumps(service.export_backup(c), ensure_ascii=False, indent=1)
        return Response(body, media_type="application/json; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=box-builder-backup-{time.strftime('%Y%m%d')}.json"})

    @app.post("/api/backup/import", dependencies=[Depends(auth)])
    async def backup_import(request: Request, dry_run: bool = True, c=Depends(conn_dep)):
        """Merge a backup in. dry_run (the default) only reports what would happen. Nothing is overwritten."""
        if int(request.headers.get("content-length") or 0) > BACKUP_MAX:
            raise HTTPException(413, "Tệp sao lưu lớn hơn 10 MB nên bị từ chối.")
        raw = await request.body()
        if len(raw) > BACKUP_MAX:
            raise HTTPException(413, "Tệp sao lưu lớn hơn 10 MB nên bị từ chối.")
        try:
            data = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            raise service.Invalid("Tệp này không phải JSON hợp lệ. Hãy chọn tệp đã tải từ nút Tải bản sao lưu.")
        report = await run_in_threadpool(service.import_backup, c, data, cat, s.max_qty, dry_run)
        if not dry_run:
            automation.log(c, "backup", f"Khôi phục: thêm {report['totals']['boxes_added']} hộp, bỏ qua {report['totals']['boxes_skipped']} hộp")
        return report

    CSV_IMPORT_MAX = 5 * 1024 * 1024

    @app.post("/api/import/boxes.csv", dependencies=[Depends(auth)])
    async def import_boxes_csv(request: Request, dry_run: bool = True, c=Depends(conn_dep)):
        """Create draft boxes from a CSV with the columns of /api/export.csv (module, box, stt, object_id, qty; optional code, anchor).
        dry_run (the default) only reports. Same rules as a restore: nothing is overwritten, taken objects are skipped and listed."""
        if int(request.headers.get("content-length") or 0) > CSV_IMPORT_MAX:                 # refused before the body is read into memory
            raise HTTPException(413, "Tệp CSV lớn hơn 5 MB nên bị từ chối. Hãy tách thành các tệp nhỏ hơn.")
        raw = await request.body()
        if len(raw) > CSV_IMPORT_MAX:
            raise HTTPException(413, "Tệp CSV lớn hơn 5 MB nên bị từ chối. Hãy tách thành các tệp nhỏ hơn.")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise service.Invalid("Không đọc được tệp. Hãy lưu tệp CSV dạng UTF-8 (trong Google Sheets: Tệp > Tải xuống > CSV).")
        report = await run_in_threadpool(service.import_boxes_csv, c, text, cat, s.max_qty, count_for, dry_run)
        if not dry_run:
            automation.log(c, "import", f"Nhập CSV: thêm {report['totals']['boxes_added']} hộp nháp, bỏ qua {report['totals']['boxes_skipped']} hộp, "
                                        f"{report['csv']['bad_total']} dòng không đọc được")
        return report

    @app.get("/print/boxes", dependencies=[Depends(auth)])
    def print_boxes(status: str = "approved", module: str = "", ids: str = "", combined: bool = True, c=Depends(conn_dep)):
        """The paper a supporter takes to the shelves: approved boxes, one page each, plus one list sorted by shelf location."""
        wanted = tuple(x.strip() for x in status.split(",") if x.strip()) or ("approved",)
        id_set = {int(x) for x in ids.split(",") if x.strip().isdigit()}
        boxes = []
        for b in service.state(c, cat)["boxes"]:
            if b["status"] not in wanted or not b["items"] or (module and b["module"] != module.strip()) or (id_set and b["id"] not in id_set):
                continue
            anchor_id, name = service.split_anchor(b["anchor"])
            if anchor_id:
                item = cat.get(anchor_id)
                name = item.name if item else ""
            boxes.append({**b, "anchor": name})
        page = printview.render_pick_list(boxes, generated_at=time.strftime("%d/%m/%Y %H:%M"), combined=combined)
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    @app.post("/api/sm/push", dependencies=[Depends(auth)])
    def sm_push_run(body: SmPushBody, c=Depends(conn_dep)):
        """Try the push on the fake SM: every ready box becomes a TaskBox there and is recorded as pushed."""
        sm = need_fake_sm()
        if not automation.is_on(c, "sm_queue"):
            raise service.Conflict("Hàng chờ đẩy lên SM đang tắt (tab Tự động hoá). Bật lại rồi thử.")
        with fake_lock:
            items = cat.items()
            sm.add_stock({oid: it.stock for oid, it in items.items()}, {oid: it.name for oid, it in items.items()})
            return sm_push.push_ready(c, cat, sm, s.max_qty, body.model_dump())

    @app.post("/api/sm/moves", dependencies=[Depends(auth)])
    def sm_moves(body: SmMoveBody, c=Depends(conn_dep)):
        """The other SM steps, tried on the fake: assign, borrow, send to the floor, hand over, return the old boxes (only with confirm)."""
        sm = need_fake_sm()
        with fake_lock:
            if body.action == "assign":
                results = sm_push.assign(sm, body.box_ids, body.person)
            elif body.action == "borrow":
                results = sm_push.borrow(sm, body.box_ids, body.person)
            elif body.action == "floor":
                results = sm_push.to_floor(sm, body.box_ids)
            elif body.action == "hand_over":
                results = sm_push.hand_over(sm, body.box_ids, body.person)
            elif body.action == "return_old":
                out = sm_push.return_old(sm, body.days, body.person, body.confirm)
                if not out["preview"]:
                    automation.log(c, "sm_push", f"SM giả: trả {sum(1 for r in out['results'] if r['ok'])} hộp cũ")
                return out
            else:
                raise service.Invalid("Thao tác không có trong danh sách: assign, borrow, floor, hand_over, return_old.")
        automation.log(c, "sm_push", f"SM giả ({body.action}): {sum(1 for r in results if r['ok'])} hộp xong, {sum(1 for r in results if not r['ok'])} không làm được")
        return {"preview": False, "boxes": [], "results": results}

    @app.get("/api/sm/mock", dependencies=[Depends(auth)])
    def sm_mock_state():
        """What the fake SM holds now."""
        sm = need_fake_sm()
        with fake_lock:
            return {"boxes": [{"box_id": b["box_id"], "title": b["title"], "status": b["status"], "status_label": STATUS_LABELS.get(b["status"], b["status"]),
                               "objects": len(b["contents"]), "object_ids": [x["object_id"] for x in b["contents"]], "note": b["note"],
                               "anchor_object_id": b["anchor_object_id"], "assignee": b.get("assignee"), "holder": b.get("holder")}
                              for b in sm.list_boxes()]}

    @app.get("/api/sm/boxes/report", dependencies=[Depends(auth)])
    def sm_boxes_report(c=Depends(conn_dep)):
        """What the last read of SM says: boxes and objects held, conflicts with this app's boxes, objects held twice."""
        return service.sm_report(c, cat)

    @app.get("/api/objects/coverage", dependencies=[Depends(auth)])
    def objects_coverage(c=Depends(conn_dep)):
        return service.coverage(c, cat)

    @app.post("/api/boxes/{box_id}/items/bulk", dependencies=[Depends(auth)])
    def add_items(box_id: int, body: BulkItems, c=Depends(conn_dep)):
        return service.add_items(c, box_id, body.object_ids, cat, s.max_qty)

    # ------------------------------------------------------------------ automations
    @app.get("/api/automations", dependencies=[Depends(auth)])
    def get_automations(c=Depends(conn_dep)):
        sw = automation.switches(c)
        return {"switches": list(sw.values()), "alerts": automation.alerts(c, cat, service.taken_ids(c)),
                "todo": automation.todo(c, cat, s.max_qty),
                "log": automation.recent_log(c), "planner": "deepseek" if engine.name == "deepseek" else "lines"}

    @app.put("/api/automations/{key}", dependencies=[Depends(auth)])
    def put_automation(key: str, body: SwitchBody, c=Depends(conn_dep)):
        return automation.set_switch(c, key, body.enabled, body.config)

    @app.get("/api/queue", dependencies=[Depends(auth)])
    def queue(c=Depends(conn_dep)):
        """Approved boxes waiting for the userscript: ready ones and blocked ones (with the reason)."""
        q = service.sm_queue(c, cat, s.max_qty)
        brief = lambda b: {"box_id": b["builder"]["box_id"], "title": b["title"], "items": b["item_count"]}
        return {"enabled": automation.is_on(c, "sm_queue"), "ready": [brief(b) for b in q["ready"]], "blocked": q["blocked"]}

    @app.post("/api/batches/plan", dependencies=[Depends(auth)])
    def batch_plan(b: PlanBody, c=Depends(conn_dep)):
        """AI (or, offline, plain line splitting) proposes several boxes from one brief. Creates nothing."""
        if not automation.is_on(c, "ai_batch"):
            raise service.Conflict("Tính năng tạo nhiều hộp bằng AI đang tắt. Bật lại ở mục Tự động hoá.")
        known = {n for n, _ in cat.categories()}
        bad = [x for x in b.categories if x not in known]
        if bad:
            raise service.Invalid(f"Nhóm không tồn tại: {', '.join(bad)}")
        taken = {r["label"] for r in c.execute("SELECT label FROM boxes WHERE module=?", (b.module.strip(),))}
        return planner.plan(engine, b.brief, b.module, b.start_label, b.boxes, lambda label: count_for(label, b.count), taken,
                            labels=b.labels, code=b.code, categories=b.categories)

    @app.post("/api/batches", dependencies=[Depends(auth)], status_code=201)
    def batch_create(b: BatchBody, c=Depends(conn_dep)):
        """Create the reviewed boxes and start picking for each. All-or-nothing on validation."""
        if not automation.is_on(c, "ai_batch"):
            raise service.Conflict("Tính năng tạo nhiều hộp bằng AI đang tắt. Bật lại ở mục Tự động hoá.")
        known = {n for n, _ in cat.categories()}
        for sp in b.boxes:
            bad = [x for x in sp.categories if x not in known]
            if bad:
                raise service.Invalid(f"Nhóm không tồn tại: {', '.join(bad)}")
        specs = [{**sp.model_dump(), "count": count_for(sp.label, sp.count)} for sp in b.boxes]
        ids = service.create_batch(c, specs, s.default_count, b.owner)
        automation.log(c, "batch", f"Tạo {len(ids)} hộp: " + ", ".join(f"{sp.module}/{sp.label}" for sp in b.boxes))
        for box_id in ids:
            picker.start_generation(s, cat, engine, box_id, auto=True, approve=b.approve)
        return {"ids": ids}

    @app.get("/api/sync/check")
    def sync_check(x_sync_token: str = Header(default=""), c=Depends(conn_dep)):
        """For the sync tool's "check the connection" button: is the address right, is the code right, what does the app hold now."""
        sync_auth(x_sync_token)
        return {"ok": True, "catalog": cat.info(), "vi_fallback": True, "min_usable": 50, "sm_boxes": service.sm_boxes_info(c)}

    @app.post("/api/sm/boxes/sync")
    def sm_boxes_sync(body: SmBoxesBody, x_sync_token: str = Header(default=""), c=Depends(conn_dep)):
        """The userscript (signed in to SM) sends the boxes SM holds so a new box never reuses one of their objects.
        Same secret as the catalog sync. Read only on SM's side; the server never contacts SM."""
        sync_auth(x_sync_token)
        return service.save_sm_boxes(c, body.boxes, time.time(), body.allow_empty)

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
                w.writerow([csv_safe(b["module"]), csv_safe(b["label"]), b["status"], it["pos"], it["id"], it["qty"],
                            csv_safe(it["name"]), csv_safe(it["vi"]), csv_safe(it["category"])])
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

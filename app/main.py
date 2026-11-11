"""FastAPI app: JSON API + the single static page. Run: uvicorn app.main:app --host 0.0.0.0 --port 8080"""
import contextlib
import csv
import hashlib
import hmac
import io
import json
import logging
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

from . import auth as login                    # not `auth`: create_app has its own function of that name (the shared access code)
from . import automation, db, images, overview, picker, planner, printview, service, sheet_panel, sm_push, usage, user_routes
from .catalog import Catalog, pad_id
from .settings import Settings
from .sm_client import STATUS_LABELS, MockSM

STATIC = os.path.join(os.path.dirname(__file__), "static")

log = logging.getLogger("box_builder")
if not log.handlers:                      # uvicorn sets up only its own loggers: without a handler of ours the info lines below would be dropped
    log.setLevel(logging.INFO)
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    log.addHandler(_handler)


def log_event(event: str, **counts: int) -> None:
    """One line per token route call: the event and whole numbers only. Never a token, an ID, a title or a name."""
    log.info("%s %s", event, " ".join(f"{k}={int(v)}" for k, v in counts.items()))


def minutes_since(ts, now: float) -> int | None:
    """Whole minutes from `ts` (epoch seconds) to `now`, or None when there is no time to count from."""
    try:
        return max(0, int((now - float(ts)) // 60))
    except (TypeError, ValueError):
        return None


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


class SheetChecksBody(BaseModel):
    checks: list = []                                  # one report per approved box; the limits and the entries are checked in service.save_sheet_checks


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
    since: str = Field(default="", max_length=10)      # the day the boxes that used these IDs were made, year-month-day; empty = today


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
    cat.image_ttl_s = s.image_ttl_minutes * 60
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

    # The interactive docs and the schema list every route to anyone who asks: off unless ENABLE_DOCS=1 is set for a look at them.
    docs = {} if s.enable_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}
    app = FastAPI(title="Box builder", lifespan=lifespan, **docs)

    def conn_dep():
        c = db.connect(s.db_path)
        try:
            yield c
        finally:
            c.close()

    # A browser sends a plain form POST from any web page without asking first. With no access code set, that would let a page the
    # visitor opens approve, regenerate or reopen boxes here. Refuse unsafe requests that a browser marks as coming from another
    # site. The two token routes are exempt: the userscript calls them from the SM page and they carry their own secret.
    TOKEN_ROUTES = ("/api/catalog/sync", "/api/sm/boxes/sync", "/api/sheet/checks")

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
    login.install(app, s)                         # login by company ID: off until ADMIN_IDS is set, see app/auth.py

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

    user_routes.install(app, s, cat, engine, count_for)       # the page and API for people who make boxes (/dung, /api/my/*)
    sheet_panel.install(app, s)                               # the Google Sheet box on the Automations tab (link, last fetch, the script)
    overview.install(app, s, cat)                             # what the admin watches: boxes per person and state, and what looks wrong

    SCRIPT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "integrations", "box_builder_sm.user.js")
    APP_URL_OK = re.compile(r"^https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?$")

    def host_of(raw: str) -> str:
        """The host name in 'host', 'host:port' or 'https://host/...', lower case; '' when there is none."""
        raw = raw.strip()
        try:
            return urlsplit(raw if "//" in raw else "//" + raw).hostname or ""
        except ValueError:
            return ""

    def script_text(request: Request, app_url: str) -> str:
        """The SM sync tool with this app's own address written into it (and into its @connect line). It carries no update address and one
        `@downloadURL none`, the value Tampermonkey documents for "never check for an update": a copy already installed stops polling here, and a
        new version is pasted by hand."""
        proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        origin = f"{proto}://{request.headers.get('host', request.url.netloc)}"
        target = app_url.strip().rstrip("/") or origin
        if not APP_URL_OK.match(target):
            raise service.Invalid("Tham số app phải là một địa chỉ dạng https://tên-máy hoặc http://tên-máy:cổng.")
        # The address goes into the tool as the place it sends the sync code to, and into its @connect line. A link that names any other machine
        # would make an installed tool hand that code to a stranger, so only this server, localhost, 127.0.0.1 and the hosts the owner listed pass.
        if app_url.strip() and host_of(target) not in {host_of(origin), "localhost", "127.0.0.1", *(host_of(h) for h in re.split(r"[,\s;]+", s.allowed_app_hosts))} - {""}:
            raise service.Invalid("Tham số app chỉ nhận địa chỉ của chính máy chủ này, localhost, 127.0.0.1 hoặc máy trong ALLOWED_APP_HOSTS.")
        with open(SCRIPT_FILE, encoding="utf-8") as f:
            text = f.read()
        host = urlsplit(target).hostname
        text = text.replace('const DEFAULT_APP = "";', f"const DEFAULT_APP = {json.dumps(target)};", 1)
        connect = "" if f"// @connect      {host}\n" in text else f"// @connect      {host}\n"       # not twice if the file already lists it
        return text.replace("// @connect      localhost\n", f"// @connect      localhost\n{connect}// @downloadURL none\n", 1)

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
                "draft_ttl_hours": s.draft_ttl_hours}

    # ------------------------------------------------------------------ everything below needs the access code (if set)
    @app.get("/api/state")
    def get_state(rev: int = -1, c=Depends(conn_dep)):
        service.housekeeping(c, s.draft_ttl_hours, s.box_done_days)
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
                 "sector": o.get("sector"), "img": images.pick_image(o, hosts=cat.image_hosts),
                 # borrow count and creation date: optional, kept as whole number and epoch seconds (None = unknown), so the saved snapshot holds clean values
                 "uses": usage.parse_uses(o.get("uses")), "created": usage.parse_created(o.get("created"))} for o in body.objects]
        sent_uses = any(r["uses"] is not None for r in rows)
        if not sent_uses:
            # This tool sends no borrow counts (the team's own script v2.23.0 is one). Every sync replaces the whole catalog, so without this
            # one of its syncs would erase the numbers a sync with counts brought. Keep each object's last known count and date; an object
            # that is new here stays unknown. A batch that carries any count is taken as it is.
            # It reads the saved snapshot, not the usable catalog: an object that is out of stock at this sync (borrowed out, often the
            # most borrowed ones) is not in the catalog but its row, with its count, is still in the snapshot.
            snap = service.load_snapshot(c)
            known = {}
            for old in (snap[0] if snap else []):
                key = pad_id(old.get("id"))
                if key and (old.get("uses") is not None or old.get("created") is not None):
                    known[key] = old
            for r in rows:
                old = known.get(pad_id(r["id"]))
                if old is not None:
                    r["uses"], r["created"] = usage.parse_uses(old.get("uses")), usage.parse_created(old.get("created"))
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
        summary["with_uses"] = sum(1 for it in cat.items().values() if it.uses is not None)      # objects that came with a borrow count
        # Only keep what this app is allowed to use. Objects from other sectors are counted, then dropped, never stored.
        service.save_snapshot(c, [r for r in rows if cat.allowed_sector(r)], now)
        if sent_uses:                                  # when the borrow counts last came from SM itself (not carried over)
            c.execute("INSERT INTO catalog_snapshot(k, v) VALUES('usage_at', ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v", (json.dumps(now),))
        summary["uses_carried_over"] = not sent_uses and summary["with_uses"] > 0
        claimed = {r[0] for r in c.execute("SELECT object_id FROM box_items")}
        summary["claimed_now_unavailable"] = sorted(claimed - set(cat.items()))
        summary["healed_boxes"] = heal_boxes(c) if automation.is_on(c, "auto_heal") else []
        log_event("catalog_sync", received=len(body.objects), usable=len(cat.items()), healed=len(summary["healed_boxes"]))
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

    @app.get("/api/catalog")
    def catalog_info():
        return cat.info()

    @app.get("/api/categories")
    def categories():
        return [{"name": n, "count": k} for n, k in cat.categories()]

    @app.get("/api/search")
    def search(q: str, c=Depends(conn_dep)):
        taken = service.taken_ids(c)
        return [{"id": i.id, "name": i.name, "vi": i.vi, "category": i.category, "stock": i.stock, "img": i.img,
                 "img_exp": cat.image_expiry(i.img) if i.img else None, "detail": images.detail_url(i.id)}
                for i in cat.search(q, exclude=taken, limit=15)]

    @app.post("/api/boxes", status_code=201)
    def create_box(b: NewBox, request: Request, c=Depends(conn_dep)):
        known = {n for n, _ in cat.categories()}
        bad = [x for x in b.categories if x not in known]
        if bad:
            raise service.Invalid(f"Nhóm không tồn tại: {', '.join(bad)}")
        who = request.state.person
        box_id = service.create_box(c, b.module, b.label, b.theme, b.categories, count_for(b.label, b.count), b.owner.strip() or (who.id if who else ""),
                                    b.code, b.anchor)
        login.audit(c, login.who(request), "box_create", box_id, f"{b.module} / {b.label}", login.client_ip(request))
        picker.start_generation(s, cat, engine, box_id, auto=True)
        return {"id": box_id}

    @app.post("/api/boxes/{box_id}/regenerate")
    def regenerate(box_id: int, c=Depends(conn_dep)):
        released = service.release_unlocked(c, box_id)
        picker.start_generation(s, cat, engine, box_id, avoid=set(released))
        return {"released": len(released)}

    @app.post("/api/boxes/{box_id}/items/{pos}/replace")
    def replace(box_id: int, pos: int, c=Depends(conn_dep)):
        row = c.execute("SELECT locked FROM box_items WHERE box_id=? AND pos=?", (box_id, pos)).fetchone()
        if row and row["locked"]:
            raise service.Conflict("Hãy mở khoá món này trước khi thay.")
        released = service.release_unlocked(c, box_id, [pos])
        picker.start_generation(s, cat, engine, box_id, avoid=set(released), positions=[pos])
        return {"released": len(released)}

    @app.post("/api/boxes/{box_id}/items", status_code=201)
    def add_item(box_id: int, a: AddItem, c=Depends(conn_dep)):
        return {"pos": service.add_item(c, box_id, a.object_id, a.qty, cat, s.max_qty)}

    @app.post("/api/boxes/{box_id}/items/{pos}/lock")
    def lock(box_id: int, pos: int, body: Lock, c=Depends(conn_dep)):
        service.set_locked(c, box_id, pos, body.locked)
        return {"ok": True}

    @app.patch("/api/boxes/{box_id}/items/{pos}")
    def patch_item(box_id: int, pos: int, body: Qty, c=Depends(conn_dep)):
        service.set_qty(c, box_id, pos, body.qty, cat, s.max_qty)
        return {"ok": True}

    @app.delete("/api/boxes/{box_id}/items/{pos}")
    def del_item(box_id: int, pos: int, c=Depends(conn_dep)):
        service.remove_item(c, box_id, pos)
        return {"ok": True}

    @app.post("/api/boxes/{box_id}/fill")
    def fill(box_id: int, c=Depends(conn_dep)):
        """Fill the empty slots (after removing objects by hand) without touching the rest."""
        service.release_unlocked(c, box_id, [])       # status -> generating, nothing released
        picker.start_generation(s, cat, engine, box_id)
        return {"ok": True}

    @app.post("/api/boxes/{box_id}/approve")
    def approve(box_id: int, request: Request, c=Depends(conn_dep)):
        service.approve(c, box_id)
        login.audit(c, login.who(request), "box_approve", box_id, ip=login.client_ip(request))
        return {"ok": True}

    @app.post("/api/boxes/{box_id}/reopen")
    def reopen(box_id: int, request: Request, c=Depends(conn_dep)):
        service.reopen(c, box_id)
        login.audit(c, login.who(request), "box_reopen", box_id, ip=login.client_ip(request))
        return {"ok": True}

    @app.delete("/api/boxes/{box_id}")
    def delete_box(box_id: int, request: Request, c=Depends(conn_dep)):
        service.delete_box(c, box_id)
        login.audit(c, login.who(request), "box_delete", box_id, ip=login.client_ip(request))
        return {"ok": True}

    @app.post("/api/used")
    def import_used(body: UsedIds, c=Depends(conn_dep)):
        return service.import_used(c, body.text, body.note, cat, body.since)

    @app.delete("/api/used/{oid}")
    def remove_used(oid: str, c=Depends(conn_dep)):
        service.remove_used(c, oid)
        return {"ok": True}

    def no_sheet(tb: dict) -> dict:
        """These two feeds need no login, so they do not carry the supporters' check detail (object IDs they listed): the queue reads it from its own copy."""
        return {**tb, "builder": {k: v for k, v in tb["builder"].items() if k != "sheet"}}

    @app.get("/api/taskboxes")
    def taskboxes(status: str = "approved", pending: bool = False, c=Depends(conn_dep)):
        """Boxes in the SM TaskBox userscript's own format: approved boxes only (the `status` parameter is kept for old callers and ignored: this
        route needs no login, so it must not list drafts). pending=true hides boxes that were already pushed to SM (what a Google Sheet script wants)."""
        service.housekeeping(c, s.draft_ttl_hours, s.box_done_days)             # a finished box is never handed to the team's tools
        wanted = ("approved",)
        if pending and wanted == ("approved",):
            # what the SM userscript asks for: only boxes that passed the checks, and nothing when the queue is switched off
            if not automation.is_on(c, "sm_queue"):
                return {"boxes": [], "disabled": True}
            return {"boxes": [no_sheet(tb) for tb in service.sm_queue(c, cat, s.max_qty)["ready"]]}
        return {"boxes": [no_sheet(tb) for tb in service.taskboxes(c, cat, wanted, pending_only=pending)]}

    def sheet_auth(x_sheet_token: str = Header(default="")):
        """Its own secret, not the sync token. Whoever holds it can report the supporters' check; with the gate on, a clean report changes the objects of approved boxes that are not in SM yet. The sheet script keeps it in its Script Properties, so it is readable by whoever can edit that script."""
        if not s.sheet_token:
            raise HTTPException(503, "Máy chủ chưa đặt SHEET_TOKEN nên chưa nhận kết quả kiểm từ bảng tính.")
        if not hmac.compare_digest(x_sheet_token.encode(), s.sheet_token.encode()):
            raise HTTPException(401, "Sai mã của bảng tính.")

    @app.post("/api/sheet/checks")
    def sheet_checks(body: SheetChecksBody, _=Depends(sheet_auth), c=Depends(conn_dep)):
        """The sheet's own script reports, for each approved box, the SC's tick, the supporters' tick and the objects the supporters left in the block.
        The server only records it (and, with the supporter gate on, applies a clean report to the box). It never reads the sheet."""
        out = service.save_sheet_checks(c, body.checks, time.time(), automation.is_on(c, "need_supporter_check"))
        sheet_panel.touch_check(c, out["saved"])
        return {"ok": True, **out}

    @app.get("/api/sheet-boxes")
    def sheet_boxes(c=Depends(conn_dep)):
        """What the Google Sheet script reads. Same shape as /api/taskboxes; empty when its switch is off."""
        if not automation.is_on(c, "sheet_export"):
            sheet_panel.touch(c, 0)
            return {"boxes": [], "disabled": True}
        service.housekeeping(c, s.draft_ttl_hours, s.box_done_days)
        newest = {}
        for tb in sorted(service.taskboxes(c, cat, ("approved",)), key=lambda x: x["builder"]["box_id"]):
            newest[(tb["module"], tb["builder"]["label"])] = tb             # the same unit and kind on two days: the sheet gets the newest only
        boxes = sorted(newest.values(), key=lambda x: x["builder"]["box_id"])
        sheet_panel.touch(c, len(boxes))                       # so the page can say when the sheet's script last asked
        return {"boxes": boxes}

    # ------------------------------------------------------------------ object browser
    @app.get("/api/objects")
    def objects(q: str = "", category: str = "", status: str = "", sort: str = "id", page: int = 1, per: int = 50, band: str = "",
                c=Depends(conn_dep)):
        if status and status not in service.OBJECT_STATUSES:
            raise service.Invalid("Trạng thái không hợp lệ.")
        return service.objects_view(c, cat, q, category, status, sort, page, per, band)

    @app.get("/api/usage")
    def usage_report(c=Depends(conn_dep)):
        """How many times the objects were borrowed and what the "Giảm món đã dùng nhiều" switch does with them. Read only.
        `usage_at` is when SM last sent the borrow counts itself (the numbers may be older than the catalog: a sync from a tool that sends none keeps them)."""
        out = service.usage_report(c, cat)
        row = c.execute("SELECT v FROM catalog_snapshot WHERE k='usage_at'").fetchone()
        try:
            out["usage_at"] = float(json.loads(row[0])) if row else None
        except (ValueError, TypeError):
            out["usage_at"] = None
        return out

    def csv_safe(v) -> str:
        """Text from SM goes into a spreadsheet: a cell that starts with = + - or @ would run as a formula, so mark it as text."""
        v = "" if v is None else str(v)
        return "'" + v if v[:1] in ("=", "+", "-", "@", "\t", "\r") else v

    @app.get("/api/objects.csv")
    def objects_csv(q: str = "", category: str = "", status: str = "", sort: str = "id", band: str = "", c=Depends(conn_dep)):
        """The same list and filters as /api/objects, all pages, as a UTF-8 CSV (with BOM so Excel reads the accents)."""
        if status and status not in service.OBJECT_STATUSES:
            raise service.Invalid("Trạng thái không hợp lệ.")
        rows, _ = service.object_rows(c, cat, q, category, status, sort, band)
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

    @app.get("/api/backup.json")
    def backup(c=Depends(conn_dep)):
        """Everything this app owns, as one JSON file: boxes and items, pushed records, used-in-sheet IDs, automation switches.
        No secrets, no catalog, no SM snapshot."""
        body = json.dumps(service.export_backup(c), ensure_ascii=False, indent=1)
        return Response(body, media_type="application/json; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=box-builder-backup-{time.strftime('%Y%m%d')}.json"})

    @app.post("/api/backup/import")
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

    @app.post("/api/import/boxes.csv")
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

    @app.get("/print/boxes")
    def print_boxes(status: str = "approved", module: str = "", ids: str = "", combined: bool = True, day: str = "", c=Depends(conn_dep)):
        """The paper a supporter takes to the shelves: approved boxes, one page each, plus one list sorted by shelf location."""
        wanted = tuple(x.strip() for x in status.split(",") if x.strip()) or ("approved",)
        id_set = {int(x) for x in ids.split(",") if x.strip().isdigit()}
        want_day = "" if (id_set or day == "all") else (day.strip() or service.vn_day(time.time()))     # the paper of the day, unless asked for another
        boxes = []
        for b in service.state(c, cat)["boxes"]:
            if b["status"] not in wanted or not b["items"] or (module and b["module"] != module.strip()) or (id_set and b["id"] not in id_set):
                continue
            if want_day and b["day"] and b["day"] != want_day:
                continue
            anchor_id, name = service.split_anchor(b["anchor"])
            if anchor_id:
                item = cat.get(anchor_id)
                name = item.name if item else ""
            code, label = b["code"], b["label"]
            if code and label.startswith(code + " ") and len(label) > len(code) + 1:
                label = label[len(code) + 1:]                      # the stored name carries the section code in front: the paper shows it once
            boxes.append({**b, "anchor": name, "label": label})
        page = printview.render_pick_list(boxes, generated_at=service.vn_now_text(), combined=combined)
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    @app.post("/api/sm/push")
    def sm_push_run(body: SmPushBody, c=Depends(conn_dep)):
        """Try the push on the fake SM: every ready box becomes a TaskBox there and is recorded as pushed."""
        sm = need_fake_sm()
        if not automation.is_on(c, "sm_queue"):
            raise service.Conflict("Hàng chờ lên SM đang tắt (tab Tự động hoá). Bật lại rồi thử.")
        with fake_lock:
            items = cat.items()
            sm.add_stock({oid: it.stock for oid, it in items.items()}, {oid: it.name for oid, it in items.items()})
            return sm_push.push_ready(c, cat, sm, s.max_qty, body.model_dump())

    @app.post("/api/sm/moves")
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

    @app.get("/api/sm/mock")
    def sm_mock_state():
        """What the fake SM holds now."""
        sm = need_fake_sm()
        with fake_lock:
            return {"boxes": [{"box_id": b["box_id"], "title": b["title"], "status": b["status"], "status_label": STATUS_LABELS.get(b["status"], b["status"]),
                               "objects": len(b["contents"]), "object_ids": [x["object_id"] for x in b["contents"]], "note": b["note"],
                               "anchor_object_id": b["anchor_object_id"], "assignee": b.get("assignee"), "holder": b.get("holder")}
                              for b in sm.list_boxes()]}

    @app.get("/api/sm/boxes/report")
    def sm_boxes_report(ages: bool = False, c=Depends(conn_dep)):
        """What the last read of SM says: boxes and objects held, conflicts with this app's boxes, objects held twice.
        With ages=1 it also gives how old the boxes are and what each choice of days for the reuse switch would free."""
        return service.sm_report(c, cat, ages)

    @app.get("/api/objects/coverage")
    def objects_coverage(c=Depends(conn_dep)):
        return service.coverage(c, cat)

    @app.post("/api/boxes/{box_id}/items/bulk")
    def add_items(box_id: int, body: BulkItems, c=Depends(conn_dep)):
        return service.add_items(c, box_id, body.object_ids, cat, s.max_qty)

    # ------------------------------------------------------------------ automations
    @app.get("/api/automations")
    def get_automations(c=Depends(conn_dep)):
        sw = automation.switches(c)
        return {"switches": list(sw.values()), "alerts": automation.alerts(c, cat, service.taken_ids(c)),
                "todo": automation.todo(c, cat, s.max_qty),
                "log": automation.recent_log(c), "planner": "deepseek" if engine.name == "deepseek" else "lines"}

    @app.put("/api/automations/{key}")
    def put_automation(key: str, body: SwitchBody, c=Depends(conn_dep)):
        return automation.set_switch(c, key, body.enabled, body.config)

    @app.get("/api/queue")
    def queue(c=Depends(conn_dep)):
        """Approved boxes waiting for the userscript: ready ones and blocked ones (with the reason)."""
        q = service.sm_queue(c, cat, s.max_qty)
        brief = lambda b: {"box_id": b["builder"]["box_id"], "title": b["title"], "items": b["item_count"]}
        return {"enabled": automation.is_on(c, "sm_queue"), "ready": [brief(b) for b in q["ready"]], "blocked": q["blocked"]}

    @app.post("/api/batches/plan")
    def batch_plan(b: PlanBody, c=Depends(conn_dep)):
        """AI (or, offline, plain line splitting) proposes several boxes from one brief. Creates nothing."""
        if not automation.is_on(c, "ai_batch"):
            raise service.Conflict("Tính năng tạo nhiều hộp bằng AI đang tắt. Bật lại ở mục Tự động hoá.")
        known = {n for n, _ in cat.categories()}
        bad = [x for x in b.categories if x not in known]
        if bad:
            raise service.Invalid(f"Nhóm không tồn tại: {', '.join(bad)}")
        taken = {r["label"] for r in c.execute("SELECT label FROM boxes WHERE module=? AND day=?", (b.module.strip(), service.vn_day(time.time())))}
        return planner.plan(engine, b.brief, b.module, b.start_label, b.boxes, lambda label: count_for(label, b.count), taken,
                            labels=b.labels, code=b.code, categories=b.categories)

    @app.post("/api/batches", status_code=201)
    def batch_create(b: BatchBody, request: Request, c=Depends(conn_dep)):
        """Create the reviewed boxes and start picking for each. All-or-nothing on validation."""
        if not automation.is_on(c, "ai_batch"):
            raise service.Conflict("Tính năng tạo nhiều hộp bằng AI đang tắt. Bật lại ở mục Tự động hoá.")
        known = {n for n, _ in cat.categories()}
        for sp in b.boxes:
            bad = [x for x in sp.categories if x not in known]
            if bad:
                raise service.Invalid(f"Nhóm không tồn tại: {', '.join(bad)}")
        specs = [{**sp.model_dump(), "count": count_for(sp.label, sp.count)} for sp in b.boxes]
        who = request.state.person
        ids = service.create_batch(c, specs, s.default_count, b.owner.strip() or (who.id if who else ""))
        automation.log(c, "batch", f"Tạo {len(ids)} hộp: " + ", ".join(f"{sp.module}/{sp.label}" for sp in b.boxes))
        for box_id, sp in zip(ids, b.boxes):
            login.audit(c, login.who(request), "box_create", box_id, f"{sp.module} / {sp.label}", login.client_ip(request))
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
        info = service.save_sm_boxes(c, body.boxes, time.time(), body.allow_empty)
        log_event("sm_boxes_sync", received=info["received"], active=info["active"], held=info["objects_held"])
        return info

    def sync_auth(x_sync_token: str):
        if not s.sync_token:
            raise HTTPException(503, "Máy chủ chưa đặt SYNC_TOKEN.")
        if not hmac.compare_digest(x_sync_token.encode(), s.sync_token.encode()):
            raise HTTPException(401, "Sai mã đồng bộ.")

    def script_version() -> str | None:
        """The version the repo's tool says it is (its `const VERSION`), so the tool can tell whether the copy in the browser is the current one."""
        try:
            with open(SCRIPT_FILE, encoding="utf-8") as f:
                m = re.search(r'^\s*const VERSION = "([^"]{1,40})";', f.read(), re.M)
        except OSError:
            return None
        return m.group(1) if m else None

    @app.get("/api/preflight")
    def preflight(x_sync_token: str = Header(default=""), c=Depends(conn_dep)):
        """The tool asks this before it does anything: is the app awake and its database answering, is login on, is the queue on, how old are the
        two snapshots, how many boxes are ready. Needs the sync token like /api/sync/check. No owner, no names. A pure read: no housekeeping,
        so a poll from the tool never retires a box or frees an object."""
        sync_auth(x_sync_token)
        c.execute("SELECT 1").fetchone()
        now = time.time()
        sm_info = service.sm_boxes_info(c)
        out = {"ok": True, "login_enforced": login.enforced(s), "sm_queue": automation.is_on(c, "sm_queue"),
               "catalog_age_min": minutes_since(cat.info().get("synced_at"), now),
               "sm_snapshot_age_min": minutes_since(sm_info.get("synced_at") if sm_info else None, now),
               "ready_boxes": len(service.sm_queue(c, cat, s.max_qty)["ready"]), "expected_script_version": script_version()}
        log_event("preflight", ready=out["ready_boxes"], queue=out["sm_queue"], login=out["login_enforced"])
        return out

    @app.get("/api/sm/returnable")
    def sm_returnable(today: bool = False, x_sync_token: str = Header(default=""), c=Depends(conn_dep)):
        """Boxes made on an earlier day that are, or were, in SM (title, day, and the SM id when the tool reported it), for the tool's return
        preview. today=1 adds today's boxes. No owner, no names, and it never runs housekeeping: a poll must not retire a box."""
        sync_auth(x_sync_token)
        boxes = service.returnable(c, include_today=today)
        log_event("returnable", boxes=len(boxes), with_today=today)
        return {"boxes": boxes}

    @app.post("/api/boxes/{box_id}/pushed")
    def box_pushed(box_id: int, body: PushedBody, x_sync_token: str = Header(default=""), c=Depends(conn_dep)):
        """The userscript reports a box it just created in SM, so it is never pushed twice. Needs the sync token
        (not the shared access code, which may be switched off). The same SM id again is a no-op; another id for a box that has one is a 409."""
        sync_auth(x_sync_token)
        written = service.mark_pushed(c, box_id, body.sm_box_id)
        log_event("pushed", new=written, same=not written)
        return {"ok": True}

    @app.delete("/api/boxes/{box_id}/pushed")
    def box_unpushed(box_id: int, x_sync_token: str = Header(default=""), c=Depends(conn_dep)):
        """Admin escape hatch: forget that a box was pushed (e.g. someone deleted it in SM by hand)."""
        sync_auth(x_sync_token)
        service.unmark_pushed(c, box_id)
        log_event("unpushed", boxes=1)
        return {"ok": True}

    @app.get("/api/export.csv")
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

    # Registered last on purpose: the last middleware added is the outermost, so these headers are also on the answers the login gate and the
    # cross-site check give without reaching a route (a 401, a 403, a redirect).
    @app.middleware("http")
    async def safe_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"      # an answer with boxes or counts is never kept by a browser or a proxy
        return response

    return app


_app = None


def __getattr__(name):            # lets `uvicorn app.main:app` build the app lazily from env
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)

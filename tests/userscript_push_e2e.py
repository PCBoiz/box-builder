"""Browser test of the box features of integrations/box_builder_sm.user.js: reading SM's boxes, and pushing ready boxes in two steps.
SM is never contacted: sm.config.inc is answered by app/sm_client.py's MockSM behind Playwright routes. The app is the real one, on its own port
and database. Run: python -I tests/userscript_push_e2e.py (needs playwright + chromium; set E2E_BROWSER=firefox to use Firefox).
Works on Windows and Linux."""
import csv, json, os, re, shutil, socket, sqlite3, subprocess, sys, tempfile, time, urllib.error, urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import sync_playwright

# ---- portability (Windows and Linux): UTF-8 output, no proxy for local calls, a free port, the server started with this Python,
# ---- and clean-up that survives a file still locked for a moment after the server stops
for _stream in (sys.stdout, sys.stderr):
    try: _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def clean_env(**extra):
    drop = ("DATABASE_URL", "DEEPSEEK_API_KEY", "ACCESS_CODE", "LLM_MODE", "IMAGE_HOSTS", "SECTORS", "SM_PUSH_MODE", "TEST_DATABASE_URL")
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.update(extra)
    return env

def stop_server(proc, log, *dirs):
    if proc is not None and proc.poll() is None:
        if os.name == "nt":   # a venv's python.exe starts the real interpreter as a child: stop the whole tree
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            proc.terminate()
        try: proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill(); proc.wait(timeout=20)
    try: log.close()
    except Exception: pass
    for d in dirs:
        for attempt in range(10):
            try: shutil.rmtree(d, ignore_errors=attempt == 9); break
            except FileNotFoundError: break
            except OSError: time.sleep(0.3)

def start_server(root, env, port, tmp):
    log = open(os.path.join(tmp, "server.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(300):
        if proc.poll() is not None: break
        try: urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=1); return proc, log
        except Exception: time.sleep(0.2)
    log.flush()
    tail = open(os.path.join(tmp, "server.log"), encoding="utf-8", errors="replace").read()[-1500:]
    stop_server(proc, log, tmp)
    raise RuntimeError("the app did not start: " + tail)

def read_text(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh: return fh.read()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)                                     # python -I leaves the project off the path
from app.sm_client import MockSM, SMError                    # noqa: E402

PORT = int(os.environ.get("E2E_PORT") or free_port())
BROWSER = os.environ.get("E2E_BROWSER", "chromium")
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "tok-push-e2e"
SCRIPT = read_text(ROOT, "integrations", "box_builder_sm.user.js")       # the file as it ships: read-only (WRITES_ENABLED false)
OFF = "const WRITES_ENABLED = false;"
SCRIPT_WRITE = SCRIPT.replace(OFF, "const WRITES_ENABLED = true;")        # NEGATIVE CONTROL: the same file with the one switch turned on
assert SCRIPT.count(OFF) == 1 and SCRIPT_WRITE != SCRIPT and SCRIPT_WRITE.count("const WRITES_ENABLED = true;") == 1, "the interlock constant moved or changed"
def without(text, old, new=""):
    """The shipped script with one guard taken out, to prove each guard blocks on its own. The edit must happen, or the guard moved."""
    assert text.count(old) == 1, f"guard text not found exactly once: {old!r}"
    return text.replace(old, new)
tmp = tempfile.mkdtemp()
DB = os.path.join(tmp, "p.db")

# ---- the pretend SM: the real catalog's usable objects, held in a MockSM
with open(os.path.join(ROOT, "data", "catalog.csv"), encoding="utf-8-sig", newline="") as fh:
    rows = list(csv.reader(fh))[1:]
usable = [r for r in rows if r[6].strip() == "ACTIVE" and r[2].strip() and r[7].strip().isdigit() and int(r[7]) > 0]
objects = [{"id": f"O{r[0].strip().zfill(6)}", "iid": int(r[0]), "name": r[1], "type": "Obj", "status": "ACTIVE", "quantity": int(r[7]), "loan_reserved": 0,
            "transfer_reserved": 0, "location": r[5], "sector": "Hanoi-GPI", "image_url": ""} for r in usable]
ALL_IDS = sorted(o["id"][1:] for o in objects)
sm = MockSM({o["id"][1:]: o["quantity"] for o in objects}, {o["id"][1:]: o["name"] for o in objects},
           clock=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))        # boxes made now: the reuse rule must treat them as recent
PAGE = 2                                                     # SM answers two boxes per page, so the script has to follow has_next
SECRETS = ("Tran Thi Assignee", "Le Van Holder", "Nguyen Van Creator", "Pham Van People", "Hoang Thi Receiver", "Pham Van Returner")

env = clean_env(DB_PATH=DB, CATALOG_PATH=os.path.join(ROOT, "data", "catalog.csv"), SYNC_TOKEN=TOKEN)
srv, srv_log = start_server(ROOT, env, PORT, tmp)

fails, errs = [], []
sm_requests, sm_posts, sm_queries, app_requests = [], [], [], []
state = {"signed_in": True, "post_401": False, "app_force": {}}
def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

def api(path, method="GET", body=None, token=True):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", **({"X-Sync-Token": TOKEN} if token else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b"null")

def gm(opts):
    """Tampermonkey's request: the script's calls to the app land here."""
    path = urlsplit(opts["url"]).path
    app_requests.append((opts["method"], path, opts.get("data") or ""))
    if path in state["app_force"]:
        return {"status": state["app_force"][path], "responseText": '{"detail":"forced"}'}
    req = urllib.request.Request(opts["url"], method=opts["method"], headers=opts.get("headers") or {}, data=(opts.get("data") or "").encode() or None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return {"status": r.status, "responseText": r.read().decode()}
    except urllib.error.HTTPError as e: return {"status": e.code, "responseText": e.read().decode()}
    except Exception as e: return {"error": str(e)}

LOGIN = dict(status=200, content_type="text/html", body="<html><body>Login</body></html>")
def sm_route(route):
    """Everything under sm.config.inc/api: objects like the sync test, boxes from the MockSM. Anything else is recorded and refused."""
    req = route.request
    path = urlsplit(req.url).path
    sm_requests.append((req.method, path, dict(req.headers), req.post_data or ""))
    if path == "/api/objects" and req.method == "GET":
        if not state["signed_in"]: return route.fulfill(**LOGIN)
        page = int(parse_qs(urlsplit(req.url).query).get("page", ["1"])[0])
        pages = -(-len(objects) // 30)
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"objects": objects[(page - 1) * 30: page * 30], "page": page, "total_items": len(objects), "total_pages": pages, "per_page": 30, "has_next": page < pages}))
    if path == "/api/boxes" and req.method == "GET":
        if not state["signed_in"]: return route.fulfill(**LOGIN)
        q = parse_qs(urlsplit(req.url).query); sm_queries.append(q)
        page = int(q.get("page", ["1"])[0])
        newest = list(reversed(sm.list_boxes()))
        newest = [dict(b, created_by_name="Nguyen Van Creator", people=["Pham Van People"], receiver="Hoang Thi Receiver") for b in newest]
        chunk = newest[(page - 1) * PAGE: page * PAGE]
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(
            {"count": len(chunk), "total": len(newest), "page": page, "page_size": PAGE, "has_next": page * PAGE < len(newest), "boxes": chunk}))
    if path == "/api/boxes" and req.method == "POST":
        body = json.loads(req.post_data)
        if state["post_401"]:
            sm_posts.append({"body": body, "status": 401})
            return route.fulfill(status=401, content_type="application/json", body='{"detail":"Unauthorized"}')
        try:
            new_id = sm.create_box(body)
        except SMError as e:
            sm_posts.append({"body": body, "status": 422, "detail": e.message})
            return route.fulfill(status=422, content_type="application/json", body=json.dumps({"detail": e.message}))
        sm_posts.append({"body": body, "status": 200, "id": new_id})
        return route.fulfill(status=200, content_type="application/json", body=json.dumps({"box_id": new_id}))
    route.fulfill(status=404, content_type="application/json", body='{"detail":"not here"}')

SHIM = """
window.__store = {bb_app: %s, bb_token: %s};
window.GM_getValue = (k, d) => (k in window.__store ? window.__store[k] : d);
window.GM_setValue = (k, v) => { window.__store[k] = v; };
window.GM_addStyle = (css) => { const s = document.createElement('style'); s.textContent = css; document.head.append(s); return s; };
window.GM_xmlhttpRequest = (o) => {
  window.__gm({method: o.method || 'GET', url: o.url, headers: o.headers || {}, data: o.data || null}).then((r) => {
    if (r.error) { o.onerror && o.onerror({error: r.error}); } else { o.onload({status: r.status, responseText: r.responseText}); }
  });
};
""" % (json.dumps(BASE), json.dumps(TOKEN))

def log_text(pg): return pg.inner_text("#bb-log")
def wait_log(pg, needle, timeout=60000):
    try: pg.wait_for_function("n => document.querySelector('#bb-log').innerText.includes(n)", arg=needle, timeout=timeout)
    except Exception: pass
    return needle in log_text(pg)
def idle(pg):
    pg.wait_for_function("!document.getElementById('bb-view').disabled", timeout=120000)
def panel_text(pg, sel): return pg.inner_text(sel)

# ---- helpers on the app side
def make_box(label, code, anchor, count=6):
    st, b = api("/api/boxes", "POST", {"module": "M8", "label": label, "theme": "đồ dùng", "count": count, "code": code, "anchor": anchor, "owner": "test"}, token=False)
    assert st == 201 or st == 200, (st, b)
    for _ in range(150):
        box = next(x for x in api("/api/state", token=False)[1]["boxes"] if x["id"] == b["id"])
        if box["status"] != "generating": break
        time.sleep(0.2)
    st, r = api(f"/api/boxes/{b['id']}/approve", "POST", token=False)
    assert st == 200, (st, r)
    return b["id"]
def pending(): return {b["builder"]["box_id"]: b for b in api("/api/taskboxes?pending=true", token=False)[1]["boxes"]}
def app_items():
    return {i["id"] for b in api("/api/state", token=False)[1]["boxes"] for i in b["items"]}
def spare(n, used=set()):
    """Object ids no app box holds and no decoy used yet, so a decoy never blocks a box by accident."""
    out = [i for i in ALL_IDS if i not in app_items() and i not in used][-n:]
    used.update(out)
    return out
def lines(ids): return [{"object_id": i, "quantity": 1} for i in ids]
def returned(title, ids):
    bid = sm.create_box({"title": title, "contents": lines(ids), "borrow_now": False})
    sm.transition(bid, "assigned", assignee="Tran Thi Assignee"); sm.transition(bid, "collected", borrower="Le Van Holder")
    sm.transition(bid, "deactivated", returned_by="Pham Van Returner")
    return bid
def made_by_script(): return [p for p in sm_posts if p["status"] == 200]

try:
    st, r = api("/api/automations/sm_queue", "PUT", {"enabled": True}, token=False)         # on purpose, so the run does not depend on the app's default
    assert st == 200, (st, r)
    with sync_playwright() as pw:
        b = getattr(pw, BROWSER).launch()
        def open_sm(script):
            """A signed-in SM tab with Tampermonkey's calls wired in, and `script` loaded the way Tampermonkey would (after the page)."""
            page = b.new_context(viewport={"width": 1300, "height": 1000}).new_page()
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.route("https://sm.config.inc/api/**", sm_route)
            page.route("https://sm.config.inc/", lambda r: r.fulfill(status=200, content_type="text/html", body="<html><body><h1>Scenario Manager</h1></body></html>"))
            page.expose_function("__gm", gm)
            page.goto("https://sm.config.inc/"); page.add_script_tag(content=SHIM); page.add_script_tag(content=script)
            page.wait_for_selector("#bb-btn")
            return page
        pg = open_sm(SCRIPT_WRITE)                                  # the create scenarios below run on the negative control: they must really create

        # --- the app holds the catalog (what "Đồng bộ kho ngay" would have sent)
        seed = [{"id": o["id"], "name": o["name"], "vi": "", "type": "Obj", "status": "ACTIVE", "avail": o["quantity"], "qty": o["quantity"],
                 "loc": o["location"], "sector": "Hanoi-GPI", "image_url": ""} for o in objects]
        st, r = api("/api/catalog/sync", "POST", {"objects": seed})
        check(st == 200 and r["usable"] == len(objects), f"the app took the catalog ({st}, {r.get('usable')} usable)")

        # --- nothing ready: the panel says so, and the view button does not even ask SM
        pg.click("#bb-btn")
        pg.wait_for_function("document.querySelector('#bb-ready-count').innerText.includes('Chưa có hộp nào sẵn sàng')", timeout=20000)
        check(True, "no ready box: the panel says so")
        pg.click("#bb-view"); idle(pg)
        check("Chưa có hộp nào sẵn sàng đưa lên SM" in log_text(pg) and not sm_requests, "the view button on an empty list says so and sends nothing to SM")
        check(pg.locator("#bb-confirm").is_hidden(), "the confirm button stays hidden")

        # --- an empty SM: the script says SM has no boxes, and the app accepts that on purpose
        pg.click("#bb-boxes"); idle(pg)
        check(wait_log(pg, "Box builder nhận 0 hộp: 0 đang dùng (giữ 0 món), 0 đã trả nên bỏ qua."), "SM with no boxes: the app accepts the empty list and the summary says so")

        # --- two approved boxes appear in the count and the list
        id_a = make_box("Box A1", "M8-01", ALL_IDS[0])             # the anchor is an object id
        id_b = make_box("Box B2", "M8-02", "Bàn thí nghiệm")       # the anchor is a name, so there is no anchor id
        pg.click("#bb-refresh")
        pg.wait_for_function("document.querySelector('#bb-ready-count').innerText.startsWith('2 hộp sẵn sàng đưa lên SM')", timeout=20000)
        check(True, "the panel counts 2 boxes ready to go to SM")
        pend = pending(); ta, tb = pend[id_a], pend[id_b]
        lst = panel_text(pg, "#bb-ready-list")
        check(f"{ta['title']}: 6 món" in lst and f"{tb['title']}: 6 món" in lst, f"the list shows each title and its number of objects ({ta['title']}; {tb['title']})")
        check(not sm_posts, "nothing was written to SM so far")

        # --- THE INTERLOCK: the file as it ships (WRITES_ENABLED false) cannot create, whatever is pressed. Two approved boxes are ready right now.
        def writes_since(i, j):
            """What went to SM and to the app between two marks: anything that is not a GET to SM, and every report of a created box."""
            return [(m, p) for m, p, _, _ in sm_requests[i:] if m != "GET"], [(m, p) for m, p, _ in app_requests[j:] if p.endswith("/pushed")]
        def force_confirm(page):
            page.evaluate("() => { const b = document.getElementById('bb-confirm'); b.hidden = false; b.disabled = false; b.click(); b.dispatchEvent(new MouseEvent('click', {bubbles: true})); }")
            time.sleep(1.5)
        ro = open_sm(SCRIPT)
        ro.click("#bb-btn")
        check("Ghi vào SM: TẮT" in ro.inner_text("#bb-panel") and "(v0.3.1)" in ro.inner_text("#bb-panel"), "the shipped script's panel says 'Ghi vào SM: TẮT' and its version")
        check("Bản này chỉ đọc" in ro.inner_text("#bb-panel") and "Xem hộp sẽ tạo" == ro.inner_text("#bb-view"), "the panel text and the view button promise no create")
        mark_sm, mark_app = len(sm_requests), len(app_requests)
        ro.click("#bb-view"); idle(ro)
        check(wait_log(ro, "Bản này chỉ đọc: xem được hộp sẽ tạo, chưa tạo được hộp trong SM."), "view: says the build is read-only")
        check("Sẽ có 2 hộp cần tạo" in log_text(ro) and "Xác nhận" not in log_text(ro), "view: still shows what would be made (2 boxes) and never asks for a confirm")
        check(ro.locator("#bb-confirm").is_hidden(), "view: the confirm button stays hidden")
        force_confirm(ro)
        w_sm, w_app = writes_since(mark_sm, mark_app)
        check(not w_sm and not w_app and not sm_posts and len(sm.list_boxes()) == 0, f"a forced click on the hidden confirm button: zero writes to SM and no /pushed ({w_sm}, {w_app})")
        check(len(sm_requests) > mark_sm and all(m == "GET" for m, _, _, _ in sm_requests[mark_sm:]), "the view still read SM (GET only), so the preview is real")
        # each guard blocks on its own: take the view guard out so a plan (and the button) exists, then also the create flow's guard
        no_view = without(SCRIPT, "create: [], skip };", "create, skip };")
        v1 = open_sm(no_view); v1.click("#bb-btn"); mark_sm, mark_app = len(sm_requests), len(app_requests)
        v1.click("#bb-view"); idle(v1)
        check(v1.locator("#bb-confirm").is_visible(), "with the view guard removed the confirm button shows (so the next guard is what is tested)")
        v1.click("#bb-confirm"); check(wait_log(v1, "Bản này chỉ đọc: chưa tạo được hộp trong SM."), "the create flow's own first-line guard stops the click"); idle(v1)
        w_sm, w_app = writes_since(mark_sm, mark_app)
        check(not w_sm and not w_app and not sm_posts, "no write and no /pushed with only the create-flow guard left")
        no_flow = without(no_view, "    if (!WRITES_ENABLED) throw new Plain(READ_ONLY_CREATE);\n")
        v2 = open_sm(no_flow); v2.click("#bb-btn"); mark_sm, mark_app = len(sm_requests), len(app_requests)
        v2.click("#bb-view"); idle(v2); v2.click("#bb-confirm")
        check(wait_log(v2, "SM không tạo được. Bản này chỉ đọc: chưa tạo được hộp trong SM."), "with the first two guards removed, the guard in front of the create request stops it"); idle(v2)
        w_sm, w_app = writes_since(mark_sm, mark_app)
        check(not w_sm and not w_app and not sm_posts and len(sm.list_boxes()) == 0, "the create request never left the tab: zero writes, no /pushed, nothing in SM")
        for page in (ro, v1, v2): page.close()

        # --- reading SM's boxes: decoys carry people and history that must never reach the app
        d_assigned = sm.create_box({"title": "Decoy assigned", "contents": lines(spare(2)), "assignee": "Tran Thi Assignee", "borrow_now": False})
        d_held = sm.create_box({"title": "Decoy borrowed", "contents": lines(spare(1)), "borrow_now": True, "borrower": "Le Van Holder"})
        returned("Decoy returned", spare(1))
        before = len(sm_requests); n_posts = len(app_requests); pg.click("#bb-boxes"); idle(pg)
        check(wait_log(pg, "Box builder nhận 3 hộp: 2 đang dùng (giữ 3 món), 1 đã trả nên bỏ qua."), "the boxes button reads 3 boxes over two pages and says what the app kept")
        new = sm_requests[before:]
        check(all(m == "GET" and p == "/api/boxes" for m, p, _, _ in new) and len(new) == 2, f"two GET requests to SM boxes, no other ({len(new)})")
        check(sm_queries[-2].get("limit") == ["500"] and sm_queries[-1].get("page") == ["2"], "it asks for 500 at a time and follows to page 2")
        posted = [json.loads(d) for m, p, d in app_requests[n_posts:] if m == "POST" and p == "/api/sm/boxes/sync"]
        sent = json.dumps(posted)
        check(len(posted) == 1 and set(posted[0]) == {"boxes"} and len(posted[0]["boxes"]) == 3, "one post to the app, with a list of boxes and nothing else")
        check(all(set(x) == {"box_id", "title", "status", "status_label", "created_at", "contents"} for x in posted[0]["boxes"]), "each box carries exactly the allowed fields")
        check(all(set(c) == {"object_id", "quantity"} for x in posted[0]["boxes"] for c in x["contents"]), "each content line carries only object_id and quantity")
        check(not any(x in sent for x in SECRETS + ("assignee", "holder", "history", "created_by", "people", "receiver", "returned_by")), "no person, holder or history was sent to the app")
        con = sqlite3.connect(Path(DB).as_uri() + "?mode=ro", uri=True)       # a file: address that also works for a Windows path
        dump = json.dumps([list(r) for t in ("sm_boxes", "sm_box_items") for r in con.execute(f"SELECT * FROM {t}")] +
                          [list(r) for r in con.execute("SELECT v FROM catalog_snapshot WHERE k='sm_boxes'")])
        con.close()
        report = json.dumps(api("/api/sm/boxes/report", token=False)[1])
        check(not any(x in dump or x in report for x in SECRETS), "the app's sm_boxes tables and report hold no person's name")
        rep = json.loads(report)
        check(rep["boxes"] == 2 and rep["objects_held"] == 3, f"the app holds 2 active boxes and 3 objects ({rep['boxes']}, {rep['objects_held']})")

        # --- step one of the push: look, create nothing
        pg.click("#bb-view"); idle(pg)
        check("Chưa tạo gì" in log_text(pg) and not sm_posts and len(sm.list_boxes()) == 3, "the view button creates nothing in SM")
        check(pg.locator("#bb-confirm").is_visible() and pg.inner_text("#bb-confirm") == "Xác nhận tạo 2 hộp", "a separate confirm button appears: 'Xác nhận tạo 2 hộp'")

        # --- step two: pressing confirm twice in the same instant creates each box once
        pg.evaluate("() => { const b = document.getElementById('bb-confirm'); b.click(); b.click(); }")
        check(wait_log(pg, "Xong: tạo 2 hộp, lỗi 0, bỏ qua 0."), "confirm: both boxes were created")
        idle(pg)
        ok = made_by_script()
        check(len(sm_posts) == 2 and len(ok) == 2, f"exactly one POST to SM per box ({len(sm_posts)})")
        by_title = {x["title"]: x for x in sm.list_boxes()}
        for tb_, pid in ((ta, id_a), (tb, id_b)):
            got = by_title.get(tb_["title"])
            check(got is not None and got["note"] == f"Created by Box builder #{pid}", f"SM has '{tb_['title']}' with the note naming the builder box")
            check(got is not None and got["anchor_object_id"] == (tb_["anchor_object_id"] or None), f"its anchor is {tb_['anchor_object_id'] or 'none'}")
            check(got is not None and [(c["object_id"], c["quantity"]) for c in got["contents"]] == [(c["object_id"], c["quantity"]) for c in tb_["contents"]], "its contents match the app's box")
            check(got is not None and got["status"] == "created" and got["holder"] is None and got["assignee"] is None, "it is a plain created box: not assigned, not borrowed")
        check(all(set(p["body"]) == {"title", "anchor_object_id", "note", "contents", "borrow_now"} and p["body"]["borrow_now"] is False for p in sm_posts), "every create sent exactly the agreed fields with borrow_now false")
        st_ = api("/api/state", token=False)[1]["boxes"]
        pushed = {x["id"]: x["pushed"] for x in st_}
        check(pushed[id_a] and pushed[id_b] and {pushed[id_a]["sm_box_id"], pushed[id_b]["sm_box_id"]} == {p["id"] for p in ok}, "the app marks both boxes pushed, with SM's new ids")
        check(api("/api/queue", token=False)[1]["ready"] == [], "the app's queue has no ready box left")
        told = [(m, p) for m, p, _ in app_requests if p.endswith("/pushed")]
        check(sorted(told) == sorted([("POST", f"/api/boxes/{id_a}/pushed"), ("POST", f"/api/boxes/{id_b}/pushed")]), "the app was told about each box once")
        check("Chưa có hộp nào sẵn sàng đưa lên SM" in panel_text(pg, "#bb-ready-count") and pg.locator("#bb-confirm").is_hidden(), "the panel reloads the list: nothing ready, confirm hidden")
        lt = log_text(pg)
        check(lt.count('đã tạo trong SM') == 2 and ta["title"] in lt and tb["title"] in lt, "one result line per box")

        # --- a second press creates nothing
        pg.evaluate("document.getElementById('bb-confirm').click()"); time.sleep(1.5)
        check(len(sm_posts) == 2 and len(sm.list_boxes()) == 5, "a second press of confirm creates nothing")
        pg.click("#bb-view"); idle(pg)
        check(len(sm_posts) == 2 and "Chưa có hộp nào sẵn sàng" in log_text(pg), "viewing again finds nothing to create")

        # --- titles: one in SM already (skipped), one in SM only as returned (created), one in SM with the same objects (app drops it)
        id_c = make_box("Box C3", "M8-03", "Kệ lớn")
        id_d = make_box("Box D4", "M8-04", "Tủ nhựa")
        id_g = make_box("Box G5", "M8-05", "Giá sắt")
        pend = pending(); tc, td, tg = pend[id_c], pend[id_d], pend[id_g]
        sm.create_box({"title": tc["title"], "contents": lines(spare(2)), "borrow_now": False})                       # same title, other objects
        returned(td["title"], spare(1))                                                                                # same title, but already returned
        sm.create_box({"title": tg["title"], "contents": lines([c["object_id"] for c in tg["contents"][:2]]), "borrow_now": False})   # same title and objects
        pg.click("#bb-view"); idle(pg)
        lt = log_text(pg)
        check(f'Hộp "{tc["title"]}": đã có trong SM, bỏ qua.' in lt, "a ready box whose title is already in SM is reported and skipped")
        check(f'Hộp "{tg["title"]}": không còn sẵn sàng sau khi đọc SM' in lt, "a box whose objects SM already holds drops out and is reported")
        check(f'"{td["title"]}"' not in lt, "a box whose title exists only as a returned box is not skipped")
        check("(đã có trong SM, bỏ qua)" in panel_text(pg, "#bb-ready-list") and pg.inner_text("#bb-confirm") == "Xác nhận tạo 1 hộp", "the list marks it, and confirm offers just 1 box")
        n_before = len(sm_posts)
        pg.click("#bb-confirm"); check(wait_log(pg, "Xong: tạo 1 hộp, lỗi 0, bỏ qua 0."), "confirm creates only the box that was not in SM"); idle(pg)
        titles = [x["title"] for x in sm.list_boxes() if x["status"] != "deactivated"]
        check(len(sm_posts) == n_before + 1 and sm_posts[-1]["body"]["title"] == td["title"], "exactly one more POST, for the returned-title box")
        check(titles.count(tc["title"]) == 1 and titles.count(tg["title"]) == 1 and titles.count(td["title"]) == 1, "no duplicate title was made in SM")
        check(f"{tc['title']}" in panel_text(pg, "#bb-ready-list") and panel_text(pg, "#bb-ready-count").startswith("1 hộp sẵn sàng"), "the skipped box stays in the list for the person to deal with")

        # --- sign out between the two steps: the confirm step reads SM again and creates nothing
        id_e = make_box("Box E6", "M8-06", "Bàn sắt")
        id_f = make_box("Box F7", "M8-07", "Ghế cao")
        pend = pending(); te, tf = pend[id_e], pend[id_f]
        stock_oid = te["contents"][0]["object_id"]; stock_qty = sm._stock.pop(stock_oid)         # SM no longer knows one object of box E
        pg.click("#bb-view"); idle(pg)
        check(pg.inner_text("#bb-confirm") == "Xác nhận tạo 2 hộp", "two more boxes are ready to create (the skipped one stays skipped)")
        n_before = len(sm_posts); state["signed_in"] = False
        pg.click("#bb-confirm"); check(wait_log(pg, "Chưa đăng nhập SM"), "signed out before confirm: it says to sign in"); idle(pg)
        check(len(sm_posts) == n_before and pg.locator("#bb-confirm").is_hidden(), "and creates nothing, so the plan must be viewed again")
        pg.click("#bb-boxes"); idle(pg)
        check(wait_log(pg, "Chưa đăng nhập SM"), "the boxes button says to sign in as well")
        state["signed_in"] = True

        # --- one box SM refuses does not stop the next one
        pg.click("#bb-view"); idle(pg)
        pg.click("#bb-confirm"); check(wait_log(pg, "Xong: tạo 1 hộp, lỗi 1, bỏ qua 0."), "confirm: one created, one refused, one summary"); idle(pg)
        lt = log_text(pg)
        check(f'Hộp "{te["title"]}": SM không tạo được. SM từ chối: Món {stock_oid} không có trong kho.' in lt, "the refused box gets its own line with SM's reason")
        check(re.search(rf'Hộp "{re.escape(tf["title"])}": đã tạo trong SM \(MBX-\d+\) và báo cho Box builder\.', lt) is not None, "the other box still got created, with its own line")
        pushed = {x["id"]: x["pushed"] for x in api("/api/state", token=False)[1]["boxes"]}
        check(pushed[id_f] and not pushed[id_e], "the app marks only the box that was created")
        check(te["title"] not in [x["title"] for x in sm.list_boxes()], "SM holds nothing for the refused box")

        # --- the sign-in lapses in the middle of creating: it stops and says so
        sm._stock[stock_oid] = stock_qty
        id_h = make_box("Box H8", "M8-08", "Cái cân"); id_i = make_box("Box I9", "M8-09", "Cái thước")
        pg.click("#bb-view"); idle(pg)
        check(pg.inner_text("#bb-confirm") == "Xác nhận tạo 3 hộp", "the refused box is offered again, with two new ones")
        n_before = len(sm_posts); state["post_401"] = True
        pg.click("#bb-confirm"); check(wait_log(pg, "Dừng lại, 2 hộp còn lại chưa tạo."), "SM answers 401 to the create: it stops after the first box"); idle(pg)
        check(len(sm_posts) == n_before + 1 and "Chưa đăng nhập SM" in log_text(pg), "only one create was tried, and the message says to sign in")
        state["post_401"] = False
        pg.click("#bb-view"); idle(pg)
        pg.click("#bb-confirm"); check(wait_log(pg, "Xong: tạo 3 hộp, lỗi 0, bỏ qua 0."), "after signing in again, the same three boxes are created"); idle(pg)

        # --- messages from the app
        state["app_force"] = {"/api/sm/boxes/sync": 503}
        pg.click("#bb-boxes"); idle(pg)
        check(wait_log(pg, "chưa đặt SYNC_TOKEN"), "the app answers 503: the message says SYNC_TOKEN is not set")
        state["app_force"] = {}
        pg.fill("#bb-token", "not-the-token"); pg.click("#bb-boxes"); idle(pg)
        check(wait_log(pg, "Mã đồng bộ sai"), "wrong sync code: the message says so")
        pg.fill("#bb-token", TOKEN)

        # --- the sync button ends by reading SM's boxes
        pg.click("#bb-sync")
        check(wait_log(pg, "Hoàn tất.", timeout=180000) and "Xong. Box builder có" in log_text(pg), "the sync button finishes")
        active = [x for x in sm.list_boxes() if x["status"] != "deactivated"]
        check(f"Box builder nhận {len(sm.list_boxes())} hộp: {len(active)} đang dùng" in log_text(pg), "its last step reads SM's boxes and reports them")

        # --- the guarantees
        check(sm_requests and all(m == "GET" or (m == "POST" and p == "/api/boxes") for m, p, _, _ in sm_requests), "every request to SM was a GET, except the box creates")
        check({p for _, p, _, _ in sm_requests} <= {"/api/boxes", "/api/objects"}, f"SM was only asked for: {sorted({p for _, p, _, _ in sm_requests})}")
        created = made_by_script()
        check(len(created) == 7 and len({p["body"]["title"] for p in created}) == 7 and len(sm.list_boxes()) == 3 + 3 + 7, f"7 boxes were created, each title once ({len(created)})")
        check(len(created) > 0 and "const WRITES_ENABLED = true;" in SCRIPT_WRITE and OFF not in SCRIPT_WRITE, "negative control: with only the switch turned on, the same scenarios really create boxes, so the switch is what blocks")
        check(len([x for x in sm_posts if x["status"] != 200]) == 2, "two creates did not succeed: the refused one and the 401")
        check(not any(TOKEN in json.dumps(h) or TOKEN in body or "not-the-token" in json.dumps(h) or "not-the-token" in body for _, _, h, body in sm_requests),
              "the sync code never reached SM")
        check(not any(w in SCRIPT for w in ('"PUT"', '"PATCH"', '"DELETE"', "/transition", "handover")) and len(re.findall(r'method: "POST"', SCRIPT)) == 1,
              "the script holds no other kind of write to SM")
        pg.screenshot(path=os.path.join(tempfile.gettempdir(), "box-builder-push-panel.png"))
        b.close()
finally:
    stop_server(srv, srv_log, tmp)

check(not errs, f"no script errors {errs[:2]}")
print("FAILED" if fails else "ALL PUSH TOOL CHECKS PASSED")
sys.exit(1 if fails else 0)

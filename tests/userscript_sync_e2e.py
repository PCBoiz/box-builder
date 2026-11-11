"""Browser test of integrations/box_builder_sm.user.js against a pretend SM page and the real app (own port, own database).
SM is never contacted: sm.config.inc is answered by this script. Run: python -I tests/userscript_sync_e2e.py (needs playwright + chromium;
set E2E_BROWSER=firefox to use Firefox). Works on Windows and Linux.
What it covers: the panel and its failure messages, the catalog sync (which objects arrive, the Vietnamese names, the picture links),
the lifetime use and creation date that ride along (read from SM in several shapes, since the real shape of `_usage_count` is not verified),
the plain Vietnamese status lines after a sync, the box read, the script on SM's /object page, and that SM only ever got GET requests.
Since 0.4.0 also: a sleeping server (proxy 502/503/524 or an HTML page) is "waking", never "SYNC_TOKEN not set", reads are tried 4 times 2, 4 and 8 seconds
apart and writes once, every call to the app is anonymous with redirect "error", the update line and the Box builder line of the panel, and the optional
auto refresh (boxes every 10 minutes, catalog every 60, never over a running run, off when signed out), driven by Playwright's fake clock."""
import csv, json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
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
PORT = int(os.environ.get("E2E_PORT") or free_port())
BROWSER = os.environ.get("E2E_BROWSER", "chromium")
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "tok-sync-e2e"
SCRIPT = read_text(ROOT, "integrations", "box_builder_sm.user.js")
VERSION = "0.4.0"

# ---- the pretend SM: objects shaped like the real /api/objects (no Vietnamese name), plus another sector and a hostile picture link
with open(os.path.join(ROOT, "data", "catalog.csv"), encoding="utf-8-sig", newline="") as fh:
    rows = list(csv.reader(fh))[1:]
usable = [r for r in rows if r[6].strip() == "ACTIVE" and r[2].strip() and r[7].strip().isdigit() and int(r[7]) > 0]
S3 = "https://configint-main.s3.amazonaws.com/scenario-manager/cache/objects/O{}.jpg?X-Amz-Signature=abc123"
objects = []
for r in usable:
    n = r[0].strip().zfill(6)
    objects.append({"id": f"O{n}", "iid": int(n), "name": r[1], "type": "Obj", "status": "ACTIVE", "quantity": int(r[7]), "loan_reserved": 0,
                    "transfer_reserved": 0, "location": r[5], "sector": "Hanoi-GPI", "image_url": S3.format(n)})
objects[1]["image_url"] = "https://evil.example.com/steal.jpg"                    # must not survive
for i in range(100):                                                              # another sector: must not be stored
    objects.append({"id": f"O9{i:05d}", "iid": 900000 + i, "name": f"ZZSector4F-{i}", "type": "Obj", "status": "ACTIVE", "quantity": 3,
                    "location": "Z", "sector": "Hanoi-4F", "image_url": "", "_usage_count": 9999, "created_at": "2020-01-01T00:00:00Z"})

# ---- lifetime use and creation date in the shapes SM might use. The real shape of `_usage_count` is NOT verified, so the script has to cope with
# ---- all of these. EXPECT maps an object id to what must reach the app: (uses, created). Every other object must arrive with both null.
MISSING = object()
EXPECT = {}
def shape(i, usage=MISSING, dates=None, uses=None, created=None):
    o = objects[i]
    if usage is not MISSING: o["_usage_count"] = usage
    o.update(dates or {})
    EXPECT[o["id"]] = (uses, created)
EPOCH_ISO = "2023-11-14T22:13:20.000Z"                                            # 1700000000 seconds since 1970: the script sends a number date as an ISO text
LENDER = "Hoang Thi Lender"                                                       # a name inside SM's usage field: it must never leave the SM tab
shape(10, 1587, {"created_at": "2024-03-05T10:22:11.123456+00:00"}, 1587, "2024-03-05T10:22:11.123456+00:00")      # a plain number; an ISO date
shape(11, {"borrows": 1587, "borrow_units": 1611, "borrows_undercounted": False, "last_borrower": LENDER}, {"createdAt": 1700000000}, 1587, EPOCH_ISO)   # an object like a box's; epoch seconds
shape(12, {"borrow_units": 1611, "borrows_undercounted": True, "count": "320"}, {"created": "2023-11-02"}, 320, "2023-11-02")        # a named key wins over the first number; a numeric string inside
shape(13, "42", {"date_created": "05/03/2024"}, 42, "05/03/2024")                                                                      # a numeric string
shape(14, {"label": "heavy", "n": 7, "m": 9}, {"first_seen": "2022-01-01T00:00:00Z"}, 7, "2022-01-01T00:00:00Z")                      # no known key: the first number
shape(15, {"count": 0}, {"created_at": "", "created": "2021-06-30"}, 0, "2021-06-30")                                                   # a real zero stays 0; an empty date falls through
shape(16, None, {"created_at": None})                                                                                                    # null
shape(17, "many", {"created_at": {"x": 1}, "created": 5})                                                                                # text, an object date, a number that is no date
shape(18, [1, 2], {"created_at": "unknown"})                                                                                             # a list; a text without a digit
shape(19, True, {"created_at": True})                                                                                                    # a boolean
shape(20, -3)                                                                                                                            # a negative number
shape(21, {"borrows": "n/a", "units": None, "flag": False})                                                                              # an object with no number in it
shape(22)                                                                                                                                # neither field
shape(23, MISSING, {"created": "1700000000"}, None, EPOCH_ISO)                                                                           # epoch seconds written as a text of digits
shape(24, MISSING, {"created_at": 1700000000000}, None, EPOCH_ISO)                                                                       # epoch milliseconds
for i in (30, 31):                                                                # decoy fields the script must never read or send
    objects[i].update(last_borrower="Hoang Thi Lender", assignee="Tran Thi Assignee", holder="Le Van Holder")
READABLE_USES = sum(1 for u, _ in EXPECT.values() if u is not None)               # 6: 1587, 1587, 320, 42, 7 and a real 0
READABLE_DATES = sum(1 for _, c in EXPECT.values() if c is not None)              # 8

HAS_VI = {"on": False}
# two pretend SM boxes shaped like the real /api/boxes, with fields the app must never receive
SM_BOXES = [
    {"box_id": "SMB-1", "title": "M8-01 - A1", "status": "assigned", "status_label": "Assigned", "created_at": time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() + 7 * 3600)),
     "note": "Created by Box builder [BB 2026-01-01 #1] private-note", "next_actions": ["collected", "created"], "anchor_object_id": None,       # SM's own shape: a note, the next statuses, Vietnam time with no zone
     "assignee": "Tran Thi Assignee", "holder": None, "created_by_name": "Nguyen Van Creator", "people": ["Pham Van People"], "history": [{"to": "assigned"}],
     "contents": [{"object_id": objects[2]["id"][1:], "quantity": 1, "name": "x", "available": 3, "short": False},
                  {"object_id": objects[3]["id"][1:], "quantity": 2, "name": "y", "available": 3, "short": False}]},
    {"box_id": "SMB-2", "title": "M8-02 - B2", "status": "deactivated", "status_label": "Return everything", "created_at": time.strftime("%Y-%m-%d %H:%M", time.gmtime(time.time() - 86400 + 7 * 3600)),
     "note": "", "next_actions": [], "assignee": None, "holder": "Le Van Holder", "contents": [{"object_id": objects[4]["id"][1:], "quantity": 1}]},
]
tmp = tempfile.mkdtemp()
env = clean_env(DB_PATH=os.path.join(tmp, "s.db"), CATALOG_PATH=os.path.join(ROOT, "data", "catalog.csv"), SYNC_TOKEN=TOKEN)
srv, srv_log = start_server(ROOT, env, PORT, tmp)

def switch_on(name):
    """Switch an automation on through the app's own API, so a run does not depend on the app's default (sm_queue is off by default)."""
    req = urllib.request.Request(BASE + f"/api/automations/{name}", method="PUT", data=json.dumps({"enabled": True}).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r: assert r.status == 200
switch_on("sm_queue")

fails, errs = [], []
sm_requests, app_requests, app_posts, sm_box_queries = [], [], [], []
state = {"signed_in": True, "old_server": False, "usage_mode": "mixed", "queue": {}, "preflight_version": VERSION}
app_calls = []                                                                     # every request the script made to the app, with the options it passed
HTML_503 = {"status": 503, "responseText": "<!doctype html><html><body>Service is waking up</body></html>"}
HTML_502 = {"status": 502, "responseText": "<html><body>Bad gateway</body></html>"}
def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

fixture_used = {}
def gm(opts):
    """Tampermonkey's request: the script's calls to the app land here. `queue` holds canned answers for a path (None lets one through to the app).
    /api/preflight exists only once the server work is merged: until the real app answers 404 for it, it is answered here with what the real
    app would say for this database, so the same test passes before and after the merge."""
    parts = urlsplit(opts["url"]); path = parts.path
    app_requests.append((opts["method"], path)); app_calls.append(opts)
    if opts["method"] == "POST": app_posts.append((path, opts.get("data") or ""))
    canned = state["queue"].get(path)
    if canned:
        nxt = canned.pop(0)
        if nxt is not None: return nxt
    if state["old_server"] and path == "/api/sync/check":
        return {"status": 404, "responseText": '{"detail":"Not Found"}'}
    req = urllib.request.Request(opts["url"], method=opts["method"], headers=opts.get("headers") or {}, data=(opts.get("data") or "").encode() or None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return {"status": r.status, "responseText": r.read().decode()}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if e.code == 404 and path == "/api/preflight":
            fixture_used[path] = fixture_used.get(path, 0) + 1
            fx = {"ok": True, "login_enforced": False, "sm_queue": True, "catalog_age_min": 2, "sm_snapshot_age_min": 1, "ready_boxes": 0,
                  "expected_script_version": state["preflight_version"]}
            return {"status": 200, "responseText": json.dumps(fx)}
        return {"status": e.code, "responseText": body}
    except Exception as e: return {"error": str(e)}

def note(req):
    sm_requests.append((req.method, urlsplit(req.url).path, dict(req.headers)))

def as_served(o, i):
    """What SM sends for object i in the current mode: the mixed shapes above, none at all, numbers everywhere, or a shape nobody can read."""
    mode = state["usage_mode"]
    if mode == "mixed": return o
    o = {k: v for k, v in o.items() if k not in ("_usage_count", "created_at", "createdAt", "created", "date_created", "first_seen")}
    if mode == "numbers": o.update(_usage_count=i % 7 * 100 if i % 5 else {"borrows": 1587 if i == 0 else i % 700}, created_at="2024-01-02T03:04:05Z")
    elif mode == "unreadable": o.update(_usage_count={"label": "heavy", "flag": True}, created_at="2024-01-02T03:04:05Z")
    return o                                                                       # mode "missing": neither field is there

def sm_objects(route):
    req = route.request
    note(req)
    if not state["signed_in"]:
        return route.fulfill(status=200, content_type="text/html", body="<html><body>Login</body></html>")
    page = int(parse_qs(urlsplit(req.url).query).get("page", ["1"])[0])
    chunk = [as_served(o, (page - 1) * 30 + j) for j, o in enumerate(objects[(page - 1) * 30: page * 30])]
    chunk = [dict(o, name_vi=o["name"]) for o in chunk] if HAS_VI["on"] else chunk
    pages = -(-len(objects) // 30)
    route.fulfill(status=200, content_type="application/json", body=json.dumps(
        {"objects": chunk, "page": page, "total_items": len(objects), "total_pages": pages, "per_page": 30, "has_next": page < pages}))

def sm_boxes(route):
    req = route.request
    note(req)
    if not state["signed_in"]:
        return route.fulfill(status=200, content_type="text/html", body="<html><body>Login</body></html>")
    query = parse_qs(urlsplit(req.url).query)
    sm_box_queries.append(query)
    route.fulfill(status=200, content_type="application/json", body=json.dumps(
        {"count": len(SM_BOXES), "total": len(SM_BOXES), "page": 1, "page_size": 500, "has_next": False, "boxes": SM_BOXES}))

def sm_page(html):
    def handler(route):
        note(route.request)
        route.fulfill(status=200, content_type="text/html", body=html)
    return handler

def anything_else(route):
    """Every other address on the pretend SM is recorded and refused, so a stray request shows up in the guarantees below."""
    note(route.request)
    route.fulfill(status=404, content_type="application/json", body='{"detail":"not here"}')

HOME = "<html><body><h1>Scenario Manager</h1></body></html>"
USAGE_URL = "https://sm.config.inc/object?sort_by=usage&sort_dir=desc&page=1"
USAGE_PAGE = ('<html><body><h1>Objects</h1><div id="card"><b>LIFETIME USE:</b> 1587 borrows at least, 1611 items total, Heavily used</div></body></html>')

CLOCK0 = 1_900_000_000_000                                                        # the fake clock starts here (milliseconds): a day in 2030
def open_sm(browser, served=None, url="https://sm.config.inc/", html=HOME, clock=False, seed=None):
    """A signed-in SM tab with the sandbox for Tampermonkey's calls, and the script loaded the way Tampermonkey would (after the page).
    With clock=True the page runs on Playwright's fake clock (it keeps running, and a test can jump it); `seed` is what the script's storage already holds."""
    page = browser.new_context(viewport={"width": 1300, "height": 900}).new_page()
    page.on("pageerror", lambda e: errs.append(str(e)))
    if clock: page.clock.install(time=CLOCK0 // 1000)                                 # Playwright's Python wants epoch seconds here
    page.route("https://sm.config.inc/**", anything_else)                          # registered first, so it is asked last
    page.route("https://sm.config.inc/api/objects**", sm_objects)
    page.route("https://sm.config.inc/api/boxes**", sm_boxes)
    page.route(lambda u: u == url, sm_page(html))                                  # this exact address (a glob would read ? and * in it)
    page.expose_function("__gm", gm)
    page.goto(url)
    text = served or SCRIPT
    head = re.search(r"^// ==UserScript==\n.*?^// ==/UserScript==", text, flags=re.S | re.M).group(0)
    if not served: head = head.replace("// @connect      localhost\n", "// @connect      localhost\n// @downloadURL  none\n", 1)          # as the app serves it: one @downloadURL none
    page.add_script_tag(content=SHIM + "window.GM_info = {scriptMetaStr: %s, scriptWillUpdate: true};" % json.dumps(head)     # the real flag says true by default: the panel must not use it
                        + ("Object.assign(window.__store, %s);" % json.dumps(seed) if seed else ""))
    page.add_script_tag(content=text)
    return page

SHIM = """
window.__store = {};
window.__delays = [];                                        // the waits it asked for between tries (shortened here so the test is fast)
window.GM_getValue = (k, d) => (k in window.__store ? window.__store[k] : d);
window.GM_setValue = (k, v) => { window.__store[k] = v; };
window.GM_addStyle = (css) => { const s = document.createElement('style'); s.textContent = css; document.head.append(s); return s; };
const realTimeout = window.setTimeout.bind(window);
window.setTimeout = (f, ms, ...a) => { if (ms === 2000 || ms === 4000 || ms === 8000) { window.__delays.push(ms); ms = 15; } return realTimeout(f, ms, ...a); };
const callApp = (o) => window.__gm({method: o.method || 'GET', url: o.url, headers: o.headers || {}, data: o.data || null, anonymous: o.anonymous, redirect: o.redirect}).then((r) => {
  if (r.error) { o.onerror && o.onerror({error: r.error}); } else { o.onload({status: r.status, responseText: r.responseText}); }
});
window.__held = [];                                          // while __hold is true the script's calls to the app wait here, so a run stays busy
window.GM_xmlhttpRequest = (o) => { if (window.__hold) { window.__held.push(o); return; } callApp(o); };
window.__release = () => { window.__hold = false; window.__held.splice(0).forEach(callApp); };
"""

def log_text(pg): return pg.inner_text("#bb-log")
def wait_log(pg, needle, timeout=60000):
    try: pg.wait_for_function("n => document.querySelector('#bb-log').innerText.includes(n)", arg=needle, timeout=timeout)
    except Exception: pass
    return needle in log_text(pg)
def fill(pg, app=None, token=None, sectors=None):
    if app is not None: pg.fill("#bb-app", app)
    if token is not None: pg.fill("#bb-token", token)
    if sectors is not None: pg.fill("#bb-sectors", sectors)
def run_sync(pg):
    """Press the sync button and wait for the whole run (boxes step included). Returns the log and the rows the app was sent."""
    before = len(app_posts); pg.click("#bb-sync")
    done = wait_log(pg, "Hoàn tất.", timeout=180000)
    sent = [json.loads(d)["objects"] for p, d in app_posts[before:] if p == "/api/catalog/sync"]
    return done, log_text(pg), (sent[-1] if sent else None)
def app_item(oid):
    digits = oid[1:]
    return next((o for o in json.load(urllib.request.urlopen(BASE + f"/api/objects?q={digits}"))["items"] if o["id"] == digits), None)

ROW_KEYS = {"id", "name", "vi", "type", "status", "avail", "qty", "loc", "sector", "image_url", "uses", "created"}
PEOPLE = ("Hoang Thi Lender", "Tran Thi Assignee", "Le Van Holder", "Nguyen Van Creator", "Pham Van People")

try:
    with sync_playwright() as pw:
        b = getattr(pw, BROWSER).launch()
        pg = open_sm(b)
        pg.wait_for_selector("#bb-btn")
        check(pg.locator("#bb-panel").is_hidden(), "the panel starts closed, only a small button shows")
        pg.evaluate("document.getElementById('bb-btn').remove(); document.getElementById('bb-panel').remove()")     # the page redraws its body
        pg.wait_for_selector("#bb-btn", timeout=8000)
        check(pg.locator("#bb-btn").is_visible(), "if the page removes the button, the script puts it back")
        pg.click("#bb-btn")
        check(pg.locator("#bb-panel").is_visible() and "chỉ đọc" in pg.inner_text("#bb-panel").lower(), "the panel opens and says it only reads from SM")
        check("Tự cập nhật: TẮT" in pg.inner_text("#bb-panel"), "the panel reads the header of the installed copy: @downloadURL none and no @updateURL, so 'Tự cập nhật: TẮT' (GM_info says scriptWillUpdate true)")

        # --- every way a check can fail says what to do
        pg.click("#bb-check"); check(wait_log(pg, "Chưa nhập địa chỉ Box builder"), "no address: asks for one")
        fill(pg, app="http://127.0.0.1:9", token=TOKEN); pg.click("#bb-check")
        check(wait_log(pg, "Không kết nối được tới http://127.0.0.1:9"), "wrong address: says it cannot connect and what to check")
        check(pg.evaluate("window.__delays") == [2000, 4000, 8000] and len([1 for m, p in app_requests if p == "/api/config"]) == 4, "a read that cannot connect is tried 4 times, 2, 4 and 8 seconds apart, then gives up")
        fill(pg, app=BASE, token="not-the-code"); pg.click("#bb-check")
        check(wait_log(pg, "Mã đồng bộ sai") and "đã đăng nhập" in log_text(pg), "wrong code: says so, after confirming SM and the app answered")
        state["signed_in"] = False; fill(pg, token=TOKEN); pg.click("#bb-check")
        check(wait_log(pg, "Chưa đăng nhập SM"), "not signed in to SM: says to sign in")
        state["signed_in"] = True; pg.click("#bb-check")
        check(wait_log(pg, "Hoàn tất."), "all good: the check finishes")
        t = log_text(pg)
        check(f"Script Box builder v{VERSION} đang chạy trên sm.config.inc" in t, "the check first says the script is running, and its version")
        check(re.search(r"@version\s+(\S+)", SCRIPT).group(1) == VERSION and f'const VERSION = "{VERSION}";' in SCRIPT, "the header and the VERSION constant agree")
        check(f"v{VERSION}" in pg.inner_text("#bb-panel"), "the panel title shows the version")
        check("Mã đúng" in t and "SM không trả tên tiếng Việt" in t and "file CSV" in t, "good check: code accepted, and it explains the Vietnamese names come from the CSV")

        # --- the sync itself (the mixed shapes of lifetime use and creation date)
        done, t, sent = run_sync(pg)
        check(done and "Xong. Box builder có" in t, "sync finishes, boxes step included")
        st = json.load(urllib.request.urlopen(BASE + "/api/state"))
        check(st["catalog"]["source"] == "live", f"the app now uses the live catalog ({st['catalog']['source']})")
        check(st["catalog"]["usable"] == len(usable), f"every usable object arrived: {st['catalog']['usable']} of {len(usable)}")
        check(st["catalog"]["received"] == len(usable), "the other sector was filtered out before sending")
        other = json.load(urllib.request.urlopen(BASE + "/api/objects?q=ZZSector4F"))
        check(other["total"] == 0, "objects from another sector are not in the app")
        n0, n1 = objects[0]["id"][1:], objects[1]["id"][1:]
        got0 = json.load(urllib.request.urlopen(BASE + f"/api/objects?q={n0}"))["items"]
        got1 = json.load(urllib.request.urlopen(BASE + f"/api/objects?q={n1}"))["items"]
        a0 = next((o for o in got0 if o["id"] == n0), None); a1 = next((o for o in got1 if o["id"] == n1), None)
        check(a0 is not None and a0["img"].startswith("https://configint-main.s3.amazonaws.com/") and "X-Amz-Signature=abc123" in a0["img"],
              "a signed SM picture link is kept")
        check(a1 is not None and a1["img"] == "", "a picture link on a foreign host is dropped by the app")
        first = [a0, a1]
        check(all(o["vi"] for o in first), "objects got their Vietnamese names from the app's CSV")
        check("Lần đồng bộ gần nhất" in pg.inner_text("#bb-panel"), "the panel shows when the last sync was")

        # --- lifetime use and creation date: what the script sends to the app
        total = len(usable)
        by_id = {r["id"]: r for r in sent or []}
        check(sent is not None and len(sent) == total, f"the catalog post carries every row of the sector ({len(sent or [])} of {total})")
        check(all(set(r) == ROW_KEYS for r in sent or []), "each row carries exactly the agreed fields, `uses` and `created` included, and nothing raw from SM")
        check(all(r["uses"] is None or type(r["uses"]) is int for r in sent or []) and all(r["created"] is None or isinstance(r["created"], str) for r in sent or []),
              "`uses` is a whole number or null, `created` a text or null")
        shapes = [("a plain number", 10), ("an object with borrows (and a name inside it); epoch seconds", 11), ("a named key beating the first number", 12), ("a numeric string", 13),
                  ("an object with no known key: the first number", 14), ("an object holding a real zero", 15), ("epoch seconds as a text of digits", 23), ("epoch milliseconds", 24)]
        for what, i in shapes:
            oid = objects[i]["id"]; want = EXPECT[oid]
            check(oid in by_id and (by_id[oid]["uses"], by_id[oid]["created"]) == want, f"uses and created for {what}: {want} (got {(by_id.get(oid) or {}).get('uses', '?')}, {(by_id.get(oid) or {}).get('created', '?')})")
        for what, i in (("null", 16), ("a text that is no number", 17), ("a list and a text without a digit", 18), ("a boolean", 19), ("a negative number", 20), ("an object with no number", 21), ("neither field", 22)):
            oid = objects[i]["id"]
            check(oid in by_id and by_id[oid]["uses"] is None and by_id[oid]["created"] is None, f"unknown stays null, never 0 ({what})")
        known = {o["id"] for o in objects[10:25]}
        check(all(r["uses"] is None and r["created"] is None for r in sent or [] if r["id"] not in known), "an object SM sent nothing for arrives with both null")
        check(not any(r["uses"] == 9999 for r in sent or []) and not any(o["sector"] == "Hanoi-4F" for o in sent or []), "the other sector's usage never left the SM tab")
        check(not any(x in json.dumps(sent) for x in PEOPLE) and "_usage_count" not in json.dumps(sent), "no person's name and no raw `_usage_count` went to the app")
        # the status lines: plain Vietnamese, what could be read, no invented 0
        check(f"Số lần dùng: đọc được ở {READABLE_USES} trên {total} món và đã gửi kèm (nhiều nhất 1587 lần). {total - READABLE_USES} món còn lại để trống, không ghi là 0." in t,
              f"status line: lifetime use read for {READABLE_USES} of {total}, the rest left empty and not written as 0")
        check(f"Ngày tạo: đọc được ở {READABLE_DATES} trên {total} món, {total - READABLE_DATES} món còn lại để trống." in t, f"status line: creation date read for {READABLE_DATES} of {total}")
        check(not any(c in t for c in ("—", "!")), "the status lines hold no em dash and no exclamation mark")
        # read back through the app's own API: the object list returns `uses` and `created` (a UTC day) for what was sent
        item = app_item(objects[10]["id"])
        if item is not None and "uses" in item:
            back = {i: app_item(objects[i]["id"]) for i in range(10, 25)}
            check(all(back[i] is not None for i in back), "all the objects with a lifetime use or date are in the app's list")
            got_uses = {i: back[i]["uses"] for i in back}
            want_uses = {i: EXPECT[objects[i]["id"]][0] for i in back}
            check(got_uses == want_uses, f"the app's object list returns the uses it was sent (a real 0 stays 0, unknown stays empty): {got_uses}")
            days = {10: "2024-03-05", 11: "2023-11-14", 12: "2023-11-02", 14: "2022-01-01", 15: "2021-06-30", 23: "2023-11-14", 24: "2023-11-14"}
            got_days = {i: back[i].get("created") for i in days}
            check(got_days == days, f"and the creation day of each object that had a readable date: {got_days}")
            check(all(not back[i].get("created") for i in (16, 17, 18, 19, 20, 21, 22)), "an object with no readable date has no creation day in the app")
            other_ids = [o for o in json.load(urllib.request.urlopen(BASE + "/api/objects?per=200"))["items"] if o["id"] not in {objects[i]["id"][1:] for i in back}]
            check(other_ids and all(o["uses"] is None for o in other_ids), "objects SM sent no usage for show as unknown, not 0")
        else:
            print("note the app's /api/objects does not return `uses` yet: only what the script sends was checked")

        # --- other ways SM may answer: no usage field at all, numbers for every object, and a shape nobody can read
        state["usage_mode"] = "missing"
        done, t, sent = run_sync(pg)
        check(done and sent is not None and len(sent) == total and all(r["uses"] is None and r["created"] is None for r in sent), "SM sends neither field: every row carries null, no 0 is made up")
        check(f"Số lần dùng: SM không gửi số nào đọc được cho {total} món, nên Box builder chưa có số này. Kho vẫn đồng bộ bình thường." in t, "no usage at all: the line says SM sent no readable number")
        check("Dạng SM gửi: không có trường _usage_count." in t and "gửi cho người phát triển" in t, "and it says what SM sent, and what to do: send it to the developer")
        check("Số lần dùng: đọc được" not in t and "Ngày tạo: SM không gửi ngày tạo nào đọc được nên để trống." in t, "it does not claim a number was read; the creation date says the same")
        check("lỗi của bạn" not in t and "bạn sai" not in t.lower(), "the wording does not blame the person")
        state["usage_mode"] = "unreadable"
        done, t, sent = run_sync(pg)
        check(done and sent is not None and all(r["uses"] is None for r in sent), "a usage field with no number in it gives null for every object")
        check("Dạng SM gửi: nhóm có label (chuỗi), flag (đúng hoặc sai)." in t and "SM không gửi số nào đọc được" in t, "the line shows the kinds and key names SM sent, never a value")
        check(f"Ngày tạo: đọc được ở cả {total} món." in t, "creation dates are all read in that case")
        state["usage_mode"] = "numbers"
        done, t, sent = run_sync(pg)
        top = max((r["uses"] for r in sent or [] if r["uses"] is not None), default=-1)
        check(done and sent is not None and all(type(r["uses"]) is int for r in sent), "numbers for every object: every row carries a whole number")
        check(f"Số lần dùng: đọc được ở cả {total} món và đã gửi kèm (nhiều nhất {top} lần)." in t and top == 1587, f"status line: read for all {total}, the largest is {top}")
        state["usage_mode"] = "mixed"

        # --- the last step of the sync reads SM's boxes: only the allowed fields go to the app, and the summary is plain
        check("Box builder nhận 2 hộp: 1 đang dùng (giữ 2 món), 1 đã trả nên bỏ qua." in t, "sync ends by reading SM's boxes and says what the app kept")
        box_posts = [json.loads(d) for p, d in app_posts if p == "/api/sm/boxes/sync"]
        check(len(box_posts) == 4 and all(len(x["boxes"]) == 2 for x in box_posts), "the boxes were posted to the app once per sync")
        sent_boxes = json.dumps(box_posts[0])
        check(all(set(b_) == {"box_id", "title", "status", "status_label", "created_at", "contents"} for b_ in box_posts[0]["boxes"]), "each box carries only the allowed fields")
        check(all(set(c) == {"object_id", "quantity"} for b_ in box_posts[0]["boxes"] for c in b_["contents"]), "each content line carries only object_id and quantity")
        check(not any(x in sent_boxes for x in ("Tran Thi Assignee", "Nguyen Van Creator", "Pham Van People", "Le Van Holder", "assignee", "holder", "history")), "no person or history left the SM tab")
        check(sm_box_queries and sm_box_queries[0].get("limit") == ["500"], "SM's boxes are asked for 500 at a time")
        rep0 = json.load(urllib.request.urlopen(BASE + "/api/sm/boxes/report"))
        check(rep0["boxes"] == 1 and rep0["objects_held"] == 2, f"the app holds the one active SM box and its two objects ({rep0['boxes']}, {rep0['objects_held']})")
        check("Chưa có hộp nào sẵn sàng đưa lên SM" in pg.inner_text("#bb-ready-count"), "the panel says no box is ready to go to SM")

        # --- the single button for the boxes read
        before = len(app_posts); pg.click("#bb-boxes")
        check(wait_log(pg, "Box builder nhận 2 hộp") and wait_log(pg, "Hoàn tất."), "the boxes button reads SM's boxes on its own")
        check(len(app_posts) == before + 1, "it posts once to the app")

        # --- an app too old to fill in Vietnamese names must not be fed rows without them
        state["old_server"] = True; before = len(app_requests); pg.click("#bb-sync")
        check(wait_log(pg, "chưa tự bù từ file CSV"), "old app + no Vietnamese names: refuses to send")
        check(not any(m == "POST" and p == "/api/catalog/sync" for m, p in app_requests[before:]), "nothing was posted in that case")
        state["old_server"] = False

        # --- when SM does send Vietnamese names they are used
        HAS_VI["on"] = True; pg.click("#bb-check")
        check(wait_log(pg, "SM trả tên tiếng Việt cho món"), "SM names in Vietnamese are recognised")

        # --- the script as the app serves it: the address is already filled in, nothing to type but the code
        served = urllib.request.urlopen(BASE + "/box_builder_sm.user.js?app=" + BASE, timeout=30).read().decode()
        check(re.search(r"@version\s+(\S+)", served).group(1) == VERSION, "the app serves the new version")
        pg2 = open_sm(b, served=served)
        pg2.wait_for_selector("#bb-btn"); pg2.click("#bb-btn")
        check(pg2.input_value("#bb-app") == BASE, "the served script already holds the app's address")
        served_head = re.search(r"^// ==UserScript==\n.*?^// ==/UserScript==", served, flags=re.S | re.M).group(0)
        updates = bool(re.search(r"^// @updateURL\s+\S+", served_head, flags=re.M)) or any(m != "none" for m in re.findall(r"^// @downloadURL\s+(\S+)", served_head, flags=re.M))
        check(("Tự cập nhật: BẬT" if updates else "Tự cập nhật: TẮT") in pg2.inner_text("#bb-panel"), f"the served copy's panel says what its own header says about updates ({'on' if updates else 'off'})")
        pg2.fill("#bb-token", TOKEN); pg2.click("#bb-check")
        check(wait_log(pg2, "Mã đúng"), "with the address pre-filled, only the code is needed")

        # --- a sleeping server: a proxy's 502, 503, 524 or an HTML page is "waking", never "SYNC_TOKEN not set"; reads are tried again, writes once
        pw_ = open_sm(b); pw_.wait_for_selector("#bb-btn"); pw_.click("#bb-btn"); fill(pw_, app=BASE, token=TOKEN)
        cfg = lambda: len([1 for m, p in app_requests if p == "/api/config"])
        c0 = cfg(); state["queue"]["/api/config"] = [HTML_503, HTML_503, None]
        pw_.click("#bb-check"); check(wait_log(pw_, "Hoàn tất."), "a cold start: two HTML 503 pages, then the app answers: the check goes on")
        check("Box builder: có trả lời." in log_text(pw_) and "SYNC_TOKEN" not in log_text(pw_) and cfg() - c0 == 3 and pw_.evaluate("window.__delays") == [2000, 4000], "three tries 2 and 4 seconds apart, and no word about SYNC_TOKEN")
        c0 = cfg(); sc0 = len([1 for m, p in app_requests if p == "/api/sync/check"])
        state["queue"]["/api/config"] = [HTML_503, HTML_502, {"status": 524, "responseText": ""}, {"status": 200, "responseText": "<html><body>Login</body></html>"}]
        pw_.evaluate("window.__delays = []"); pw_.click("#bb-check"); woke = wait_log(pw_, "Máy chủ đang thức dậy, chưa gửi gì sang SM")
        check(woke and "SYNC_TOKEN" not in log_text(pw_) and "Hoàn tất." not in log_text(pw_), "503 and 502 pages, a 524 and an HTML 200: all four mean 'waking'; the message says nothing was sent to SM")
        check(cfg() - c0 == 4 and pw_.evaluate("window.__delays") == [2000, 4000, 8000] and len([1 for m, p in app_requests if p == "/api/sync/check"]) == sc0, "4 tries in all, then it stops before the code check")
        state["queue"]["/api/sm/boxes/sync"] = [HTML_503]; bp0 = len([1 for m, p in app_requests if p == "/api/sm/boxes/sync"]); pw_.evaluate("window.__delays = []")
        pw_.click("#bb-boxes"); check(wait_log(pw_, "Máy chủ đang thức dậy, chưa gửi gì sang SM") and "SYNC_TOKEN" not in log_text(pw_), "a proxy's 503 on a write: 'waking', not 'SYNC_TOKEN not set'")
        check(len([1 for m, p in app_requests if p == "/api/sm/boxes/sync"]) - bp0 == 1 and pw_.evaluate("window.__delays") == [], "a write is tried once, never repeated")
        state["queue"]["/api/sm/boxes/sync"] = [{"status": 503, "responseText": '{"detail":"not set"}'}]
        pw_.click("#bb-boxes"); check(wait_log(pw_, "chưa đặt SYNC_TOKEN"), "only the app's own JSON 503 says SYNC_TOKEN is not set")
        state["queue"] = {}
        # --- the panel's line about Box builder (GET /api/preflight): ready boxes, how old its data is, which script version it expects
        pw_.click("#bb-view"); check(wait_log(pw_, "Hoàn tất."), "the view run finishes with no box in the app")
        t = log_text(pw_)
        check("Box builder sẵn sàng." in t and "Chưa có hộp nào sẵn sàng đưa lên SM" in t and "return" not in t.lower() and "trả hộp" not in t, "the run starts by asking the app how it is, finds nothing to make, and has no return step")
        pre = lambda: pw_.inner_text("#bb-pre")
        check(re.search(r"^Box builder \(lúc \d\d:\d\d\): 0 hộp sẵn sàng đưa lên SM\. Danh sách hộp SM cập nhật .+, kho cập nhật .+\. Bản script: bạn dùng " + re.escape(VERSION) + r"(, Box builder mong [0-9]+(\.[0-9]+)*)?\.( Dán bản mới vào Tampermonkey\.)?$", pre()) is not None,
              f"the line says 0 boxes are ready, how old the data is, and that this script is {VERSION}: {pre()!r}")
        check(f"Box builder mong {VERSION}." in pre() or fixture_used.get("/api/preflight") is None, "an app that expects this very version raises no warning")
        if fixture_used.get("/api/preflight"):
            state["preflight_version"] = "9.9.9"; pw_.click("#bb-refresh")
            pw_.wait_for_function("document.querySelector('#bb-pre').innerText.includes('Box builder mong 9.9.9')", timeout=20000)
            check("Dán bản mới vào Tampermonkey." in pre() and pw_.locator("#bb-pre.warn").count() == 1, "an app that expects another script version says so, in the warning colour")
            state["preflight_version"] = VERSION
        else:
            print("note the app answers /api/preflight itself: the version-mismatch line was not tried (it needs an app that expects another version)")
        state["queue"]["/api/preflight"] = [{"status": 404, "responseText": '{"detail":"Not Found"}'}]; pw_.click("#bb-refresh")
        pw_.wait_for_function("document.querySelector('#bb-pre').innerText.includes('chưa có phần kiểm tra trước')", timeout=20000)
        check(pw_.locator("#bb-pre.warn").count() == 1, "an older Box builder (404): the line says it has no preflight yet")
        state["queue"]["/api/preflight"] = [{"status": 200, "responseText": '{"ok": false}'}]; pw_.click("#bb-refresh")
        pw_.wait_for_function("document.querySelector('#bb-pre').innerText.includes('cơ sở dữ liệu chưa trả lời')", timeout=20000)
        check(True, "ok false: the line says the database is not answering")
        state["queue"]["/api/preflight"] = [HTML_503, HTML_503, HTML_502, HTML_503]; pw_.click("#bb-refresh")
        pw_.wait_for_function("document.querySelector('#bb-pre').innerText.includes('Máy chủ đang thức dậy, chưa gửi gì sang SM')", timeout=20000)
        check("SYNC_TOKEN" not in pre(), "a sleeping server: the line says it is waking, nothing about SYNC_TOKEN")
        state["queue"] = {}; pw_.click("#bb-refresh")
        pw_.wait_for_function("document.querySelector('#bb-pre').innerText.includes('0 hộp sẵn sàng')", timeout=20000)
        check(True, "and the line recovers on the next refresh")
        pw_.close()

        # --- optional auto refresh, on a fake clock: boxes every 10 minutes, the catalog every 60, never over a run that is going, off when signed out
        def open_clock(seed=None, fields=True):
            page = open_sm(b, clock=True, seed=seed); page.wait_for_selector("#bb-btn"); page.click("#bb-btn")
            if fields: fill(page, app=BASE, token=TOKEN, sectors="Hanoi-GPI")
            return page
        n_sm_boxes = lambda: len([1 for m, p, _ in sm_requests if p == "/api/boxes"])
        n_sm_objects = lambda: len([1 for m, p, _ in sm_requests if p == "/api/objects"])
        n_posts = lambda path: len([1 for p, _ in app_posts if p == path])
        n_done = lambda page: log_text(page).count("Tự làm mới: xong.")
        stored = lambda page, key: page.evaluate("k => window.__store[k]", key)
        def idle_(page): page.wait_for_function("!document.getElementById('bb-view').disabled", timeout=180000)
        def minutes(page, n):
            """The fake clock moves a minute at a time (one look of the timer each); a run that starts is let finish before the next minute."""
            for _ in range(n):
                page.clock.run_for(60000)
                idle_(page)
        snap = lambda: (n_sm_boxes(), n_sm_objects(), n_posts("/api/sm/boxes/sync"), n_posts("/api/catalog/sync"))

        ap = open_clock()
        check(not ap.locator("#bb-auto").is_checked() and "Tự làm mới đang tắt" in ap.inner_text("#bb-auto-note"), "auto refresh is off until the person turns it on")
        s0 = snap(); ap.clock.run_for(3 * 3600 * 1000); time.sleep(0.5)
        check(snap() == s0 and not stored(ap, "bb_auto"), "off: three hours pass and nothing is asked of SM or the app")
        # on: the first refresh is due at once (catalog and boxes), then the next times are stored
        ap.check("#bb-auto"); check(stored(ap, "bb_auto") is True and ap.locator("#bb-auto").is_checked(), "turning it on is kept in the script's storage")
        ap.clock.run_for(3000); idle_(ap); ap.wait_for_function("document.querySelector('#bb-log').innerText.includes('Tự làm mới: xong.')", timeout=180000); idle_(ap)
        s1 = snap(); t_ok = ap.evaluate("Date.now()")
        check(s1[3] == s0[3] + 1 and s1[2] >= s0[2] + 1 and s1[1] > s0[1], "the first run read the catalog and the boxes, and sent both to the app")
        check(abs(stored(ap, "bb_auto_boxes") - (t_ok + 600000)) < 120000 and abs(stored(ap, "bb_auto_catalog") - (t_ok + 3600000)) < 120000, "the next times are stored: boxes in 10 minutes, catalog in 60")
        check("Tự làm mới đang bật" in ap.inner_text("#bb-auto-note") and "Chỉ đọc SM" in ap.inner_text("#bb-auto-note"), "the panel says it is on and read-only")
        minutes(ap, 9); time.sleep(0.3)
        check(snap() == s1, "9 minutes later: nothing yet")
        minutes(ap, 2)
        s2 = snap()
        check(s2[0] == s1[0] + 1 and s2[2] == s1[2] + 1 and s2[3] == s1[3] and s2[1] == s1[1], "after 10 minutes: exactly one boxes read (SM boxes, one post of the snapshot), no catalog")
        n_cat0, n_box0, n_obj0 = s2[3], s2[2], s2[1]
        minutes(ap, 45); time.sleep(0.3)
        s3 = snap()
        check(s3[3] == n_cat0 and 4 <= s3[2] - n_box0 <= 5 and s3[1] == n_obj0, f"until the 60 minutes are up: boxes about every 10 minutes ({s3[2] - n_box0} reads in 45 minutes), still no catalog")
        minutes(ap, 8)
        s4 = snap()
        check(s4[3] == n_cat0 + 1 and s4[1] > n_obj0, "after 60 minutes: the catalog is read and sent once")
        check(n_done(ap) >= 6, f"every run says it is done in the log ({n_done(ap)} so far)")
        # never over a run that is going: the app does not answer, so a manual run stays busy while the clock passes several due times
        ap.evaluate("window.__hold = true"); ap.click("#bb-boxes"); time.sleep(0.8); s5 = snap()         # the manual run read SM and now waits for the app
        check(ap.locator("#bb-view").is_disabled(), "a manual run is going (the buttons are off)")
        for _ in range(25): ap.clock.run_for(60000)
        time.sleep(0.5)
        check(snap() == s5 and len(ap.evaluate("window.__held")) >= 1, "25 minutes pass with that run still going: no second run starts, nothing new is asked of SM")
        ap.evaluate("window.__release()"); idle_(ap)
        s6 = snap(); check(s6[0] - s5[0] in (0, 1) and s6[2] == s5[2] + 1 and s6[3] == s5[3], f"the manual run finishes by itself when the app answers ({s5} -> {s6})")
        d0 = n_done(ap); minutes(ap, 1)
        check(n_done(ap) == d0 + 1 and snap()[0] == s6[0] + 1, "the next minute the overdue auto run starts and finishes")
        # a failure is tried again after 5 minutes, not every minute
        for _ in range(11): ap.clock.run_for(60000); idle_(ap)               # until a boxes run is due again
        state["queue"]["/api/sm/boxes/sync"] = [HTML_503]
        a0 = n_posts("/api/sm/boxes/sync")
        for _ in range(12):
            ap.clock.run_for(60000); idle_(ap)
            if n_posts("/api/sm/boxes/sync") > a0: break
        check(n_posts("/api/sm/boxes/sync") == a0 + 1 and "Máy chủ đang thức dậy" in log_text(ap) and stored(ap, "bb_auto") is True, "a run that meets a sleeping server fails, and the switch stays on")
        minutes(ap, 4); time.sleep(0.3)
        check(n_posts("/api/sm/boxes/sync") == a0 + 1, "4 minutes after the failure: no new try")
        minutes(ap, 2)
        check(n_posts("/api/sm/boxes/sync") == a0 + 2 and n_done(ap) >= d0 + 2, "5 minutes after: one more try, and it works")
        # signed out: it stops
        state["signed_in"] = False
        for _ in range(12):
            ap.clock.run_for(60000); idle_(ap)
            if not stored(ap, "bb_auto"): break
        check(stored(ap, "bb_auto") is False and not ap.locator("#bb-auto").is_checked() and "Tự làm mới đã tắt vì SM chưa đăng nhập" in ap.inner_text("#bb-auto-note"), "SM says nobody is signed in: auto refresh turns itself off and the panel says why")
        a2 = snap(); ap.clock.run_for(3 * 3600 * 1000); time.sleep(0.5)
        check(snap() == a2, "three hours later: it asked for nothing more")
        state["signed_in"] = True; nd = n_done(ap); ap.check("#bb-auto"); ap.clock.run_for(3000); idle_(ap)
        check(n_done(ap) == nd + 1 and stored(ap, "bb_auto") is True and "Tự làm mới đã tắt" not in ap.inner_text("#bb-auto-note") and snap()[3] == a2[3] + 1, "signed in again and switched on again: it starts at once with the catalog")
        ap.uncheck("#bb-auto"); a3 = snap(); ap.clock.run_for(2 * 3600 * 1000); time.sleep(0.5)
        check(stored(ap, "bb_auto") is False and snap() == a3, "switched off: no more runs")
        ap.close()
        # another SM tab just refreshed (its next times are in the storage): this tab waits, then refreshes when they come
        ap = open_clock(seed={"bb_app": BASE, "bb_token": TOKEN, "bb_sectors": "Hanoi-GPI", "bb_auto": True, "bb_auto_boxes": CLOCK0 + 3600000, "bb_auto_catalog": CLOCK0 + 7200000})
        check(ap.evaluate("Date.now()") - CLOCK0 < 60000, "the fake clock starts where the test says")
        s7 = snap(); ap.clock.run_for(40 * 60 * 1000); time.sleep(0.5)
        check(snap() == s7, f"next times already stored by another tab: 40 minutes pass and this tab does nothing ({s7} -> {snap()})")
        minutes(ap, 25)
        s8 = snap(); check(s8[0] == s7[0] + 1 and s8[2] == s7[2] + 1 and s8[3] == s7[3], f"when the stored time comes this tab reads the boxes once ({s7} -> {s8})")
        ap.close()
        # it cannot be turned on without an address and a sync code
        ap0 = open_clock(fields=False); ap0.evaluate("window.__store.bb_app = ''; window.__store.bb_token = ''"); ap0.click("#bb-auto"); time.sleep(0.3)
        check(not ap0.locator("#bb-auto").is_checked() and stored(ap0, "bb_auto") in (None, False) and "Nhập địa chỉ Box builder và mã đồng bộ" in ap0.inner_text("#bb-auto-note"), "without an address and a sync code the switch refuses to turn on and says what is missing")
        ap0.close()

        # --- SM's object page (the lifetime use page): the script is told to load there and does, and leaves the page alone
        matches = re.findall(r"^// @match\s+(\S+)", SCRIPT, flags=re.M)
        def tm_match(pattern, url):               # Tampermonkey's @match: the text before * must be there, * stands for anything
            return re.fullmatch(".*".join(re.escape(part) for part in pattern.split("*")), url) is not None
        check("https://sm.config.inc/*" in matches and "https://sm.config.inc/object*" in matches, f"the header keeps the old @match and adds the object page ({matches})")
        check(any(tm_match(m, USAGE_URL) for m in matches) and tm_match("https://sm.config.inc/object*", USAGE_URL), "the object page's address (sorted by usage) is covered by @match")
        pg3 = open_sm(b, url=USAGE_URL, html=USAGE_PAGE)
        pg3.wait_for_selector("#bb-btn", timeout=8000)
        check(pg3.locator("#bb-btn").is_visible(), "the script loads on SM's /object page and shows its button")
        check(pg3.inner_text("#card") == "LIFETIME USE: 1587 borrows at least, 1611 items total, Heavily used", "it leaves the page's own LIFETIME USE text as it was")
        pg3.click("#bb-btn"); pg3.fill("#bb-app", BASE); pg3.fill("#bb-token", TOKEN); pg3.click("#bb-check")
        check(wait_log(pg3, "Mã đúng") and wait_log(pg3, "Hoàn tất."), "from the /object page the check works the same")
        check(pg3.evaluate("location.pathname + location.search") == "/object?sort_by=usage&sort_dir=desc&page=1", "and it did not move the page")

        # --- the guarantees
        non_get = [(m, p) for m, p, _ in sm_requests if m != "GET"]
        check(sm_requests and not non_get, f"every request to SM was a GET ({len(sm_requests)} of them, {len(non_get)} writes)")
        paths = {p for _, p, _ in sm_requests}
        check(paths <= {"/", "/object", "/api/objects", "/api/boxes"}, f"SM was only asked for: {sorted(paths)}")
        check(not any(TOKEN in json.dumps(h) or "not-the-code" in json.dumps(h) for _, _, h in sm_requests), "the sync code was never sent to SM")
        check({p for m, p in app_requests} <= {"/api/config", "/api/sync/check", "/api/catalog/sync", "/api/sm/boxes/sync", "/api/taskboxes", "/api/preflight"}, f"the app was only asked for: {sorted({p for _, p in app_requests})}")
        check(app_calls and all(c.get("anonymous") is True and c.get("redirect") == "error" for c in app_calls), f"all {len(app_calls)} calls to the app were anonymous (no cookies) with redirect 'error'")
        check(not any(x in json.dumps(c) for c in app_calls for x in ("private-note", "[BB 2026-01-01", "next_actions", "collected")), "the SM boxes' notes and next_actions never went to the app")
        check(not any(x in d for _, d in app_posts for x in PEOPLE), "no person's name appears in anything posted to the app")
        check(not any(TOKEN in (pg.inner_text("#bb-log")) for _ in [0]), "the code never shows in the log")
        check(len(re.findall(r'method: "POST"', SCRIPT)) == 1 and not any(w in SCRIPT for w in ('"PUT"', '"PATCH"', '"DELETE"', "/transition", "handover")),
              "the script text holds one POST to SM (the box create, switched off in this build) and no other write")
        check(SCRIPT.count("const WRITES_ENABLED = false;") == 1 and "Ghi vào SM: TẮT" in pg.inner_text("#bb-panel"), "the shipped build has writes switched off and the panel says so")
        pg.screenshot(path=os.path.join(tempfile.gettempdir(), "box-builder-sync-panel.png"))
        b.close()
finally:
    stop_server(srv, srv_log, tmp)

check(not errs, f"no script errors {errs[:2]}")
print("FAILED" if fails else "ALL SYNC TOOL CHECKS PASSED")
sys.exit(1 if fails else 0)

"""Browser test of integrations/box_builder_sm.user.js against a pretend SM page and the real app (own port, own database).
SM is never contacted: sm.config.inc is answered by this script. Run: python tests/userscript_sync_e2e.py (needs playwright + chromium)."""
import csv, json, os, subprocess, sys, tempfile, time, urllib.error, urllib.request
from urllib.parse import parse_qs, urlsplit
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8094
BASE = f"http://127.0.0.1:{PORT}"
TOKEN = "tok-sync-e2e"
SCRIPT = open(os.path.join(ROOT, "integrations", "box_builder_sm.user.js"), encoding="utf-8").read()

# ---- the pretend SM: objects shaped like the real /api/objects (no Vietnamese name), plus another sector and a hostile picture link
rows = list(csv.reader(open(os.path.join(ROOT, "data", "catalog.csv"), encoding="utf-8-sig")))[1:]
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
                    "location": "Z", "sector": "Hanoi-4F", "image_url": ""})
HAS_VI = {"on": False}
env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DEEPSEEK_API_KEY")}
env.update(DB_PATH=os.path.join(tempfile.mkdtemp(), "s.db"), CATALOG_PATH=os.path.join(ROOT, "data", "catalog.csv"), SYNC_TOKEN=TOKEN)
srv = subprocess.Popen([os.path.join(ROOT, ".venv/bin/uvicorn"), "app.main:app", "--port", str(PORT)], cwd=ROOT, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try: urllib.request.urlopen(BASE + "/api/config", timeout=1); break
    except Exception: time.sleep(0.2)

fails, errs = [], []
sm_requests, app_requests = [], []
state = {"signed_in": True, "old_server": False}
def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

def gm(opts):
    """Tampermonkey's request: the script's calls to the app land here."""
    path = urlsplit(opts["url"]).path
    app_requests.append((opts["method"], path))
    if state["old_server"] and path == "/api/sync/check":
        return {"status": 404, "responseText": '{"detail":"Not Found"}'}
    req = urllib.request.Request(opts["url"], method=opts["method"], headers=opts.get("headers") or {}, data=(opts.get("data") or "").encode() or None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r: return {"status": r.status, "responseText": r.read().decode()}
    except urllib.error.HTTPError as e: return {"status": e.code, "responseText": e.read().decode()}
    except Exception as e: return {"error": str(e)}

def sm_objects(route):
    req = route.request
    sm_requests.append((req.method, urlsplit(req.url).path, dict(req.headers)))
    if not state["signed_in"]:
        return route.fulfill(status=200, content_type="text/html", body="<html><body>Login</body></html>")
    page = int(parse_qs(urlsplit(req.url).query).get("page", ["1"])[0])
    chunk = objects[(page - 1) * 30: page * 30]
    chunk = [dict(o, name_vi=o["name"]) for o in chunk] if HAS_VI["on"] else chunk
    pages = -(-len(objects) // 30)
    route.fulfill(status=200, content_type="application/json", body=json.dumps(
        {"objects": chunk, "page": page, "total_items": len(objects), "total_pages": pages, "per_page": 30, "has_next": page < pages}))

SHIM = """
window.__store = {};
window.GM_getValue = (k, d) => (k in window.__store ? window.__store[k] : d);
window.GM_setValue = (k, v) => { window.__store[k] = v; };
window.GM_xmlhttpRequest = (o) => {
  window.__gm({method: o.method || 'GET', url: o.url, headers: o.headers || {}, data: o.data || null}).then((r) => {
    if (r.error) { o.onerror && o.onerror({error: r.error}); } else { o.onload({status: r.status, responseText: r.responseText}); }
  });
};
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

try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(); pg = b.new_context(viewport={"width": 1300, "height": 900}).new_page()
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.route("https://sm.config.inc/api/objects**", sm_objects)
        pg.route("https://sm.config.inc/", lambda r: r.fulfill(status=200, content_type="text/html", body="<html><body><h1>Scenario Manager</h1></body></html>"))
        pg.expose_function("__gm", gm)
        pg.goto("https://sm.config.inc/")
        pg.add_script_tag(content=SHIM); pg.add_script_tag(content=SCRIPT)
        pg.wait_for_selector("#bb-btn")
        check(pg.locator("#bb-panel").is_hidden(), "the panel starts closed, only a small button shows")
        pg.click("#bb-btn")
        check(pg.locator("#bb-panel").is_visible() and "chỉ đọc" in pg.inner_text("#bb-panel").lower(), "the panel opens and says it only reads from SM")

        # --- every way a check can fail says what to do
        pg.click("#bb-check"); check(wait_log(pg, "Chưa nhập địa chỉ Box builder"), "no address: asks for one")
        fill(pg, app="http://127.0.0.1:9", token=TOKEN); pg.click("#bb-check")
        check(wait_log(pg, "Không kết nối được tới http://127.0.0.1:9"), "wrong address: says it cannot connect and what to check")
        fill(pg, app=BASE, token="not-the-code"); pg.click("#bb-check")
        check(wait_log(pg, "Mã đồng bộ sai") and "đã đăng nhập" in log_text(pg), "wrong code: says so, after confirming SM and the app answered")
        state["signed_in"] = False; fill(pg, token=TOKEN); pg.click("#bb-check")
        check(wait_log(pg, "Chưa đăng nhập SM"), "not signed in to SM: says to sign in")
        state["signed_in"] = True; pg.click("#bb-check")
        check(wait_log(pg, "Hoàn tất."), "all good: the check finishes")
        t = log_text(pg)
        check("Mã đúng" in t and "SM không trả tên tiếng Việt" in t and "file CSV" in t, "good check: code accepted, and it explains the Vietnamese names come from the CSV")

        # --- the sync itself
        pg.click("#bb-sync")
        check(wait_log(pg, "Xong. Box builder có", timeout=120000), "sync finishes")
        t = log_text(pg)
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

        # --- an app too old to fill in Vietnamese names must not be fed rows without them
        state["old_server"] = True; before = len(app_requests); pg.click("#bb-sync")
        check(wait_log(pg, "chưa tự bù từ file CSV"), "old app + no Vietnamese names: refuses to send")
        check(not any(m == "POST" and p == "/api/catalog/sync" for m, p in app_requests[before:]), "nothing was posted in that case")
        state["old_server"] = False

        # --- when SM does send Vietnamese names they are used
        HAS_VI["on"] = True; pg.click("#bb-check")
        check(wait_log(pg, "SM trả tên tiếng Việt cho món"), "SM names in Vietnamese are recognised")

        # --- the guarantees
        check(sm_requests and all(m == "GET" for m, _, _ in sm_requests), f"every request to SM was a GET ({len(sm_requests)} of them)")
        check(not any(TOKEN in json.dumps(h) or "not-the-code" in json.dumps(h) for _, _, h in sm_requests), "the sync code was never sent to SM")
        check({p for m, p in app_requests} <= {"/api/config", "/api/sync/check", "/api/catalog/sync"}, f"the app was only asked for: {sorted({p for _, p in app_requests})}")
        check(not any(TOKEN in (pg.inner_text("#bb-log")) for _ in [0]), "the code never shows in the log")
        pg.screenshot(path=os.path.join(tempfile.gettempdir(), "box-builder-sync-panel.png"))
        b.close()
finally:
    srv.terminate()

check(not errs, f"no script errors {errs[:2]}")
print("FAILED" if fails else "ALL SYNC TOOL CHECKS PASSED")
sys.exit(1 if fails else 0)

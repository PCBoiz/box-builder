"""Browser test of the object browser, automations and AI batch flow on a throw-away instance (own port, own DB).
Run:  pip install playwright && python -m playwright install chromium && python -I tests/ui_automation_e2e.py
Works on Windows and Linux. Set E2E_BROWSER=firefox to use Firefox, E2E_PORT to pick the port.
Screenshots go to the system temp dir. Never touches SM, Render or Neon.
Optional: AXE_JS=/path/to/axe.min.js (from the axe-core npm package) adds an accessibility run on every tab."""
import os, shutil, socket, subprocess, sys, tempfile, time, urllib.request, json
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
OUT = os.path.join(tempfile.gettempdir(), "box-builder-ui-shots")
os.makedirs(OUT, exist_ok=True)
PORT = int(os.environ.get("E2E_PORT") or free_port())
BROWSER = os.environ.get("E2E_BROWSER", "chromium")
BASE = f"http://127.0.0.1:{PORT}"
tmp = tempfile.mkdtemp()
env = clean_env(DB_PATH=os.path.join(tmp, "ui.db"), CATALOG_PATH=os.path.join(ROOT, "data", "catalog.csv"), SYNC_TOKEN="t", SM_PUSH_MODE="mock")
srv, srv_log = start_server(ROOT, env, PORT, tmp)


import struct, zlib
def png(w=8, h_=8, rgb=(200, 90, 30)):
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h_))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h_, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")

fails, errs, net, toasts = [], [], [], []
def switches():
    return {x["key"]: x for x in json.load(urllib.request.urlopen(BASE + "/api/automations"))["switches"]}

def wait_switch(key, want, timeout=8):
    """The checkbox ticks instantly, the save lands a moment later: wait for the server, not the page."""
    end = time.time() + timeout
    while time.time() < end:
        if switches()[key]["enabled"] is want:
            return True
        time.sleep(0.1)
    return False

def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)

try:
    with sync_playwright() as pw:
        b = getattr(pw, BROWSER).launch()
        pg = b.new_context(viewport={"width": 1400, "height": 1000}).new_page()
        pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        pg.on("console", lambda m: errs.append("console: " + m.text) if m.type == "error" else None)
        pg.on("response", lambda r: net.append(f"{r.request.method} {r.url.replace(BASE, '')} -> {r.status}") if r.request.method != "GET" else None)
        pg.goto(BASE)
        pg.wait_for_selector("#live b:has-text('Trực tiếp')")
        check(pg.locator(".tab").count() == 3, "three tabs")

        # --- accessibility: the tab pattern, and arrow keys move between tabs
        check(pg.locator("[role=tablist] [role=tab]").count() == 3, "tabs have the tab role inside a tablist")
        sel = pg.evaluate("[...document.querySelectorAll('[role=tab]')].map(t => [t.getAttribute('aria-selected'), t.tabIndex])")
        check(sel == [["true", 0], ["false", -1], ["false", -1]], f"one tab stop (roving tabindex) {sel}")
        check(pg.evaluate("[...document.querySelectorAll('[role=tab]')].every(t => { const p = document.getElementById(t.getAttribute('aria-controls')); "
                          "return p && p.getAttribute('role') === 'tabpanel' && p.getAttribute('aria-labelledby') === t.id; })"),
              "each tab controls a tabpanel that points back to it")
        pg.focus("#tab-boxes"); pg.keyboard.press("ArrowRight")
        check(pg.evaluate("document.activeElement.id") == "tab-objects" and pg.locator("#view-objects").is_visible(), "ArrowRight moves to the next tab and shows it")
        pg.keyboard.press("End")
        check(pg.evaluate("document.activeElement.id") == "tab-auto" and pg.locator("#view-auto").is_visible(), "End goes to the last tab")
        pg.keyboard.press("ArrowRight")
        check(pg.evaluate("document.activeElement.id") == "tab-boxes", "ArrowRight wraps around to the first tab")
        pg.keyboard.press("ArrowLeft"); pg.keyboard.press("Home")
        check(pg.evaluate("document.activeElement.id") == "tab-boxes" and pg.locator("#view-boxes").is_visible(), "Home returns to the first tab")
        check(pg.locator("#sr-live[role=status][aria-live=polite]").count() == 1, "a polite live region exists for announcements")

        # --- first use: a guide, one plain next step, a short form, and kind messages
        check(pg.locator("#guide").is_visible() and pg.locator("#guide .steps li").count() == 4, "first visit shows the four-step guide")
        pg.wait_for_function("document.querySelector('#nextstep span').textContent.includes('Tạo hộp đầu tiên')", timeout=8000)
        check("Tạo hộp đầu tiên" in pg.inner_text("#nextstep"), "the next step tells a new user to create the first box")
        check(pg.locator("#adv[open]").count() == 0 and not pg.locator("#f-count").is_visible(),
              "the form starts short: section code, big object, count and categories are under 'more options'")
        check(pg.locator("#h-module").is_visible() and "M8" in pg.inner_text("#h-module"), "each main field has a plain hint")
        pg.fill("#f-module", ""); pg.click("#f-go")
        msg = pg.evaluate("document.querySelector('#f-module').validationMessage")
        check(msg == "Hãy nhập Module, ví dụ M8.", f"an empty Module gets a Vietnamese message: {msg!r}")
        pg.fill("#f-module", "m8"); pg.press("#f-module", "Tab")
        check(pg.input_value("#f-module") == "M8", "m8 is tidied to M8 when the person leaves the field")

        # --- boxes: make two drafts (A1 full 21, B1 with 6 slots to hand-fill)
        pg.fill("#f-label", "Box A1"); pg.fill("#f-theme", "bếp"); pg.click("#f-go")
        pg.wait_for_selector(".tray .pill.draft", timeout=30000)
        pg.wait_for_selector("#f-warn:not([hidden])", timeout=8000)
        check(pg.is_disabled("#f-go") and "đã có" in pg.inner_text("#f-warn"), "a name that is already used is flagged before the button is pressed")
        check("nháp chưa duyệt" in pg.inner_text("#nextstep"), "the next step says the draft is not approved yet")
        pg.fill("#f-label", "Box B1")
        check(pg.locator("#f-warn").is_hidden() and pg.is_enabled("#f-go"), "a free name clears the warning")
        pg.click("#adv summary")
        pg.fill("#f-label", "Box A1")
        check(pg.input_value("#f-count") == "12", "the size follows the box name: A1 gets 12 objects")
        pg.fill("#f-label", "Box B2")
        check(pg.input_value("#f-count") == "21", "B2 gets 21 objects")
        pg.fill("#f-count", "6"); pg.fill("#f-label", "Box C1")
        check(pg.input_value("#f-count") == "6", "a number the person typed is kept when the name changes")
        pg.fill("#f-label", "Box B1"); pg.click("#f-go")
        pg.wait_for_function("document.querySelectorAll('.tray .pill.draft').length === 2", timeout=30000)
        # empty B1 by hand so there is room: remove 3 items through the API
        st = json.load(urllib.request.urlopen(BASE + "/api/state"))
        b1 = next(x for x in st["boxes"] if x["label"] == "Box B1")
        for it in b1["items"][:3]:
            urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/boxes/{b1['id']}/items/{it['pos']}", method="DELETE"))

        # --- objects tab
        pg.click(".tab[data-view=objects]")
        pg.wait_for_selector("#o-body tr td.oid")
        check(pg.locator("#view-boxes").is_hidden() and pg.locator("#view-objects").is_visible(), "objects view shown")
        check(pg.locator("#cov .cov-row").count() == 12, "12 coverage rows by default")
        rows = pg.locator("#o-body tr").count()
        check(rows == 50, f"50 rows per page (got {rows})")
        pg.screenshot(path=f"{OUT}/objects.png")
        n_all = pg.locator(".fchip").first.inner_text()
        pg.click(".fchip:has-text('Trong hộp nháp')")
        pg.wait_for_function("document.querySelector('#o-body tr .pill.st-draft')")
        check(pg.locator("#o-body .pill.st-free").count() == 0 and pg.locator("#o-body .pill.st-draft").count() > 0, "status filter: only draft objects")
        check(pg.locator("#o-body input[type=checkbox]").count() == 0, "taken objects cannot be selected")
        pg.click(".fchip:has-text('Còn trống')")
        pg.wait_for_function("document.querySelector('#o-body tr .pill.st-free')")
        pg.fill("#o-q", "bát")
        pg.wait_for_timeout(700)
        check(pg.locator("#o-body tr").count() > 0, "search returns rows")
        boxes_cb = pg.locator("#o-body input[type=checkbox]")
        boxes_cb.nth(0).check(); boxes_cb.nth(1).check()
        check(pg.locator("#o-bar").is_visible() and "2" in pg.inner_text("#o-sel"), "pick bar shows 2 selected")
        pg.wait_for_function("document.querySelector('#o-target option') && document.querySelector('#o-target option').value !== ''", timeout=8000)
        opts = pg.locator("#o-target option").all_inner_texts(); check(len(opts) == 1 and "Box B1" in opts[0], f"target list = only the draft with room {opts}")
        picked = [pg.locator("#o-body tr.sel td.oid").nth(i).inner_text() for i in range(2)]
        pg.click("#o-add")
        pg.wait_for_function("!document.querySelector('#o-bar') || document.querySelector('#o-bar').hidden")
        st = json.load(urllib.request.urlopen(BASE + "/api/state"))
        b1 = next(x for x in st["boxes"] if x["label"] == "Box B1")
        check({i["id"] for i in b1["items"]} >= set(picked), "hand-picked objects landed in Box B1")
        check(all(i["locked"] for i in b1["items"] if i["id"] in picked), "hand-picked objects are locked")
        # clicking a coverage row filters by category
        pg.fill("#o-q", ""); pg.wait_for_timeout(500)
        pg.locator("#cov .cov-row").nth(2).click()
        pg.wait_for_timeout(600)
        check(pg.locator("#o-cat").input_value() != "", "coverage click sets the category filter")

        # --- CSV export: the button downloads every row that matches the current filters, not just this page
        shown = int("".join(ch for ch in pg.inner_text("#o-page").split("·")[1] if ch.isdigit()))
        with pg.expect_download() as dl:
            pg.click("#o-csv")
        with open(dl.value.path(), "rb") as fh: raw = fh.read().decode("utf-8")
        lines = [ln for ln in raw.lstrip("\ufeff").splitlines() if ln]
        check(raw.startswith("\ufeff") and lines[0].startswith("id,name,vietnamese_name"), "CSV download has the BOM and the header")
        check(len(lines) - 1 == shown, f"CSV holds all {shown} filtered objects (got {len(lines) - 1})")
        check(dl.value.suggested_filename == "objects.csv", "download is named objects.csv")

        # --- automations tab
        pg.click(".tab[data-view=auto]")
        pg.wait_for_selector(".sw-row")
        check(pg.locator(".sw-row").count() == 9, "nine switches")
        check(not pg.locator("#sw-auto_approve").is_checked(), "auto-approve starts OFF")
        check(pg.locator("#sw-sheet_export").is_checked(), "sheet export starts ON")
        check(pg.locator("#a-alerts li").count() >= 1, "an alert is listed (catalog not synced with SM)")
        check(pg.locator("#alert-badge").is_visible(), "alert badge on the tab")
        check(pg.locator("#a-todo li .where").count() >= 1, "to-do list names a step that waits for a person")
        check(not pg.locator("#sw-auto_heal").is_checked(), "auto-heal starts OFF")
        pg.screenshot(path=f"{OUT}/auto.png", full_page=True)

        # toggle auto-approve via the track (label click) and confirm persisted server-side
        pg.locator("label.sw[for=sw-auto_approve]").click()
        pg.wait_for_function("document.querySelector('#sw-auto_approve').checked")
        check(wait_switch("auto_approve", True), "auto-approve persisted ON")
        # the SM queue starts OFF (the reads-only stage) and the panel says so; the queue and fake-push checks below need it ON
        check(not pg.locator("#sw-sm_queue").is_checked() and "Hàng chờ đang tắt" in pg.inner_text("#a-queue"), "the SM queue starts OFF and its panel says the tool sees nothing")
        pg.locator("label.sw[for=sw-sm_queue]").click()
        pg.wait_for_function("document.querySelector('#sw-sm_queue').checked")
        check(wait_switch("sm_queue", True), "the SM queue persisted ON")
        # keyboard: space toggles a switch
        pg.focus("#sw-sheet_export"); pg.keyboard.press("Space")
        pg.wait_for_function("!document.querySelector('#sw-sheet_export').checked")
        check(wait_switch("sheet_export", False), "sheet export OFF via keyboard")
        try:
            pg.wait_for_function("document.activeElement && document.activeElement.id === 'sw-sheet_export'", timeout=5000)
        except Exception:
            pass
        check(pg.evaluate("document.activeElement.id") == "sw-sheet_export", "keyboard focus stays on the switch after it saves")
        check(pg.locator(".sw-state[aria-hidden=true]").count() == 9, "the visual On/Off text is hidden from screen readers (the switch already says it)")
        reuse_row = pg.locator(".sw-row:has(#sw-reuse_old)")
        check(pg.locator("#sw-reuse_old").is_checked() and "Dùng lại món của hộp cũ" in reuse_row.inner_text() and "ngày" in reuse_row.inner_text(), "reuse: the switch is on and says what it does")
        check(reuse_row.locator("input[type=number]").input_value() == "30", "reuse: thirty days by default")
        reuse_row.locator("input[type=number]").fill("14"); reuse_row.locator("input[type=number]").press("Tab")
        pg.wait_for_selector(".toast:has-text('Đã lưu')", timeout=8000)
        check({x["key"]: x for x in json.load(urllib.request.urlopen(BASE + "/api/automations"))["switches"]}["reuse_old"]["config"]["days"] == 14, "reuse: the number of days is saved")
        reuse_row.locator("input[type=number]").fill("30"); reuse_row.locator("input[type=number]").press("Tab")
        pg.wait_for_function("document.querySelector('#sw-reuse_old').closest('.sw-row').querySelector('input[type=number]').value === '30'")
        # turning the watch off removes every alert: that change is announced once, in the live region
        pg.evaluate("document.querySelector('#sr-live').textContent = ''")      # drop the text from the earlier switch
        pg.locator("label.sw[for=sw-catalog_watch]").click()
        try:
            pg.wait_for_function("document.querySelector('#sr-live').textContent.includes('cảnh báo')", timeout=8000)
        except Exception:
            pass
        check("0 cảnh báo" in pg.inner_text("#sr-live"), f"change announced: {pg.inner_text('#sr-live')!r}")
        pg.locator("label.sw[for=sw-catalog_watch]").click()
        pg.wait_for_function("document.querySelector('#sw-catalog_watch').checked")
        # a bad config value is refused and the UI says so
        pg.fill("input[type=number][min='1'][max='720']", "0"); pg.keyboard.press("Tab")
        pg.wait_for_selector(".toast.bad")
        check(True, "bad number rejected with a toast")

        # --- the sync panel: where the catalog stands, and a one-click install link
        check(pg.locator("#sync-install").get_attribute("href") == "/box_builder_sm.user.js", "sync: the install link points at the script the app serves")
        check("Chưa đồng bộ lần nào" in pg.inner_text("#sync-status"), "sync: says plainly that the kho has never been synced")
        script_text = urllib.request.urlopen(BASE + "/box_builder_sm.user.js").read().decode()
        check(script_text.startswith("// ==UserScript==") and f'const DEFAULT_APP = "{BASE}"' in script_text, "sync: the served script holds this app's address")
        # the install route that does not rely on Tampermonkey catching the link: open the code, copy it, paste it into "Create a new script"
        check("Cách B" in pg.inner_text("#a-sync") and pg.locator("#sync-copy").is_visible(), "sync: a copy-and-paste way to install is shown next to the link")
        check(pg.locator("#sync-code").is_hidden() and not pg.input_value("#sync-code"), "sync: the code box starts closed and empty")
        pg.locator("#sync-code-box summary").click()
        pg.wait_for_function("document.querySelector('#sync-code').value.startsWith('// ==UserScript==')", timeout=8000)
        txt_copy = urllib.request.urlopen(BASE + "/box_builder_sm.txt").read().decode()
        check(pg.input_value("#sync-code") == txt_copy == script_text, "sync: the box holds exactly the script the install link serves")
        pg.locator("#sync-code-box summary").click()
        pg.locator("#sync-copy").click()
        pg.wait_for_function("document.querySelector('#sync-copy-msg').textContent.length > 0", timeout=8000)
        copy_msg = pg.inner_text("#sync-copy-msg")
        check("Đã sao chép" in copy_msg or "Ctrl+C" in copy_msg, f"sync: the copy button answers either way: {copy_msg!r}")
        check("Đã sao chép" in copy_msg or pg.locator("#sync-code").is_visible(), "sync: when the clipboard is blocked the code is opened and selected for Ctrl+C")

        # --- backup and restore: a bad file is refused, a good one is previewed first, and restoring twice adds nothing
        free3 = [o["id"] for o in json.load(urllib.request.urlopen(BASE + "/api/objects?status=free&per=3"))["items"]]
        bk_dir = tempfile.mkdtemp(dir=tmp)                  # inside the test's own temp dir, so it is cleaned up with it
        bad_path, good_path = os.path.join(bk_dir, "bad.json"), os.path.join(bk_dir, "good.json")
        with open(bad_path, "w", encoding="utf-8") as fh: fh.write("{{{ not json")
        with open(good_path, "w", encoding="utf-8") as fh:
            json.dump({"format": "box-builder-backup", "version": 1, "boxes": [
                {"module": "M5", "label": "Box A1", "count": 3, "status": "approved", "items": [{"pos": n + 1, "object_id": oid} for n, oid in enumerate(free3)]}],
                "used": [], "automations": []}, fh)
        check(pg.is_disabled("#bk-preview") and pg.is_disabled("#bk-restore"), "backup: preview and restore start switched off")
        pg.set_input_files("#bk-file", bad_path)
        pg.wait_for_selector(".toast.bad:has-text('không phải JSON')", timeout=8000)
        check(pg.is_disabled("#bk-preview"), "backup: a file that is not JSON is refused with a plain message")
        pg.set_input_files("#bk-file", good_path)
        pg.wait_for_function("!document.querySelector('#bk-preview').disabled")
        check(pg.is_disabled("#bk-restore"), "backup: restore stays off until a preview has run")
        pg.click("#bk-preview")
        pg.wait_for_selector("#bk-body tr")
        check("Sẽ thêm 1 hộp (3 món)" in pg.inner_text("#bk-summary") and "M5 Box A1" in pg.inner_text("#bk-body"), "backup: the preview says what would be added")
        check(not any(b["module"] == "M5" for b in json.load(urllib.request.urlopen(BASE + "/api/state"))["boxes"]), "backup: a preview writes nothing")
        pg.click("#bk-restore")
        pg.wait_for_selector(".toast:has-text('Đã khôi phục 1 hộp')", timeout=15000)
        st_bk = json.load(urllib.request.urlopen(BASE + "/api/state"))
        got = next((b for b in st_bk["boxes"] if b["module"] == "M5"), None)
        check(got is not None and got["status"] == "approved" and [i["id"] for i in got["items"]] == free3, "backup: the box came back complete and approved")
        pg.set_input_files("#bk-file", good_path); pg.wait_for_function("!document.querySelector('#bk-preview').disabled"); pg.click("#bk-preview")
        pg.wait_for_function("document.querySelector('#bk-body').innerText.includes('đã có hộp cùng tên')", timeout=8000)
        check(pg.is_disabled("#bk-restore"), "backup: restoring the same file again would add nothing, so the button stays off")
        dl = pg.locator("#bk-download").get_attribute("href")
        check(dl == "/api/backup.json", "backup: the download link points at the export")

        # --- import boxes from a CSV: bad rows are listed, a preview writes nothing, the box comes in as a draft, a second import adds nothing
        csv_ids = [o["id"] for o in json.load(urllib.request.urlopen(BASE + "/api/objects?status=free&per=3"))["items"]]
        csv_path = os.path.join(bk_dir, "boxes.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:      # newline="": the same bytes on Windows and Linux
            fh.write("module,box,stt,object_id,qty\n" + "".join(f"M6,Box A1,{n + 1},{oid},1\n" for n, oid in enumerate(csv_ids)) + "M6,Box A1,4,abc,1\n")
        check(pg.is_disabled("#csv-preview") and pg.is_disabled("#csv-import"), "csv import: preview and import start switched off")
        pg.set_input_files("#csv-file", csv_path); pg.wait_for_function("!document.querySelector('#csv-preview').disabled"); pg.click("#csv-preview")
        pg.wait_for_function("document.querySelector('#csv-summary').innerText.includes('Sẽ thêm')", timeout=8000)
        check("Sẽ thêm 1 hộp nháp (3 món)" in pg.inner_text("#csv-summary") and "1 dòng không đọc được" in pg.inner_text("#csv-summary"), f"csv import: the preview counts boxes and bad rows: {pg.inner_text('#csv-summary')!r}")
        check("Dòng 5" in pg.inner_text("#csv-bad") and "M6 Box A1" in pg.inner_text("#csv-body"), "csv import: the bad row is named by its line")
        check(not any(b["module"] == "M6" for b in json.load(urllib.request.urlopen(BASE + "/api/state"))["boxes"]), "csv import: a preview writes nothing")
        pg.click("#csv-import")
        pg.wait_for_selector(".toast:has-text('Đã nhập 1 hộp nháp')", timeout=15000)
        got6 = next((b for b in json.load(urllib.request.urlopen(BASE + "/api/state"))["boxes"] if b["module"] == "M6"), None)
        check(got6 is not None and got6["status"] == "draft" and [i["id"] for i in got6["items"]] == csv_ids and {i["source"] for i in got6["items"]} == {"import"}, "csv import: the box came in as a draft with its items")
        pg.click("#csv-preview")
        pg.wait_for_function("document.querySelector('#csv-body').innerText.includes('đã có hộp cùng tên')", timeout=8000)
        check(pg.is_disabled("#csv-import"), "csv import: importing the same file again would add nothing, so the button stays off")
        # a file that is not UTF-8 reaches the server as it is, so the person is told to save it as UTF-8 (the page must not silently garble it)
        latin = os.path.join(bk_dir, "latin1.csv")
        with open(latin, "wb") as fh: fh.write(b"module,box,object_id\nM6,Box \xe9,000001\n")
        pg.set_input_files("#csv-file", latin)
        check(pg.locator("#csv-body").is_hidden() and pg.is_disabled("#csv-import"), "csv import: choosing another file clears the old result and keeps import off")
        pg.click("#csv-preview")
        pg.wait_for_function("document.querySelector('#csv-summary').innerText.includes('UTF-8')", timeout=8000)
        check(pg.is_disabled("#csv-import") and pg.locator("#csv-summary").is_visible(), "csv import: a file that is not UTF-8 is refused with a message that stays on the page")
        check("UTF-8" in pg.inner_text("#sr-live"), "csv import: the message is announced to screen readers")
        urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/boxes/{got6['id']}", method="DELETE"))      # leave the later checks as they were

        # --- AI batch plan -> edit -> create
        pg.click("#b-section")
        check(pg.input_value("#b-labels") == "Box B2, Box B3, Box A1", "section preset fills the three box names")
        pg.fill("#b-labels", "")
        pg.click("#b-cats summary")
        pg.wait_for_selector("#b-cats-list label")
        picked_cat = pg.evaluate("document.querySelector('#b-cats-list label').childNodes[1].textContent")
        pg.locator("#b-cats-list input").first.check()
        check("đã chọn 1" in pg.inner_text("#b-cats-sum"), "batch category picker counts the selection")
        check(pg.locator("#cats-list input:checked").count() == 0, "the new-box picker is not affected by the batch picker")
        pg.fill("#b-module", "M9"); pg.fill("#b-n", "3")
        pg.fill("#b-brief", "bàn bếp\nkhu vườn nhỏ\ndọn dẹp nhà")
        pg.click("#b-plan")
        pg.wait_for_selector("#b-result:not([hidden])")
        check(pg.evaluate("document.activeElement.id") == "b-result", "focus moves to the plan once it is ready")
        check(pg.locator("#b-body tr").count() == 3, "plan has 3 rows")
        labels = [pg.locator("#b-body tr").nth(i).locator("input").first.input_value() for i in range(3)]
        check(labels == ["Box A1", "Box B1", "Box C1"], f"labels incremented {labels}")
        pg.locator("#b-body tr").nth(2).locator("button").click()
        check(pg.locator("#b-body tr").count() == 2 and "Tạo 2 hộp" in pg.inner_text("#b-create"), "row removed, button count updated")
        check(picked_cat in pg.inner_text("#b-cats-note"), "the plan says which category the boxes are limited to")
        pg.screenshot(path=f"{OUT}/plan.png", full_page=True)
        pg.click("#b-create")
        pg.wait_for_selector("#view-boxes:not([hidden])")
        check(pg.evaluate("document.activeElement.id") == "view-boxes", "after creating, focus lands on the boxes panel")
        pg.wait_for_function("document.querySelectorAll('.tray[aria-label^=\"M9\"]').length === 2", timeout=30000)
        # auto-approve is ON, so these fresh model-built boxes should end up approved
        pg.wait_for_function("document.querySelectorAll('.tray[aria-label^=\"M9\"] .pill.approved').length === 2", timeout=40000)
        check(True, "batch boxes built and auto-approved")
        st = json.load(urllib.request.urlopen(BASE + "/api/state"))
        ids = [i["id"] for bx in st["boxes"] for i in bx["items"]]
        check(len(ids) == len(set(ids)), f"no duplicate objects across {len(st['boxes'])} boxes ({len(ids)} items)")
        m9 = [i for bx in st["boxes"] if bx["module"] == "M9" for i in bx["items"]]
        check(len(m9) >= 42 and all(i["category"] == picked_cat for i in m9), f"M9 boxes only hold objects from '{picked_cat}'")

        # groups: a box with a section code lands in its own group under the same module
        urllib.request.urlopen(urllib.request.Request(BASE + "/api/boxes", method="POST", headers={"Content-Type": "application/json"},
            data=json.dumps({"module": "M8", "label": "Box B2", "theme": "t", "count": 3, "code": "M8-02"}).encode()))
        pg.wait_for_selector(".group-head:has-text('M8 · M8-02')", timeout=30000)
        heads = pg.locator(".group-head span").all_inner_texts()
        check(heads == sorted(heads, key=lambda t: (t.split(" · ")[0], len(t))) and "M8" in heads and "M9" in heads,
              f"groups are ordered by module then section {heads}")
        check(pg.locator(".tray[aria-label='M8 Box B2'] .sec").inner_text() == "M8-02", "section code chip shown on the box")
        check(pg.locator(".tray[aria-label='M8 Box A1'] .sec").count() == 0, "no chip when the box has no section code")
        order = pg.evaluate("[...document.querySelectorAll('#boxes > .group-head, #boxes > .tray')].map(e => e.classList.contains('group-head') ? 'H' : 'T')")
        check(order[0] == "H" and order.count("H") == len(heads), "every group has one header, first child is a header")

        # queue + log
        pg.click(".tab[data-view=auto]")
        try:
            pg.wait_for_function("document.querySelector('#a-queue').innerText.includes('Sẵn sàng (4)')", timeout=8000)
        except Exception:
            pass
        check("Sẵn sàng (4)" in pg.inner_text("#a-queue"), "queue lists 4 ready boxes (two batch boxes, the sectioned one, and the restored backup box)")
        check("tự duyệt" in pg.inner_text("#a-log").lower(), "log mentions auto-approve")
        pg.screenshot(path=f"{OUT}/auto2.png", full_page=True)


        # --- pictures: sync a catalog where a few objects carry image URLs, then look at table, lightbox and slots
        rows, page = [], 1
        while True:
            res = json.load(urllib.request.urlopen(f"{BASE}/api/objects?per=200&page={page}"))
            rows += [{"id": o["id"], "name": o["name"], "vi": o["vi"], "type": "Obj", "status": "ACTIVE", "avail": o["stock"],
                      "qty": o["stock"], "loc": o["slot"], "sector": "Hanoi-GPI"} for o in res["items"]]
            if page >= res["pages"]: break
            page += 1
        with_pic = [r["id"] for r in rows[:3]]
        dead = rows[3]["id"]
        for r in rows[:3]: r["image_url"] = f"https://sm.config.inc/media/ok-{r['id']}.png"
        rows[3]["image_url"] = "https://sm.config.inc/media/missing.png"
        rows[4]["image_url"] = "https://evil.example.com/steal.png"            # must never reach the page
        stale_id, fresh_id = rows[5]["id"], rows[6]["id"]
        rows[5]["image_url"] = f"https://sm.config.inc/media/old-{stale_id}.png?Expires={int(time.time()) - 600}&Signature=abc"      # ran out ten minutes ago
        rows[6]["image_url"] = f"https://sm.config.inc/media/fresh-{fresh_id}.png?Expires={int(time.time()) + 3000}&Signature=abc"  # about fifty minutes left
        stale_hits = []
        pg.route("https://sm.config.inc/media/old-*", lambda route: (stale_hits.append(route.request.url), route.abort()))
        pg.route("https://sm.config.inc/media/fresh-*", lambda route: route.fulfill(status=200, content_type="image/png", body=png()))
        pg.route("https://sm.config.inc/media/ok-*", lambda route: route.fulfill(status=200, content_type="image/png", body=png()))
        pg.route("https://sm.config.inc/media/missing.png", lambda route: route.fulfill(status=404, body="no"))
        evil_hits = []
        pg.route("https://evil.example.com/**", lambda route: (evil_hits.append(route.request.url), route.abort()))
        req = urllib.request.Request(BASE + "/api/catalog/sync", method="POST", data=json.dumps({"objects": rows}).encode(),
                                     headers={"Content-Type": "application/json", "X-Sync-Token": "t"})
        check(urllib.request.urlopen(req).status == 200, "catalog synced with picture URLs")
        pg.click(".tab[data-view=objects]")
        pg.fill("#o-q", "")
        pg.select_option("#o-cat", "")                     # earlier steps left a category and a status filter on
        pg.select_option("#o-sort", "id")
        pg.locator(".fchip").first.click()
        pg.wait_for_function(f"document.querySelector('#o-body .bbt[data-id=\"{with_pic[0]}\"] img')", timeout=10000)
        pg.wait_for_timeout(500)
        ok_imgs = pg.evaluate("ids => ids.map(i => { const im = document.querySelector(`#o-body .bbt[data-id=\"${i}\"] img`); return !!im && im.naturalWidth > 0; })", with_pic)
        check(all(ok_imgs), f"three pictures rendered in the table {ok_imgs}")
        check(pg.locator(f"#o-body .bbt[data-id='{dead}'] img").count() == 0 and pg.locator(f"#o-body .bbt[data-id='{dead}'] svg.bbt-ph").count() == 1,
              "a dead picture URL falls back to the placeholder")
        check(not evil_hits, f"a picture from a foreign host was never requested {evil_hits}")
        # signed SM links run out: the page does not ask for an expired one, says why, and a link with time left still loads
        stale = pg.locator(f"#o-body .bbt[data-id='{stale_id}']")
        check(stale.locator("img").count() == 0 and stale.locator("svg.bbt-ph").count() == 1 and "bbt-expired" in (stale.get_attribute("class") or ""),
              "expired link: placeholder, no picture element")
        check("hết hạn" in (stale.get_attribute("title") or "") and "Đồng bộ lại kho trong SM" in (stale.get_attribute("title") or ""), "expired link: the tooltip says what to do")
        check(not stale_hits, f"expired link: the picture was never requested {stale_hits}")
        check(pg.evaluate("id => { const im = document.querySelector(`#o-body .bbt[data-id=\"${id}\"] img`); return !!im && im.naturalWidth > 0; }", fresh_id), "a link with time left still loads")
        check(pg.locator("#o-imgtip").is_visible() and "1 ảnh ở trang này đã hết hạn" in pg.inner_text("#o-imgtip-text") and "Đồng bộ kho ngay" in pg.inner_text("#o-imgtip-text"),
              f"one tip above the table explains it: {pg.inner_text('#o-imgtip-text')!r}")
        pg.click("#o-imgtip-go")
        pg.wait_for_selector("#view-auto:not([hidden])", timeout=8000)
        check(pg.locator("#a-sync").is_visible() and "Ảnh của món do SM cấp có hạn" in pg.inner_text("#a-sync"), "the tip's button leads to the sync steps, which say pictures last about an hour")
        check(pg.evaluate("document.activeElement && document.activeElement.id") == "sync-h", "the tip's button moves focus to the sync steps (its own view is hidden)")
        pg.click(".tab[data-view=objects]")
        pg.wait_for_function(f"document.querySelector('#o-body .bbt[data-id=\"{with_pic[0]}\"] img')", timeout=10000)
        pg.locator(f"#o-body .bbt[data-id='{with_pic[0]}']").click()
        pg.wait_for_selector("dialog.bbt-dlg[open]")
        link = pg.locator("dialog.bbt-dlg a").get_attribute("href")
        check(link == f"https://sm.config.inc/object_detail/{with_pic[0]}", f"lightbox links to the SM object page {link}")
        pg.screenshot(path=f"{OUT}/lightbox.png")
        pg.keyboard.press("Escape")
        check(pg.locator("dialog.bbt-dlg[open]").count() == 0, "Escape closes the lightbox")
        pg.click(".tab[data-view=boxes]")
        pg.wait_for_selector(".tray .slot .bbt")
        check(pg.locator(".tray .slot .bbt").count() >= 20, "box slots show a thumbnail or placeholder")
        pg.screenshot(path=f"{OUT}/boxes-with-pics.png", full_page=False)

        # --- the guide and the text size are remembered, a network failure reads kindly
        pg.click("#guide-close")
        check(pg.locator("#guide").is_hidden(), "the guide can be hidden")
        pg.click("#fs-up")
        check(pg.evaluate("getComputedStyle(document.body).zoom") == "1.15", "A+ makes everything larger")
        pg.reload(); pg.wait_for_selector("#live b:has-text('Trực tiếp')")
        check(pg.locator("#guide").is_hidden() and pg.evaluate("getComputedStyle(document.body).zoom") == "1.15",
              "both choices survive a reload")
        pg.click("#guide-open")
        check(pg.locator("#guide").is_visible(), "Hướng dẫn in the header brings the guide back")
        pg.click("#fs-down")
        check(pg.evaluate("getComputedStyle(document.body).zoom") == "1" and pg.is_disabled("#fs-down"), "A- returns to normal size and stops there")
        pg.route("**/api/used", lambda route: route.abort())
        pg.fill("#u-text", "123456"); pg.click("#used button[type=submit]")
        pg.wait_for_selector(".toast.bad:has-text('Không kết nối được')", timeout=8000)
        check(True, "a dropped connection says what to do, not 'Failed to fetch'")
        pg.unroute("**/api/used")

        # --- reuse rule on the used form: the day the boxes were made, and an old day makes the object usable again
        old_day = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 45 * 86400))
        some = [o["id"] for o in json.load(urllib.request.urlopen(BASE + "/api/objects?status=free&per=2"))["items"]]
        check(pg.locator("#u-since").get_attribute("type") == "date" and "để trống là hôm nay" in pg.inner_text("#used"), "reuse: the used form asks for the day the boxes were made")
        pg.fill("#u-text", some[0]); pg.fill("#u-since", old_day); pg.click("#used button[type=submit]")
        pg.wait_for_selector(".toast:has-text('Đã đánh dấu 1 món')", timeout=8000)
        pg.wait_for_function("document.querySelector('#u-count').innerText.includes('đủ cũ để dùng lại')", timeout=8000)
        check("1 món đã đủ cũ để dùng lại" in pg.inner_text("#u-count"), f"reuse: the list says the old one is usable again: {pg.inner_text('#u-count')!r}")
        st_old = next(o["status"] for o in json.load(urllib.request.urlopen(BASE + f"/api/objects?q={some[0]}&per=5"))["items"] if o["id"] == some[0])
        check(st_old == "free", "reuse: an ID used by boxes 45 days ago is still free")
        pg.fill("#u-text", some[1]); pg.click("#used button[type=submit]")                       # no day: today
        end, st_new = time.time() + 8, ""
        while time.time() < end and st_new != "external":                                         # wait for the state itself (the first paste's toast may still be on screen)
            time.sleep(0.2)
            st_new = next(o["status"] for o in json.load(urllib.request.urlopen(BASE + f"/api/objects?q={some[1]}&per=5"))["items"] if o["id"] == some[1])
        pg.wait_for_function("document.querySelector('#u-text').value === ''", timeout=8000)
        check(st_new == "external" and pg.input_value("#u-since") == "", f"reuse: an ID used today is held, and the day field was cleared after the paste (status {st_new!r}, field {pg.input_value('#u-since')!r})")
        for oid in some:
            urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/used/{oid}", method="DELETE"))       # leave the later checks as they were

        # --- the push, tried on the fake SM: the approved boxes become TaskBoxes there and show as pushed
        pg.click(".tab[data-view=auto]")
        pg.wait_for_selector("#a-smpush:not([hidden])", timeout=8000)
        check("SM giả" in pg.inner_text("#smp-h") and "không đụng tới SM thật" in pg.inner_text("#a-smpush"), "fake push: the panel says it is a fake and touches no real SM")
        ready_before = len(json.load(urllib.request.urlopen(BASE + "/api/queue"))["ready"])
        pg.click("#smp-go")
        pg.wait_for_selector("#smp-body tr", timeout=15000)
        check(f"Tạo được {ready_before} hộp trong SM giả" in pg.inner_text("#smp-sum"), f"fake push: it created the {ready_before} ready boxes")
        check(pg.locator("#smp-body .pill.st-free").count() == ready_before and "MBX-" in pg.inner_text("#smp-body"), "fake push: the table shows each box and its code in the fake")
        pg.wait_for_function("!document.querySelector('#smp-list').innerText.includes('Chưa có hộp nào')", timeout=8000)
        check(pg.locator("#smp-list li").count() == ready_before, "fake push: the list of what the fake holds matches")
        check(len(json.load(urllib.request.urlopen(BASE + "/api/queue"))["ready"]) == 0, "fake push: nothing is left in the queue")
        # the other steps on the same fake box: borrow before assigning is refused and says why, then the whole path, then the return
        pg.locator("#smp-list input[type=checkbox]").first.check()
        pg.click("#smp-moves [data-move=borrow]")
        pg.wait_for_selector("#smp-body .pill:has-text('Không làm được')", timeout=8000)
        check("Unassigned" in pg.inner_text("#smp-body"), "fake steps: borrowing a box nobody has been given is refused with the reason")
        pg.fill("#smp-who", "An")
        for move, word in (("assign", "Assigned"), ("borrow", "Borrowed"), ("floor", "On the floor")):
            pg.locator("#smp-list input[type=checkbox]").first.check()
            pg.click(f"#smp-moves [data-move={move}]")
            pg.wait_for_function("w => document.querySelector('#smp-list').innerText.includes(w)", arg=word, timeout=8000)
            check(word in pg.inner_text("#smp-list"), f"fake steps: the box is now {word}")
        pg.fill("#smp-days", "0"); pg.click("#smp-old-preview")
        pg.wait_for_selector("#smp-body .pill:has-text('Sẽ trả')", timeout=8000)
        check("Returned" not in pg.inner_text("#smp-list") and not pg.is_disabled("#smp-old-go"), "fake steps: the preview returns nothing and switches the confirm button on")
        pg.click("#smp-old-go")
        pg.wait_for_function("document.querySelector('#smp-list').innerText.includes('Returned')", timeout=8000)
        check("Đã trả 1 hộp" in pg.inner_text("#smp-sum"), "fake steps: confirming returns the old box")
        pg.click(".tab[data-view=boxes]")
        pg.wait_for_function("document.querySelectorAll('.pill.pushed').length >= 1", timeout=8000)
        check(pg.locator(".pill.pushed").count() == ready_before, "fake push: the boxes now show 'Đã lên SM'")

        # --- the paper for supporters opens in a new tab: approved boxes, sorted by shelf, with a print button
        check(pg.locator("#print-link").get_attribute("href") == "/print/boxes", "print: the header has a link to the pick list")
        pg.click(".tab[data-view=boxes]")
        with pg.context.expect_page() as popup:
            pg.click("#print-link")
        paper = popup.value
        paper.wait_for_load_state()
        check(paper.locator("table").count() >= 2 and "Phiếu lấy đồ" in paper.inner_text("body"), "print: the page shows the approved boxes in tables")
        check(paper.locator("button:has-text('In phiếu')").count() == 1, "print: it has one print button")
        check(paper.locator("script").count() == 0, "print: the page runs no scripts")
        paper.emulate_media(media="print")
        check(paper.locator("button:has-text('In phiếu')").is_hidden(), "print: the button is hidden on paper")
        paper.close()

        # --- optional: run axe-core on every tab when AXE_JS points at axe.min.js (kept out of the repo on purpose)
        axe_js = os.environ.get("AXE_JS")
        if axe_js and os.path.exists(axe_js):
            for tab in ("boxes", "objects", "auto"):
                pg.click(f".tab[data-view={tab}]"); pg.wait_for_timeout(800)
                pg.add_script_tag(path=axe_js)
                found = pg.evaluate("axe.run(document, {resultTypes: ['violations']}).then(r => r.violations.map(v => v.id + ' x' + v.nodes.length))")
                check(not found, f"axe-core finds no violations on the {tab} tab {found}")
        else:
            print("skip axe-core run (set AXE_JS to the path of axe.min.js to include it)")

        # --- phone width
        ph = b.new_context(viewport={"width": 390, "height": 800}).new_page()
        ph.on("pageerror", lambda e: errs.append("phone pageerror: " + str(e)))
        ph.goto(BASE + "#kho"); ph.wait_for_selector("#o-body td.oid")
        w = ph.evaluate("document.documentElement.scrollWidth")
        check(w <= 392, f"objects view fits phone width (scrollWidth {w})")
        ph.screenshot(path=f"{OUT}/phone-objects.png")
        ph.goto(BASE + "#tudong"); ph.wait_for_selector(".sw-row")
        w = ph.evaluate("document.documentElement.scrollWidth")
        check(w <= 392, f"automations view fits phone width (scrollWidth {w})")
        ph.screenshot(path=f"{OUT}/phone-auto.png", full_page=True)
        b.close()
except Exception:
    print("NET", net[-6:], "ERRS", errs[-3:])
    try:
        for bx in json.load(urllib.request.urlopen(BASE + "/api/state"))["boxes"]:
            print("STATE", bx["module"], bx["label"], bx["status"], len(bx["items"]), "/", bx["count"], repr(bx["message"]))
    except Exception as e:
        print("could not read state:", e)
    raise
finally:
    stop_server(srv, srv_log, tmp)

errs = [e for e in errs if "favicon" not in e and "422" not in e and "404" not in e and "ERR_FAILED" not in e]   # 422, 404 and ERR_FAILED = the bad number, the dead picture and the dropped connection the test uses on purpose
check(not errs, f"no browser errors {errs[:3]}")
print("FAILED" if fails else "ALL UI CHECKS PASSED")
sys.exit(1 if fails else 0)

"""Browser test of the object browser, automations and AI batch flow on a throw-away instance (own port, own DB).
Run:  pip install playwright && python -m playwright install chromium && python tests/ui_automation_e2e.py
Screenshots go to the system temp dir. Never touches SM, Render or Neon.
Optional: AXE_JS=/path/to/axe.min.js (from the axe-core npm package) adds an accessibility run on every tab."""
import os, subprocess, sys, tempfile, time, urllib.request, json
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(tempfile.gettempdir(), "box-builder-ui-shots")
os.makedirs(OUT, exist_ok=True)
PORT = 8091
BASE = f"http://127.0.0.1:{PORT}"
tmp = tempfile.mkdtemp()
env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DEEPSEEK_API_KEY")}
env.update(DB_PATH=os.path.join(tmp, "ui.db"), CATALOG_PATH=os.path.join(ROOT, "data/catalog.csv"), SYNC_TOKEN="t")
srv = subprocess.Popen([os.path.join(ROOT, ".venv/bin/uvicorn"), "app.main:app", "--port", str(PORT)], cwd=ROOT, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        urllib.request.urlopen(BASE + "/api/config", timeout=1); break
    except Exception:
        time.sleep(0.2)


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
        b = pw.chromium.launch()
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
        check("Duyệt" in pg.inner_text("#nextstep"), "the next step now says to review and approve the draft")
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
        raw = open(dl.value.path(), "rb").read().decode("utf-8")
        lines = [ln for ln in raw.lstrip("\ufeff").splitlines() if ln]
        check(raw.startswith("\ufeff") and lines[0].startswith("id,name,vietnamese_name"), "CSV download has the BOM and the header")
        check(len(lines) - 1 == shown, f"CSV holds all {shown} filtered objects (got {len(lines) - 1})")
        check(dl.value.suggested_filename == "objects.csv", "download is named objects.csv")

        # --- automations tab
        pg.click(".tab[data-view=auto]")
        pg.wait_for_selector(".sw-row")
        check(pg.locator(".sw-row").count() == 6, "six switches")
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
        # keyboard: space toggles a switch
        pg.focus("#sw-sheet_export"); pg.keyboard.press("Space")
        pg.wait_for_function("!document.querySelector('#sw-sheet_export').checked")
        check(wait_switch("sheet_export", False), "sheet export OFF via keyboard")
        try:
            pg.wait_for_function("document.activeElement && document.activeElement.id === 'sw-sheet_export'", timeout=5000)
        except Exception:
            pass
        check(pg.evaluate("document.activeElement.id") == "sw-sheet_export", "keyboard focus stays on the switch after it saves")
        check(pg.locator(".sw-state[aria-hidden=true]").count() == 6, "the visual On/Off text is hidden from screen readers (the switch already says it)")
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
            pg.wait_for_function("document.querySelector('#a-queue').innerText.includes('Sẵn sàng (3)')", timeout=8000)
        except Exception:
            pass
        check("Sẵn sàng (3)" in pg.inner_text("#a-queue"), "queue lists 3 ready boxes (the two batch boxes and the sectioned one, all auto-approved)")
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
        pg.wait_for_selector(".toast.bad:has-text('Không kết nối được máy chủ')", timeout=8000)
        check(True, "a dropped connection says what to do, not 'Failed to fetch'")
        pg.unroute("**/api/used")

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
    srv.terminate()

errs = [e for e in errs if "favicon" not in e and "422" not in e and "404" not in e and "ERR_FAILED" not in e]   # 422, 404 and ERR_FAILED = the bad number, the dead picture and the dropped connection the test uses on purpose
check(not errs, f"no browser errors {errs[:3]}")
print("FAILED" if fails else "ALL UI CHECKS PASSED")
sys.exit(1 if fails else 0)

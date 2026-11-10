"""Browser test of the admin page with login ON: login, the list of people, the sheet box, the heavy-use chart, the age tables, the borrow column.
Own port, own database, own catalog snapshot. Never contacts Scenario Manager, Google, Render or Neon (everything is 127.0.0.1).
Run:  python tests/admin_e2e.py      (needs playwright + its Chromium; AXE_JS=path/to/axe.min.js adds the accessibility runs)"""
import csv, http.cookiejar, json, os, sqlite3, subprocess, sys, tempfile, time, urllib.error, urllib.request

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("ADMIN_E2E_PORT", "8093"))
BASE = f"http://127.0.0.1:{PORT}"
OUT = os.path.join(tempfile.gettempdir(), "box-builder-admin-shots")
os.makedirs(OUT, exist_ok=True)
tmp = tempfile.mkdtemp()
env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "DEEPSEEK_API_KEY", "ACCESS_CODE", "ADMIN_CODE")}
env.update(DB_PATH=os.path.join(tmp, "admin.db"), CATALOG_PATH=os.path.join(ROOT, "data", "catalog.csv"), SYNC_TOKEN="t", ADMIN_IDS="1001",
           PYTHONIOENCODING="utf-8")
srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(PORT)], cwd=ROOT, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(100):
    try:
        urllib.request.urlopen(BASE + "/api/config", timeout=1)
        break
    except Exception:
        time.sleep(0.2)

fails, errs, bad_net = [], [], []
checks = 0


def check(cond, msg):
    global checks
    checks += 1
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)


class Api:
    """A plain HTTP client with a cookie jar, for setting the scene (the browser then looks at the result)."""
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None, headers=None):
        h = {"Content-Type": "application/json", "Origin": BASE, **(headers or {})}
        req = urllib.request.Request(BASE + path, data=None if body is None else json.dumps(body).encode(), headers=h, method=method)
        try:
            with self.op.open(req, timeout=60) as r:
                raw = r.read().decode()
                return r.status, (json.loads(raw) if raw and raw.lstrip()[:1] in "{[" else raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw


def catalog_rows():
    with open(os.path.join(ROOT, "data", "catalog.csv"), encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    out = []
    for n, r in enumerate(rows[1:]):
        if len(r) < 8 or not r[0].strip().isdigit():
            continue
        qty = int(r[7]) if r[7].strip().isdigit() else 0
        if r[6].strip() != "ACTIVE" or not r[2].strip() or qty <= 0:          # only objects the app can use, so the borrow counts below land on real ones
            continue
        out.append({"id": r[0].strip().zfill(6), "name": r[1], "vi": r[2], "type": "Obj", "status": "ACTIVE", "avail": qty, "qty": qty, "loc": r[5],
                    "sector": "Hanoi-GPI"})
    return out


def day(n):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - n * 86400))


try:
    admin = Api()
    st, body = admin.call("POST", "/api/auth/login", {"id": "1001"})
    check(st == 200 and body["go"] == "/", "the admin signs in through the API")
    st, body = admin.call("POST", "/api/admin/people", {"text": "2002, Hải, M2\n3003, Hưng, M3"})
    check(st == 200 and body["added"] == 2, "two people are on the list")

    # the kho with borrow counts: a long tail with a few objects over 500, some with unknown counts, ages from 40 to 900 days
    rows = catalog_rows()
    top = [1587, 1083, 892, 825, 811, 786, 640, 610, 560, 520, 505]
    for n, row in enumerate(rows):
        if n < len(top):
            row["uses"], row["created"] = top[n], day(120 + n * 60)
        elif n % 7 == 0:
            row["uses"], row["created"] = None, None                       # SM sent no number for these
        else:
            row["uses"], row["created"] = (n * 37) % 480, day(30 + (n * 11) % 800)
    st, body = admin.call("POST", "/api/catalog/sync", {"objects": rows}, {"X-Sync-Token": "t"})
    check(st == 200 and body["usable"] > 50, f"the kho with borrow counts is synced ({body.get('usable') if isinstance(body, dict) else body})")
    ids = [r["id"] for r in rows]
    boxes = [{"box_id": f"SM-{n}", "title": f"hộp {n}", "status": "assigned", "status_label": "Assigned", "created_at": day(age),
              "contents": [{"object_id": ids[20 + n * 3 + k], "quantity": 1} for k in range(3)]} for n, age in enumerate([2, 5, 9, 20, 40, 75, 100])]
    st, body = admin.call("POST", "/api/sm/boxes/sync", {"boxes": boxes}, {"X-Sync-Token": "t"})
    check(st == 200 and body["active"] == 7, "seven SM boxes of different ages are read")
    st, usage = admin.call("GET", "/api/usage")
    check(st == 200 and usage["over_start"] >= len(top), f"the API counts the objects at 500 or more ({usage.get('over_start')})")

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1400, "height": 1100}, permissions=["clipboard-read", "clipboard-write"])
        pg = ctx.new_page()
        pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
        # refusals this test provokes on purpose: a wrong ID (401), the sheet link that is not a sheet (422), the admin API asked by a user (403)
        expected = {("PUT", "/api/sheet", 422), ("GET", "/api/state", 403)}
        pg.on("console", lambda m: errs.append("console: " + m.text) if m.type == "error" and not any(f"status of {c}" in m.text for c in (401, 422, 403, 429)) else None)
        pg.on("response", lambda r: bad_net.append(f"{r.request.method} {r.url.replace(BASE, '')} -> {r.status}")
              if r.status >= 400 and r.status not in (401, 429) and (r.request.method, r.url.replace(BASE, ""), r.status) not in expected else None)
        axe_js = os.environ.get("AXE_JS")

        def axe(label):
            if not axe_js or not os.path.exists(axe_js):
                return
            pg.add_script_tag(path=axe_js)
            res = pg.evaluate("axe.run(document, {runOnly: ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa','best-practice']}).then(r => r.violations.map(v => v.id + ': ' + v.nodes.length + ' ' + v.nodes[0].target.join(' ')))")
            check(res == [], f"axe: {label}" + ("" if res == [] else " " + "; ".join(res)))

        # --- not signed in: sent to the login page; a wrong ID is refused in plain words
        pg.goto(BASE + "/")
        pg.wait_for_url("**/login?next=/")
        check(pg.locator("h2:has-text('Đăng nhập')").is_visible(), "a visitor with no session lands on the login page")
        check(pg.evaluate("document.activeElement.id") == "login-id", "the cursor is in the ID box")
        axe("login page")
        pg.fill("#login-id", "9999")
        pg.click("#login-go")
        pg.wait_for_selector("#login-err:not([hidden])")
        check("Không vào được" in pg.inner_text("#login-err"), "a wrong ID gets the refusal sentence")
        check(pg.evaluate("document.querySelector('#login-err').getAttribute('role')") == "alert", "the refusal is announced (role alert)")
        pg.screenshot(path=os.path.join(OUT, "login.png"))
        pg.fill("#login-id", "1001")
        pg.keyboard.press("Enter")
        pg.wait_for_url(BASE + "/")
        pg.wait_for_selector("#live b:has-text('Trực tiếp')")
        check(pg.locator("#login-chip").is_visible() and "(1001)" in pg.inner_text("#login-chip"), "the header says who is signed in")
        check(pg.locator("#logout").is_visible() and pg.locator("#user-page-link").is_visible(), "Thoát and the link to the user page are there")

        # --- Tự động hoá: people
        pg.click("#tab-auto")
        pg.wait_for_selector("#ppl-body tr")
        check("Đã bật đăng nhập" in pg.inner_text("#ppl-state") and "2 admin" not in pg.inner_text("#ppl-state"), "the people box says login is on")
        rowtext = pg.inner_text("#ppl-body")
        check("1001" in rowtext and "2002" in rowtext and "3003" in rowtext and "đặt trong cài đặt" in rowtext, "the list shows the setting admin and the two people")
        check(pg.locator("#ppl-body tr", has_text="1001").locator("button").count() == 0, "the admin from the setting has no Xoá button")
        pg.fill("#ppl-text", "4004, Lan, M4, admin\nxx yy\n2002, Hải Mới, M5")
        pg.click("#ppl-save")
        pg.wait_for_selector("#ppl-bad:not([hidden])")
        check("Dòng 2" in pg.inner_text("#ppl-bad"), "a line that cannot be read is named by its number")
        pg.wait_for_function("document.querySelector('#ppl-body').innerText.includes('4004') && document.querySelector('#ppl-body').innerText.includes('Hải Mới')")
        check(True, "new and changed people appear in the table")
        pg.once("dialog", lambda d: d.accept())
        pg.locator("#ppl-body tr", has_text="3003").locator("button").click()
        pg.wait_for_function("!document.querySelector('#ppl-body').innerText.includes('3003')")
        check(pg.evaluate("document.activeElement.id") == "ppl-h", "after Xoá the keyboard goes to the box heading")
        check("đăng nhập" in pg.inner_text("#ppl-audit") and "lưu danh sách người" in pg.inner_text("#ppl-audit"), "the recent-work list shows the logins and the saved list")

        # --- the sheet box
        check(pg.locator("#sh-url").is_enabled(), "the sheet link box is open because login is on")
        pg.fill("#sh-url", "https://example.com/not-a-sheet")
        pg.click("#sh-save")
        pg.wait_for_selector(".toast.bad")
        check("Google Sheets" in pg.inner_text("#toasts"), "a link that is not a Google Sheet is refused in words")
        fake = "https://docs.google.com/spreadsheets/d/AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit?usp=drivesdk&ouid=1"
        pg.fill("#sh-url", fake)
        pg.click("#sh-save")
        pg.wait_for_function("document.querySelector('#sh-open') && !document.querySelector('#sh-open').hidden")
        check(pg.get_attribute("#sh-open", "href") == "https://docs.google.com/spreadsheets/d/AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit", "the saved link is the plain one, no markers")
        check("chưa lấy danh sách lần nào" in pg.inner_text("#sh-last"), "the page says the sheet script has not asked yet")
        admin_nocookie = Api()
        admin_nocookie.call("GET", "/api/sheet-boxes")                          # what the sheet's script does: no login
        pg.reload()
        pg.click("#tab-auto")
        pg.wait_for_function("document.querySelector('#sh-last').innerText.includes('nhận 0 hộp')")
        check(True, "after the sheet script asks, the page says when and how many boxes")
        pg.click("#sh-copy")
        pg.wait_for_selector(".toast:has-text('Đã sao chép script')")
        clip = pg.evaluate("navigator.clipboard.readText()")
        check(f"APP_URL: '{BASE}'" in clip and "onrender.com" not in clip, "the copied script carries this app's address")

        # --- the heavy-use chart in Cảnh báo kho
        pg.wait_for_selector("#a-usage .bbu")
        lede = pg.inner_text("#a-usage")
        check(f"Có {usage['over_start']} trong {usage['total']} món" in lede.replace(".", "").replace(",", "") or "món đã đạt từ 500 lượt mượn trở lên" in lede, "the chart opens with one plain sentence")
        check(pg.locator("#a-usage table").count() == 1 and pg.locator("#a-usage ol li").count() >= 5, "a band table and a list of the heaviest objects")
        check("1.587" in pg.inner_text("#a-usage"), "the heaviest object's count is printed")
        pg.screenshot(path=os.path.join(OUT, "auto-chart.png"), full_page=False)

        # --- card 41: how old the SM boxes are
        pg.wait_for_selector("#smb-ages:not([hidden])")
        check(pg.locator("#smb-age-body tr").count() == 6, "six age bands")
        opts = pg.locator("#smb-opt-body tr")
        check(opts.count() >= 5, "the what-if table has a row per choice of days")
        pg.locator("#smb-opt-body tr", has_text="14 ngày").locator("button").click()
        pg.wait_for_function("document.querySelector('#smb-opt-body tr.cur') && document.querySelector('#smb-opt-body tr.cur').innerText.includes('14 ngày')")
        st, sw = admin.call("GET", "/api/automations")
        days = next(x for x in sw["switches"] if x["key"] == "reuse_old")["config"]["days"]
        check(days == 14, "pressing the 14 days button sets the switch to 14")
        check(pg.evaluate("document.activeElement.id") == "smb-opt-h", "the keyboard is parked on the table heading afterwards")
        axe("Tự động hoá tab")

        # --- Kho đồ: the borrow column, sort and band filter
        pg.click("#tab-objects")
        pg.wait_for_selector("#o-body tr td.num")
        check("Lượt mượn" in pg.inner_text(".otable thead"), "the object table has a Lượt mượn column")
        pg.select_option("#o-sort", "uses")
        pg.wait_for_function("document.querySelector('#o-body tr .oid') && document.querySelector('#o-body tr').innerText.includes('1.587')")
        check(True, "sorted by borrows, the most borrowed object is first")
        pg.select_option("#o-band", "500-999")
        pg.wait_for_function("[...document.querySelectorAll('#o-body tr')].every(r => !r.innerText.includes('1.587')) && document.querySelectorAll('#o-body tr .oid').length > 0")
        nums = pg.evaluate("[...document.querySelectorAll('#o-body tr')].map(r => r.children[7].innerText.split('\\n')[0].replace(/\\./g,''))")
        check(nums and all(500 <= int(n) <= 999 for n in nums), f"the 500 to 999 filter shows only those ({len(nums)} rows)")
        pg.select_option("#o-band", "unknown")
        pg.wait_for_function("(() => { const rows = [...document.querySelectorAll('#o-body tr .oid')]; return rows.length > 0 && "
                             "[...document.querySelectorAll('#o-body tr')].every(r => r.children[7] && r.children[7].innerText.trim() === '-'); })()")
        dashes = pg.evaluate("[...document.querySelectorAll('#o-body tr')].map(r => r.children[7].innerText.trim())")
        check(dashes and all(d == "-" for d in dashes), "objects with no borrow data show a dash, never a 0")
        pg.select_option("#o-band", "")
        axe("Kho đồ tab")
        pg.screenshot(path=os.path.join(OUT, "objects.png"))

        # --- the form of the Hộp tab: only a box made today blocks the same name (a unit makes a new B2 every day)
        st, made = admin.call("POST", "/api/boxes", {"module": "M8", "label": "Box B2", "theme": "bếp"})
        check(st == 201, f"the admin makes a Box B2 for M8 {st}")
        dup_text = "Hôm nay đã có hộp M8 Box B2. Đặt tên khác, hoặc mở hộp đó ở bên phải."

        def try_name():
            pg.goto(BASE + "/")
            pg.wait_for_selector("#live b:has-text('Trực tiếp')")
            pg.click("#tab-boxes")
            pg.wait_for_function("document.querySelectorAll('.tray .pill').length > 0")
            pg.fill("#f-module", "M8"); pg.fill("#f-label", "Box B2")
            pg.wait_for_timeout(300)

        try_name()
        check(pg.is_visible("#f-warn") and pg.inner_text("#f-warn") == dup_text and pg.is_disabled("#f-go"), f"a box made today blocks the same name, in words {pg.inner_text('#f-warn')!r}")
        axe("Hộp tab with the warning")
        yesterday = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 7 * 3600 - 86400))
        con = sqlite3.connect(os.path.join(tmp, "admin.db"), timeout=30)
        con.execute("UPDATE boxes SET day=?, created_at=created_at-86400 WHERE id=?", (yesterday, made["id"]))
        con.commit()
        try_name()
        check(pg.is_hidden("#f-warn") and pg.is_enabled("#f-go"), "the same name of a box made yesterday does not block today's")
        con.execute("UPDATE boxes SET day='' WHERE id=?", (made["id"],))
        con.commit(); con.close()
        try_name()
        check(pg.is_hidden("#f-warn") and pg.is_enabled("#f-go"), "a box with no day never blocks a name")

        # --- signed out: the cookie is gone and the page is out of reach
        pg.click("#logout")
        pg.wait_for_url("**/login")
        pg.goto(BASE + "/")
        pg.wait_for_url("**/login?next=/")
        check(True, "after Thoát the admin page sends the visitor back to the login page")

        # --- a user is kept off the admin page
        pg.goto(BASE + "/login")
        pg.fill("#login-id", "2002")
        pg.click("#login-go")
        pg.wait_for_url(BASE + "/dung")
        pg.goto(BASE + "/")
        pg.wait_for_url(BASE + "/dung")
        check(True, "a user who opens the admin address is sent to the user page")
        st403 = pg.evaluate("fetch('/api/state', {credentials: 'same-origin'}).then(r => r.status)")
        check(st403 == 403, "and the admin API answers 403 to that user")
        b.close()
finally:
    srv.terminate()
    try:
        srv.wait(timeout=10)
    except Exception:
        srv.kill()

for e in errs:
    print("ERR  " + e)
check(not errs, "no page or console errors")
check(not bad_net, "no failed requests" + ("" if not bad_net else ": " + ", ".join(bad_net[:5])))
print(f"\n{checks} checks, {len(fails)} failed")
sys.exit(1 if fails else 0)

"""Browser test of the page for people who make boxes (/dung) and its login, on a throw-away instance (own port, own database).
Run:  pip install playwright && python -m playwright install chromium && python -I tests/dung_e2e.py
Works on Windows and Linux. Set E2E_PORT to pick the port. Never contacts anything but 127.0.0.1 (no SM, Render, Neon or Google).
Optional: AXE_JS=/path/to/axe.min.js (from the axe-core npm package) adds the accessibility runs on /login and on /dung in every state.
The server runs with ADMIN_IDS=1001 (login on), the mock picker, and the people 2002 (Hải, M2) and 3003 (Hưng, M3).
A second server has login off. A third has ADMIN_IDS=1001, USER_IDS=4001,4002 and a made-up USER_PASSWORD (kept in this process only).
The first server runs with BOX_DONE_DAYS=2: the app finishes a box 1 day after the day it was made, and the tests of the tag Hôm qua need a box of yesterday that is still alive."""
import datetime, http.cookiejar, json, os, re, shutil, socket, sqlite3, subprocess, sys, tempfile, time, traceback, urllib.error, urllib.parse, urllib.request
from playwright.sync_api import sync_playwright

for _stream in (sys.stdout, sys.stderr):
    try: _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def clean_env(**extra):
    drop = ("DATABASE_URL", "DEEPSEEK_API_KEY", "ACCESS_CODE", "LLM_MODE", "IMAGE_HOSTS", "SECTORS", "SM_PUSH_MODE", "TEST_DATABASE_URL",
            "ADMIN_IDS", "ADMIN_CODE", "USER_IDS", "USER_PASSWORD", "BOX_DONE_DAYS", "STAGE_COUNTS", "DEFAULT_COUNT", "DRAFT_TTL_HOURS", "SYNC_TOKEN", "DB_PATH", "CATALOG_PATH")
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


def start_server(env, port, tmp):
    log = open(os.path.join(tmp, "server.log"), "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    for _ in range(300):
        if proc.poll() is not None: break
        try: urllib.request.urlopen(f"http://127.0.0.1:{port}/api/config", timeout=1); return proc, log
        except Exception: time.sleep(0.2)
    log.flush()
    tail = open(os.path.join(tmp, "server.log"), encoding="utf-8", errors="replace").read()[-1500:]
    stop_server(proc, log, tmp)
    raise RuntimeError("the app did not start: " + tail)


class Client:
    """A plain HTTP client with a cookie jar: how the admin signs in without a browser. Writes carry the Origin header the app checks."""
    def __init__(self, base):
        self.base = base
        self.op = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Origin": self.base, **(headers or {})})
        try:
            with self.op.open(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try: return e.code, json.loads(raw or b"null")
            except ValueError: return e.code, raw.decode("utf-8", "replace")


results = []
fails = []
def check(cond, msg):
    results.append(bool(cond))
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond: fails.append(msg)


PORT = int(os.environ.get("E2E_PORT") or free_port())
BASE = f"http://127.0.0.1:{PORT}"
AXE = os.environ.get("AXE_JS")
tmp = tempfile.mkdtemp()
srv = srv_log = srv2 = srv2_log = srv3 = srv3_log = None
tmp2 = tempfile.mkdtemp()
tmp3 = tempfile.mkdtemp()

ALLOWED_PREFIXES = ("/dung", "/login", "/static/", "/api/my/", "/api/auth/", "/api/config", "/my/print", "/favicon.ico")
LIVE_WATCH = """() => { window.__live = []; const el = document.getElementById('live'); if (!el) return;
  new MutationObserver(() => { if (el.textContent) window.__live.push(el.textContent); }).observe(el, {childList: true, characterData: true, subtree: true}); }"""
ALLOWED_BUTTONS = {"thoát", "thử lại", "tạo hộp", "đổi món", "chọn lại cả hộp", "có, chọn lại", "xoá hộp", "có, xoá hộp", "giữ lại",
                   "sao chép id", "sao chép id và số lượng", "duyệt hộp", "có, duyệt hộp", "chưa", "bỏ duyệt", "điền ô trống",
                   "thêm unit", "xoá unit", "lưu kế hoạch", "sao chép báo cáo", "tạo b2", "tạo b3", "tạo a1", "xuống duyệt hộp"}
ALLOWED_LINKS = {"bỏ qua, đến phần chính", "trang admin", "in phiếu lấy đồ", "tạo hộp", "xem và duyệt", "lấy đồ", "xem hộp b2", "xem hộp b3", "xem hộp a1"}
# words of the admin page: a person's page must not show them (the automation switches, backup and import, the people list, the sheet box, the catalog tools)
ADMIN_ONLY = ("tự duyệt hộp", "tự động hoá", "kho đồ", "người dùng và quyền", "bảng tính google", "sao lưu và khôi phục", "nhập hộp từ tệp", "nhập các hộp này",
              "mã truy cập", "mã quản trị", "cảnh báo kho", "đồng bộ kho")
OWN_APPROVE = "Đã duyệt hộp M2 Box A1. Bạn lấy đồ ở bước 3."
SEEN_APPROVE = "Hộp M2 Box A1 đã được duyệt. Bạn lấy đồ ở bước 3."
OWN_REOPEN = "Đã bỏ duyệt hộp M2 Box A1."
ASK_APPROVE = "Duyệt hộp M2 Box A1?"
NOT_SAVED = "Chưa lưu kế hoạch hôm nay. Lưu kế hoạch rồi báo cáo hiện ở đây."
BY_APP = "app điền"
CHECK_BY_APP = "Xem kỹ các món app điền."
# the answer to the click on Có, duyệt hộp is held back for 7 seconds, so the next poll sees the change first
SLOW_APPROVE = """() => { window.__fetch = window.fetch;
  window.fetch = (u, o) => { const p = window.__fetch(u, o); return /\\/approve$/.test(String(u)) ? p.then(r => new Promise(res => setTimeout(() => res(r), 7000))) : p; }; }"""
FILLER = re.compile(r"\b(seamless(ly)?|robust|leverag(e|ing)|delve|tapestry|cutting-edge|game-?changer|empower|streamlin(e|ed)|holistic|"
                    r"comprehensive|effortless(ly)?|elevate)\b|mạnh mẽ|toàn diện|tối ưu hoá|mượt mà|vô cùng|hết sức|đa dạng và phong phú|dễ dàng nhanh chóng", re.I)

ENV = clean_env(DB_PATH=os.path.join(tmp, "dung.db"), CATALOG_PATH=os.path.join(ROOT, "data", "catalog.csv"), SYNC_TOKEN="t",
                ADMIN_IDS="1001", BOX_DONE_DAYS="2", LLM_MODE="mock")


PUMP = [None]      # the page that waits pump: the sync API only hands over browser events while it is inside a call


def pause(seconds):
    if PUMP[0] is not None: PUMP[0].wait_for_timeout(int(seconds * 1000))
    else: time.sleep(seconds)


def wait_until(fn, timeout=15, step=0.2):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn(): return True
        except Exception: pass
        pause(step)
    return False


def live_has(page, pred, timeout=6):
    """The live region fills 60 ms after the action: wait for it."""
    return wait_until(lambda: any(pred(t) for t in page.evaluate("window.__live")), timeout, 0.1)


def live_count(page, text):
    return page.evaluate("window.__live").count(text)


def axe_check(page, label):
    if not AXE or not os.path.exists(AXE):
        return
    if page.evaluate("typeof axe") == "undefined":
        page.add_script_tag(path=AXE)
    found = page.evaluate("axe.run(document, {resultTypes: ['violations']}).then(r => r.violations.map(v => v.id + ' x' + v.nodes.length + ' ' + v.nodes[0].target.join(' ')))")
    check(not found, f"axe-core finds no violations {label} {found}")


def overflow_ok(page):
    w = page.evaluate("[document.documentElement.scrollWidth, document.documentElement.clientWidth, document.body.scrollWidth]")
    return w[0] <= w[1] and w[2] <= w[1], w


def small_targets(page):
    return page.evaluate("""() => [...document.querySelectorAll('button, a[href], input:not([type=radio]), textarea, select, summary, .d-choice label')]
      .filter(e => !e.disabled && !e.classList.contains('d-skip') && e.getBoundingClientRect().width > 0)
      .map(e => { const r = e.getBoundingClientRect(); return [e.tagName, (e.textContent || e.id).trim().slice(0, 24), Math.round(r.height)]; })
      .filter(x => x[2] < 48)""")


def small_text(page):
    return page.evaluate("""() => { const out = []; const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
      while (w.nextNode()) { const n = w.currentNode; const el = n.parentElement;
        if (!n.textContent.trim() || !el || el.closest('script, style, noscript, [hidden]') || el.classList.contains('sr-only') || el.getBoundingClientRect().width === 0) continue;
        const fs = parseFloat(getComputedStyle(el).fontSize); if (fs < 16) out.push([n.textContent.trim().slice(0, 20), fs]); }
      return out; }""")


def login_look(page, label):
    """The login page: nothing under 16 px, every control at least 48 px high, no sideways scroll."""
    check(not small_text(page), f"the login page {label}: no text under 16 px {small_text(page)}")
    check(not small_targets(page), f"the login page {label}: every control is at least 48 px high {small_targets(page)}")
    ok, w = overflow_ok(page)
    check(ok, f"the login page {label}: no horizontal scroll {w}")


def admin_fold_look(page, label, is_open):
    """The fold "Tôi là admin": a caret that points right when it is closed and down when it is open (drawn, so nothing is read aloud), the words underlined, at least 48 px high."""
    got = page.evaluate("""() => { const s = document.querySelector('#login-admin summary'), cs = getComputedStyle(s), b = getComputedStyle(s, '::before'), m = new DOMMatrix(b.transform);
      return {open: s.parentElement.open, content: b.content, w: parseFloat(b.width), h: parseFloat(b.height), angle: Math.round(Math.atan2(m.b, m.a) * 180 / Math.PI),
              line: cs.textDecorationLine, height: s.getBoundingClientRect().height, text: s.textContent.trim()}; }""")
    check(got["open"] is is_open and got["content"] in ('""', "''") and got["w"] >= 9 and got["h"] >= 9,
          f"the login page {label}: Tôi là admin has a caret with an empty content (nothing to read aloud) {got}")
    check(got["angle"] == (45 if is_open else -45), f"the login page {label}: the caret points {'down' if is_open else 'right'} (turned {got['angle']} degrees)")
    check("underline" in got["line"] and got["text"] == "Tôi là admin", f"the login page {label}: the words Tôi là admin are underlined {got['line']!r} {got['text']!r}")
    check(got["height"] >= 48, f"the login page {label}: Tôi là admin is at least 48 px high ({got['height']:.0f} px)")


def controls_ok(page):
    """Only the controls of this page: Duyệt hộp and Bỏ duyệt for the person's own boxes, but no automation switches, no backup or import,
    no people list, no sheet box, no catalog tools (by the buttons, the links, the kinds of control and the words on the page)."""
    got = page.evaluate("""() => ({
      buttons: [...document.querySelectorAll('button, [role=button], input[type=submit], input[type=button]')].map(e => (e.innerText || e.textContent || e.value || '').trim().toLowerCase()),
      links: [...document.querySelectorAll('a[href]')].map(e => (e.closest('nav') ? e.querySelector('.d-step-t').textContent : (e.innerText || e.textContent)).trim().toLowerCase()),
      switches: document.querySelectorAll('[role=switch], input[type=checkbox], input[type=file], table, select:not(#units select), details:not(.d-fold)').length,
      text: document.body.innerText.toLowerCase() })""")
    bad = [b for b in got["buttons"] if b not in ALLOWED_BUTTONS] + [a for a in got["links"] if a not in ALLOWED_LINKS] + [w for w in ADMIN_ONLY if w in got["text"]]
    return not bad and got["switches"] == 0, bad, got["switches"]


def focus_key(page):
    return page.evaluate("document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.key : null")


def focus_on_head(page, box_id):
    return page.evaluate("document.activeElement.dataset.key === 'head' && !!document.activeElement.closest('article') && document.activeElement.closest('article').dataset.box === '%s'" % box_id)


def steps(page):
    """The three captions of the progress strip."""
    return page.evaluate("[...document.querySelectorAll('.d-step-li')].map(li => li.querySelector('.d-step-s').textContent)")


VN = datetime.timezone(datetime.timedelta(hours=7))
SHIFT_TEXTS = ["Cả ngày", "Nửa ca đầu", "Nửa ca sau", "Chỉ làm B1 và B2", "Nghỉ"]


def vn_at(days_ago, hour=12):
    """(epoch seconds, 'YYYY-MM-DD') of that hour in Vietnam, some days ago."""
    d = datetime.datetime.now(VN).replace(hour=hour, minute=0, second=0, microsecond=0) - datetime.timedelta(days=days_ago)
    return d.timestamp(), d.strftime("%Y-%m-%d")


def db_get(sql, args=()):
    con = sqlite3.connect(os.path.join(tmp, "dung.db"), timeout=30)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def db_run(sql, args=()):
    """Writes straight into the test database (the server runs with its own DB_PATH in a temp directory)."""
    con = sqlite3.connect(os.path.join(tmp, "dung.db"), timeout=30)
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()


def add_history(box_id, module, label, code, anchor, theme, owner, title, days_ago):
    created, day = vn_at(days_ago)
    db_run("INSERT INTO box_history(box_id,module,label,code,anchor,theme,count,owner,title,sm_box_id,created_at,done_at,day,items) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           (box_id, module, label, code, anchor, theme, 21, owner, title, "", created, time.time(), day, "[]"))


def add_box(module, label, code, anchor, owner, status, days_ago, hour=15):
    created, day = vn_at(days_ago, hour)
    db_run("INSERT INTO boxes(module,label,theme,categories,code,anchor,count,status,message,owner,created_at,updated_at,day) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
           (module, label, "", "[]", code, anchor, 21, status, "", owner, created, created, day))


def marks(page):
    """The word at the end of each line of the day list, in order (empty when the line has none)."""
    return page.evaluate("[...document.querySelectorAll('#timeline li')].map(li => li.querySelector('.d-tl-mark').textContent)")


def tag(page, sel):
    return page.evaluate("(s) => { const e = document.querySelector(s); return e.hidden ? '' : e.textContent.trim(); }", sel)


def open_folds(page):
    for f in ("#fold-day", "#fold-return", "#fold-plan", "#fold-report"):
        if not page.evaluate("(s) => document.querySelector(s).open", f):
            page.click(f + " > summary")


def rows_of(page):
    return page.locator("#units > li")


def row_needs(row):
    return [t.strip() for t in row.locator(".d-need-k").all_inner_texts()]


def row_states(row):
    return [t.strip() for t in row.locator(".d-need-row .d-chip").all_inner_texts()]


def clip_text(page):
    return page.evaluate("navigator.clipboard.readText()").replace("\r\n", "\n")


JARGON = re.compile(r"mã 5|mã 4|plain text|máy chủ|javascript|token|\bAPI\b|object id|\(ID ", re.I)


def jargon_hits(text):
    """Words a person with no IT knowledge must never meet on /dung or /login (error codes, the machine room, the loading of the page)."""
    return JARGON.findall(text) + re.findall(r"\bAI\b", text)


def sentence_count(text):
    return len(re.findall(r"[.?]\s+(?=[A-ZÀ-Ỹ])", text.strip())) + 1


page_texts = []


def main():
    global srv, srv_log, srv2, srv2_log, srv3, srv3_log
    # ---------------------------------------------------------------- the shipped text follows the writing rules
    for name in ("app/static/dung.html", "app/static/dung.js", "app/static/dung.css"):
        text = open(os.path.join(ROOT, name), encoding="utf-8").read()
        check("—" not in text, f"{name} has no em dash")
        check(not FILLER.search(text), f"{name} has no filler words")
    html = open(os.path.join(ROOT, "app/static/dung.html"), encoding="utf-8").read()
    body = re.sub(r"<!--.*?-->|<script.*?</script>", "", html, flags=re.S)
    check("!" not in re.sub(r"<!doctype html>", "", body, flags=re.I), "dung.html has no exclamation mark")
    js = open(os.path.join(ROOT, "app/static/dung.js"), encoding="utf-8").read()
    check(not re.search(r"""["'`][^"'`\n]*[À-ỹ][^"'`\n]*![^=][^"'`\n]*["'`]""", js), "dung.js has no exclamation mark inside a Vietnamese sentence")
    shown = re.sub(r"/\*.*?\*/|//[^\n]*", "", js, flags=re.S) + html      # the text of the page: no comments
    check(not re.search(r"admin\s+(sẽ\s+)?(duyệt|mở lại)|[Nn]hờ admin|[Cc]hờ admin|cho đến khi admin|do admin duyệt", shown),
          "dung.html and dung.js say nowhere that an admin approves")
    check(not re.search(r"mã truy cập|access.?code|mã chung|mã quản trị", shown, re.I), "dung.html and dung.js mention no access code")
    check("Chờ duyệt" in js and "Chờ bạn duyệt" not in js and "Chờ admin duyệt" not in js, "dung.js names the waiting state Chờ duyệt, nothing else")
    check("Chờ bạn duyệt" not in html and "Chờ bạn duyệt" not in open(os.path.join(ROOT, "app/static/dung.css"), encoding="utf-8").read(), "dung.html and dung.css do not use the old name")
    for want in ("Xem và duyệt", "Duyệt hộp", "Bỏ duyệt", "Điền ô trống", "Có hộp để lấy", "Hôm nay", "Hộp cần trả sáng nay", "Kế hoạch hôm nay", "Báo cáo cuối ngày",
                 "Lưu kế hoạch", "Sao chép báo cáo", "Gần đây ở", "Chọn bối cảnh khác để khỏi làm trùng.",
                 NOT_SAVED, "Có, chọn lại", "Các món hiện có sẽ bị thay.", BY_APP, CHECK_BY_APP):
        check(want in shown, f"the page has the text {want!r}")
    check(not re.search(r"máy chủ|\bAPI\b|đồng bộ|\bphiên\b|\btoken\b", shown), "dung.html and dung.js use no technical word (máy chủ, API, đồng bộ, phiên, token)")
    code_free = re.sub(r"/\*.*?\*/|//[^\n]*", "", js, flags=re.S)
    long_lines = [a or b for a, b in re.findall(r'"([^"\n]{20,})"|`([^`\n]{20,})`', code_free) if re.search(r"[À-ỹ]", a or b) and sentence_count(a or b) > 2]
    long_lines += [t for t in re.split(r"<[^>]+>", body) if t.strip() and sentence_count(t) > 2]
    check(not long_lines, f"no message of dung.js or dung.html has more than two sentences {long_lines[:3]}")
    login_src = "".join(re.sub(r"<!--.*?-->|/\*.*?\*/|//[^\n]*", "", open(os.path.join(ROOT, n), encoding="utf-8").read(), flags=re.S) for n in ("app/static/login.html", "app/static/login.js"))
    technical = re.findall(r"mã 5|mã 4|Plain text|máy chủ|JavaScript|token|\bAPI\b|Object ID|\(ID |\bmáy\b|\bAI\b|đồng bộ|mã lỗi|noscript", shown + login_src)
    check(not technical, f"dung.html, dung.js, login.html and login.js carry no technical word {technical[:4]}")
    check("(mã " not in shown and "mã ${" not in shown, "no message shows an error number")
    check("Tiếp theo" in js and "Ngoài giờ làm." in js and 'timeZone: "Asia/Ho_Chi_Minh"' in js, "the clock of Hôm nay uses the Vietnam time zone by name")

    srv, srv_log = start_server(ENV, PORT, tmp)
    admin = Client(BASE)
    code, out = admin.call("POST", "/api/auth/login", {"id": "1001"})
    check(code == 200 and out["person"]["admin"] is True and out["go"] == "/", "the admin signs in with a plain HTTP client")
    code, out = admin.call("POST", "/api/admin/people", {"text": "2002, Hải, M2\n3003, Hưng, M3"})
    check(code == 200 and out["added"] == 2, f"the admin adds two people {out}")
    code, cfg = admin.call("GET", "/api/config")
    stage = cfg["stage_counts"]

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        reqs = []
        rec = lambda r: reqs.append((r.method, r.url))
        errs = []

        def new_ctx(**kw):
            c = browser.new_context(locale="vi-VN", **kw)
            c.on("request", rec)
            c.add_init_script("""window.addEventListener('DOMContentLoaded', (""" + LIVE_WATCH + """));""")
            return c

        def watch(page, label):
            page.on("pageerror", lambda e: errs.append(f"{label} pageerror: {e}"))
            page.on("console", lambda m: errs.append(f"{label} console: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)

        ctx = new_ctx(viewport={"width": 1100, "height": 900})
        ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
        page = ctx.new_page(); watch(page, "user")
        PUMP[0] = page
        flow_log = []          # in order: ("write", path, status) when an answer to a write arrives, ("summary", url) when the list is asked for
        page.on("response", lambda r: flow_log.append(("write", urllib.parse.urlsplit(r.url).path, r.status)) if r.request.method != "GET" and "/api/my/" in r.url else None)
        page.on("request", lambda r: flow_log.append(("summary", r.url)) if "/api/my/summary" in r.url else None)

        # ---------------------------------------------------------------- login
        page.goto(BASE + "/dung")
        check(urllib.parse.urlsplit(page.url).path == "/login" and "next=/dung" in page.url, f"/dung without a session goes to /login?next=/dung ({page.url})")
        axe_check(page, "on /login")
        login_look(page, "(normal)")
        admin_fold_look(page, "(closed)", False)
        check(page.is_hidden("#login-pw-field") and page.is_hidden("#login-pw") and "Mật khẩu" not in page.inner_text("body"),
              "no user password is asked: the login page shows no field Mật khẩu")
        page.click("#login-admin summary")
        check(page.is_visible("#login-code"), "Tôi là admin opens the field of the admin code")
        login_look(page, "(admin code open)")
        admin_fold_look(page, "(open)", True)
        axe_check(page, "on /login (admin code open)")
        page.click("#login-admin summary")
        login_text = page.inner_text("body")
        check(not jargon_hits(login_text) and "Chưa có ID? Nhờ admin thêm ID của bạn." in login_text and "Đừng đưa ID cho người ngoài công ty." in login_text and "Ai biết ID" not in login_text,
              f"the login page: the field, the button, two short sentences, nothing technical {login_text!r}")
        check(not page.is_visible("#login-code") and page.locator("#login-admin summary").inner_text() == "Tôi là admin", "the admin code stays inside Tôi là admin")
        page_texts.append(login_text)
        page.fill("#login-id", "9999")
        with page.expect_response(lambda r: r.url.endswith("/api/auth/login")) as refused:
            page.press("#login-id", "Enter")
        page.wait_for_selector("#login-err:not([hidden])")
        refusal = refused.value.json().get("detail")
        check(refused.value.status >= 400 and refusal and page.inner_text("#login-err") == refusal, f"a wrong ID shows the sentence of the server {refusal!r}")
        axe_check(page, "on /login with the refusal")
        login_look(page, "(refusal)")
        check(page.evaluate("parseFloat(getComputedStyle(document.getElementById('login-err')).fontSize)") >= 16, "the sentence of the refusal is 16 px or more")
        lp = ctx.new_page(); watch(lp, "login-phone")
        lp.set_viewport_size({"width": 375, "height": 800})
        lp.goto(BASE + "/login"); lp.wait_for_selector("#login-id")
        login_look(lp, "at 375 px")
        admin_fold_look(lp, "at 375 px (closed)", False)
        lp.click("#login-admin summary")
        login_look(lp, "at 375 px (admin code open)")
        admin_fold_look(lp, "at 375 px (open)", True)
        axe_check(lp, "on /login at 375 px (admin code open)")
        lp.close()
        page_texts.append(page.inner_text("body"))
        page.fill("#login-id", "2002"); page.press("#login-id", "Enter")
        page.wait_for_url(BASE + "/dung")
        check(page.url == BASE + "/dung", "ID 2002 lands on /dung")

        # ---------------------------------------------------------------- empty state
        page.wait_for_function("document.querySelector('#s2-lead').textContent.includes('Chưa có hộp nào')")
        check(page.title() == "Làm hộp - Box builder" and page.inner_text("h1") == "Làm hộp", "the page has a title and one h1")
        check(page.locator("header").count() == 1 and page.locator("nav[aria-label]").count() == 1 and page.locator("main").count() == 1,
              "landmarks: one header, one named nav, one main")
        check(page.locator("[aria-live], [role=status], [role=alert]").count() == 1 and page.get_attribute("#live", "aria-live") == "polite",
              "one polite live region")
        check(page.input_value("#f-module") == "M2", "the form is prefilled with the module of the person (M2)")
        check(page.inner_text("#who") == "Hải" and page.is_visible("#logout") and not page.is_visible("#admin-link"),
              "who is signed in, a Thoát button, no admin link for a person")
        check(not page.is_visible("#open-note"), "no login-off sentence when login is on")
        kinds = [x.strip().replace("\n", " ") for x in page.locator(".d-choice label").all_inner_texts()]
        check(kinds == [f"Box B2 {stage['B2']} món", f"Box B3 {stage['B3']} món", f"Box A1 {stage['A1']} món"], f"the kinds of box with their object counts {kinds}")
        check(page.locator("input[name=kind]:checked").count() == 0, "no kind is chosen until the person chooses")
        check(page.get_attribute("#step-create h2", "id") == "s1-h" and "Bước 1" in page.inner_text("#s1-h") and "Bước 2" in page.inner_text("#s2-h") and "Bước 3" in page.inner_text("#s3-h"),
              "three headed steps")
        cur = page.locator(".d-steps a[aria-current=step]")
        check(cur.count() == 1 and "Tạo hộp" in cur.inner_text(), "the progress strip is on step 1 when there is no box")
        check(page.locator(".d-step-li[data-step='2'] .d-step-t").inner_text() == "Xem và duyệt" and "Xem và duyệt" in page.inner_text("#s2-h"),
              "step 2 is called Xem và duyệt in the progress strip and in its heading")
        check(steps(page) == ["Đang ở đây", "Chưa tới", "Chưa tới"], f"the captions of the progress strip with no box {steps(page)}")
        check(page.get_attribute("#f-theme", "placeholder") == "Ví dụ: bàn bếp và bàn ăn", "the theme box has an example in its placeholder")
        check(overflow_ok(page)[0], "no horizontal scroll (empty state, desktop)")
        check(page.evaluate("getComputedStyle(document.body).fontSize") == "18px", "body text is 18 px")
        check(not small_text(page), f"no text under 16 px {small_text(page)}")
        check(not small_targets(page), f"every control is at least 48 px high {small_targets(page)}")
        ok, bad, sw = controls_ok(page)
        check(ok, f"only the controls of this page (empty state) {bad} {sw}")
        page_texts.append(page.inner_text("body"))
        axe_check(page, "on /dung (empty)")
        check(page.inner_text("#today-h") == "Hôm nay" and page.evaluate("!!(document.getElementById('today').compareDocumentPosition(document.getElementById('step-create')) & Node.DOCUMENT_POSITION_FOLLOWING)"),
              "the panel Hôm nay is above step 1")
        check(page.locator("details.d-fold").count() == 4 and page.locator("details.d-fold[open]").count() == 0, "the four sections of Hôm nay are closed at first")
        page.wait_for_function("document.querySelector('#rep-empty').textContent === 'Chưa có unit nào.'", timeout=15000)
        check(page.is_hidden("#rep-box") and page.text_content("#ret-empty") == "Không có hộp nào cần trả." and page.is_hidden("#plan-old"),
              "no plan yet: the report says Chưa có unit nào., nothing to hand back")
        check(re.fullmatch(r"Bây giờ \d\d:\d\d\. (Tiếp theo: .+ lúc \d+:\d\d\.|Ca làm hết lúc 11:30\.|Ngoài giờ làm\.)", page.inner_text("#now-line")) is not None,
              f"the line of the clock {page.inner_text('#now-line')!r}")

        # form checks before any box exists
        page.fill("#f-module", ""); page.click("#f-go")
        check(page.inner_text("#err-module") == "Nhập Module." and page.evaluate("document.activeElement.id") == "f-module"
              and page.get_attribute("#f-module", "aria-invalid") == "true", "an empty Module gets a plain sentence and the focus")
        axe_check(page, "on /dung (module error)")
        page.fill("#f-module", "m2"); page.click("#f-go")
        check(page.inner_text("#err-kind") == "Chọn loại hộp." and page.evaluate("document.activeElement.name") == "kind"
              and page.input_value("#f-module") == "M2", "no kind chosen: a plain sentence and the focus on the kinds; m2 is tidied to M2")
        axe_check(page, "on /dung (kind error)")

        # ---------------------------------------------------------------- keyboard only: make a Box A1
        page.goto(BASE + "/dung")
        page.wait_for_function("document.querySelector('#s2-lead').textContent.includes('Chưa có hộp nào')")
        page.evaluate("document.activeElement && document.activeElement.blur()")
        seen = []
        for _ in range(18):
            page.keyboard.press("Tab")
            seen.append(page.evaluate("document.activeElement.id || document.activeElement.getAttribute('href')"))
            if seen[-1] == "f-module": break
        check(seen[-1] == "f-module" and len(seen) <= 12, f"Tab reaches the Module field within a dozen stops (the four closed sections of Hôm nay come first) {seen}")
        page.keyboard.press("Control+A"); page.keyboard.type("M2")
        page.keyboard.press("Tab")
        check(page.evaluate("document.activeElement.name") == "kind", "Tab goes from Module to the kinds")
        page.keyboard.press("ArrowRight"); page.keyboard.press("ArrowRight")
        check(page.evaluate("document.querySelector('input[name=kind]:checked').value") == "Box A1", "arrow keys choose Box A1")
        page.keyboard.press("Tab"); page.keyboard.type("M2-01")
        page.keyboard.press("Tab"); page.keyboard.type("Tủ lạnh")
        page.keyboard.press("Tab"); page.keyboard.type("bếp và bàn ăn")
        page.keyboard.press("Tab")
        check(page.evaluate("document.activeElement.id") == "f-go", "Tab reaches the Tạo hộp button")
        page.keyboard.press("Enter")
        page.wait_for_function("document.activeElement && document.activeElement.dataset.key === 'head' && document.activeElement.closest('article')", timeout=15000)
        check(True, "after Tạo hộp the keyboard focus moves to the new box in step 2")
        page.wait_for_selector("article.d-card.is-draft", timeout=30000)
        check(page.inner_text("article.d-card .d-chip") == "Chờ duyệt", "the box becomes Chờ duyệt")
        n_items = page.locator("article.d-card .d-item").count()
        check(n_items == stage["A1"], f"the box has {stage['A1']} objects (got {n_items})")
        check(page.evaluate("document.activeElement.dataset.key") == "head", "the focus stays on the box heading when it fills in")
        lives = page.evaluate("window.__live")
        check(any("Đã tạo hộp M2 Box A1. Đang chọn đồ." == t for t in lives), f"the live region says the box was made {lives}")
        card = page.locator("article.d-card")
        check(card.locator("h3").inner_text() == "M2 Box A1", "the card heading is the module and the kind")
        meta = card.locator(".d-meta").inner_text()
        check("Mã section M2-01" in meta and "Đồ lớn Tủ lạnh" in meta and "Bối cảnh bếp và bàn ăn" in meta and f"{stage['A1']} món" in meta, f"the card shows code, large object, theme, count {meta!r}")
        first = card.locator(".d-item").first
        check(first.locator(".d-name").inner_text().strip() != "" and re.fullmatch(r"ID \d{6}", first.locator(".d-facts span").nth(0).inner_text())
              and first.locator(".d-facts span").nth(1).inner_text().startswith("Số lượng ")
              and re.match(r"(Vị trí kệ \S+|Chưa rõ vị trí kệ)", first.locator(".d-facts span").nth(2).inner_text()), "each object shows name, ID, quantity and shelf slot")
        exp = card.locator(".d-expiry").inner_text()
        m = re.fullmatch(r"Hộp này tự mất sau khoảng (\d+) giờ nếu bạn chưa duyệt\.", exp)
        check(m and int(m.group(1)) in (11, 12) and "is-warn" not in (card.locator(".d-expiry").get_attribute("class") or ""), f"a draft says how long it has left (ttl is 12 hours): {exp!r}")
        cur = page.locator(".d-steps a[aria-current=step]")
        check("Xem và duyệt" in cur.inner_text(), "the progress strip moves to step 2 for a draft")
        check(steps(page) == ["Xong", "Đang ở đây", "Có hộp để lấy"], f"a draft with objects: step 3 says Có hộp để lấy, not Chưa tới {steps(page)}")
        check(page.is_hidden("#s2-lead"), "step 2 has no sentence of help once there is a box: the buttons say what they do")
        take = page.locator(".d-take")
        check(take.count() == 1 and "Danh sách còn đổi được đến khi bạn duyệt hộp." in take.inner_text(), "step 3 lists the draft and says the list can still change until the person approves")
        ap = page.locator("article.d-card [data-key=approve]")
        check(ap.count() == 1 and ap.inner_text() == "Duyệt hộp" and ap.is_enabled() and ap.bounding_box()["height"] >= 48 and "d-btn-primary" in ap.get_attribute("class"),
              "a draft with all its objects shows a primary button Duyệt hộp, enabled, at least 48 px high")
        check(not [b for b in page.locator("button, a").all_inner_texts() if b.strip() in ("Duyệt", "Admin duyệt")], "there is no button that only says Duyệt")
        check(page.locator("article.d-card [data-key=reopen]").count() == 0 and page.locator("article.d-card [data-key=fill]").count() == 0, "a full draft has no Bỏ duyệt and no Điền ô trống")
        ok, bad, sw = controls_ok(page)
        check(ok, f"only the controls of this page (draft) {bad} {sw}")
        check(not small_targets(page), f"every control is at least 48 px high (draft) {small_targets(page)}")
        check(not small_text(page), f"no text under 16 px (draft) {small_text(page)}")
        check(overflow_ok(page)[0], "no horizontal scroll (draft, desktop)")
        page_texts.append(page.inner_text("body"))
        check(not jargon_hits(page.inner_text("body")) and not re.search(r"\(ID |\bID \d{4}\b", page.inner_text("#who")), f"/dung with a box on it: nothing technical in the text {jargon_hits(page.inner_text('body'))}")
        axe_check(page, "on /dung (draft box, step 3 says Có hộp để lấy)")

        # ---------------------------------------------------------------- replace one object, by keyboard
        old_first = first.locator(".d-facts span").nth(0).inner_text()
        for _ in range(4):
            page.keyboard.press("Tab")
            if page.evaluate("document.activeElement.dataset.key") == "swap-1": break
        check(page.evaluate("document.activeElement.dataset.key") == "swap-1", "Tab goes from the box heading to the first Đổi món")
        page.keyboard.press("Enter")
        changed = wait_until(lambda: page.evaluate("""(old) => { const e = document.querySelector('article.d-card.is-draft .d-item:first-child .d-facts span');
                                                    return !!e && e.textContent !== old; }""", old_first), 30)
        check(changed, "Đổi món gives another object in the same place")
        check(page.locator("article.d-card .d-item").count() == stage["A1"], "the box still has all its objects after the swap")
        check(wait_until(lambda: page.evaluate("document.activeElement.dataset.key") == "swap-1", 8), "keyboard focus comes back to the same Đổi món")
        check(live_has(page, lambda t: "Đang đổi món" in t), "the live region says an object is being changed")

        # ---------------------------------------------------------------- copy and print
        code, st = admin.call("GET", "/api/state")
        mine = [b for b in st["boxes"] if b["module"] == "M2" and b["label"].endswith("Box A1")][0]
        by_pos = {i["pos"]: i for i in mine["items"]}
        want_ids = "\n".join(by_pos[p]["id"] if p in by_pos else "" for p in range(1, mine["count"] + 1))
        want_both = "\n".join(f"{by_pos[p]['id']}\t{by_pos[p]['qty']}" if p in by_pos else "" for p in range(1, mine["count"] + 1))
        page.click(".d-take [data-key=copy-ids]")
        page.wait_for_function("window.__live.some(t => t.startsWith('Đã chép'))")
        got = page.evaluate("navigator.clipboard.readText()").replace("\r\n", "\n")      # Windows keeps line breaks as CR LF on the clipboard
        check(got == want_ids and len(got.split("\n")) == mine["count"], f"Sao chép ID puts one ID per line, in order, on the clipboard (same text as the admin page) {got!r} {want_ids!r}")
        check(any(t == f"Đã chép {stage['A1']} món của hộp M2 Box A1. Dán vào bảng tính." for t in page.evaluate("window.__live")), "copying says what was copied, in the words of the team")
        check(page.inner_text("#live").startswith("Đã chép"), "the message is also visible")
        page.click(".d-take [data-key=copy-both]")
        page.wait_for_function("window.__live.filter(t => t.startsWith('Đã chép')).length >= 2")
        got = page.evaluate("navigator.clipboard.readText()").replace("\r\n", "\n")
        check(got == want_both and all(re.fullmatch(r"\d{6}\t\d+", ln) for ln in got.split("\n")), "Sao chép ID và số lượng puts ID, a tab and the quantity on each line")
        with ctx.expect_page() as pop:
            page.click(".d-take a[data-key=print]")
        paper = pop.value
        paper.wait_for_load_state()
        check(urllib.parse.urlsplit(paper.url).path == "/my/print" and f"ids={mine['id']}" in paper.url, f"In phiếu lấy đồ opens /my/print for this box in a new tab ({paper.url})")
        check(by_pos[1]["id"] in paper.content(), "the pick list contains the first object ID of the box")
        paper.close()

        # ---------------------------------------------------------------- Xuống Duyệt hộp is for a phone: on a wide screen it is there but takes no part (no tab stop, nothing for a screen reader)
        wide = ctx.new_page(); watch(wide, "wide")
        wide.goto(BASE + "/dung"); wide.wait_for_selector("article.d-card.is-draft")
        wj = wide.locator("article.d-card .d-jump")
        check(wj.count() == 1 and wj.is_hidden() and wide.evaluate("getComputedStyle(document.querySelector('.d-jump')).display") == "none",
              "wide screen: the link Xuống Duyệt hộp is hidden with display none, not only made see-through")
        check(wide.get_by_role("button", name="Xuống Duyệt hộp").count() == 0, "wide screen: the link is not in the accessibility tree")
        wide.locator("article.d-card [data-key=head]").focus()
        stops = []
        for _ in range(stage["A1"] + 3):
            wide.keyboard.press("Tab")
            stops.append(focus_key(wide))
            if stops[-1] == "approve": break
        check(stops[0] == "swap-1" and stops[-1] == "approve" and "jump" not in stops, f"wide screen: Tab goes from the heading to the first Đổi món and on to Duyệt hộp, never to the link {stops}")
        wide.close()

        # ---------------------------------------------------------------- 375 px
        phone = ctx.new_page(); watch(phone, "phone")
        phone.set_viewport_size({"width": 375, "height": 800})
        phone.goto(BASE + "/dung")
        phone.wait_for_selector("article.d-card.is-draft")
        ok, w = overflow_ok(phone)
        check(ok, f"no horizontal scroll at 375 px with a draft box {w}")
        check(not small_targets(phone), f"every control is at least 48 px high at 375 px {small_targets(phone)}")
        check(not small_text(phone), f"no text under 16 px at 375 px {small_text(phone)}")
        axe_check(phone, "on /dung at 375 px (draft box)")
        pj = phone.locator("article.d-card .d-jump")
        check(pj.count() == 1 and pj.is_visible() and pj.inner_text() == "Xuống Duyệt hộp" and pj.bounding_box()["height"] >= 48 and pj.evaluate("e => getComputedStyle(e).textDecorationLine") == "underline"
              and phone.get_by_role("button", name="Xuống Duyệt hộp").count() == 1,
              f"375 px: a draft with {stage['A1']} món has the underlined link Xuống Duyệt hộp, at least 48 px high, in the accessibility tree")
        in_view = "(() => { const r = document.activeElement.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; })()"
        phone.locator("article.d-card [data-key=head]").focus(); phone.keyboard.press("Tab")
        check(focus_key(phone) == "jump", f"375 px: Tab from the heading reaches Xuống Duyệt hộp first {focus_key(phone)}")
        phone.keyboard.press("Enter")
        check(wait_until(lambda: focus_key(phone) == "approve" and phone.evaluate(in_view), 3) and phone.evaluate("scrollY") > 400,
              "375 px: Enter on Xuống Duyệt hộp puts the focus on Duyệt hộp and scrolls it into view")
        phone.evaluate("window.scrollTo(0, 0)")
        pj.click()
        check(wait_until(lambda: focus_key(phone) == "approve" and phone.evaluate(in_view), 3), "375 px: a tap on Xuống Duyệt hộp does the same")
        check(overflow_ok(phone)[0] and not small_targets(phone) and not small_text(phone), "375 px: with the link, no sideways scroll, large controls, text 16 px or more")
        phone.locator("article.d-card [data-key=approve]").click()
        ok, w = overflow_ok(phone)
        check(ok and phone.locator(".d-confirm").count() == 1, f"no horizontal scroll at 375 px with the approve question open {w}")
        check(not small_targets(phone) and not small_text(phone), f"the approve question at 375 px: large controls, text 16 px or more {small_targets(phone)}")
        axe_check(phone, "on /dung at 375 px (asking to approve)")
        phone.keyboard.press("Escape")
        check(phone.locator(".d-confirm").count() == 0 and phone.evaluate("document.activeElement.dataset.key") == "approve",
              "Escape closes the approve question and gives the focus back to Duyệt hộp")
        phone.locator("article.d-card [data-key=regen]").click()
        ok, w = overflow_ok(phone)
        check(ok and phone.locator(".d-confirm").count() == 1 and not small_targets(phone) and not small_text(phone),
              f"no horizontal scroll at 375 px with the question on Chọn lại cả hộp open, large controls {w} {small_targets(phone)}")
        axe_check(phone, "on /dung at 375 px (asking to pick the whole box again)")
        phone.close()

        # ---------------------------------------------------------------- a second box by mouse, then Xoá hộp with one question
        page.reload()
        page.wait_for_selector("article.d-card.is-draft")
        check(page.input_value("#f-module") == "M2" and page.locator("input[name=kind]:checked").count() == 0, "after a reload the module is prefilled and no kind is chosen")
        page.click("label:has-text('Box B3')")
        page.click("#f-go")
        page.wait_for_selector("article.d-card:has-text('Box B3').is-draft", timeout=30000)
        check(page.locator("article.d-card").count() == 2 and page.locator("article.d-card:has-text('Box B3') .d-item").count() == stage["B3"], f"a second box (Box B3) with {stage['B3']} objects")
        check(page.locator("article.d-card").first.locator("h3").inner_text() == "M2 Box B3", "the newest box is on top")
        # Chọn lại cả hộp asks first when the box has món: Escape and Giữ lại keep them, Có, chọn lại picks the whole box again
        sent = lambda tail: [e for e in flow_log if e[0] == "write" and e[1].endswith(tail)]
        b3 = page.locator("article.d-card:has-text('Box B3')")
        b3_ids = lambda: [t.strip() for t in b3.locator(".d-item .d-facts span:first-child").all_inner_texts()]
        kept_ids = b3_ids()
        b3.locator("[data-key=regen]").click()
        q = b3.locator(".d-confirm")
        check(q.count() == 1 and q.locator("p").inner_text() == "Chọn lại cả hộp M2 Box B3? Các món hiện có sẽ bị thay."
              and q.locator("button").all_inner_texts() == ["Có, chọn lại", "Giữ lại"] and q.get_attribute("role") == "group" and q.get_attribute("aria-labelledby"),
              f"Chọn lại cả hộp asks once, with the name of the box and what changes {q.inner_text()!r}")
        check(focus_key(page) == "regen-no" and b3.locator("[data-key=regen]").count() == 0 and b3.locator("[data-key=del]").count() == 0 and not sent("/regenerate"),
              "the focus goes to the safe answer, Giữ lại; the question takes the place of the buttons; nothing is sent")
        check(not small_targets(page) and overflow_ok(page)[0], f"the question on Chọn lại cả hộp: large controls, no sideways scroll {small_targets(page)}")
        axe_check(page, "on /dung (asking to pick the whole box again)")
        page.keyboard.press("Escape")
        check(b3.locator(".d-confirm").count() == 0 and focus_key(page) == "regen" and b3_ids() == kept_ids and not sent("/regenerate"),
              "Escape closes the question, keeps every món, sends nothing and returns the focus to Chọn lại cả hộp")
        b3.locator("[data-key=regen]").click()
        b3.locator("[data-key=regen-no]").click()
        check(b3.locator(".d-confirm").count() == 0 and focus_key(page) == "regen" and b3_ids() == kept_ids and not sent("/regenerate"),
              "Giữ lại closes the question, keeps every món, sends nothing and returns the focus to Chọn lại cả hộp")
        b3.locator("[data-key=del]").click()
        page.keyboard.press("Escape")
        check(b3.locator(".d-confirm").count() == 0 and focus_key(page) == "del" and not sent("/boxes/%s" % b3.get_attribute("data-box")),
              "Escape also closes the question on Xoá hộp")
        b3.locator("[data-key=regen]").click()
        b3.locator("[data-key=regen-yes]").click()
        check(wait_until(lambda: len(sent("/regenerate")) == 1 and sent("/regenerate")[0][2] == 200, 10), f"Có, chọn lại sends one request and the server accepts it {sent('/regenerate')}")
        check(live_has(page, lambda t: t == "Đang chọn lại cả hộp M2 Box B3."), "Có, chọn lại: the live region says the box is being picked again")
        check(wait_until(lambda: [b["status"] for b in admin.call("GET", "/api/state")[1]["boxes"] if b["label"].endswith("Box B3")] == ["draft"]
                         and len([b for b in admin.call("GET", "/api/state")[1]["boxes"] if b["label"].endswith("Box B3")][0]["items"]) == stage["B3"], 30),
              "after Có, chọn lại the box is a full draft again on the server")
        check(wait_until(lambda: b3.locator("[data-key=regen]").is_enabled() and b3.locator(".d-item").count() == stage["B3"] and b3.locator(".d-confirm").count() == 0, 15)
              and wait_until(lambda: focus_key(page) == "regen", 10), f"the box shows all its món again and the focus is back on Chọn lại cả hộp {focus_key(page)}")
        b3.locator("[data-key=del]").click()
        check(b3.locator(".d-confirm").inner_text().startswith("Xoá hộp M2 Box B3? Các món trong hộp sẽ trả về kho."), "Xoá hộp asks once, in plain words")
        check(page.evaluate("document.activeElement.dataset.key") == "del-no", "the focus goes to the safe answer, Giữ lại")
        axe_check(page, "on /dung (asking to delete)")
        page.keyboard.press("Enter")
        check(page.locator(".d-confirm").count() == 0 and page.evaluate("document.activeElement.dataset.key") == "del", "Giữ lại closes the question and returns the focus to Xoá hộp")
        page.locator("article.d-card:has-text('Box B3') [data-key=del]").click()
        page.locator("article.d-card:has-text('Box B3') [data-key=del-yes]").click()
        page.wait_for_function("document.querySelectorAll('article.d-card').length === 1")
        check(page.locator("article.d-card h3").inner_text() == "M2 Box A1" and page.evaluate("document.activeElement.closest('article') && document.activeElement.closest('article').dataset.box") == str(mine["id"])
              and page.evaluate("document.activeElement.dataset.key") == "head", "after deleting, the focus is on the next box heading")
        check(live_has(page, lambda t: t == "Đã xoá hộp M2 Box B3. Các món đã trả về kho."), "the live region says the box was deleted")
        code, st = admin.call("GET", "/api/state")
        check(not [b for b in st["boxes"] if b["label"].endswith("Box B3")], "the deleted box is gone on the server")
        # a box with the same module and kind is refused with the server's sentence
        page.fill("#f-code", "M2-01"); page.check("input[value='Box A1']"); page.click("#f-go")
        page.wait_for_selector("#err-form:not([hidden])")
        check("đã có" in page.inner_text("#err-form") and "Mã section riêng" in page.inner_text("#err-form"), f"a repeated module, section and kind shows a sentence a person can act on: {page.inner_text('#err-form')!r}")
        check(page.locator("input[name=kind]:checked").count() == 1, "the choice stays after a refusal")

        # ---------------------------------------------------------------- the person approves their own box: the question, Chưa, the keyboard, Có, and the undo
        card = page.locator("article.d-card")
        ap = card.locator("[data-key=approve]")
        ap.click()
        q = card.locator(".d-confirm")
        check(q.count() == 1 and q.locator("p").inner_text() == ASK_APPROVE, f"Duyệt hộp asks once, in plain words {q.inner_text()!r}")
        check(q.locator("button").all_inner_texts() == ["Có, duyệt hộp", "Chưa"] and q.get_attribute("role") == "group" and q.get_attribute("aria-labelledby"),
              "the question has the buttons Có, duyệt hộp and Chưa, and is a named group")
        check(focus_key(page) == "approve-no", "the focus goes to the safe answer, Chưa")
        check(card.locator("[data-key=approve]").count() == 0 and card.locator("[data-key=regen]").count() == 0, "while the question is open it takes the place of the buttons")
        check(not small_targets(page) and overflow_ok(page)[0], f"the approve question: large controls, no sideways scroll {small_targets(page)}")
        axe_check(page, "on /dung (asking to approve)")
        card.locator("[data-key=approve-no]").click()
        check(card.locator(".d-confirm").count() == 0 and focus_key(page) == "approve" and card.get_attribute("class").endswith("is-draft"),
              "Chưa closes the question, keeps the box a draft and returns the focus to Duyệt hộp")
        check(not sent("/approve"), "Chưa sends nothing to the server")
        page.keyboard.press("Enter")
        check(focus_key(page) == "approve-no" and card.locator(".d-confirm").count() == 1, "Enter on Duyệt hộp opens the question and puts the focus on Chưa")
        page.keyboard.press("Shift+Tab")
        check(focus_key(page) == "approve-yes", "Shift+Tab reaches Có, duyệt hộp")
        page.keyboard.press("Tab"); page.keyboard.press("Enter")
        check(card.locator(".d-confirm").count() == 0 and focus_key(page) == "approve" and not sent("/approve"),
              "with the keyboard, Enter on Chưa closes the question and returns the focus to Duyệt hộp, nothing is sent")
        page.keyboard.press("Enter")
        page.keyboard.press("Escape")
        check(card.locator(".d-confirm").count() == 0 and focus_key(page) == "approve" and not sent("/approve"),
              "Escape closes the approve question, returns the focus to Duyệt hộp and sends nothing")

        asked = len([t for t in page.evaluate("window.__live") if t == OWN_APPROVE])
        ap.click()
        card.locator("[data-key=approve-yes]").click()
        page.wait_for_selector("article.d-card.is-approved", timeout=15000)
        check(len(sent("/approve")) == 1 and sent("/approve")[0][2] == 200, f"Có, duyệt hộp sends one approval {sent('/approve')}")
        check(card.locator(".d-chip").inner_text() == "Đã duyệt", "after the person approves, the chip says Đã duyệt")
        check(live_has(page, lambda t: t == OWN_APPROVE), f"the live region says the box was approved and where to go next {page.evaluate('window.__live')[-3:]}")
        check(focus_on_head(page, mine["id"]), "after approving, the keyboard focus is on the heading of that box")
        check("Lấy đồ" in page.locator(".d-steps a[aria-current=step]").inner_text() and steps(page) == ["Xong", "Xong", "Đang ở đây"],
              f"after approving, the progress strip is on step 3 {steps(page)}")
        check(card.locator("[data-key=approve]").count() == 0 and card.locator("[data-key=reopen]").is_enabled()
              and card.locator("[data-key=reopen]").inner_text() == "Bỏ duyệt" and card.locator("[data-key=reopen]").bounding_box()["height"] >= 48,
              "an approved box that is not in SM shows Bỏ duyệt (at least 48 px) and no Duyệt hộp")
        check("Hộp đã duyệt. Bấm Bỏ duyệt nếu cần sửa." in card.inner_text() and "Bỏ duyệt để sửa lại hộp." not in card.inner_text(),
              "one short line says why the edit buttons are off and what to press, no second line says the same")
        check(card.locator(".d-jump").count() == 0, "an approved box has no link Xuống Duyệt hộp")
        code, st = admin.call("GET", "/api/state")
        check([b["status"] for b in st["boxes"] if b["id"] == mine["id"]] == ["approved"], "the server holds the box as approved")
        pause(7.5)           # one more poll: it sees what the page already knows and must stay silent
        lives = page.evaluate("window.__live")
        check(lives.count(OWN_APPROVE) == asked + 1 and not any("đã được duyệt" in t for t in lives), f"the approval is said once, also after the next poll {lives[-4:]}")
        axe_check(page, "on /dung (approved by the person, with Bỏ duyệt)")

        taken = len([t for t in lives if t == OWN_REOPEN])
        card.locator("[data-key=reopen]").click()          # the undo asks nothing
        page.wait_for_selector("article.d-card.is-draft", timeout=15000)
        check(len(sent("/reopen")) == 1 and sent("/reopen")[0][2] == 200, f"Bỏ duyệt sends one request and asks no question {sent('/reopen')}")
        check(card.locator(".d-chip").inner_text() == "Chờ duyệt" and card.locator(".d-confirm").count() == 0, "after Bỏ duyệt the box is a draft again")
        check(live_has(page, lambda t: t == OWN_REOPEN) and page.evaluate("window.__live").count(OWN_REOPEN) == taken + 1, f"the live region says the approval was taken back, once {taken} {page.evaluate('window.__live')[-5:]}")
        check(focus_on_head(page, mine["id"]), "after Bỏ duyệt the focus is on the heading of that box")
        check(card.locator("[data-key^=swap]").count() == stage["A1"] and all(b.is_enabled() for b in card.locator("[data-key^=swap]").all())
              and card.locator("[data-key=regen]").is_enabled() and card.locator("[data-key=del]").is_enabled() and card.locator("[data-key=approve]").is_enabled()
              and card.locator("[data-key=reopen]").count() == 0, "after Bỏ duyệt every edit button and Duyệt hộp work again, Bỏ duyệt is gone")
        check("Hộp đã duyệt" not in card.inner_text() and card.locator(".d-expiry").count() == 1, "the draft sentences are back (time left), the approved ones are gone")
        check(steps(page) == ["Xong", "Đang ở đây", "Có hộp để lấy"], f"the progress strip goes back to step 2 {steps(page)}")
        code, st = admin.call("GET", "/api/state")
        check([b["status"] for b in st["boxes"] if b["id"] == mine["id"]] == ["draft"], "the server holds the box as a draft again")
        kept = [b for b in st["boxes"] if b["id"] == mine["id"]][0]["items"]
        check(len(kept) == stage["A1"] and {i["pos"]: i["id"] for i in kept} == {p: i["id"] for p, i in by_pos.items()},
              "taking the approval back keeps all the objects of the box, the same ones")

        after_write = []
        for i, entry in enumerate(flow_log):
            if entry[0] == "write" and entry[2] < 300:
                nxt = next((e for e in flow_log[i + 1:] if e[0] == "summary"), None)
                if nxt: after_write.append((entry[1], nxt[1]))
        check(len(after_write) >= 4 and all("rev=-1" in u for _, u in after_write), f"after each of its own writes the page asks for the whole list (rev=-1) {after_write}")

        # ---------------------------------------------------------------- polling: paused when hidden, unchanged answers, rhythm
        times, bodies = [], []
        page.on("request", lambda r: times.append((time.time(), r.url)) if "/api/my/summary" in r.url else None)
        page.on("response", lambda r: bodies.append(r) if "/api/my/summary" in r.url else None)
        page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}); document.dispatchEvent(new Event('visibilitychange'))")
        n0 = len(times)
        pause(7.5)
        check(len(times) == n0, "no request while the tab is hidden")
        page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange'))")
        check(wait_until(lambda: len(times) > n0, 3), "a request is made as soon as the tab is shown again")
        t_first = times[n0][0]
        check(wait_until(lambda: len(times) > n0 + 1, 10), "the page keeps asking while it is open")
        gap = times[n0 + 1][0] - t_first
        check(5.3 <= gap <= 8.5, f"with nothing generating it asks about every 6 seconds (gap {gap:.1f} s)")
        check(re.search(r"rev=\d+", times[n0][1]) is not None, f"the first request after showing the tab carries a rev {times[n0][1]}")
        check(re.search(r"rev=\d+", times[n0 + 1][1]) is not None and "rev=-1" not in times[n0 + 1][1], f"the next request carries the rev of the last answer {times[n0 + 1][1]}")
        pause(0.5)
        unchanged = [b.json() for b in bodies if b.ok]
        check(any(b.get("unchanged") is True and set(b) == {"rev", "unchanged"} for b in unchanged), f"an answer with nothing new is only rev and unchanged {unchanged[:1]}")
        check(page.locator("article.d-card").count() == 1 and page.locator("article.d-card .d-item").count() == stage["A1"], "the box stays on the page after an unchanged answer")

        # ---------------------------------------------------------------- the clock moves while the page is open: the time left is worked out again from answers that say "unchanged"
        page.evaluate("window.__real = Date.now; Date.now = () => window.__real() + 9 * 3600 * 1000")
        ok = wait_until(lambda: re.fullmatch(r"Hộp này tự mất sau khoảng [23] giờ nếu bạn chưa duyệt\.", page.inner_text("article.d-card .d-expiry")), 12)
        check(ok, f"9 hours later the sentence is worked out again with no reload: {page.inner_text('article.d-card .d-expiry')!r}")
        page.evaluate("Date.now = () => window.__real() + 10.5 * 3600 * 1000")
        ok = wait_until(lambda: page.locator("article.d-card .d-expiry.is-warn").count() == 1 and "khoảng 1 giờ" in page.inner_text("article.d-card .d-expiry"), 12)
        check(ok, f"under 2 hours left the sentence switches to the warning style with no reload: {page.inner_text('article.d-card .d-expiry')!r}")
        page.evaluate("Date.now = window.__real")

        # ---------------------------------------------------------------- states the real server only shows for a moment: the answer is rewritten on its way
        force = {"mode": None, "left": 0}

        def summary_route(route):
            if not force["mode"]:
                route.continue_(); return
            resp = route.fetch(url=re.sub(r"rev=-?\d+", "rev=-1", route.request.url))
            data = resp.json()
            for b in data.get("boxes", []):
                if force["mode"] == "generating":
                    b.update(status="generating", status_text="Đang chọn đồ", message="", items=[])
                elif force["mode"] == "error":
                    b.update(status="error", status_text="Chưa chọn được đồ", message="Còn 3 ô chưa điền được.", items=b["items"][:-3])
                elif force["mode"] == "errorempty":                                                            # failed with no món at all: nothing to lose
                    b.update(status="error", status_text="Chưa chọn được đồ", message="Chưa chọn được món nào.", items=[])
                elif force["mode"] == "age" and b["status"] == "draft":
                    b["updated_at"] = data["now"] - (data["ttl_hours"] - force["left"]) * 3600
                elif force["mode"] == "pad" and b["status"] == "draft" and len(b["items"]) < b["count"]:      # the page believes the box is full, the server knows it is not
                    used = {i["pos"] for i in b["items"]}
                    b["items"] += [{**b["items"][0], "pos": p} for p in range(1, b["count"] + 1) if p not in used]
                elif force["mode"] == "unpushed":                                                              # the page has not heard that the box went to SM
                    b["pushed"] = False
            route.fulfill(status=resp.status, content_type="application/json", body=json.dumps(data))
        ctx.route(re.compile(r"/api/my/summary"), summary_route)

        force["mode"] = "generating"
        page.reload()
        page.wait_for_selector("article.d-card.is-generating")
        card = page.locator("article.d-card")
        check(card.locator(".d-chip").inner_text() == "Đang chọn đồ" and card.locator(".d-working").count() == 0 and card.locator(".d-why").count() == 1, "creating state: the chip says it once, no second sentence")
        check(card.locator("[data-key=regen]").is_disabled() and card.locator("[data-key=del]").is_disabled(), "creating state: the box buttons are disabled")
        check("bạn chưa sửa được hộp này" not in card.inner_text(), "creating state: no second line repeats why the box cannot be changed")
        ap = card.locator("[data-key=approve]")
        why_id = (ap.get_attribute("aria-describedby") or "").split()[-1]
        check(ap.count() == 1 and ap.is_disabled() and ap.inner_text() == "Duyệt hộp" and page.locator("#" + why_id).inner_text() == "Chưa chọn xong đồ nên chưa duyệt được."
              and "Chưa chọn xong đồ nên chưa duyệt được." in card.inner_text(), "creating state: Duyệt hộp is turned off and one short sentence says why")
        check(page.locator(".d-take").count() == 0 and "Chưa có hộp nào chọn xong." in page.inner_text("#s3-lead"), "creating state: step 3 waits")
        check("Xem và duyệt" in page.locator(".d-steps a[aria-current=step]").inner_text() and steps(page) == ["Xong", "Đang ở đây", "Chưa tới"],
              f"creating state: the progress strip is on step 2 and step 3 says Chưa tới {steps(page)}")
        check(page.evaluate("getComputedStyle(document.querySelector('.d-chip.is-generating'), '::before').animationName") == "d-pulse", "creating state: a slow pulse in the chip (motion allowed)")
        check(small_targets(page) == [] and overflow_ok(page)[0], "creating state: controls are large and the page does not scroll sideways")
        page_texts.append(page.inner_text("body"))
        axe_check(page, "on /dung (creating, Duyệt hộp turned off with its reason)")
        t0 = len(times)
        wait_until(lambda: len(times) >= t0 + 4, 12)
        gaps = [times[i + 1][0] - times[i][0] for i in range(t0, len(times) - 1)]
        check(len(gaps) >= 3 and all(1.0 <= g <= 2.6 for g in gaps), f"while a box is being picked it asks about every 1.5 seconds {['%.1f' % g for g in gaps]}")

        force["mode"] = "error"
        page.reload()
        page.wait_for_selector("article.d-card.is-error")
        card = page.locator("article.d-card")
        check(card.locator(".d-chip").inner_text() == "Chưa chọn được đồ" and "Còn 3 ô chưa điền được." in card.inner_text(), "error state: the chip and the message of the server")
        check(card.locator(".d-errblock .d-why").inner_text() == "Bấm Điền ô trống hoặc Chọn lại cả hộp.",
              "error state: the advice starts with Điền ô trống")
        check(card.locator("[data-key=regen]").is_enabled() and card.locator("[data-key=del]").is_enabled() and card.locator("[data-key=fill]").is_enabled()
              and card.locator("[data-key=fill]").inner_text() == "Điền ô trống" and card.locator("[data-key=fill]").bounding_box()["height"] >= 48,
              "error state: a box that still has objects gets Điền ô trống, and both box buttons work")
        check(card.locator("[data-key=approve]").count() == 0 and card.locator("[data-key=reopen]").count() == 0, "error state: no Duyệt hộp and no Bỏ duyệt on a box that failed")
        check(card.locator("[data-key^=swap]").count() == 0 and page.locator(".d-take").count() == 0, "error state: no Đổi món on a half-filled box, nothing to take")
        check(card.locator(".d-item").count() > 6 and card.locator(".d-jump").count() == 0, "error state: a box that failed has no link Xuống Duyệt hộp, even with more than 6 món")
        check(steps(page) == ["Xong", "Đang ở đây", "Chưa tới"], f"error state: nothing to take yet, step 3 says Chưa tới {steps(page)}")
        check(re.fullmatch(r"Hộp này tự mất sau khoảng \d+ giờ nếu bạn chưa chọn lại\.", card.locator(".d-expiry").inner_text()) is not None, "error state: says how long the box has left")
        page_texts.append(page.inner_text("body"))
        axe_check(page, "on /dung (error, with Điền ô trống)")
        card.locator("[data-key=regen]").click()
        check(card.locator(".d-confirm p").inner_text() == "Chọn lại cả hộp M2 Box A1? Các món hiện có sẽ bị thay." and focus_key(page) == "regen-no" and card.locator("[data-key=fill]").count() == 0,
              "error state with món: Chọn lại cả hộp asks too, the focus is on Giữ lại")
        axe_check(page, "on /dung (error, asking to pick the whole box again)")
        card.locator("[data-key=regen-no]").click()
        check(card.locator(".d-confirm").count() == 0 and focus_key(page) == "regen" and card.locator("[data-key=fill]").is_enabled(),
              "error state: Giữ lại closes the question and the buttons are back")
        # Điền ô trống calls the real route; the real box is a draft, which the route also accepts
        card.locator("[data-key=fill]").click()
        check(wait_until(lambda: any(e[0] == "write" and e[1] == f"/api/my/boxes/{mine['id']}/fill" and e[2] == 200 for e in flow_log), 10), "Điền ô trống calls /fill of that box and the server accepts it")
        check(live_has(page, lambda t: t == "Đang điền các ô trống của hộp M2 Box A1."), "Điền ô trống: the live region says the empty places are being filled")
        check(wait_until(lambda: focus_key(page) == "fill", 8), f"Điền ô trống: the keyboard focus is back on the button once the box is shown again {focus_key(page)}")
        wait_until(lambda: [b["status"] for b in admin.call("GET", "/api/state")[1]["boxes"] if b["id"] == mine["id"]] == ["draft"], 15)

        # a box that failed and has no món at all has nothing to lose: Chọn lại cả hộp acts at once, with no question (the request is answered on its way, the real box is left alone)
        regen_posts = []
        def fake_regen(route):
            regen_posts.append(route.request.url)
            route.fulfill(status=200, content_type="application/json", body="{}")
        page.route(re.compile(r"/regenerate$"), fake_regen)
        force["mode"] = "errorempty"
        page.reload()
        page.wait_for_selector("article.d-card.is-error")
        card = page.locator("article.d-card")
        check(card.locator(".d-item").count() == 0 and card.locator("[data-key=fill]").count() == 0 and card.locator("[data-key=regen]").is_enabled(),
              "error state with no món: no Điền ô trống, Chọn lại cả hộp works")
        card.locator("[data-key=regen]").click()
        check(wait_until(lambda: len(regen_posts) == 1, 8) and card.locator(".d-confirm").count() == 0, "error state with no món: Chọn lại cả hộp acts at once, with no question")
        check(live_has(page, lambda t: t == "Đang chọn lại cả hộp M2 Box A1."), "error state with no món: the live region says the box is being picked again")
        page.unroute(re.compile(r"/regenerate$"))

        force["mode"] = "age"
        for left, want, warn in ((9, "Hộp này tự mất sau khoảng 9 giờ nếu bạn chưa duyệt.", False),
                                 (1.5, "Hộp này sắp tự mất: còn khoảng 1 giờ nếu bạn chưa duyệt.", True),
                                 (0.5, "Hộp này sắp tự mất: còn chưa đầy 1 giờ nếu bạn chưa duyệt.", True)):
            force["left"] = left
            page.reload()
            page.wait_for_selector("article.d-card.is-draft .d-expiry")
            e = page.locator("article.d-card .d-expiry")
            check(e.inner_text() == want and ("is-warn" in e.get_attribute("class")) is warn, f"a draft with {left} hours left says {want!r}" + (" in the warning style" if warn else ""))
            if warn:
                axe_check(page, f"on /dung (draft with {left} hours left)")
        force["mode"] = None

        # ---------------------------------------------------------------- the món the app put in (the picker found no valid one) are marked; the others look as before
        page.reload()
        page.wait_for_selector("article.d-card.is-draft")
        check(page.locator(".d-filled").count() == 0 and page.locator(".d-fillnote").count() == 0 and BY_APP not in page.locator("article.d-card").inner_text(),
              "a box whose món were all chosen by the picker has no tag and no line about the app")
        sources = db_get("SELECT pos, source FROM box_items WHERE box_id=? ORDER BY pos", (mine["id"],))
        for pos in (2, 3):
            db_run("UPDATE box_items SET source='fill' WHERE box_id=? AND pos=?", (mine["id"], pos))
        page.reload()
        page.wait_for_selector("article.d-card.is-draft .d-filled")
        card = page.locator("article.d-card")
        check(card.locator(".d-filled").count() == 2 and card.locator(".d-filled").all_inner_texts() == [BY_APP, BY_APP]
              and card.locator(".d-item[data-pos='2'] .d-filled").count() == 1 and card.locator(".d-item[data-pos='3'] .d-filled").count() == 1,
              "the two món the app put in each have the word app điền next to the name")
        check(card.locator(".d-item[data-pos='1'] .d-filled").count() == 0 and card.locator(".d-item[data-pos='4'] .d-filled").count() == 0
              and BY_APP not in card.locator(".d-item[data-pos='1']").inner_text(), "the other món have no tag")
        name2 = card.locator(".d-item[data-pos='2'] .d-name").inner_text()
        check(name2.endswith(" " + BY_APP) and name2[:-len(BY_APP)].strip() != "", f"the tag is plain text at the end of the name {name2!r}")
        check(card.locator(".d-fillnote").count() == 1 and card.locator(".d-fillnote").inner_text() == CHECK_BY_APP
              and card.evaluate("c => !!(c.querySelector('.d-fillnote').compareDocumentPosition(c.querySelector('.d-items')) & Node.DOCUMENT_POSITION_FOLLOWING)"),
              "one short line sits above the list of món")
        check(not small_targets(page) and not small_text(page) and overflow_ok(page)[0], f"món marked by the app: large controls, text 16 px or more, no sideways scroll {small_targets(page)} {small_text(page)}")
        page_texts.append(page.inner_text("body"))
        axe_check(page, "on /dung (draft with món put in by the app)")
        fphone = ctx.new_page(); watch(fphone, "phone-filled")
        fphone.set_viewport_size({"width": 375, "height": 800})
        fphone.goto(BASE + "/dung")
        fphone.wait_for_selector("article.d-card.is-draft .d-filled")
        check(overflow_ok(fphone)[0] and not small_targets(fphone) and not small_text(fphone), f"món marked by the app at 375 px: no horizontal scroll, large controls {overflow_ok(fphone)[1]}")
        axe_check(fphone, "on /dung at 375 px (draft with món put in by the app)")
        fphone.close()
        for pos, source in sources:
            db_run("UPDATE box_items SET source=? WHERE box_id=? AND pos=?", (source, mine["id"], pos))

        # ---------------------------------------------------------------- the server refuses Duyệt hộp: its sentence shows under the card, and goes when the box changes
        code, out = admin.call("DELETE", f"/api/boxes/{mine['id']}/items/{stage['A1']}")
        check(code == 200, f"the admin takes the last object out of the draft through the API {code} {out if code != 200 else ''}")
        force["mode"] = "pad"                       # the page is told the box is full, the real server knows it has an empty place
        page.reload()
        page.wait_for_selector("article.d-card.is-draft")
        card = page.locator("article.d-card")
        check(card.locator("[data-key=approve]").is_enabled(), "the page believes the box is full, so Duyệt hộp is on")
        card.locator("[data-key=approve]").click()
        card.locator("[data-key=approve-yes]").click()
        refused = f"Hộp mới có {stage['A1'] - 1}/{stage['A1']} món. Điền đủ món trước khi duyệt."
        check(wait_until(lambda: card.locator(".d-card-err").count() == 1, 10) and card.locator(".d-card-err").inner_text() == refused,
              f"the 422 sentence of the server shows under the card {card.inner_text()[-120:]!r}")
        check(any(e[0] == "write" and e[1].endswith("/approve") and e[2] == 422 for e in flow_log), "the server answered 422 to that approval")
        check(live_has(page, lambda t: t == refused) and page.evaluate("document.getElementById('live').classList.contains('is-bad')"), "the live region says the sentence, in the warning style")
        check(card.locator(".d-confirm").count() == 0 and wait_until(lambda: focus_key(page) == "approve", 5) and card.get_attribute("class").endswith("is-draft"),
              "after a refusal the question is closed, the box stays a draft and the focus is on Duyệt hộp")
        check(not small_targets(page) and overflow_ok(page)[0], "the refusal sentence: large controls, no sideways scroll")
        axe_check(page, "on /dung (the server refused Duyệt hộp, sentence under the card)")
        force["mode"] = "generating"                # the box changes state (seen on the next poll): the old sentence must not stay
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        check(wait_until(lambda: page.locator("article.d-card.is-generating").count() == 1 and page.locator(".d-card-err").count() == 0, 10),
              "when the box changes state the sentence under the card goes")
        force["mode"] = None

        # ---------------------------------------------------------------- a real draft with an empty place: Duyệt hộp is off with its reason, Điền ô trống fills the place
        page.reload()
        page.wait_for_selector("article.d-card.is-draft")
        card = page.locator("article.d-card")
        ap = card.locator("[data-key=approve]")
        why = f"Hộp mới có {stage['A1'] - 1}/{stage['A1']} món. Điền đủ món trước khi duyệt."
        why_id = (ap.get_attribute("aria-describedby") or "").split()[-1]
        check(ap.is_disabled() and page.locator("#" + why_id).inner_text() == why and why in card.inner_text(),
              f"a draft with an empty place: Duyệt hộp is turned off and the sentence says why {why!r}")
        check(f"{stage['A1'] - 1} trên {stage['A1']} món" in card.locator(".d-meta").inner_text(), "the card says how many objects it has of how many")
        check(card.locator("[data-key=fill]").is_enabled() and card.locator("[data-key^=swap]").count() == stage["A1"] - 1, "the draft with an empty place offers Điền ô trống and still lets the person change a món")
        check(small_targets(page) == [] and overflow_ok(page)[0] and not small_text(page), "the draft with an empty place: large controls, text 16 px or more, no sideways scroll")
        page_texts.append(page.inner_text("body"))
        axe_check(page, "on /dung (draft with an empty place, Duyệt hộp turned off with its reason)")
        card.locator("[data-key=fill]").click()
        check(live_has(page, lambda t: t == "Đang điền các ô trống của hộp M2 Box A1."), "Điền ô trống on a real draft: the live region says the empty place is being filled")
        check(wait_until(lambda: card.locator("[data-key=approve]").count() == 1 and card.locator("[data-key=approve]").is_enabled()
                         and card.locator(".d-item").count() == stage["A1"], 20), "after Điền ô trống the box is full again and Duyệt hộp works")
        check(wait_until(lambda: focus_on_head(page, mine["id"]), 8), f"after Điền ô trống the focus is on the box heading {focus_key(page)}")
        check(card.locator("[data-key=fill]").count() == 0, "a full box has no Điền ô trống")
        code, st = admin.call("GET", "/api/state")
        check(len([b for b in st["boxes"] if b["id"] == mine["id"]][0]["items"]) == stage["A1"], "the server box has all its objects again")
        ap_card = card.locator("[data-key=approve]")
        check(ap_card.is_enabled(), "Duyệt hộp is on again")

        # ---------------------------------------------------------------- when the server is not reachable the page says so and recovers
        page.reload(); page.wait_for_selector("article.d-card.is-draft")
        page.route(re.compile(r"/api/my/summary"), lambda r: r.abort())
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        check(wait_until(lambda: page.is_visible("#net-banner"), 10), "when the list cannot load a banner appears")
        check("Chưa tải được danh sách hộp." == page.inner_text("#net-msg") and page.is_visible("#retry"), "the banner says what happened, and the button Thử lại is next to it")
        check(page.locator("article.d-card").count() == 1, "the last list stays on screen while the network is down")
        axe_check(page, "on /dung (network down)")
        page.unroute(re.compile(r"/api/my/summary"))
        page.click("#retry")
        check(wait_until(lambda: not page.is_visible("#net-banner"), 8), "Thử lại hides the banner once the server answers")
        check(live_has(page, lambda t: t == "Đã kết nối lại."), "the live region says the connection is back")

        fresh = ctx.new_page(); watch(fresh, "fresh")
        fresh.route(re.compile(r"/api/my/summary"), lambda r: r.abort())
        fresh.goto(BASE + "/dung")
        fresh.wait_for_selector("#net-banner:not([hidden])")
        check(fresh.is_hidden("#s2-lead") and fresh.inner_text("#net-msg") == "Chưa tải được danh sách hộp." and fresh.locator("input[name=kind]").count() == 3, "first load fails: no blank page, the form still has its three kinds")
        check(fresh.locator("main").inner_text().strip() != "" and fresh.is_visible("#f-go"), "first load fails: the form is still usable")
        axe_check(fresh, "on /dung (first load failed)")
        fresh.close()

        broken = ctx.new_page(); watch(broken, "create-fails")
        broken.goto(BASE + "/dung"); broken.wait_for_selector("article.d-card")
        broken.route(re.compile(r"/api/my/boxes$"), lambda r: r.fulfill(status=500, content_type="application/json", body="{}") if r.request.method == "POST" else r.continue_())
        broken.check("input[value='Box B2']"); broken.click("#f-go")
        broken.wait_for_selector("#err-form:not([hidden])")
        check(broken.inner_text("#err-form") == "Chưa làm được. Thử lại.", f"a server error is only 'not done, try again': no code, no technical word {broken.inner_text('#err-form')!r}")
        check(broken.is_enabled("#f-go"), "the button works again after an error")
        broken.close()

        gone = ctx.new_page(); watch(gone, "session-ends")
        flow = []
        gone.on("framenavigated", lambda f: flow.append(f.url) if f == gone.main_frame else None)
        once = {"n": 0}
        def once_401(route):
            once["n"] += 1
            if once["n"] == 1: route.fulfill(status=401, content_type="application/json", body='{"detail": "Cần đăng nhập."}')
            else: route.continue_()
        gone.route(re.compile(r"/api/my/summary"), once_401)
        gone.goto(BASE + "/dung")
        check(wait_until(lambda: any("/login?next=/dung" in u for u in flow), 10), f"a 401 from the server sends the person to /login?next=/dung {flow}")
        gone.close()

        # ---------------------------------------------------------------- the kho: one line above Tạo hộp when it runs low; said aloud only when it crosses a threshold, not on every poll
        big = max(stage.values())
        kho = {"free": 150}
        def kho_route(route):
            data = route.fetch(url=re.sub(r"rev=-?\d+", "rev=-1", route.request.url)).json()
            data["free_objects"] = kho["free"]
            route.fulfill(status=200, content_type="application/json", body=json.dumps(data))
        kho_pat = re.compile(r"/api/my/summary")
        kline = lambda p: p.evaluate("(() => { const e = document.getElementById('kho-line'); return e.hidden ? '' : e.innerText; })()")
        kspoken = lambda p: [t for t in p.evaluate("window.__live") if t.startswith("Kho")]
        LOW = lambda n: f"Kho còn {n} món trống."
        SHORT = lambda n: f"Kho chỉ còn {n} món trống. Chưa đủ cho một hộp."

        def kho_page(width, free, label):
            kho["free"] = free
            p = ctx.new_page(); watch(p, label)
            p.set_viewport_size({"width": width, "height": 900})
            seen = []
            p.on("request", lambda r: seen.append(1) if "/api/my/summary" in r.url else None)
            p.route(kho_pat, kho_route)
            p.goto(BASE + "/dung"); p.wait_for_selector("article.d-card")
            def poll():
                n0 = len(seen)
                p.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
                wait_until(lambda: len(seen) > n0, 5); pause(0.5)
            return p, poll

        kp, kpoll = kho_page(1100, 150, "kho")
        check(kline(kp) == "" and kp.is_hidden("#kho-line") and kspoken(kp) == [], "kho: with 150 free món there is no line and nothing is said")
        check(kp.evaluate("document.getElementById('kho-line').parentElement.id") == "create-form" and kp.evaluate("document.getElementById('kho-line').nextElementSibling.id") == "f-go",
              "kho: the line sits in step 1, just above Tạo hộp")
        check(kp.locator("[aria-live], [role=status], [role=alert]").count() == 1, "kho: the line adds no live region of its own (still one polite region)")
        kho["free"] = 100; kpoll()
        check(kline(kp) == "" and kspoken(kp) == [], "kho: with 100 free món there is still no line")
        for n_free, want_line, spoken_n, label in ((80, LOW(80), 1, "80"), (79, LOW(79), 1, "79 (the number moves, nothing new is said)"), (big, LOW(big), 1, f"{big}, as many as the biggest box"),
                                                   (big - 1, SHORT(big - 1), 2, f"{big - 1}, not enough for the biggest box"), (10, SHORT(10), 2, "10 (the number moves, nothing new is said)"),
                                                   (0, SHORT(0), 2, "0"), (150, "", 2, "150 again (the line goes, nothing is said)"), (80, LOW(80), 3, "80 again")):
            kho["free"] = n_free; kpoll()
            check(wait_until(lambda: kline(kp) == want_line, 5) and wait_until(lambda: len(kspoken(kp)) == spoken_n, 5) and len(kspoken(kp)) == spoken_n,
                  f"kho: with {label} free món the line says {want_line!r} and {spoken_n} sentence(s) were said aloud {kline(kp)!r} {kspoken(kp)}")
            if n_free == 80 and spoken_n == 1:
                check(kspoken(kp) == [LOW(80)] and kp.locator("#kho-line strong").inner_text() == "80" and int(kp.evaluate("getComputedStyle(document.querySelector('#kho-line strong')).fontWeight")) >= 700,
                      "kho: the number is bold, so the meaning is not in a colour")
                axe_check(kp, "on /dung (the kho runs low)")
            if n_free == 10:
                axe_check(kp, "on /dung (the kho cannot fill a box)")
        check(kspoken(kp) == [LOW(80), SHORT(big - 1), LOW(80)], f"kho: aloud only at the crossings (low, not enough for a box, low again) {kspoken(kp)}")
        check(small_targets(kp) == [] and not small_text(kp) and overflow_ok(kp)[0], "kho: with the line, large controls, text 16 px or more, no sideways scroll")
        # the module of the person is used before the one remembered in the browser
        kp.evaluate("localStorage.setItem('bb_last_module', 'M5')")
        kho["free"] = 150
        kp.reload(); kp.wait_for_function("document.querySelector('#rep-empty').textContent !== 'Đang tải.'", timeout=15000)
        check(kp.input_value("#f-module") == "M2", f"the module of the person (M2) wins over the one remembered in the browser (M5) {kp.input_value('#f-module')!r}")
        kp.evaluate("localStorage.setItem('bb_last_module', 'M2')")
        kp.close()
        # another message is on screen: the sentence about the kho waits for a quiet moment
        kd, kdpoll = kho_page(1100, 150, "kho-wait")
        kd.click(".d-take [data-key=copy-ids]")
        check(live_has(kd, lambda t: t.startswith("Đã chép")), "kho: a message about copying is on screen")
        kho["free"] = 50; kdpoll()
        check(kline(kd) == LOW(50) and kspoken(kd) == [], f"kho: the line shows at once, but the sentence waits while another message is on screen {kline(kd)!r} {kspoken(kd)}")
        pause(9.3); kdpoll()
        check(wait_until(lambda: kspoken(kd) == [LOW(50)], 5), f"kho: once the screen is quiet the sentence is said, once {kspoken(kd)}")
        kd.close()
        # the page opens with a low kho: the line is there, and nothing is said
        kr, krpoll = kho_page(1100, 80, "kho-first")
        pause(1.5)
        check(kline(kr) == LOW(80) and kspoken(kr) == [], f"kho: a page that opens with a low kho shows the line and says nothing {kline(kr)!r} {kspoken(kr)}")
        kr.close()
        # a phone
        kq, kqpoll = kho_page(375, 10, "kho-phone")
        check(kline(kq) == SHORT(10) and overflow_ok(kq)[0] and not small_targets(kq) and not small_text(kq), f"kho at 375 px: the two sentences fit, no sideways scroll {kline(kq)!r} {overflow_ok(kq)[1]}")
        axe_check(kq, "on /dung at 375 px (the kho cannot fill a box)")
        kq.set_viewport_size({"width": 320, "height": 800})
        check(overflow_ok(kq)[0] and not small_targets(kq) and not small_text(kq), "kho at 320 px: no sideways scroll, large controls")
        page_texts.append(kq.inner_text("body"))
        kq.close()

        # ---------------------------------------------------------------- reduced motion
        ctx_rm = new_ctx(viewport={"width": 1100, "height": 900}, reduced_motion="reduce", storage_state=ctx.storage_state())
        rm = ctx_rm.new_page(); watch(rm, "reduced-motion")
        def all_generating(route):
            data = route.fetch(url=re.sub(r"rev=-?\d+", "rev=-1", route.request.url)).json()
            data["boxes"] = [{**b, "status": "generating", "status_text": "Đang chọn đồ", "items": []} for b in data["boxes"]]
            route.fulfill(status=200, content_type="application/json", body=json.dumps(data))
        ctx_rm.route(re.compile(r"/api/my/summary"), all_generating)
        rm.goto(BASE + "/dung"); rm.wait_for_selector("article.d-card.is-generating")
        check(rm.evaluate("getComputedStyle(document.querySelector('.d-chip.is-generating'), '::before').animationName") == "none", "reduced motion: the pulse is off")
        check(rm.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches"), "reduced motion: the browser asked for it")
        axe_check(rm, "on /dung (reduced motion, creating)")
        ctx_rm.close()

        # ---------------------------------------------------------------- another person sees none of these boxes
        ctx3 = new_ctx(viewport={"width": 1100, "height": 900})
        p3 = ctx3.new_page(); watch(p3, "3003")
        p3.goto(BASE + "/login")
        p3.fill("#login-id", "3003"); p3.press("#login-id", "Enter")
        p3.wait_for_url(BASE + "/dung")
        p3.wait_for_function("document.querySelector('#s2-lead').textContent.includes('Chưa có hộp nào')")
        check(p3.input_value("#f-module") == "M3" and p3.inner_text("#who") == "Hưng", "ID 3003 is prefilled with M3 and sees their own name only")
        check(p3.locator("article.d-card").count() == 0 and p3.locator(".d-take").count() == 0, "ID 3003 sees none of the boxes of ID 2002 (draft)")
        s3 = p3.evaluate("fetch('/api/my/summary?rev=-1').then(r => r.json())")
        check(s3["boxes"] == [] and s3["open_mode"] is False, "the API gives ID 3003 an empty list")
        r = p3.evaluate("fetch('/api/my/boxes/%d/items/1/replace', {method: 'POST', headers: {'Content-Type': 'application/json'}}).then(async r => [r.status, (await r.json()).detail])" % mine["id"])
        check(r[0] == 404, f"ID 3003 cannot change a box of ID 2002 (answer {r})")
        r = p3.evaluate("fetch('/my/print?ids=%d').then(r => r.text())" % mine["id"])
        check(by_pos[1]["id"] not in r, "the pick list of ID 3003 does not contain the box of ID 2002")
        p3p = ctx3.new_page(); watch(p3p, "3003-phone")
        p3p.set_viewport_size({"width": 375, "height": 800})
        p3p.goto(BASE + "/dung"); p3p.wait_for_function("document.querySelector('#s2-lead').textContent.includes('Chưa có hộp nào')")
        check(overflow_ok(p3p)[0] and not small_targets(p3p), "375 px, empty: no horizontal scroll, large controls")
        axe_check(p3p, "on /dung at 375 px (empty)")
        p3p.close()

        # ---------------------------------------------------------------- the person approves with the keyboard only; a poll sees the change before the answer to the click arrives
        page.reload(); page.wait_for_selector("article.d-card.is-draft")
        card = page.locator("article.d-card")
        card.locator("[data-key=head]").focus()
        reached = False
        for _ in range(stage["A1"] + 6):
            page.keyboard.press("Tab")
            if focus_key(page) == "approve": reached = True; break
        check(reached, "Tab reaches Duyệt hộp from the box heading, after every Đổi món")
        page.keyboard.press("Enter")
        check(focus_key(page) == "approve-no" and card.locator(".d-confirm").count() == 1, "keyboard: Enter on Duyệt hộp opens the question, the focus is on Chưa")
        page.keyboard.press("Shift+Tab")
        check(focus_key(page) == "approve-yes", "keyboard: Shift+Tab reaches Có, duyệt hộp")
        page.evaluate(SLOW_APPROVE)
        page.keyboard.press("Enter")
        check(wait_until(lambda: [b["status"] for b in admin.call("GET", "/api/state")[1]["boxes"] if b["id"] == mine["id"]] == ["approved"], 8), "keyboard: Enter on Có, duyệt hộp approves the box on the server")
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")           # a poll right now: the server already approved, the click has not heard back yet
        check(wait_until(lambda: page.locator("article.d-card.is-approved").count() == 1, 6), "a poll shows Đã duyệt while the answer to the click is still on its way")
        mid = page.evaluate("window.__live")
        check(SEEN_APPROVE not in mid and not any("đã được duyệt" in t for t in mid), f"the poll does not announce an approval the person just made {mid[-3:]}")
        check(live_has(page, lambda t: t == OWN_APPROVE, 15), "then the person's own message is said")
        pause(1)
        lives = page.evaluate("window.__live")
        check(lives.count(OWN_APPROVE) == 1 and not any("đã được duyệt" in t for t in lives), f"the approval is announced once, not twice {lives[-4:]}")
        page.evaluate("() => { window.fetch = window.__fetch; }")          # (a bare "window.fetch = ..." string would be called as a function by Playwright)
        check(wait_until(lambda: focus_on_head(page, mine["id"]), 5), "after the keyboard approval the focus is on the heading of that box")
        card = page.locator("article.d-card")
        check(card.locator(".d-chip").inner_text() == "Đã duyệt", "the chip says Đã duyệt")
        check("Lấy đồ" in page.locator(".d-steps a[aria-current=step]").inner_text() and page.locator(".d-step-li.is-done").count() == 2
              and steps(page) == ["Xong", "Xong", "Đang ở đây"], f"the progress strip is on step 3, steps 1 and 2 are done {steps(page)}")
        check(card.locator("[data-key=del]").is_disabled() and card.locator("[data-key=regen]").is_disabled(), "Xoá hộp and Chọn lại cả hộp are disabled for an approved box")
        check(all(b.is_disabled() for b in card.locator("[data-key^=swap]").all()) and card.locator("[data-key^=swap]").count() == stage["A1"], "every Đổi món is disabled for an approved box")
        check("Hộp đã duyệt. Bấm Bỏ duyệt nếu cần sửa." in card.inner_text() and "Nhờ admin" not in card.inner_text(), "a short sentence says why the edit buttons are off, and points to Bỏ duyệt")
        check(card.locator(".d-expiry").count() == 0, "an approved box has no time-left sentence")
        check(page.locator(".d-take").count() == 1 and "Danh sách này còn có thể đổi" not in page.locator(".d-take").inner_text()
              and page.locator(".d-take [data-key=copy-ids]").is_enabled(), "step 3 keeps the copy and print buttons, without the draft sentence")
        status, detail = page.evaluate("fetch('/api/my/boxes/%d/regenerate', {method: 'POST', headers: {'Content-Type': 'application/json'}}).then(async r => [r.status, (await r.json()).detail])" % mine["id"])
        check(status == 409 and detail == "Hộp này đã duyệt nên chưa sửa được. Bấm Bỏ duyệt nếu cần sửa lại.", f"the server also refuses to change an approved box, and says to use Bỏ duyệt {status} {detail!r}")
        status, detail = page.evaluate("fetch('/api/my/boxes/%d', {method: 'DELETE'}).then(async r => [r.status, (await r.json()).detail])" % mine["id"])
        check(status == 409, f"the server also refuses to delete an approved box {status}")
        ok, bad, sw = controls_ok(page)
        check(ok, f"only the controls of this page (approved) {bad} {sw}")
        check(not small_targets(page) and not small_text(page) and overflow_ok(page)[0], "approved: large controls, text 16 px or more, no sideways scroll")
        page_texts.append(page.inner_text("body"))
        axe_check(page, "on /dung (approved box, Bỏ duyệt)")
        s3 = p3.evaluate("fetch('/api/my/summary?rev=-1').then(r => r.json())")
        check(s3["boxes"] == [], "ID 3003 still sees none of the boxes after approval")
        p3.close(); ctx3.close()

        # ---------------------------------------------------------------- an approval made somewhere else (the admin steps in) shows up by polling
        card.locator("[data-key=reopen]").click()
        page.wait_for_selector("article.d-card.is-draft", timeout=15000)
        live_has(page, lambda t: t == OWN_REOPEN)
        pause(0.5)
        seen0, back0 = live_count(page, SEEN_APPROVE), live_count(page, OWN_REOPEN)
        code, out = admin.call("POST", f"/api/boxes/{mine['id']}/approve")
        check(code == 200, f"the admin approves the box through the API {code} {out if code != 200 else ''}")
        check(wait_until(lambda: page.locator("article.d-card.is-approved").count() == 1, 15), "the page shows Đã duyệt without a reload")
        card = page.locator("article.d-card")
        check(card.locator(".d-chip").inner_text() == "Đã duyệt" and card.locator("[data-key=reopen]").is_enabled() and card.locator("[data-key=reopen]").inner_text() == "Bỏ duyệt",
              "an approval made elsewhere shows the chip Đã duyệt and the button Bỏ duyệt")
        check(live_has(page, lambda t: t == SEEN_APPROVE) and live_count(page, SEEN_APPROVE) == seen0 + 1, "the live region says the box was approved, once")
        check(steps(page) == ["Xong", "Xong", "Đang ở đây"], f"the progress strip is on step 3 {steps(page)}")
        code, out = admin.call("POST", f"/api/boxes/{mine['id']}/reopen")
        check(code == 200, f"the admin takes the approval back through the API {code} {out if code != 200 else ''}")
        check(wait_until(lambda: page.locator("article.d-card.is-draft").count() == 1, 15) and card.locator("[data-key=approve]").is_enabled(), "the page shows the draft again without a reload")
        check(live_has(page, lambda t: t == OWN_REOPEN) and live_count(page, OWN_REOPEN) == back0 + 1, "the live region says the approval was taken back, once")
        code, out = admin.call("POST", f"/api/boxes/{mine['id']}/approve")
        check(code == 200 and wait_until(lambda: page.locator("article.d-card.is-approved").count() == 1, 15), "approved again")

        # ---------------------------------------------------------------- a box that went to SM: no Bỏ duyệt, and the SM sentence
        code, out = admin.call("POST", f"/api/boxes/{mine['id']}/pushed", {"sm_box_id": "SM-1"}, headers={"X-Sync-Token": "t"})
        check(code == 200, f"the team's script marks the box as pushed to SM {code} {out if code != 200 else ''}")
        sm_line = "Hộp đã lên SM nên không sửa được ở đây. Báo admin nếu cần đổi."
        check(wait_until(lambda: card.locator("[data-key=reopen]").count() == 0 and sm_line in card.inner_text(), 15), "a pushed box loses Bỏ duyệt and says it went to SM")
        check(card.locator(".d-chip").inner_text() == "Đã duyệt" and card.locator("[data-key=approve]").count() == 0 and "Bấm Bỏ duyệt" not in card.inner_text()
              and "Bỏ duyệt để sửa lại hộp." not in card.inner_text(), "a pushed box has no Bỏ duyệt, no Duyệt hộp and no line that points to them")
        check(card.locator("[data-key=del]").is_disabled() and card.locator("[data-key=regen]").is_disabled()
              and all(b.is_disabled() for b in card.locator("[data-key^=swap]").all()), "a pushed box keeps its edit buttons turned off")
        check(page.locator(".d-take [data-key=copy-ids]").is_enabled() and page.locator(".d-take a[data-key=print]").count() == 1, "a pushed box can still be copied and printed")
        status, detail = page.evaluate("fetch('/api/my/boxes/%d/reopen', {method: 'POST', headers: {'Content-Type': 'application/json'}}).then(async r => [r.status, (await r.json()).detail])" % mine["id"])
        check(status == 409 and "SM" in detail, f"the server refuses Bỏ duyệt for a box in SM, with a sentence about SM {status} {detail!r}")
        ok, bad, sw = controls_ok(page)
        check(ok, f"only the controls of this page (approved, pushed) {bad} {sw}")
        check(not small_targets(page) and not small_text(page) and overflow_ok(page)[0], "approved and pushed: large controls, text 16 px or more, no sideways scroll")
        page_texts.append(page.inner_text("body"))
        axe_check(page, "on /dung (approved box in SM)")
        phone2 = ctx.new_page(); watch(phone2, "phone-pushed")
        phone2.set_viewport_size({"width": 375, "height": 800})
        phone2.goto(BASE + "/dung"); phone2.wait_for_selector("article.d-card.is-approved")
        check(overflow_ok(phone2)[0] and not small_targets(phone2) and not small_text(phone2), "approved and pushed at 375 px: no horizontal scroll, large controls")
        axe_check(phone2, "on /dung at 375 px (approved box in SM)")
        phone2.close()
        # the page has not heard of SM yet (the answer is rewritten): Bỏ duyệt is shown, the server refuses, its sentence shows under the card
        force["mode"] = "unpushed"
        page.reload(); page.wait_for_selector("article.d-card.is-approved")
        card = page.locator("article.d-card")
        check(card.locator("[data-key=reopen]").is_enabled(), "(rewritten answer) Bỏ duyệt is shown")
        card.locator("[data-key=reopen]").click()
        check(wait_until(lambda: card.locator(".d-card-err").count() == 1, 10) and "đã lên SM" in card.locator(".d-card-err").inner_text(),
              f"the 409 sentence about SM shows under the card {card.inner_text()[-160:]!r}")
        check(any(e[0] == "write" and e[1].endswith("/reopen") and e[2] == 409 for e in flow_log) and card.get_attribute("class").endswith("is-approved"), "the server answered 409 and the box stays approved")
        check(wait_until(lambda: focus_key(page) == "reopen", 5), "after the refusal the focus is on Bỏ duyệt")
        axe_check(page, "on /dung (the server refused Bỏ duyệt, sentence under the card)")
        force["mode"] = None
        page.reload(); page.wait_for_selector("article.d-card.is-approved")
        card = page.locator("article.d-card")

        # ---------------------------------------------------------------- an admin sees the link to the admin page
        ctx_a = browser.new_context(locale="vi-VN", viewport={"width": 1100, "height": 900})      # not recorded: the admin page makes admin calls
        pa = ctx_a.new_page(); pa.on("pageerror", lambda e: errs.append(f"admin pageerror: {e}"))
        pa.goto(BASE + "/login"); pa.fill("#login-id", "1001"); pa.press("#login-id", "Enter")
        pa.wait_for_url(BASE + "/")
        pa.goto(BASE + "/dung"); pa.wait_for_selector("#admin-link:not([hidden])")
        check(pa.get_attribute("#admin-link", "href") == "/" and pa.inner_text("#admin-link") == "Trang admin" and pa.inner_text("#who") == "1001", "an admin with no name sees the ID alone, and the link Trang admin")
        ok, bad, sw = controls_ok(pa)
        check(ok, f"only the controls of this page (admin) {bad} {sw}")
        axe_check(pa, "on /dung (admin)")
        ctx_a.close()

        # ---------------------------------------------------------------- logout
        page.click("#logout")
        page.wait_for_url(BASE + "/login")
        check(page.url == BASE + "/login", "Thoát returns to /login")
        page.goto(BASE + "/dung")
        check(urllib.parse.urlsplit(page.url).path == "/login" and "next=/dung" in page.url, "after Thoát, /dung redirects back to /login")
        code = page.evaluate("fetch('/api/my/summary').then(r => r.status)")
        check(code == 401, "after Thoát the API answers 401")
        page.fill("#login-id", "2002"); page.press("#login-id", "Enter")
        page.wait_for_url(BASE + "/dung")
        check(page.url == BASE + "/dung", "signing in again with ID 2002 lands on /dung")
        page.wait_for_selector("article.d-card.is-approved")

        # ================================================================ the panel Hôm nay, the plan, the report and the block Gần đây (people 3003 and 4004)
        code, out = admin.call("POST", "/api/admin/people", {"text": "4004, Lan, M4"})
        check(code == 200 and out["added"] == 1, f"the admin adds a third person {out}")
        c3 = Client(BASE)
        code, out = c3.call("POST", "/api/auth/login", {"id": "3003"})
        check(code == 200, "ID 3003 signs in with a plain HTTP client")
        _, yesterday = vn_at(1)
        db_run("INSERT INTO plans(owner, day, data, updated_at) VALUES(?,?,?,?)",
               ("3003", yesterday, json.dumps({"module": "M3", "units": [{"code": "M3-01", "anchor": "Tủ lạnh", "shift": "off"}, {"code": "M3-02", "anchor": "Máy giặt", "shift": "first"}]}, ensure_ascii=False), time.time()))
        # scenes made lately for module M3 (by nobody of this test: they must show in the block Gần đây, and not in anyone's list to hand back)
        add_history(900001, "M3", "M3-01 Box B3", "M3-01", "Tủ lạnh", "nấu ăn", "5005", "M3-01 - Tủ lạnh - B3", 1)
        add_history(900002, "M3", "M3-02 Box B2", "M3-02", "Máy giặt", "giặt đồ", "5005", "M3-02 - Máy giặt - B2", 2)
        add_history(900003, "M3", "M3-03 Box A1", "M3-03", "Giường", "phòng ngủ", "5005", "M3-03 - Giường - A1", 3)
        # person 4004: a live approved box of yesterday and a finished box of the day before (both to hand back), one of three days ago and one of today (neither)
        add_box("M4", "M4-01 Box B3", "M4-01", "Bàn làm việc", "4004", "approved", 1)
        add_history(900004, "M4", "M4-02 Box B2", "M4-02", "Máy giặt", "", "4004", "M4-02 - Máy giặt - B2", 2)
        add_history(900005, "M4", "M4-03 Box A1", "M4-03", "Giường", "", "4004", "M4-03 - Giường - A1", 3)
        add_box("M4", "M4-04 Box B2", "M4-04", "Bếp", "4004", "approved", 0, hour=0)

        # ---------------------------------------------------------------- the boxes to hand back (from live boxes and from finished ones)
        ctx4 = new_ctx(viewport={"width": 1100, "height": 900})
        p4 = ctx4.new_page(); watch(p4, "4004")
        p4.goto(BASE + "/login"); p4.fill("#login-id", "4004"); p4.press("#login-id", "Enter"); p4.wait_for_url(BASE + "/dung")
        p4.wait_for_function("document.querySelectorAll('#ret-list li').length > 0", timeout=15000)
        lines = p4.eval_on_selector_all("#ret-list li", "els => els.map(e => e.textContent)")
        check(lines == ["Hôm qua: M4-01 - Bàn làm việc - B3", "Hôm kia: M4-02 - Máy giặt - B2"],
              f"the boxes to hand back are yesterday's and the day before's, from live and finished boxes, none of today or three days ago {lines}")
        check(p4.inner_text("#fold-return .d-fold-t") == "Hộp cần trả sáng nay" and p4.text_content("#ret-n").strip() == "(2)", f"the closed section says how many boxes to hand back {p4.inner_text('#fold-return > summary')!r}")
        p4.click("#fold-return > summary")
        check(p4.is_hidden("#ret-empty") and p4.locator("#ret-list li").count() == 2 and p4.locator("#fold-return input").count() == 0
              and "đã trả" not in p4.inner_text("#fold-return").lower(), "the list has no tick box and never says anything was handed back")
        check(small_targets(p4) == [] and overflow_ok(p4)[0], f"boxes to hand back: large controls, no sideways scroll {small_targets(p4)}")
        page_texts.append(p4.inner_text("body"))
        axe_check(p4, "on /dung (boxes to hand back, section open)")
        ctx4.close()

        # ---------------------------------------------------------------- person 3003 signs in; the clock of the day at many moments, in a browser set to a far away time zone
        ctx_h = new_ctx(viewport={"width": 1100, "height": 900})
        ctx_h.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
        ph = ctx_h.new_page(); watch(ph, "3003-login")
        ph.goto(BASE + "/login"); ph.fill("#login-id", "3003"); ph.press("#login-id", "Enter"); ph.wait_for_url(BASE + "/dung")
        ph.wait_for_function("document.querySelectorAll('#units > li').length === 2", timeout=15000)
        state3 = ctx_h.storage_state()
        ph.close()
        ctx_c = new_ctx(viewport={"width": 1100, "height": 900}, timezone_id="America/New_York", storage_state=state3)
        DONE, NOW, NEXT = "Xong", "Bây giờ", "Tiếp theo"
        CLOCKS = [   # (hour, minute), the line, the marks of the 7 lines (start, B1, B2, B2, printing, report, B3), the tag of Hộp cần trả, the tag of Báo cáo
            ((2, 29), "Bây giờ 02:29. Ngoài giờ làm.", ["", "", "", "", "", "", ""], "", ""),
            ((2, 30), "Bây giờ 02:30. Tiếp theo: B1 lúc 3:30.", [NOW, NEXT, "", "", "", "", ""], NOW, ""),
            ((2, 40), "Bây giờ 02:40. Tiếp theo: B1 lúc 3:30.", [NOW, NEXT, "", "", "", "", ""], NOW, ""),
            ((4, 0), "Bây giờ 04:00. Tiếp theo: B2 lúc 6:00.", [DONE, NOW, NEXT, "", "", "", ""], "", ""),
            ((5, 45), "Bây giờ 05:45. Tiếp theo: B2 lúc 6:00.", [DONE, DONE, NEXT, "", "", "", ""], "", ""),
            ((6, 30), "Bây giờ 06:30. Tiếp theo: B2 lúc 8:15.", [DONE, DONE, NOW, NEXT, "", "", ""], "", ""),
            ((8, 0), "Bây giờ 08:00. Tiếp theo: B2 lúc 8:15.", [DONE, DONE, DONE, NEXT, "", "", ""], "", ""),
            ((8, 15), "Bây giờ 08:15. Tiếp theo: in giấy cho supporter và báo cáo cuối ngày lúc 9:00.", [DONE, DONE, DONE, NOW, NEXT, NEXT, ""], "", NEXT),
            ((8, 59), "Bây giờ 08:59. Tiếp theo: in giấy cho supporter và báo cáo cuối ngày lúc 9:00.", [DONE, DONE, DONE, NOW, NEXT, NEXT, ""], "", NEXT),
            ((9, 0), "Bây giờ 09:00. Tiếp theo: B3 lúc 9:30.", [DONE, DONE, DONE, NOW, NOW, NOW, NEXT], "", NOW),
            ((9, 29), "Bây giờ 09:29. Tiếp theo: B3 lúc 9:30.", [DONE, DONE, DONE, NOW, NOW, NOW, NEXT], "", NOW),
            ((9, 30), "Bây giờ 09:30. Ca làm hết lúc 11:30.", [DONE, DONE, DONE, DONE, NOW, NOW, NOW], "", NOW),
            ((9, 45), "Bây giờ 09:45. Ca làm hết lúc 11:30.", [DONE, DONE, DONE, DONE, NOW, NOW, NOW], "", NOW),
            ((9, 59), "Bây giờ 09:59. Ca làm hết lúc 11:30.", [DONE, DONE, DONE, DONE, NOW, NOW, NOW], "", NOW),
            ((10, 0), "Bây giờ 10:00. Ca làm hết lúc 11:30.", [DONE, DONE, DONE, DONE, DONE, DONE, NOW], "", ""),
            ((10, 5), "Bây giờ 10:05. Ca làm hết lúc 11:30.", [DONE, DONE, DONE, DONE, DONE, DONE, NOW], "", ""),
            ((11, 10), "Bây giờ 11:10. Ca làm hết lúc 11:30.", [DONE, DONE, DONE, DONE, DONE, DONE, NOW], "", ""),
            ((11, 30), "Bây giờ 11:30. Ngoài giờ làm.", ["", "", "", "", "", "", ""], "", ""),
            ((11, 40), "Bây giờ 11:40. Ngoài giờ làm.", ["", "", "", "", "", "", ""], "", ""),
            ((13, 0), "Bây giờ 13:00. Ngoài giờ làm.", ["", "", "", "", "", "", ""], "", ""),
        ]
        for (hh, mm), line, want_marks, ret_tag, rep_tag in CLOCKS:
            cp = ctx_c.new_page(); watch(cp, f"clock-{hh:02d}{mm:02d}")
            cp.clock.install(time=datetime.datetime.now(VN).replace(hour=hh, minute=mm, second=5, microsecond=0))
            cp.goto(BASE + "/dung")
            cp.wait_for_function("document.querySelector('#now-line').textContent.startsWith('Bây giờ')", timeout=15000)
            check(cp.inner_text("#now-line") == line, f"at {hh:02d}:{mm:02d} Vietnam time the line says {line!r} (got {cp.inner_text('#now-line')!r})")
            check(marks(cp) == want_marks, f"at {hh:02d}:{mm:02d} the marks of the day list are {want_marks} (got {marks(cp)})")
            check(tag(cp, "#ret-tag") == ret_tag and tag(cp, "#rep-tag") == rep_tag, f"at {hh:02d}:{mm:02d} the tags of the sections are {ret_tag!r} and {rep_tag!r} (got {tag(cp, '#ret-tag')!r}, {tag(cp, '#rep-tag')!r})")
            if (hh, mm) == (4, 0):
                check(cp.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone") == "America/New_York", "the browser itself is set to another time zone: the line still follows Vietnam")
                cp.click("#fold-day > summary")
                check(cp.is_visible("#timeline") and cp.locator("#timeline li").count() == 7, "the day list shows 7 lines")
                parts = cp.evaluate("[...document.querySelectorAll('#timeline li')].map(li => [...li.children].map(c => c.textContent))")
                check([p[0] for p in parts] == ["2:30", "3:30 đến 5:30", "6:00 đến 7:15", "8:15 đến 9:30", "9:00 đến 10:00", "9:00 đến 10:00", "từ 9:30"]
                      and [p[1] for p in parts] == ["Đầu ngày: trả hộp trong SM, hỏi leader unit nào đi làm", "B1", "B2", "B2", "In giấy cho supporter",
                                                    "Báo cáo cuối ngày: gửi quản lý các unit không đi làm", "B3"], f"the day list: the times and what to do {parts}")
                widths = cp.evaluate("[...document.querySelectorAll('#timeline .d-tl-when')].map(e => Math.round(e.getBoundingClientRect().width))")
                check(len(set(widths)) == 1, f"the time column of the day list has one width, so the texts line up {widths}")
                check(cp.locator("#timeline li[aria-current=time]").count() == 1 and "is-now" in cp.locator("#timeline li[aria-current=time]").get_attribute("class"),
                      "the current line carries aria-current, and the marks are words")
                check(not small_targets(cp) and not small_text(cp) and overflow_ok(cp)[0], "the day list: large controls, text 16 px or more, no sideways scroll")
                page_texts.append(cp.inner_text("body"))
                axe_check(cp, "on /dung (Hôm nay, day list open at 04:00)")
            if (hh, mm) == (8, 59):
                page_texts.append(cp.inner_text("body"))
                axe_check(cp, "on /dung (Hôm nay at 08:59, the report is next)")
            if (hh, mm) == (9, 0):
                page_texts.append(cp.inner_text("body"))
                axe_check(cp, "on /dung (Hôm nay at 09:00, the report is now)")
            cp.close()

        # a minute passes, the tab is hidden, the plan is read again every few minutes
        cp = ctx_c.new_page(); watch(cp, "clock-ticks")
        plan_gets = []
        cp.on("request", lambda r: plan_gets.append(r.url) if r.method == "GET" and "/api/my/plan" in r.url else None)
        cp.clock.install(time=datetime.datetime.now(VN).replace(hour=5, minute=29, second=10, microsecond=0))
        cp.goto(BASE + "/dung")
        cp.wait_for_function("document.querySelector('#now-line').textContent.startsWith('Bây giờ')")
        check(cp.inner_text("#now-line") == "Bây giờ 05:29. Tiếp theo: B2 lúc 6:00." and marks(cp)[1] == NOW, f"05:29: B1 is still on {cp.inner_text('#now-line')!r}")
        wait_until(lambda: len(plan_gets) >= 1, 10)
        n0 = len(plan_gets)
        cp.clock.run_for(60000)
        check(wait_until(lambda: cp.inner_text("#now-line") == "Bây giờ 05:30. Tiếp theo: B2 lúc 6:00.", 6) and marks(cp)[1] == DONE, f"a minute later the line and the marks are worked out again {cp.inner_text('#now-line')!r} {marks(cp)}")
        cp.clock.run_for(125000)
        check(wait_until(lambda: len(plan_gets) == n0 + 1, 8), f"after about 3 minutes the plan is read once more ({n0} then {len(plan_gets)})")
        cp.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => true}); document.dispatchEvent(new Event('visibilitychange'))")
        text_before = cp.inner_text("#now-line")
        cp.clock.run_for(180000)
        check(cp.inner_text("#now-line") == text_before and len(plan_gets) == n0 + 1, f"while the tab is hidden the clock and the plan wait {cp.inner_text('#now-line')!r}")
        cp.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, get: () => false}); document.dispatchEvent(new Event('visibilitychange'))")
        check(wait_until(lambda: cp.inner_text("#now-line") != text_before, 5) and cp.inner_text("#now-line").startswith("Bây giờ 05:3"), f"when the tab is shown again the line is right at once {cp.inner_text('#now-line')!r}")
        cp.close()
        ctx_c.close()

        # ---------------------------------------------------------------- the plan: yesterday's units, then three more with other shifts, then save
        pg = ctx_h.new_page(); watch(pg, "plan")
        plan_calls, put_bodies, recent_reqs = [], [], []
        pg.on("response", lambda r: plan_calls.append((r.request.method, urllib.parse.urlsplit(r.url).path, r.status)) if "/api/my/plan" in r.url else None)
        pg.on("request", lambda r: put_bodies.append(r.post_data_json) if r.method == "PUT" and "/api/my/plan" in r.url else None)
        pg.on("request", lambda r: recent_reqs.append(r.url) if "/api/my/recent" in r.url else None)
        pg.goto(BASE + "/dung")
        pg.wait_for_function("document.querySelectorAll('#units > li').length === 2", timeout=15000)
        check(pg.text_content("#ret-empty") == "Không có hộp nào cần trả." and pg.text_content("#ret-n") == "", "nothing to hand back: one plain sentence")
        open_folds(pg)
        rows = rows_of(pg)
        check(pg.is_visible("#plan-old") and pg.inner_text("#plan-old") == "Đây là unit hôm qua, ai cũng làm cả ngày. Chọn ca hôm nay.",
              "a plan started from yesterday's units says so in one short sentence")
        code, plan0 = c3.call("GET", "/api/my/plan")
        check(code == 200 and plan0["saved"] is False and plan0["report"] == "" and len(plan0["units"]) == 2, f"the server has today's plan unsaved and sends no report {plan0.get('saved')} {plan0.get('report')!r}")
        check(pg.is_visible("#rep-empty") and pg.inner_text("#rep-empty") == NOT_SAVED and pg.is_hidden("#rep-box") and pg.is_hidden("#rep-text") and pg.is_hidden("#rep-copy"),
              f"the plan is not saved today: no report, no copy button, one sentence {pg.inner_text('#rep-empty')!r}")
        check(pg.input_value("#p-module") == "M3" and pg.input_value("#f-module") == "M3", "the module of the plan is in its field, and the create form has it too")
        check(rows.count() == 2 and rows.nth(0).locator("input").nth(0).input_value() == "M3-01" and rows.nth(0).locator("input").nth(1).input_value() == "Tủ lạnh"
              and rows.nth(1).locator("input").nth(0).input_value() == "M3-02", "yesterday's two units are in rows (section code, large object)")
        check(pg.eval_on_selector_all("#units > li:first-child select option", "els => els.map(e => e.textContent)") == SHIFT_TEXTS
              and pg.eval_on_selector_all("#units > li:first-child select option", "els => els.map(e => e.value)") == ["full", "first", "second", "b1b2", "off"],
              "the shift select offers the texts the server gives")
        check(rows.nth(0).locator("select").input_value() == "full" and rows.nth(1).locator("select").input_value() == "full",
              "yesterday's units start with everyone on a full day (also the one that rested)")
        check(rows.nth(0).locator(".d-need-h").inner_text() == "Cần làm:" and row_needs(rows.nth(0)) == ["B2", "B3", "A1"] and row_states(rows.nth(0)) == ["Chưa tạo"] * 3
              and rows.nth(0).locator(".d-needs button").all_inner_texts() == ["Tạo B2", "Tạo B3", "Tạo A1"], f"a unit shows the boxes it needs, their state and a button for each {row_needs(rows.nth(0))}")
        page_texts.append(pg.inner_text("body"))
        axe_check(pg, "on /dung (plan open, yesterday's units, sentence on top, report not saved)")

        pg.click("#unit-add")
        check(pg.evaluate("document.activeElement.id") == rows.nth(2).locator("input").nth(0).get_attribute("id") and rows.count() == 3, "Thêm unit adds a row and puts the keyboard on its section code")
        pg.keyboard.type("M3-03"); pg.keyboard.press("Tab"); pg.keyboard.type("Bếp từ")
        pg.click("#unit-add"); pg.keyboard.type("M3-04"); pg.keyboard.press("Tab"); pg.keyboard.type("Lò vi sóng")
        pg.click("#unit-add"); pg.keyboard.type("M3-05"); pg.keyboard.press("Tab"); pg.keyboard.type("Giường")
        rows.nth(0).locator("select").select_option("off")
        rows.nth(1).locator("select").select_option("first")
        rows.nth(2).locator("select").select_option("b1b2")
        rows.nth(3).locator("select").select_option("second")
        check(rows.count() == 5 and pg.is_visible("#plan-dirty") and pg.inner_text("#plan-dirty") == "Chưa lưu thay đổi.", "five rows and the sentence Chưa lưu thay đổi.")
        check(all(rows.nth(i).locator(".d-needs *").count() == 0 for i in range(5)), "a row that differs from the saved plan shows no needs")
        check(rows.nth(0).locator("[data-key=unit-del]").get_attribute("aria-label") == "Xoá unit M3-01", "the Xoá unit button names its unit")
        check(small_targets(pg) == [] and not small_text(pg) and overflow_ok(pg)[0], f"plan being edited: large controls, text 16 px or more, no sideways scroll {small_targets(pg)}")
        axe_check(pg, "on /dung (plan with changes not saved)")
        pg.click("#plan-save")
        check(wait_until(lambda: ("PUT", "/api/my/plan", 200) in plan_calls, 10), f"Lưu kế hoạch writes the plan {plan_calls}")
        check(put_bodies and put_bodies[-1] == {"module": "M3", "units": [{"code": "M3-01", "anchor": "Tủ lạnh", "shift": "off"}, {"code": "M3-02", "anchor": "Máy giặt", "shift": "first"},
                                                                     {"code": "M3-03", "anchor": "Bếp từ", "shift": "b1b2"}, {"code": "M3-04", "anchor": "Lò vi sóng", "shift": "second"},
                                                                     {"code": "M3-05", "anchor": "Giường", "shift": "full"}]}, f"the write carries the module and the units {put_bodies[-1:]}")
        check(live_has(pg, lambda t: t == "Đã lưu kế hoạch."), "the live region says the plan was saved")
        check(wait_until(lambda: pg.is_hidden("#plan-dirty") and pg.is_hidden("#plan-old"), 5), "after saving the sentence Chưa lưu thay đổi. and the sentence about yesterday go")
        check(pg.evaluate("document.activeElement.id") == "plan-save", "after saving the keyboard focus stays on Lưu kế hoạch")
        check(wait_until(lambda: pg.is_visible("#rep-box") and pg.is_hidden("#rep-empty") and pg.input_value("#rep-text").startswith("Báo cáo ngày ") and pg.is_enabled("#rep-copy"), 5),
              "after saving the report appears with its copy button, and the sentence about saving goes")
        check(rows.nth(0).locator(".d-needs").inner_text().strip() == "Không cần làm hộp." and row_needs(rows.nth(0)) == [], "a unit that rests needs no box")
        check(row_needs(rows.nth(1)) == ["B2 (bỏ được)", "A1"] and rows.nth(1).locator(".d-why").count() == 0, f"first half: B2 can be skipped, the note does not repeat it {row_needs(rows.nth(1))}")
        check(row_needs(rows.nth(2)) == ["B2", "A1"] and rows.nth(2).locator(".d-why").all_inner_texts() == ["Dùng B2 làm B3, không làm B3 mới."], "B1 and B2 only: B2 is reused as B3, said under the list")
        check(row_needs(rows.nth(3)) == ["B3"] and row_needs(rows.nth(4)) == ["B2", "B3", "A1"], "second half needs B3 only, a full day needs B2, B3 and A1")
        code, plan = c3.call("GET", "/api/my/plan")
        check(code == 200 and plan["saved"] is True and plan["module"] == "M3" and [u["shift"] for u in plan["units"]] == ["off", "first", "b1b2", "second", "full"], f"the server has the plan {code}")
        page_texts.append(pg.inner_text("body"))
        axe_check(pg, "on /dung (plan saved, units with their needs)")
        pg.reload()
        pg.wait_for_function("document.querySelectorAll('#units > li').length === 5", timeout=15000)
        open_folds(pg)
        rows = rows_of(pg)
        check(pg.is_hidden("#plan-old") and [rows.nth(i).locator("select").input_value() for i in range(5)] == ["off", "first", "b1b2", "second", "full"] and row_needs(rows.nth(4)) == ["B2", "B3", "A1"],
              "after a reload the saved plan is there: shifts and needs")

        # ---------------------------------------------------------------- Tạo B2: the form is filled, the keyboard is on the theme; the box shows in its unit
        rows.nth(1).locator("button", has_text="Tạo B2").click()
        check(pg.input_value("#f-module") == "M3" and pg.input_value("#f-code") == "M3-02" and pg.input_value("#f-anchor") == "Máy giặt"
              and pg.evaluate("document.querySelector('input[name=kind]:checked').value") == "Box B2" and pg.input_value("#f-theme") == "", "Tạo B2 fills module, section code, large object and kind")
        check(pg.evaluate("document.activeElement.id") == "f-theme", "Tạo B2 moves the keyboard focus to the theme field")
        check(live_has(pg, lambda t: t == "Đã điền M3-02 Box B2 ở bước 1."), "the live region says the form was filled")
        check(wait_until(lambda: pg.is_visible("#recent"), 5) and pg.inner_text("#recent-h") == "Gần đây ở M3 M3-02"
              and pg.locator("#recent-list li").all_inner_texts() == ["Hôm kia · Box B2 · giặt đồ"], f"the block Gần đây shows the scene of that section {pg.locator('#recent-list li').all_inner_texts()}")
        pg.keyboard.type("máy giặt và phơi đồ"); pg.keyboard.press("Tab")
        check(pg.evaluate("document.activeElement.id") == "f-go", "Tab goes from the theme to Tạo hộp")
        pg.keyboard.press("Enter")
        pg.wait_for_selector("article.d-card", timeout=15000)
        box_id = pg.locator("article.d-card").first.get_attribute("data-box")
        check(wait_until(lambda: row_states(rows.nth(1)) == ["Chờ duyệt", "Chưa tạo"], 30), f"after the box is made and picked its unit says Chờ duyệt {row_states(rows.nth(1))}")
        check(rows.nth(1).locator(".d-needs button").all_inner_texts() == ["Tạo A1"] and rows.nth(1).locator("a[data-key=need-B2]").inner_text() == "Xem hộp B2"
              and rows.nth(1).locator("a[data-key=need-B2]").get_attribute("aria-label") == "Xem hộp B2 của M3-02", "a made box has a link, a box not made keeps its button")
        link = rows.nth(1).locator("a[data-key=need-B2]")
        link.focus(); pg.keyboard.press("Enter")
        check(pg.evaluate("document.activeElement.dataset.key") == "head" and pg.evaluate("document.activeElement.closest('article').dataset.box") == box_id and pg.url == BASE + "/dung",
              "the link moves the keyboard focus to the card of that box in step 2")
        card = pg.locator("article.d-card").first
        card.locator("[data-key=approve]").click(); card.locator("[data-key=approve-yes]").click()
        check(wait_until(lambda: row_states(rows.nth(1)) == ["Đã duyệt", "Chưa tạo"], 15), f"after the person approves, the unit says Đã duyệt {row_states(rows.nth(1))}")
        ok, bad, sw = controls_ok(pg)
        check(ok, f"only the controls of this page (plan with units) {bad} {sw}")
        page_texts.append(pg.inner_text("body"))
        axe_check(pg, "on /dung (a unit with a made, approved box)")

        # ---------------------------------------------------------------- the block Gần đây: when it shows, hides, updates, and what it does with late answers
        n_before = len(recent_reqs)
        pg.fill("#f-code", "")
        check(wait_until(lambda: pg.locator("#recent-list li").count() == 4, 5), "with no section the block lists all the lately made scenes of the module")
        got = pg.locator("#recent-list li").all_inner_texts()
        check(got == ["Hôm nay · M3-02 · Box B2 · máy giặt và phơi đồ", "Hôm qua · M3-01 · Box B3 · nấu ăn", "Hôm kia · M3-02 · Box B2 · giặt đồ", "3 ngày trước · M3-03 · Box A1 · phòng ngủ"],
              f"lines are the day, the section, the kind and the scene, newest first {got}")
        check(pg.inner_text("#recent-h") == "Gần đây ở M3" and "Chọn bối cảnh khác để khỏi làm trùng." in pg.inner_text("#recent"), "the block has its heading and one lead sentence")
        check(pg.evaluate("document.getElementById('recent').compareDocumentPosition(document.getElementById('f-theme')) & Node.DOCUMENT_POSITION_FOLLOWING") != 0
              and pg.evaluate("document.getElementById('f-code').compareDocumentPosition(document.getElementById('recent')) & Node.DOCUMENT_POSITION_FOLLOWING") != 0,
              "the block is under the module and section fields, above the theme")
        axe_check(pg, "on /dung (block Gần đây with four lines)")
        page_texts.append(pg.inner_text("body"))
        n_before = len(recent_reqs)
        pg.click("#f-code"); pg.keyboard.type("M3-01", delay=40)
        check(wait_until(lambda: pg.locator("#recent-list li").count() == 1, 5) and pg.locator("#recent-list li").first.inner_text() == "Hôm qua · Box B3 · nấu ăn"
              and pg.inner_text("#recent-h") == "Gần đây ở M3 M3-01", "changing the section updates the block")
        check(len(recent_reqs) - n_before == 1, f"typing quickly asks once, after a short wait ({len(recent_reqs) - n_before} calls)")
        pg.fill("#f-module", "M9")
        check(wait_until(lambda: pg.is_hidden("#recent"), 5), "a module with nothing lately hides the block")
        pg.fill("#f-module", "")
        n_before = len(recent_reqs)
        pause(1.0)
        check(pg.is_hidden("#recent") and len(recent_reqs) == n_before, "an empty module asks nothing and shows nothing")
        pg.fill("#f-module", "M3"); pg.fill("#f-code", "")
        wait_until(lambda: pg.locator("#recent-list li").count() == 4, 5)
        pg.evaluate("""() => { window.__fetch2 = window.fetch;
          window.fetch = (u, o) => { const p = window.__fetch2(u, o); return /api\\/my\\/recent.*code=M3-01/.test(String(u)) ? p.then(r => new Promise(res => setTimeout(() => res(r), 1800))) : p; }; }""")
        pg.fill("#f-code", "M3-01"); pause(0.7)
        pg.fill("#f-code", "M3-02")
        check(wait_until(lambda: pg.inner_text("#recent-h") == "Gần đây ở M3 M3-02", 5), "the answer to the newest question shows")
        pause(2.3)
        check(pg.inner_text("#recent-h") == "Gần đây ở M3 M3-02" and pg.locator("#recent-list li").count() == 2, f"an answer that comes late is ignored {pg.inner_text('#recent-h')!r} {pg.locator('#recent-list li').count()}")
        pg.evaluate("() => { window.fetch = window.__fetch2; }")
        said = len(pg.evaluate("window.__live"))
        pg.route(re.compile(r"/api/my/recent"), lambda r: r.abort())
        pg.fill("#f-code", "M3-01")
        check(wait_until(lambda: pg.is_hidden("#recent"), 5), "a failed call hides the block")
        pause(0.6)
        check(len(pg.evaluate("window.__live")) == said, "a failed call says nothing")
        pg.click("label:has-text('Box A1')")
        check(pg.is_enabled("#f-go") and pg.is_hidden("#err-form"), "the block never blocks making a box: the form is still ready")
        pg.unroute(re.compile(r"/api/my/recent"))
        pg.fill("#f-code", "")

        # ---------------------------------------------------------------- the report
        code, plan = c3.call("GET", "/api/my/plan")
        want = plan["report"]
        check(want.startswith("Báo cáo ngày ") and ", module M3:" in want and "Unit không đi làm: M3-01 (Tủ lạnh)." in want and "Unit làm nửa ca đầu: M3-02 (Máy giặt)." in want
              and "Unit chỉ làm B1 và B2: M3-03 (Bếp từ)." in want and "Unit làm nửa ca sau: M3-04 (Lò vi sóng)." in want, f"the server's report {want!r}")
        check(pg.input_value("#rep-text") == want and pg.get_attribute("#rep-text", "readonly") is not None and pg.is_hidden("#rep-empty"), "the report is in a read-only area, the same text as the server's")
        check(pg.get_attribute("#rep-text", "aria-labelledby") == "rep-t" and pg.inner_text("#rep-t") == "Báo cáo cuối ngày", "the area is named by its section")
        check(pg.locator("#rep-copy").inner_text() == "Sao chép báo cáo" and pg.locator("#rep-copy").bounding_box()["height"] >= 48, "the copy button says what it does and is at least 48 px high")
        pg.click("#rep-copy")
        check(live_has(pg, lambda t: t == "Đã chép báo cáo."), "the live region says the report was copied")
        check(clip_text(pg) == want, f"the clipboard holds the server's report {clip_text(pg)!r}")
        # the old way of copying when the clipboard is not allowed
        pg.evaluate("Object.defineProperty(navigator, 'clipboard', {configurable: true, get: () => undefined})")
        pg.click("#rep-copy")
        check(wait_until(lambda: live_count(pg, "Đã chép báo cáo.") == 2, 5) and not live_has(pg, lambda t: t.startswith("Không chép được"), 0.5),
              "with no clipboard the report is copied the old way")
        axe_check(pg, "on /dung (report section open)")
        # typed changes not saved: the last saved report stays, the copy button is off, and the one existing sentence says why
        anchor0 = rows.nth(0).locator("input").nth(1)
        copied = live_count(pg, "Đã chép báo cáo.")
        anchor0.fill("Tủ lạnh nhỏ")
        check(pg.is_visible("#plan-dirty") and pg.inner_text("#plan-dirty") == "Chưa lưu thay đổi." and pg.is_disabled("#rep-copy") and pg.is_visible("#rep-box")
              and pg.input_value("#rep-text") == want and pg.get_attribute("#rep-copy", "aria-describedby") == "plan-dirty",
              "with changes not saved the last saved report stays, Sao chép báo cáo is turned off and Chưa lưu thay đổi. is the only sentence")
        pg.locator("#rep-copy").click(force=True)
        pause(0.4)
        check(live_count(pg, "Đã chép báo cáo.") == copied and not live_has(pg, lambda t: t.startswith("Không chép được"), 0.3), "a turned off Sao chép báo cáo copies nothing")
        check(pg.evaluate("document.body.innerText.split('Chưa lưu thay đổi.').length - 1") == 1, "the sentence Chưa lưu thay đổi. is on the page once, no second one was added for the report")
        axe_check(pg, "on /dung (changes not saved, report copy turned off)")
        anchor0.fill("Tủ lạnh")
        check(pg.is_hidden("#plan-dirty") and pg.is_enabled("#rep-copy") and pg.get_attribute("#rep-copy", "aria-describedby") is None,
              "when the typed text is the saved text again, Sao chép báo cáo works and the sentence goes")

        # ---------------------------------------------------------------- the server refuses the plan: its sentence shows
        box0 = rows.nth(4).locator("input").nth(0)
        box0.fill("")
        pg.click("#plan-save")
        check(wait_until(lambda: pg.is_visible("#plan-err"), 8) and pg.inner_text("#plan-err") == "Mỗi unit cần một mã, ví dụ M8-02.", f"a unit with no code: the sentence of the server {pg.inner_text('#plan-err')!r}")
        check(("PUT", "/api/my/plan", 422) in plan_calls, "the server answered 422")
        check(live_has(pg, lambda t: t == "Mỗi unit cần một mã, ví dụ M8-02.") and pg.evaluate("document.getElementById('live').classList.contains('is-bad')"), "the live region says it, in the warning style")
        check(pg.evaluate("document.activeElement.id") == "plan-save" and pg.is_visible("#plan-dirty"), "after a refusal the focus stays on Lưu kế hoạch and the changes are still there")
        axe_check(pg, "on /dung (the server refused the plan, sentence under the list)")
        page_texts.append(pg.inner_text("body"))
        box0.fill("m3-01")
        pg.click("#plan-save")
        check(wait_until(lambda: pg.inner_text("#plan-err") == "Mã unit m3-01 bị lặp.", 8), f"a repeated code: the sentence of the server {pg.inner_text('#plan-err')!r}")
        box0.fill("M3-05")
        saved_before = plan_calls.count(("PUT", "/api/my/plan", 200))
        pg.click("#plan-save")
        check(wait_until(lambda: pg.is_hidden("#plan-err") and pg.is_hidden("#plan-dirty") and plan_calls.count(("PUT", "/api/my/plan", 200)) == saved_before + 1, 8), "a good save clears the sentence of the refusal")
        pg.route(re.compile(r"/api/my/plan"), lambda r: r.fulfill(status=500, content_type="application/json", body="{}") if r.request.method == "PUT" else r.continue_())
        pg.fill("#p-module", "M3")
        rows.nth(4).locator("input").nth(1).fill("Giường to")
        pg.click("#plan-save")
        check(wait_until(lambda: pg.is_visible("#plan-err") and pg.inner_text("#plan-err") == "Chưa làm được. Thử lại.", 8), f"a server error on saving says what to do {pg.inner_text('#plan-err')!r}")
        axe_check(pg, "on /dung (saving failed)")
        pg.unroute(re.compile(r"/api/my/plan"))
        pg.click("#plan-save")
        check(wait_until(lambda: pg.is_hidden("#plan-err") and rows.nth(4).locator("input").nth(1).input_value() == "Giường to", 10),
              f"saving again works {pg.is_hidden('#plan-err')} {pg.inner_text('#plan-err')!r} {rows.nth(4).locator('input').nth(1).input_value()!r} {plan_calls[-3:]} {[b["units"][4] for b in put_bodies[-2:]]}")
        pg.close()

        # ---------------------------------------------------------------- unsaved typing survives the reading every few minutes
        pg = ctx_h.new_page(); watch(pg, "plan-keeps")
        pg.goto(BASE + "/dung"); pg.wait_for_function("document.querySelectorAll('#units > li').length === 5", timeout=15000)
        pg.click("#fold-plan > summary")
        pg.locator("#units > li").nth(0).locator("input").nth(1).fill("Tủ lạnh lớn")
        pg.evaluate("document.dispatchEvent(new Event('visibilitychange'))")        # reads the plan again
        pause(1.5)
        check(pg.locator("#units > li").nth(0).locator("input").nth(1).input_value() == "Tủ lạnh lớn" and pg.is_visible("#plan-dirty"), "a new reading of the plan does not overwrite what the person typed")
        check(row_needs(pg.locator("#units > li").nth(1)) == ["B2 (bỏ được)", "A1"] and row_states(pg.locator("#units > li").nth(1)) == ["Đã duyệt", "Chưa tạo"], "the other rows keep their needs and states")
        # typing again while the answer to Lưu kế hoạch is on its way: the answer must not wipe what was typed
        pg.evaluate("""() => { window.__f = window.fetch;
          window.fetch = (u, o) => { const p = window.__f(u, o); return /api\\/my\\/plan$/.test(String(u)) && o && o.method === "PUT" ? p.then(r => new Promise(res => setTimeout(() => res(r), 1500))) : p; }; }""")
        pg.click("#plan-save")
        pg.locator("#units > li").nth(0).locator("input").nth(1).fill("Tủ lạnh rất lớn")
        pause(2.6)
        check(pg.locator("#units > li").nth(0).locator("input").nth(1).input_value() == "Tủ lạnh rất lớn" and pg.is_visible("#plan-dirty"), "text typed while a save is on its way is kept, and the sentence Chưa lưu thay đổi. says it is not saved")
        pg.evaluate("() => { window.fetch = window.__f; }")
        pg.click("#plan-save")
        check(wait_until(lambda: pg.is_hidden("#plan-dirty") and c3.call("GET", "/api/my/plan")[1]["units"][0]["anchor"] == "Tủ lạnh rất lớn", 8), "the next save keeps it")
        pg.close()

        # ---------------------------------------------------------------- keyboard only through the plan
        kp = ctx_h.new_page(); watch(kp, "plan-keys")
        kp_calls = []
        kp.on("response", lambda r: kp_calls.append((r.request.method, r.status)) if "/api/my/plan" in r.url else None)
        kp.goto(BASE + "/dung"); kp.wait_for_function("document.querySelectorAll('#units > li').length === 5", timeout=15000)
        kp.focus("#fold-plan > summary")
        kp.keyboard.press("Enter")
        check(kp.evaluate("document.getElementById('fold-plan').open"), "Enter on the title of Kế hoạch hôm nay opens it")
        kp.keyboard.press("Tab")
        check(kp.evaluate("document.activeElement.id") == "p-module", "Tab goes from the title to the Module field")
        stops = 0
        while kp.evaluate("document.activeElement.id") != "unit-add" and stops < 60:
            kp.keyboard.press("Tab"); stops += 1
        check(kp.evaluate("document.activeElement.id") == "unit-add", f"Tab reaches Thêm unit through the rows ({stops} stops)")
        kp.keyboard.press("Enter")
        check(kp.evaluate("document.activeElement.tagName") == "INPUT" and kp.locator("#units > li").count() == 6 and kp.evaluate("document.activeElement.closest('li') === document.querySelector('#units > li:last-child')"),
              "Enter on Thêm unit puts the keyboard on the new row")
        kp.keyboard.type("M3-06"); kp.keyboard.press("Tab"); kp.keyboard.type("Bàn ăn"); kp.keyboard.press("Tab")
        check(kp.evaluate("document.activeElement.tagName") == "SELECT", "Tab goes from the large object to the shift")
        kp.keyboard.press("ArrowDown")
        check(kp.evaluate("document.activeElement.value") == "first", "an arrow key changes the shift")
        kp.keyboard.press("Tab")
        check(kp.evaluate("document.activeElement.dataset.key") == "unit-del" and kp.evaluate("document.activeElement.getAttribute('aria-label')") == "Xoá unit M3-06", "Tab reaches Xoá unit, named by its unit")
        kp.keyboard.press("Enter")
        check(kp.locator("#units > li").count() == 5 and kp.evaluate("document.activeElement.tagName") == "INPUT" and kp.evaluate("document.activeElement.closest('li') === document.querySelector('#units > li:last-child')"),
              "after Xoá unit the keyboard is on the row before")
        check(live_has(kp, lambda t: t == "Đã xoá unit M3-06."), "the live region says the unit was removed")
        stops = 0
        while kp.evaluate("document.activeElement.id") != "plan-save" and stops < 60:
            kp.keyboard.press("Tab"); stops += 1
        check(kp.evaluate("document.activeElement.id") == "plan-save", f"Tab reaches Lưu kế hoạch ({stops} stops)")
        kp.keyboard.press("Enter")
        check(wait_until(lambda: ("PUT", 200) in kp_calls, 8) and live_has(kp, lambda t: t == "Đã lưu kế hoạch.") and kp.evaluate("document.activeElement.id") == "plan-save", "Enter on Lưu kế hoạch saves, and the focus stays")
        kp.focus("#fold-report > summary"); kp.keyboard.press("Space")
        check(kp.evaluate("document.getElementById('fold-report').open"), "Space on the title of Báo cáo cuối ngày opens it")
        kp.keyboard.press("Tab")
        check(kp.evaluate("document.activeElement.id") == "rep-text", "Tab goes from the title to the report")
        kp.keyboard.press("Tab")
        check(kp.evaluate("document.activeElement.id") == "rep-copy", "Tab goes from the report to Sao chép báo cáo")
        kp.keyboard.press("Enter")
        check(live_has(kp, lambda t: t == "Đã chép báo cáo."), "Enter on Sao chép báo cáo copies")
        axe_check(kp, "on /dung (keyboard in the plan)")
        # reading the plan fails: one short sentence and a retry
        kp.route(re.compile(r"/api/my/plan"), lambda r: r.abort())
        kp.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        check(wait_until(lambda: kp.is_visible("#plan-banner"), 8) and kp.inner_text("#plan-msg") == "Chưa tải được kế hoạch." and kp.inner_text("#plan-retry") == "Thử lại",
              "when the plan cannot be read: one short sentence and Thử lại")
        check(live_has(kp, lambda t: t == "Chưa tải được kế hoạch."), "the live region says it once")
        check(kp.locator("#units > li").count() == 5, "the last plan stays on screen")
        axe_check(kp, "on /dung (the plan could not be read)")
        kp.unroute(re.compile(r"/api/my/plan"))
        kp.click("#plan-retry")
        check(wait_until(lambda: kp.is_hidden("#plan-banner"), 8), "Thử lại reads the plan again")
        kp.close()
        fp = ctx_h.new_page(); watch(fp, "plan-first-fails")
        fp.route(re.compile(r"/api/my/plan"), lambda r: r.abort())
        fp.goto(BASE + "/dung")
        fp.wait_for_selector("#plan-banner:not([hidden])")
        check(fp.is_visible("#f-go") and fp.is_hidden("#rep-empty") and fp.locator("input[name=kind]").count() == 3, "the first reading of the plan fails: the create form still works")
        fp.click("#fold-plan > summary"); fp.fill("#p-module", "M3"); fp.click("#unit-add")
        check(fp.eval_on_selector_all("#units > li select option", "els => els.map(e => e.textContent)") == SHIFT_TEXTS, "even then the rows offer the five shifts")
        axe_check(fp, "on /dung (first reading of the plan failed)")
        fp.close()

        # ---------------------------------------------------------------- 375 px, reduced motion, the plan polling is calm, nothing for the admin
        pp = ctx_h.new_page(); watch(pp, "plan-phone")
        pp.set_viewport_size({"width": 375, "height": 800})
        pp.goto(BASE + "/dung"); pp.wait_for_function("document.querySelectorAll('#units > li').length === 5", timeout=15000)
        top = pp.evaluate("document.getElementById('s1-h').getBoundingClientRect().top + window.scrollY")
        check(top < 800, f"at 375 px (800 px high) step 1 starts inside the first screen, under the closed panel ({top:.0f} px from the top)")
        check(overflow_ok(pp)[0] and not small_targets(pp) and not small_text(pp), f"at 375 px the closed panel: no sideways scroll, large controls {small_targets(pp)}")
        open_folds(pp)
        ok, w = overflow_ok(pp)
        check(ok, f"no horizontal scroll at 375 px with every section open {w}")
        check(not small_targets(pp) and not small_text(pp), f"at 375 px every section open: large controls, text 16 px or more {small_targets(pp)} {small_text(pp)}")
        check(wait_until(lambda: pp.evaluate("(() => { const t = document.getElementById('rep-text'); return t.scrollHeight <= t.clientHeight + 2; })()"), 3),      # the area grows when the section has finished opening: a moment after the click
              "at 375 px the report area shows the whole report")
        pp.fill("#f-code", "M3-02")
        check(wait_until(lambda: pp.is_visible("#recent"), 5) and overflow_ok(pp)[0] and not small_targets(pp), "at 375 px the block Gần đây fits")
        axe_check(pp, "on /dung at 375 px (every section open, units, report, block Gần đây)")
        page_texts.append(pp.inner_text("body"))
        pp.close()
        ctx_rm2 = new_ctx(viewport={"width": 1100, "height": 900}, reduced_motion="reduce", storage_state=state3)
        rp = ctx_rm2.new_page(); watch(rp, "plan-reduced")
        rp.goto(BASE + "/dung"); rp.wait_for_function("document.querySelectorAll('#units > li').length === 5", timeout=15000)
        open_folds(rp)
        check(rp.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches") and rp.evaluate("getComputedStyle(document.querySelector('#fold-plan > summary'), '::before').transitionDuration") in ("0s", ""),
              "reduced motion: nothing moves in the panel")
        axe_check(rp, "on /dung (reduced motion, panel open)")
        ctx_rm2.close()
        pg = ctx_h.new_page(); watch(pg, "plan-calm")
        gets = []
        pg.on("request", lambda r: gets.append(r.url) if r.method == "GET" and "/api/my/plan" in r.url else None)
        pg.goto(BASE + "/dung"); pg.wait_for_function("document.querySelectorAll('#units > li').length === 5", timeout=15000)
        pause(8)
        check(len(gets) <= 1, f"the plan is read once on loading and not again every few seconds ({len(gets)} readings in 8 seconds)")
        pg.close()
        ctx_h.close()

        # ---------------------------------------------------------------- the same box name on two days (with BOX_DONE_DAYS=2 yesterday's box is still alive, a unit needs a new B2 every day)
        code, out = admin.call("POST", "/api/admin/people", {"text": "6006, Mai, M6"})
        check(code == 200 and out["added"] == 1, f"the admin adds a fourth person {out}")
        c6 = Client(BASE)
        code, out = c6.call("POST", "/api/auth/login", {"id": "6006"})
        check(code == 200, "ID 6006 signs in with a plain HTTP client")
        code, out = c6.call("PUT", "/api/my/plan", {"module": "M6", "units": [{"code": "M6-01", "anchor": "Tủ lạnh", "shift": "full"}]})
        check(code == 200, f"ID 6006 saves a plan with one unit {code}")
        my6 = lambda: c6.call("GET", "/api/my/summary?rev=-1")[1]
        box6 = lambda i: next((b for b in my6()["boxes"] if b["id"] == i), None)
        code, out = c6.call("POST", "/api/my/boxes", {"module": "M6", "label": "Box B2", "theme": "bếp", "code": "M6-01", "anchor": "Tủ lạnh"})
        check(code == 201, f"the first Box B2 of the unit is made {code} {out}")
        old_id = out["id"]
        check(wait_until(lambda: box6(old_id)["status"] == "draft" and len(box6(old_id)["items"]) == stage["B2"], 30), "that box is picked")
        code, out = c6.call("POST", f"/api/my/boxes/{old_id}/approve")
        check(code == 200, f"and approved {code}")
        old_items = [i["id"] for i in box6(old_id)["items"]]
        made_at = db_get("SELECT created_at FROM boxes WHERE id=?", (old_id,))[0][0]
        yesterday_6 = datetime.datetime.fromtimestamp(made_at - 86400, VN).strftime("%Y-%m-%d")
        db_run("UPDATE boxes SET day=?, created_at=created_at-86400 WHERE id=?", (yesterday_6, old_id))          # it was made yesterday
        s6 = my6()
        check(s6["today"] == vn_at(0)[1] and [b["day"] for b in s6["boxes"]] == [yesterday_6] and yesterday_6 < s6["today"],
              f"the server says which day it is and which day the box is from {s6['today']} {[b['day'] for b in s6['boxes']]}")

        ctx6 = new_ctx(viewport={"width": 1100, "height": 900})
        p6 = ctx6.new_page(); watch(p6, "6006")
        p6.goto(BASE + "/login"); p6.fill("#login-id", "6006"); p6.press("#login-id", "Enter"); p6.wait_for_url(BASE + "/dung")
        p6.wait_for_selector("article.d-card")
        cards6 = p6.locator("article.d-card")
        # only yesterday's box: it shows its day, and the person is at step 1 again
        check(cards6.count() == 1 and cards6.first.locator(".d-day").inner_text() == "Hôm qua" and cards6.first.locator(".d-chip").inner_text() == "Đã duyệt",
              "a box made yesterday shows the tag Hôm qua next to its title in step 2")
        check(cards6.first.locator("h3").evaluate("e => e.textContent") == "M6 Box B2 Hôm qua" and p6.get_by_role("article", name=re.compile(r"^M6 Box B2, hôm qua$")).count() == 1,
              "its name for a screen reader is M6 Box B2, hôm qua")
        check(steps(p6) == ["Đang ở đây", "Chưa tới", "Chưa tới"] and "Tạo hộp" in p6.locator(".d-steps a[aria-current=step]").inner_text(),
              f"with only yesterday's box the progress strip is at step 1 {steps(p6)}")
        check(p6.locator(".d-take").count() == 0 and p6.inner_text("#s3-lead") == "Chưa có hộp nào chọn xong." and p6.is_hidden("#s2-lead"),
              "step 3 lists nothing of yesterday, and says so in its one sentence")
        paper = p6.evaluate("fetch('/my/print').then(r => r.text())")
        check(not any(i in paper for i in old_items), "the printed paper has nothing of yesterday's box")
        check(not small_text(p6) and not small_targets(p6) and overflow_ok(p6)[0], f"yesterday's box: text 16 px or more, large controls, no sideways scroll {small_text(p6)} {small_targets(p6)}")
        page_texts.append(p6.inner_text("body"))
        axe_check(p6, "on /dung (only yesterday's box, tagged Hôm qua, step 1)")
        p6.wait_for_function("document.querySelectorAll('#units > li').length === 1", timeout=15000)
        p6.click("#fold-plan > summary")
        unit6 = rows_of(p6).nth(0)
        check(row_states(unit6) == ["Chưa tạo"] * 3 and unit6.locator(".d-needs button").all_inner_texts() == ["Tạo B2", "Tạo B3", "Tạo A1"],
              f"the unit does not count yesterday's box: Tạo B2 is still there {row_states(unit6)}")
        # the tag is in the words said aloud too
        cards6.first.locator("[data-key=reopen]").click()
        check(live_has(p6, lambda t: t == "Đã bỏ duyệt hộp M6 Box B2, hôm qua."), f"the live region says the day {p6.evaluate('window.__live')[-2:]}")
        p6.wait_for_selector("article.d-card.is-draft")
        cards6.first.locator("[data-key=approve]").click()
        check(cards6.first.locator(".d-confirm p").inner_text() == "Duyệt hộp M6 Box B2, hôm qua?", f"the question says the day {cards6.first.locator('.d-confirm').inner_text()!r}")
        axe_check(p6, "on /dung (asking to approve yesterday's box)")
        cards6.first.locator("[data-key=approve-yes]").click()
        check(live_has(p6, lambda t: t == "Đã duyệt hộp M6 Box B2, hôm qua.") and not any("bước 3" in t for t in p6.evaluate("window.__live")),
              "approving yesterday's box says the day, and does not send the person to step 3")
        p6.wait_for_selector("article.d-card.is-approved")
        # the same name again today
        p6.fill("#f-code", "M6-01"); p6.fill("#f-anchor", "Tủ lạnh"); p6.click("label:has-text('Box B2')")
        check(p6.input_value("#f-module") == "M6", "the form has the module")
        with p6.expect_response(lambda r: r.url.endswith("/api/my/boxes") and r.request.method == "POST") as again:
            p6.click("#f-go")
        check(again.value.status == 201, f"the same module and name is accepted today: HTTP {again.value.status}")
        new_id = again.value.json()["id"]
        p6.wait_for_selector(f"article.d-card[data-box='{new_id}'].is-draft", timeout=30000)
        check(live_has(p6, lambda t: t == "Đã tạo hộp M6 Box B2. Đang chọn đồ."), "a box of today is said without a day")
        order6 = [c.get_attribute("data-box") for c in cards6.all()]
        check(order6 == [str(new_id), str(old_id)] and cards6.nth(0).locator(".d-day").count() == 0 and cards6.nth(1).locator(".d-day").inner_text() == "Hôm qua"
              and cards6.nth(0).locator("h3").inner_text() == "M6 Box B2",
              f"step 2 has two cards, today's first and without a tag, yesterday's second with Hôm qua {order6}")
        takes6 = p6.locator(".d-take")
        check(takes6.count() == 1 and takes6.get_attribute("data-box") == str(new_id) and takes6.locator(".d-day").count() == 0
              and takes6.locator("a[data-key=print]").get_attribute("href") == f"/my/print?ids={new_id}", "step 3 shows today's box only")
        check(steps(p6) == ["Xong", "Đang ở đây", "Có hộp để lấy"], f"the progress strip follows today's box {steps(p6)}")
        check(wait_until(lambda: row_states(rows_of(p6).nth(0)) == ["Chờ duyệt", "Chưa tạo", "Chưa tạo"], 15), f"the unit counts today's box only {row_states(rows_of(p6).nth(0))}")
        new_items = [i["id"] for i in box6(new_id)["items"]]
        paper = p6.evaluate("fetch('/my/print').then(r => r.text())")
        check(new_items[0] in paper and not any(i in paper for i in old_items), "the printed paper has today's box and not yesterday's")
        check(not small_text(p6) and not small_targets(p6) and overflow_ok(p6)[0], f"two boxes of two days: text 16 px or more, large controls, no sideways scroll {small_text(p6)} {small_targets(p6)}")
        page_texts.append(p6.inner_text("body"))
        axe_check(p6, "on /dung (the same name on two days: today's draft first, yesterday's tagged Hôm qua)")
        p6p = ctx6.new_page(); watch(p6p, "6006-phone")
        p6p.set_viewport_size({"width": 375, "height": 800})
        p6p.goto(BASE + "/dung"); p6p.wait_for_selector(f"article.d-card[data-box='{new_id}']")
        check(overflow_ok(p6p)[0] and not small_targets(p6p) and not small_text(p6p) and p6p.locator(".d-day").count() == 1,
              f"two boxes of two days at 375 px: no horizontal scroll, large controls {overflow_ok(p6p)[1]} {small_targets(p6p)}")
        axe_check(p6p, "on /dung at 375 px (two boxes of two days)")
        # a long box on a phone: tight rows (name and button on one line, the three facts on one wrapping line), and a link at the top, only for a draft with more than 6 món
        draft6 = p6p.locator(f"article.d-card[data-box='{new_id}']")
        old6 = p6p.locator(f"article.d-card[data-box='{old_id}']")
        measure = lambda loc: loc.evaluate("""c => { const items = [...c.querySelectorAll('.d-item')], hs = items.map(i => i.getBoundingClientRect().height);
          return {card: Math.round(c.getBoundingClientRect().height), n: items.length, avg: Math.round(hs.reduce((a, b) => a + b, 0) / (hs.length || 1)), max: Math.round(Math.max(...hs)),
                  swap: Math.round(Math.min(...items.map(i => i.querySelector('.d-btn').getBoundingClientRect().height))),
                  wide: items.every(i => i.querySelector('.d-facts').getBoundingClientRect().width >= i.getBoundingClientRect().width - 24)}; }""")
        sz = measure(draft6)
        print("     (measured at 375 px, 21 món)", sz)
        check(sz["n"] == stage["B2"] and sz["card"] <= 2700 and sz["avg"] <= 105, f"375 px: a card with {stage['B2']} món is at most 2700 px tall and a row 105 px on average {sz}")
        check(sz["wide"] and sz["swap"] >= 44, f"375 px: the ID, the quantity and the shelf slot share one line the width of the row, and Đổi món stays at least 44 px high {sz}")
        check(measure(old6)["card"] <= 2700, f"375 px: an approved long box is as compact {measure(old6)}")
        check(draft6.locator(".d-jump").count() == 1 and draft6.locator(".d-jump").is_visible() and old6.locator(".d-jump").count() == 0 and p6p.locator(".d-jump").count() == 1,
              "375 px: the link Xuống Duyệt hộp is on the draft with 21 món and not on the approved one")
        draft6.locator("[data-key=head]").focus(); p6p.keyboard.press("Tab")
        check(focus_key(p6p) == "jump", f"375 px: the link is the first stop after the heading {focus_key(p6p)}")
        p6p.keyboard.press("Enter")
        in_view6 = "(() => { const r = document.activeElement.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; })()"
        check(wait_until(lambda: focus_key(p6p) == "approve" and p6p.evaluate(in_view6) and p6p.evaluate("document.activeElement.closest('article').dataset.box") == str(new_id), 3) and p6p.evaluate("scrollY") > 1500,
              "375 px: it moves the focus to the Duyệt hộp of that box, far down the page, and scrolls it into view")
        # exactly 6 món: no link; 7 món: the link (the answer is cut on its way). With an empty place Duyệt hộp is off: the link goes to Điền ô trống
        cut = {"n": None}
        def cut_route(route):
            data = route.fetch(url=re.sub(r"rev=-?\d+", "rev=-1", route.request.url)).json()
            if cut["n"]:
                for b in data["boxes"]:
                    if b["status"] == "draft": b["items"] = b["items"][:cut["n"]]
            route.fulfill(status=200, content_type="application/json", body=json.dumps(data))
        cut_pat = re.compile(r"/api/my/summary")
        p6p.route(cut_pat, cut_route)
        for n_cut, want_link in ((6, 0), (7, 1)):
            cut["n"] = n_cut
            p6p.reload()
            check(wait_until(lambda: draft6.locator(".d-item").count() == n_cut, 10) and draft6.locator(".d-jump").count() == want_link,
                  f"a draft with {n_cut} món has {'no link' if not want_link else 'the link'} Xuống Duyệt hộp")
        draft6.locator(".d-jump").click()
        check(wait_until(lambda: focus_key(p6p) == "fill", 3), f"with an empty place Xuống Duyệt hộp goes to Điền ô trống, the next thing to press {focus_key(p6p)}")
        p6p.unroute(cut_pat)
        p6p.set_viewport_size({"width": 320, "height": 700})
        p6p.reload(); p6p.wait_for_selector(f"article.d-card[data-box='{new_id}'] .d-item")
        sz320 = measure(draft6)
        print("     (measured at 320 px, 21 món)", sz320)
        check(sz320["card"] <= 3300 and sz320["wide"] and overflow_ok(p6p)[0] and not small_targets(p6p) and not small_text(p6p),
              f"320 px: the long card is at most 3300 px tall, no sideways scroll, large controls {sz320} {overflow_ok(p6p)[1]} {small_targets(p6p)}")
        axe_check(p6p, "on /dung at 320 px (long box)")
        p6p.close()
        cards6.nth(0).locator("[data-key=approve]").click(); cards6.nth(0).locator("[data-key=approve-yes]").click()
        check(live_has(p6, lambda t: t == "Đã duyệt hộp M6 Box B2. Bạn lấy đồ ở bước 3."), "today's box is approved with the sentence that points to step 3")
        check(wait_until(lambda: steps(p6) == ["Xong", "Xong", "Đang ở đây"], 8), f"today's approved box moves the strip to step 3 {steps(p6)}")
        # the page left open over midnight: the clock moves a day, every box is a day older, and the person is at step 1 again
        p6.evaluate("window.__real = Date.now; Date.now = () => window.__real() + 24 * 3600 * 1000")
        check(wait_until(lambda: cards6.nth(0).locator(".d-day").count() == 1 and cards6.nth(0).locator(".d-day").inner_text() == "Hôm qua"
                         and cards6.nth(1).locator(".d-day").inner_text() == "Hôm kia", 15), "a day later with no reload: yesterday's box is Hôm qua and the older one Hôm kia")
        check(p6.locator(".d-take").count() == 0 and steps(p6) == ["Đang ở đây", "Chưa tới", "Chưa tới"], f"a day later: step 3 is empty and the strip is at step 1 {steps(p6)}")
        axe_check(p6, "on /dung (a day later, with no reload)")
        p6.evaluate("Date.now = window.__real")
        check(wait_until(lambda: cards6.nth(0).locator(".d-day").count() == 0 and p6.locator(".d-take").count() == 1, 15), "with the clock back, today's box is without a tag again and in step 3")
        # older days are named by words, and a box with no day counts as today (the answer of the server is rewritten on its way)
        day_mode = {"mode": None}

        def day_route(route):
            if not day_mode["mode"]:
                route.continue_(); return
            data = route.fetch(url=re.sub(r"rev=-?\d+", "rev=-1", route.request.url)).json()
            old = next(b for b in data["boxes"] if b["id"] == old_id)
            if day_mode["mode"] == "older":
                old["day"] = vn_at(2)[1]
                data["boxes"].append({**old, "id": old_id + 1000, "day": vn_at(3)[1]})          # the biggest id and the oldest day: it must still come last
            elif day_mode["mode"] == "noday":
                for b in data["boxes"]:
                    if b["id"] == new_id: b["day"] = ""
            route.fulfill(status=200, content_type="application/json", body=json.dumps(data))
        p6.route(re.compile(r"/api/my/summary"), day_route)
        day_mode["mode"] = "older"
        p6.reload(); p6.wait_for_selector(f"article.d-card[data-box='{old_id + 1000}']")
        tags6 = [c.locator(".d-day").inner_text() if c.locator(".d-day").count() else "" for c in cards6.all()]
        check([c.get_attribute("data-box") for c in cards6.all()] == [str(new_id), str(old_id), str(old_id + 1000)] and tags6 == ["", "Hôm kia", "3 ngày trước"],
              f"older boxes are named Hôm kia and 3 ngày trước, the newest day first, whatever their numbers are {tags6}")
        check(p6.get_by_role("article", name=re.compile(r"^M6 Box B2, 3 ngày trước$")).count() == 1 and p6.locator(".d-take").count() == 1, "the name said aloud has the day, step 3 still has today's box only")
        check(not small_text(p6) and not small_targets(p6) and overflow_ok(p6)[0], f"three boxes of three days: text 16 px or more, large controls, no sideways scroll {small_text(p6)} {small_targets(p6)}")
        axe_check(p6, "on /dung (three boxes of three days)")
        day_mode["mode"] = "noday"
        p6.reload(); p6.wait_for_selector(f"article.d-card[data-box='{new_id}']")
        check(cards6.nth(0).locator(".d-day").count() == 0 and cards6.nth(1).locator(".d-day").inner_text() == "Hôm qua" and p6.locator(".d-take").count() == 1,
              "a box with no day counts as today: no tag, in step 3")
        p6.unroute(re.compile(r"/api/my/summary"))
        p6.close(); ctx6.close()

        # ---------------------------------------------------------------- every request the page made
        outside =[u for m, u in reqs if urllib.parse.urlsplit(u).netloc != f"127.0.0.1:{PORT}" and not u.startswith("data:")]
        check(not outside, f"every request went to 127.0.0.1 {outside[:3]}")
        paths = sorted({(m, urllib.parse.urlsplit(u).path) for m, u in reqs if u.startswith(BASE)})
        stray = [(m, p) for m, p in paths if not p.startswith(ALLOWED_PREFIXES) and p != "/"]
        stray_admin = [(m, p) for m, p in stray]
        # the admin's own visit to "/" is the only other page: it is not part of the page under test
        check(not stray_admin, f"the pages call only /api/my/*, /api/auth/*, /api/config, /my/print and static files {stray_admin}")
        wrote = sorted({(m, re.sub(r"/\d+", "/N", urllib.parse.urlsplit(u).path)) for m, u in reqs if m != "GET" and u.startswith(BASE)})
        check(all(p.startswith(("/api/my/", "/api/auth/")) for m, p in wrote), f"every write is under /api/my/ or /api/auth/ {wrote}")
        mine_writes = {"/api/my/boxes", "/api/my/boxes/N", "/api/my/boxes/N/approve", "/api/my/boxes/N/reopen", "/api/my/boxes/N/fill",
                       "/api/my/boxes/N/regenerate", "/api/my/boxes/N/items/N/replace", "/api/my/plan", "/api/auth/login", "/api/auth/logout"}
        check({p for m, p in wrote} <= mine_writes and {("POST", "/api/my/boxes/N/approve"), ("POST", "/api/my/boxes/N/reopen"), ("POST", "/api/my/boxes/N/fill"), ("PUT", "/api/my/plan")} <= set(wrote),
              f"the writes are the person's own: create, change, approve, reopen, fill, delete, save the plan, sign in and out {wrote}")
        check([m for m, p in wrote if p == "/api/my/plan"] == ["PUT"] and {("GET", "/api/my/plan"), ("GET", "/api/my/recent")} <= set(paths),
              "the plan is only read (GET) and written (PUT), the recent scenes are only read")
        check(not [p for m, p in paths if p.startswith(("/api/boxes", "/api/state", "/api/automations", "/api/backup", "/api/import", "/api/sm", "/api/admin", "/api/catalog",
                                                         "/api/used", "/api/objects", "/api/batches", "/api/queue", "/api/usage", "/api/sheet", "/api/taskboxes", "/api/export"))],
              "the page never calls an admin route (no automation switches, backup, import, people list, sheet box or catalog tool)")
        for t in page_texts:
            if "!" in t or "—" in t or FILLER.search(t):
                check(False, f"rendered text has an exclamation mark, an em dash or a filler word: {t[:80]!r}")
                break
        else:
            check(True, f"the rendered text of {len(page_texts)} states has no exclamation mark, em dash or filler word")
        bad = [(t[:60], jargon_hits(t)) for t in page_texts if jargon_hits(t)]
        check(not bad, f"the rendered text of {len(page_texts)} states has no error number, no machine word, no Plain text, no Object ID {bad[:2]}")
        check(not errs, f"no script error or console error {errs[:3]}")
        ctx.close()

        # ---------------------------------------------------------------- login switched off: the page works for everyone
        env2 = clean_env(DB_PATH=os.path.join(tmp2, "open.db"), CATALOG_PATH=os.path.join(ROOT, "data", "catalog.csv"), SYNC_TOKEN="t", LLM_MODE="mock")
        port2 = free_port()
        srv2, srv2_log = start_server(env2, port2, tmp2)
        base2 = f"http://127.0.0.1:{port2}"
        PUMP[0] = None            # the first page is closed by now: waits are plain sleeps, every call on a page handles the events that came in meanwhile
        # ---------------------------------------------------------------- the last Module typed is remembered (here nobody has a module of their own and nothing is planned yet)
        mod_errs = []
        def mwatch(p):
            p.on("pageerror", lambda e: mod_errs.append(f"pageerror: {e}"))
            p.on("console", lambda m: mod_errs.append(f"console: {m.text}") if m.type == "error" and "Failed to load resource" not in m.text else None)
        plan_loaded = lambda p: p.wait_for_function("document.querySelector('#rep-empty').textContent === 'Chưa có unit nào.'", timeout=15000)
        keys = lambda p: p.evaluate("Object.keys(localStorage)")
        ctx_m = new_ctx(viewport={"width": 1100, "height": 900})
        m1 = ctx_m.new_page(); mwatch(m1)
        m1.goto(base2 + "/dung"); plan_loaded(m1)
        check(m1.input_value("#f-module") == "" and keys(m1) == [], f"module memory: nothing remembered yet, the field is empty and the page has stored nothing {m1.input_value('#f-module')!r} {keys(m1)}")
        m1.fill("#f-module", "m5"); m1.press("#f-module", "Tab")
        check(m1.evaluate("localStorage.getItem('bb_last_module')") == "M5" and keys(m1) == ["bb_last_module"],
              f"module memory: the Module typed is kept as M5 under bb_last_module, and nothing else is kept {keys(m1)}")
        m2 = ctx_m.new_page(); mwatch(m2)
        m2.goto(base2 + "/dung"); plan_loaded(m2)
        check(m2.input_value("#f-module") == "M5" and keys(m2) == ["bb_last_module"], f"module memory: the next visit has M5 in the Module field {m2.input_value('#f-module')!r}")
        # what the person types is never overwritten: the answer about the plan comes late
        held = []
        m3 = ctx_m.new_page(); mwatch(m3)
        plan_pat = re.compile(r"/api/my/plan$")
        m3.route(plan_pat, lambda r: held.append(r))
        m3.goto(base2 + "/dung")
        m3.wait_for_function("document.querySelector('#s2-lead').textContent.includes('Chưa có hộp nào')")
        check(len(held) == 1 and m3.input_value("#f-module") == "", "module memory: until the plan has answered the field stays empty (no M5 that then changes)")
        m3.fill("#f-module", "k9")
        held[0].continue_()
        m3.unroute(plan_pat)
        plan_loaded(m3); pause(0.5)
        check(m3.input_value("#f-module") == "k9", f"module memory: what the person typed (k9) is not replaced by the remembered M5 {m3.input_value('#f-module')!r}")
        m3.evaluate("document.dispatchEvent(new Event('visibilitychange'))"); pause(1.0)
        check(m3.input_value("#f-module") == "k9" and keys(m3) == ["bb_last_module"] and m3.evaluate("localStorage.getItem('bb_last_module')") == "M5",
              "module memory: a later poll keeps the typed text too, and typing without leaving the field keeps nothing new")
        m3.fill("#f-module", "")
        m3.evaluate("document.dispatchEvent(new Event('visibilitychange'))"); pause(1.0)
        check(m3.input_value("#f-module") == "", "module memory: a field the person emptied stays empty")
        check(not mod_errs, f"module memory: no script error {mod_errs[:3]}")
        ctx_m.close()
        # storage blocked: the page works the same, nothing is read or kept, no error (a value is there in the browser, but cannot be read)
        BLOCK_PROTO = "const e = () => { throw new DOMException('blocked', 'SecurityError'); }; Storage.prototype.getItem = e; Storage.prototype.setItem = e;"
        BLOCK_WINDOW = "Object.defineProperty(window, 'localStorage', {configurable: true, get() { throw new DOMException('blocked', 'SecurityError'); }});"
        state_m = {"cookies": [], "origins": [{"origin": base2, "localStorage": [{"name": "bb_last_module", "value": "M5"}]}]}
        for how, script in (("getItem and setItem throw", BLOCK_PROTO), ("localStorage itself throws", BLOCK_WINDOW)):
            ctx_b = new_ctx(viewport={"width": 1100, "height": 900})
            ctx_b.add_init_script("try { localStorage.setItem('bb_last_module', 'M5'); } catch (e) {}")      # a value is there, then the storage is blocked
            ctx_b.add_init_script(script)
            b1 = ctx_b.new_page(); mwatch(b1)
            b1.goto(base2 + "/dung"); plan_loaded(b1)
            check(b1.input_value("#f-module") == "" and b1.is_visible("#f-go"), f"module memory, storage blocked ({how}): the page opens and the field stays empty")
            b1.fill("#f-module", "k9"); b1.press("#f-module", "Tab")
            b1.route(re.compile(r"/api/my/boxes$"), lambda r: r.fulfill(status=500, content_type="application/json", body="{}") if r.request.method == "POST" else r.continue_())
            b1.click("label:has-text('Box B2')"); b1.click("#f-go")
            b1.wait_for_selector("#err-form:not([hidden])")
            check(b1.inner_text("#err-form") == "Chưa làm được. Thử lại." and b1.input_value("#f-module") == "K9" and b1.is_enabled("#f-go"),
                  f"module memory, storage blocked ({how}): typing, leaving the field and pressing Tạo hộp work the same {b1.inner_text('#err-form')!r}")
            check(not mod_errs, f"module memory, storage blocked ({how}): no script error {mod_errs[:3]}")
            ctx_b.close()
        ctx_o = new_ctx(viewport={"width": 1100, "height": 900})
        po = ctx_o.new_page(); watch(po, "open")
        po.goto(base2 + "/dung")
        po.wait_for_selector("#open-note:not([hidden])")
        check(po.inner_text("#open-note") == "Bạn đang xem hộp của mọi người.", "login off: one calm sentence says every box is shown")
        check(not po.is_visible("#who-box") and not po.is_visible("#logout"), "login off: no sign-in name and no Thoát")
        axe_check(po, "on /dung (login off, empty)")
        po.fill("#f-module", "M9"); po.check("input[value='Box B2']"); po.click("#f-go")
        po.wait_for_selector("article.d-card.is-draft", timeout=30000)
        check(po.locator("article.d-card .d-item").count() == stage["B2"], f"login off: a Box B2 with {stage['B2']} objects can be made")
        axe_check(po, "on /dung (login off, draft)")
        po.click("#fold-plan > summary")
        po.fill("#p-module", "m7")
        po.click("#unit-add"); po.keyboard.type("M9-01"); po.keyboard.press("Tab"); po.keyboard.type("Tủ lạnh")
        po.click("#plan-save")
        po.wait_for_selector("#units > li .d-need-k", timeout=10000)
        check(po.input_value("#p-module") == "M7" and row_needs(rows_of(po).nth(0)) == ["B2", "B3", "A1"], "login off: the plan can be saved, the module is tidied to capitals, the unit shows its needs")
        po.reload()
        po.wait_for_function("document.querySelectorAll('#units > li').length === 1", timeout=15000)
        check(po.input_value("#f-module") == "M7", f"login off: the create form takes the module of the plan when it is empty {po.input_value('#f-module')!r}")
        po.click("#fold-plan > summary")
        rows_of(po).nth(0).locator("button", has_text="Tạo B3").click()
        check(po.input_value("#f-module") == "M7" and po.input_value("#f-code") == "M9-01" and po.input_value("#f-anchor") == "Tủ lạnh"
              and po.evaluate("document.querySelector('input[name=kind]:checked').value") == "Box B3" and po.evaluate("document.activeElement.id") == "f-theme",
              "login off: Tạo B3 fills the form and focuses the theme")
        axe_check(po, "on /dung (login off, plan with a unit)")
        # a plan with a module is saved now: it wins over the module remembered in the browser (M5)
        ctx_m2 = new_ctx(viewport={"width": 1100, "height": 900}, storage_state=state_m)
        m4 = ctx_m2.new_page(); mwatch(m4)
        m4.goto(base2 + "/dung"); m4.wait_for_function("document.querySelectorAll('#units > li').length === 1", timeout=15000)
        check(m4.input_value("#f-module") == "M7" and m4.evaluate("localStorage.getItem('bb_last_module')") == "M5", f"module memory: the module of the plan (M7) wins over the remembered M5 {m4.input_value('#f-module')!r}")
        check(not mod_errs, f"module memory: no script error {mod_errs[:3]}")
        ctx_m2.close()
        lo = ctx_o.new_page(); watch(lo, "login-off")
        lo.goto(base2 + "/login")
        lo.wait_for_selector("#login-off:not([hidden])")
        check(lo.is_hidden("#login-form") and lo.inner_text("#login-off") == "Chọn trang admin hoặc trang làm hộp.", f"login off: the login page says where to go {lo.inner_text('#login-off')!r}")
        login_look(lo, "(login off)")
        link_heights = lo.evaluate("[...document.querySelectorAll('#login-off a')].map(a => Math.round(a.getBoundingClientRect().height))")
        check(len(link_heights) == 2 and min(link_heights) >= 44, f"login off: the two links are at least 44 px high {link_heights}")
        axe_check(lo, "on /login (login off)")
        lo.set_viewport_size({"width": 375, "height": 800})
        login_look(lo, "(login off, 375 px)")
        axe_check(lo, "on /login at 375 px (login off)")
        lo.close()
        ctx_o.close()

        # ---------------------------------------------------------------- login with a shared password: ADMIN_IDS=1001, USER_IDS=4001,4002, USER_PASSWORD (made up, never printed)
        PW, BAD_PW = "test-pass", "wrong-pass"
        REFUSAL_PW = "Không vào được. Kiểm tra lại ID và mật khẩu, hoặc nhờ admin thêm ID của bạn."
        env3 = clean_env(DB_PATH=os.path.join(tmp3, "pw.db"), CATALOG_PATH=os.path.join(ROOT, "data", "catalog.csv"), SYNC_TOKEN="t",
                         ADMIN_IDS="1001", USER_IDS="4001,4002", USER_PASSWORD=PW, LLM_MODE="mock")
        port3 = free_port()
        srv3, srv3_log = start_server(env3, port3, tmp3)
        base3 = f"http://127.0.0.1:{port3}"
        code, st = Client(base3).call("GET", "/api/auth/status")
        check(code == 200 and st.get("enforced") is True and st.get("user_password") is True and PW not in json.dumps(st), "password server: the status says a password is asked, and never shows it")
        ctx_p = new_ctx(viewport={"width": 1000, "height": 900})
        pp = ctx_p.new_page(); watch(pp, "pw-login")

        def pw_cookies():
            return [c["name"] for c in ctx_p.cookies() if c["name"] in ("sid", "__Host-sid")]

        def pw_submit(pid, password, key_in="#login-pw"):
            """Types the ID and the password, presses Enter, returns the answer of the server once the page has shown it."""
            pp.fill("#login-id", pid); pp.fill("#login-pw", password)
            with pp.expect_response(lambda r: r.url.endswith("/api/auth/login")) as got:
                pp.press(key_in, "Enter")
            return got.value

        def wait_refusal():
            pp.wait_for_function("(() => { const e = document.getElementById('login-err'); return !e.hidden && e.textContent !== ''; })()", timeout=10000)

        pp.goto(base3 + "/login")
        pp.wait_for_selector("#login-pw-field:not([hidden])")
        tops = pp.evaluate("[document.getElementById('login-id'), document.getElementById('login-pw'), document.querySelector('#login-admin summary')].map(e => e.getBoundingClientRect().top)")
        check(pp.inner_text("label[for=login-pw]") == "Mật khẩu" and pp.get_attribute("#login-pw", "type") == "password" and pp.get_attribute("#login-pw", "autocomplete") == "current-password"
              and pp.get_attribute("#login-pw", "maxlength") == "300" and pp.get_attribute("#login-pw", "required") is None and pp.input_value("#login-pw") == "",
              "password server: the field Mật khẩu is a password box with the right hints, not required in the page")
        check(tops[0] < tops[1] < tops[2], f"password server: Mật khẩu sits under ID công ty and above Tôi là admin {tops}")
        pw_text = pp.inner_text("body")
        check("Chưa có ID? Nhờ admin thêm ID của bạn." in pw_text and "Đừng đưa ID cho người ngoài công ty." in pw_text and not jargon_hits(pw_text)
              and pp.locator("#login-admin summary").inner_text() == "Tôi là admin" and not pp.is_visible("#login-code"),
              f"password server: the same two sentences as before, the admin code still inside Tôi là admin {pw_text!r}")
        login_look(pp, "(with Mật khẩu)")
        axe_check(pp, "on /login (with Mật khẩu)")
        pp.click("#login-admin summary")
        check(pp.is_visible("#login-code") and pp.is_visible("#login-pw"), "password server: Tôi là admin opens the admin code and the password stays")
        login_look(pp, "(with Mật khẩu, admin code open)")
        axe_check(pp, "on /login (with Mật khẩu, admin code open)")
        pp.click("#login-admin summary")

        pq = ctx_p.new_page(); watch(pq, "pw-phone")
        pq.set_viewport_size({"width": 375, "height": 800})
        pq.goto(base3 + "/login"); pq.wait_for_selector("#login-pw-field:not([hidden])")
        login_look(pq, "(with Mật khẩu, 375 px)")
        axe_check(pq, "on /login at 375 px (with Mật khẩu)")
        pq.click("#login-admin summary")
        login_look(pq, "(with Mật khẩu, 375 px, admin code open)")
        axe_check(pq, "on /login at 375 px (with Mật khẩu, admin code open)")
        pq.close()

        # a wrong password: the sentence of the server, the ID stays, the password goes
        got = pw_submit("4001", BAD_PW)
        wait_refusal()
        sent = got.request.post_data_json
        check(got.status == 401 and got.json().get("detail") == REFUSAL_PW and pp.inner_text("#login-err") == REFUSAL_PW, f"password server: a wrong password shows the sentence of the server {pp.inner_text('#login-err')!r}")
        check(pp.input_value("#login-id") == "4001" and pp.input_value("#login-pw") == "" and pp.input_value("#login-code") == "" and urllib.parse.urlsplit(pp.url).path == "/login",
              "password server: after a wrong password the ID stays and the password is empty")
        check(sorted(sent) == ["code", "id", "password"] and sent["id"] == "4001" and sent["password"] == BAD_PW, "password server: the sign-in sends the ID, the code and the password")
        check(pp.evaluate("parseFloat(getComputedStyle(document.getElementById('login-err')).fontSize)") >= 16, "password server: the sentence of the refusal is 16 px or more")
        login_look(pp, "(with Mật khẩu, refusal)")
        axe_check(pp, "on /login (with Mật khẩu, refusal)")
        # no password at all, and an ID that is not listed with the right password: the same sentence
        got = pw_submit("4001", "")
        wait_refusal()
        check(got.status == 401 and pp.inner_text("#login-err") == REFUSAL_PW, "password server: a listed ID with no password is refused with the same sentence")
        got = pw_submit("4999", PW)
        wait_refusal()
        check(got.status == 401 and got.json().get("detail") == REFUSAL_PW and pp.inner_text("#login-err") == REFUSAL_PW and pp.input_value("#login-pw") == "",
              "password server: an ID that is not listed is refused with the same sentence, even with the right password")
        check(pw_cookies() == [], "password server: a refusal sets no cookie")
        # the right password
        got = pw_submit("4001", PW)
        pp.wait_for_url(base3 + "/dung")
        pp.wait_for_function("document.getElementById('who').textContent === '4001'", timeout=15000)
        check(got.status == 200 and pp.url == base3 + "/dung" and pp.evaluate("fetch('/api/auth/status').then(r => r.json()).then(j => [j.person.id, j.person.admin])") == ["4001", False],
              "password server: ID 4001 with the right password lands on /dung as an ordinary person")
        check(len(pw_cookies()) == 1 and not pp.is_visible("#admin-link"), "password server: a cookie is set, and an ordinary person has no link to the admin page")
        pp.click("#logout")
        pp.wait_for_url(base3 + "/login")
        check(pw_cookies() == [], "password server: after Thoát the cookie is gone")
        pp.goto(base3 + "/dung")
        check(urllib.parse.urlsplit(pp.url).path == "/login" and "next=/dung" in pp.url, "password server: after Thoát, /dung goes back to /login")
        pp.wait_for_selector("#login-pw-field:not([hidden])")
        # the second ID, the same password
        got = pw_submit("4002", PW)
        pp.wait_for_url(base3 + "/dung")
        pp.wait_for_function("document.getElementById('who').textContent === '4002'", timeout=15000)
        check(got.status == 200 and len(pw_cookies()) == 1, "password server: the second ID 4002 works with the same password")
        pp.click("#logout")
        pp.wait_for_url(base3 + "/login")
        check(pw_cookies() == [], "password server: after Thoát the cookie of 4002 is gone")
        # the admin: no password, no admin code (the fold stays closed)
        pp.wait_for_selector("#login-pw-field:not([hidden])")
        pp.fill("#login-id", "1001")
        with pp.expect_response(lambda r: r.url.endswith("/api/auth/login")) as got:
            pp.press("#login-id", "Enter")
        pp.wait_for_url(base3 + "/")
        check(got.value.status == 200 and pp.url == base3 + "/" and len(pw_cookies()) == 1 and pp.evaluate("fetch('/api/auth/status').then(r => r.json()).then(j => j.person.admin)") is True,
              "password server: the admin ID with no admin code enters without the password and lands on the admin page")
        pp.evaluate("fetch('/api/auth/logout', {method: 'POST'}).then(r => r.status)")
        check(wait_until(lambda: pw_cookies() == [], 6), "password server: after the admin signs out the cookie is gone")
        check(not [e for e in errs if e.startswith("pw-")], f"password server: no script error or console error {[e for e in errs if e.startswith('pw-')][:3]}")
        ctx_p.close()

        browser.close()


try:
    main()
except Exception:
    traceback.print_exc()
    check(False, "the test ran to the end without an exception")
finally:
    stop_server(srv, srv_log, tmp)
    stop_server(srv2, srv2_log, tmp2)
    stop_server(srv3, srv3_log, tmp3)

print(f"\n{len(results)} checks, {len(fails)} failed")
for f in fails:
    print("  FAILED: " + f)
sys.exit(1 if fails else 0)

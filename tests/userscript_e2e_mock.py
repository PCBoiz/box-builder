"""Manual smoke run of the team's own Tampermonkey tool (the one that lives outside this repo) against the real app (own port, own database)
and a pretend SM page. It prints what happened; it asserts nothing. SM is never contacted: sm.config.inc is answered by this script, and any
request from the tool to an address other than this machine is refused here.
Run: python tests/userscript_e2e_mock.py   (needs playwright + chromium; the team's script is found next to this project, or set TEAM_SCRIPT)
Works on Windows and Linux."""
import json, os, re, shutil, socket, subprocess, sys, tempfile, time, urllib.error, urllib.request
from urllib.parse import urlsplit
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
CANDIDATES = [os.environ.get("TEAM_SCRIPT") or "", os.path.join(ROOT, "..", "tampermonkey_taskbox_sync_tool.user.js"),
              os.path.join(ROOT, "..", "sm-userscript", "tampermonkey_taskbox_sync_tool.user.js")]
TEAM_PATH = next((c for c in CANDIDATES if c and os.path.isfile(c)), None)
if TEAM_PATH is None:
    sys.exit("The team's script was not found. Put tampermonkey_taskbox_sync_tool.user.js next to this project, or set TEAM_SCRIPT to its path.")
SCRIPT = read_text(TEAM_PATH)
PORT = int(os.environ.get("E2E_PORT") or free_port())
BROWSER = os.environ.get("E2E_BROWSER", "chromium")
BASE = f"http://127.0.0.1:{PORT}"; TOK = "tok-123"
tmp = tempfile.mkdtemp()
srv, srv_log = start_server(ROOT, clean_env(DB_PATH=os.path.join(tmp, "m.db"), CATALOG_PATH=os.path.join(ROOT, "data", "catalog.csv"), SYNC_TOKEN=TOK), PORT, tmp)

def api(path, method='GET', body=None, h=None):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={'Content-Type': 'application/json', **(h or {})})
    try:
        x = urllib.request.urlopen(r, timeout=30); return x.status, json.loads(x.read() or b'null')
    except urllib.error.HTTPError as e: return e.code, json.loads(e.read() or b'null')

refused = []
def gm(opts):
    """The tool's own requests land here. Only this machine is answered: an address on the internet is refused, never fetched."""
    if urlsplit(opts['url']).hostname not in ('127.0.0.1', 'localhost'):
        refused.append(opts['url']); return {'status': 0, 'responseText': ''}
    req = urllib.request.Request(opts['url'], data=(opts.get('data') or '').encode() or None, headers=opts.get('headers') or {}, method=opts.get('method', 'GET'))
    try:
        r = urllib.request.urlopen(req, timeout=30); return {'status': r.status, 'responseText': r.read().decode()}
    except urllib.error.HTTPError as e: return {'status': e.code, 'responseText': e.read().decode()}

try:
    # catalog + two approved boxes
    rows = [{"id": f"{9000+i:06d}", "name": f"T{i}", "vi": f"Đồ {i}", "type": "Obj", "status": "ACTIVE", "avail": 5, "qty": 5, "loc": "A01", "sector": "Hanoi-GPI"} for i in range(150)]
    print('sync', api('/api/catalog/sync', 'POST', {"objects": rows}, {'X-Sync-Token': TOK})[0])
    ids = []
    for lab in ('Box A1', 'Box B1'):
        s, b = api('/api/boxes', 'POST', {"module": "M8", "label": lab, "theme": "x"}); ids.append(b['id'])
        for _ in range(60):
            st = api('/api/state')[1]; bx = [x for x in st['boxes'] if x['id'] == b['id']][0]
            if bx['status'] != 'generating': break
            time.sleep(.2)
        print(lab, bx['status'], len(bx['items']), api(f"/api/boxes/{b['id']}/approve", 'POST')[0])
    sm_posts = []; sm_boxes = []
    def sm_boxes_route(route):
        req = route.request
        if req.method == 'GET': return route.fulfill(status=200, content_type='application/json', body=json.dumps({"boxes": sm_boxes}))
        body = json.loads(req.post_data); sm_posts.append((req.url, body))
        if req.url.rstrip('/').endswith('/api/boxes'):
            n = f"SMB-{len(sm_boxes)+1}"; sm_boxes.append({"box_id": n, "title": body['title'], "status": "created", "created_at": time.strftime('%Y-%m-%dT%H:%M:%S'), "contents": body['contents']})
            return route.fulfill(status=200, content_type='application/json', body=json.dumps({"box_id": n}))
        route.fulfill(status=200, content_type='application/json', body='{}')
    with sync_playwright() as pw:
        b = getattr(pw, BROWSER).launch(); pg = b.new_context(viewport={'width': 1500, 'height': 1000}).new_page()
        errs = []; pg.on('pageerror', lambda e: errs.append(str(e))); pg.on('dialog', lambda d: (print('dialog:', d.message[:70].replace('\n', ' ')), d.accept()))
        pg.route(lambda u: u.startswith('http') and urlsplit(u).hostname not in ('127.0.0.1', 'localhost', 'sm.config.inc'), lambda r: (refused.append(r.request.url), r.abort()))   # nothing else on the internet, ever
        pg.route('https://sm.config.inc/api/boxes**', sm_boxes_route)
        pg.route('https://sm.config.inc/', lambda r: r.fulfill(status=200, content_type='text/html', body='<html><body><nav class="navbar"><strong>tester</strong></nav></body></html>'))
        pg.expose_function('__gm', gm); pg.goto('https://sm.config.inc/')
        pg.evaluate(f"localStorage.setItem('sm_bb_url','{BASE}/');localStorage.setItem('sm_bb_token','{TOK}');localStorage.setItem('sm_bb_auto','0')")
        pg.evaluate("""(src)=>{const G=(o)=>{window.__gm({url:o.url,headers:o.headers,data:o.data,method:o.method}).then(r=>o.onload(r))};new Function('GM_xmlhttpRequest','GM_setValue','GM_getValue',src)(G,()=>{},()=>undefined);}""", SCRIPT)
        pg.click('#sm-sync-floating-btn')
        def preview():
            pg.fill('#sm-sheet-url', BASE + '/'); pg.click('#sm-btn-preview'); pg.wait_for_function("!document.getElementById('sm-btn-preview').disabled", timeout=60000); time.sleep(.5)
            print('rows:', pg.inner_text('#sm-preview-tbody, tbody').replace('\n', ' | ')[:300])
        preview()
        pg.click('#sm-btn-sync'); pg.wait_for_function("document.getElementById('sm-log-box').innerText.includes('HOÀN TẤT')", timeout=60000)
        print(pg.inner_text('#sm-log-box'))
        print('SM creates:', len([p for p in sm_posts if p[0].rstrip('/').endswith('/api/boxes')]))
        time.sleep(1.5); print('rows after re-scan:', pg.inner_text('tbody').replace('\n', ' | ')[:300])
        # second sync must create nothing
        before = len(sm_posts); pg.click('#sm-btn-sync'); time.sleep(1)
        print('extra SM writes on 2nd click:', len(sm_posts) - before)
        print('pageerrors:', errs, '| requests to other hosts refused:', refused); b.close()
    st = api('/api/state')[1]; print('app pushed:', [(x['label'], x['pushed'] and x['pushed']['sm_box_id']) for x in st['boxes']])
    print('pending:', [x['builder']['label'] for x in api('/api/taskboxes?pending=true')[1]['boxes']])
finally:
    stop_server(srv, srv_log, tmp)

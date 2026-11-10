"""Read-only look at SM's TaskBox tab. The person signs in themselves in the window this opens.
After sign-in every request that is not a read (GET/HEAD/OPTIONS) is blocked at the network level, so this cannot change SM.
Writes only screenshots and a small JSON report of structure (labels, columns, key names), not row data. Deletes its profile at the end."""
import json, os, re, shutil, sys, time
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

OUT = os.environ.get("SM_PROBE_OUT", os.path.expanduser("~/sm-probe-out"))     # where the small report goes
os.makedirs(OUT, exist_ok=True)
PROFILE = os.path.expanduser("~/.cache/box-builder-sm-profile")
os.makedirs(OUT, exist_ok=True)
LOG = open(f"{OUT}/progress.log", "w", buffering=1)
def log(m): print(m); LOG.write(m + "\n")

state = {"signed_in": False, "blocked": [], "api_gets": {}, "done": False}
def step(name, fn):
    try:
        fn()
    except Exception as e:
        report["notes"].append(f"{name} failed: {str(e)[:160]}")
        log(f"step '{name}' failed (continuing): {str(e)[:120]}")
report = {"tabs": [], "headings": [], "buttons": [], "tables": [], "dialog": {}, "sample_titles": [], "objects_api": {}, "notes": []}

def guard(route):
    r = route.request
    if state["signed_in"] and r.method not in ("GET", "HEAD", "OPTIONS"):
        state["blocked"].append(f"{r.method} {urlsplit(r.url).path}")
        return route.abort()
    return route.continue_()

def seen(resp):
    p = urlsplit(resp.url)
    if p.netloc.endswith("sm.config.inc") and p.path.startswith("/api/") and resp.request.method == "GET":
        state["api_gets"][p.path] = state["api_gets"].get(p.path, 0) + 1

try:
    with sync_playwright() as pw:
        # Firefox, because that is the browser that works on this machine. Nothing watches or changes requests until sign-in is done.
        ctx = pw.firefox.launch_persistent_context(PROFILE, headless=False, viewport={"width": 1400, "height": 900})
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        pg.on("response", seen)
        pg.goto("https://sm.config.inc/boxes")
        log("WINDOW OPEN (Firefox): waiting for the person to sign in (up to 15 minutes)")
        end = time.time() + 900
        last_log = 0
        while time.time() < end:
            try:
                status, ctype = pg.evaluate("fetch('/api/boxes?limit=1',{credentials:'same-origin'}).then(r=>[r.status, r.headers.get('content-type')||'']).catch(()=>[0,''])")
                path = urlsplit(pg.url).path
                if time.time() - last_log > 10:
                    log(f"waiting: page {path}, boxes api -> {status} {ctype[:20]}")
                    last_log = time.time()
                if status == 200 and "json" in ctype and "/login" not in path:     # the API answers with data, so there is a session
                    break
            except Exception as e:
                log("check error: " + str(e)[:80])
            time.sleep(2)
        else:
            log("TIMEOUT: nobody signed in"); sys.exit(2)
        state["signed_in"] = True
        ctx.route("**/*", guard)                      # from here on only reads can leave this browser
        log("SIGNED IN: read-only guard is now on (non-GET requests are blocked)")
        pg.goto("https://sm.config.inc/boxes"); pg.wait_for_load_state("networkidle"); time.sleep(1.5)
        pg.screenshot(path=f"{OUT}/01-boxes.png", full_page=False)

        report["headings"] = pg.eval_on_selector_all("h1,h2,h3", "els => els.map(e => e.innerText.trim()).filter(Boolean).slice(0,20)")
        clickables = pg.eval_on_selector_all("a,button,[role=tab]", "els => els.filter(e => e.offsetParent !== null).map(e => (e.innerText||e.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' ')).filter(t => t && t.length < 50)")
        report["tabs"] = list(dict.fromkeys(clickables))[:80]
        log(f"clickable labels on /boxes: {report['tabs']}")

        def tab_and_tables():
            sel = pg.eval_on_selector_all("[aria-selected=true], .active, .nav-link.active, button.active", "els => els.map(e => (e.innerText||'').trim().replace(/\\s+/g,' ')).filter(t => t && t.length < 40)")
            report["selected_tabs"] = list(dict.fromkeys(sel))[:6]
            pg.screenshot(path=f"{OUT}/02-taskbox-tab.png", full_page=False)
            report["buttons"] = list(dict.fromkeys(pg.eval_on_selector_all("button,a.btn,[role=button]", "els => els.filter(e => e.offsetParent !== null).map(e => (e.innerText||e.getAttribute('aria-label')||'').trim().replace(/\\s+/g,' ')).filter(t => t && t.length < 60)")))[:60]
            report["filters"] = pg.eval_on_selector_all("select, input[type=search], input[type=text], input[type=date]", "els => els.filter(e => e.offsetParent !== null).map(e => (e.getAttribute('placeholder')||e.getAttribute('aria-label')||e.name||e.type||e.tagName).toString().slice(0,50))")[:20]
            report["status_filter_options"] = pg.eval_on_selector_all("select option", "els => els.map(e => e.innerText.trim()).slice(0,20)")
            report["row_actions"] = list(dict.fromkeys(pg.eval_on_selector_all("button", "els => els.filter(e => e.offsetParent !== null).map(e => (e.innerText||'').trim()).filter(t => /borrow|return|assign|send|collect|handover|unassign|cancel/i.test(t))")))[:20]
            report["badges"] = list(dict.fromkeys(pg.eval_on_selector_all(".badge, [class*=badge], [class*=status]", "els => els.map(e => (e.innerText||'').trim()).filter(t => t && t.length < 25)")))[:20]
            titles = pg.eval_on_selector_all("h2,h3,h4,strong,b", "els => els.map(e => (e.innerText||'').trim()).filter(t => t && t.length < 40)")
            report["sample_titles"] = list(dict.fromkeys(titles))[:12]
            report["match_line"] = (pg.get_by_text(re.compile(r"\d+\s+boxes\s+match", re.I)).first.inner_text() if pg.get_by_text(re.compile(r"\d+\s+boxes\s+match", re.I)).count() else "")
        step("tab, filters and row actions", tab_and_tables)

        def dialog():
            nb = pg.get_by_role("button", name=re.compile(r"new\s*task\s*box", re.I)).first
            if not nb.count(): nb = pg.get_by_text(re.compile(r"new\s*task\s*box", re.I)).first
            nb.click(timeout=8000); time.sleep(1.2)
            pg.screenshot(path=f"{OUT}/03-new-taskbox-dialog.png")
            dlg = pg.locator("[role=dialog], .modal, dialog").first
            report["dialog"] = {
                "labels": dlg.locator("label, h3, h4").all_inner_texts()[:20],
                "inputs": dlg.eval_on_selector_all("input,select,textarea", "els => els.map(e => ({type:e.type||e.tagName.toLowerCase(), placeholder:e.placeholder||'', name:e.name||e.id||''}))"),
                "buttons": dlg.locator("button").all_inner_texts()[:12],
            }
            cancel = dlg.get_by_role("button", name=re.compile(r"cancel|close", re.I)).first
            if cancel.count(): cancel.click(timeout=5000)
            else: pg.keyboard.press("Escape")
            time.sleep(0.8)
            log("dialog read and closed without saving")
        step("new TaskBox dialog", dialog)

        def objects_api():
            d = pg.evaluate("fetch('/api/objects?page=1',{credentials:'same-origin'}).then(r=>r.json())")
            arr = d if isinstance(d, list) else next((d[k] for k in ("objects","items","data","results") if isinstance(d.get(k), list)), [])
            o = arr[0] if arr else {}
            rx = re.compile(r"img|image|thumb|photo|pic|url|file|media", re.I)
            hits = {k: (v if not isinstance(v, str) else v.split("?")[0].split("#")[0]) for k, v in o.items() if rx.search(k)}
            report["objects_api"] = {"top_keys": list(d.keys()) if isinstance(d, dict) else "list", "object_keys": list(o.keys()),
                                      "image_like": {k: (v if isinstance(v, (str, int, float, bool, type(None))) else type(v).__name__) for k, v in hits.items()}}
            url = next((v for v in hits.values() if isinstance(v, str) and re.search(r"\.(png|jpe?g|webp|gif)|/media|/upload|/static|/image", v, re.I)), None)
            if url:
                full = url if url.startswith("http") else "https://sm.config.inc" + (url if url.startswith("/") else "/" + url)
                resp = pw.request.new_context().get(full, timeout=15000)       # a fresh client with no cookies
                report["objects_api"]["image_url_pattern"] = re.sub(r"\d{4,}", "<id>", full)
                report["objects_api"]["anonymous_status"] = resp.status
        step("objects api", objects_api)

        def boxes_api():
            d = pg.evaluate("fetch('/api/boxes?limit=3',{credentials:'same-origin'}).then(r=>r.json())")
            arr = d if isinstance(d, list) else next((d[k] for k in ("boxes","items","data","results") if isinstance(d.get(k), list)), [])
            o = arr[0] if arr else {}
            report["boxes_api"] = {"top_keys": list(d.keys()) if isinstance(d, dict) else "list", "box_keys": list(o.keys()),
                                    "content_keys": list((o.get("contents") or [{}])[0].keys()) if isinstance(o.get("contents"), list) and o.get("contents") else []}
        step("boxes api", boxes_api)

        report["api_gets_seen"] = state["api_gets"]
        report["blocked_non_get"] = state["blocked"]
        ctx.close()
    json.dump(report, open(f"{OUT}/report.json", "w"), ensure_ascii=False, indent=2)
    state["done"] = True
    log("DONE: report.json written")
finally:
    if state["done"]:
        shutil.rmtree(PROFILE, ignore_errors=True)   # the sign-in data does not stay on disk once the report exists
        log("profile deleted")
    else:
        log("stopped before the report was written: profile kept so the sign-in can be reused")

"""One-time READ-ONLY look at how SM reports how often an object was used. The person signs in themselves in the window this opens
(this script never sees a password). After sign-in every request that is not a read (GET/HEAD/OPTIONS) is blocked at the network level.
Stores only numbers and field shapes (no names, no people, no row data), then deletes its browser profile."""
import json, os, shutil, sys, time
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

OUT = os.environ.get("SM_PROBE_OUT", os.path.expanduser("~/sm-probe-out"))     # where the small report goes
os.makedirs(OUT, exist_ok=True)
PROFILE = os.path.expanduser("~/.cache/box-builder-sm-profile")
LOG = open(f"{OUT}/usage_probe.log", "w", buffering=1)
def log(m): print(m); LOG.write(m + "\n")
state = {"signed_in": False, "blocked": []}

def guard(route):
    r = route.request
    if state["signed_in"] and r.method not in ("GET", "HEAD", "OPTIONS"):
        state["blocked"].append(f"{r.method} {urlsplit(r.url).path}")
        return route.abort()
    return route.continue_()

def shape(v, depth=0):
    if isinstance(v, bool): return "bool:" + str(v)
    if isinstance(v, (int, float)): return v
    if isinstance(v, str): return "str:" + v[:30]
    if v is None: return None
    if isinstance(v, list): return [shape(x, depth + 1) for x in v[:4]] if depth < 2 else f"list[{len(v)}]"
    if isinstance(v, dict): return {k: shape(x, depth + 1) for k, x in list(v.items())[:14]} if depth < 2 else f"dict[{len(v)}]"
    return str(type(v))

report = {"samples": [], "pages": 0, "objects": 0, "hanoi_gpi": 0, "usage_type_counts": {}, "extraction": {}, "hist": {}, "activeness_type_counts": {}, "notes": []}
BANDS = [(0, 49), (50, 99), (100, 199), (200, 299), (300, 399), (400, 499), (500, 749), (750, 999), (1000, 1499), (1500, 10**9)]
def band(n):
    for lo, hi in BANDS:
        if lo <= n <= hi: return f"{lo}-{hi if hi < 10**9 else 'up'}"
def extract(u):
    """number of borrows from whatever _usage_count is"""
    if isinstance(u, bool): return None, "bool"
    if isinstance(u, (int, float)): return int(u), "number"
    if isinstance(u, dict):
        for k in ("borrows", "borrow_count", "count", "total", "uses", "used", "value"):
            if isinstance(u.get(k), (int, float)) and not isinstance(u.get(k), bool): return int(u[k]), "dict." + k
        nums = [(k, x) for k, x in u.items() if isinstance(x, (int, float)) and not isinstance(x, bool)]
        if nums: return int(nums[0][1]), "dict.first:" + nums[0][0]
    return None, "unreadable"

try:
    with sync_playwright() as pw:
        ctx = pw.firefox.launch_persistent_context(PROFILE, headless=False, viewport={"width": 1300, "height": 850})
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        pg.goto("https://sm.config.inc/boxes")
        log("WINDOW OPEN (Firefox): waiting for the person to sign in (up to 15 minutes)")
        end, last = time.time() + 900, 0
        while time.time() < end:
            try:
                status, ctype = pg.evaluate("fetch('/api/boxes?limit=1',{credentials:'same-origin'}).then(r=>[r.status, r.headers.get('content-type')||'']).catch(()=>[0,''])")
                path = urlsplit(pg.url).path
                if time.time() - last > 15: log(f"waiting: page {path}, api -> {status}"); last = time.time()
                if status == 200 and "json" in ctype and "/login" not in path: break
            except Exception as e:
                pass
            time.sleep(2)
        else:
            log("TIMEOUT: nobody signed in"); sys.exit(2)
        state["signed_in"] = True
        ctx.route("**/*", guard)
        log("SIGNED IN: read-only guard is on")
        total_pages, page = 1, 1
        while page <= total_pages and page <= 200:
            d = pg.evaluate("p => fetch('/api/objects?page=' + p, {credentials:'same-origin'}).then(r => r.json())", page)
            if page == 1: total_pages = int(d.get("total_pages") or 1); report["total_items"] = d.get("total_items"); log(f"objects: {d.get('total_items')} in {total_pages} pages")
            for o in d.get("objects", []):
                report["objects"] += 1
                if o.get("sector") != "Hanoi-GPI": continue
                report["hanoi_gpi"] += 1
                u = o.get("_usage_count"); a = o.get("_activeness_score")
                report["usage_type_counts"][type(u).__name__] = report["usage_type_counts"].get(type(u).__name__, 0) + 1
                report["activeness_type_counts"][type(a).__name__] = report["activeness_type_counts"].get(type(a).__name__, 0) + 1
                if len(report["samples"]) < 4:
                    report["samples"].append({"_usage_count": shape(u), "_activeness_score": shape(a), "_inventory": shape(o.get("_inventory")), "last_activity": shape(o.get("last_activity")),
                                              "status": o.get("status"), "availability_status": o.get("availability_status"), "quantity": o.get("quantity")})
                n, how = extract(u)
                report["extraction"][how] = report["extraction"].get(how, 0) + 1
                if n is not None:
                    b = band(n); report["hist"][b] = report["hist"].get(b, 0) + 1
            report["pages"] = page
            if page % 10 == 0: log(f"read page {page}/{total_pages}")
            page += 1
            time.sleep(0.15)
        report["blocked_non_get"] = state["blocked"]
        json.dump(report, open(f"{OUT}/usage_probe.json", "w"), indent=1, ensure_ascii=False)
        log("DONE: usage_probe.json written")
        ctx.close()
    shutil.rmtree(PROFILE, ignore_errors=True)
    log("profile deleted")
except SystemExit:
    raise
except Exception as e:
    log("FAILED: " + str(e)[:200])
    report["notes"].append(str(e)[:200]); json.dump(report, open(f"{OUT}/usage_probe.json", "w"), indent=1, ensure_ascii=False)

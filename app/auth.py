"""Login by company ID, for the people who use the box page, with an admin role for the people who approve.

What this is and is not: an ID says who someone is, it does not prove it. Anyone who learns an ID can use it, so this tells the team who made
which box and keeps strangers away from the admin page, but it is not a strong lock. An optional ADMIN_CODE (a second secret the owner puts in
Render) makes the admin role stronger.

Switched off until the owner sets ADMIN_IDS in Render. With it empty the app is open exactly as before, so deploying this code cannot lock the
admin out. Nothing here talks to Scenario Manager, Google Sheets or Render.

The scripts that run in the team's browser tabs (the Tampermonkey tools and the sheet script) cannot log in. The routes they call are listed in
MACHINE and are never gated by a session: they keep the sync token for writes and are otherwise open (the shared access code is gone). So
they must never carry an owner or a company ID, which is the login: a stranger can read them.
Every other route is admin-only unless it is listed as public or as a user route (default deny)."""
import hashlib
import hmac
import os
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import db

ID_RE = re.compile(r"^[A-Za-z0-9_.-]{2,20}$")
IDLE_SECONDS = 14 * 86400            # a session that has not been used for this long ends
MAX_SECONDS = 30 * 86400             # and no session lasts longer than this
TOUCH_EVERY = 300                    # last_seen is written at most this often, so reading a page is not a database write
FAIL_WINDOW = 600                    # failed logins are counted over this many seconds
IP_MAX = 30                          # failures from one address in the window before it must wait (one office shares an address and, with a shared password, its typos)
KEY_MAX = 5                          # failures from one address against one ID in 15 minutes before that pair must wait (an admin code guessed at)
GLOBAL_MAX = 100                     # failures from everyone in the window before addresses that already failed once must wait
MAX_PEOPLE_LINES = 500
HEADER_WORDS = {"id", "stt", "mã", "ma", "mã nv", "ma nv", "mã số", "ma so", "user", "user id", "company id", "id công ty", "id cong ty"}

PUBLIC_EXACT = {"/login", "/api/config", "/api/auth/status", "/api/auth/login", "/api/auth/logout",
                "/box_builder_sm.user.js", "/box_builder_sm.txt", "/favicon.ico"}
PUBLIC_PREFIX = ("/static/",)
MACHINE_EXACT = {"/api/taskboxes", "/api/sheet-boxes", "/api/catalog/sync", "/api/sm/boxes/sync", "/api/sync/check"}
MACHINE_PUSHED = re.compile(r"^/api/boxes/[^/]+/pushed$")
USER_EXACT = {"/dung", "/my/print"}
USER_PREFIX = ("/api/my/",)
HTML_PAGES = {"/", "/dung", "/my/print", "/print/boxes"}      # pages a browser opens: a visitor with no session is sent to the login page, not shown JSON
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")

GENERIC_REFUSAL = "Không vào được. Kiểm tra lại ID, hoặc nhờ admin thêm ID của bạn."
REFUSAL_PASSWORD = "Không vào được. Kiểm tra lại ID và mật khẩu, hoặc nhờ admin thêm ID của bạn."          # same sentence for a wrong ID and a wrong password


@dataclass(frozen=True)
class Person:
    id: str
    name: str = ""
    module: str = ""
    admin: bool = False

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "module": self.module, "admin": self.admin}


class Refused(Exception):
    def __init__(self, status: int, detail: str, retry_after: int = 0):
        super().__init__(detail)
        self.status, self.detail, self.retry_after = status, detail, retry_after


# ----------------------------------------------------------------------------- who counts as an admin, and whether login is on
def env_admins(s) -> set[str]:
    return {x for x in (normalize_id(p) for p in re.split(r"[,\s;]+", s.admin_ids or "")) if x}


def env_users(s) -> set[str]:
    """Ordinary users named in USER_IDS (Render): they can sign in before any list is pasted. Never admins."""
    return {x for x in (normalize_id(p) for p in re.split(r"[,\s;]+", s.user_ids or "")) if x}


def user_password_of(s) -> str:
    """The password every ordinary user types, trimmed like the admin code. Empty = users sign in with the ID alone."""
    return (s.user_password or "").strip()


def admin_code_of(s) -> str:
    """The admin code with stray spaces and newlines removed: a trailing space in the Render value must not lock every admin out."""
    return (s.admin_code or "").strip()


def enforced(s) -> bool:
    """Login is on only when the owner has named at least one admin. Otherwise the app is open, as it was before this existed."""
    return bool(env_admins(s))


def normalize_id(raw) -> str | None:
    t = str(raw if raw is not None else "").strip()
    return t if ID_RE.fullmatch(t) else None


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:24]


def find_person(conn, s, pid: str | None) -> Person | None:
    if not pid:
        return None
    row = conn.execute("SELECT person_id, name, module, is_admin FROM people WHERE person_id=?", (pid,)).fetchone()
    admin = pid in env_admins(s) or bool(row and row["is_admin"])
    if row is None and not admin and pid not in env_users(s):
        return None
    return Person(pid, row["name"] if row else "", row["module"] if row else "", admin)


# ----------------------------------------------------------------------------- audit
def audit(conn, actor: str, action: str, box_id: int | None = None, detail: str = "", ip: str = "", now: float | None = None) -> None:
    """Append one line to the log of who did what. Never put a secret, a token or a typed ID that failed in `detail`."""
    conn.execute("INSERT INTO audit_log(at, actor, action, box_id, detail, ip_hash) VALUES(?,?,?,?,?,?)",
                 (time.time() if now is None else now, actor[:40], action[:40], box_id, detail[:300], _h(ip) if ip else ""))
    conn.execute("DELETE FROM audit_log WHERE id <= (SELECT MAX(id) FROM audit_log) - 5000")       # failed logins by a stranger must not grow the table for ever


def recent_audit(conn, n: int = 50) -> list[dict]:
    rows = conn.execute("SELECT at, actor, action, box_id, detail FROM audit_log ORDER BY id DESC LIMIT ?", (max(1, min(n, 500)),)).fetchall()
    return [{"at": r["at"], "actor": r["actor"], "action": r["action"], "box_id": r["box_id"], "detail": r["detail"]} for r in rows]


# ----------------------------------------------------------------------------- delay after too many failures
def _wait_seconds(conn, ip_h: str, key_h: str, now: float) -> int:
    """How long this attempt must wait, 0 when it may go ahead. Counters live in the database, so a restart or a sleeping server does not
    reset them. A delay that grows, not a lock-out: someone who knows a colleague's ID must not be able to shut that colleague out."""
    since = now - FAIL_WINDOW

    def last_and_count(where: str, params: tuple):
        r = conn.execute(f"SELECT COUNT(*), MAX(at) FROM login_fail WHERE {where}", params).fetchone()
        return int(r[0] or 0), float(r[1] or 0)

    wait_until = 0.0
    n, last = last_and_count("ip_hash=? AND at>=?", (ip_h, since))
    if n >= IP_MAX:
        wait_until = max(wait_until, last + min(900, 30 * 2 ** (n - IP_MAX)))
    if key_h:
        n, last = last_and_count("key_hash=? AND at>=?", (key_h, now - 900))
        if n >= KEY_MAX:
            wait_until = max(wait_until, last + min(300, 15 * 2 ** (n - KEY_MAX)))
    total, last_any = last_and_count("at>=?", (since,))
    mine, _ = last_and_count("ip_hash=? AND at>=?", (ip_h, since))
    if total >= GLOBAL_MAX and mine >= 1:
        wait_until = max(wait_until, last_any + 60)
    return max(0, int(wait_until - now + 0.999))


def _record_fail(conn, ip_h: str, key_h: str, now: float) -> None:
    conn.execute("INSERT INTO login_fail(at, ip_hash, key_hash) VALUES(?,?,?)", (now, ip_h, key_h))
    conn.execute("DELETE FROM login_fail WHERE at < ?", (now - 86400,))


# ----------------------------------------------------------------------------- sessions
def _new_session(conn, pid: str, now: float, secret: str = "") -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO sessions(token_hash, person_id, created_at, last_seen, expires_at) VALUES(?,?,?,?,?)",
                 (_h_full(token, secret), pid, now, now, now + MAX_SECONDS))
    conn.execute("DELETE FROM sessions WHERE expires_at < ? OR last_seen < ?", (now, now - IDLE_SECONDS))
    return token


def _h_full(token: str, secret: str = "") -> str:
    """What is stored for a session token. With a user password set the fingerprint is keyed by it, so setting or changing USER_PASSWORD ends
    every session made before (a stranger who got in while the ID was enough, or a password that leaked, is out at once)."""
    if not secret:
        return hashlib.sha256(token.encode("utf-8", "replace")).hexdigest()
    return hmac.new(secret.encode("utf-8", "replace"), token.encode("utf-8", "replace"), "sha256").hexdigest()


def person_for_token(conn, s, token: str, now: float | None = None) -> Person | None:
    now = time.time() if now is None else now
    if not token:
        return None
    th = _h_full(token, user_password_of(s))
    row = conn.execute("SELECT person_id, last_seen, expires_at FROM sessions WHERE token_hash=?", (th,)).fetchone()
    if row is None:
        return None
    if now > row["expires_at"] or now - row["last_seen"] > IDLE_SECONDS:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (th,))
        return None
    person = find_person(conn, s, row["person_id"])
    if person is None:                                   # taken off the list since: the session ends with it
        conn.execute("DELETE FROM sessions WHERE person_id=?", (row["person_id"],))
        return None
    if now - row["last_seen"] > TOUCH_EVERY:
        conn.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, th))
    return person


def logout(conn, token: str, secret: str = "") -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (_h_full(token, secret),))


def login(conn, s, raw_id, code: str, ip: str, now: float | None = None, password: str = "") -> tuple[Person, str]:
    now = time.time() if now is None else now
    pid = normalize_id(raw_id)
    ip_h, key_h = _h(ip or "-"), (_h(f"{ip or '-'}|{pid}") if pid else "")      # the per-ID delay is per address too: an outsider's guesses must not slow the real admin down
    wait = _wait_seconds(conn, ip_h, key_h, now)
    if wait:
        how_long = f"{wait} giây" if wait < 120 else f"{-(-wait // 60)} phút"            # minutes from 2 minutes up, rounded up so nobody tries too early
        raise Refused(429, f"Đã thử sai nhiều lần. Chờ {how_long} rồi thử lại.", wait)
    person = find_person(conn, s, pid)
    ok = person is not None
    if ok and person.admin and admin_code_of(s):         # the admin code is only asked of admins; a wrong one looks like any other refusal
        ok = hmac.compare_digest((code or "").strip().encode("utf-8", "replace"), admin_code_of(s).encode("utf-8", "replace"))
    elif ok and not person.admin and user_password_of(s):      # the user password is only asked of ordinary users; wrong looks like any other refusal
        ok = hmac.compare_digest((password or "").strip().encode("utf-8", "replace"), user_password_of(s).encode("utf-8", "replace"))
    if not ok:
        _record_fail(conn, ip_h, key_h, now)
        audit(conn, "-", "login_fail", detail="", ip=ip or "-", now=now)
        raise Refused(401, REFUSAL_PASSWORD if user_password_of(s) else GENERIC_REFUSAL)
    token = _new_session(conn, person.id, now, user_password_of(s))
    audit(conn, person.id, "login", detail="admin" if person.admin else "user", ip=ip or "-", now=now)
    return person, token


# ----------------------------------------------------------------------------- the allowlist
def parse_people(text: str) -> tuple[list[dict], list[dict]]:
    """Lines like `1111, Hải, M2` or `2222, Hưng, M3, admin` (also separated by tabs or semicolons, as copied from a sheet).
    The first word is the ID, then an optional name, an optional module, and the word `admin` anywhere after the ID."""
    rows, bad, seen = [], [], set()
    first = True
    for n, line in enumerate(str(text or "").splitlines(), 1):
        if not line.strip():
            continue
        head, first = first, False
        if head and re.split(r"[,\t;]", line)[0].strip().lower() in HEADER_WORDS:      # "ID<tab>Họ tên<tab>Module" pasted with the sheet's header row
            continue
        if len(rows) + len(bad) >= MAX_PEOPLE_LINES:
            bad.append({"line": n, "reason": f"Chỉ nhận tối đa {MAX_PEOPLE_LINES} dòng một lần."})
            break
        parts = [p.strip() for p in re.split(r"[,\t;]", line)]
        pid = normalize_id(parts[0])
        if not pid:
            spaced = len(parts) == 1 and re.search(r"\s", parts[0])
            bad.append({"line": n, "reason": "Tách ID, tên, module bằng dấu phẩy hoặc phím Tab, ví dụ 2032, Hưng, M3." if spaced
                        else "ID phải gồm 2 đến 20 ký tự: chữ, số, dấu gạch ngang, gạch dưới hoặc dấu chấm."})
            continue
        if pid in seen:
            bad.append({"line": n, "reason": f"ID {pid} bị trùng."})
            continue
        seen.add(pid)
        rest = parts[1:]
        admin = any(p.lower() == "admin" for p in rest)
        rest = [p for p in rest if p.lower() != "admin"]
        rows.append({"id": pid, "name": rest[0][:60] if rest else "", "module": rest[1][:20] if len(rest) > 1 else "", "admin": admin})
    return rows, bad


def save_people(conn, rows: list[dict], now: float | None = None) -> dict:
    now = time.time() if now is None else now
    existing = {r[0] for r in conn.execute("SELECT person_id FROM people")}
    added = updated = 0
    for r in rows:
        if r["id"] in existing:
            conn.execute("UPDATE people SET name=?, module=?, is_admin=? WHERE person_id=?", (r["name"], r["module"], int(r["admin"]), r["id"]))
            updated += 1
        else:
            conn.execute("INSERT INTO people(person_id, name, module, is_admin, added_at) VALUES(?,?,?,?,?)",
                         (r["id"], r["name"], r["module"], int(r["admin"]), now))
            added += 1
    return {"added": added, "updated": updated}


def list_people(conn, s) -> list[dict]:
    env, envu = env_admins(s), env_users(s)
    out = {r["person_id"]: {"id": r["person_id"], "name": r["name"], "module": r["module"], "admin": bool(r["is_admin"]) or r["person_id"] in env,
                            "from_setting": r["person_id"] in env or r["person_id"] in envu, "added_at": r["added_at"]}
           for r in conn.execute("SELECT person_id, name, module, is_admin, added_at FROM people ORDER BY person_id")}
    for pid in sorted(env - set(out)):
        out[pid] = {"id": pid, "name": "", "module": "", "admin": True, "from_setting": True, "added_at": 0}
    for pid in sorted(envu - set(out)):
        out[pid] = {"id": pid, "name": "", "module": "", "admin": False, "from_setting": True, "added_at": 0}
    return sorted(out.values(), key=lambda p: p["id"])


# ----------------------------------------------------------------------------- cookie and request helpers
def _https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip().lower() == "https"


def cookie_names() -> tuple[str, str]:
    return "__Host-sid", "sid"


def read_token(request: Request) -> str:
    for name in cookie_names():
        v = request.cookies.get(name)
        if v:
            return v
    return ""


def set_cookie(response: Response, token: str, secure: bool) -> None:
    response.set_cookie("__Host-sid" if secure else "sid", token, max_age=MAX_SECONDS, path="/", httponly=True, samesite="lax", secure=secure)


def clear_cookie(response: Response, secure: bool) -> None:
    for name in cookie_names():
        host_prefixed = name.startswith("__Host-")
        response.delete_cookie(name, path="/", httponly=True, samesite="lax", secure=True if host_prefixed else secure)


def client_ip(request: Request) -> str:
    """The proxy in front adds the real address at the end of X-Forwarded-For; an entry the visitor made up sits before it."""
    xff = request.headers.get("x-forwarded-for", "")
    last = xff.split(",")[-1].strip() if xff else ""
    return last or (request.client.host if request.client else "-")


def klass(method: str, path: str) -> str:
    """public | machine | user | admin. Anything not named here is admin only."""
    if path in PUBLIC_EXACT or path.startswith(PUBLIC_PREFIX):
        return "public"
    if path in MACHINE_EXACT or (MACHINE_PUSHED.match(path) and method in ("POST", "DELETE")):
        return "machine"
    if path in USER_EXACT or path.startswith(USER_PREFIX):
        return "user"
    return "admin"


def safe_next(value: str | None) -> str:
    """Where to go after login: a path on this site only."""
    v = value or ""
    return v if v.startswith("/") and not v.startswith("//") and "\\" not in v and not any(ord(ch) <= 32 or ord(ch) == 127 for ch in v) else ""


STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def static_page(name: str) -> HTMLResponse:
    """A page from app/static with its scripts and styles stamped by file time, so a browser never mixes a new page with old scripts."""
    with open(os.path.join(STATIC_DIR, name), encoding="utf-8") as f:
        html = f.read()

    def stamp(m):
        try:
            v = os.stat(os.path.join(STATIC_DIR, m.group(1))).st_mtime_ns
        except OSError:
            v = 0
        return f'/static/{m.group(1)}?v={v}"'
    return HTMLResponse(re.sub(r'/static/([A-Za-z0-9_.-]+\.(?:js|css))"', stamp, html), headers={"Cache-Control": "no-cache"})


def who(request: Request) -> str:
    """For the audit log: the signed-in ID, or a fixed word when login is off."""
    p = getattr(request.state, "person", None)
    return p.id if p else "(chưa bật đăng nhập)"


# ----------------------------------------------------------------------------- wiring
class LoginBody(BaseModel):
    # generous on purpose: a too-long ID must get the same refusal as any wrong ID, not a different kind of error
    id: str = Field(default="", max_length=300)
    code: str = Field(default="", max_length=300)
    password: str = Field(default="", max_length=300)


class PeopleBody(BaseModel):
    text: str = Field(default="", max_length=60000)


def install(app, s) -> None:
    """Add the login gate (middleware) and the routes for login, logout, status and the admin's list of people."""

    def conn_dep():
        c = db.connect(s.db_path)
        try:
            yield c
        finally:
            c.close()

    def _lookup(token: str):
        """Sync on purpose: it runs in the threadpool, so a slow database never stalls the event loop that every request shares."""
        c = db.connect(s.db_path)
        try:
            return person_for_token(c, s, token)
        finally:
            c.close()

    @app.middleware("http")
    async def gate(request: Request, call_next):
        request.state.person = None
        if not enforced(s):
            return await call_next(request)
        path, method = request.url.path, request.method
        kind = klass(method, path)
        if kind in ("public", "machine"):
            return await call_next(request)
        token = read_token(request)
        person = await run_in_threadpool(_lookup, token) if token else None
        if person is None:
            if method in SAFE_METHODS and path in HTML_PAGES:
                nxt = path + ("?" + request.url.query if request.url.query else "")
                return RedirectResponse("/login?next=" + quote(nxt, safe="/"), status_code=302)
            return JSONResponse({"detail": "Cần đăng nhập."}, status_code=401)
        if method not in SAFE_METHODS:
            # a request that carries the login cookie must prove it comes from this site: SameSite=Lax is the first guard, this is the second
            site, origin = request.headers.get("sec-fetch-site", ""), request.headers.get("origin")
            if site:
                same = site in ("same-origin", "none")
            elif origin is not None:
                same = urlsplit(origin).netloc == request.headers.get("host", "")
            else:
                same = False
            if not same:
                return JSONResponse({"detail": "Thao tác này không đến từ trang Box builder nên bị chặn. Tải lại trang rồi thử lại."}, status_code=403)
        if kind == "admin" and not person.admin:
            if method in SAFE_METHODS and path in HTML_PAGES:
                return RedirectResponse("/dung", status_code=302)
            return JSONResponse({"detail": "Việc này chỉ dành cho admin."}, status_code=403)
        request.state.person = person
        return await call_next(request)

    router = APIRouter()
    admin_router = router

    @router.get("/login")
    def login_page():
        return static_page("login.html")

    @router.get("/api/auth/status")
    def status(request: Request, c=Depends(conn_dep)):
        person = None
        if enforced(s):
            token = read_token(request)
            person = person_for_token(c, s, token) if token else None
        return {"enforced": enforced(s), "person": person.public() if person else None, "admin_code": bool(admin_code_of(s)), "user_password": bool(user_password_of(s))}

    @router.post("/api/auth/login")
    def do_login(body: LoginBody, request: Request, c=Depends(conn_dep)):
        if not enforced(s):
            raise Refused(409, "Chưa bật đăng nhập. Đặt ADMIN_IDS trong Render.")
        person, token = login(c, s, body.id, body.code, client_ip(request), password=body.password)
        out = JSONResponse({"person": person.public(), "go": "/" if person.admin else "/dung"})
        set_cookie(out, token, _https(request))
        return out

    @router.post("/api/auth/logout")
    def do_logout(request: Request, c=Depends(conn_dep)):
        token = read_token(request)
        person = person_for_token(c, s, token) if token and enforced(s) else None
        logout(c, token, user_password_of(s))
        if person:
            audit(c, person.id, "logout", ip=client_ip(request))
        out = JSONResponse({"ok": True})
        clear_cookie(out, _https(request))
        return out

    @app.exception_handler(Refused)
    async def _refused(_: Request, e: Refused):
        headers = {"Retry-After": str(e.retry_after)} if e.retry_after else None
        return JSONResponse({"detail": e.detail}, status_code=e.status, headers=headers)

    @admin_router.get("/api/admin/people")
    def people(c=Depends(conn_dep)):
        on = enforced(s)
        return {"enforced": on, "admin_code": bool(s.admin_code), "user_password": bool(user_password_of(s)),
                "people": list_people(c, s) if on else []}      # with login off anyone could read these IDs, and an ID is a login

    @admin_router.post("/api/admin/people")
    def add_people(body: PeopleBody, request: Request, c=Depends(conn_dep)):
        if not enforced(s):      # with login off anyone can call this, and an ID planted now would be an admin the day login is switched on
            raise Refused(409, "Chưa lưu được vì chưa bật đăng nhập. Hãy bật trước: đặt ADMIN_IDS trong Render.")
        rows, bad = parse_people(body.text)
        if not rows and not bad:
            raise Refused(422, "Chưa có dòng nào. Mỗi dòng một người: ID, tên, module (thêm chữ admin nếu là admin).")
        env = env_admins(s)
        for r in rows:                                   # an admin named in the setting stays admin whatever the list says
            if r["id"] in env:
                r["admin"] = True
        out = save_people(c, rows)
        audit(c, who(request), "people_save", detail=f"thêm {out['added']}, sửa {out['updated']}", ip=client_ip(request))
        return {**out, "bad": bad}

    @admin_router.delete("/api/admin/people/{pid}")
    def remove_person(pid: str, request: Request, c=Depends(conn_dep)):
        if not enforced(s):
            raise Refused(409, "Chưa xoá được vì chưa bật đăng nhập. Hãy bật trước: đặt ADMIN_IDS trong Render.")
        pid = normalize_id(pid) or ""
        if pid in env_admins(s):
            raise Refused(409, "ID này là admin cố định (đặt ở ADMIN_IDS trong Render), không xoá được ở đây.")
        if pid in env_users(s):
            raise Refused(409, "ID này đặt ở USER_IDS trong Render, không xoá được ở đây.")
        if not c.execute("SELECT 1 FROM people WHERE person_id=?", (pid,)).fetchone():
            raise Refused(404, "ID này không có trong danh sách.")
        c.execute("DELETE FROM sessions WHERE person_id=?", (pid,))
        c.execute("DELETE FROM people WHERE person_id=?", (pid,))
        audit(c, who(request), "people_remove", detail=pid, ip=client_ip(request))
        return {"ok": True}

    @admin_router.get("/api/admin/audit")
    def get_audit(n: int = 50, c=Depends(conn_dep)):
        return {"entries": recent_audit(c, n) if enforced(s) else []}

    app.include_router(router)

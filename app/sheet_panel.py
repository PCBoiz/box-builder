"""The "Bảng tính Google" box on the Automations tab: where the sheet is, when its own script last asked for boxes, and the script itself with
this app's address already in it.

The server never reads or writes the sheet. The link is only kept so the page can open it. It lives in the database, not in the repository, and
it is shown only when login is on: with login off anyone who knows the page address could read it."""
import json
import os
import re
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import auth, automation, db, service

SHEET_RE = re.compile(r"^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,100})(?:[/?#].*)?$")
SCRIPT_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "integrations", "google_sheets_apps_script.gs")
APP_URL_LINE = re.compile(r"(APP_URL:\s*')[^']*(')")


class SheetBody(BaseModel):
    url: str = Field(default="", max_length=600)


def _get(conn, key: str):
    row = conn.execute("SELECT v FROM catalog_snapshot WHERE k=?", (key,)).fetchone()
    return row[0] if row else None


def _put(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO catalog_snapshot(k, v) VALUES(?, ?) ON CONFLICT (k) DO UPDATE SET v = excluded.v", (key, value))


def touch(conn, boxes: int, now: float | None = None) -> None:
    """Called when the sheet's script asks for the approved boxes: remember when, and how many it was given."""
    _put(conn, "sheet_last_fetch", json.dumps({"at": time.time() if now is None else now, "boxes": int(boxes)}))


def normalize(url: str) -> str | None:
    """The canonical link to the sheet (no query, no user markers), or None when it is not a Google Sheets link."""
    m = SHEET_RE.match((url or "").strip())
    return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/edit" if m else None


def install(app, s) -> None:
    router = APIRouter()

    def conn_dep():
        c = db.connect(s.db_path)
        try:
            yield c
        finally:
            c.close()

    @router.get("/api/sheet")
    def get_sheet(c=Depends(conn_dep)):
        last = _get(c, "sheet_last_fetch")
        on = auth.enforced(s)
        return {"enforced": on, "url": (_get(c, "sheet_url") or "") if on else "", "last_fetch": json.loads(last) if last else None,
                "export_on": automation.is_on(c, "sheet_export")}

    @router.put("/api/sheet")
    def put_sheet(body: SheetBody, request: Request, c=Depends(conn_dep)):
        if not auth.enforced(s):
            raise service.Conflict("Chưa lưu được link vì chưa bật đăng nhập. Hãy bật trước: đặt ADMIN_IDS trong Render.")
        if not body.url.strip():
            _put(c, "sheet_url", "")
            auth.audit(c, auth.who(request), "sheet_link_clear", ip=auth.client_ip(request))
            return {"url": ""}
        url = normalize(body.url)
        if not url:
            raise service.Invalid("Đây chưa phải link Google Sheets. Link bắt đầu bằng https://docs.google.com/spreadsheets/d/")
        _put(c, "sheet_url", url)
        auth.audit(c, auth.who(request), "sheet_link_save", ip=auth.client_ip(request))        # the link itself is not written to the log
        return {"url": url}

    @router.get("/api/sheet/script")
    def script(request: Request):
        """The sheet's script as plain text, with the address of this app written in, ready to paste into Extensions, Apps Script."""
        proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        origin = f"{proto}://{request.headers.get('host', request.url.netloc)}"
        if not re.fullmatch(r"https?://[A-Za-z0-9.-]+(:[0-9]{1,5})?", origin):
            raise service.Invalid("Không đọc được địa chỉ của trang này. Tải lại trang rồi thử lại.")
        with open(SCRIPT_FILE, encoding="utf-8") as f:
            text = f.read()
        text = APP_URL_LINE.sub(lambda m: m.group(1) + origin + m.group(2), text, count=1)
        return Response(text, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})

    app.include_router(router)

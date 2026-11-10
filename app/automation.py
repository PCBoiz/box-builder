"""Switchable automations. Each switch gates one behaviour of the app; the state lives in the database so it
survives restarts and is shared by everyone who opens the page.

Nothing here talks to Scenario Manager or Google Sheets. The app only *prepares* (a validated queue, a list of
approved boxes) and the signed-in userscript / the sheet's own script do the writing, under their own safety rules."""
import json
import time

from .catalog import Catalog
from .db import tx

# key -> default state, Vietnamese title/description for the page, and the allowed range of every number setting
META = {
    "ai_batch": {
        "title": "Tạo nhiều hộp bằng AI",
        "desc": "Cho phép gõ một mô tả chung, để AI chia thành nhiều hộp (mỗi hộp một bối cảnh) rồi tạo lần lượt, không trùng món.",
        "enabled": True, "config": {}, "limits": {},
    },
    "auto_approve": {
        "title": "Tự duyệt hộp khi đã chọn đủ",
        "desc": "Hộp do mô hình chọn đủ số món (không có món do hệ thống tự điền) sẽ được duyệt luôn, khỏi bấm Duyệt. "
                "Tắt thì bạn tự xem từng hộp trước khi duyệt.",
        "enabled": False, "config": {}, "limits": {},
    },
    "sheet_export": {
        "title": "Cấp danh sách cho bảng tính Google",
        "desc": "Cho phép script của bảng tính lấy các hộp đã duyệt để điền vào ô. Script chỉ ghi vào ô còn trống, không bao giờ ghi đè.",
        "enabled": True, "config": {}, "limits": {},
    },
    "catalog_watch": {
        "title": "Theo dõi kho đồ",
        "desc": "Cảnh báo khi kho chưa đồng bộ lâu, khi món trong hộp biến mất khỏi kho SM, hoặc khi một nhóm đồ sắp hết.",
        "enabled": True, "config": {"stale_hours": 24, "low_stock": 20},
        "limits": {"stale_hours": (1, 720), "low_stock": (1, 500)},
    },
    "reuse_old": {
        "title": "Dùng lại món của hộp cũ",
        "desc": "Món nằm trong một hộp SM (hoặc trong danh sách đã dùng ở bảng tính) được coi là trống lại khi hộp đó đã tạo cách đây quá số ngày dưới đây, "
                "vì hộp đã xa ngày làm hộp mới. Tắt thì mọi món đã dùng đều bị giữ mãi. Món trong hộp của app này luôn được giữ.",
        "enabled": True, "config": {"days": 30}, "limits": {"days": (1, 365)},
    },
    "auto_heal": {
        "title": "Tự thay món biến mất khỏi kho SM",
        "desc": "Khi đồng bộ kho cho thấy SM không còn một món trong hộp chưa đẩy lên SM, món đó được thay bằng món khác "
                "và các món còn lại giữ nguyên. Hộp đã duyệt quay về Nháp để bạn duyệt lại (trừ khi bật tự duyệt). Hộp đã đẩy lên SM không bị đụng tới.",
        "enabled": False, "config": {}, "limits": {},
    },
    "sm_queue": {
        "title": "Hàng chờ đẩy lên SM",
        "desc": "Kiểm tra từng hộp đã duyệt (món còn tồn kho, số lượng hợp lệ, tiêu đề không trùng) trước khi công cụ đồng bộ trong SM thấy nó. "
                "Việc tạo TaskBox trong SM vẫn do bạn bấm xác nhận.",
        "enabled": True, "config": {}, "limits": {},
    },
}
LOG_KEEP = 200


class BadSetting(Exception):
    pass


def _stored(conn) -> dict:
    out = {}
    for r in conn.execute("SELECT k, enabled, config FROM automations"):
        try:
            cfg = json.loads(r["config"] or "{}")
        except ValueError:
            cfg = {}
        out[r["k"]] = (bool(r["enabled"]), cfg if isinstance(cfg, dict) else {})
    return out


def switches(conn) -> dict:
    """Every automation with its current state (defaults filled in for anything never changed)."""
    stored = _stored(conn)
    out = {}
    for key, m in META.items():
        enabled, cfg = stored.get(key, (m["enabled"], {}))
        out[key] = {"key": key, "title": m["title"], "desc": m["desc"], "enabled": enabled,
                    "config": {**m["config"], **{k: v for k, v in cfg.items() if k in m["config"]}},
                    "limits": {k: list(v) for k, v in m["limits"].items()}}
    return out


def is_on(conn, key: str) -> bool:
    return switches(conn)[key]["enabled"]


def config_of(conn, key: str) -> dict:
    return switches(conn)[key]["config"]


def set_switch(conn, key: str, enabled: bool | None = None, config: dict | None = None) -> dict:
    if key not in META:
        raise BadSetting(f"Không có tự động hoá '{key}'.")
    cur = switches(conn)[key]
    new_enabled = cur["enabled"] if enabled is None else bool(enabled)
    new_cfg = dict(cur["config"])
    for k, v in (config or {}).items():
        lim = META[key]["limits"].get(k)
        if lim is None:
            raise BadSetting(f"'{k}' không phải là thiết lập của '{key}'.")
        if isinstance(v, bool) or not isinstance(v, int) or not lim[0] <= v <= lim[1]:
            raise BadSetting(f"'{k}' phải là số nguyên từ {lim[0]} đến {lim[1]}.")
        new_cfg[k] = v
    with tx(conn):
        conn.execute(
            "INSERT INTO automations(k, enabled, config, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT (k) DO UPDATE SET enabled = excluded.enabled, config = excluded.config, updated_at = excluded.updated_at",
            (key, int(new_enabled), json.dumps(new_cfg), time.time()))
        if enabled is not None and enabled != cur["enabled"]:
            _log(conn, "switch", f"{META[key]['title']}: {'BẬT' if new_enabled else 'TẮT'}")
    return switches(conn)[key]


# ----------------------------------------------------------------------------- activity log
def _log(conn, kind: str, msg: str) -> None:
    """Call inside a tx()."""
    conn.execute("INSERT INTO automation_log(at, kind, msg) VALUES(?,?,?)", (time.time(), kind, msg[:400]))
    conn.execute("DELETE FROM automation_log WHERE id <= (SELECT MAX(id) FROM automation_log) - ?", (LOG_KEEP,))


def log(conn, kind: str, msg: str) -> None:
    with tx(conn):
        _log(conn, kind, msg)


def recent_log(conn, n: int = 25) -> list[dict]:
    rows = conn.execute("SELECT at, kind, msg FROM automation_log ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    return [{"at": r["at"], "kind": r["kind"], "msg": r["msg"]} for r in rows]


# ----------------------------------------------------------------------------- catalog watch
def alerts(conn, catalog: Catalog, taken: set[str]) -> list[dict]:
    """Things worth a look. Empty when the watch switch is off."""
    sw = switches(conn)["catalog_watch"]
    if not sw["enabled"]:
        return []
    cfg = sw["config"]
    out = []
    info = catalog.info()
    if info["source"] != "live":
        out.append({"kind": "stale", "level": "warn",
                    "msg": "Kho chưa đồng bộ với SM, đang dùng file CSV. Mở SM và bấm \"Đồng bộ kho ngay\" ở khung Box builder."})
    else:
        hours = (time.time() - info["synced_at"]) / 3600
        if hours >= cfg["stale_hours"]:
            out.append({"kind": "stale", "level": "warn",
                        "msg": f"Kho SM cập nhật lần cuối cách đây {hours:.0f} giờ (ngưỡng {cfg['stale_hours']} giờ)."})
    from . import service
    rep = service.sm_report(conn, catalog)
    if rep["conflicts"]:
        n = sum(len(c["objects"]) for c in rep["conflicts"])
        out.append({"kind": "sm_conflict", "level": "bad",
                    "msg": f"{n} món trong {len(rep['conflicts'])} hộp của bạn đã nằm trong hộp khác ở SM hoặc trong danh sách đã dùng ở bảng tính: "
                           + "; ".join(f"{c['box']} ({', '.join(o['id'] for o in c['objects'][:3])})" for c in rep["conflicts"][:3])})
    if rep["doubles"]:
        out.append({"kind": "sm_doubles", "level": "warn",
                    "msg": f"{len(rep['doubles'])} món đang nằm trong hai hộp SM trở lên: " + ", ".join(d["id"] for d in rep["doubles"][:6]) + (" ..." if len(rep["doubles"]) > 6 else "")})
    items = catalog.items()
    claimed = {r[0] for r in conn.execute("SELECT object_id FROM box_items")}
    gone = sorted(claimed - set(items))
    if gone:
        out.append({"kind": "gone", "level": "bad",
                    "msg": f"{len(gone)} món đang nằm trong hộp nhưng SM không còn liệt kê: {', '.join(gone[:8])}"
                           + (" ..." if len(gone) > 8 else "")})
    total: dict[str, int] = {}
    free: dict[str, int] = {}
    for it in items.values():
        c = it.category or "(chưa phân loại)"
        total[c] = total.get(c, 0) + 1
        if it.id not in taken:
            free[c] = free.get(c, 0) + 1
    low = sorted(((free.get(c, 0), c) for c, t in total.items() if t >= cfg["low_stock"] and free.get(c, 0) < cfg["low_stock"]))
    if low:
        out.append({"kind": "low", "level": "warn",
                    "msg": "Nhóm sắp hết đồ trống (dưới " + str(cfg["low_stock"]) + " món): "
                           + ", ".join(f"{c} ({n})" for n, c in low[:6]) + (" ..." if len(low) > 6 else "")})
    return out


# ----------------------------------------------------------------------------- what is waiting for a person
def todo(conn, catalog: Catalog, max_qty: int) -> list[dict]:
    """The steps that still need a human, in the order they are worth doing. Nothing here is an error: the rules say a
    person confirms the SM push and the sheet script runs in the sheet, so the app names the step instead of hiding it."""
    from . import service
    st = service.state(conn, catalog)
    boxes = st["boxes"]
    sw = switches(conn)
    out = []

    def add(kind, level, msg, where):
        out.append({"kind": kind, "level": level, "msg": msg, "where": where})

    errors = [b for b in boxes if b["status"] == "error"]
    if errors:
        add("error", "bad", f"{len(errors)} hộp bị lỗi: " + ", ".join(f"{b['module']} {b['label']}" for b in errors[:5]), "Hộp")
    drafts = [b for b in boxes if b["status"] == "draft"]
    if drafts:
        how = "Bật công tắc Tự duyệt nếu muốn bỏ bước này." if not sw["auto_approve"]["enabled"] else "Các hộp này có món do hệ thống tự điền nên cần bạn xem."
        add("review", "warn", f"{len(drafts)} hộp nháp chờ bạn xem và duyệt. {how}", "Hộp")
    q = service.sm_queue(conn, catalog, max_qty)
    if q["blocked"]:
        add("blocked", "bad", f"{len(q['blocked'])} hộp đã duyệt bị chặn khi đẩy lên SM: "
            + "; ".join(f"{b['title']} ({b['reasons'][0]})" for b in q["blocked"][:3]), "Tự động hoá")
    if q["ready"] and sw["sm_queue"]["enabled"]:
        add("push", "warn", f"{len(q['ready'])} hộp sẵn sàng đẩy lên SM. Mở SM, bấm Quét dữ liệu ở khung Box builder, xem danh sách rồi bấm Đồng bộ.", "SM")
    approved = [b for b in boxes if b["status"] == "approved"]
    if approved and sw["sheet_export"]["enabled"]:
        add("sheet", "info", f"{len(approved)} hộp đã duyệt sẵn sàng cho bảng tính. Trong bảng tính: menu Box builder, Điền các box đã duyệt "
            "(hoặc bật chạy tự động 30 phút một lần).", "Bảng tính")
    info = catalog.info()
    if info["source"] != "live":
        add("sync", "warn", "Kho chưa đồng bộ với SM. Mở SM, rồi bấm Đồng bộ kho ngay ở khung Box builder.", "SM")
    rep = service.sm_report(conn, catalog)
    if rep["conflicts"]:
        add("sm_conflict", "bad", f"{len(rep['conflicts'])} hộp có món đã nằm trong hộp khác ở SM hoặc trong danh sách đã dùng ở bảng tính. Mở hộp và thay các món đó.", "Hộp")
    stale = sw["catalog_watch"]["config"]["stale_hours"] * 3600
    if rep["synced_at"] is None:
        add("sm_boxes", "info", "Chưa đọc các hộp đang có trong SM, nên món đang nằm trong hộp SM vẫn có thể bị chọn trùng. Dùng công cụ đồng bộ trong SM.", "SM")
    elif time.time() - rep["synced_at"] > stale:
        add("sm_boxes", "warn", "Danh sách hộp đang có trong SM đã cũ. Mở SM và đồng bộ lại để tránh chọn trùng.", "SM")
    return out


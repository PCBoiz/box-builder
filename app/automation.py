"""Switchable automations. Each switch gates one behaviour of the app; the state lives in the database so it
survives restarts and is shared by everyone who opens the page.

Nothing here talks to Scenario Manager or Google Sheets. The app only *prepares* (a validated queue, a list of
approved boxes) and the signed-in userscript / the sheet's own script do the writing, under their own safety rules."""
import json
import time

from . import usage
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
        "desc": "Khi bật, hộp được AI chọn đủ món sẽ tự được duyệt, không cần bấm Duyệt. Hộp có món app điền thêm vẫn chờ người làm xem. "
                "Khi tắt, mỗi người tự xem và duyệt hộp của mình.",
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
        "desc": "Món trong hộp SM (hoặc trong danh sách đã dùng ở bảng tính) được dùng lại khi hộp đó đã tạo quá số ngày dưới đây. "
                "Tắt thì món đã dùng bị giữ mãi. Món trong hộp của app này luôn được giữ.",
        "enabled": True, "config": {"days": 30}, "limits": {"days": (1, 365)},
    },
    "usage_penalty": {
        "title": "Giảm món đã dùng nhiều",
        "desc": "Món có nhiều lượt mượn được chọn ít hơn. Từ mức bắt đầu giảm (mặc định 500 lượt), cứ thêm 300 lượt thì cơ hội được chọn giảm một nửa, thấp nhất còn 5%. "
                "Món từ mức loại hẳn trở lên không được chọn nữa (0 là không loại món nào). Bạn vẫn thêm tay được mọi món. "
                "Tắt thì mọi món có cơ hội như nhau.",
        "enabled": True,
        "config": {"soft_start": 500, "half_life": 300, "hard_limit": 0, "age_effect": 50, "min_pool": 100},
        "limits": {"soft_start": (1, 100000), "half_life": (1, 5000), "hard_limit": (0, 100000), "age_effect": (0, 100), "min_pool": (1, 1000)},
    },
    "auto_heal": {
        "title": "Tự thay món biến mất khỏi kho SM",
        "desc": "Khi SM không còn một món trong hộp (hộp chưa lên SM), app thay bằng món khác và giữ nguyên các món còn lại. "
                "Hộp đã duyệt quay về Nháp để duyệt lại, trừ khi đang bật tự duyệt. Hộp đã lên SM không bị đổi.",
        "enabled": False, "config": {}, "limits": {},
    },
    "sm_queue": {
        "title": "Hàng chờ lên SM",
        "desc": "Kiểm tra từng hộp đã duyệt (món còn tồn kho, số lượng hợp lệ, tiêu đề không trùng) trước khi công cụ đồng bộ trong SM thấy nó. "
                "Mặc định tắt: khi tắt, công cụ trong SM không thấy hộp nào. Việc tạo TaskBox trong SM vẫn do bạn bấm xác nhận.",
        "enabled": False, "config": {}, "limits": {},
    },
}
# the labels of the number boxes, the same words as CFG_LABEL in app.js
LABEL = {"stale_hours": "Cảnh báo khi kho cũ hơn (giờ)", "low_stock": "Cảnh báo khi một nhóm còn dưới (món)",
         "days": "Dùng lại món của hộp tạo cách đây hơn (ngày)", "soft_start": "Mức bắt đầu giảm (lượt mượn)",
         "half_life": "Cứ thêm bao nhiêu lượt mượn thì cơ hội giảm một nửa", "hard_limit": "Mức loại hẳn (lượt mượn, 0 là không loại món nào)",
         "age_effect": "Ảnh hưởng của tuổi món (%)", "min_pool": "Số món tối thiểu còn để chọn"}
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
            raise BadSetting(f"Ô \"{LABEL.get(k, k)}\" phải là số nguyên từ {lim[0]} đến {lim[1]}.")
        new_cfg[k] = v
    if key == "usage_penalty" and new_cfg["hard_limit"] and new_cfg["hard_limit"] < new_cfg["soft_start"]:
        raise BadSetting(f"Mức loại hẳn ({new_cfg['hard_limit']} lượt mượn) thấp hơn mức bắt đầu giảm ({new_cfg['soft_start']} lượt mượn), "
                         f"nên món bị loại trước khi bị giảm. Hãy đặt mức loại hẳn từ {new_cfg['soft_start']} lượt mượn trở lên, hoặc đặt 0 để không loại món nào.")
    with tx(conn):
        conn.execute(
            "INSERT INTO automations(k, enabled, config, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT (k) DO UPDATE SET enabled = excluded.enabled, config = excluded.config, updated_at = excluded.updated_at",
            (key, int(new_enabled), json.dumps(new_cfg), time.time()))
        if enabled is not None and enabled != cur["enabled"]:
            _log(conn, "switch", f"{META[key]['title']}: {'đã bật' if new_enabled else 'đã tắt'}")
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
                        "msg": f"Kho chưa cập nhật từ SM {hours:.0f} giờ (cảnh báo từ {cfg['stale_hours']} giờ). "
                               "Mở SM, bấm \"Đồng bộ kho ngay\" ở khung Box builder."})
    from . import service
    rep = service.sm_report(conn, catalog)
    if rep["conflicts"]:
        n = sum(len(c["objects"]) for c in rep["conflicts"])
        out.append({"kind": "sm_conflict", "level": "bad",
                    "msg": f"{n} món trong {len(rep['conflicts'])} hộp đã nằm ở hộp khác (trong SM hoặc bảng tính): "
                           + "; ".join(f"{c['box']} ({', '.join(o['id'] for o in c['objects'][:3])})" for c in rep["conflicts"][:3])
                           + ". Mở hộp và thay các món đó."})
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
    up = switches(conn)["usage_penalty"]
    if up["enabled"]:
        st = usage.summary(items.values(), time.time(), up["config"])
        if st["over_start"] > 0:
            out.append({"kind": "heavy", "level": "warn",
                        "msg": f"{st['over_start']} món có từ {up['config']['soft_start']} lượt mượn trở lên: "
                               f"{st['reduced']} món được chọn ít hơn, {st['excluded']} món bị loại."})
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
        add("error", "bad", f"{len(errors)} hộp chưa chọn được đồ: " + ", ".join(f"{b['module']} {b['label']}" for b in errors[:5])
            + ". Mở hộp, bấm Chọn lại.", "Hộp")
    drafts = [b for b in boxes if b["status"] == "draft"]
    if drafts:
        add("review", "info", f"{len(drafts)} hộp nháp chưa được duyệt. Người làm hộp tự duyệt hộp của mình.", "Hộp")
    q = service.sm_queue(conn, catalog, max_qty)
    if q["blocked"]:
        add("blocked", "bad", f"{len(q['blocked'])} hộp đã duyệt bị chặn nên chưa lên SM được: "
            + "; ".join(f"{b['title']} ({b['reasons'][0]})" for b in q["blocked"][:3]), "Tự động hoá")
    if q["ready"] and sw["sm_queue"]["enabled"]:
        add("push", "warn", f"{len(q['ready'])} hộp sẵn sàng lên SM. Mở SM, bấm Quét dữ liệu ở khung Box builder, xem danh sách rồi bấm Đồng bộ.", "SM")
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


"""The paper supporters get at 10:00 to gather the objects of approved boxes.

Pure rendering: boxes in, one self-contained HTML5 document out. No clock, no database, no network.
The caller passes the date text. Names, shelf locations, titles and picture links come from another system
(Scenario Manager and the sheet), so every dynamic string is escaped and every picture link is checked.

Box dicts are the ones service.state() builds. Only these keys are read, and every one is optional:
  box:  id, module, label, code, anchor, title, items
  item: pos, id, qty, name, vi, slot, img
"""
import html
import re
import unicodedata

from . import images

__all__ = ["render_pick_list", "collect_rows", "sort_key_for_slot", "safe_image_url", "NO_SLOT_TEXT"]

NO_SLOT_TEXT = "chưa rõ vị trí"
COMBINED_TITLE = "Danh sách gộp theo kệ"
EMPTY_TEXT = "Chưa có hộp đã duyệt để in."

MAX_TEXT = 300          # a name longer than this is cut, so one bad row cannot blow up the page
MAX_URL = 4096          # a signed S3 link carries a long query string
_URL_BAD = re.compile(r"[\s\"'<>`\\\x00-\x1f\x7f]")
_TOKEN = re.compile(r"[0-9]+|[^\W_0-9]+")   # digit runs, and letter runs (separators such as - _ . space are dropped)


# ----------------------------------------------------------------------------- cleaning
def _text(value, limit: int | None = MAX_TEXT) -> str:
    """Any value to a short plain string: no control or invisible characters (a right-to-left override can
    reverse a name on paper), whitespace collapsed. Escaping for HTML is a separate step at render time."""
    if value is None:
        return ""
    s = value if isinstance(value, str) else str(value)
    s = "".join(" " if c.isspace() else c for c in s if unicodedata.category(c) not in ("Cc", "Cf") or c.isspace())
    s = " ".join(s.split())
    if limit is not None and len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return s


def _esc(value) -> str:
    return html.escape(_text(value), quote=True)


def _int(value) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def safe_image_url(url) -> str:
    """The URL when it is safe to put in <img src>, else ''. Only https, no quote, angle bracket, backtick,
    backslash, whitespace or control character, no user info before the host."""
    if not isinstance(url, str):
        return ""
    u = url.strip()
    if not u.startswith("https://") or len(u) > MAX_URL or len(u) <= len("https://"):
        return ""
    if _URL_BAD.search(u):
        return ""
    host = u[len("https://"):].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if not host or "@" in host:
        return ""
    return u


# ----------------------------------------------------------------------------- shelf order
def _has_slot(slot) -> bool:
    return any(c.isalnum() for c in _text(slot))


def sort_key_for_slot(slot) -> tuple:
    """Natural order for shelf locations: letters first, then the number, case-insensitive.
    AM04 < AN08 < AN09 < AN10 and A2 < A10. A location that starts with a digit comes after the lettered ones.
    A missing location, or one with no letter or digit in it, sorts last. Equal keys keep their input order
    when used with sorted()."""
    tokens = _TOKEN.findall(_text(slot).casefold())
    if not tokens:
        return (2, ())
    chunks = []
    for t in tokens:
        if t[0] in "0123456789":
            z = t.lstrip("0")
            chunks.append((1, len(z), z))      # numbers compare by length, then digits: no int() needed, no size limit
        else:
            chunks.append((0, t))
    return (0 if tokens[0][0] not in "0123456789" else 1, tuple(chunks))


# ----------------------------------------------------------------------------- data
def _boxes(boxes) -> list[dict]:
    return [b for b in (boxes or []) if isinstance(b, dict)]


def _box_items(box: dict) -> list[dict]:
    items = box.get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, (list, tuple)) else []


def _box_tag(box: dict) -> str:
    """Short name of a box for the combined list: module, unit code (when it adds something), label."""
    module, code, label = _text(box.get("module")), _text(box.get("code")), _text(box.get("label"))
    parts = [module]
    if code and code != module:
        parts.append(code)
    parts.append(label)
    return " · ".join(p for p in parts if p)


def _rows_of(box: dict) -> list[dict]:
    """The items of one box in shelf order (stable). 'stt' is the original position in the box."""
    rows = []
    for n, it in enumerate(_box_items(box), start=1):
        pos = it.get("pos")
        rows.append({
            "stt": pos if isinstance(pos, int) and not isinstance(pos, bool) else n,
            "id": _text(it.get("id")), "name": _text(it.get("name")), "vi": _text(it.get("vi")),
            "slot": _text(it.get("slot")), "qty": _int(it.get("qty")),
            "img": "" if images.is_expired(it.get("img_exp")) else safe_image_url(it.get("img")),
        })
    return sorted(rows, key=lambda r: sort_key_for_slot(r["slot"]))


def collect_rows(boxes) -> list[dict]:
    """Every item of every box as one list sorted by shelf location (stable: ties keep box order, then item order).
    Row keys: slot, id, vi, name, qty, box (display text), module, code, label, box_id. Values are cleaned text,
    not HTML. A box that is passed twice shows its items twice: boxes never share objects, so the app does not."""
    rows = []
    for b in _boxes(boxes):
        tag, module, code, label = _box_tag(b), _text(b.get("module")), _text(b.get("code")), _text(b.get("label"))
        for it in _box_items(b):
            rows.append({
                "slot": _text(it.get("slot")), "id": _text(it.get("id")), "vi": _text(it.get("vi")),
                "name": _text(it.get("name")), "qty": _int(it.get("qty")), "box": tag,
                "module": module, "code": code, "label": label, "box_id": b.get("id"),
            })
    return sorted(rows, key=lambda r: sort_key_for_slot(r["slot"]))


# ----------------------------------------------------------------------------- html pieces
CSS = """
@page { size: A4; margin: 12mm 10mm; }
* { box-sizing: border-box; }
html { color-scheme: light; }
body { margin: 0; background: #fff; color: #111; font: 12px/1.4 system-ui, "Segoe UI", Roboto, Arial, sans-serif; }
.toolbar { position: sticky; top: 0; z-index: 1; display: flex; flex-wrap: wrap; align-items: center; gap: 8px 14px;
  padding: 10px 16px; background: #f3f4f6; border-bottom: 1px solid #c8ccd2; font-size: 14px; }
.toolbar button { font: inherit; font-weight: 600; padding: 7px 16px; border: 1px solid #1f2937; border-radius: 6px;
  background: #1f2937; color: #fff; cursor: pointer; }
.toolbar button:hover { background: #111827; }
.toolbar span { color: #4b5563; }
main { max-width: 210mm; margin: 0 auto; padding: 14px 16px 40px; }
h1 { margin: 0 0 2px; font-size: 20px; }
h2 { margin: 0 0 4px; font-size: 17px; }
p { margin: 0 0 6px; }
.eyebrow, .meta { color: #444; font-size: 11px; }
.facts { display: flex; flex-wrap: wrap; gap: 2px 18px; margin: 0 0 10px; padding: 0; list-style: none; }
.facts b { font-weight: 600; }
.box, .combined { margin-bottom: 28px; }
table { width: 100%; border-collapse: collapse; table-layout: auto; }
th, td { border: 1px solid #333; padding: 3px 6px; text-align: left; vertical-align: middle; overflow-wrap: anywhere; }
th { background: #e5e7eb; font-weight: 600; }
td.num, td.qty, th.num, th.qty { text-align: right; white-space: nowrap; }
td.id, td.slot { white-space: nowrap; font-variant-numeric: tabular-nums; }
td.slot { font-weight: 600; }
td.unknown { font-weight: 400; font-style: italic; color: #444; white-space: normal; }
td.pic { width: 46px; text-align: center; padding: 2px; }
td.pic img { display: block; width: 40px; height: 40px; margin: 0 auto; object-fit: contain; }
.totals { margin: 8px 0 0; font-weight: 600; }
.empty { margin-top: 24px; padding: 18px; border: 1px dashed #999; border-radius: 8px; font-size: 14px; }
thead { display: table-header-group; }
tr { break-inside: avoid; page-break-inside: avoid; }
.box { break-before: page; page-break-before: always; }
@media print {
  .toolbar { display: none; }
  main { max-width: none; padding: 0; }
  .box, .combined { margin-bottom: 0; }
  h1, h2 { break-after: avoid; page-break-after: avoid; }
}
"""


def _fact(label: str, value) -> str:
    v = _esc(value)
    return f"<li><b>{label}:</b> {v}</li>" if v else ""


def _table(head: list[tuple[str, str]], body: list[str]) -> str:
    ths = "".join(f'<th class="{c}">{t}</th>' if c else f"<th>{t}</th>" for t, c in head)
    return f"<table><thead><tr>{ths}</tr></thead><tbody>\n{''.join(body)}</tbody></table>"


def _slot_cell(slot: str, base: str = "slot") -> str:
    if _has_slot(slot):
        return f'<td class="{base}">{_esc(slot)}</td>'
    return f'<td class="{base} unknown">{NO_SLOT_TEXT}</td>'


def _combined_section(boxes: list[dict], heading: str, generated_at: str) -> str:
    rows = collect_rows(boxes)
    body = [
        f'<tr>{_slot_cell(r["slot"])}<td class="id">{_esc(r["id"])}</td><td>{_esc(r["vi"])}</td>'
        f'<td>{_esc(r["name"])}</td><td class="qty">{r["qty"]}</td><td>{_esc(r["box"])}</td></tr>\n'
        for r in rows
    ]
    head = [("Vị trí kệ", ""), ("ID", ""), ("Tên tiếng Việt", ""), ("Tên tiếng Anh", ""), ("SL", "qty"), ("Hộp", "")]
    date = f" · {_esc(generated_at)}" if _text(generated_at) else ""
    return (
        '<section class="combined">\n'
        f'<p class="eyebrow">{_esc(heading)}{date}</p>\n'
        f"<h1>{COMBINED_TITLE}</h1>\n"
        f'<p class="meta">{len(boxes)} hộp, {len(rows)} món, số lượng {sum(r["qty"] for r in rows)}. '
        "Đi một vòng kho theo thứ tự kệ, rồi chia đồ về từng hộp.</p>\n"
        f"{_table(head, body)}\n"
        f'<p class="totals">Tổng cộng: {len(rows)} món, số lượng {sum(r["qty"] for r in rows)}</p>\n'
        "</section>\n"
    )


def _box_section(box: dict, heading: str, generated_at: str) -> str:
    rows = _rows_of(box)
    name = " · ".join(p for p in (_text(box.get("module")), _text(box.get("label"))) if p) or "Hộp chưa đặt tên"
    facts = "".join([
        _fact("Module", box.get("module")), _fact("Hộp", box.get("label")), _fact("Mã section", box.get("code")),
        _fact("Đồ lớn", box.get("anchor")), _fact("Tiêu đề TaskBox", box.get("title")), _fact("Ngày", generated_at),
    ])
    out = [
        '<section class="box">\n',
        f'<p class="eyebrow">{_esc(heading)}</p>\n',
        f"<h2>{html.escape(name, quote=True)}</h2>\n",
        f'<ul class="facts">{facts}</ul>\n',
    ]
    if not rows:
        out.append('<p class="meta">Hộp này chưa có món.</p>\n')
    else:
        with_pics = any(r["img"] for r in rows)
        body = []
        for r in rows:
            pic = ""
            if with_pics:
                alt = _esc(r["vi"] or r["name"])
                pic = (f'<td class="pic"><img src="{html.escape(r["img"], quote=True)}" alt="{alt}" width="40" height="40" '
                       'loading="lazy" referrerpolicy="no-referrer"></td>') if r["img"] else '<td class="pic"></td>'
            body.append(
                f'<tr><td class="num">{r["stt"]}</td><td class="id">{_esc(r["id"])}</td><td>{_esc(r["vi"])}</td>'
                f'<td>{_esc(r["name"])}</td>{_slot_cell(r["slot"])}<td class="qty">{r["qty"]}</td>{pic}</tr>\n')
        head = [("STT", "num"), ("ID", ""), ("Tên tiếng Việt", ""), ("Tên tiếng Anh", ""), ("Vị trí kệ", ""), ("SL", "qty")]
        if with_pics:
            head.append(("Ảnh", "pic"))
        out.append(_table(head, body) + "\n")
        out.append(f'<p class="totals">Tổng cộng: {len(rows)} món, số lượng {sum(r["qty"] for r in rows)}</p>\n')
    out.append("</section>\n")
    return "".join(out)


def render_pick_list(boxes: list[dict], generated_at: str = "", heading: str = "Phiếu lấy đồ", combined: bool = True) -> str:
    """One complete HTML5 page. With two or more boxes and combined=True it opens with a single list of every
    object sorted by shelf. Then each box gets its own section that starts a new printed page.
    Same input, same string."""
    boxes = _boxes(boxes)
    when = _text(generated_at)
    if not boxes:
        sections = (f'<h1>{_esc(heading)}</h1>\n<div class="empty"><p>{EMPTY_TEXT}</p>'
                    "<p>Duyệt hộp trong app, rồi mở lại phiếu này.</p></div>\n")
    else:
        parts = []
        if combined and len(boxes) > 1:
            parts.append(_combined_section(boxes, heading, when))
        parts.extend(_box_section(b, heading, when) for b in boxes)
        sections = "".join(parts)
    return (
        "<!DOCTYPE html>\n"
        '<html lang="vi">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="referrer" content="no-referrer">\n'
        f"<title>{_esc(heading)}</title>\n"
        f"<style>{CSS}</style>\n"
        "</head>\n<body>\n"
        '<div class="toolbar"><button type="button" onclick="window.print()">In phiếu</button>'
        "<span>Hoặc nhấn Ctrl+P</span></div>\n"
        f"<main>\n{sections}</main>\n"
        "</body>\n</html>\n"
    )

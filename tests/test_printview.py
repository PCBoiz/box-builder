"""The 10:00 pick list: pure rendering, no app fixtures."""
import random
import re
from html.parser import HTMLParser

import pytest

from app.printview import collect_rows, render_pick_list, safe_image_url, sort_key_for_slot

S3 = ("https://configint-main.s3.amazonaws.com/objects/004702/photo.jpg?X-Amz-Algorithm=AWS4-HMAC-SHA256"
      "&X-Amz-Credential=AKIAEXAMPLE%2F20261110%2Fap-southeast-1%2Fs3%2Faws4_request&X-Amz-Date=20261110T030000Z"
      "&X-Amz-Expires=3600&X-Amz-SignedHeaders=host&X-Amz-Signature=" + "ab12" * 16)
NO_SLOT = "chưa rõ vị trí"


def item(oid, slot="AM04", qty=1, pos=1, name="Pan", vi="Chảo", img=""):
    return {"pos": pos, "id": oid, "qty": qty, "name": name, "vi": vi, "category": "Bếp", "slot": slot,
            "stock": 5, "img": img, "detail": "https://sm.config.inc/object/" + oid}


def box(items, module="M8", label="Box B2", code="M8-02", anchor="Tủ lạnh", title="M8-02 - Tủ lạnh - B2", bid=1):
    return {"id": bid, "module": module, "label": label, "code": code, "anchor": anchor, "title": title,
            "status": "approved", "count": len(items), "owner": "An", "items": items}


def two_boxes():
    return [
        box([item("000003", "AN10", 2, 1), item("000001", "AM04", 1, 2), item("000002", "", 3, 3)], bid=1),
        box([item("000013", "AN09", 1, 1), item("000011", "A2", 4, 2), item("000012", "A10", 1, 3)],
            module="M9", label="Box A1", code="M9-01", anchor="", title="M9-01 - A1", bid=2),
    ]


class Tags(HTMLParser):
    """Records every start tag with its attributes, and complains about unbalanced or unknown nesting."""
    VOID = {"meta", "img", "br", "hr", "link", "input"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags, self.text, self.stack, self.errors = [], [], [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.VOID:
            self.errors.append(f"end tag for void element {tag}")
        elif not self.stack or self.stack[-1] != tag:
            self.errors.append(f"unexpected </{tag}> with {self.stack[-3:]} open")
        else:
            self.stack.pop()

    def handle_data(self, data):
        self.text.append(data)

    def error(self, message):  # pragma: no cover
        self.errors.append(message)


def parse(page):
    p = Tags()
    p.feed(page)
    p.close()
    assert not p.errors, p.errors
    assert not p.stack, p.stack
    return p


def tag_attrs(page, name):
    return [a for t, a in parse(page).tags if t == name]


def visible_text(page):
    return "".join(parse(page).text)


def tables_of(page):
    """Each table as a list of rows, each row a list of cell texts (header row first)."""
    out, cur_table, cur_row, cell = [], None, None, None

    class T(HTMLParser):
        def handle_starttag(s, tag, attrs):
            nonlocal cur_table, cur_row, cell
            if tag == "table":
                cur_table = []
            elif tag == "tr":
                cur_row = []
            elif tag in ("td", "th"):
                cell = []

        def handle_endtag(s, tag):
            nonlocal cur_table, cur_row, cell
            if tag in ("td", "th") and cell is not None:
                cur_row.append("".join(cell).strip())
                cell = None
            elif tag == "tr":
                cur_table.append(cur_row)
            elif tag == "table":
                out.append(cur_table)
                cur_table = None

        def handle_data(s, data):
            if cell is not None:
                cell.append(data)

    T().feed(page)
    return out


# ----------------------------------------------------------------------------- shelf order
def test_natural_sort_letters_then_number():
    slots = ["AN10", "AM04", "AN09", "AN08"]
    assert sorted(slots, key=sort_key_for_slot) == ["AM04", "AN08", "AN09", "AN10"]
    assert sort_key_for_slot("A2") < sort_key_for_slot("A10")
    assert sort_key_for_slot("A9") < sort_key_for_slot("A10") < sort_key_for_slot("A100")
    assert sort_key_for_slot("AM04") < sort_key_for_slot("AN08")
    assert sort_key_for_slot("B1") > sort_key_for_slot("A99")
    assert sort_key_for_slot("A1") < sort_key_for_slot("AA1")


def test_sort_is_case_insensitive_and_stable():
    assert sort_key_for_slot("am04") == sort_key_for_slot("AM04")
    assert sorted(["an10", "AM04", "An09"], key=sort_key_for_slot) == ["AM04", "An09", "an10"]
    rows = [("a2", 1), ("A2", 2), ("a02", 3), ("A1", 4)]
    assert [n for _, n in sorted(rows, key=lambda r: sort_key_for_slot(r[0]))] == [4, 1, 2, 3]


def test_separators_and_spaces_do_not_break_the_order():
    assert sort_key_for_slot(" AM 04 ") == sort_key_for_slot("AM04") == sort_key_for_slot("AM-04")
    assert sort_key_for_slot("AM-04") < sort_key_for_slot("AM05")
    assert sort_key_for_slot("A1-2") < sort_key_for_slot("A1-10") < sort_key_for_slot("A2")
    assert sort_key_for_slot("A1") < sort_key_for_slot("A1-1")


def test_missing_and_odd_locations_go_last():
    last = sort_key_for_slot("ZZ999")
    for odd in ("", "   ", None, "-", "???", "_", "​", "\t\n"):
        assert sort_key_for_slot(odd) > last, repr(odd)
    assert sort_key_for_slot("") == sort_key_for_slot(None) == sort_key_for_slot("--")
    # a location that starts with a number is a real location: after lettered ones, before missing
    assert sort_key_for_slot("ZZ999") < sort_key_for_slot("12") < sort_key_for_slot("")
    assert sort_key_for_slot("12") < sort_key_for_slot("100")


def test_sort_key_survives_strange_input():
    for odd in (12, 3.5, b"x".decode(), "9" * 5000, "A" + "9" * 5000, "Ａ１", "²", "Đ4", "AM04" * 200, ["A1"], {"a": 1}):
        key = sort_key_for_slot(odd)
        assert isinstance(key, tuple)
    # numbers compare by size with no int() limit; very long locations are cut at 300 characters like the printed text
    assert sort_key_for_slot("A" + "9" * 250) > sort_key_for_slot("A" + "9" * 249)
    assert sort_key_for_slot("A" + "9" * 250) > sort_key_for_slot("A" + "8" * 250)


def test_sort_key_is_a_total_order_on_random_slots():
    rnd = random.Random(7)
    slots = ["".join(rnd.choice("aAbB0123- ") for _ in range(rnd.randint(0, 6))) for _ in range(300)]
    ordered = sorted(slots, key=sort_key_for_slot)
    keys = [sort_key_for_slot(s) for s in ordered]
    assert keys == sorted(keys)


# ----------------------------------------------------------------------------- image links
@pytest.mark.parametrize("url", [
    "http://example.com/a.jpg",
    "javascript:alert(1)",
    "JAVASCRIPT:alert(1)",
    "data:image/png;base64,AAAA",
    "//example.com/a.jpg",
    "/static/a.jpg",
    "ftp://example.com/a.jpg",
    "https://",
    "https:///a.jpg",
    "https://example.com/a.jpg\" onerror=\"x",
    "https://example.com/a'.jpg",
    "https://example.com/<a>.jpg",
    "https://example.com/a>.jpg",
    "https://example.com/a`.jpg",
    "https://example.com/a b.jpg",
    "https://example.com/a\n.jpg",
    "https://example.com/a\t.jpg",
    "https://example.com/a\\b.jpg",
    "https://example.com/a\x00.jpg",
    "https://user:pw@example.com/a.jpg",
    "https://example.com@evil.com/a.jpg",
    " javascript:alert(1)",
    "https://example.com/" + "a" * 5000,
    "", None, 12, ["https://example.com/a.jpg"],
])
def test_unsafe_image_urls_are_rejected(url):
    assert safe_image_url(url) == ""


def test_normal_image_urls_are_accepted():
    assert safe_image_url(S3) == S3
    assert len(S3) > 300
    assert safe_image_url("https://sm.config.inc/img/4702.png") == "https://sm.config.inc/img/4702.png"
    assert safe_image_url("  https://sm.config.inc/img/4702.png  ") == "https://sm.config.inc/img/4702.png"


def test_image_in_the_page_is_escaped_and_hardened():
    page = render_pick_list([box([item("000001", img=S3)])])
    imgs = tag_attrs(page, "img")
    assert len(imgs) == 1
    img = imgs[0]
    assert img["src"] == S3                       # the parser decodes &amp; back, so the link is intact
    assert img["referrerpolicy"] == "no-referrer"
    assert img["loading"] == "lazy"
    assert "&amp;X-Amz-Date" in page             # and the raw text carries the escaped form


def test_bad_image_urls_never_reach_the_page():
    bad = ["http://example.com/a.jpg", "javascript:alert(1)", "data:image/png;base64,AA", "//example.com/a.jpg",
           'https://example.com/a" onerror="x', "https://example.com/a b.jpg"]
    page = render_pick_list([box([item(f"00000{n}", img=u, pos=n) for n, u in enumerate(bad, 1)])])
    assert tag_attrs(page, "img") == []
    assert "Ảnh" not in page
    for u in bad:
        assert u not in page


# ----------------------------------------------------------------------------- picture column
def test_picture_column_only_when_needed():
    without = render_pick_list([box([item("000001"), item("000002", pos=2)])])
    assert not tag_attrs(without, "img")
    assert "Ảnh" not in without
    assert 'class="pic"' not in without
    assert len(tables_of(without)[0][0]) == 6

    with_pic = render_pick_list([box([item("000001", img=S3), item("000002", pos=2)])])
    assert len(tag_attrs(with_pic, "img")) == 1
    table = tables_of(with_pic)[0]
    assert table[0][-1] == "Ảnh"
    assert all(len(r) == 7 for r in table)        # every row keeps the column, even without a picture


def test_picture_column_is_per_box():
    page = render_pick_list([box([item("000001", img=S3)], bid=1), box([item("000002")], module="M9", bid=2)], combined=False)
    first, second = tables_of(page)
    assert len(first[0]) == 7 and len(second[0]) == 6


# ----------------------------------------------------------------------------- structure
def sections(page, cls):
    return [a for t, a in parse(page).tags if t == "section" and a.get("class") == cls]


def test_one_page_break_per_box():
    boxes = two_boxes() + [box([item("000021", "B1")], module="M10", bid=3)]
    page = render_pick_list(boxes)
    assert len(sections(page, "box")) == 3
    assert page.count('<section class="box">') == 3
    assert re.search(r"\.box\s*\{[^}]*page-break-before:\s*always", page)
    assert re.search(r"\.box\s*\{[^}]*break-before:\s*page", page)
    assert page.count("<section ") == 4          # three boxes and the combined list


def test_box_without_combined_has_no_leading_section():
    page = render_pick_list(two_boxes(), combined=False)
    assert len(sections(page, "box")) == 2
    assert not sections(page, "combined")
    assert "Danh sách gộp theo kệ" not in page
    assert len(tables_of(page)) == 2


def test_combined_section_only_with_two_boxes_and_flag():
    one = render_pick_list(two_boxes()[:1])
    assert "Danh sách gộp theo kệ" not in one and not sections(one, "combined")
    two = render_pick_list(two_boxes())
    assert "Danh sách gộp theo kệ" in two and len(sections(two, "combined")) == 1
    assert two.index("Danh sách gộp theo kệ") < two.index('<section class="box">')
    off = render_pick_list(two_boxes(), combined=False)
    assert "Danh sách gộp theo kệ" not in off
    assert len(tables_of(two)) == 3 and len(tables_of(one)) == 1


def test_combined_table_columns_and_content():
    page = render_pick_list(two_boxes())
    table = tables_of(page)[0]
    assert table[0] == ["Vị trí kệ", "ID", "Tên tiếng Việt", "Tên tiếng Anh", "SL", "Hộp"]
    assert [r[0] for r in table[1:]] == ["A2", "A10", "AM04", "AN09", "AN10", NO_SLOT]
    assert [r[1] for r in table[1:]] == ["000011", "000012", "000001", "000013", "000003", "000002"]
    assert table[1][5] == "M9 · M9-01 · Box A1"
    assert table[3][5] == "M8 · M8-02 · Box B2"


def test_combined_list_is_sorted_and_has_every_item_once():
    boxes = two_boxes()
    rows = collect_rows(boxes)
    ids = [r["id"] for r in rows]
    assert sorted(ids) == sorted(i["id"] for b in boxes for i in b["items"])
    assert len(ids) == len(set(ids)) == 6
    assert [r["slot"] for r in rows] == ["A2", "A10", "AM04", "AN09", "AN10", ""]
    keys = [sort_key_for_slot(r["slot"]) for r in rows]
    assert keys == sorted(keys)
    first = rows[0]
    assert first["module"] == "M9" and first["label"] == "Box A1" and first["code"] == "M9-01"
    assert first["qty"] == 4 and first["vi"] == "Chảo" and first["name"] == "Pan"
    assert [r[1] for r in tables_of(render_pick_list(boxes))[0][1:]] == ids


def test_collect_rows_edge_cases():
    assert collect_rows([]) == []
    assert collect_rows(None) == []
    assert collect_rows([box([])]) == []
    assert collect_rows(["junk", 3, None, {"items": "nope"}]) == []
    same_shelf = [box([item("000001", "A1")], bid=1), box([item("000002", "A1")], module="M9", bid=2)]
    assert [r["id"] for r in collect_rows(same_shelf)] == ["000001", "000002"]   # stable across boxes


def test_box_table_rows_sorted_with_original_positions():
    page = render_pick_list([two_boxes()[0]])
    table = tables_of(page)[0]
    assert table[0] == ["STT", "ID", "Tên tiếng Việt", "Tên tiếng Anh", "Vị trí kệ", "SL"]
    assert [r[4] for r in table[1:]] == ["AM04", "AN10", NO_SLOT]
    assert [r[0] for r in table[1:]] == ["2", "1", "3"]            # STT is the position in the box, not the sorted row
    assert [r[5] for r in table[1:]] == ["1", "2", "3"]


def test_missing_location_is_last_and_marked():
    page = render_pick_list([box([item("000001", ""), item("000002", "   ", pos=2), item("000003", "-", pos=3),
                                  item("000004", "AZ99", pos=4)])])
    rows = tables_of(page)[0][1:]
    assert rows[0][4] == "AZ99"
    assert [r[4] for r in rows[1:]] == [NO_SLOT] * 3
    assert [r[1] for r in rows[1:]] == ["000001", "000002", "000003"]     # stable


def test_stt_falls_back_to_row_number():
    items = [{"id": "000001", "slot": "A2"}, {"id": "000002", "slot": "A1"}]
    rows = tables_of(render_pick_list([box(items)]))[0][1:]
    assert [(r[0], r[1]) for r in rows] == [("2", "000002"), ("1", "000001")]


def test_box_header_has_the_facts_and_the_date():
    page = render_pick_list(two_boxes()[:1], generated_at="10/11/2026 10:00")
    text = visible_text(page)
    for needle in ("M8 · Box B2", "Module: M8", "Hộp: Box B2", "Mã section: M8-02", "Đồ lớn: Tủ lạnh",
                   "Ngày: 10/11/2026 10:00", "Phiếu lấy đồ"):
        assert needle in text, needle


def test_header_leaves_out_empty_facts():
    page = render_pick_list([box([item("000001")], anchor="", code="", title="")])
    text = visible_text(page)
    assert "Đồ lớn" not in text and "Mã section" not in text and "TaskBox" not in text and "Ngày" not in text


def test_totals_are_correct():
    boxes = two_boxes()
    page = render_pick_list(boxes)
    text = visible_text(page)
    assert "Tổng cộng: 3 món, số lượng 6" in text      # box 1: 2 + 1 + 3
    assert "Tổng cộng: 3 món, số lượng 6" in text      # box 2: 1 + 4 + 1
    assert text.count("Tổng cộng: 3 món, số lượng 6") == 2
    assert "Tổng cộng: 6 món, số lượng 12" in text     # combined list
    assert "2 hộp, 6 món, số lượng 12" in text
    solo = visible_text(render_pick_list([box([item("000001", qty=5), item("000002", qty=2, pos=2)])]))
    assert "Tổng cộng: 2 món, số lượng 7" in solo


def test_bad_quantities_count_as_zero():
    page = render_pick_list([box([item("000001", qty="x"), item("000002", qty=None, pos=2), item("000003", qty=True, pos=3),
                                  item("000004", qty=-4, pos=4), item("000005", qty="3", pos=5)])])
    assert "Tổng cộng: 5 món, số lượng 3" in visible_text(page)


def test_box_without_items_still_prints_its_header():
    page = render_pick_list([box([]), two_boxes()[1]])
    assert len(sections(page, "box")) == 2
    assert "Hộp này chưa có món." in page
    assert len(tables_of(page)) == 2               # combined list and the one box that has items


# ----------------------------------------------------------------------------- empty
@pytest.mark.parametrize("empty", [[], None, [None, "x"]])
def test_empty_input_gives_a_friendly_page(empty):
    page = render_pick_list(empty, generated_at="10/11/2026")
    assert "Chưa có hộp đã duyệt để in." in page
    assert not tables_of(page)
    assert "<table" not in page and "<section" not in page
    assert "Phiếu lấy đồ" in visible_text(page)
    parse(page)


# ----------------------------------------------------------------------------- security
PAYLOADS = ["<script>alert(1)</script>", '" onerror="x', "&amp;", "'><img src=x onerror=alert(1)>", "</td></tr></table><h1>x</h1>",
            "<style>body{display:none}</style>", "‮evil", "a\x00b"]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_untrusted_strings_are_escaped_everywhere(payload):
    b = box([item("000001", slot="AM04" + payload, name="N" + payload, vi="V" + payload, img=S3)],
            module="M" + payload, label="L" + payload, code="C" + payload, anchor="A" + payload, title="T" + payload)
    b2 = box([item("000002", slot=payload + "A1")], module="M9", label="Box A1", bid=2)
    page = render_pick_list([b, b2], generated_at="D" + payload, heading="H" + payload)
    p = parse(page)
    names = [t for t, _ in p.tags]
    assert names.count("script") == 0
    assert names.count("style") == 1
    assert names.count("h1") == 1
    assert names.count("table") == 3
    assert names.count("img") == 1
    for tag, attrs in p.tags:
        assert not [k for k in attrs if k.startswith("on")] or (tag == "button" and attrs == {"type": "button", "onclick": "window.print()"})
        assert "onerror" not in attrs
    text = visible_text(page)
    clean = payload.replace("‮", "").replace("\x00", "")
    for prefix in ("N", "V", "L", "C", "A", "D"):                 # the SM title is no longer printed (the box facts name the box)
        assert prefix + clean in text, prefix
    assert "AM04" + clean in text
    assert "<script" not in page and "<img src=x" not in page
    if "<" in payload:
        assert payload not in page
    assert "‮" not in page and "\x00" not in page


def test_ampersand_payload_roundtrips_as_text():
    page = render_pick_list([box([item("000001", name="Salt &amp; pepper", vi="Muối & tiêu <b>", slot="A&1")])])
    assert "Salt &amp;amp; pepper" in page and "Muối &amp; tiêu &lt;b&gt;" in page
    row = tables_of(page)[0][1]
    assert row[2:5] == ["Muối & tiêu <b>", "Salt &amp; pepper", "A&1"]


def test_attribute_breakout_in_alt_is_escaped():
    page = render_pick_list([box([item("000001", vi='" onerror="alert(1)', img=S3)])])
    img = tag_attrs(page, "img")[0]
    assert set(img) == {"src", "alt", "width", "height", "loading", "referrerpolicy"}
    assert img["alt"] == '" onerror="alert(1)'


def test_non_string_values_do_not_echo_raw_objects():
    page = render_pick_list([box([item("000001", name={"secret": "token-123"}, vi=["x"], slot=object)])])
    assert "secret" in page          # str() of a dict is plain text, escaped, never markup
    parse(page)
    assert "<" not in re.sub(r"<[^>]*>", "", re.sub(r"<style>.*?</style>", "", page, flags=re.S)).replace("&lt;", "")


ALLOWED_ATTRS = {
    "html": {"lang"}, "meta": {"charset", "name", "content"}, "button": {"type", "onclick"},
    "img": {"src", "alt", "width", "height", "loading", "referrerpolicy"},
    "section": {"class"}, "div": {"class"}, "p": {"class"}, "ul": {"class"}, "td": {"class"}, "th": {"class"},
}


def test_no_external_requests_and_only_the_print_handler():
    boxes = two_boxes()
    boxes[0]["items"][0]["img"] = S3
    page = render_pick_list(boxes, generated_at="10/11/2026")
    assert "http://" not in page
    assert "@import" not in page and "url(" not in page.lower()
    p = parse(page)
    seen_onclick = 0
    for tag, attrs in p.tags:
        assert tag not in ("script", "link", "iframe", "object", "embed", "form", "a", "base", "video", "audio", "source", "svg")
        assert set(attrs) <= ALLOWED_ATTRS.get(tag, set()), (tag, attrs)
        for k, v in attrs.items():
            assert k not in ("href", "action", "srcset", "style", "formaction", "poster", "data")
            if k == "src":
                assert tag == "img" and v.startswith("https://") and v == S3
            if k.startswith("on"):
                assert tag == "button" and k == "onclick" and v == "window.print()"
                seen_onclick += 1
    assert seen_onclick == 1
    assert 'onclick="window.print()"' in page


def test_head_and_toolbar():
    page = render_pick_list(two_boxes())
    assert page.startswith("<!DOCTYPE html>")
    assert tag_attrs(page, "html") == [{"lang": "vi"}]
    metas = tag_attrs(page, "meta")
    assert {"charset": "utf-8"} in metas
    assert {"name": "viewport", "content": "width=device-width, initial-scale=1"} in metas
    assert tag_attrs(page, "button") == [{"type": "button", "onclick": "window.print()"}]
    text = visible_text(page)
    assert "In phiếu" in text and "Ctrl+P" not in text
    assert page.index("In phiếu") < page.index("<main>")
    assert page.count("<title>") == 1


def test_print_css():
    page = render_pick_list(two_boxes())
    css = re.search(r"<style>(.*?)</style>", page, re.S).group(1)
    assert "@page" in css and "size: A4" in css and "margin:" in css
    assert re.search(r"@media print\s*\{[^@]*\.toolbar\s*\{\s*display:\s*none", css)
    assert re.search(r"thead\s*\{\s*display:\s*table-header-group", css)
    assert re.search(r"tr\s*\{[^}]*break-inside:\s*avoid", css) and "page-break-inside: avoid" in css
    assert re.search(r"th, td\s*\{[^}]*border: 1px solid", css)
    sizes = [int(n) for n in re.findall(r"font:\s*(\d+)px", css)]
    assert sizes and all(11 <= s <= 12 for s in sizes)


def test_page_parses_without_errors_for_many_shapes():
    for boxes in ([], two_boxes(), two_boxes()[:1], [box([])], [box([item("1", None, None, None, None, None, None)])]):
        for combined in (True, False):
            parse(render_pick_list(boxes, generated_at="x", combined=combined))


# ----------------------------------------------------------------------------- misc
def test_output_is_deterministic():
    boxes = two_boxes()
    boxes[0]["items"][0]["img"] = S3
    a = render_pick_list(boxes, generated_at="10/11/2026 10:00")
    b = render_pick_list(two_boxes_with_img(), generated_at="10/11/2026 10:00")
    assert a == b == render_pick_list(boxes, generated_at="10/11/2026 10:00")
    assert render_pick_list([]) == render_pick_list([])


def two_boxes_with_img():
    boxes = two_boxes()
    boxes[0]["items"][0]["img"] = S3
    return boxes


def test_input_is_not_modified():
    import copy
    boxes = two_boxes()
    before = copy.deepcopy(boxes)
    render_pick_list(boxes, generated_at="x")
    collect_rows(boxes)
    assert boxes == before


def test_long_names_are_cut_and_text_is_plain():
    page = render_pick_list([box([item("000001", name="x" * 5000, vi="Chảo\n\tlớn   nhỏ")])])
    row = tables_of(page)[0][1]
    assert row[2] == "Chảo lớn nhỏ"
    assert len(row[3]) <= 301 and row[3].endswith("…")


def test_ui_text_follows_the_writing_rules():
    page = render_pick_list(two_boxes(), generated_at="10/11/2026")
    for chunk in (page, render_pick_list([])):
        assert "—" not in chunk
        assert "!" not in re.sub(r"<!DOCTYPE html>", "", chunk)


def test_custom_heading_is_used_and_escaped():
    page = render_pick_list(two_boxes()[:1], heading="Phiếu <đợt 2>")
    assert "<title>Phiếu &lt;đợt 2&gt;</title>" in page
    assert "Phiếu <đợt 2>" in visible_text(page)

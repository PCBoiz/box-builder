"""Browser test of the usage chart (app/static/usage_chart.js and usage_chart.css) on a tiny local page with made-up data.
Run:  python tests/usage_chart_e2e.py        (needs: pip install playwright && python -m playwright install chromium)
It opens a page that loads the two files like the real page will, then checks: the sentences, the table and list in the
accessibility tree, no sideways scroll at 320 px, reduced motion, forced colours, bar contrast, and axe-core.
AXE_JS=/path/to/axe.min.js (from the axe-core npm package) turns on the axe run; without it that one step is skipped and says so.
Screenshots go to the system temp dir. It never contacts SM, Render, Neon or Google Sheets."""
import http.server, json, os, sys, tempfile, threading
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent.parent
JS, CSS = ROOT / "app/static/usage_chart.js", ROOT / "app/static/usage_chart.css"
OUT = Path(tempfile.gettempdir()) / "box-builder-usage-chart"
OUT.mkdir(exist_ok=True)
AXE = os.environ.get("AXE_JS") or ""                                                      # without it the axe step is skipped and says so

# The panel colour and spacing of the real app (app.css: --case #f3f5f4, .panel padding 16px, .app padding 20px, body 14px).
PAGE = f"""<!doctype html>
<html lang="vi"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Thử biểu đồ lượt mượn</title>
<style>
  *, *::before, *::after {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #e5e9e7; color: #18232b; font: 400 14px/1.5 system-ui, "Segoe UI", sans-serif; }}
  main {{ max-width: 760px; margin: 0 auto; padding: 20px; }}
  .panel {{ display: grid; gap: 12px; padding: 16px; border: 1px solid #c4cdca; border-radius: 14px; background: #f3f5f4; }}
  .panel h2 {{ margin: 0; font-size: 15px; }}
</style>
<link rel="stylesheet" href="/usage_chart.css">
</head><body><main><h1 style="font-size:17px;margin:0 0 12px">Thử biểu đồ</h1>
<section class="panel" aria-labelledby="t"><h2 id="t">Cảnh báo kho</h2><div id="c"></div></section></main>
<script src="/usage_chart.js"></script></body></html>"""

class Site(http.server.BaseHTTPRequestHandler):
    """Serves the page and the two real files from 127.0.0.1 only, so axe may read the stylesheet (it cannot from file://)."""
    def do_GET(self):
        route = {"/page.html": (PAGE.encode("utf-8"), "text/html; charset=utf-8"), "/usage_chart.css": (CSS.read_bytes(), "text/css; charset=utf-8"),
                 "/usage_chart.js": (JS.read_bytes(), "text/javascript; charset=utf-8")}.get(self.path)
        self.send_response(200 if route else 404)
        body, kind = route or (b"", "text/plain")
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def top(rows):
    return [{"id": i, "name": n, "vi": v, "uses": u, "age_days": 200, "weight": w} for i, n, v, u, w in rows]

SAMPLE = {
    "enabled": True, "known": 1490, "total": 1533, "soft_start": 500, "hard_limit": 0, "half_life": 300, "age_effect": 50, "min_pool": 100,
    "over_start": 46, "excluded": 0, "reduced": 46, "free_over_start": 3,
    "bands": [{"from": 0, "to": 99, "count": 1210}, {"from": 100, "to": 249, "count": 190}, {"from": 250, "to": 499, "count": 87},
              {"from": 500, "to": 999, "count": 44}, {"from": 1000, "to": None, "count": 2}],
    "unknown": 43,
    "top": top([("000724", "Stainless steel tongs", "Kẹp inox", 1587, 0.22), ("001203", "Cast iron pan", "Chảo gang", 1083, 0.27),
                ("000311", "Wooden spoon", "Muỗng gỗ", 892, 0.41), ("002250", "Glass jar", "Hũ thủy tinh", 825, 0.45),
                ("000990", "Kettle", "Ấm đun nước", 811, 0.46), ("004702", "Frying pan", "Chảo", 786, 0.5),
                ("000555", "Cutting board", "", 700, 0.6), ("000556", "Whisk", "Phới lồng", 640, 0.7),
                ("000557", "Ladle", "Muôi", 610, 0.75), ("000558", "Colander", "Rổ", 590, 0.8),
                ("000559", "Peeler", "Dao gọt", 570, 0.85), ("000560", "Grater", "Nạo", 540, 0.9),
                ("000561", "Rolling pin", "Cán bột", 520, 0.95), ("000562", "Teapot", "Ấm trà", 505, 0.998),
                ("000563", "Mug", "Cốc", 499, 1.0)]),
}
HARD = dict(SAMPLE, hard_limit=1000, excluded=2, top=top([("000724", "Stainless steel tongs", "Kẹp inox", 1587, 0.0), ("001203", "Cast iron pan", "Chảo gang", 1083, 0),
                                                          ("000311", "Wooden spoon", "Muỗng gỗ", 892, 0.41), ("002250", "Glass jar", "Hũ thủy tinh", 825, 0.001)]))
OFF = dict(SAMPLE, enabled=False)
NONE = dict(SAMPLE, over_start=0, reduced=0, bands=[{"from": 0, "to": 99, "count": 1300}, {"from": 100, "to": 249, "count": 150}, {"from": 250, "to": 499, "count": 40},
                                                     {"from": 500, "to": 999, "count": 0}, {"from": 1000, "to": None, "count": 0}],
            top=top([("000724", "Tongs", "Kẹp inox", 450, 1), ("001203", "Pan", "Chảo gang", 300, 1)]))
NODATA = dict(SAMPLE, known=0, over_start=0, reduced=0, unknown=1533, bands=[], top=[])
MIDBAND = dict(SAMPLE, soft_start=600, over_start=30)
# Odd input: huge numbers, null and wrong-typed fields, weights outside 0..1, long names, no bands.
ODD = {"enabled": True, "known": 10**15, "total": 10**15 + 5, "soft_start": 500, "hard_limit": 10**12, "over_start": 10**14, "excluded": 7, "reduced": 10**14,
       "bands": [{"from": 0, "to": 99, "count": 10**15}, {"from": 500, "to": 999, "count": 1}, {"from": 1000, "to": None, "count": 2}], "unknown": 10**13,
       "top": [{"id": "1" * 40, "name": "A" * 120, "vi": None, "uses": 10**12, "weight": -3}, {"id": None, "name": None, "vi": None, "uses": 5, "weight": 7},
               {"id": "x", "name": "<img src=x onerror=alert(1)>", "vi": "", "uses": "9", "weight": None}, "junk", None]}

fails = []
def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        fails.append(msg)

def draw(pg, data):
    pg.evaluate("(d) => BBUsageChart.render(document.getElementById('c'), d)", data)

def sideways(pg):
    """Elements whose right edge is past the viewport, and the page's own scroll width."""
    return pg.evaluate("""() => {
      const vw = document.documentElement.clientWidth, bad = [];
      for (const e of document.querySelectorAll('.bbu, .bbu *')) { const r = e.getBoundingClientRect(); if (r.width && r.right > vw + 0.5) bad.push(e.className + ' ' + Math.round(r.right)); }
      return { scroll: document.documentElement.scrollWidth, client: vw, bad: bad.slice(0, 5) };
    }""")

def main():
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        errs = []
        site = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Site)
        threading.Thread(target=site.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{site.server_address[1]}/page.html"

        def new(width=1000, **kw):
            ctx = b.new_context(viewport={"width": width, "height": 900}, **kw)
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: errs.append("pageerror: " + str(e)))
            pg.on("console", lambda m: errs.append("console: " + m.text) if m.type in ("error", "warning") else None)
            pg.goto(url)
            check(pg.evaluate("typeof BBUsageChart.render") == "function", "window.BBUsageChart.render exists")
            return ctx, pg

        # ---------------------------------------------------------------- the sentences
        ctx, pg = new(1000)
        draw(pg, SAMPLE)
        lede = pg.locator(".bbu-lede")
        check(lede.text_content() == "Cảnh báo: Có 46 trong 1.533 món đã đạt từ 500 lượt mượn trở lên.", "sentence: " + (lede.text_content() or ""))
        vh = pg.locator(".bbu-lede .bbu-vh").bounding_box()
        check(vh is not None and vh["width"] <= 1.01 and vh["height"] <= 1.01, "the prefix 'Cảnh báo: ' is visually hidden (1 px box)")
        check(pg.locator(".bbu-effect").text_content() == "46 món được chọn ít hơn. Mức loại đang là 0, nên không món nào bị loại.", "effect sentence (limit 0): " + pg.locator(".bbu-effect").text_content())
        check(pg.locator("[role=alert], [aria-live]").count() == 0, "no alert or live region for static text")

        # ---------------------------------------------------------------- table: name, description, headers, rows
        tbl = pg.get_by_role("table", name="Số món theo số lượt mượn")
        check(tbl.count() == 1, "one table named by its heading: 'Số món theo số lượt mượn'")
        expect(tbl).to_have_accessible_description("Thanh gạch chéo: từ mức bắt đầu giảm trở lên. Hàng nét đứt đánh dấu mức đó.")
        check(True, "the table is described by the reading note")
        heads = [h.strip() for h in tbl.get_by_role("columnheader").all_text_contents()]
        check(heads == ["Số lượt mượn", "Số món"], f"column headers {heads}")
        rows = [r.strip() for r in tbl.get_by_role("rowheader").all_text_contents()]
        check(rows == ["Dưới 100", "100 đến 249", "250 đến 499", "Mức bắt đầu giảm: 500 lượt mượn", "500 đến 999", "Từ 1.000 trở lên", "Chưa có số lượt mượn"], f"row headers {rows}")
        cells = [c.strip() for c in tbl.get_by_role("cell").all_text_contents()]
        check(cells == ["1.210", "190", "87", "44", "2", "43"], f"cells carry the counts {cells}")
        snap = tbl.aria_snapshot()
        check("rowheader \"Mức bắt đầu giảm: 500 lượt mượn\"" in snap and "cell \"1.210\"" in snap, "accessibility tree has the divider row header and the printed counts")
        print(snap)
        check(pg.locator(".bbu-track:not([aria-hidden=true])").count() == 0, "every bar track is aria-hidden")
        check(pg.locator("table [aria-hidden=true]").count() == 6, "6 bars in the table are hidden from assistive tech, the numbers are not")

        # ---------------------------------------------------------------- list
        lst = pg.get_by_role("list", name="15 món có nhiều lượt mượn nhất")
        check(lst.count() == 1, "one list named by its heading: '15 món có nhiều lượt mượn nhất'")
        expect(lst).to_have_accessible_description("Thanh gạch chéo: từ mức bắt đầu giảm trở lên. Đường nét đứt đánh dấu mức đó.")
        items = lst.get_by_role("listitem")
        check(items.count() == 15, "15 list items")
        first = items.nth(0).text_content().strip()
        check(first == "000724 Kẹp inox 1.587 lượt mượn Còn 22% khả năng được chọn" + "Giảm từ 500", f"first row text (id, name, uses, effect, marker label): {first!r}")
        check(items.nth(6).text_content().strip().startswith("000555 Cutting board 700 lượt mượn"), "no Vietnamese name: the English name is shown")
        check(items.nth(6).locator(".bbu-name").get_attribute("lang") == "en", "an English fallback name is marked lang=en")
        check(items.nth(14).text_content().strip() == "000563 Cốc 499 lượt mượn", "weight 1: nothing said about the effect: " + items.nth(14).text_content().strip())
        check("Còn trên 99% khả năng được chọn" in items.nth(13).text_content(), "weight 0.998 is read as 'trên 99%'")
        check(pg.locator(".bbu-tag").count() == 1 and pg.locator(".bbu-tag").get_attribute("class").startswith("bbu-tag is-soft"), "one marker label (the soft line), no hard line")
        check(pg.locator(".bbu-tag").evaluate("e => !!e.closest('[aria-hidden=true]')"), "marker labels sit in aria-hidden tracks (the same facts are in the sentences)")
        pg.screenshot(path=str(OUT / "sample_1000.png"), full_page=True)
        ctx.close()

        # ---------------------------------------------------------------- states
        ctx, pg = new(1000)
        draw(pg, HARD)
        check(pg.locator(".bbu-effect").text_content() == "46 món được chọn ít hơn. 2 món bị loại vì có từ 1.000 lượt mượn trở lên.", "effect sentence (limit 1000, 2 left out): " + pg.locator(".bbu-effect").text_content())
        check(pg.locator(".bbu-tag").count() == 2, "two marker labels: Giảm từ 500 and Loại từ 1.000")
        check(pg.locator(".bbu-tag.is-hard").text_content() == "Loại từ 1.000", "hard label text")
        check(pg.locator(".bbu-top > li").nth(0).text_content().count("Loại: không được chọn") == 1, "weight 0 reads 'Loại: không được chọn'")
        check("Còn dưới 1% khả năng được chọn" in pg.locator(".bbu-top > li").nth(3).text_content(), "weight 0.001 reads 'dưới 1%'")
        check("Đường liền là mức loại." in pg.locator(".bbu-note").nth(1).text_content(), "reading note names the solid line")
        pg.screenshot(path=str(OUT / "hard_1000.png"), full_page=True)
        draw(pg, dict(HARD, excluded=0))
        check(pg.locator(".bbu-effect").text_content() == "46 món được chọn ít hơn. Chưa món nào bị loại. Món có từ 1.000 lượt mượn trở lên sẽ bị loại.", "limit set, none left out yet")
        draw(pg, OFF)
        check(pg.locator(".bbu-effect").text_content() == "Công tắc Giảm món đã dùng nhiều đang tắt, nên không món nào được chọn ít hơn hay bị loại.", "switch off sentence")
        check("khả năng được chọn" not in pg.locator(".bbu-top").text_content(), "switch off: no per-object effect text")
        draw(pg, NONE)
        check(pg.locator(".bbu-lede").text_content() == "Chưa có món nào đạt từ 500 lượt mượn trở lên." and pg.locator(".bbu-effect").count() == 0, "no object over: calm sentence, no effect sentence")
        check(pg.locator(".bbu-vh").count() == 0, "calm sentence has no 'Cảnh báo' prefix")
        check(pg.get_by_role("table").count() == 1 and pg.get_by_role("list").count() == 1, "no object over: the charts still show how far the top is from the threshold")
        pg.screenshot(path=str(OUT / "none_1000.png"), full_page=True)
        draw(pg, NODATA)
        check(pg.locator(".bbu-lede").text_content() == "Chưa có số lượt mượn của món nào. Cập nhật công cụ đồng bộ trong SM lên bản 0.3.0, rồi bấm \"Đồng bộ kho ngay\".", "known 0: say there is no data and to update the SM sync tool to 0.3.0")
        check(pg.get_by_role("table").count() == 0 and pg.get_by_role("list").count() == 0, "known 0: no chart")
        draw(pg, MIDBAND)
        div = pg.locator(".bbu-thr th").text_content()
        check(div == "Mức bắt đầu giảm: 600 lượt mượn, nằm trong dòng trên", "threshold inside a band: " + div)
        check(pg.get_by_role("table").get_by_role("rowheader").all_text_contents()[:5] == ["Dưới 100", "100 đến 249", "250 đến 499", "500 đến 999", "Mức bắt đầu giảm: 600 lượt mượn, nằm trong dòng trên"], "divider comes after the band that holds the threshold")
        pg.screenshot(path=str(OUT / "midband_1000.png"), full_page=True)
        # odd input must not throw, and an object name is text, never markup
        draw(pg, ODD)
        check(pg.locator("img").count() == 0, "a name with markup in it stays text")
        check("<img src=x onerror=alert(1)>" in pg.locator(".bbu-top").text_content(), "the markup shows as text")
        for odd in (None, {}, [], "x", 5, {"bands": 5, "top": "no", "known": "12", "soft_start": "500", "over_start": None}):
            pg.evaluate("(d) => BBUsageChart.render(document.getElementById('c'), d)", odd)
        draw(pg, SAMPLE)
        check(pg.locator(".bbu-lede").text_content().endswith("Có 46 trong 1.533 món đã đạt từ 500 lượt mượn trở lên."), "render never threw on odd input, and the next render still works")
        ctx.close()

        # ---------------------------------------------------------------- 320 px: no sideways scroll, real widths
        ctx, pg = new(320)
        for name, data in (("sample", SAMPLE), ("hard", HARD), ("off", OFF), ("none", NONE), ("midband", MIDBAND), ("nodata", NODATA), ("odd", ODD)):
            draw(pg, data)
            s = sideways(pg)
            check(s["scroll"] <= s["client"] and not s["bad"], f"320 px, {name}: no sideways scroll (scrollWidth {s['scroll']}, viewport {s['client']}, past the edge: {s['bad']})")
        draw(pg, SAMPLE)
        m = pg.evaluate("""() => {
          const w = (s, i = 0) => { const e = document.querySelectorAll(s)[i]; return e ? Math.round(e.getBoundingClientRect().width * 10) / 10 : null; };
          return { page: w('main'), panel: w('.panel'), chart: w('.bbu'), table: w('.bbu-bands'), labelCol: w('.bbu-bands tbody th'), track: w('.bbu-bands .bbu-track'),
                   num: w('.bbu-bands .bbu-num'), barBig: w('.bbu-bands .bbu-fill'), bar44: w('.bbu-bands .bbu-fill', 3), bar2: w('.bbu-bands .bbu-fill', 4), bar43: w('.bbu-bands .bbu-fill', 5),
                   listTrack: w('.bbu-top .bbu-track'), listVal: w('.bbu-top .bbu-val'), fontPx: getComputedStyle(document.querySelector('.bbu')).fontSize };
        }""")
        print("320 px widths (CSS px):", json.dumps(m))
        vals = pg.evaluate("() => [...document.querySelectorAll('.bbu-top .bbu-val')].map(e => Math.round(e.getBoundingClientRect().height))")
        check(max(vals) < 24, f"320 px: every 'lượt mượn' number fits on one line (heights {sorted(set(vals))} px)")
        check(m["chart"] <= 246.5, f"chart box {m['chart']} px fits the panel (246 px at 320)")
        check(m["bar2"] >= 6, f"the bar for 2 objects is {m['bar2']} px wide (a true share would be {m['track'] * 2 / 1210:.1f} px): the minimum keeps it visible")
        check(m["bar44"] >= 6 and m["bar43"] >= 6, f"bars for 44 and 43 objects are {m['bar44']} and {m['bar43']} px")
        draw(pg, HARD)
        check(pg.locator(".bbu-tag").count() == 2, "320 px: both marker labels present")
        tags = pg.evaluate("""() => [...document.querySelectorAll('.bbu-tag')].map(e => { const r = e.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right)]; })""")
        check(all(0 <= l and r <= 320 for l, r in tags), f"320 px: marker labels stay inside the screen {tags}")
        pg.screenshot(path=str(OUT / "hard_320.png"), full_page=True)
        draw(pg, SAMPLE)
        pg.screenshot(path=str(OUT / "sample_320.png"), full_page=True)
        # text spacing of WCAG 1.4.12 must not break the layout either
        pg.add_style_tag(content="*{line-height:1.5!important;letter-spacing:.12em!important;word-spacing:.16em!important} p{margin-bottom:2em!important}")
        s = sideways(pg)
        check(s["scroll"] <= s["client"], f"320 px with WCAG text spacing: no sideways scroll ({s['scroll']})")
        pg.screenshot(path=str(OUT / "sample_320_spacing.png"), full_page=True)
        ctx.close()
        # a larger text size (browser zoom 200% is the same as 160 px wide, so use 400% = 320 px at the 1280 base, done above); large base font:
        ctx, pg = new(320)
        pg.add_style_tag(content="body{font-size:22px!important}")
        draw(pg, HARD)
        s = sideways(pg)
        check(s["scroll"] <= s["client"] and not s["bad"], f"320 px with 22 px text: no sideways scroll ({s})")
        ctx.close()

        # ---------------------------------------------------------------- wide layout
        ctx, pg = new(1000)
        draw(pg, SAMPLE)
        row = pg.evaluate("""() => { const li = document.querySelector('.bbu-top > li:nth-child(2)'); const t = li.querySelector('.bbu-track').getBoundingClientRect(), n = li.querySelector('.bbu-nm').getBoundingClientRect();
                                      return { sameLine: Math.abs(t.top + t.height / 2 - (n.top + 10)) < 14, name: n.width, track: t.width }; }""")
        check(row["sameLine"], f"wide box: name and bar share a line {row}")
        ctx.close()

        # ---------------------------------------------------------------- reduced motion and normal motion: the chart adds none
        for pref in ("reduce", "no-preference"):
            ctx, pg = new(1000, reduced_motion=pref)
            draw(pg, HARD)
            check(pg.evaluate("matchMedia('(prefers-reduced-motion: reduce)').matches") == (pref == "reduce"), f"emulating prefers-reduced-motion: {pref}")
            motion = pg.evaluate("""() => { const bad = []; for (const e of document.querySelectorAll('.bbu, .bbu *')) { const s = getComputedStyle(e);
                if (parseFloat(s.transitionDuration) > 0 || (s.animationName !== 'none' && parseFloat(s.animationDuration) > 0)) bad.push(e.className); }
                return { running: document.getAnimations().length, bad }; }""")
            check(motion["running"] == 0 and not motion["bad"], f"{pref}: no animation or transition runs on the chart {motion}")
            ctx.close()

        # ---------------------------------------------------------------- forced colours (Windows high contrast, emulated)
        ctx, pg = new(1000, forced_colors="active")
        draw(pg, HARD)
        check(pg.evaluate("matchMedia('(forced-colors: active)').matches"), "emulating forced-colors: active")
        fc = pg.evaluate("""() => { const css = (e) => getComputedStyle(e), txt = css(document.querySelector('.bbu-lede')).color;
          const under = document.querySelector('.bbu-fill.is-under'), over = document.querySelector('.bbu-fill.is-over'), unk = document.querySelector('.bbu-fill.is-unk');
          const tr = document.querySelector('.bbu-thr th');
          return { txt, underBg: css(under).backgroundColor, underBorder: css(under).borderTopColor, overImg: css(over).backgroundImage, overBorder: css(over).borderTopColor,
                   unkBg: css(unk).backgroundColor, unkBorder: css(unk).borderTopColor, divider: css(tr).borderTopStyle, dividerColor: css(tr).borderTopColor,
                   widths: [under, over, unk].map(e => e.getBoundingClientRect().width) }; }""")
        print("forced colours:", json.dumps(fc))
        check(fc["underBg"] == fc["txt"] and fc["underBorder"] == fc["txt"], "forced colours: the bar is drawn in the text colour")
        check("repeating-linear-gradient" in fc["overImg"] and fc["overBorder"] == fc["txt"], "forced colours: the over-threshold bar keeps its hatch and outline")
        check(fc["unkBg"] != fc["txt"] and fc["unkBorder"] == fc["txt"], "forced colours: the no-data bar stays hollow with an outline")
        check(fc["divider"] == "dashed" and fc["dividerColor"] == fc["txt"], "forced colours: the divider row keeps its dashed edges")
        check(all(w > 0 for w in fc["widths"]), "forced colours: bars still have width")
        pg.screenshot(path=str(OUT / "hard_forced_colors.png"), full_page=True)
        ctx.close()

        # ---------------------------------------------------------------- colour is not the only cue
        ctx, pg = new(1000)
        draw(pg, SAMPLE)
        cues = pg.evaluate("""() => { const c = (e) => getComputedStyle(e); const u = document.querySelector('.bbu-fill.is-under'), o = document.querySelector('.bbu-fill.is-over');
          return { underImg: c(u).backgroundImage, overImg: c(o).backgroundImage, underBorder: c(u).borderTopWidth, overBorder: c(o).borderTopWidth, thrStyle: c(document.querySelector('.bbu-thr th')).borderTopStyle,
                   lineStyle: getComputedStyle(document.querySelector('.bbu-top .bbu-track'), '::before').borderLeftStyle }; }""")
        check(cues["underImg"] == "none" and "repeating-linear-gradient" in cues["overImg"], "over-threshold bars are hatched, others are solid")
        tokens = pg.evaluate("""() => { const c = (e, p) => getComputedStyle(e)[p];
          return { panel: c(document.querySelector('.panel'), 'backgroundColor'), ink: c(document.querySelector('.bbu'), 'color'), blue: c(document.querySelector('.bbu-fill.is-under'), 'backgroundColor'),
                   warnBorder: c(document.querySelector('.bbu-fill.is-over'), 'borderTopColor'), track: c(document.querySelector('.bbu-track'), 'backgroundColor'), soft: c(document.querySelector('.bbu-note'), 'color') }; }""")
        check(tokens == {"panel": "rgb(243, 245, 244)", "ink": "rgb(24, 35, 43)", "blue": "rgb(47, 111, 160)", "warnBorder": "rgb(184, 67, 10)", "track": "rgb(223, 229, 226)", "soft": "rgb(72, 87, 95)"},
              f"rendered colours are the measured ones (#f3f5f4, #18232b, #2f6fa0, #b8430a, #dfe5e2, #48575f): {tokens}")
        check(cues["overBorder"] == "2px" and cues["thrStyle"] == "dashed" and cues["lineStyle"] == "dashed", "outline 2 px, dashed divider row and dashed threshold line")
        ctx.close()

        # ---------------------------------------------------------------- axe
        if os.path.exists(AXE):
            for width in (1000, 320):
                ctx, pg = new(width)
                pg.add_script_tag(path=AXE)
                for name, data in (("sample", SAMPLE), ("hard", HARD), ("off", OFF), ("none", NONE), ("midband", MIDBAND), ("nodata", NODATA), ("odd", ODD)):
                    draw(pg, data)
                    res = pg.evaluate("""async () => { const r = await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'] } });
                      return { v: r.violations.map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).join(' | ')), passes: r.passes.length, inc: r.incomplete.map(v => v.id) }; }""")
                    check(not res["v"], f"axe at {width} px, {name}: 0 violations ({res['passes']} rules passed; to review by hand: {res['inc']}) {res['v']}")
                ctx.close()
        else:
            print("SKIP axe: set AXE_JS to axe.min.js (npm package axe-core)")

        check(not errs, "no page errors or console warnings " + str(errs))
        b.close()
    print(f"\nscreenshots: {OUT}")
    print("FAILED: " + "; ".join(fails) if fails else "usage chart e2e OK")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())

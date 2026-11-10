"use strict";
/* Chart for the "Cảnh báo kho" panel: which objects have many lifetime uses ("lượt mượn"). Styles: usage_chart.css.
   BBUsageChart.render(container, usage) replaces the container's content. usage = the JSON of GET /api/usage.
   Page order: one sentence with the count, one sentence on what happens, a table of bands, a list of the heaviest objects.
   The table and the list are the text alternative: bars are aria-hidden and every number is printed next to them.
   Nodes are built with createElement and textContent only, and bar widths go through CSS custom properties.
   Under Node (no window) the same file exports its API through module.exports; tests/usage_chart_test.js does that. */
(function () {
  // Every word the reader sees lives here. {name} marks a number or text that the code fills in.
  const W = {
    warn: "Cảnh báo: ",                                   // read by screen readers, not shown
    lede: "Có {over} trong {total} món đã đạt từ {soft} lượt mượn trở lên.",
    ledeNone: "Chưa có món nào đạt từ {soft} lượt mượn trở lên.",
    noData: "Chưa có số lượt mượn của món nào. Cập nhật công cụ đồng bộ trong SM lên bản 0.3.0, rồi bấm \"Đồng bộ kho ngay\".",
    noSoft: "Chưa đặt mức bắt đầu giảm.",
    switchOff: "Công tắc Giảm món đã dùng nhiều đang tắt, nên không món nào được chọn ít hơn hay bị loại.",
    less: "{reduced} món được chọn ít hơn.",
    lessNone: "Chưa có món nào được chọn ít hơn.",
    cut: "{excluded} món bị loại vì có từ {hard} lượt mượn trở lên.",
    cutCount: "{excluded} món bị loại.",
    cutNone: "Chưa món nào bị loại. Món có từ {hard} lượt mượn trở lên sẽ bị loại.",
    cutOff: "Mức loại đang là 0, nên không món nào bị loại.",
    bandsTitle: "Số món theo số lượt mượn",
    bandsHow: "Thanh gạch chéo: từ mức bắt đầu giảm trở lên. Hàng nét đứt đánh dấu mức đó.",
    colBand: "Số lượt mượn",
    colCount: "Số món",
    bandBelow: "Dưới {to}",
    bandRange: "{from} đến {to}",
    bandFrom: "Từ {from} trở lên",
    thrRow: "Mức bắt đầu giảm: {soft} lượt mượn",
    thrInside: "Mức bắt đầu giảm: {soft} lượt mượn, nằm trong dòng trên",
    unknown: "Chưa có số lượt mượn",
    topTitle: "{n} món có nhiều lượt mượn nhất",
    topHow: "Thanh gạch chéo: từ mức bắt đầu giảm trở lên. Đường nét đứt đánh dấu mức đó.",
    topHowHard: " Đường liền là mức loại.",
    tagSoft: "Giảm từ {soft}",
    tagHard: "Loại từ {hard}",
    uses: "{n} lượt mượn",
    noName: "Món chưa có tên",
    fxOut: "Loại: không được chọn",
    fxPct: "Còn {pct}% khả năng được chọn",
    fxLow: "Còn dưới 1% khả năng được chọn",
    fxHigh: "Còn trên 99% khả năng được chọn",
  };
  const TOP_MAX = 15;            // rows in the list of heaviest objects
  const AT_START = 40;           // a marker label starts at its line when the line is left of this percent, else it ends at the line
  let seq = 0;                   // ids for aria-labelledby, unique per render

  const fmt = (n) => Number(n).toLocaleString("vi-VN");
  const fill = (tpl, vars) => tpl.replace(/\{(\w+)\}/g, (_m, k) => (k in vars ? vars[k] : ""));
  function num(v) {              // a finite number, or null for anything else (null, "", true, NaN, objects)
    if (typeof v === "number") return isFinite(v) ? v : null;
    if (typeof v === "string" && v.trim() !== "") { const n = Number(v); return isFinite(n) ? n : null; }
    return null;
  }
  const count = (v) => { const n = num(v); return n === null || n < 0 ? 0 : Math.floor(n); };   // a whole number, 0 or more
  const share = (n, max) => (max > 0 ? Math.min(1, Math.max(0, n / max)) : 0);
  const pct = (r) => (r * 100).toFixed(2) + "%";
  const text = (v) => (typeof v === "string" ? v.trim() : typeof v === "number" && isFinite(v) ? String(v) : "");

  function effectOf(weight) {    // the chance multiplier in words; null = say nothing
    const x = num(weight);
    if (x === null) return null;
    if (x <= 0) return W.fxOut;
    if (x >= 1) return null;
    const p = Math.round(x * 100);
    return p < 1 ? W.fxLow : p > 99 ? W.fxHigh : fill(W.fxPct, { pct: fmt(p) });
  }

  function bandLabel(b) {
    if (b.to === null) return fill(W.bandFrom, { from: fmt(b.from) });
    if (b.from === 0) return fill(W.bandBelow, { to: fmt(b.to + 1) });
    return b.from === b.to ? fmt(b.from) : fill(W.bandRange, { from: fmt(b.from), to: fmt(b.to) });
  }

  function effectText(m) {       // sentence two: what the number means for picking
    if (!m.enabled) return W.switchOff;
    const less = m.reduced > 0 ? fill(W.less, { reduced: fmt(m.reduced) }) : W.lessNone;
    const vars = { excluded: fmt(m.excluded), hard: fmt(m.hard) };
    const cut = m.hard === 0 ? (m.excluded > 0 ? fill(W.cutCount, vars) : W.cutOff)
      : m.excluded > 0 ? fill(W.cut, vars) : fill(W.cutNone, vars);
    return less + " " + cut;
  }

  // Everything the page shows, worked out from the JSON and nothing else (no DOM): easy to test, and it never throws.
  function model(usage) {
    const u = usage && typeof usage === "object" ? usage : null;
    if (!u) return { state: "empty", bands: [], top: [], unk: null, divider: null };
    const soft = count(u.soft_start), hard = count(u.hard_limit), known = count(u.known), over = count(u.over_start);
    const m = {
      state: "over", soft, hard, known, over, total: Math.max(count(u.total), known, over), unknown: count(u.unknown),
      excluded: count(u.excluded), reduced: count(u.reduced), enabled: u.enabled !== false,
      bands: [], top: [], unk: null, divider: null, scaleMax: 0, t: null, h: null, lede: "", effect: "", warn: false,
    };
    if (soft <= 0) { m.state = "nosoft"; m.lede = W.noSoft; return m; }
    if (known <= 0) { m.state = "nodata"; m.lede = W.noData; return m; }
    const vars = { over: fmt(over), total: fmt(m.total), soft: fmt(soft) };
    m.warn = over > 0;
    m.state = m.warn ? "over" : "none";
    m.lede = fill(m.warn ? W.lede : W.ledeNone, vars);
    if (m.warn) m.effect = effectText(m);

    // bands: from the API in order of their lower edge; "over" = the whole band is at or past the threshold
    const raw = (Array.isArray(u.bands) ? u.bands : []).filter((b) => b && typeof b === "object" && num(b.from) !== null)
      .map((b) => ({ from: count(b.from), to: num(b.to) === null ? null : count(b.to), n: count(b.count) }))
      .sort((a, b) => a.from - b.from);
    const maxN = Math.max(0, m.unknown, ...raw.map((b) => b.n));
    let split = raw.findIndex((b) => b.from >= soft);          // first band that is over; the divider goes before it
    if (split < 0) split = raw.length;
    m.bands = raw.map((b, i) => ({ label: bandLabel(b), n: b.n, w: share(b.n, maxN), over: i >= split }));
    const inside = split > 0 && (raw[split - 1].to === null || raw[split - 1].to >= soft);   // the threshold falls inside the band above the divider
    if (raw.length) m.divider = { index: split, text: fill(inside ? W.thrInside : W.thrRow, vars), inside };
    if (m.unknown > 0) m.unk = { label: W.unknown, n: m.unknown, w: share(m.unknown, maxN) };

    // heaviest objects: sorted, linear from zero; the scale reaches at least the threshold so its line is always on the chart
    m.top = (Array.isArray(u.top) ? u.top : []).filter((t) => t && typeof t === "object").map((t) => {
      const vi = text(t.vi), en = text(t.name), id = text(t.id);
      return { id, name: vi || en || (id ? "" : W.noName), lang: !vi && en ? "en" : "", uses: count(t.uses),
        fx: m.enabled ? effectOf(t.weight) : null };
    }).sort((a, b) => b.uses - a.uses).slice(0, TOP_MAX);
    m.scaleMax = Math.max(soft, ...m.top.map((t) => t.uses));
    m.top.forEach((t) => { t.w = share(t.uses, m.scaleMax); t.over = t.uses >= soft; t.label = fill(W.uses, { n: fmt(t.uses) }); });
    m.t = share(soft, m.scaleMax) * 100;
    m.h = hard > 0 && hard <= m.scaleMax ? share(hard, m.scaleMax) * 100 : null;
    return m;
  }

  function h(doc, tag, cls, txt) {
    const e = doc.createElement(tag);
    if (cls) e.setAttribute("class", cls);
    if (txt) e.textContent = txt;
    return e;
  }
  function bar(doc, w, kind) {   // a track with a fill, hidden from screen readers: the printed number is the data
    const track = h(doc, "span", "bbu-track");
    track.setAttribute("aria-hidden", "true");
    if (w !== null) {
      const f = h(doc, "span", "bbu-fill" + (kind ? " is-" + kind : ""));
      f.style.setProperty("--w", pct(w));
      track.appendChild(f);
    }
    return track;
  }
  function bandRow(doc, label, n, w, kind) {
    const tr = h(doc, "tr");
    const th = h(doc, "th", null, label);
    th.setAttribute("scope", "row");
    const cell = h(doc, "div", "bbu-cell");
    cell.appendChild(bar(doc, n > 0 ? w : null, kind));
    cell.appendChild(h(doc, "span", "bbu-num", fmt(n)));
    const td = h(doc, "td");
    td.appendChild(cell);
    tr.appendChild(th);
    tr.appendChild(td);
    return tr;
  }

  function bandsTable(doc, m, uid) {
    const table = h(doc, "table", "bbu-bands");
    table.setAttribute("aria-labelledby", uid + "-h1");
    table.setAttribute("aria-describedby", uid + "-n1");
    const head = h(doc, "tr");
    for (const c of [W.colBand, W.colCount]) { const th = h(doc, "th", null, c); th.setAttribute("scope", "col"); head.appendChild(th); }
    table.appendChild(h(doc, "thead")).appendChild(head);
    if (m.divider) {                                          // rows under the threshold, then a labelled divider row heading the rows from it up
      const under = h(doc, "tbody"), over = h(doc, "tbody", "bbu-over");
      const div = h(doc, "tr", "bbu-thr");
      const dth = h(doc, "th", null, m.divider.text);
      dth.setAttribute("scope", "rowgroup");
      dth.setAttribute("colspan", "2");
      div.appendChild(dth);
      over.appendChild(div);
      m.bands.forEach((b, i) => (i < m.divider.index ? under : over).appendChild(bandRow(doc, b.label, b.n, b.w, b.over ? "over" : "under")));
      if (m.divider.index > 0) table.appendChild(under);
      table.appendChild(over);
    }
    if (m.unk) {
      const body = h(doc, "tbody", "bbu-unk");
      body.appendChild(bandRow(doc, m.unk.label, m.unk.n, m.unk.w, "unk"));
      table.appendChild(body);
    }
    return table;
  }

  function topList(doc, m, uid) {
    const ol = h(doc, "ol", "bbu-top" + (m.h !== null ? " has-hard" : ""));
    ol.setAttribute("role", "list");                          // Safari drops list semantics when the bullets are removed with CSS
    ol.setAttribute("aria-labelledby", uid + "-h2");
    ol.setAttribute("aria-describedby", uid + "-n2");
    ol.style.setProperty("--t", pct(m.t / 100));
    if (m.h !== null) ol.style.setProperty("--h", pct(m.h / 100));
    const last = m.top.length - 1;
    m.top.forEach((t, i) => {
      const li = h(doc, "li");
      const nm = h(doc, "span", "bbu-nm");
      if (t.id) nm.appendChild(h(doc, "span", "bbu-id", t.id));
      if (t.id && t.name) nm.appendChild(doc.createTextNode(" "));
      if (t.name) { const n = h(doc, "span", "bbu-name", t.name); if (t.lang) n.setAttribute("lang", t.lang); nm.appendChild(n); }
      li.appendChild(nm);
      li.appendChild(doc.createTextNode(" "));
      li.appendChild(h(doc, "span", "bbu-val", t.label));
      if (t.fx) { li.appendChild(doc.createTextNode(" ")); li.appendChild(h(doc, "span", "bbu-fx", t.fx)); }
      const track = bar(doc, t.uses > 0 ? t.w : null, t.over ? "over" : "under");
      if (i === 0) track.appendChild(h(doc, "span", "bbu-tag is-soft " + (m.t <= AT_START ? "at-start" : "at-end"), fill(W.tagSoft, { soft: fmt(m.soft) })));
      if (i === last && m.h !== null) track.appendChild(h(doc, "span", "bbu-tag is-hard " + (m.h <= AT_START ? "at-start" : "at-end"), fill(W.tagHard, { hard: fmt(m.hard) })));
      li.appendChild(track);
      ol.appendChild(li);
    });
    return ol;
  }

  function render(container, usage) {
    if (!container) return null;
    const doc = container.ownerDocument || (typeof document !== "undefined" ? document : null);
    if (!doc) return null;
    const m = model(usage);
    const uid = "bbu" + (++seq);
    container.textContent = "";
    if (m.state === "empty") return null;
    const root = h(doc, "div", "bbu");
    root.setAttribute("data-state", m.state);

    const lede = h(doc, "p", "bbu-lede" + (m.warn ? "" : " is-calm"));
    if (m.warn) lede.appendChild(h(doc, "span", "bbu-vh", W.warn));
    lede.appendChild(doc.createTextNode(m.lede));
    root.appendChild(lede);
    if (m.effect) root.appendChild(h(doc, "p", "bbu-effect", m.effect));

    if (m.state === "over" || m.state === "none") {
      if (m.bands.length || m.unk) {
        const h1 = h(doc, "h3", "bbu-h", W.bandsTitle);
        h1.setAttribute("id", uid + "-h1");
        const n1 = h(doc, "p", "bbu-note", W.bandsHow);
        n1.setAttribute("id", uid + "-n1");
        root.appendChild(h1);
        root.appendChild(n1);
        root.appendChild(bandsTable(doc, m, uid));
      }
      if (m.top.length) {
        const h2 = h(doc, "h3", "bbu-h", fill(W.topTitle, { n: fmt(m.top.length) }));
        h2.setAttribute("id", uid + "-h2");
        const n2 = h(doc, "p", "bbu-note", W.topHow + (m.h !== null ? W.topHowHard : ""));
        n2.setAttribute("id", uid + "-n2");
        root.appendChild(h2);
        root.appendChild(n2);
        root.appendChild(topList(doc, m, uid));
      }
    }
    container.appendChild(root);
    return root;
  }

  const api = { render, model, effectOf, W };
  if (typeof window !== "undefined") window.BBUsageChart = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})();

"use strict";
/* Box builder front end. No framework, no build step. All text goes through textContent (never innerHTML). */

const PALETTE = ["#17776f", "#4659c9", "#b0346f", "#657214", "#7048b8", "#1f6fa8", "#8a5528", "#4f5d66"];
const PRESETS = [
  ["Bếp và bàn ăn", "bàn bếp và bàn ăn (bát đĩa, đồ nấu nướng, thực phẩm, dụng cụ nhà bếp)"],
  ["Bàn làm việc và thủ công", "góc bàn làm việc và thủ công (văn phòng phẩm, dụng cụ nhỏ, đồ chơi, hộp đựng)"],
  ["Dọn dẹp", "khu vực dọn dẹp và giặt giũ (bàn chải, miếng rửa, hộp đựng, khăn nhỏ)"],
  ["Làm vườn", "khu vườn nhỏ trong nhà (chậu, cây, dụng cụ cầm tay, đồ tưới cây)"],
  ["Thú cưng", "góc chăm sóc thú cưng (đồ chơi, bát ăn, đồ chải lông, hộp đựng nhỏ)"],
  ["Chăm sóc cá nhân", "bàn trang điểm (mỹ phẩm, cọ, phụ kiện, hộp đựng nhỏ)"],
];
const ICONS = {
  lock: '<path d="M4 7.5h8v6H4z"/><path d="M5.7 7.5V5.6a2.3 2.3 0 0 1 4.6 0v1.9"/>',
  unlock: '<path d="M4 7.5h8v6H4z"/><path d="M5.7 7.5V5.6a2.3 2.3 0 0 1 4.3-1.1"/>',
  swap: '<path d="M13 8a5 5 0 1 1-1.5-3.6"/><path d="M13 2.8v3.1h-3.1"/>',
  x: '<path d="M4 4l8 8M12 4l-8 8"/>',
};

const $ = (s, el = document) => el.querySelector(s);
const fmt = (n) => Number(n).toLocaleString("vi-VN");
const store = {
  get(k) { try { return localStorage.getItem(k) || ""; } catch { return ""; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: ignore */ } },
};

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  return el;
}
function icon(name) {
  const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  s.setAttribute("viewBox", "0 0 16 16");
  s.setAttribute("class", "i");
  s.setAttribute("aria-hidden", "true");
  s.innerHTML = ICONS[name]; // static strings above, never user text
  return s;
}
const debounce = (fn, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
const natural = (a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });

/* ------------------------------------------------------------------ app state */
let cfg = { default_count: 21, max_qty: 3, needs_code: false, llm: "mock" };
let code = store.get("bb_code");
let rev = -1;
let data = null;
let categories = [];
const chosenCats = new Set();
const ui = {};                          // per box: {q, results, confirm}
const cache = new Map();                // box id -> {el, sig}
const uiOf = (id) => (ui[id] ||= { q: "", results: null, confirm: false });

/* ------------------------------------------------------------------ talking to the server */
async function api(path, opts = {}) {
  const res = await fetch(path, {
    method: opts.method || "GET",
    headers: { "Content-Type": "application/json", "X-Access-Code": code },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 401) throw Object.assign(new Error("Cần nhập mã truy cập"), { status: 401 });
  let json = null;
  try { json = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const d = json && json.detail;
    const msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : `Yêu cầu thất bại (${res.status})`;
    throw Object.assign(new Error(msg), { status: res.status });
  }
  return json;
}

function toast(msg, kind) {
  const t = h("div", { class: "toast" + (kind === "bad" ? " bad" : "") }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), kind === "bad" ? 8000 : 4500);
}
const fail = (e) => { if (e.status !== 401) toast(e.message, "bad"); else showGate(); };

async function copyText(text) {
  try {
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* fall through */ }
  const ta = h("textarea", { style: "position:fixed;top:0;left:0;opacity:0" });
  ta.value = text;
  document.body.append(ta);
  ta.select();
  let ok = false;
  try { ok = document.execCommand("copy"); } catch { /* ignore */ }
  ta.remove();
  return ok;
}

/* ------------------------------------------------------------------ rendering */
function setLive(ok) {
  const live = $("#live");
  live.classList.toggle("off", !ok);
  $("b", live).textContent = ok ? "Trực tiếp" : "Mất kết nối, đang thử lại";
}

function renderKho() {
  const chip = $("#kho");
  const c = data && data.catalog;
  if (!c) return;
  chip.hidden = false;
  if (c.source !== "live") {
    chip.textContent = "Kho: file CSV, chưa đồng bộ với SM";
    chip.classList.add("warn");
    return;
  }
  const min = Math.max(0, Math.round((Date.now() / 1000 - c.synced_at) / 60));
  const ago = min < 1 ? "vừa xong" : min < 60 ? `${min} phút trước` : `${Math.round(min / 60)} giờ trước`;
  chip.textContent = `Kho SM: cập nhật ${ago}`;
  chip.title = `${fmt(c.usable)} món dùng được trên ${fmt(c.received)} món SM trả về`;
  chip.classList.toggle("warn", min >= 60);       // a stale list is worse than none: say so
}

function renderLedger(d) {
  const { stats, boxes } = d;
  $("#free-n").textContent = fmt(stats.free);
  $("#ledger-sub").textContent = `${fmt(stats.catalog)} món dùng được trong kho` + (stats.gone ? ` · ⚠ ${fmt(stats.gone)} món trong hộp không còn tồn kho ở SM` : "");
  const bar = $("#bar"), legend = $("#legend");
  bar.replaceChildren();
  legend.replaceChildren();
  const seg = (grow, cls, color, title) => h("span", { class: cls, style: `flex:${grow} ${grow} 0;${color ? "--c:" + color : ""}`, title });
  for (const b of boxes) {
    const n = b.items.length;
    if (!n) continue;
    const color = colorOf(b);
    bar.append(seg(n, "", color, `${b.module} ${b.label}: ${n} món`));
    legend.append(h("li", { style: "--c:" + color }, h("i"), h("b", {}, `${b.module} ${b.label}`), `${n} món`));
  }
  if (stats.external) {
    bar.append(seg(stats.external, "ext", "", `Đã dùng trong bảng tính: ${stats.external}`));
    legend.append(h("li", {}, h("i", { class: "ext" }), h("b", {}, "Đã dùng trong bảng tính"), `${fmt(stats.external)} món`));
  }
  bar.append(seg(Math.max(stats.free, 0.0001), "free", "", `Còn trống: ${stats.free}`));
  bar.setAttribute("aria-label", `${fmt(stats.free)} trên ${fmt(stats.catalog)} món còn trống`);
}

function renderUsed(d) {
  const wrap = $("#u-list-wrap");
  wrap.hidden = d.used.length === 0;
  $("#u-count").textContent = `${fmt(d.used.length)} món đã đánh dấu là đã dùng`;
  const ul = $("#u-list");
  ul.replaceChildren(...d.used.slice(0, 400).map((u) =>
    h("li", {}, h("button", { type: "button", title: (u.note ? u.note + ". " : "") + "Bấm để trả món này về kho", "aria-label": `Trả ${u.id} về kho`,
      onclick: () => api("/api/used/" + u.id, { method: "DELETE" }).then(() => poll(true)).catch(fail) }, u.id, icon("x")))));
}

const colorOf = (b) => PALETTE[(b.id - 1) % PALETTE.length];

function renderBoxes(d) {
  const root = $("#boxes");
  const sorted = [...d.boxes].sort((a, b) => natural(a.module, b.module) || natural(a.label, b.label));
  const alive = new Set(sorted.map((b) => b.id));
  for (const [id, c] of cache) if (!alive.has(id)) { c.el.remove(); cache.delete(id); }
  $(".empty-state", root)?.remove();

  for (const box of sorted) mountBox(box);

  const want = sorted.map((b) => String(b.id));
  const have = [...root.querySelectorAll(":scope > .tray")].map((e) => e.dataset.id);
  if (want.join() !== have.join()) for (const b of sorted) root.append(cache.get(b.id).el);

  if (!sorted.length) root.append(emptyState());
}

function mountBox(box) {
  const u = uiOf(box.id);
  const sig = JSON.stringify([box, u.q, u.results, u.confirm]);
  const old = cache.get(box.id);
  if (old && old.sig === sig) return;
  // keep keyboard focus (and caret) across a re-render
  const active = document.activeElement;
  const keep = old && old.el.contains(active) && active.dataset ? active.dataset.keep : null;
  const caret = keep && "selectionStart" in active ? active.selectionStart : null;
  const el = buildBox(box, u);
  if (old) old.el.replaceWith(el); else $("#boxes").append(el);
  cache.set(box.id, { el, sig });
  if (keep) {
    const n = el.querySelector(`[data-keep="${keep}"]`);
    if (n && !n.disabled) { n.focus(); if (caret != null) try { n.setSelectionRange(caret, caret); } catch { /* not a text field */ } }
  }
}

function emptyState() {
  return h("div", { class: "empty-state" },
    h("div", { class: "msg" },
      h("h2", {}, "Chưa có hộp nào"),
      h("p", {}, "Mô tả bối cảnh ở bên trái rồi bấm Tạo hộp. Trang sẽ chọn đồ và đảm bảo không món nào trùng với hộp khác.")),
    h("div", { class: "ghost-tray", "aria-hidden": "true" }, Array.from({ length: 21 }, () => h("i"))));
}

const STATUS = { generating: "Đang chọn đồ", draft: "Nháp", approved: "Đã duyệt", error: "Cần xử lý" };

function buildBox(box, u) {
  const n = box.items.length;
  const byPos = new Map(box.items.map((i) => [i.pos, i]));
  const editable = box.status === "draft" || box.status === "error";
  const slots = [];
  for (let p = 1; p <= box.count; p++) slots.push(slotEl(box, p, byPos.get(p), editable));

  const filled = box.items.filter((i) => i.source === "fill").length;
  let note = null;
  if (box.status === "error") note = h("p", { class: "note bad", role: "alert" }, box.message);
  else if (box.status === "generating" && box.message) note = h("p", { class: "note" }, box.message);
  else if (filled) note = h("p", { class: "note" }, `${filled} món do hệ thống tự điền (viền nét đứt) vì mô hình trả về thiếu món hợp lệ. Hãy kiểm tra lại hoặc bấm Chọn lại.`);

  return h("article", { class: "tray", "data-id": box.id, style: "--c:" + colorOf(box), "aria-label": `${box.module} ${box.label}` },
    h("header", { class: "tray-head" },
      h("div", { class: "tray-title" },
        h("span", { class: "mod" }, box.module),
        h("h2", {}, box.label),
        h("span", { class: "pill " + box.status }, STATUS[box.status]),
        box.pushed ? h("span", { class: "pill pushed", title: "Box ID trong SM: " + box.pushed.sm_box_id }, "Đã đẩy lên SM") : null,
        box.owner ? h("span", { class: "by" }, "Tạo bởi " + box.owner) : null),
      box.title ? h("p", { class: "tb-title", title: "Tiêu đề trong SM TaskBox" }, box.title) : null,
      box.theme ? h("p", { class: "theme" }, box.theme) : null,
      note),
    h("div", { class: "slots", style: `--rows3:${Math.ceil(box.count / 3)};--rows2:${Math.ceil(box.count / 2)}` }, slots),
    footEl(box, u, n, editable));
}

function slotEl(box, pos, it, editable) {
  const gen = box.status === "generating";
  if (!it) {
    return h("div", { class: "slot empty" + (gen ? " wait" : "") },
      h("span", { class: "stt" }, pos), h("div", { class: "body" }, gen ? "Đang chọn…" : "Ô trống"));
  }
  const cap = Math.max(1, Math.min(cfg.max_qty, it.stock || 1));
  const tip = [it.name, it.vi, it.category, `Tồn ${it.stock}, vị trí ${it.slot}`].filter(Boolean).join("\n");
  const act = (fn) => () => fn().then(() => poll(true)).catch(fail);
  const base = `/api/boxes/${box.id}/items/${pos}`;
  return h("div", { class: "slot" + (it.locked ? " locked" : "") + (it.source === "fill" ? " fill" : ""), title: tip },
    h("span", { class: "stt", title: it.source === "fill" ? "Do hệ thống tự điền, không phải do mô hình chọn" : null }, pos),
    h("div", { class: "body" },
      h("div", { class: "l1" },
        h("span", { class: "oid" }, it.id),
        h("button", { class: "qty", type: "button", "data-keep": `q${pos}`, disabled: !editable || cap < 2,
          title: cap < 2 ? "Chỉ còn 1 trong kho" : "Số lượng trong hộp này. Bấm để đổi",
          "aria-label": `Số lượng ${it.qty}${cap < 2 ? "" : ", bấm để đổi"}`,
          onclick: act(() => api(base, { method: "PATCH", body: { qty: it.qty >= cap ? 1 : it.qty + 1 } })) }, "×" + it.qty),
        h("span", { class: "cat" }, it.category)),
      h("div", { class: "en" }, it.name),
      h("div", { class: "vi" }, it.vi)),
    editable ? h("div", { class: "tools" },
      h("button", { class: "tool" + (it.locked ? " on" : ""), type: "button", "data-keep": `l${pos}`,
        title: it.locked ? "Mở khoá món này" : "Khoá món này để Chọn lại giữ nguyên",
        "aria-label": (it.locked ? "Mở khoá " : "Khoá ") + it.id, "aria-pressed": it.locked ? "true" : "false",
        onclick: act(() => api(base + "/lock", { method: "POST", body: { locked: !it.locked } })) }, icon(it.locked ? "lock" : "unlock")),
      h("button", { class: "tool", type: "button", "data-keep": `r${pos}`, disabled: it.locked,
        title: it.locked ? "Mở khoá trước khi thay" : "Thay bằng món khác", "aria-label": "Thay " + it.id,
        onclick: act(() => api(base + "/replace", { method: "POST" })) }, icon("swap")),
      h("button", { class: "tool", type: "button", "data-keep": `x${pos}`, title: "Bỏ khỏi hộp này", "aria-label": "Bỏ " + it.id,
        onclick: act(() => api(base, { method: "DELETE" })) }, icon("x"))) : null);
}

function footEl(box, u, n, editable) {
  const id = box.id;
  const gen = box.status === "generating";
  const call = (path, opts, msg) => () => api(`/api/boxes/${id}${path}`, opts)
    .then(() => { if (msg) toast(msg); return poll(true); }).catch(fail);
  const btn = (label, onclick, o = {}) => h("button", {
    type: "button", class: "btn" + (o.cls ? " " + o.cls : ""), "data-keep": o.keep, disabled: o.disabled, title: o.title, onclick }, label);
  const name = `${box.module} ${box.label}`;
  const kids = [];

  if (!gen) {
    kids.push(
      btn("Sao chép ID", async () => {
        const text = Array.from({ length: box.count }, (_, i) => (box.items.find((x) => x.pos === i + 1) || {}).id || "").join("\n");
        toast((await copyText(text)) ? `Đã sao chép ${n} ID. Dán vào cột Object ID.` : "Trình duyệt chặn việc sao chép. Hãy chọn ID bằng tay.", "");
      }, { keep: "copyids", disabled: !n, title: "Mỗi dòng một ID theo thứ tự STT, giữ số 0 ở đầu" }),
      btn("Sao chép số lượng", async () => {
        const text = Array.from({ length: box.count }, (_, i) => String((box.items.find((x) => x.pos === i + 1) || {}).qty || "")).join("\n");
        toast((await copyText(text)) ? `Đã sao chép ${n} số lượng. Dán vào cột SL.` : "Trình duyệt chặn việc sao chép.", "");
      }, { keep: "copyqty", disabled: !n, title: "Mỗi dòng một số lượng theo thứ tự STT" }));
  }
  if (editable) {
    kids.push(findEl(box, u));
  }
  kids.push(h("span", { class: "grow" }));
  if (editable) {
    if (n < box.count) kids.push(btn("Điền ô trống", call("/fill", { method: "POST" }), { keep: "fill" }));
    kids.push(btn("Chọn lại", call("/regenerate", { method: "POST" }), { keep: "regen", title: "Thay mọi món chưa khoá" }));
    kids.push(btn("Duyệt", call("/approve", { method: "POST" }, `Đã duyệt ${name}`), { cls: "primary", keep: "approve", disabled: n < box.count,
      title: n < box.count ? `Cần đủ ${box.count} món` : "Chốt hộp. Các món vẫn được giữ chỗ." }));
  } else if (box.status === "approved") {
    if (!box.pushed) kids.push(btn("Mở lại", call("/reopen", { method: "POST" }), { keep: "reopen" }));
  }
  if (u.confirm) {
    kids.push(h("span", { class: "confirm" }, `Trả ${n} món về kho?`,
      btn("Huỷ hộp", call("", { method: "DELETE" }, `Đã huỷ ${name}`), { cls: "danger small", keep: "discard-yes" }),
      btn("Giữ lại", () => { u.confirm = false; remount(box.id); }, { cls: "small", keep: "discard-no" })));
  } else {
    if (!box.pushed) kids.push(btn("Huỷ", () => { u.confirm = true; remount(box.id); }, { cls: "danger", keep: "discard" }));
  }
  if (box.status === "approved") {
    kids.push(h("p", { class: "sheet-tip" }, "Trước khi dán vào bảng tính, hãy đặt các ô Object ID thành Plain text để 004702 giữ số 0 ở đầu."));
  }
  return h("footer", { class: "tray-foot" }, kids);
}

function findEl(box, u) {
  const full = box.items.length >= box.count;
  const input = h("input", {
    type: "search", "data-keep": "find", placeholder: full ? "Hộp đã đầy" : "Thêm món theo tên hoặc ID", disabled: full,
    "aria-label": "Thêm món theo tên hoặc ID", autocomplete: "off",
    oninput: (e) => { u.q = e.target.value; debounced(box.id)(u.q); },
    onkeydown: (e) => { if (e.key === "Escape") { u.q = ""; u.results = null; remount(box.id); } },
  });
  input.value = u.q;
  let list = null;
  if (u.q.trim().length >= 2 && u.results) {
    list = h("div", { class: "results" }, u.results.length
      ? u.results.map((r) => h("button", { type: "button", onclick: () => addObject(box, r) },
          h("b", {}, r.id), h("span", {}, r.name), h("small", {}, `${r.vi} (${r.category || "chưa phân loại"})`)))
      : h("p", {}, "Không có món trống nào khớp. Có thể món đó đã nằm trong hộp khác."));
  }
  return h("div", { class: "find" }, input, list);
}

const debouncers = new Map();
function debounced(id) {
  if (!debouncers.has(id)) debouncers.set(id, debounce(async (q) => {
    const u = uiOf(id);
    if (q.trim().length < 2) { u.results = null; return remount(id); }
    try { u.results = await api("/api/search?q=" + encodeURIComponent(q.trim())); } catch (e) { return fail(e); }
    if (u.q === q) remount(id);
  }, 220));
  return debouncers.get(id);
}
async function addObject(box, r) {
  const u = uiOf(box.id);
  try {
    const res = await api(`/api/boxes/${box.id}/items`, { method: "POST", body: { object_id: r.id, qty: 1 } });
    toast(`Đã thêm ${r.id} vào ô ${res.pos} và khoá lại.`);
    u.q = ""; u.results = null;
    await poll(true);
  } catch (e) { fail(e); }
}
function remount(id) {
  const box = data && data.boxes.find((b) => b.id === id);
  if (box) mountBox(box);
}

/* ------------------------------------------------------------------ polling */
async function poll(force) {
  if (document.hidden && !force) return;
  try {
    const d = await api("/api/state?rev=" + (force ? -1 : rev));
    setLive(true);
    $("#gate").hidden = true;
    if (d.unchanged) return;
    rev = d.rev;
    data = d;
    renderKho();
    renderLedger(d);
    renderUsed(d);
    renderBoxes(d);
  } catch (e) {
    if (e.status === 401) showGate(); else setLive(false);
  }
}
function schedule() {
  const busy = data && data.boxes.some((b) => b.status === "generating");
  setTimeout(async () => { await poll(false); schedule(); }, busy ? 1000 : 2500);
}

/* ------------------------------------------------------------------ forms */
function showGate() { $("#gate").hidden = false; $("#gate-code").focus(); }

function setupGate() {
  $("#gate-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    code = $("#gate-code").value;
    $("#gate-err").hidden = true;
    try { await api("/api/state?rev=-1"); store.set("bb_code", code); $("#gate").hidden = true; updateExport(); await poll(true); }
    catch { $("#gate-err").hidden = false; }
  });
}
function updateExport() { $("#export").href = "/api/export.csv" + (code ? "?access_code=" + encodeURIComponent(code) : ""); }

function setupNewBox() {
  const theme = $("#f-theme");
  const presets = $("#presets");
  for (const [label, text] of PRESETS) presets.append(h("button", { type: "button", onclick: () => { theme.value = text; theme.focus(); } }, label));
  $("#f-module").value = store.get("bb_module");
  $("#new").addEventListener("submit", async (e) => {
    e.preventDefault();
    const go = $("#f-go");
    const body = {
      module: $("#f-module").value.trim(), label: $("#f-label").value.trim(), theme: theme.value.trim(),
      categories: [...chosenCats], count: parseInt($("#f-count").value, 10) || cfg.default_count, owner: $("#who").value.trim(),
      code: $("#f-code").value.trim(), anchor: $("#f-anchor").value.trim(),
    };
    store.set("bb_module", body.module);
    go.disabled = true;
    try {
      await api("/api/boxes", { method: "POST", body });
      toast(`Đang chọn ${body.count} món cho ${body.module} ${body.label}.`);
      await poll(true);
    } catch (err) { fail(err); } finally { go.disabled = false; }
  });
}

function setupUsed() {
  $("#used").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#u-text").value;
    if (!text.trim()) return toast("Hãy dán ít nhất một Object ID.", "bad");
    try {
      const r = await api("/api/used", { method: "POST", body: { text, note: $("#u-note").value } });
      const bits = [`Đã đánh dấu ${fmt(r.added)} món là đã dùng.`];
      if (r.already_in_a_box.length) bits.push(`${r.already_in_a_box.length} món đã nằm trong một hộp ở đây.`);
      if (r.not_in_catalog.length) bits.push(`${r.not_in_catalog.length} món không có trong kho.`);
      if (r.unreadable.length) bits.push(`Không đọc được: ${r.unreadable.slice(0, 4).join(", ")}.`);
      toast(bits.join(" "));
      $("#u-text").value = "";
      await poll(true);
    } catch (err) { fail(err); }
  });
}

function setupCategories() {
  const list = $("#cats-list"), q = $("#cats-q");
  const draw = () => {
    const needle = q.value.trim().toLowerCase();
    list.replaceChildren(...categories.filter((c) => c.name.toLowerCase().includes(needle)).map((c) =>
      h("label", {}, h("input", { type: "checkbox", checked: chosenCats.has(c.name), onchange: (e) => {
        e.target.checked ? chosenCats.add(c.name) : chosenCats.delete(c.name); sum(); } }), c.name, h("small", {}, c.count))));
  };
  const sum = () => { $("#cats-sum").textContent = chosenCats.size ? `đã chọn ${chosenCats.size}` : "Tất cả nhóm"; };
  q.addEventListener("input", draw);
  $("#cats-clear").addEventListener("click", () => { chosenCats.clear(); draw(); sum(); });
  api("/api/categories").then((c) => { categories = c; draw(); }).catch(() => {});
}

/* ------------------------------------------------------------------ start */
async function init() {
  const who = $("#who");
  who.value = store.get("bb_name");
  who.addEventListener("input", () => store.set("bb_name", who.value));
  try { cfg = await (await fetch("/api/config")).json(); } catch { /* keep defaults */ }
  $("#f-count").value = cfg.default_count;
  const chip = $("#llm");
  chip.hidden = false;
  chip.textContent = cfg.llm === "mock" ? "Chọn thử, chưa nối mô hình" : `DeepSeek ${cfg.model}`;
  chip.classList.toggle("warn", cfg.llm === "mock");
  setupGate(); setupNewBox(); setupUsed();
  updateExport();
  await poll(true);
  if (!$("#gate").hidden) await new Promise((r) => { const t = setInterval(() => { if ($("#gate").hidden) { clearInterval(t); r(); } }, 300); });
  setupCategories();
  schedule();
  setInterval(renderKho, 30000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(true); });
}
init();

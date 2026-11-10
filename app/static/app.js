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
const batchCats = new Set();
const ui = {};                          // per box: {q, results, confirm}
const cache = new Map();                // box id -> {el, sig}
const uiOf = (id) => (ui[id] ||= { q: "", results: null, confirm: false });

/* ------------------------------------------------------------------ talking to the server */
async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      method: opts.method || "GET",
      headers: { "Content-Type": opts.raw !== undefined ? "text/csv" : "application/json", "X-Access-Code": code },
      body: opts.raw !== undefined ? opts.raw : opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch {
    throw Object.assign(new Error("Không kết nối được máy chủ. Kiểm tra mạng rồi thử lại."), { status: 0 });
  }
  if (res.status === 401) throw Object.assign(new Error("Cần nhập mã truy cập"), { status: 401 });
  let json = null;
  try { json = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const d = json && json.detail;
    const msg = typeof d === "string" ? d
      : Array.isArray(d) ? "Thông tin nhập chưa đúng. Hãy kiểm tra lại các ô đã điền."
      : `Có lỗi xảy ra (mã ${res.status}). Hãy thử lại. Nếu vẫn lỗi, báo cho người quản lý.`;
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
  if (stats.in_sm) {
    bar.append(seg(stats.in_sm, "sm", "", `Trong hộp SM: ${stats.in_sm}`));
    legend.append(h("li", {}, h("i", { class: "sm" }), h("b", {}, "Trong hộp SM"), `${fmt(stats.in_sm)} món`));
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
    h("li", { class: u.reusable ? "reusable" : "" }, h("button", { type: "button",
      title: [u.note, u.added_at ? "Hộp tạo ngày " + new Date(u.added_at * 1000).toLocaleDateString("vi-VN") : "Chưa rõ ngày tạo hộp, nên luôn được giữ", u.reusable ? "Đã đủ cũ, món này dùng lại được" : "", "Bấm để trả món này về kho"].filter(Boolean).join(". "),
      "aria-label": `Trả ${u.id} về kho${u.reusable ? " (đã đủ cũ, dùng lại được)" : ""}`,
      onclick: () => api("/api/used/" + u.id, { method: "DELETE" }).then(() => poll(true)).catch(fail) }, u.id, icon("x")))));
  const free = d.used.filter((u) => u.reusable).length;
  $("#u-count").textContent += free ? `, trong đó ${fmt(free)} món đã đủ cũ để dùng lại` : "";
}

const colorOf = (b) => PALETTE[(b.id - 1) % PALETTE.length];

/* Trays are grouped by module and SM section code (M8, M8-02, ...). Headers show only when there is more than one group. */
const secOf = (b) => (b.code && b.code !== b.module ? b.code : "");
const groupKey = (b) => b.module + "\u0001" + secOf(b);
const heads = new Map();                // group key -> header element, reused so reordering does not rebuild it

function groupHead(key, b, n) {
  let el = heads.get(key);
  if (!el) { el = h("h3", { class: "group-head" }, h("span"), h("small")); heads.set(key, el); }
  el.firstChild.textContent = secOf(b) ? `${b.module} · ${secOf(b)}` : b.module;
  el.lastChild.textContent = `${n} hộp`;
  return el;
}

function renderBoxes(d) {
  const root = $("#boxes");
  const sorted = [...d.boxes].sort((a, b) => natural(a.module, b.module) || natural(secOf(a), secOf(b)) || natural(a.label, b.label));
  const alive = new Set(sorted.map((b) => b.id));
  for (const [id, c] of cache) if (!alive.has(id)) { c.el.remove(); cache.delete(id); }
  $(".empty-state", root)?.remove();

  for (const box of sorted) mountBox(box);

  const counts = new Map();
  for (const b of sorted) counts.set(groupKey(b), (counts.get(groupKey(b)) || 0) + 1);
  const nodes = [];
  let last = null;
  for (const b of sorted) {
    const k = groupKey(b);
    if (counts.size > 1 && k !== last) nodes.push(groupHead(k, b, counts.get(k)));
    last = k;
    nodes.push(cache.get(b.id).el);
  }
  const have = [...root.children];
  if (nodes.length !== have.length || nodes.some((n, i) => n !== have[i])) root.replaceChildren(...nodes);

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
        secOf(box) ? h("span", { class: "sec", title: "Mã section trong SM" }, secOf(box)) : null,
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
  return h("div", { class: "slot pic" + (it.locked ? " locked" : "") + (it.source === "fill" ? " fill" : ""), title: tip },
    h("span", { class: "stt", title: it.source === "fill" ? "Do hệ thống tự điền, không phải do mô hình chọn" : null }, pos),
    h("div", { class: "pic" }, BBThumbs.thumb(it, { size: "sm" })),
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
          BBThumbs.thumb(r, { size: "sm" }), h("b", {}, r.id), h("span", {}, r.name), h("small", {}, `${r.vi} (${r.category || "chưa phân loại"})`)))
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
    BBThumbs.setServerNow(d.now);                      // picture end times are server times
    renderKho();
    renderLedger(d);
    renderUsed(d);
    renderBoxes(d);
    renderNextStep(d);
    checkDup();
    if (view === "objects") loadObjectsSoon();
    if (view === "auto") refreshAuto();
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
function updateExport() {
  const q = code ? "?access_code=" + encodeURIComponent(code) : "";
  $("#export").href = "/api/export.csv" + q;
  $("#print-link").href = "/print/boxes" + q;
}

function setupNewBox() {
  const theme = $("#f-theme");
  const presets = $("#presets");
  for (const [label, text] of PRESETS) presets.append(h("button", { type: "button", onclick: () => { theme.value = text; theme.focus(); } }, label));
  $("#f-module").value = store.get("bb_module");
  $("#new").addEventListener("submit", async (e) => {
    e.preventDefault();
    const go = $("#f-go");
    const body = {
      module: normModule($("#f-module").value), label: $("#f-label").value.trim(), theme: theme.value.trim(),
      categories: [...chosenCats], count: parseInt($("#f-count").value, 10) || cfg.default_count, owner: $("#who").value.trim(),
      code: $("#f-code").value.trim(), anchor: $("#f-anchor").value.trim(),
    };
    store.set("bb_module", body.module);
    go.disabled = true;
    try {
      await api("/api/boxes", { method: "POST", body });
      toast(`Đang chọn ${body.count} món cho ${body.module} ${body.label}.`);
      await poll(true);
    } catch (err) { fail(err); } finally { go.disabled = false; checkDup(); }
  });
}

function setupUsed() {
  $("#used").addEventListener("submit", async (e) => {
    e.preventDefault();
    const text = $("#u-text").value;
    if (!text.trim()) return toast("Hãy dán ít nhất một Object ID.", "bad");
    try {
      const r = await api("/api/used", { method: "POST", body: { text, note: $("#u-note").value, since: $("#u-since").value } });
      const bits = [`Đã đánh dấu ${fmt(r.added)} món là đã dùng.`];
      if (r.refreshed) bits.push(`${fmt(r.refreshed)} món đã có, được ghi ngày mới.`);
      if (r.kept) bits.push(`${fmt(r.kept)} món đã có trong danh sách, giữ nguyên ngày cũ. Muốn đổi, chọn ngày ở ô ngày.`);
      if (r.reusable_on && (r.added || r.refreshed)) bits.push(`Dùng lại được từ ${new Date(r.reusable_on + "T12:00:00Z").toLocaleDateString("vi-VN")}.`);
      if (r.already_in_a_box.length) bits.push(`${r.already_in_a_box.length} món đã nằm trong một hộp ở đây.`);
      if (r.not_in_catalog.length) bits.push(`${r.not_in_catalog.length} món không có trong kho.`);
      if (r.unreadable.length) bits.push(`Không đọc được: ${r.unreadable.slice(0, 4).join(", ")}.`);
      toast(bits.join(" "));
      $("#u-text").value = "";
      $("#u-since").value = "";                           // the day is for this paste only: a stale day must never free the next batch
      await poll(true);
    } catch (err) {
      fail(err);
      if (/ngày|năm/i.test(err.message || "")) { $("#u-since").setAttribute("aria-invalid", "true"); $("#u-since").focus(); }
    }
  });
  $("#u-since").addEventListener("input", () => $("#u-since").removeAttribute("aria-invalid"));
}

/* One category picker, used by the new-box form and the batch form. Returns a function that redraws it. */
function bindCategoryPicker(chosen, ids) {
  const list = $(ids.list), q = $(ids.q);
  const sum = () => { $(ids.sum).textContent = chosen.size ? `đã chọn ${chosen.size}` : "Tất cả nhóm"; };
  const draw = () => {
    const needle = q.value.trim().toLowerCase();
    list.replaceChildren(...categories.filter((c) => c.name.toLowerCase().includes(needle)).map((c) =>
      h("label", {}, h("input", { type: "checkbox", checked: chosen.has(c.name), onchange: (e) => {
        e.target.checked ? chosen.add(c.name) : chosen.delete(c.name); sum(); } }), c.name, h("small", {}, c.count))));
  };
  q.addEventListener("input", draw);
  $(ids.clear).addEventListener("click", () => { chosen.clear(); draw(); sum(); });
  return () => { draw(); sum(); };
}

function setupCategories() {
  const redraw = [
    bindCategoryPicker(chosenCats, { list: "#cats-list", q: "#cats-q", sum: "#cats-sum", clear: "#cats-clear" }),
    bindCategoryPicker(batchCats, { list: "#b-cats-list", q: "#b-cats-q", sum: "#b-cats-sum", clear: "#b-cats-clear" }),
  ];
  api("/api/categories").then((c) => { categories = c; redraw.forEach((f) => f()); }).catch(() => {});
}

/* ------------------------------------------------------------------ tabs */
const VIEWS = { boxes: "hop", objects: "kho", auto: "tudong" };
let view = "boxes";

function showView(name) {
  view = VIEWS[name] ? name : "boxes";
  for (const v of Object.keys(VIEWS)) $("#view-" + v).hidden = v !== view;
  for (const b of document.querySelectorAll(".tab")) {
    const on = b.dataset.view === view;
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;                                // one tab stop; arrow keys move between tabs
  }
  try { history.replaceState(null, "", "#" + VIEWS[view]); } catch { /* file:// or sandbox */ }
  if (view === "objects") loadObjects(true);
  if (view === "auto") refreshAuto();
}
function setupTabs() {
  const tabs = [...document.querySelectorAll(".tab")];
  tabs.forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
  $(".tablist").addEventListener("keydown", (e) => {
    const i = tabs.indexOf(document.activeElement);
    if (i < 0) return;
    const to = { ArrowRight: (i + 1) % tabs.length, ArrowLeft: (i + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    showView(tabs[to].dataset.view);
    tabs[to].focus();
  });
  const fromHash = () => Object.keys(VIEWS).find((k) => "#" + VIEWS[k] === location.hash) || "boxes";
  window.addEventListener("hashchange", () => { if (fromHash() !== view) showView(fromHash()); });   // links and the back button
  showView(fromHash());
}

/* ------------------------------------------------------------------ object browser */
const OBJ_STATUS = { free: "Còn trống", draft: "Trong hộp nháp", approved: "Đã duyệt", pushed: "Đã đẩy lên SM", external: "Đã dùng (bảng tính)", sm: "Trong hộp SM" };
const ov = { q: "", status: "", category: "", sort: "id", page: 1, sel: new Set(), res: null, cov: [], covAll: false, seq: 0 };
const MAX_PICK = 60;

async function loadObjects(withCoverage) {
  const my = ++ov.seq;
  const qs = new URLSearchParams({ q: ov.q, category: ov.category, status: ov.status, sort: ov.sort, page: ov.page, per: 50 });
  try {
    if (withCoverage) ov.cov = await api("/api/objects/coverage");
    const res = await api("/api/objects?" + qs);
    if (my !== ov.seq) return;                           // a newer request is already on its way
    ov.res = res;
    BBThumbs.setServerNow(res.now);
  } catch (e) { return fail(e); }
  if (ov.page > ov.res.pages) { ov.page = ov.res.pages; return loadObjects(false); }
  if (withCoverage) { renderCoverage(); fillCategories(); }
  renderObjects();
}
const loadObjectsSoon = debounce(() => { if (view === "objects") loadObjects(true); }, 400);

function renderCoverage() {
  const root = $("#cov");
  const rows = ov.covAll ? ov.cov : ov.cov.slice(0, 12);
  root.replaceChildren(...rows.map((c) => {
    const pct = c.total ? Math.round((c.free / c.total) * 100) : 0;
    return h("button", { type: "button", class: "cov-row" + (ov.category === c.name ? " on" : ""),
      title: `${c.name}: ${c.free} còn trống, ${c.used} đã dùng, ${c.total} tổng`, onclick: () => { ov.category = ov.category === c.name ? "" : c.name; ov.page = 1; $("#o-cat").value = ov.category; loadObjects(false); } },
      h("span", { class: "cov-name" }, c.name),
      h("span", { class: "cov-bar", "aria-hidden": "true" }, h("i", { style: `width:${pct}%` })),
      h("span", { class: "cov-n" }, `${fmt(c.free)}/${fmt(c.total)}`));
  }));
  const more = $("#cov-more");
  more.hidden = ov.cov.length <= 12;
  more.textContent = ov.covAll ? "Thu gọn" : `Hiện thêm ${ov.cov.length - 12} nhóm`;
}
function fillCategories() {
  const sel = $("#o-cat");
  sel.replaceChildren(h("option", { value: "" }, "Tất cả nhóm"),
    ...ov.cov.map((c) => h("option", { value: c.name }, `${c.name} (${fmt(c.total)})`)));
  sel.value = ov.category;
}

function renderObjects() {
  const r = ov.res;
  if (!r) return;
  const total = Object.values(r.counts).reduce((a, b) => a + b, 0);
  const chips = [["", "Tất cả", total], ...Object.keys(OBJ_STATUS).map((k) => [k, OBJ_STATUS[k], r.counts[k]])];
  $("#o-chips").replaceChildren(...chips.map(([k, label, n]) =>
    h("button", { type: "button", class: "fchip" + (ov.status === k ? " on" : ""), "aria-pressed": String(ov.status === k),
      onclick: () => { ov.status = k; ov.page = 1; loadObjects(false); } }, label, h("small", {}, fmt(n)))));

  BBThumbs.resetExpired();
  $("#o-body").replaceChildren(...(r.items.length ? r.items.map(objRow)
    : [h("tr", {}, h("td", { colspan: 8, class: "none" }, "Không có món nào khớp bộ lọc này."))]));
  const gone = BBThumbs.expiredCount();            // pictures whose SM link ran out (SM lets them be seen for about an hour)
  $("#o-imgtip").hidden = !gone;
  if (gone) {                                         // write the text only when it changed, so a screen reader is not told again on every redraw
    const text = `${fmt(gone)} ảnh ở trang này đã hết hạn, vì SM chỉ cho xem ảnh khoảng một giờ. Mở SM và bấm "Đồng bộ kho ngay" để có ảnh mới.`;
    if ($("#o-imgtip-text").textContent !== text) $("#o-imgtip-text").textContent = text;
  }
  const freeHere = r.items.filter((o) => o.status === "free");
  const all = $("#o-all");
  all.disabled = !freeHere.length;
  all.checked = freeHere.length > 0 && freeHere.every((o) => ov.sel.has(o.id));
  $("#o-page").textContent = `Trang ${r.page}/${r.pages} · ${fmt(r.total)} món`;
  const csvQuery = new URLSearchParams({ q: ov.q, category: ov.category, status: ov.status, sort: ov.sort });
  if (code) csvQuery.set("access_code", code);
  $("#o-csv").href = "/api/objects.csv?" + csvQuery;
  $("#o-prev").disabled = r.page <= 1;
  $("#o-next").disabled = r.page >= r.pages;
  renderPickBar();
}
function objRow(o) {
  const free = o.status === "free";
  return h("tr", { class: ov.sel.has(o.id) ? "sel" : "" },
    h("td", { class: "c-sel" }, free ? h("input", { type: "checkbox", checked: ov.sel.has(o.id), "aria-label": `Chọn ${o.id} ${o.name}`,
      onchange: (e) => togglePick(o.id, e.target.checked, e.target) }) : null),
    h("td", { class: "c-img" }, BBThumbs.thumb(o, { size: "sm" })),
    h("td", { class: "oid" }, o.id), h("td", {}, o.name), h("td", {}, o.vi), h("td", {}, o.category),
    h("td", { class: "num" }, fmt(o.stock)),
    h("td", {}, h("span", { class: "pill st-" + o.status }, OBJ_STATUS[o.status]), o.holder ? h("small", { class: "holder" }, o.holder) : null));
}
function togglePick(id, on, box) {
  if (on && ov.sel.size >= MAX_PICK) { box.checked = false; return toast(`Chỉ chọn được tối đa ${MAX_PICK} món một lần.`, "bad"); }
  on ? ov.sel.add(id) : ov.sel.delete(id);
  box.closest("tr").classList.toggle("sel", on);
  const freeHere = ov.res.items.filter((o) => o.status === "free");
  $("#o-all").checked = freeHere.length > 0 && freeHere.every((o) => ov.sel.has(o.id));
  renderPickBar();
}
function pickTargets() {
  return (data ? data.boxes : []).filter((b) => (b.status === "draft" || b.status === "error") && b.items.length < b.count)
    .sort((a, b) => natural(a.module, b.module) || natural(a.label, b.label));
}
function renderPickBar() {
  const bar = $("#o-bar");
  bar.hidden = ov.sel.size === 0;
  if (bar.hidden) return;
  $("#o-sel").textContent = `Đã chọn ${ov.sel.size} món`;
  const sel = $("#o-target"), keep = sel.value;
  const targets = pickTargets();
  sel.replaceChildren(...(targets.length ? targets.map((b) => h("option", { value: b.id }, `${b.module} ${b.label} (${b.items.length}/${b.count})`))
    : [h("option", { value: "" }, "Chưa có hộp nháp còn chỗ")]));
  if (keep && targets.some((b) => String(b.id) === keep)) sel.value = keep;
  $("#o-add").disabled = !targets.length;
}
function setupObjects() {
  $("#o-q").addEventListener("input", debounce((e) => { ov.q = e.target.value.trim(); ov.page = 1; loadObjects(false); }, 250));
  $("#o-cat").addEventListener("change", (e) => { ov.category = e.target.value; ov.page = 1; renderCoverage(); loadObjects(false); });
  $("#o-sort").addEventListener("change", (e) => { ov.sort = e.target.value; ov.page = 1; loadObjects(false); });
  $("#o-prev").addEventListener("click", () => { ov.page = Math.max(1, ov.page - 1); loadObjects(false); });
  $("#o-next").addEventListener("click", () => { ov.page += 1; loadObjects(false); });
  $("#cov-more").addEventListener("click", () => { ov.covAll = !ov.covAll; renderCoverage(); });
  $("#o-all").addEventListener("change", (e) => {
    for (const o of ov.res.items) if (o.status === "free") {
      if (e.target.checked && ov.sel.size < MAX_PICK) ov.sel.add(o.id); else if (!e.target.checked) ov.sel.delete(o.id);
    }
    renderObjects();
  });
  $("#o-clear").addEventListener("click", () => { ov.sel.clear(); renderObjects(); });
  $("#o-add").addEventListener("click", async () => {
    const target = $("#o-target").value;
    if (!target || !ov.sel.size) return;
    const btn = $("#o-add");
    btn.disabled = true;
    try {
      const r = await api(`/api/boxes/${target}/items/bulk`, { method: "POST", body: { object_ids: [...ov.sel] } });
      for (const a of r.added) ov.sel.delete(a.id);
      for (const f of r.failed) ov.sel.delete(f.id);
      const bits = [];
      if (r.added.length) bits.push(`Đã thêm ${r.added.length} món và khoá lại.`);
      if (r.failed.length) bits.push(`${r.failed.length} món không thêm được: ${r.failed[0].reason}`);
      toast(bits.join(" "), r.added.length ? "" : "bad");
      await poll(true);
      await loadObjects(true);
      $("#o-q").focus();                          // the selection bar is gone now, so keep the keyboard in the list
    } catch (e) { fail(e); } finally { btn.disabled = false; }
  });
}

/* ------------------------------------------------------------------ automations */
let auto = null, queue = null, plan = null, smReport = null;

let lastAnnounced = null;

async function refreshAuto() {
  try {
    const [a, q, r] = await Promise.all([api("/api/automations"), api("/api/queue"), api("/api/sm/boxes/report")]);
    auto = a; queue = q; smReport = r;
  } catch (e) { if (e.status === 401) showGate(); return; }
  const n = auto.alerts.length + auto.todo.length, badge = $("#alert-badge");
  badge.hidden = n === 0;
  badge.textContent = String(n);
  const sig = `${auto.todo.length}/${auto.alerts.length}`;
  if (lastAnnounced !== null && sig !== lastAnnounced) {      // say it once when it changes, not on every refresh
    $("#sr-live").textContent = `Có ${auto.todo.length} việc cần làm và ${auto.alerts.length} cảnh báo.`;
  }
  lastAnnounced = sig;
  if (view === "auto") renderAuto();
}

function renderSyncStatus() {
  const c = data && data.catalog, el = $("#sync-status");
  if (!c || !el) return;
  if (c.source !== "live") { el.textContent = "Chưa đồng bộ lần nào: kho đang dùng file CSV, nên số lượng có thể cũ."; el.className = "hint warnline"; return; }
  const min = Math.max(0, Math.round((Date.now() / 1000 - c.synced_at) / 60));
  el.textContent = `Đã đồng bộ với SM ${min < 1 ? "vừa xong" : min < 60 ? min + " phút trước" : Math.round(min / 60) + " giờ trước"}: ${fmt(c.usable)} món dùng được.`;
  el.className = "hint";
}

function renderSmBoxes() {
  const r = smReport;
  if (!r) return;
  const sum = $("#smb-sum");
  if (!r.synced_at) {
    sum.textContent = "Chưa đọc các hộp đang có trong SM. Khi công cụ đồng bộ trong SM gửi danh sách, món đang nằm trong hộp SM sẽ không bị chọn trùng.";
  } else {
    const min = Math.max(0, Math.round((Date.now() / 1000 - r.synced_at) / 60));
    sum.textContent = `Đã đọc ${min < 1 ? "vừa xong" : min < 60 ? min + " phút trước" : Math.round(min / 60) + " giờ trước"}: ${fmt(r.boxes)} hộp đang dùng trong SM, giữ ${fmt(r.objects_held)} món.`;
  }
  const ru = r.reuse;
  const reuseEl = $("#smb-reuse");
  if (ru && r.synced_at) {
    reuseEl.hidden = false;
    reuseEl.textContent = !ru.on
      ? "Đang tắt việc dùng lại món của hộp cũ, nên mọi món trong hộp SM đều được giữ."
      : `Hộp SM tạo cách đây hơn ${ru.days} ngày không còn giữ món (trong SM chúng vẫn nằm ở hộp cũ): ${fmt(ru.old_boxes)} hộp, nhờ đó ${fmt(ru.freed_objects)} món được tính là trống để làm hộp mới.` +
        (ru.undated_boxes ? ` ${fmt(ru.undated_boxes)} hộp không đọc được ngày tạo nên vẫn giữ món.` : "");
  } else reuseEl.hidden = true;
  const fill = (listId, headId, rows) => {
    $(listId).replaceChildren(...rows);
    $(listId).hidden = $(headId).hidden = rows.length === 0;
  };
  fill("#smb-conf", "#smb-conf-h", r.conflicts.map((c) => h("li", {}, h("b", {}, c.box),
    ": " + c.objects.map((o) => `${o.id}${o.name ? " " + o.name : ""} (đang ở ${o.sm_box})`).join(", "))));
  fill("#smb-dbl", "#smb-dbl-h", r.doubles.slice(0, 50).map((d) => h("li", {}, h("b", {}, d.id), `${d.name ? " " + d.name : ""}: ${d.boxes.join(", ")}`)));
}

function renderAuto() {
  if (!auto) return;
  renderSyncStatus();
  renderSmBoxes();
  $("#a-todo").replaceChildren(...(auto.todo.length
    ? auto.todo.map((t) => h("li", { class: t.level }, h("b", { class: "where" }, t.where), t.msg))
    : [h("li", { class: "ok" }, "Không có bước nào đang chờ bạn.")]));
  $("#a-alerts-panel").hidden = false;
  $("#a-alerts").replaceChildren(...(auto.alerts.length
    ? auto.alerts.map((a) => h("li", { class: a.level }, a.msg))
    : [h("li", { class: "ok" }, "Không có cảnh báo nào.")]));
  if (!$("#a-switches").contains(document.activeElement)) $("#a-switches").replaceChildren(...auto.switches.map(switchRow));

  const batchOn = auto.switches.find((s) => s.key === "ai_batch").enabled;
  $("#b-plan").disabled = !batchOn;
  $("#b-create").disabled = !batchOn;
  $("#b-who").textContent = batchOn ? (auto.planner === "deepseek" ? "Kế hoạch do DeepSeek đề xuất."
    : "Chế độ thử: chưa nối mô hình nên kế hoạch chỉ chia theo từng dòng bạn gõ.") : "Tính năng này đang tắt ở công tắc phía trên.";

  const q = queue;
  const queueEls = [];
  if (!q.enabled) queueEls.push(h("p", { class: "hint" }, "Hàng chờ đang tắt: công cụ đồng bộ trong SM sẽ không thấy hộp nào để đưa lên SM."));
  else {
    queueEls.push(h("p", { class: "hint" }, "Các hộp đã duyệt, chưa có trong SM. Hộp đạt kiểm tra sẽ hiện ở khung Box builder trong SM. Việc tạo TaskBox vẫn do bạn bấm xác nhận ở SM."));
    queueEls.push(h("h3", {}, `Sẵn sàng (${q.ready.length})`));
    queueEls.push(q.ready.length ? h("ul", { class: "qlist" }, q.ready.map((b) => h("li", {}, h("b", {}, b.title), ` · ${b.items} món`)))
      : h("p", { class: "hint" }, "Chưa có hộp nào sẵn sàng."));
    if (q.blocked.length) {
      queueEls.push(h("h3", {}, `Bị chặn (${q.blocked.length})`));
      queueEls.push(h("ul", { class: "qlist bad" }, q.blocked.map((b) => h("li", {}, h("b", {}, b.title || `Hộp ${b.box_id}`),
        h("ul", {}, b.reasons.slice(0, 4).map((r) => h("li", {}, r)), b.reasons.length > 4 ? h("li", {}, `... và ${b.reasons.length - 4} lý do khác`) : null)))));
    }
  }
  $("#a-queue").replaceChildren(...queueEls);

  const sheetOn = auto.switches.find((s) => s.key === "sheet_export").enabled;
  const approved = data ? data.boxes.filter((b) => b.status === "approved").length : 0;
  $("#a-sheet").replaceChildren(
    h("p", {}, sheetOn ? `${approved} hộp đã duyệt đang chờ script của bảng tính lấy về.` : "Công tắc đang tắt: script của bảng tính nhận danh sách rỗng."),
    h("p", { class: "hint" }, "Script nằm ở integrations/google_sheets_apps_script.gs. Nó chỉ ghi vào ô còn trống và luôn hỏi xem trước khi ghi (trừ khi bạn bật chạy tự động 30 phút)."));

  $("#a-log").replaceChildren(...(auto.log.length ? auto.log.map((e) =>
    h("li", {}, h("time", {}, new Date(e.at * 1000).toLocaleString("vi-VN")), e.msg)) : [h("li", { class: "none" }, "Chưa có hoạt động nào.")]));
}

const CFG_LABEL = { stale_hours: "Cảnh báo khi kho cũ hơn (giờ)", low_stock: "Cảnh báo khi một nhóm còn dưới (món)", days: "Dùng lại món của hộp tạo cách đây hơn (ngày)" };

function switchRow(sw) {
  const id = "sw-" + sw.key;
  const state = h("span", { class: "sw-state", "aria-hidden": "true" }, sw.enabled ? "Bật" : "Tắt");
  const toggle = h("input", { type: "checkbox", id, role: "switch", class: "sw-input", checked: sw.enabled,
    onchange: async (e) => {
      const want = e.target.checked;
      if (e.target.dataset.busy) { e.target.checked = !want; return; }      // a save is already running
      e.target.dataset.busy = "1";
      e.target.setAttribute("aria-busy", "true");
      try {
        await api("/api/automations/" + sw.key, { method: "PUT", body: { enabled: want } });
        toast(`${sw.title}: ${want ? "đã bật" : "đã tắt"}.`);
        e.target.closest(".sw-row").classList.toggle("on", want);          // update this row where it stands: no rebuild, so
        state.textContent = want ? "Bật" : "Tắt";                          // keyboard focus never moves
      } catch (err) { e.target.checked = !want; fail(err); }
      finally { delete e.target.dataset.busy; e.target.removeAttribute("aria-busy"); }
      await refreshAuto();
    } });
  const cfgs = Object.entries(sw.config).map(([k, v]) => {
    const [lo, hi] = sw.limits[k] || [1, 9999];
    return h("label", { class: "cfg" }, CFG_LABEL[k] || k,
      h("input", { type: "number", min: lo, max: hi, value: v, onchange: async (e) => {
        try { await api("/api/automations/" + sw.key, { method: "PUT", body: { config: { [k]: parseInt(e.target.value, 10) } } }); toast("Đã lưu."); }
        catch (err) { e.target.value = v; fail(err); }                       // refused: show the saved number again
        await refreshAuto();
      } }));
  });
  return h("div", { class: "sw-row" + (sw.enabled ? " on" : "") },
    h("div", { class: "sw-text" }, h("label", { for: id, class: "sw-title" }, sw.title), h("p", { class: "hint" }, sw.desc), cfgs.length ? h("div", { class: "cfgs" }, cfgs) : null),
    h("label", { class: "sw", for: id }, toggle, h("span", { class: "sw-track", "aria-hidden": "true" }, h("i")), state));
}

function renderPlan() {
  $("#b-result").hidden = !plan;
  if (!plan) return;
  const rows = plan.boxes.map((b, i) => h("tr", {},
    h("td", {}, h("input", { value: b.label, maxlength: 40, "aria-label": `Tên hộp ${i + 1}`, oninput: (e) => { b.label = e.target.value; } })),
    h("td", {}, h("input", { value: b.theme, maxlength: 300, "aria-label": `Bối cảnh hộp ${i + 1}`, oninput: (e) => { b.theme = e.target.value; } })),
    h("td", { class: "num" }, h("input", { type: "number", min: 1, max: 60, value: b.count, class: "n", "aria-label": `Số món hộp ${i + 1}`,
      oninput: (e) => { b.count = parseInt(e.target.value, 10) || cfg.default_count; } })),
    h("td", {}, h("button", { type: "button", class: "tool", title: "Bỏ hộp này khỏi kế hoạch", "aria-label": `Bỏ hộp ${b.label}`,
      onclick: () => { plan.boxes.splice(i, 1); plan.boxes.length ? renderPlan() : (plan = null, renderPlan()); } }, icon("x")))));
  $("#b-body").replaceChildren(...rows);
  const cats = plan.boxes[0].categories || [];
  const note = $("#b-cats-note");
  note.hidden = !cats.length;
  note.textContent = cats.length ? `Mọi hộp chỉ chọn đồ trong ${cats.length} nhóm: ${cats.slice(0, 4).join(", ")}${cats.length > 4 ? "..." : ""}.` : "";
  $("#b-create").textContent = `Tạo ${plan.boxes.length} hộp này`;
}

function setupBatch() {
  $("#a-batch").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#b-plan");
    btn.disabled = true;
    btn.textContent = "Đang lên kế hoạch…";
    try {
      plan = await api("/api/batches/plan", { method: "POST", body: {
        brief: $("#b-brief").value.trim(), module: $("#b-module").value.trim(), start_label: $("#b-start").value.trim() || "Box A1",
        boxes: parseInt($("#b-n").value, 10) || 4, count: cfg.default_count,
        labels: $("#b-labels").value.split(",").map((x) => x.trim()).filter(Boolean), code: $("#b-code").value.trim(),
        categories: [...batchCats] } });
      store.set("bb_module", $("#b-module").value.trim());
      renderPlan();
      $("#b-result").focus();                     // screen readers read the plan, keyboard users start at it
    } catch (err) { fail(err); } finally { btn.disabled = false; btn.textContent = "AI lên kế hoạch"; }
  });
  $("#b-section").addEventListener("click", () => { $("#b-labels").value = "Box B2, Box B3, Box A1"; $("#b-code").focus(); });
  $("#b-cancel").addEventListener("click", () => { plan = null; renderPlan(); });
  $("#b-create").addEventListener("click", async () => {
    if (!plan || !plan.boxes.length) return;
    const btn = $("#b-create");
    btn.disabled = true;
    try {
      const r = await api("/api/batches", { method: "POST", body: { boxes: plan.boxes, owner: $("#who").value.trim(), approve: $("#b-approve").checked } });
      toast(`Đang chọn đồ cho ${r.ids.length} hộp, lần lượt từng hộp để không trùng món.`);
      plan = null; renderPlan();
      showView("boxes");
      $("#view-boxes").focus();
      await poll(true);
    } catch (err) { fail(err); } finally { btn.disabled = false; }
  });
  $("#b-module").value = store.get("bb_module");
}

/* ------------------------------------------------------------------ the push, tried on a fake SM (only when the server runs in that mode) */
async function loadFakeSm() {
  if (cfg.sm_push_mode !== "mock") return;
  try {
    const r = await api("/api/sm/mock");
    $("#smp-list").replaceChildren(...(r.boxes.length ? r.boxes.map((b) => h("li", {},
      h("label", { class: "pick" }, h("input", { type: "checkbox", value: b.box_id, "aria-label": `Chọn ${b.title}` }), h("b", {}, b.title)),
      ` · ${b.status_label} · ${b.objects} món${b.assignee ? " · người nhận " + b.assignee : ""}${b.holder ? " · đang giữ " + b.holder : ""}`))
      : [h("li", {}, "Chưa có hộp nào.")]));
  } catch (e) { fail(e); }
}

function showFakeResults(r, okText) {
  const rows = r.results.length ? r.results : [];
  $("#smp-body").replaceChildren(...rows.map((x) => h("tr", {}, h("td", {}, x.title || x.box_id),
    h("td", {}, h("span", { class: "pill " + (x.ok ? "st-free" : "st-external") }, x.ok ? okText : "Không làm được")),
    h("td", {}, x.ok ? (x.sm_box_id ? `Mã trong SM giả: ${x.sm_box_id}` : `Trạng thái: ${x.status_label}`) : x.error))));
  $("#smp-wrap").hidden = rows.length === 0;
}

function setupImgTip() {
  $("#o-imgtip-go").addEventListener("click", () => {
    showView("auto");
    const head = $("#sync-h");                         // the button hides with its view: put focus and the screen where the steps are
    head.tabIndex = -1;
    head.focus();
    head.scrollIntoView({ block: "start" });
  });
}

function setupSyncInstall() {
  const box = $("#sync-code"), msg = $("#sync-copy-msg"), details = $("#sync-code-box");
  if (!box) return;
  const loadCode = async () => {                       // the same script the install link serves, as plain text
    if (box.value) return box.value;
    const r = await fetch("/box_builder_sm.txt", { cache: "no-store" });
    if (!r.ok) throw new Error("Không tải được mã công cụ. Thử lại sau ít giây.");
    box.value = await r.text();
    return box.value;
  };
  details.addEventListener("toggle", () => { if (details.open) loadCode().catch(fail); });
  box.addEventListener("focus", () => box.select());
  $("#sync-copy").addEventListener("click", async () => {
    try {
      const code = await loadCode();
      let ok = false;
      try { await navigator.clipboard.writeText(code); ok = true; } catch (e) { /* blocked, or the address is plain http: use the fallback */ }
      if (!ok) {
        details.open = true;                          // show the code and select it, so Ctrl+C is all that is left
        box.focus(); box.select();
        try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      }
      msg.textContent = ok ? "Đã sao chép. Giờ dán vào Tampermonkey (Create a new script)." : "Chưa sao chép được tự động. Mã đã được chọn sẵn trong khung bên dưới: nhấn Ctrl+C.";
      msg.className = ok ? "hint" : "hint warnline";
    } catch (e) { fail(e); }
  });
}

function setupSmPush() {
  const panel = $("#a-smpush");
  panel.hidden = cfg.sm_push_mode !== "mock";
  if (panel.hidden) return;
  const sum = $("#smp-sum");
  const say = (t) => { sum.hidden = !t; sum.textContent = t || ""; };
  const refresh = async () => { await poll(true); await refreshAuto(); await loadFakeSm(); };
  $("#smp-go").addEventListener("click", async () => {
    const btn = $("#smp-go");
    btn.disabled = true;
    try {
      const r = await api("/api/sm/push", { method: "POST", body: { assignee: $("#smp-who").value.trim(), borrow_now: $("#smp-borrow").checked } });
      say(r.results.length ? `Tạo được ${r.created} hộp trong SM giả, ${r.failed} hộp không tạo được.` : "Không có hộp nào sẵn sàng để đẩy.");
      showFakeResults(r, "Đã tạo");
      await refresh();
    } catch (e) { fail(e); } finally { btn.disabled = false; }
  });
  for (const b of document.querySelectorAll("#smp-moves [data-move]")) {
    b.addEventListener("click", async () => {
      const ids = [...document.querySelectorAll("#smp-list input:checked")].map((i) => i.value);
      if (!ids.length) return toast("Hãy chọn ít nhất một hộp trong danh sách.", "bad");
      b.disabled = true;
      try {
        const r = await api("/api/sm/moves", { method: "POST", body: { action: b.dataset.move, box_ids: ids, person: $("#smp-who").value.trim() } });
        const done = r.results.filter((x) => x.ok).length;
        say(`${done} hộp xong, ${r.results.length - done} hộp không làm được.`);
        showFakeResults(r, "Xong");
        await loadFakeSm();
      } catch (e) { fail(e); } finally { b.disabled = false; }
    });
  }
  const days = () => Math.max(0, parseInt($("#smp-days").value, 10) || 0);
  $("#smp-old-preview").addEventListener("click", async () => {
    try {
      const r = await api("/api/sm/moves", { method: "POST", body: { action: "return_old", days: days(), person: $("#smp-who").value.trim() } });
      say(r.boxes.length ? `Có ${r.boxes.length} hộp ở sàn đã tạo từ ${days()} ngày trở lên. Bấm Xác nhận trả để trả.` : "Không có hộp nào ở sàn đủ lâu để trả.");
      $("#smp-body").replaceChildren(...r.boxes.map((b) => h("tr", {}, h("td", {}, b.title), h("td", {}, h("span", { class: "pill st-draft" }, "Sẽ trả")), h("td", {}, `Tạo cách đây ${b.days_old} ngày`))));
      $("#smp-wrap").hidden = r.boxes.length === 0;
      $("#smp-old-go").disabled = r.boxes.length === 0;
    } catch (e) { fail(e); }
  });
  $("#smp-old-go").addEventListener("click", async () => {
    const btn = $("#smp-old-go");
    btn.disabled = true;
    try {
      const r = await api("/api/sm/moves", { method: "POST", body: { action: "return_old", days: days(), person: $("#smp-who").value.trim(), confirm: true } });
      const done = r.results.filter((x) => x.ok).length;
      say(`Đã trả ${done} hộp, ${r.results.length - done} hộp không trả được.`);
      showFakeResults(r, "Đã trả");
      await loadFakeSm();
    } catch (e) { fail(e); }
  });
  loadFakeSm();
}

/* ------------------------------------------------------------------ backup and restore */
function setupBackup() {
  const file = $("#bk-file"), prev = $("#bk-preview"), rest = $("#bk-restore"), sum = $("#bk-summary");
  let parsed = null;
  $("#bk-download").href = "/api/backup.json" + (code ? "?access_code=" + encodeURIComponent(code) : "");
  const clear = () => { $("#bk-table-wrap").hidden = true; sum.hidden = true; rest.disabled = true; };
  file.addEventListener("change", async () => {
    clear(); parsed = null; prev.disabled = true;
    const f = file.files && file.files[0];
    if (!f) return;
    if (f.size > 10 * 1024 * 1024) return toast("Tệp lớn hơn 10 MB nên không dùng được.", "bad");
    try { parsed = JSON.parse((await f.text()).replace(/^\uFEFF/, "")); }
    catch { return toast("Tệp này không phải JSON hợp lệ. Hãy chọn tệp đã tải từ nút Tải bản sao lưu.", "bad"); }
    prev.disabled = false;
  });
  const show = (r) => {
    const t = r.totals;
    sum.hidden = false;
    sum.textContent = `${r.dry_run ? "Sẽ thêm" : "Đã thêm"} ${t.boxes_added} hộp (${t.items_added} món), bỏ qua ${t.boxes_skipped} hộp và ${t.items_skipped} món. ` +
      `Món đã dùng trong bảng tính: thêm ${r.used.added}. Thiết lập áp dụng: ${r.automations.applied.length}.`;
    $("#bk-body").replaceChildren(...r.boxes.map((b) => {
      const skipped = b.items_skipped.slice(0, 3).map((i) => `${i.object_id} (${i.reason})`).join("; ") + (b.items_skipped.length > 3 ? `; và ${b.items_skipped.length - 3} món khác` : "");
      const note = [b.reason, skipped].filter(Boolean).join(". ");
      return h("tr", {}, h("td", {}, `${b.module} ${b.label}`), h("td", {}, h("span", { class: "pill " + (b.result === "added" ? "st-free" : "st-external") }, b.result === "added" ? "Thêm" : "Bỏ qua")),
        h("td", { class: "num" }, `${b.items_added}/${b.items_total}`), h("td", {}, note));
    }));
    $("#bk-table-wrap").hidden = !r.boxes.length;
  };
  prev.addEventListener("click", async () => {
    if (!parsed) return;
    prev.disabled = true;
    try {
      const r = await api("/api/backup/import?dry_run=true", { method: "POST", body: parsed });
      show(r);
      rest.disabled = !(r.totals.boxes_added || r.used.added || r.automations.applied.length);
    } catch (e) { fail(e); } finally { prev.disabled = false; }
  });
  rest.addEventListener("click", async () => {
    if (!parsed) return;
    rest.disabled = prev.disabled = true;
    try {
      const r = await api("/api/backup/import?dry_run=false", { method: "POST", body: parsed });
      show(r);
      toast(`Đã khôi phục ${r.totals.boxes_added} hộp.`);
      await poll(true);
      await refreshAuto();
    } catch (e) { fail(e); rest.disabled = false; } finally { prev.disabled = false; }
  });
}

function setupCsvImport() {
  const file = $("#csv-file"), prev = $("#csv-preview"), imp = $("#csv-import"), sum = $("#csv-summary");
  let gen = 0;                                          // bumped when another file is chosen, so a late answer for the old file is ignored
  const say = (text, bad) => {
    sum.hidden = !text;
    sum.textContent = text || "";
    sum.className = bad ? "hint warnline" : "hint";
    if (text) $("#sr-live").textContent = text;
  };
  const clear = () => { $("#csv-table-wrap").hidden = true; $("#csv-bad").hidden = true; say(""); imp.disabled = true; };
  const bytes = async () => {                           // read when the button is pressed, so a file fixed and saved again is read again
    const f = file.files && file.files[0];
    if (!f) throw Object.assign(new Error("Chưa chọn tệp. Hãy chọn tệp CSV ở trên."), { status: 0 });
    try { return await f.arrayBuffer(); }
    catch { throw Object.assign(new Error("Không đọc được tệp. Hãy chọn lại tệp CSV."), { status: 0 }); }
  };
  file.addEventListener("change", () => {
    gen += 1;
    clear();
    prev.disabled = !(file.files && file.files[0]);
    const f = file.files && file.files[0];
    if (f && f.size > 5 * 1024 * 1024) { prev.disabled = true; say("Tệp lớn hơn 5 MB nên không dùng được. Hãy tách thành các tệp nhỏ hơn.", true); }
  });
  const show = (r) => {
    const t = r.totals;
    say(`${r.dry_run ? "Sẽ thêm" : "Đã thêm"} ${t.boxes_added} hộp nháp (${t.items_added} món), bỏ qua ${t.boxes_skipped} hộp và ${t.items_skipped} món. ` +
      `Đọc ${r.csv.rows} dòng, ${r.csv.bad_total} dòng không đọc được.`);
    $("#csv-body").replaceChildren(...r.boxes.map((b) => {
      const skipped = b.items_skipped.slice(0, 3).map((i) => `${i.object_id} (${i.reason})`).join("; ") + (b.items_skipped.length > 3 ? `; và ${b.items_skipped.length - 3} món khác` : "");
      const note = [b.reason, skipped].filter(Boolean).join(". ");
      return h("tr", {}, h("td", {}, `${b.module} ${b.label}`), h("td", {}, h("span", { class: "pill " + (b.result === "added" ? "st-free" : "st-external") }, b.result === "added" ? "Thêm" : "Bỏ qua")),
        h("td", { class: "num" }, `${b.items_added}/${b.items_total}`), h("td", {}, note));
    }));
    $("#csv-table-wrap").hidden = !r.boxes.length;
    const bad = r.csv.bad_rows.slice(0, 10).map((x) => h("li", { class: "warn" }, `Dòng ${x.line}${x.object_id ? " (" + x.object_id + ")" : ""}: ${x.reason}`));
    if (r.csv.bad_total > 10) bad.push(h("li", { class: "warn" }, `Và ${r.csv.bad_total - 10} dòng khác không đọc được.`));
    $("#csv-bad").replaceChildren(...bad);
    $("#csv-bad").hidden = !bad.length;
  };
  const send = async (dry) => {
    const mine = gen;
    prev.disabled = imp.disabled = true;
    try {
      const r = await api(`/api/import/boxes.csv?dry_run=${dry}`, { method: "POST", raw: await bytes() });
      if (mine !== gen) return null;                    // another file was chosen meanwhile: this answer belongs to the old one
      show(r);
      return r;
    } catch (e) {
      if (mine === gen) { clear(); say(e.message, true); }
      fail(e);
      return null;
    } finally {
      if (mine === gen) prev.disabled = !(file.files && file.files[0]);
    }
  };
  prev.addEventListener("click", async () => {
    const r = await send(true);
    imp.disabled = !(r && r.totals.boxes_added);
  });
  imp.addEventListener("click", async () => {
    const r = await send(false);
    if (!r) return;
    imp.disabled = true;                                // the same file would add nothing now
    toast(r.totals.boxes_added ? `Đã nhập ${r.totals.boxes_added} hộp nháp. Mở tab Hộp để xem lại và duyệt.` : "Không có hộp nào được thêm. Xem lý do trong bảng bên dưới.");
    await poll(true);
    await refreshAuto();
  });
}

/* ------------------------------------------------------------------ help for people who are new to the tool */
const normModule = (v) => { const t = v.trim(); return /^m\d+$/i.test(t) ? t.toUpperCase() : t; };   // m8 -> M8, other tab names as typed

function renderNextStep(d) {
  const boxes = d.boxes, n = (st) => boxes.filter((b) => b.status === st).length;
  let text;
  if (!boxes.length) text = "Tạo hộp đầu tiên ở khung bên trái: gõ Module và tên hộp, rồi bấm Tạo hộp.";
  else if (n("generating")) text = `Đang chọn đồ cho ${n("generating")} hộp. Chờ khoảng 15 giây.`;
  else if (n("error")) text = `${n("error")} hộp bị lỗi. Mở hộp đó và bấm Chọn lại.`;
  else if (n("draft")) text = `Có ${n("draft")} hộp nháp. Xem từng hộp, sửa nếu cần, rồi bấm Duyệt.`;
  else {
    const waiting = boxes.filter((b) => b.status === "approved" && !b.pushed).length;
    text = waiting
      ? `${waiting} hộp đã duyệt. Điền vào bảng tính (menu Box builder) hoặc đưa lên SM. Tab Tự động hoá chỉ từng bước.`
      : "Tất cả hộp đã lên SM. Tạo hộp mới nếu cần.";
  }
  $("#nextstep span").textContent = text;
}

function setupGuide() {
  const guide = $("#guide");
  guide.hidden = store.get("bb_guide") === "hidden";
  $("#guide-close").addEventListener("click", () => { guide.hidden = true; store.set("bb_guide", "hidden"); $("#guide-open").focus(); });
  $("#guide-open").addEventListener("click", () => {
    showView("boxes");
    guide.hidden = false;
    store.set("bb_guide", "shown");
    guide.scrollIntoView({ block: "start" });
  });
}

const ZOOMS = [100, 115, 130];
function setupTextSize() {
  const apply = (i) => {
    document.body.style.zoom = String(ZOOMS[i] / 100);
    $("#fs-down").disabled = i === 0;
    $("#fs-up").disabled = i === ZOOMS.length - 1;
    store.set("bb_zoom", String(i));
  };
  let i = Math.min(ZOOMS.length - 1, Math.max(0, parseInt(store.get("bb_zoom"), 10) || 0));
  apply(i);
  $("#fs-down").addEventListener("click", () => apply((i = Math.max(0, i - 1))));
  $("#fs-up").addEventListener("click", () => apply((i = Math.min(ZOOMS.length - 1, i + 1))));
}

/* A box name can be used once per module. Say so before the person presses the button, not after. */
function checkDup() {
  const warn = $("#f-warn"), go = $("#f-go");
  const m = normModule($("#f-module").value), l = $("#f-label").value.trim().toLowerCase();
  const dup = !!(data && m && l && data.boxes.some((b) => b.module === m && b.label.toLowerCase() === l));
  warn.hidden = !dup;
  warn.textContent = dup ? `Hộp ${m} ${$("#f-label").value.trim()} đã có. Đặt tên khác, hoặc mở hộp đó ở bên phải.` : "";
  go.disabled = dup;
}

/* The number of objects follows the box name (A1 12, B2 and B3 21) until the person types their own number. */
function followStageSize() {
  const count = $("#f-count");
  if (count.dataset.touched === "1") return;
  const stage = ($("#f-label").value.match(/(?:^|[\s_-])([A-Za-z]\d+)\s*$/) || [])[1];
  count.value = (cfg.stage_counts || {})[(stage || "").toUpperCase()] || cfg.default_count;
}

function setupFormHelp() {
  $("#f-count").addEventListener("input", () => { $("#f-count").dataset.touched = "1"; });
  $("#f-label").addEventListener("input", followStageSize);
  followStageSize();
  for (const [sel, msg] of [["#f-module", "Hãy nhập Module, ví dụ M8."], ["#f-label", "Hãy nhập tên hộp, ví dụ Box A1."]]) {
    const el = $(sel);
    el.addEventListener("invalid", () => el.setCustomValidity(msg));     // the browser's own text may not be Vietnamese
    el.addEventListener("input", () => el.setCustomValidity(""));
    el.addEventListener("input", checkDup);
  }
  $("#f-module").addEventListener("blur", (e) => { e.target.value = normModule(e.target.value); checkDup(); });
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
  setupGate(); setupNewBox(); setupUsed(); setupObjects(); setupBatch(); setupGuide(); setupTextSize(); setupFormHelp(); setupBackup(); setupSmPush(); setupSyncInstall(); setupCsvImport(); setupImgTip();
  updateExport();
  await poll(true);
  if (!$("#gate").hidden) await new Promise((r) => { const t = setInterval(() => { if ($("#gate").hidden) { clearInterval(t); r(); } }, 300); });
  setupCategories();
  setupTabs();
  refreshAuto();
  schedule();
  setInterval(renderKho, 30000);
  setInterval(refreshAuto, 60000);
  setInterval(() => { if (!document.hidden) poll(true); }, 5 * 60 * 1000);       // numbers that depend on the clock (days since a box, picture links) catch up within minutes
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(true); });
}
init();

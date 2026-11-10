"use strict";
/* The page for people who make boxes (/dung). No framework, no build step. All text goes through textContent, never innerHTML.
   It calls only /api/my/*, /api/auth/logout and /my/print (opened in a new tab). People approve their own boxes here (and take the approval back
   while a box is not in SM yet). The admin page only watches the process and can step in.
   The panel "Hôm nay" (the clock of the shift in Vietnam time, the boxes to hand back, the plan of the units, the end-of-day report) reads and writes
   /api/my/plan; the block "Gần đây" in the form reads /api/my/recent. Neither blocks making a box. */
(() => {
  const $ = (s, el = document) => el.querySelector(s);

  /* ---------------------------------------------------------------- the words on the page */
  const STATUS = { generating: "Đang chọn đồ", draft: "Chờ duyệt", approved: "Đã duyệt", error: "Chưa chọn được đồ" };
  const NEED_STATE = { none: "Chưa tạo", ...STATUS };
  const T = {
    fail: "Chưa làm được. Thử lại.",                        // every problem the person cannot fix: no code, no word of the machine room
    offline: "Không kết nối được. Kiểm tra mạng.",
    badInput: "Thông tin chưa đúng. Kiểm tra lại các ô đã điền.",
    loadFail: "Chưa tải được danh sách hộp.",
    back: "Đã kết nối lại.",
    needModule: "Nhập Module.",
    needKind: "Chọn loại hộp.",
    created: (name) => `Đã tạo hộp ${name}. Đang chọn đồ.`,
    ready: (name, n) => `Hộp ${name} đã chọn xong ${n} món. Chờ duyệt.`,
    approved: (name, tail) => `Hộp ${name} đã được duyệt.${tail}`,                    // seen by the page: approved somewhere else (the team's switch, an admin)
    approvedNow: (name, tail) => `Đã duyệt hộp ${name}.${tail}`,                      // the person pressed Có, duyệt hộp
    toStep3: " Bạn lấy đồ ở bước 3.",                                                // only for a box of today: step 3 lists no other
    reopened: (name) => `Đã bỏ duyệt hộp ${name}.`,
    failed: (name) => `Hộp ${name} chưa chọn được đồ.`,
    gone: (name) => `Hộp ${name} không còn trong danh sách. Có thể nó đã tự mất vì quá lâu chưa được duyệt.`,
    swapping: (what) => `Đang đổi món ${what}.`,
    regen: (name) => `Đang chọn lại cả hộp ${name}.`,
    filling: (name) => `Đang điền các ô trống của hộp ${name}.`,
    deleted: (name) => `Đã xoá hộp ${name}. Các món đã trả về kho.`,
    copied: (n, name) => `Đã chép ${n} món của hộp ${name}. Dán vào bảng tính.`,
    copyBlocked: "Không chép được. Bấm In phiếu lấy đồ.",
    logoutFail: "Chưa thoát được. Thử lại.",
    whyApproved: "Hộp đã duyệt. Bấm Bỏ duyệt nếu cần sửa.",
    whyPushed: "Hộp đã lên SM nên không sửa được ở đây. Báo admin nếu cần đổi.",
    whyNotDone: "Chưa chọn xong đồ nên chưa duyệt được.",
    whyShort: (n, count) => `Hộp mới có ${n}/${count} món. Điền đủ món trước khi duyệt.`,
    draftCanChange: "Danh sách còn đổi được đến khi bạn duyệt hộp.",
    loading: "Đang tải.",
    noBox: "Chưa có hộp nào.",
    noReady: "Chưa có hộp nào chọn xong.",
    errorNext: "Bấm Chọn lại cả hộp.",
    errorFill: "Bấm Điền ô trống hoặc Chọn lại cả hộp.",
    confirmDelete: (name) => `Xoá hộp ${name}? Các món trong hộp sẽ trả về kho.`,
    confirmApprove: (name) => `Duyệt hộp ${name}?`,
    confirmRegen: (name) => `Chọn lại cả hộp ${name}? Các món hiện có sẽ bị thay.`,
    byApp: "app điền",                                       // next to a món the app put in because the AI found no valid one
    checkByApp: "Xem kỹ các món app điền.",
    stepReady: "Có hộp để lấy",
    // the panel "Hôm nay"
    clock: (now, tail) => `Bây giờ ${now}. ${tail}`,
    clockNext: (what, at) => `Tiếp theo: ${what} lúc ${at}.`,
    clockEnd: (at) => `Ca làm hết lúc ${at}.`,
    clockOff: "Ngoài giờ làm.",
    markDone: "Xong", markNow: "Bây giờ", markNext: "Tiếp theo",
    loadingPlan: "Đang tải.",
    planFail: "Chưa tải được kế hoạch.",
    noReturn: "Không có hộp nào cần trả.",
    noUnits: "Chưa có unit nào.",
    reportNotSaved: "Chưa lưu kế hoạch hôm nay. Lưu kế hoạch rồi báo cáo hiện ở đây.",
    needHead: "Cần làm:",
    needOptional: "bỏ được",
    needNone: "Không cần làm hộp.",
    planSaved: "Đã lưu kế hoạch.",
    unitRemoved: (code) => `Đã xoá unit ${code || "trống"}.`,
    filled: (code, kind) => `Đã điền ${code} Box ${kind} ở bước 1.`,
    copiedReport: "Đã chép báo cáo.",
    copyBlockedReport: "Không chép được. Chọn chữ trong khung rồi chép tay.",
    // the block "Gần đây" in the form
    recentIn: (module, code) => `Gần đây ở ${module}${code ? " " + code : ""}`,
    today: "Hôm nay", yesterday: "Hôm qua", dayBefore: "Hôm kia",
    daysAgo: (n) => `${n} ngày trước`,
  };
  const hoursText = (left) => {                          // under 2 hours the number is rounded down: never promise more time than there is
    if (left < 3600) return "chưa đầy 1 giờ";
    return `khoảng ${left < 7200 ? 1 : Math.round(left / 3600)} giờ`;
  };

  /* ---------------------------------------------------------------- small helpers */
  function h(tag, attrs, ...kids) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (v === true) el.setAttribute(k, "");
      else el.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return el;
  }
  const btn = (label, onclick, o = {}) => h("button", {
    type: "button", class: "d-btn" + (o.cls ? " " + o.cls : ""), "data-key": o.key, disabled: o.disabled, "aria-describedby": o.desc, "aria-label": o.aria, onclick,
  }, label);
  const dayWord = (n) => (n <= 0 ? T.today : n === 1 ? T.yesterday : n === 2 ? T.dayBefore : T.daysAgo(n));
  /* A box is finished 1 day after the day it was made, and a unit makes a new one every day: yesterday's box and today's can look the same.
     The page says which day a box is from. The day is worked out with the clock of the server, so a page left open over midnight is right too. */
  const serverNow = () => Date.now() / 1000 + S.skew;
  const vnDay = (sec) => new Date((sec + 7 * 3600) * 1000).toISOString().slice(0, 10);               // the Vietnam day of a moment: UTC+7 all year
  const dayGap = (later, earlier) => Math.round((Date.parse(later) - Date.parse(earlier)) / 86400000);
  const ageOf = (b) => { const n = b.day ? dayGap(vnDay(serverNow()), b.day) : 0; return n > 0 ? n : 0; };     // days since the box was made; no day (a very old box) counts as today
  const isToday = (b) => ageOf(b) === 0;
  const dayTag = (b) => (isToday(b) ? "" : dayWord(ageOf(b)));                                       // "Hôm qua", "Hôm kia", "3 ngày trước"
  const boxName = (b) => `${b.module} ${b.label}` + (isToday(b) ? "" : `, ${dayTag(b).toLowerCase()}`);   // also what is said aloud: "M8 Box B2, hôm qua"
  const newestFirst = (a, b) => ageOf(a) - ageOf(b) || b.id - a.id;                                  // today's boxes first, then the older days; the newest first inside each group
  const toStep3 = (b) => (isToday(b) ? T.toStep3 : "");
  const itemName = (it) => it.vi || it.name || it.id;
  const headingOf = (b, id) => {
    const tag = dayTag(b);
    return h("h3", { id, tabindex: "-1", "data-key": "head", "aria-label": tag ? boxName(b) : null },     // a screen reader hears "M8 Box B2, hôm qua"
      h("span", { class: "d-mod" }, b.module), " ", b.label, tag ? [" ", h("span", { class: "d-day" }, tag)] : null);
  };

  /* ---------------------------------------------------------------- state */
  const S = {
    person: null, open: false, stages: [], boxes: [], rev: -1, ttl: 0, skew: 0, day: "",
    loaded: false, failed: false, focusNew: null,
  };
  const P = {                                             // the plan: server = last answer, rows = what the person edits, base = the answer the rows were last synced with
    server: null, rows: [], base: null, failed: false, sig: null,
  };
  const ui = { ask: new Map(), msg: new Map() };         // per box: the question that is open ("del", "approve" or "regen"), last problem
  const lists = { cards: new Map(), takes: new Map() };   // box id -> {el, sig}
  const lastStatus = new Map();                           // box id -> status seen last time, to announce changes
  const lastCount = new Map();                            // box id -> number of objects seen last time
  const deletedHere = new Set();
  const approvedHere = new Set();                         // boxes this person just approved or took back: their own message is said once,
  const reopenedHere = new Set();                         // so the same change seen by the next poll must stay silent
  let lost = null;                                        // where keyboard focus was before a re-render made it vanish
  let leaving = false;

  /* ---------------------------------------------------------------- talking to the server */
  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }
  function goLogin() { if (!leaving) { leaving = true; location.replace("/login?next=/dung"); } }

  async function api(path, opts = {}) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), 20000);
    let res;
    try {
      res = await fetch(path, {
        method: opts.method || "GET", credentials: "same-origin", signal: ctl.signal,
        headers: { "Content-Type": "application/json" }, body: opts.body ? JSON.stringify(opts.body) : undefined,
      });
    } catch { throw new ApiError(T.offline, 0); } finally { clearTimeout(timer); }
    if (res.status === 401) { goLogin(); throw new ApiError("", 401); }
    let json = null;
    try { json = await res.json(); } catch { /* empty body */ }
    if (!res.ok) {
      const d = json && json.detail;
      const plain = res.status < 500 && res.status !== 403 && typeof d === "string";       // a sentence the server wrote for a person; anything else is only "not done"
      throw new ApiError(plain ? d : Array.isArray(d) && res.status < 500 ? T.badInput : T.fail, res.status);
    }
    if (json === null) throw new ApiError(T.fail, res.status);     // 200 with no JSON: a page of something else, not ours
    return json;
  }

  /* ---------------------------------------------------------------- the one polite message (also the live region) */
  let sayTimer = 0, sayShow = 0;
  function say(msg, bad) {
    const el = $("#live");
    clearTimeout(sayTimer); clearTimeout(sayShow);
    el.textContent = "";                                 // a cleared region announces a repeated sentence again
    el.classList.toggle("is-bad", !!bad);
    sayShow = setTimeout(() => { el.textContent = msg; }, 60);
    sayTimer = setTimeout(() => { el.textContent = ""; }, 9000);
  }

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
    } catch { /* fall through to the old way */ }
    const prev = document.activeElement;
    const ta = h("textarea", { style: "position:fixed;top:0;left:0;opacity:0", "aria-hidden": "true", tabindex: "-1" });
    ta.value = text;
    document.body.append(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch { /* ignore */ }
    ta.remove();
    if (prev && prev.focus) prev.focus();
    return ok;
  }

  /* ---------------------------------------------------------------- loading the list */
  let seq = 0, timer = 0;
  async function refresh(force) {
    const mine = ++seq;
    try {
      const d = await api(`/api/my/summary?rev=${force || !S.loaded ? -1 : S.rev}`);
      if (mine !== seq || leaving) return;
      const wasFailed = S.failed;
      S.failed = false;
      if (d.unchanged) {                                   // nothing new: keep the last full answer, only the clock moved
        renderBanner();
        if (wasFailed) say(T.back);
        renderTimed();
        return;
      }
      S.rev = d.rev; S.person = d.person; S.open = !!d.open_mode; S.stages = d.stages || [];
      S.ttl = Number(d.ttl_hours) || 0;
      S.skew = typeof d.now === "number" ? d.now - Date.now() / 1000 : 0;
      S.boxes = [...d.boxes].sort(newestFirst);
      S.day = vnDay(serverNow());
      const first = !S.loaded;
      S.loaded = true;
      if (!first) announce();
      rememberStatus();
      renderAll();
      if (wasFailed) say(T.back);
    } catch (err) {
      if (mine !== seq || err.status === 401 || leaving) return;
      const e = err instanceof ApiError ? err : new ApiError(T.fail, -1);
      const was = S.failed;
      S.failed = true;
      $("#net-msg").textContent = T.loadFail;
      renderBanner();
      renderLeads();
      if (!was) say($("#net-msg").textContent, true);
    }
  }
  function renderBanner() { $("#net-banner").hidden = !S.failed; }

  function schedule() {
    clearTimeout(timer);
    if (document.hidden || leaving) return;
    timer = setTimeout(tick, S.boxes.some((b) => b.status === "generating") ? 1500 : 6000);
  }
  async function tick() {
    if (document.hidden) return;
    await refresh(false);
    schedule();
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) clearTimeout(timer);
    else refresh(false).then(schedule);
  });

  function rememberStatus() {
    lastStatus.clear(); lastCount.clear();
    for (const b of S.boxes) { lastStatus.set(b.id, b.status); lastCount.set(b.id, b.items.length); }
  }
  function announce() {
    const now = new Map(S.boxes.map((b) => [b.id, b]));
    for (const b of S.boxes) {
      const was = lastStatus.get(b.id);
      if ((was && was !== b.status) || (lastCount.has(b.id) && lastCount.get(b.id) !== b.items.length)) ui.msg.delete(b.id);   // a problem line told how the box was; when the box moves on, the line goes
      if (!was || was === b.status) continue;
      if (b.status === "approved") { if (!approvedHere.delete(b.id)) say(T.approved(boxName(b), toStep3(b))); }
      else if (b.status === "draft" && was === "approved") { if (!reopenedHere.delete(b.id)) say(T.reopened(boxName(b))); }
      else if (b.status === "draft" && was === "generating") say(T.ready(boxName(b), b.items.length));
      else if (b.status === "error") say(T.failed(boxName(b)), true);
    }
    for (const [id, was] of lastStatus) {
      if (now.has(id) || deletedHere.has(id) || was === "approved") continue;
      const old = lists.cards.get(id);
      say(T.gone(old ? old.name : `số ${id}`), true);
    }
  }

  /* ---------------------------------------------------------------- the top: who is signed in, the login-off sentence */
  function renderWho() {
    const p = S.person;
    $("#who-box").hidden = !p;
    $("#admin-link").hidden = !(p && p.admin);
    $("#who").textContent = p ? (p.name || p.id) : "";             // the name; the ID only when there is no name
    $("#open-note").hidden = !S.open;
  }

  /* ---------------------------------------------------------------- progress strip: follows the newest box of today */
  function renderProgress() {
    const mine = S.boxes.filter(isToday);                  // a box of an earlier day is done with: the new day starts at step 1
    const now = mine.length ? (mine[0].status === "approved" ? 3 : 2) : 1;
    const ready = mine.some(readyBox);                     // an older box of today may already be waiting in step 3
    for (const li of document.querySelectorAll(".d-step-li")) {
      const n = Number(li.dataset.step);
      li.classList.toggle("is-now", n === now);
      li.classList.toggle("is-done", n < now);
      const a = $("a", li);
      if (n === now) a.setAttribute("aria-current", "step"); else a.removeAttribute("aria-current");
      $(".d-step-s", li).textContent = n < now ? "Xong" : n === now ? "Đang ở đây" : n === 3 && ready ? T.stepReady : "Chưa tới";
    }
  }

  /* ---------------------------------------------------------------- step 1: the form */
  const FALLBACK_KINDS = [{ label: "Box B2" }, { label: "Box B3" }, { label: "Box A1" }];
  const KIND_ORDER = ["B2", "B3", "A1"];
  let kindsSig = "";
  function renderKinds() {
    const rank = (s) => { const i = KIND_ORDER.indexOf(String(s.label).replace(/^Box\s+/i, "").toUpperCase()); return i < 0 ? 99 : i; };
    const list = (S.stages.length ? [...S.stages] : FALLBACK_KINDS).sort((a, b) => rank(a) - rank(b) || String(a.label).localeCompare(String(b.label)));
    const sig = JSON.stringify(list);
    if (sig === kindsSig) return;
    kindsSig = sig;
    const root = $("#kinds");
    const checked = $("input[name=kind]:checked", root);
    const keep = checked ? checked.value : "";
    const hadFocus = root.contains(document.activeElement);
    root.replaceChildren(...list.map((s, i) => h("div", { class: "d-choice" },
      h("input", { type: "radio", name: "kind", id: `k-${i}`, value: s.label, required: true, checked: s.label === keep }),
      h("label", { for: `k-${i}` }, h("span", { class: "d-kind" }, s.label), s.count ? h("span", { class: "d-count" }, `${s.count} món`) : null))));
    if (hadFocus) $("input[name=kind]", root).focus();
  }
  let moduleTouched = false, autoModule = "";
  function prefillModule() {                              // the module of this week's plan first, then the one of the person; never over what the person typed
    const input = $("#f-module");
    const m = (P.server && P.server.module) || (S.person && S.person.module) || "";
    if (moduleTouched || !m || input.value === m || (input.value && input.value !== autoModule)) return;
    input.value = m; autoModule = m;
    queueRecent(true);
  }

  function fieldError(which, msg, focusEl) {
    const err = $(`#err-${which}`);
    err.textContent = msg; err.hidden = !msg;
    if (which === "module") $("#f-module").setAttribute("aria-invalid", msg ? "true" : "false");
    if (which === "kind") for (const r of document.querySelectorAll("input[name=kind]")) r.setAttribute("aria-invalid", msg ? "true" : "false");
    if (msg && focusEl) focusEl.focus();
  }
  function clearErrors() {
    fieldError("module", ""); fieldError("kind", "");
    $("#err-form").hidden = true; $("#err-form").textContent = "";
  }

  async function onCreate(e) {
    e.preventDefault();
    clearErrors();
    const input = $("#f-module");
    const module = input.value.trim().toUpperCase();
    input.value = module;
    const kind = $("input[name=kind]:checked");
    if (!module) { fieldError("module", T.needModule, input); return; }
    if (!kind) { fieldError("kind", T.needKind, $("input[name=kind]")); return; }
    const go = $("#f-go");
    go.disabled = true;
    try {
      const out = await api("/api/my/boxes", {
        method: "POST",
        body: { module, label: kind.value, theme: $("#f-theme").value.trim(), code: $("#f-code").value.trim(), anchor: $("#f-anchor").value.trim() },
      });
      kind.checked = false;                                // one box per module and kind: the next one is another kind
      S.focusNew = out.id;
      say(T.created(`${module} ${kind.value}`));
      await refresh(true);
      loadPlan();
      queueRecent(true);
      schedule();
    } catch (err) {
      if (err.status === 401) return;
      $("#err-form").textContent = err.message; $("#err-form").hidden = false;
      say(err.message, true);
    } finally { go.disabled = false; }
  }

  /* ---------------------------------------------------------------- keyed lists that keep keyboard focus across re-renders */
  function refocus(name, id, el, key) {
    const find = (k) => {
      const t = k ? el.querySelector(`[data-key="${k}"]`) : null;
      return t && !t.disabled ? t : null;
    };
    const mine = lost && lost.list === name && lost.id === id;
    let t = key && key !== "head" ? find(key) : null;
    if (!t && key === "head" && mine) t = find(lost.key);
    if (t) { t.focus(); if (mine) lost = null; return; }
    const head = find("head");
    if (head) head.focus();
    const busyNow = el.classList.contains("is-generating");        // a button turned off because the box is busy: give focus back when it is on again
    if (key && key !== "head" && busyNow) lost = { list: name, id, key };
    else if (mine && !busyNow) lost = null;                        // the box is not busy and the button is gone for good: forget it
  }

  function syncList(name, root, boxes, sigOf, build) {
    const map = lists[name];
    const alive = new Set(boxes.map((b) => b.id));
    for (const [id, c] of map) {
      if (alive.has(id)) continue;
      const hadFocus = c.el.contains(document.activeElement);
      c.el.remove(); map.delete(id);
      if (hadFocus) focusAfterRemoval(name);
    }
    for (const b of boxes) {
      const sig = sigOf(b);
      const old = map.get(b.id);
      if (old && old.sig === sig) continue;
      const active = document.activeElement;
      const had = old && old.el.contains(active);
      const key = had && active.dataset ? active.dataset.key : null;
      const el = build(b);
      if (old) old.el.replaceWith(el);
      map.set(b.id, { el, sig, name: boxName(b) });
      if (had) refocus(name, b.id, el, key);
    }
    const nodes = boxes.map((b) => map.get(b.id).el);
    const have = [...root.children];
    if (nodes.length !== have.length || nodes.some((n, i) => n !== have[i])) root.replaceChildren(...nodes);
  }
  function focusAfterRemoval(name) {
    const first = name === "cards" && S.boxes.length ? lists.cards.get(S.boxes[0].id) : null;
    const head = first ? $('[data-key="head"]', first.el) : name === "cards" ? $("#s1-h") : $("#s3-h");
    if (head) head.focus();
  }

  /* ---------------------------------------------------------------- step 2: one card per box */
  function expiryOf(b) {                                    // a draft or a failed box nobody touches is deleted by the server after ttl_hours
    if (!S.ttl || (b.status !== "draft" && b.status !== "error")) return null;
    const left = b.updated_at + S.ttl * 3600 - serverNow();
    const warn = left < 7200;
    const when = hoursText(Math.max(0, left));
    const text = b.status === "draft"
      ? (warn ? `Hộp này sắp tự mất: còn ${when} nếu bạn chưa duyệt.` : `Hộp này tự mất sau ${when} nếu bạn chưa duyệt.`)
      : (warn ? `Hộp này sắp tự mất: còn ${when} nếu bạn chưa chọn lại.` : `Hộp này tự mất sau ${when} nếu bạn chưa chọn lại.`);
    return { text, warn };
  }
  const isFull = (b) => b.items.length === b.count;
  function askOf(b) {                                       // the question that is open on this card, only while it still makes sense
    const a = ui.ask.get(b.id);
    if (a === "del") return b.status === "draft" || b.status === "error" ? a : null;
    if (a === "approve") return b.status === "draft" && isFull(b) ? a : null;
    if (a === "regen") return (b.status === "draft" || b.status === "error") && b.items.length > 0 ? a : null;     // nothing to lose with no món: no question
    return null;
  }
  const cardSig = (b) => JSON.stringify([b, askOf(b) || "", ui.msg.get(b.id) || "", expiryOf(b), dayTag(b)]);

  function renderCards() {
    for (const id of [...ui.ask.keys()]) {                  // the box changed under an open question (approved elsewhere, an object taken out): close it
      const b = S.boxes.find((x) => x.id === id);
      if (!b || !askOf(b)) ui.ask.delete(id);
    }
    syncList("cards", $("#cards"), S.boxes, cardSig, buildCard);
  }
  function renderTakes() {
    syncList("takes", $("#takes"), S.boxes.filter(takeable), (b) => JSON.stringify(b), buildTake);
  }
  function renderTimed() {                                  // the clock moved: a card may need a new "time left" sentence, and after midnight every box is a day older
    const day = vnDay(serverNow());
    if (day !== S.day) {
      S.day = day;
      S.boxes.sort(newestFirst);
      renderProgress(); renderLeads(); renderTakes();
      if (P.server) loadPlan();                             // the units need today's boxes only
    }
    renderCards();
  }
  const readyBox = (b) => (b.status === "draft" || b.status === "approved") && b.items.length > 0;
  const takeable = (b) => readyBox(b) && isToday(b);        // yesterday's món were taken yesterday and the box is already in SM

  const busy = new Set();
  async function act(b, work) {                              // true when the work went through
    if (busy.has(b.id)) return false;
    busy.add(b.id);
    ui.msg.delete(b.id);
    try {
      await work();
      await refresh(true);
      loadPlan();                                            // the unit that owns this box shows its new state
      return true;
    } catch (e) {
      if (e.status !== 401) { ui.msg.set(b.id, e.message); say(e.message, true); renderCards(); await refresh(true); }
      return false;
    } finally { busy.delete(b.id); schedule(); }
  }

  const swap = (b, it) => act(b, async () => {
    await api(`/api/my/boxes/${b.id}/items/${it.pos}/replace`, { method: "POST" });
    say(T.swapping(itemName(it)));
  });
  const regen = (b) => act(b, async () => {
    await api(`/api/my/boxes/${b.id}/regenerate`, { method: "POST" });
    say(T.regen(boxName(b)));
  });
  const fill = (b) => act(b, async () => {
    await api(`/api/my/boxes/${b.id}/fill`, { method: "POST" });
    say(T.filling(boxName(b)));
  });
  function focusIn(id, key) {
    const c = lists.cards.get(id);
    const t = c && $(`[data-key="${key}"]`, c.el);
    if (t && !t.disabled) { t.focus(); return true; }
    return false;
  }
  function ask(b, what, yes) {                              // an inline question in the card: "del" (delete), "approve" or "regen" (pick the whole box again)
    if (yes) ui.ask.set(b.id, what); else ui.ask.delete(b.id);
    renderCards();
    lost = null;
    focusIn(b.id, yes ? `${what}-no` : what);               // the safe answer first; Chưa and Giữ lại (and Escape) give the focus back to the button that asked
  }
  function doRegen(b) {                                     // Có, chọn lại: the question closes and the work goes on as if the button was pressed (same focus, same message)
    ask(b, "regen", false);
    return regen(b);
  }
  function confirmBox(b, what, o) {                         // the question in the card: a named group, the safe answer first, Escape is the safe answer
    const id = `cf-${b.id}`;
    return h("div", { class: "d-confirm" + (o.ok ? " is-ok" : ""), role: "group", "aria-labelledby": id,
      onkeydown: (e) => { if (e.key === "Escape") { e.preventDefault(); ask(b, what, false); } } },
    h("p", { id }, o.text),
    btn(o.yes, o.onYes, { key: `${what}-yes`, cls: o.yesCls }),
    btn(o.no, () => ask(b, what, false), { key: `${what}-no` }));
  }
  async function doApprove(b) {
    approvedHere.add(b.id);
    const ok = await act(b, async () => {
      try { await api(`/api/my/boxes/${b.id}/approve`, { method: "POST" }); }
      catch (e) { approvedHere.delete(b.id); ui.ask.delete(b.id); throw e; }
    });
    lost = null;
    if (ok) { say(T.approvedNow(boxName(b), toStep3(b))); focusIn(b.id, "head"); }
    else { approvedHere.delete(b.id); ui.ask.delete(b.id); if (!focusIn(b.id, "approve")) focusIn(b.id, "head"); }
  }
  async function doReopen(b) {                              // the undo: no question
    reopenedHere.add(b.id);
    const ok = await act(b, async () => {
      try { await api(`/api/my/boxes/${b.id}/reopen`, { method: "POST" }); }
      catch (e) { reopenedHere.delete(b.id); throw e; }
    });
    lost = null;
    if (ok) { say(T.reopened(boxName(b))); focusIn(b.id, "head"); }
    else { reopenedHere.delete(b.id); if (!focusIn(b.id, "reopen")) focusIn(b.id, "head"); }
  }
  async function doDelete(b) {
    const order = S.boxes.map((x) => x.id);
    const at = order.indexOf(b.id);
    deletedHere.add(b.id);
    ui.ask.delete(b.id);
    await act(b, async () => {
      await api(`/api/my/boxes/${b.id}`, { method: "DELETE" });
      S.boxes = S.boxes.filter((x) => x.id !== b.id);
      const near = S.boxes[at] || S.boxes[at - 1];
      lost = null;
      renderAll();
      const el = near && lists.cards.get(near.id);
      const head = el ? $('[data-key="head"]', el.el) : $("#s1-h");
      if (head) head.focus();
      say(T.deleted(boxName(b)));
    });
    if (lists.cards.has(b.id)) deletedHere.delete(b.id);     // it did not go: forget
  }

  function buildCard(b) {
    const st = b.status, gen = st === "generating", appr = st === "approved";
    const hid = `bh-${b.id}`;
    const items = [...b.items].sort((x, y) => x.pos - y.pos);
    const full = isFull(b);
    const canSwap = st === "draft", canWhole = st === "draft" || st === "error";
    const canFill = canWhole && items.length > 0 && !full;      // some objects are there, some places are empty
    const canApprove = st === "draft" && full;
    const showApprove = st === "draft" || gen;                  // a box that is still being picked shows the button too, turned off, with the reason
    const approveWhy = gen ? T.whyNotDone : st === "draft" && !full ? T.whyShort(items.length, b.count) : "";
    const wid = `aw-${b.id}`, uid = `au-${b.id}`;
    const sec = b.code && b.code !== b.module ? b.code : "";
    const facts = [];
    if (sec) facts.push(`Mã section ${sec}`);
    if (b.anchor) facts.push(`Đồ lớn ${b.anchor}`);
    if (b.theme) facts.push(`Bối cảnh ${b.theme}`);
    facts.push(gen || items.length >= b.count ? `${b.count} món` : `${items.length} trên ${b.count} món`);
    const exp = expiryOf(b);
    const asking = askOf(b);

    const buttons = () => h("div", { class: "d-actions" },
      showApprove ? btn("Duyệt hộp", () => ask(b, "approve", true), {
        key: "approve", cls: "d-btn-primary d-btn-wide", disabled: !canApprove, desc: approveWhy ? `${hid} ${wid}` : hid }) : null,
      appr && !b.pushed ? btn("Bỏ duyệt", () => doReopen(b), { key: "reopen", desc: `${hid} ${uid}` }) : null,
      canFill ? btn("Điền ô trống", () => fill(b), { key: "fill", cls: "d-btn-primary", desc: hid }) : null,
      btn("Chọn lại cả hộp", () => (items.length ? ask(b, "regen", true) : regen(b)), { key: "regen", disabled: !canWhole, desc: hid }),     // with no món there is nothing to lose: no question
      btn("Xoá hộp", () => ask(b, "del", true), { key: "del", disabled: !canWhole, cls: "d-btn-danger", desc: hid }));
    const actions = asking === "del"
      ? confirmBox(b, "del", { text: T.confirmDelete(boxName(b)), yes: "Có, xoá hộp", onYes: () => doDelete(b), yesCls: "d-btn-danger", no: "Giữ lại" })
      : asking === "approve"
        ? confirmBox(b, "approve", { text: T.confirmApprove(boxName(b)), yes: "Có, duyệt hộp", onYes: () => doApprove(b), yesCls: "d-btn-primary", no: "Chưa", ok: true })
        : asking === "regen"
          ? confirmBox(b, "regen", { text: T.confirmRegen(boxName(b)), yes: "Có, chọn lại", onYes: () => doRegen(b), yesCls: "d-btn-danger", no: "Giữ lại" })
          : buttons();

    return h("article", { class: `d-card is-${st}`, "data-box": b.id, "aria-labelledby": hid },
      h("div", { class: "d-card-top" }, headingOf(b, hid), h("span", { class: `d-chip is-${st}` }, b.status_text || STATUS[st] || st)),
      h("p", { class: "d-meta" }, facts.map((f) => h("span", {}, f))),
      st === "error" ? h("div", { class: "d-errblock" }, b.message ? h("p", { class: "d-alert" }, b.message) : null, h("p", { class: "d-why" }, canFill ? T.errorFill : T.errorNext)) : null,
      exp ? h("p", { class: "d-expiry" + (exp.warn ? " is-warn" : "") }, exp.text) : null,
      items.some((it) => it.filled) ? h("p", { class: "d-fillnote" }, T.checkByApp) : null,
      items.length ? h("ol", { class: "d-items", role: "list" }, items.map((it) => {
        const nid = `in-${b.id}-${it.pos}`;
        return h("li", { class: "d-item", "data-pos": it.pos },
          h("span", { class: "d-num", "aria-hidden": "true" }, String(it.pos)),
          h("div", { class: "d-item-main" },
            h("p", { class: "d-name", id: nid }, itemName(it), it.filled ? [" ", h("span", { class: "d-filled" }, T.byApp)] : null),
            h("p", { class: "d-facts" },
              h("span", {}, `ID ${it.id}`), h("span", {}, `Số lượng ${it.qty}`),
              h("span", {}, it.slot ? `Vị trí kệ ${it.slot}` : "Chưa rõ vị trí kệ"))),
          st === "error" ? null : btn("Đổi món", () => swap(b, it), { key: `swap-${it.pos}`, disabled: !canSwap, desc: nid, cls: "d-btn-small" }));
      })) : null,
      actions,
      !asking && approveWhy ? h("p", { class: "d-why", id: wid }, approveWhy) : null,
      appr ? h("p", { class: "d-why", id: uid }, b.pushed ? T.whyPushed : T.whyApproved) : null,
      ui.msg.get(b.id) ? h("p", { class: "d-card-err" }, ui.msg.get(b.id)) : null);
  }

  /* ---------------------------------------------------------------- step 3: copy and print */
  async function copy(id, kind) {
    const b = S.boxes.find((x) => x.id === id);
    if (!b) return;
    const rows = Array.from({ length: b.count }, (_, i) => b.items.find((x) => x.pos === i + 1));    // one line per place, empty when it is empty
    const text = kind === "ids"
      ? rows.map((it) => (it ? it.id : "")).join("\n")                       // the same text as "Sao chép ID" on the admin page
      : rows.map((it) => (it ? `${it.id}\t${it.qty}` : "")).join("\n");      // ID, a tab, quantity: two columns when pasted
    const n = b.items.length;
    if (await copyText(text)) say(T.copied(n, boxName(b)));
    else say(T.copyBlocked, true);
  }

  function buildTake(b) {
    const hid = `th-${b.id}`;
    return h("div", { class: `d-take is-${b.status}`, role: "group", "aria-labelledby": hid, "data-box": b.id },
      h("div", { class: "d-card-top" }, headingOf(b, hid), h("span", { class: `d-chip is-${b.status}` }, b.status_text || STATUS[b.status])),
      b.status === "draft" ? h("p", { class: "d-why" }, T.draftCanChange) : null,
      h("div", { class: "d-take-actions" },
        btn("Sao chép ID", () => copy(b.id, "ids"), { key: "copy-ids", desc: hid }),
        btn("Sao chép ID và số lượng", () => copy(b.id, "both"), { key: "copy-both", desc: hid }),
        h("a", { class: "d-btn d-btn-primary", href: `/my/print?ids=${b.id}`, target: "_blank", rel: "noopener", "data-key": "print",
          "aria-describedby": `${hid} d-newtab` }, "In phiếu lấy đồ")));
  }

  /* ---------------------------------------------------------------- the sentences between the headings */
  function renderLeads() {
    const ready = S.boxes.some(takeable);
    setLine($("#s2-lead"), !S.loaded ? (S.failed ? "" : T.loading) : S.boxes.length ? "" : T.noBox);
    setLine($("#s3-lead"), !S.loaded ? (S.failed ? "" : T.loading) : ready ? "" : T.noReady);
  }

  function renderAll() {
    if (leaving) return;
    renderWho(); renderBanner(); renderKinds(); prefillModule(); renderProgress(); renderLeads(); renderCards(); renderTakes();
    if (S.focusNew != null) {                               // a box was just made: take the keyboard to it
      const c = lists.cards.get(S.focusNew);
      if (c) { S.focusNew = null; $('[data-key="head"]', c.el).focus(); }
    }
    const sig = S.boxes.map((b) => `${b.id}:${b.status}`).join(",");      // a box changed state (also elsewhere): the units show it
    if (P.sig !== null && sig !== P.sig && P.server) loadPlan();
    P.sig = sig;
  }

  /* ---------------------------------------------------------------- Hôm nay: the clock of the shift (Vietnam time, never the computer's zone) */
  // minutes since midnight: the work day is 2:30 to 11:30; "to" is when the item is finished. The paper and the report share the window 9:00 to 10:00; B3 starts in the middle of it.
  const DAY = [
    { key: "start", from: 150, to: 210, when: "2:30", text: "Đầu ngày: trả hộp trong SM, hỏi leader unit nào đi làm", short: "việc đầu ngày" },
    { key: "b1", from: 210, to: 330, when: "3:30 đến 5:30", text: "B1", short: "B1" },
    { key: "b2a", from: 360, to: 435, when: "6:00 đến 7:15", text: "B2", short: "B2" },
    { key: "b2b", from: 495, to: 570, when: "8:15 đến 9:30", text: "B2", short: "B2" },
    { key: "print", from: 540, to: 600, when: "9:00 đến 10:00", text: "In giấy cho supporter", short: "in giấy cho supporter" },
    { key: "report", from: 540, to: 600, when: "9:00 đến 10:00", text: "Báo cáo cuối ngày: gửi quản lý các unit không đi làm", short: "báo cáo cuối ngày" },
    { key: "b3", from: 570, to: 690, when: "từ 9:30", text: "B3", short: "B3" },
  ];
  const SHIFT_START = 150, SHIFT_END = 690;
  const VN_TIME = (() => {
    try { return new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Ho_Chi_Minh", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }); } catch { return null; }
  })();
  function vnClock() {
    const d = new Date();
    let hh, mm;
    if (VN_TIME) {
      const parts = VN_TIME.formatToParts(d);
      hh = Number(parts.find((p) => p.type === "hour").value) % 24;
      mm = Number(parts.find((p) => p.type === "minute").value);
    } else {                                                // no time zone data: Vietnam is UTC+7 all year
      const v = new Date(d.getTime() + 7 * 3600 * 1000);
      hh = v.getUTCHours(); mm = v.getUTCMinutes();
    }
    return { min: hh * 60 + mm, text: `${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}` };
  }
  const clockText = (m) => `${Math.floor(m / 60)}:${String(m % 60).padStart(2, "0")}`;
  const dayRows = new Map();                                // item key -> {li, mark}
  function buildTimeline() {
    $("#timeline").replaceChildren(...DAY.map((it) => {
      const mark = h("span", { class: "d-tl-mark" });
      const li = h("li", { class: "d-tl" }, h("span", { class: "d-tl-when" }, it.when), h("span", { class: "d-tl-what" }, it.text), mark);
      dayRows.set(it.key, { li, mark });
      return li;
    }));
  }
  function setText(el, text) { if (el.textContent !== text) el.textContent = text; }
  function setTag(el, state) {                              // a word, not only a colour: the section that is now or next
    const text = state === "now" ? T.markNow : state === "next" ? T.markNext : "";
    setText(el, text ? ` ${text}` : "");
    el.hidden = !text;
  }
  function renderClock() {
    const t = vnClock();
    const inShift = t.min >= SHIFT_START && t.min < SHIFT_END;
    const ahead = inShift ? DAY.filter((it) => it.from > t.min) : [];
    const nextAt = ahead.length ? Math.min(...ahead.map((it) => it.from)) : -1;       // what starts next; items with the same start are next together
    const next = ahead.filter((it) => it.from === nextAt);
    const state = {};
    for (const it of DAY) state[it.key] = !inShift ? "" : t.min >= it.to ? "done" : t.min >= it.from ? "now" : it.from === nextAt ? "next" : "";
    setText($("#now-line"), T.clock(t.text, !inShift ? T.clockOff : next.length ? T.clockNext(next.map((it) => it.short).join(" và "), clockText(nextAt)) : T.clockEnd(clockText(SHIFT_END))));
    for (const it of DAY) {
      const row = dayRows.get(it.key), s = state[it.key];
      if (!row) continue;
      setText(row.mark, s === "done" ? T.markDone : s === "now" ? T.markNow : s === "next" ? T.markNext : "");
      row.li.className = `d-tl${s ? " is-" + s : ""}`;
      if (s === "now") row.li.setAttribute("aria-current", "time"); else row.li.removeAttribute("aria-current");
    }
    setTag($("#ret-tag"), state.start);
    setTag($("#rep-tag"), state.report);
  }
  let clockTimer = 0;
  function tickClock() {                                    // once a minute, not while the tab is hidden
    clearTimeout(clockTimer);
    renderClock();
    if (document.hidden || leaving) return;
    clockTimer = setTimeout(tickClock, 60000 - (Date.now() % 60000) + 50);
  }

  /* ---------------------------------------------------------------- Hôm nay: the boxes to hand back, the plan of the units, the report */
  const SHIFTS_FALLBACK = [{ key: "full", text: "Cả ngày" }, { key: "first", text: "Nửa ca đầu" }, { key: "second", text: "Nửa ca sau" },
    { key: "b1b2", text: "Chỉ làm B1 và B2" }, { key: "off", text: "Nghỉ" }];
  const shiftList = () => (P.server && P.server.shifts && P.server.shifts.length ? P.server.shifts : SHIFTS_FALLBACK);
  const rowEls = new Map();                                 // row uid -> its elements
  let rowN = 0;
  const rowOf = (u) => ({ uid: `u${++rowN}`, code: u.code || "", anchor: u.anchor || "", shift: u.shift || "full" });
  const planKey = (module, units) => JSON.stringify([String(module || "").trim(), units.map((u) => [String(u.code || "").trim(), String(u.anchor || "").trim(), u.shift])]);
  const rowsKey = () => planKey($("#p-module").value, P.rows);
  const isDirty = () => P.base !== null && rowsKey() !== P.base;
  function setLine(el, text) { setText(el, text); el.hidden = !text; }

  function needsOf(r) {                                     // the saved unit that matches this row, only while the row says the same as the saved plan
    const code = r.code.trim().toLowerCase();
    const u = P.server && code ? P.server.units.find((x) => String(x.code).trim().toLowerCase() === code) : null;
    return u && u.shift === r.shift ? u : null;
  }
  function goCard(id) {
    const c = lists.cards.get(id);
    const head = c && $('[data-key="head"]', c.el);
    (head || $("#s2-h")).focus();
  }
  function fillForm(r, kind) {                              // "Tạo B2": the create form gets this unit and this kind, the keyboard goes to the theme
    const module = $("#p-module").value.trim().toUpperCase();
    const code = r.code.trim();
    if (module) { $("#f-module").value = module; moduleTouched = true; }
    if ($("#f-code").value.trim() !== code) $("#f-theme").value = "";     // another unit: the old scene does not belong to it
    $("#f-code").value = code;
    $("#f-anchor").value = r.anchor.trim();
    const radio = [...document.querySelectorAll("input[name=kind]")].find((x) => String(x.value).replace(/^Box\s+/i, "").toUpperCase() === kind);
    if (radio) radio.checked = true;
    clearErrors();
    queueRecent(true);
    say(T.filled(code, kind));
    $("#f-theme").focus();
  }
  function buildNeeds(r, u) {
    if (!u.needs.length) return [h("p", { class: "d-why" }, T.needNone)];
    return [
      h("p", { class: "d-need-h" }, T.needHead),
      h("ul", { class: "d-needlist", role: "list" }, u.needs.map((n) => h("li", { class: "d-need" },
        h("div", { class: "d-need-row" },
          h("span", { class: "d-need-k" }, n.optional ? `${n.kind} (${T.needOptional})` : n.kind),
          h("span", { class: `d-chip is-${n.state}` }, NEED_STATE[n.state] || n.state),
          n.box_id
            ? h("a", { class: "d-link", href: `#bh-${n.box_id}`, "data-key": `need-${n.kind}`, "aria-label": `Xem hộp ${n.kind} của ${u.code}`,
              onclick: (e) => { e.preventDefault(); goCard(n.box_id); } }, `Xem hộp ${n.kind}`)
            : btn(`Tạo ${n.kind}`, () => fillForm(r, n.kind), { key: `need-${n.kind}`, aria: `Tạo ${n.kind} cho ${u.code}`, cls: "d-btn-small" })),
        n.note && !n.optional ? h("p", { class: "d-why" }, n.note) : null))),     // an optional box says so in its name: its note would only repeat that
    ];
  }
  function renderRowNeeds(r) {
    const e = rowEls.get(r.uid);
    if (!e) return;
    const code = r.code.trim();
    e.remove.setAttribute("aria-label", code ? `Xoá unit ${code}` : "Xoá unit");
    const u = needsOf(r);
    const sig = u ? JSON.stringify([u.code, u.needs]) : "";
    if (sig === e.sig) return;
    e.sig = sig;
    const had = e.needs.contains(document.activeElement);
    const key = had && document.activeElement.dataset ? document.activeElement.dataset.key : null;
    e.needs.replaceChildren(...(u ? buildNeeds(r, u) : []));
    if (had) { const t = key && $(`[data-key="${key}"]`, e.needs); (t || e.code).focus(); }
  }
  function buildRow(r) {
    const id = r.uid;
    const sync = (key) => (e) => { r[key] = e.target.value; renderRowNeeds(r); renderDirty(); };
    const code = h("input", { id: `${id}-c`, type: "text", maxlength: "20", value: r.code, spellcheck: "false", autocapitalize: "characters", autocomplete: "off", oninput: sync("code") });
    const anchor = h("input", { id: `${id}-a`, type: "text", maxlength: "120", value: r.anchor, autocomplete: "off", oninput: sync("anchor") });
    const sel = h("select", { id: `${id}-s`, onchange: sync("shift") }, shiftList().map((s) => h("option", { value: s.key, selected: s.key === r.shift }, s.text)));
    const remove = btn("Xoá unit", () => removeRow(r), { cls: "d-btn-danger", key: "unit-del" });
    const needs = h("div", { class: "d-needs" });
    const li = h("li", { class: "d-unit", "data-uid": id },
      h("div", { class: "d-row2" },
        h("div", { class: "d-field" }, h("label", { for: `${id}-c` }, "Mã section"), code),
        h("div", { class: "d-field" }, h("label", { for: `${id}-a` }, "Đồ lớn"), anchor)),
      h("div", { class: "d-unit-line" }, h("div", { class: "d-field" }, h("label", { for: `${id}-s` }, "Ca làm"), sel), remove),
      needs);
    rowEls.set(id, { li, needs, code, anchor, sel, remove, sig: null });
    return li;
  }
  function setRows(d) {                                     // the rows follow an answer of the server
    const had = $("#units").contains(document.activeElement);
    $("#p-module").value = d.module || "";
    P.rows = d.units.map(rowOf);
    rowEls.clear();
    $("#units").replaceChildren(...P.rows.map(buildRow));
    if (had) $("#p-module").focus();
  }
  function addRow() {
    const r = rowOf({});
    P.rows.push(r);
    $("#units").append(buildRow(r));
    renderRowNeeds(r);
    renderDirty();
    rowEls.get(r.uid).code.focus();
  }
  function removeRow(r) {
    const at = P.rows.indexOf(r);
    if (at < 0) return;
    P.rows.splice(at, 1);
    rowEls.get(r.uid).li.remove();
    rowEls.delete(r.uid);
    renderDirty();
    const near = P.rows[at] || P.rows[at - 1];
    (near ? rowEls.get(near.uid).code : $("#unit-add")).focus();
    say(T.unitRemoved(r.code.trim()));
  }
  function renderDirty() {                                  // typed changes not saved: the last saved report stays, but it cannot be copied until the plan is saved
    const dirty = isDirty();
    $("#plan-dirty").hidden = !dirty;
    const copy = $("#rep-copy");
    copy.disabled = dirty;
    if (dirty) copy.setAttribute("aria-describedby", "plan-dirty"); else copy.removeAttribute("aria-describedby");     // the one sentence "Chưa lưu thay đổi." says why
  }

  function renderReturn(d) {
    const list = d ? d.to_return : [];
    setText($("#ret-n"), list.length ? ` (${list.length})` : "");
    setLine($("#ret-empty"), !d ? (P.failed ? "" : T.loadingPlan) : list.length ? "" : T.noReturn);
    $("#ret-list").replaceChildren(...list.map((x) => h("li", {}, `${dayWord(x.days_ago)}: ${x.title}`)));
  }
  function renderReport(d) {                                // the report is only shown once today's plan is saved: from yesterday's units it would be wrong
    const has = !!(d && d.units.length);
    const show = has && !!d.saved;
    $("#rep-box").hidden = !show;
    setLine($("#rep-empty"), show ? "" : !d ? (P.failed ? "" : T.loadingPlan) : has ? T.reportNotSaved : T.noUnits);
    if (!show) return;
    const box = $("#rep-text");
    if (box.value !== d.report) box.value = d.report;
    box.rows = Math.min(10, Math.max(3, d.report.split("\n").length + 1));
    fitReport();
  }
  function fitReport() {                                    // long lines wrap on a phone: the area grows to show the whole report (only while the section is open)
    const box = $("#rep-text");
    if (!$("#fold-report").open || !box.offsetParent) return;
    box.style.height = "auto";
    box.style.height = `${box.scrollHeight + 4}px`;
  }
  function renderPlan() {
    const d = P.server;
    $("#plan-banner").hidden = !P.failed;
    renderReturn(d);
    renderReport(d);
    $("#plan-old").hidden = !(d && d.units.length && !d.saved);
    for (const r of P.rows) renderRowNeeds(r);
    renderDirty();
    renderClock();
  }
  function applyPlan(d) {
    P.server = d;
    P.failed = false;
    if (!isDirty()) {                                       // nothing typed that is not saved: the rows follow the answer; typed text is never overwritten
      const key = planKey(d.module, d.units);
      if (rowsKey() !== key) setRows(d);
      P.base = key;
    }
    renderPlan();
    prefillModule();
  }

  let planSeq = 0, planBusy = false, planAgain = false, planTimer = 0;
  async function loadPlan() {
    if (planBusy) { planAgain = true; return; }
    planBusy = true;
    const mine = ++planSeq;
    try {
      const d = await api("/api/my/plan");
      if (mine !== planSeq || leaving) return;
      applyPlan(d);
    } catch (err) {
      if (mine !== planSeq || err.status === 401 || leaving) return;
      const was = P.failed;
      P.failed = true;
      $("#plan-msg").textContent = T.planFail;
      renderPlan();
      if (!was) say(T.planFail, true);
    } finally {
      planBusy = false;
      if (planAgain) { planAgain = false; loadPlan(); }
    }
  }
  function schedulePlan() {                                 // every few minutes, not while the tab is hidden
    clearTimeout(planTimer);
    if (document.hidden || leaving) return;
    planTimer = setTimeout(() => { loadPlan().then(schedulePlan); }, 180000);
  }
  let saving = false;
  async function savePlan() {
    if (saving) return;
    saving = true;
    const err = $("#plan-err");
    err.hidden = true; err.textContent = "";
    const body = { module: $("#p-module").value.trim().toUpperCase(), units: P.rows.map((r) => ({ code: r.code.trim(), anchor: r.anchor.trim(), shift: r.shift })) };
    const sent = P.rows.map((r) => ({ r, code: r.code.trim(), anchor: r.anchor.trim(), shift: r.shift }));
    try {
      planSeq++;                                            // an answer to an older read must not undo this write
      const d = await api("/api/my/plan", { method: "PUT", body });
      planSeq++;
      P.server = d;
      P.failed = false;
      if ($("#p-module").value.trim().toUpperCase() === body.module) $("#p-module").value = d.module || "";
      d.units.forEach((u, i) => {                           // the server tidied what was sent: show that, row by row, without rebuilding (the keyboard stays where it is)
        const s = sent[i], e = s && rowEls.get(s.r.uid);    // a row typed in again while the answer was on its way keeps what the person typed
        if (!e || s.r.code.trim() !== s.code || s.r.anchor.trim() !== s.anchor || s.r.shift !== s.shift) return;
        s.r.code = u.code; s.r.anchor = u.anchor || ""; s.r.shift = u.shift;
        if (e.code.value !== s.r.code) e.code.value = s.r.code;
        if (e.anchor.value !== s.r.anchor) e.anchor.value = s.r.anchor;
      });
      P.base = planKey(d.module, d.units);
      renderPlan();
      prefillModule();
      say(T.planSaved);
    } catch (e) {
      if (e.status === 401) return;
      err.textContent = e.message; err.hidden = false;
      say(e.message, true);
    } finally { saving = false; }
  }
  async function copyReport() {
    const d = P.server;
    if (!d || !d.saved || !d.report || isDirty()) return;
    if (await copyText(d.report)) say(T.copiedReport);
    else say(T.copyBlockedReport, true);
  }

  /* ---------------------------------------------------------------- "Gần đây": the scenes made lately for this module and section */
  let recentSeq = 0, recentTimer = 0;
  function renderRecent(module, code, items) {
    $("#recent").hidden = !items.length;
    if (!items.length) return;
    $("#recent-h").textContent = T.recentIn(module, code);
    $("#recent-list").replaceChildren(...items.map((x) => h("li", {}, [dayWord(x.days_ago), code ? "" : x.code, x.kind, x.theme].filter(Boolean).join(" · "))));
  }
  async function loadRecent() {
    const module = $("#f-module").value.trim().toUpperCase(), code = $("#f-code").value.trim();
    const mine = ++recentSeq;
    if (!module) { $("#recent").hidden = true; return; }
    try {
      const d = await api(`/api/my/recent?module=${encodeURIComponent(module)}&code=${encodeURIComponent(code)}`);
      if (mine !== recentSeq || leaving) return;
      renderRecent(module, code, Array.isArray(d.items) ? d.items : []);
    } catch { if (mine === recentSeq) $("#recent").hidden = true; }      // it only helps: a failed call never says anything and never blocks a box
  }
  function queueRecent(now) {
    recentSeq++;                                            // an answer to an older question is ignored
    clearTimeout(recentTimer);
    recentTimer = setTimeout(loadRecent, now ? 0 : 400);
  }

  /* ---------------------------------------------------------------- start */
  async function logout() {
    try {
      await api("/api/auth/logout", { method: "POST" });
    } catch (e) {
      if (e.status !== 401) { say(T.logoutFail, true); return; }
    }
    leaving = true;
    location.replace("/login");
  }

  $("#create-form").addEventListener("submit", onCreate);
  $("#f-module").addEventListener("input", () => { moduleTouched = true; queueRecent(false); });
  $("#f-code").addEventListener("input", () => queueRecent(false));
  $("#logout").addEventListener("click", logout);
  $("#retry").addEventListener("click", () => { refresh(true).then(schedule); });
  $("#p-module").addEventListener("input", renderDirty);
  $("#unit-add").addEventListener("click", addRow);
  $("#plan-save").addEventListener("click", savePlan);
  $("#plan-retry").addEventListener("click", () => { loadPlan().then(schedulePlan); });
  $("#rep-copy").addEventListener("click", copyReport);
  $("#fold-report").addEventListener("toggle", fitReport);
  window.addEventListener("resize", fitReport);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { clearTimeout(clockTimer); clearTimeout(planTimer); return; }
    tickClock();
    loadPlan().then(schedulePlan);
  });
  buildTimeline();
  tickClock();
  renderKinds();
  renderLeads();
  refresh(true).then(schedule);
  loadPlan().then(schedulePlan);
})();

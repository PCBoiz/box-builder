// ==UserScript==
// @name         Box builder: đồng bộ kho với SM
// @namespace    box-builder
// @version      0.4.1
// @description  Đọc kho đồ (kèm số lần dùng và ngày tạo) và danh sách hộp từ Scenario Manager (chỉ đọc) và gửi cho Box builder. Có thể tự làm mới khi tab SM đang mở. Bản này chỉ đọc: không tạo, sửa hay xoá gì trong SM.
// @match        https://sm.config.inc/*
// @match        https://sm.config.inc/object*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_addStyle
// @grant        GM_info
// @connect      box-builder-guwc.onrender.com
// @connect      localhost
// @connect      127.0.0.1
// @run-at       document-idle
// @noframes
// ==/UserScript==

/* What this does, in one paragraph: it runs in your own signed-in SM tab. It reads SM's object list and box list with GET
   requests only, and posts a compact copy to Box builder (the address and sync code are typed once into the panel and kept in
   the browser's own storage). This build writes nothing to SM: the constant WRITES_ENABLED is false, and three places check it
   (the preview returns no plan, so no confirm button appears; the create flow and the one function that holds the create request
   stop at their first line). The create code stays in the file so the review sees it, and it is dead until a separate build turns
   the constant on. It never moves, hands over, assigns, borrows, edits or deletes anything in SM, and it never sends the sync code
   to SM. The app's own checks decide what is kept.
   Every request to SM goes through one function (smRequest): it checks the address is this site and that the method and path are
   on a short list (SM_ALLOW), and it refuses the POST while the switch is false. Every request to Box builder goes through app():
   no cookies (anonymous), no redirects, and reads are tried again while a sleeping free server wakes up.
   Since 0.3.0 each object row also carries `uses` (the lifetime use count SM shows on its object page) and `created` (the date SM
   gives for the object). Both come from the same GET /api/objects pages, only as a number and a date string, never a person.
   Since 0.4.0 the panel shows what Box builder says about itself (ready boxes, how old its data is, which script version it expects),
   what the installed header says about updates, and has an optional switch that refreshes SM's boxes and the catalog on a timer
   while the SM tab is open (reads only).
   Since 0.4.1: the script runs in the top page only (@noframes), so a frame inside SM gets no panel and no timer of its own; an automatic run
   never saves what is typed in this tab's boxes (it reads the address, code and sector from storage, so a stale tab cannot undo a change made in another
   tab); an automatic catalog read waits a moment between pages; only a 401, a 403 or a redirect to the login page counts as "signed out" (a 5xx or a
   maintenance page is "SM is unwell" and the timer stays on); and a read never forwards a body or headers to SM. */
(function () {
  "use strict";
  if (window.__bbSm) return;
  window.__bbSm = true;

  const VERSION = "0.4.1";
  const WRITES_ENABLED = false;         // this build only reads SM; the create flow below is switched off in three places
  const DEFAULT_APP = "";               // the app fills this in when it serves the script, so nobody has to type the address
  const PAGE_CAP = 200;                 // never read more than this many pages of 30 from SM
  const APP_TIMEOUT = 90000;            // a sleeping free server can take about a minute to answer the first call
  const SM_TIMEOUT = 60000;             // one request to SM; after this it is given up
  const READ_RETRY_MS = [2000, 4000, 8000];   // a read from Box builder is tried again after these waits: 4 tries in all
  const AUTO_TICK_MS = 60 * 1000;       // how often the optional auto refresh looks at the clock
  const AUTO_BOXES_MS = 10 * 60 * 1000;     // SM's boxes are read this often while auto refresh is on
  const AUTO_CATALOG_MS = 60 * 60 * 1000;   // the catalog (and the boxes with it) this often
  const AUTO_RETRY_MS = 5 * 60 * 1000;      // after a failed run the next try is this far away
  const AUTO_PAGE_PAUSE_MS = 250;           // the automatic catalog read waits this long between two pages of SM, so up to 200 reads do not come in a burst
  const store = {
    get: (k, d) => { try { return GM_getValue(k, d); } catch { return d; } },
    set: (k, v) => { try { GM_setValue(k, v); } catch { /* storage unavailable: the panel still works for this visit */ } },
  };
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  let liveSay = null;                    // the panel's log while a run is going, so a wait for the server can be shown

  /* ------------------------------------------------------------------ talking to SM (GET only in this build) */
  class Plain extends Error { }          // an error whose message is already written for the person
  const READ_ONLY_PLAN = "Bản này chỉ đọc: xem được hộp sẽ tạo, chưa tạo được hộp trong SM.";
  const READ_ONLY_CREATE = "Bản này chỉ đọc: chưa tạo được hộp trong SM.";
  const signedOut = () => {
    const e = new Plain("Chưa đăng nhập SM trong tab này. Hãy đăng nhập SM rồi bấm lại.");
    e.stop = true; e.signedOut = true;
    return e;
  };
  const parseJson = (text, type) => { if (!/json/.test(type || "")) return null; try { return JSON.parse(text); } catch { return null; } };

  /* The only requests this script may send to SM. smRequest is the one place that calls fetch: it builds the address with
     `new URL`, refuses anything that is not this site, and refuses any method and path that is not on this list. This build sends
     GET only. The POST is listed so the create code further down can be reviewed, and smRequest refuses it while WRITES_ENABLED
     is false. */
  const SM_ALLOW = [
    ["GET", /^\/api\/boxes$/],
    ["GET", /^\/api\/objects$/],
    ["POST", /^\/api\/boxes$/],
  ];
  async function smRequest(path, init) {
    const opts = init || {};
    const method = String(opts.method || "GET").toUpperCase();
    let u;
    try { u = new URL(path, location.origin); } catch { throw new Plain("Địa chỉ gọi SM không hợp lệ nên không gửi gì."); }
    const ok = u.origin === location.origin && !u.username && !u.password
      && SM_ALLOW.some(([m, re]) => m === method && re.test(u.pathname)) && (method === "GET" || WRITES_ENABLED);
    if (!ok) throw new Plain(`Script này không được phép gọi ${method} ${u.pathname} trên SM nên không gửi gì.`);
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), SM_TIMEOUT);
    try {
      const extra = method === "GET" ? {} : { headers: opts.headers, body: opts.body };     // a read carries no body and no extra headers; only the listed POST forwards them
      const res = await fetch(u.href, { method, credentials: "same-origin", ...extra, signal: ctrl.signal });
      const text = await res.text();             // read inside the timer: a stalled body also ends as a timeout
      const type = res.headers.get("content-type") || "";
      return { status: res.status, ok: res.ok, redirected: res.redirected, url: res.url, type, text, json: parseJson(text, type) };
    } catch (e) {
      throw new Plain(ctrl.signal.aborted ? "SM không trả lời kịp. Thử lại sau ít phút." : "Không gọi được SM. Kiểm tra mạng rồi thử lại.");
    } finally { clearTimeout(timer); }
  }
  const loginPage = (r) => r.redirected && /\/login/.test(r.url);

  /* "Signed out" is decided only by a 401, a 403 or a redirect to the login page: those switch the auto refresh off. Anything else that is not
     JSON is SM being unwell: a 5xx or a proxy's page keeps the timer on (the next try is 5 minutes later). A 200 page that is not data could be a
     login page drawn in place or a maintenance page, so it is worded as both and, for the timer, is not "signed out". */
  const notData = () => new Plain("Chưa đăng nhập SM trong tab này, hoặc SM đang bảo trì (SM trả một trang web thay vì dữ liệu). Đăng nhập SM rồi bấm lại, hoặc thử lại sau ít phút.");
  async function smJson(path) {                  // a read that must give JSON
    const r = await smRequest(path, { method: "GET" });
    if (r.status === 401 || r.status === 403 || loginPage(r)) throw signedOut();
    if (!r.ok) throw new Plain(`SM trả lỗi ${r.status}. Thử lại sau ít phút.`);
    if (!/json/.test(r.type)) throw notData();
    if (r.json === null) throw new Plain("SM trả dữ liệu không đọc được. Thử lại sau ít phút.");
    return r.json;
  }
  const listOf = (d) => (Array.isArray(d) ? d : (d.objects || d.items || d.data || d.results || []));

  /* The only request in this file that could write to SM: one new box, same site as the page, called only from the confirm
     button's flow. It does nothing while WRITES_ENABLED is false. borrow_now is always false here, so SM never lends anything
     because of this script. */
  async function smCreate(payload) {
    if (!WRITES_ENABLED) { const e = new Plain(READ_ONLY_CREATE); e.stop = true; throw e; }
    const r = await smRequest("/api/boxes", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "application/json" }, body: JSON.stringify(payload),
    });
    if (r.status === 401 || loginPage(r)) throw signedOut();
    const json = r.json;
    if (!r.ok) {
      if (!json && /html/.test(r.type) && r.status !== 403) throw signedOut();
      const why = detailText(json);
      throw new Plain(why ? `SM từ chối: ${why}` : (r.status === 403 ? "SM từ chối (403). Tài khoản này có thể không được tạo hộp." : `SM trả lỗi ${r.status}.`));
    }
    if (!json) throw signedOut();
    const id = json.box_id != null ? json.box_id : (json.id != null ? json.id : (json.box && (json.box.box_id != null ? json.box.box_id : json.box.id)));
    return id == null || id === "" ? null : String(id);
  }
  function detailText(j) {                      // SM or the app explains a refusal as text or as a list of problems
    if (!j) return "";
    const d = j.detail != null ? j.detail : (j.message != null ? j.message : j.error);
    const t = Array.isArray(d) ? d.map((x) => (x && x.msg) || (typeof x === "string" ? x : "")).filter(Boolean).join("; ") : (typeof d === "string" ? d : "");
    return t.slice(0, 240);
  }

  /* ------------------------------------------------------------------ compact rows: only what the app needs */
  const digits = (v) => String(v == null ? "" : v).replace(/\D/g, "");
  function pickVi(o) {
    const t = o.translation && typeof o.translation === "object" ? (o.translation.vi || o.translation.name_vi || "") : "";
    return String(o.name_vi || o.vi || t || "").trim();
  }
  function availOf(o) {
    const q = Number(o.quantity);
    if (!Number.isFinite(q)) return null;
    const r = (Number(o.loan_reserved) || 0) + (Number(o.transfer_reserved) || 0);   // numbers only; anything else counts as 0
    return Math.max(0, q - r);
  }

  /* Lifetime use (SM's object page shows it as "LIFETIME USE: 1587 borrows at least, 1611 items total"). SM's JSON field is
     `_usage_count`, but its shape is not verified: it may be a number, a numeric string, or an object (boxes carry `borrows`,
     `borrow_units`, `borrows_undercounted`). Only a whole number leaves this function; anything else is null, never 0. */
  const USE_KEYS = ["borrows", "borrow_count", "count", "total", "uses", "used", "value"];
  function wholeNumber(v) {
    let n = null;
    if (typeof v === "number") n = v;
    else if (typeof v === "string" && /^\s*\d+(\.\d+)?\s*$/.test(v)) n = Number(v);
    return n !== null && Number.isFinite(n) && n >= 0 && n <= Number.MAX_SAFE_INTEGER ? Math.round(n) : null;
  }
  function usesOf(o) {
    const u = o._usage_count;
    const direct = wholeNumber(u);
    if (direct !== null) return direct;
    if (u && typeof u === "object" && !Array.isArray(u)) {
      for (const k of USE_KEYS) { const n = wholeNumber(u[k]); if (n !== null) return n; }     // the named keys first, in this order
      for (const k of Object.keys(u)) { const n = wholeNumber(u[k]); if (n !== null) return n; }   // then the first number there is
    }
    return null;
  }
  /* The creation date: a text with a digit in it goes as SM wrote it. A number of seconds or milliseconds since 1970 (as a number, or as a
     text of only digits) goes as an ISO text, so the app reads it the same way as any other date. Anything else is null. */
  const CREATED_KEYS = ["created_at", "createdAt", "created", "date_created", "first_seen"];
  function epochText(v) {
    const ms = v >= 1e9 && v < 4.2e9 ? v * 1000 : (v >= 1e12 && v < 4.2e12 ? v : null);   // seconds, or milliseconds; the years 2001 to 2100
    return ms === null ? null : new Date(ms).toISOString();
  }
  function createdOf(o) {
    for (const k of CREATED_KEYS) {
      const v = o[k];
      if (typeof v === "string") {
        const t = v.trim();
        const when = /^\d{9,13}$/.test(t) ? epochText(Number(t)) : (t && t.length <= 40 && /\d/.test(t) ? t : null);
        if (when !== null) return when;
      } else if (typeof v === "number" && Number.isFinite(v)) {
        const when = epochText(v);
        if (when !== null) return when;
      }
    }
    return null;
  }
  /* What SM sent for `_usage_count`, as kinds and key names only (never a value), so the log can show it without showing anything personal. */
  const kindOf = (v) => (v === null ? "trống" : Array.isArray(v) ? "danh sách" : typeof v === "number" ? "số" : typeof v === "string" ? "chuỗi" : typeof v === "boolean" ? "đúng hoặc sai" : "nhóm");
  function shapeOf(v) {
    if (v === undefined) return "không có trường _usage_count";
    if (v && typeof v === "object" && !Array.isArray(v)) {
      const keys = Object.keys(v);
      return `nhóm có ${keys.length ? keys.slice(0, 8).map((k) => `${k.slice(0, 30)} (${kindOf(v[k])})`).join(", ") : "0 khoá"}`;
    }
    return kindOf(v);
  }

  function compact(o) {
    const idDigits = digits(o.id);
    const id = idDigits.length >= 1 && idDigits.length <= 6 ? o.id : (o.iid != null ? o.iid : o.id);   // SM ids look like O004719
    return {
      id: String(id == null ? "" : id), name: String(o.name || ""), vi: pickVi(o), type: String(o.type || ""),
      status: String(o.status || ""), avail: availOf(o), qty: o.quantity == null ? null : o.quantity,
      loc: String(o.location || o.root_slot || ""), sector: String(o.sector || ""), image_url: String(o.image_url || ""),
      uses: usesOf(o), created: createdOf(o),
    };
  }
  function sayUsage(say, rows, sample) {       // after a sync: what could be read, said plainly, and never a 0 for "unknown"
    const total = rows.length, used = rows.filter((r) => r.uses !== null), dated = rows.filter((r) => r.created !== null).length;
    if (used.length) {
      const top = used.reduce((m, r) => Math.max(m, r.uses), 0);
      say(used.length === total
        ? `Số lần dùng: đọc được ở cả ${total} món và đã gửi kèm (nhiều nhất ${top} lần).`
        : `Số lần dùng: đọc được ở ${used.length} trên ${total} món và đã gửi kèm (nhiều nhất ${top} lần). ${total - used.length} món còn lại để trống, không ghi là 0.`, "ok");
    } else {
      say(`Số lần dùng: SM không gửi số nào đọc được cho ${total} món, nên Box builder chưa có số này. Kho vẫn đồng bộ bình thường.`, "warn");
      say(`Dạng SM gửi: ${shapeOf(sample)}. Bạn chép hai dòng này gửi cho người phát triển để chỉnh script đọc đúng.`, "warn");
    }
    if (!dated) say("Ngày tạo: SM không gửi ngày tạo nào đọc được nên để trống. Gửi một mẫu món cho người phát triển nếu bạn cần ngày này.", "warn");
    else say(dated === total ? `Ngày tạo: đọc được ở cả ${total} món.` : `Ngày tạo: đọc được ở ${dated} trên ${total} món, ${total - dated} món còn lại để trống.`, "ok");
  }

  /* ------------------------------------------------------------------ talking to Box builder (Tampermonkey's request, not fetch) */
  const WAKING = "Máy chủ đang thức dậy, chưa gửi gì sang SM. Đợi khoảng 1 phút rồi bấm lại.";
  const looksWaking = (r) => r.json === null && ([502, 503, 524].includes(r.status) || /^\s*</.test(r.text));   // a proxy's page or an HTML body, never the app's own JSON

  function appOnce(method, path, body) {       // one request, no cookies, no redirects
    const base = String(store.get("bb_app", DEFAULT_APP) || "").trim().replace(/\/+$/, "");
    if (!/^https?:\/\//i.test(base)) return Promise.reject(new Plain("Chưa nhập địa chỉ Box builder (bắt đầu bằng https:// hoặc http://)."));
    const down = (text) => { const e = new Plain(text); e.net = true; return e; };
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method, url: base + path, timeout: APP_TIMEOUT, anonymous: true, redirect: "error",
        headers: { "Content-Type": "application/json", "X-Sync-Token": String(store.get("bb_token", "") || "") },
        data: body == null ? undefined : JSON.stringify(body),
        onload: (r) => {
          let json = null;
          try { json = JSON.parse(r.responseText); } catch { /* not json */ }
          resolve({ status: r.status, json, text: r.responseText || "" });
        },
        onerror: () => reject(down(`Không kết nối được tới ${base}. Kiểm tra địa chỉ và mạng (máy chủ miễn phí có thể đang ngủ, thử lại sau 1 phút).`)),
        ontimeout: () => reject(down("Box builder không trả lời kịp. Nếu dùng máy chủ miễn phí, nó cần khoảng 1 phút để thức dậy: bấm lại.")),
      });
    });
  }
  /* Reads are tried again 2, 4 and 8 seconds apart (4 tries) while the server wakes up. A write is tried once. A proxy's 502, 503,
     524 or any HTML page is "the server is waking", never "SYNC_TOKEN is not set": only the app's own JSON 503 means that. */
  async function app(method, path, body) {
    const tries = method === "GET" ? READ_RETRY_MS.length + 1 : 1;
    let last = null;
    for (let i = 0; i < tries; i++) {
      if (i) {
        if (liveSay) liveSay(`Box builder chưa trả lời, thử lại sau ${READ_RETRY_MS[i - 1] / 1000} giây (lần ${i + 1}/${tries})…`, "progress");
        await sleep(READ_RETRY_MS[i - 1]);
      }
      try {
        const r = await appOnce(method, path, body);
        if (!looksWaking(r)) return r;
        last = new Plain(WAKING); last.stop = true; last.waking = true;
      } catch (e) {
        if (!(e instanceof Plain) || !e.net) throw e;
        last = e;
      }
    }
    throw last;
  }
  let awakeAt = 0;
  async function wake(force) {                 // the first call to a sleeping server only wakes it; nothing is read from SM before it answers
    if (!force && Date.now() - awakeAt < 60000) return null;
    const r = await app("GET", "/api/config");
    if (r.status === 200 && r.json) { awakeAt = Date.now(); return r.json; }
    throw new Plain(explainApp(r));
  }
  /* What Box builder says about itself (GET /api/preflight): awake, database answering, boxes ready, how old its data is, and which
     script version it expects. The panel keeps the last answer in `lastPre`. An older Box builder without the call answers 404. */
  let lastPre = null;
  const minutes = (n) => (typeof n === "number" && Number.isFinite(n) ? `${Math.round(n)} phút trước` : "chưa có");
  async function preflight(say) {
    const r = await app("GET", "/api/preflight");
    if (r.status === 404) { lastPre = { at: Date.now(), old: true }; return null; }
    if (r.status !== 200 || !r.json) throw new Plain(explainApp(r));
    const j = r.json;
    if (j.ok === false) {
      lastPre = { at: Date.now(), down: true };
      const e = new Plain("Box builder báo chưa sẵn sàng (cơ sở dữ liệu chưa trả lời). Thử lại sau ít phút."); e.stop = true; throw e;
    }
    lastPre = { at: Date.now(), json: j };
    say(`Box builder sẵn sàng. Kho cập nhật ${minutes(j.catalog_age_min)}, danh sách hộp SM cập nhật ${minutes(j.sm_snapshot_age_min)}.`, "ok");
    return j;
  }
  function preText() {                         // the panel's one line about Box builder: { text, warn }
    const p = lastPre;
    if (!p) return { text: "Trạng thái Box builder: chưa kiểm tra.", warn: false };
    const when = new Date(p.at).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" });
    if (p.error) return { text: `Trạng thái Box builder (lúc ${when}): ${p.error}`, warn: true };
    if (p.old) return { text: `Trạng thái Box builder (lúc ${when}): phiên bản này chưa có phần kiểm tra trước, cập nhật Box builder để xem.`, warn: true };
    if (p.down) return { text: `Trạng thái Box builder (lúc ${when}): chưa sẵn sàng, cơ sở dữ liệu chưa trả lời.`, warn: true };
    const j = p.json, want = j.expected_script_version ? String(j.expected_script_version).slice(0, 20) : "";
    const ready = typeof j.ready_boxes === "number" ? `${j.ready_boxes} hộp sẵn sàng đưa lên SM` : "chưa rõ số hộp sẵn sàng";
    const stale = want && want !== VERSION;
    return {
      text: `Box builder (lúc ${when}): ${ready}. Danh sách hộp SM cập nhật ${minutes(j.sm_snapshot_age_min)}, kho cập nhật ${minutes(j.catalog_age_min)}. `
        + `Bản script: bạn dùng ${VERSION}${want ? `, Box builder mong ${want}` : ""}.${stale ? " Dán bản mới vào Tampermonkey." : ""}`,
      warn: !!stale,
    };
  }
  let readyAt = 0;
  async function gate(say) {                   // before the first Box builder call of a run: wake it, then ask how it is
    if (Date.now() - readyAt < 60000) return;
    await wake(true);
    await preflight(say);
    readyAt = Date.now();
  }
  function explainCheck(r) {                   // the sync-code step
    if (r.status === 200) return null;
    if (r.status === 401) return "Mã đồng bộ sai. Lấy đúng giá trị SYNC_TOKEN (trong Render, mục Environment) rồi nhập lại.";
    if (r.status === 503) return "Máy chủ Box builder chưa đặt SYNC_TOKEN nên chưa nhận đồng bộ.";
    if (r.status === 404) return "NOCHECK";
    return `Box builder trả lỗi ${r.status}.`;
  }
  function explainApp(r) {                     // an answer that is not 200, for the calls added in 0.2.0
    const why = detailText(r.json);
    if (r.status === 401 && /truy cập/i.test(why)) return "Box builder đang đòi mã truy cập chung nên script chưa đọc được danh sách hộp. Báo người quản trị Box builder.";
    if (r.status === 404) return "Phiên bản Box builder này còn cũ, chưa có chức năng này. Cập nhật Box builder rồi thử lại.";
    return explainCheck(r) || `Box builder trả lỗi ${r.status}.`;
  }

  /* ------------------------------------------------------------------ SM boxes (reads only) */
  const BOX_LIMIT = 500;                       // boxes per request
  const BOX_CAP = 2000;                        // the newest boxes are read, older ones are left out
  const ENDED = ["deactivated"];               // SM's only ended status; SM's default box list leaves such boxes out
  const lc = (v) => String(v == null ? "" : v).trim().toLowerCase();
  const isEnded = (b) => ENDED.includes(lc(b.status)) || ENDED.includes(lc(b.status_label));
  const normTitle = (t) => String(t == null ? "" : t).trim().replace(/\s+/g, " ").toLowerCase();

  function compactBox(b) {                     // only these fields leave the SM tab: never a person, a holder or a history
    const lines = Array.isArray(b.contents) ? b.contents : [];
    const q = (v) => (Number.isFinite(Number(v)) && v !== null && v !== "" ? Number(v) : 1);
    return {
      box_id: String(b.box_id != null ? b.box_id : (b.id != null ? b.id : "")),
      title: String(b.title == null ? "" : b.title), status: String(b.status == null ? "" : b.status),
      status_label: String(b.status_label == null ? "" : b.status_label), created_at: String(b.created_at == null ? "" : b.created_at),
      contents: lines.filter((c) => c && typeof c === "object").map((c) => ({ object_id: String(c.object_id == null ? "" : c.object_id), quantity: q(c.quantity) })),
    };
  }

  async function readSmBoxes(say) {
    const boxes = [];
    let page = 1, total = null, capped = false;
    for (let guard = 0; guard < 30; guard++) {
      const d = await smJson(`/api/boxes?limit=${BOX_LIMIT}` + (page > 1 ? `&page=${page}` : ""));
      const list = Array.isArray(d) ? d : (d && Array.isArray(d.boxes) ? d.boxes : null);
      if (!list) throw new Plain("SM trả danh sách hộp theo dạng lạ nên không đọc được.");
      if (d && typeof d.total === "number") total = d.total;
      for (const b of list) if (b && typeof b === "object") boxes.push(compactBox(b));
      say(`Đã đọc ${boxes.length} hộp từ SM…`, "progress");
      if (!(d && d.has_next === true) || !list.length) break;
      if (boxes.length >= BOX_CAP) { capped = true; break; }
      page += 1;
    }
    if (boxes.length > BOX_CAP) { boxes.length = BOX_CAP; capped = true; }
    return { boxes, total, capped };
  }

  async function syncSmBoxes(say) {            // read SM's boxes, then tell Box builder which objects they hold
    say("Đang đọc hộp trong SM…");
    const read = await readSmBoxes(say);
    const n = read.boxes.length;
    say(read.capped ? `Đã đọc ${n} hộp mới nhất từ SM. Hộp cũ hơn không đọc.` : `Đã đọc ${n} hộp từ SM.`, read.capped ? "warn" : "ok");
    if (n && !read.boxes.some((b) => b.contents.length)) say("SM không trả danh sách món trong hộp, nên Box builder không biết hộp nào giữ món nào.", "warn");
    say(`Đang gửi ${n} hộp cho Box builder…`);
    const body = { boxes: read.boxes };
    if (!n && read.total === 0) body.allow_empty = true;        // SM said it has none; an empty read for any other reason is refused by the app
    const r = await app("POST", "/api/sm/boxes/sync", body);
    if (r.status === 200 && r.json) {
      const j = r.json;
      say(`Box builder nhận ${j.received} hộp: ${j.active} đang dùng (giữ ${j.objects_held} món), ${j.ignored_ended} đã trả nên bỏ qua.`, "ok");
      if (j.skipped_invalid) say(`${j.skipped_invalid} hộp thiếu mã hoặc sai dạng nên bị bỏ qua.`, "warn");
      return read;
    }
    if (r.status === 422 && detailText(r.json)) throw new Plain(detailText(r.json));
    throw new Plain(explainApp(r));
  }

  /* ------------------------------------------------------------------ boxes ready to go to SM */
  async function readPending() {
    const r = await app("GET", "/api/taskboxes?pending=true");
    if (r.status !== 200 || !r.json || !Array.isArray(r.json.boxes)) throw new Plain(explainApp(r));
    const boxes = [];
    let bad = 0;
    for (const b of r.json.boxes) {
      const id = Number(b && b.builder && b.builder.box_id);
      const lines = (b && Array.isArray(b.contents) ? b.contents : []).filter((c) => c && c.object_id != null)
        .map((c) => ({ object_id: String(c.object_id), quantity: Number(c.quantity) }));
      const title = String((b && b.title) || "").trim();
      if (!Number.isInteger(id) || id < 1 || !title || !lines.length || lines.some((c) => !Number.isInteger(c.quantity) || c.quantity < 1)) { bad += 1; continue; }
      boxes.push({ id, title, anchor: b.anchor_object_id ? String(b.anchor_object_id) : "", contents: lines });   // "" when there is no anchor, as the team's earlier script sent it
    }
    return { boxes, bad, disabled: !!r.json.disabled };
  }
  const sameBox = (a, b) => JSON.stringify([a.title, a.anchor, a.contents]) === JSON.stringify([b.title, b.anchor, b.contents]);

  function classify(ready, smBoxes) {          // which ready boxes to create, and why the others are left alone
    const taken = new Set(smBoxes.filter((b) => !isEnded(b)).map((b) => normTitle(b.title)));
    const create = [], skip = new Map(), seen = new Set();
    for (const b of ready) {
      const t = normTitle(b.title);
      if (taken.has(t)) skip.set(b.id, "đã có trong SM, bỏ qua");
      else if (seen.has(t)) skip.set(b.id, "trùng tên với một hộp khác trong danh sách, bỏ qua");
      else create.push(b);
      seen.add(t);
    }
    return { create, skip };
  }

  async function view(say) {                   // step one: reads only, creates nothing
    await gate(say);
    say("1/3 Đang lấy hộp sẵn sàng từ Box builder…");
    const first = await readPending();
    if (first.disabled) { say("Hàng đợi đưa hộp lên SM đang tắt trong Box builder. Bật lại ở mục Tự động hoá.", "warn"); return { ready: [], create: [], skip: new Map() }; }
    if (!first.boxes.length) { say("Chưa có hộp nào sẵn sàng đưa lên SM. Duyệt hộp trong Box builder trước. Chưa đọc hay tạo gì trong SM.", "warn"); return { ready: [], create: [], skip: new Map() }; }
    say("2/3 Đang đọc hộp trong SM…");
    const sm = await syncSmBoxes(say);
    say("3/3 Đang đối chiếu tên hộp…");
    const now = await readPending();             // the app may drop a box once it knows what SM holds
    const still = new Set(now.boxes.map((b) => b.id));
    for (const b of first.boxes) if (!still.has(b.id)) say(`Hộp "${b.title}": không còn sẵn sàng sau khi đọc SM (món đã nằm trong hộp SM hoặc hộp đã đổi), bỏ qua.`, "warn");
    const { create, skip } = classify(now.boxes, sm.boxes);
    for (const b of now.boxes) if (skip.has(b.id)) say(`Hộp "${b.title}": ${skip.get(b.id)}.`, "warn");
    if (now.bad) say(`${now.bad} hộp thiếu tên hoặc món nên không hiện trong danh sách.`, "warn");
    if (!WRITES_ENABLED) {                       // read-only build: show what would be made, hand back no plan, so no confirm button shows
      say(create.length ? `Sẽ có ${create.length} hộp cần tạo, bỏ qua ${skip.size} hộp.` : `Không có hộp nào cần tạo (bỏ qua ${skip.size} hộp).`, "ok");
      say(READ_ONLY_PLAN, "warn");
      return { ready: now.boxes, create: [], skip };
    }
    say(create.length
      ? `Sẽ tạo ${create.length} hộp, bỏ qua ${skip.size} hộp. Chưa tạo gì. Bấm "Xác nhận tạo ${create.length} hộp" để tạo.`
      : `Không có hộp nào cần tạo (bỏ qua ${skip.size} hộp). Chưa tạo gì.`, create.length ? "ok" : "warn");
    return { ready: now.boxes, create, skip };
  }

  async function createBoxes(say, todo) {      // step two: runs only from the confirm button
    if (!WRITES_ENABLED) throw new Plain(READ_ONLY_CREATE);
    await wake();
    say("Đang kiểm tra lại trước khi tạo…");
    const p = await readPending();
    const sm = await readSmBoxes(say);           // reads only: nothing is posted to Box builder here
    const { create, skip } = classify(p.boxes, sm.boxes);
    const now = new Map(create.map((b) => [b.id, b]));
    const out = { made: 0, failed: 0, skipped: 0 };
    const go = [];
    for (const b of todo) {
      const f = now.get(b.id);
      if (f && sameBox(f, b)) go.push(b);
      else {
        out.skipped += 1;
        const why = skip.get(b.id) || (p.boxes.some((x) => x.id === b.id) ? "đã đổi từ lúc bạn xem, bỏ qua. Bấm Xem và tạo trong SM để xem lại" : "không còn sẵn sàng (đã đưa lên SM hoặc đã đổi), bỏ qua");
        say(`Hộp "${b.title}": ${why}.`, "warn");
      }
    }
    for (let i = 0; i < go.length; i++) {
      const b = go[i];
      say(`Đang tạo hộp ${i + 1}/${go.length}: ${b.title}…`, "progress");
      const payload = { title: b.title, anchor_object_id: b.anchor, note: `Created by Box builder #${b.id}`, contents: b.contents, borrow_now: false };
      let smId;
      try { smId = await smCreate(payload); }
      catch (e) {
        if (!(e instanceof Plain)) throw e;
        out.failed += 1;
        say(`Hộp "${b.title}": SM không tạo được. ${e.message}`, "bad");
        if (e.stop) { const rest = go.length - i - 1; if (rest) { out.skipped += rest; say(`Dừng lại, ${rest} hộp còn lại chưa tạo.`, "warn"); } break; }
        continue;
      }
      if (smId == null) {
        out.failed += 1;
        say(`Hộp "${b.title}": SM nhận yêu cầu nhưng không trả mã hộp. Kiểm tra trong SM trước khi tạo lại.`, "warn");
        continue;
      }
      let told;
      try { told = await app("POST", `/api/boxes/${b.id}/pushed`, { sm_box_id: smId }); }
      catch (e) { told = { status: 0, json: null, note: e instanceof Plain ? e.message : "" }; }
      if (told.status === 200) { out.made += 1; say(`Hộp "${b.title}": đã tạo trong SM (${smId}) và báo cho Box builder.`, "ok"); }
      else {
        out.made += 1;
        say(`Hộp "${b.title}": đã tạo trong SM (${smId}) nhưng chưa báo được cho Box builder. ${detailText(told.json) || told.note || `Box builder trả lỗi ${told.status}.`} Đừng tạo lại: lần Xem sau, hộp sẽ hiện là đã có trong SM.`, "warn");
      }
    }
    say(`Xong: tạo ${out.made} hộp, lỗi ${out.failed}, bỏ qua ${out.skipped}.`, out.failed ? "warn" : "ok");
    return out;
  }

  /* ------------------------------------------------------------------ the two actions */
  async function check(say) {
    say(`Script Box builder v${VERSION} đang chạy trên ${location.host}.`, "ok");
    say("1/3 Đang hỏi SM…");
    const first = await smJson("/api/objects?page=1");
    const sample = listOf(first)[0] || {};
    const total = first.total_items != null ? first.total_items : "?";
    const pages = first.total_pages != null ? first.total_pages : "?";
    const hasVi = !!pickVi(sample);
    say(`SM: đã đăng nhập. Kho SM có ${total} món (${pages} trang).`, "ok");
    say(hasVi ? "SM trả tên tiếng Việt cho món." : "SM không trả tên tiếng Việt. Box builder sẽ dùng tên trong file CSV của nó.", hasVi ? "ok" : "warn");
    say("2/3 Đang gọi Box builder…");
    await wake(true);
    say("Box builder: có trả lời.", "ok");
    say("3/3 Đang kiểm tra mã đồng bộ…");
    const r = await app("GET", "/api/sync/check");
    const problem = explainCheck(r);
    if (problem && problem !== "NOCHECK") throw new Plain(problem);
    let viFallback = false;
    if (problem === "NOCHECK") {
      say("Phiên bản Box builder này còn cũ (chưa có phần kiểm tra). Vẫn đồng bộ được nếu SM có tên tiếng Việt.", "warn");
    } else {
      viFallback = !!(r.json && r.json.vi_fallback);
      const c = (r.json && r.json.catalog) || {};
      say(`Mã đúng. Kho trong Box builder hiện là ${c.source === "live" ? "bản đồng bộ từ SM" : "file CSV"} (${c.usable} món dùng được).`, "ok");
    }
    return { total, hasVi, viFallback };
  }

  async function sync(say, opts) {             // opts.pace: wait a moment between pages (the automatic run only)
    const info = await check(say);
    const sectors = String(store.get("bb_sectors", "Hanoi-GPI") || "").split(",").map((x) => x.trim()).filter(Boolean);
    say("Đang đọc kho từ SM…");
    const rows = [];
    let page = 1, pages = 1, sample;             // sample: the first raw `_usage_count` SM sent, kept only to show its shape if none can be read
    do {
      const d = await smJson(`/api/objects?page=${page}`);
      pages = Math.min(PAGE_CAP, Number(d.total_pages) || 1);
      for (const o of listOf(d)) {
        const row = compact(o);
        if (!sectors.length || sectors.includes(row.sector)) {
          rows.push(row);
          if (sample === undefined && o._usage_count != null) sample = o._usage_count;
        }
      }
      say(`Đã đọc trang ${page}/${pages} (${rows.length} món thuộc khu ${sectors.join(", ") || "tất cả"}).`, "progress");
      page += 1;
      if (page <= pages && opts && opts.pace) await sleep(AUTO_PAGE_PAUSE_MS);
    } while (page <= pages);
    if (rows.length < 50) throw new Plain(`Chỉ đọc được ${rows.length} món thuộc khu ${sectors.join(", ")}. Kiểm tra ô Khu (đúng chính tả, ví dụ Hanoi-GPI).`);
    const withVi = rows.filter((r) => r.vi).length;
    if (!info.viFallback && withVi < rows.length / 2) {
      throw new Plain("SM không trả tên tiếng Việt và phiên bản Box builder này chưa tự bù từ file CSV. Gửi đi sẽ làm kho gần như trống. Hãy cập nhật Box builder rồi thử lại.");
    }
    say(`Đang gửi ${rows.length} món cho Box builder…`);
    const r = await app("POST", "/api/catalog/sync", { objects: rows });
    if (r.status === 200 && r.json) {
      const sk = r.json.skipped || {};
      say(`Xong. Box builder có ${r.json.usable} món dùng được trong ${r.json.received} món nhận về.`, "ok");
      say(`Bỏ qua: thiếu tên tiếng Việt ${sk.no_vietnamese_name || 0}, hết hàng ${sk.out_of_stock || 0}, không hoạt động ${sk.inactive || 0}, khác loại ${sk.other_type || 0}.`, "progress");
      sayUsage(say, rows, sample);
      const gone = r.json.claimed_now_unavailable || [];
      if (gone.length) say(`Chú ý: ${gone.length} món đang nằm trong hộp nhưng SM không còn liệt kê: ${gone.slice(0, 6).join(", ")}.`, "warn");
      store.set("bb_last", new Date().toLocaleString("vi-VN"));
      try { await syncSmBoxes(say); }            // last step: which objects SM's boxes hold
      catch (e) { if (!(e instanceof Plain)) throw e; say(`Kho đã đồng bộ, nhưng chưa đọc được hộp SM. ${e.message}`, "warn"); }
      return;
    }
    if (r.status === 422 && r.json && r.json.detail) throw new Plain(String(r.json.detail));
    const problem = explainCheck(r);
    throw new Plain(problem && problem !== "NOCHECK" ? problem : `Box builder trả lỗi ${r.status}.`);
  }

  /* What the installed copy's own header says about updates (Tampermonkey keeps it in GM_info.scriptMetaStr). A copy updates itself only
     when the header names an @updateURL, or a @downloadURL other than "none". GM_info.scriptWillUpdate is not used: it does not say what the header holds. */
  function updateState() {
    let meta = "";
    try { meta = String((typeof GM_info !== "undefined" && GM_info && GM_info.scriptMetaStr) || ""); } catch { meta = ""; }
    if (!meta) return "Tự cập nhật: không rõ";
    const heads = meta.split(/\r?\n/).map((l) => /^\s*\/\/\s*@(updateURL|downloadURL)\s+(\S+)/i.exec(l)).filter(Boolean);
    const on = heads.some((m) => m[1].toLowerCase() === "updateurl" || m[2].toLowerCase() !== "none");
    return on ? "Tự cập nhật: BẬT" : "Tự cập nhật: TẮT";
  }

  /* ------------------------------------------------------------------ optional auto refresh (reads only)
     While an SM tab is open and the person has switched it on in the panel: SM's boxes are read every 10 minutes and the catalog (with the
     boxes) every 60 minutes, so what Box builder knows about SM stays fresh without a click. It reads SM with GET and posts what a button press
     would post, nothing else. It never starts while another run is going, a failed run is tried again 5 minutes later, and it switches
     itself off when SM says the person is signed out. The switch and the next due times live in the script's own storage, so a second SM
     tab sees that someone just refreshed and waits. One timer for the whole page: the panel hands its pieces to `panelApi`. */
  const autoOn = () => store.get("bb_auto", false) === true;
  const dueAt = (key) => Number(store.get(key, 0)) || 0;
  let panelApi = null, autoGoing = false;
  async function autoTick() {
    if (autoGoing || !autoOn() || !panelApi || panelApi.busy() || !panelApi.configured()) return;       // never while a run is still going
    const now = Date.now();
    const kind = now >= dueAt("bb_auto_catalog") ? "catalog" : (now >= dueAt("bb_auto_boxes") ? "boxes" : null);
    if (!kind) return;
    autoGoing = true;
    try { await panelApi.autoRun(kind); } finally { autoGoing = false; }
  }

  /* ------------------------------------------------------------------ the panel */
  function build() {
    const addStyle = (text) => {
      try { if (typeof GM_addStyle === "function") return GM_addStyle(text); } catch { /* fall through */ }
      const st = document.createElement("style"); st.textContent = text; document.head.append(st);
    };
    addStyle(`
      #bb-btn{position:fixed;right:16px;bottom:16px;z-index:2147483000;padding:9px 14px;border:0;border-radius:99px;background:#18232b;color:#fff;font:600 13px system-ui,sans-serif;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.3)}
      #bb-panel{position:fixed;right:16px;bottom:64px;z-index:2147483000;width:min(420px,calc(100vw - 32px));max-height:80vh;overflow:auto;background:#fff;color:#18232b;border:1px solid #c4cdca;border-radius:12px;padding:14px;font:13px/1.45 system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.3)}
      #bb-panel[hidden],#bb-panel [hidden]{display:none !important}
      #bb-panel h3{margin:0 0 4px;font-size:15px}
      #bb-panel label{display:block;margin:8px 0 2px;font-weight:600}
      #bb-panel input[type=text],#bb-panel input[type=password]{width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid #98a5a0;border-radius:6px}
      #bb-panel .row{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px}
      #bb-panel button{padding:7px 12px;border:1px solid #98a5a0;border-radius:8px;background:#fff;font-weight:600;cursor:pointer}
      #bb-panel button.go{background:#f0691a;border-color:#d95a10}
      #bb-panel button:disabled{opacity:.5;cursor:default}
      #bb-ready{margin-top:12px;padding-top:8px;border-top:1px solid #dbe1de}
      #bb-ready-count{font-weight:600}
      #bb-ready-count.warn{color:#7a4a00;font-weight:400}
      #bb-ready-list{margin:6px 0 0;padding:0 0 0 18px;max-height:140px;overflow:auto}
      #bb-ready-list .skip{color:#7a4a00}
      #bb-log{margin-top:10px;min-height:40px;max-height:240px;overflow:auto;border:1px solid #dbe1de;border-radius:8px;padding:6px 8px;background:#f8faf9}
      #bb-log .ok{color:#0c4a3e}#bb-log .warn{color:#7a4a00}#bb-log .bad{color:#a8231b;font-weight:600}#bb-log .progress{color:#48575f}
      #bb-note{margin-top:8px;color:#48575f;font-size:12px}
      #bb-writes{margin:0 0 2px;font-weight:600;color:#7a4a00}
      #bb-update{margin:0 0 2px;color:#48575f;font-size:12px}
      #bb-pre{margin:6px 0 0;color:#48575f;font-size:12px}
      #bb-pre.warn{color:#7a4a00;font-weight:600}
      #bb-auto-row{display:flex;align-items:center;gap:6px;margin-top:10px}
      #bb-auto-row label{display:inline;margin:0;font-weight:600}
      #bb-auto-note{margin-top:4px;color:#48575f;font-size:12px}`);

    const btn = document.createElement("button");
    btn.id = "bb-btn"; btn.type = "button"; btn.textContent = "Box builder";
    const panel = document.createElement("div");
    panel.id = "bb-panel"; panel.hidden = true; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Box builder và SM");
    const field = (id, label, type, value, ph) => {
      const l = document.createElement("label"); l.htmlFor = id; l.textContent = label;
      const i = document.createElement("input"); i.id = id; i.type = type; i.value = value; i.placeholder = ph || ""; i.autocomplete = "off";
      panel.append(l, i); return i;
    };
    const button = (id, text, cls) => {
      const b = document.createElement("button"); b.id = id; b.type = "button"; b.textContent = text; if (cls) b.className = cls; return b;
    };
    const title = document.createElement("h3"); title.textContent = `Box builder và SM (v${VERSION})`;
    const writes = document.createElement("div"); writes.id = "bb-writes"; writes.textContent = `Ghi vào SM: ${WRITES_ENABLED ? "BẬT" : "TẮT"}`;
    const intro = document.createElement("div"); intro.id = "bb-note";
    intro.textContent = WRITES_ENABLED ? "Đọc kho và hộp từ SM (chỉ đọc) và gửi cho Box builder. Chỉ tạo hộp mới trong SM khi bạn bấm xác nhận."
      : "Đọc kho và hộp từ SM (chỉ đọc) và gửi cho Box builder. Bản này chỉ đọc: chưa tạo được hộp trong SM.";
    const upd = document.createElement("div"); upd.id = "bb-update"; upd.textContent = updateState();
    const pre = document.createElement("div"); pre.id = "bb-pre";
    panel.append(title, writes, upd, intro, pre);
    const fApp = field("bb-app", "Địa chỉ Box builder", "text", store.get("bb_app", DEFAULT_APP) || "", "https://box-builder-guwc.onrender.com");
    const fTok = field("bb-token", "Mã đồng bộ (SYNC_TOKEN)", "password", store.get("bb_token", "") || "");
    const fSec = field("bb-sectors", "Khu (Sector)", "text", store.get("bb_sectors", "Hanoi-GPI") || "Hanoi-GPI", "Hanoi-GPI");
    const row = document.createElement("div"); row.className = "row";
    const bCheck = button("bb-check", "Kiểm tra kết nối");
    const bSync = button("bb-sync", "Đồng bộ kho ngay", "go");
    const bBoxes = button("bb-boxes", "Đọc hộp trong SM");
    row.append(bCheck, bSync, bBoxes);
    const autoRow = document.createElement("div"); autoRow.id = "bb-auto-row";
    const auto = document.createElement("input"); auto.type = "checkbox"; auto.id = "bb-auto";
    const autoLabel = document.createElement("label"); autoLabel.htmlFor = "bb-auto"; autoLabel.textContent = "Tự làm mới khi tab SM đang mở (chỉ đọc)";
    autoRow.append(auto, autoLabel);
    const autoNote = document.createElement("div"); autoNote.id = "bb-auto-note";

    const readyBox = document.createElement("div"); readyBox.id = "bb-ready";
    const readyCount = document.createElement("div"); readyCount.id = "bb-ready-count";
    const readyList = document.createElement("ul"); readyList.id = "bb-ready-list";
    const readyRow = document.createElement("div"); readyRow.className = "row";
    const bView = button("bb-view", WRITES_ENABLED ? "Xem và tạo trong SM" : "Xem hộp sẽ tạo");
    const bConfirm = button("bb-confirm", "Xác nhận tạo 0 hộp", "go"); bConfirm.hidden = true;
    const bRefresh = button("bb-refresh", "Làm mới");
    readyRow.append(bView, bConfirm, bRefresh);
    readyBox.append(readyCount, readyList, readyRow);

    const log = document.createElement("div"); log.id = "bb-log"; log.setAttribute("role", "log"); log.setAttribute("aria-live", "polite");
    const last = document.createElement("div"); last.id = "bb-note";
    const showLast = () => { last.textContent = store.get("bb_last", "") ? `Lần đồng bộ gần nhất: ${store.get("bb_last", "")}.` : "Chưa đồng bộ lần nào."; };
    showLast();
    const foot = document.createElement("div"); foot.id = "bb-note";
    foot.textContent = WRITES_ENABLED ? "Chỉ đọc từ SM. Việc duy nhất ghi vào SM là tạo hộp mới sau khi bạn bấm Xác nhận. Không sửa, giao, mượn hay xoá gì."
      : "Chỉ đọc từ SM. Bản này không ghi gì vào SM: không tạo, sửa, giao, mượn hay xoá hộp.";
    panel.append(row, autoRow, autoNote, readyBox, log, last, foot);
    document.body.append(btn, panel);

    let busy = false, ready = [], plan = [], skip = new Map(), seq = 0;
    const all = [bCheck, bSync, bBoxes, bView, bRefresh];
    const say = (text, kind) => {
      const d = document.createElement("div");
      if (kind) d.className = kind;
      d.textContent = text;
      if (kind === "progress" && log.lastChild && log.lastChild.className === "progress") log.lastChild.replaceWith(d); else log.append(d);
      while (log.childNodes.length > 300) log.firstChild.remove();       // a panel left open for days does not grow without end
      log.scrollTop = log.scrollHeight;
    };
    const quiet = () => { };
    const showPre = () => { const t = preText(); pre.textContent = t.text; pre.className = t.warn ? "warn" : ""; };
    const hhmm = (t) => (t > Date.now() ? new Date(t).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" }) : "ngay khi rảnh");
    const showAuto = () => {
      auto.checked = autoOn();
      autoNote.textContent = store.get("bb_auto_note", "") || (autoOn()
        ? `Tự làm mới đang bật. Lần kế tiếp: hộp SM ${hhmm(dueAt("bb_auto_boxes"))}, kho ${hhmm(dueAt("bb_auto_catalog"))}. Chỉ đọc SM, không tạo hay sửa gì.`
        : "Tự làm mới đang tắt. Khi bật: đọc hộp SM mỗi 10 phút, kho mỗi 60 phút.");
    };
    const configured = () => /^https?:\/\//i.test(String(store.get("bb_app", DEFAULT_APP) || "").trim()) && !!store.get("bb_token", "");
    const save = () => { store.set("bb_app", fApp.value.trim()); store.set("bb_token", fTok.value); store.set("bb_sectors", fSec.value.trim()); };
    const showConfirm = () => {
      bConfirm.hidden = plan.length === 0; bConfirm.disabled = busy;
      bConfirm.textContent = `Xác nhận tạo ${plan.length} hộp`;
    };
    const render = (note) => {
      readyCount.className = note ? "warn" : "";
      readyCount.textContent = note || (ready.length ? `${ready.length} hộp sẵn sàng đưa lên SM` : "Chưa có hộp nào sẵn sàng đưa lên SM.");
      readyList.replaceChildren(...ready.map((b) => {
        const li = document.createElement("li");
        li.textContent = `${b.title}: ${b.contents.length} món`;
        if (skip.has(b.id)) { const m = document.createElement("span"); m.className = "skip"; m.textContent = ` (${skip.get(b.id)})`; li.append(m); }
        return li;
      }));
    };
    const refreshReady = async () => {          // the count of boxes waiting; quiet when the address or code is not typed yet
      const mine = ++seq;
      if (!/^https?:\/\//i.test(fApp.value.trim()) || !fTok.value) { ready = []; render("Nhập địa chỉ Box builder và mã đồng bộ để xem hộp sẵn sàng."); return; }
      save();
      try {
        const p = await readPending();
        if (mine !== seq) return;
        ready = p.boxes;
        if (p.disabled) { ready = []; render("Hàng đợi đưa hộp lên SM đang tắt trong Box builder. Bật lại ở mục Tự động hoá."); } else render();
      } catch (e) {
        if (mine !== seq) return;
        ready = []; render(e instanceof Plain ? e.message : "Chưa đọc được danh sách hộp sẵn sàng.");
      }
      try { await preflight(quiet); }
      catch (e) { lastPre = { at: Date.now(), error: e instanceof Plain ? e.message : "chưa đọc được trạng thái." }; }
      if (mine === seq) showPre();
    };
    const run = (fn, opts) => async () => {
      if (busy) return;
      busy = true;                              // set before the first await, so a second press in the same instant finds it set
      if (!(opts && opts.nosave)) save();       // a button press keeps what is typed; an automatic run does not (see autoRun)
      if (!(opts && opts.keep)) log.replaceChildren();
      all.forEach((b) => { b.disabled = true; }); bConfirm.disabled = true;
      let ok = false;
      liveSay = say;
      try { await fn(say); ok = true; say("Hoàn tất.", "ok"); }
      catch (e) { say(e instanceof Plain ? e.message : `Có lỗi không rõ: ${e && e.message ? e.message : e}`, "bad"); }
      finally { liveSay = null; busy = false; all.forEach((b) => { b.disabled = false; }); showLast(); showConfirm(); showAuto(); showPre(); }
      if (ok && !(opts && opts.own)) await refreshReady();
    };
    btn.addEventListener("click", () => { panel.hidden = !panel.hidden; if (!panel.hidden && !busy) refreshReady(); });
    bCheck.addEventListener("click", run(async (s) => { await check(s); }));
    bSync.addEventListener("click", run(async (s) => { await sync(s); }));
    bBoxes.addEventListener("click", run(async (s) => { await wake(); await syncSmBoxes(s); }));
    bRefresh.addEventListener("click", () => { if (!busy) refreshReady(); });
    bView.addEventListener("click", run(async (s) => {
      plan = []; skip = new Map(); showConfirm();
      const v = await view(s);
      ready = v.ready; skip = v.skip; plan = v.create; seq += 1; render(); showConfirm();
    }, { own: true }));
    let todo = [];
    const make = run(async (s) => {
      const mine = todo; todo = [];             // taken off the plan first, so nothing can be created twice
      await createBoxes(s, mine);
      try { const p = await readPending(); ready = p.boxes; skip = new Map(); render(); }   // what is still waiting after this run
      catch (e) { ready = []; render(e instanceof Plain ? e.message : "Chưa đọc được danh sách hộp sẵn sàng."); }
    }, { keep: true, own: true });
    bConfirm.addEventListener("click", () => {
      if (busy || !plan.length) return;         // a second press, or a press with nothing planned, creates nothing
      todo = plan; plan = []; showConfirm();
      make();
    });

    /* Auto refresh: one run, claimed first in storage so a second SM tab waits, then the same reads a button press does. */
    const autoRun = (kind) => run(async (s) => {
      const t0 = Date.now();
      store.set("bb_auto_boxes", t0 + AUTO_RETRY_MS);                        // a failed run leaves this as the time of the next try
      if (kind === "catalog") store.set("bb_auto_catalog", t0 + AUTO_RETRY_MS);
      s(`Tự làm mới: ${kind === "catalog" ? "đọc kho và hộp" : "đọc hộp"} từ SM…`, "progress");
      try {
        if (kind === "catalog") await sync(s, { pace: true }); else { await gate(s); await syncSmBoxes(s); }
        const t1 = Date.now();
        store.set("bb_auto_boxes", t1 + AUTO_BOXES_MS);
        if (kind === "catalog") store.set("bb_auto_catalog", t1 + AUTO_CATALOG_MS);
        s("Tự làm mới: xong.", "ok");
      } catch (e) {
        if (e instanceof Plain && e.signedOut) {                              // SM says nobody is signed in: stop until the person turns it on again
          store.set("bb_auto", false);
          store.set("bb_auto_note", "Tự làm mới đã tắt vì SM chưa đăng nhập. Đăng nhập SM rồi bật lại.");
        }
        throw e;
      }
    }, { keep: true, own: true, nosave: true })();     // nosave: this tab's boxes may be stale or half typed; app() and sync() read the storage
    auto.addEventListener("change", () => {
      if (auto.checked) save();                                              // what was typed in the boxes counts, even if no button was pressed yet
      if (auto.checked && !configured()) { store.set("bb_auto_note", "Nhập địa chỉ Box builder và mã đồng bộ trước khi bật tự làm mới."); auto.checked = false; showAuto(); return; }
      if (auto.checked) {
        store.set("bb_auto", true); store.set("bb_auto_note", ""); store.set("bb_auto_boxes", 0); store.set("bb_auto_catalog", 0);   // due now
        setTimeout(autoTick, 1500);
      } else { store.set("bb_auto", false); store.set("bb_auto_note", ""); }
      showAuto();
    });
    panelApi = { busy: () => busy, configured, autoRun };
    showAuto(); showPre();
  }

  setInterval(autoTick, AUTO_TICK_MS);
  setTimeout(autoTick, 5000);
  const ensure = () => { if (document.body && !document.getElementById("bb-btn")) build(); };
  console.log(`[Box builder] script v${VERSION} đang chạy trên ${location.host}`);
  if (document.body) ensure(); else window.addEventListener("DOMContentLoaded", ensure);
  setInterval(ensure, 3000);                  // some pages redraw the whole body: put the button back
})();

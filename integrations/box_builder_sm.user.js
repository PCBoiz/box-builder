// ==UserScript==
// @name         Box builder: đồng bộ kho với SM
// @namespace    box-builder
// @version      0.2.0
// @description  Đọc kho đồ và danh sách hộp từ Scenario Manager (chỉ đọc) và gửi cho Box builder. Chỉ tạo hộp mới trong SM sau khi bạn bấm xác nhận. Không sửa hay xoá gì.
// @match        https://sm.config.inc/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_addStyle
// @connect      box-builder-guwc.onrender.com
// @connect      localhost
// @connect      127.0.0.1
// @run-at       document-idle
// ==/UserScript==

/* What this does, in one paragraph: it runs in your own signed-in SM tab. It reads SM's object list and box list with GET
   requests only, and posts a compact copy to Box builder (the address and sync code are typed once into the panel and kept in
   the browser's own storage). The one thing it ever writes to SM is a new box (POST /api/boxes, same site as the page you are
   on), and only after you press the confirm button in the panel. It never moves, hands over, assigns, borrows, edits or deletes
   anything in SM, and it never sends the sync code to SM. The app's own checks decide what is kept. */
(function () {
  "use strict";
  if (window.__bbSm) return;
  window.__bbSm = true;

  const VERSION = "0.2.0";
  const DEFAULT_APP = "";               // the app fills this in when it serves the script, so nobody has to type the address
  const PAGE_CAP = 200;                 // never read more than this many pages of 30 from SM
  const APP_TIMEOUT = 90000;            // a sleeping free server can take about a minute to answer the first call
  const store = {
    get: (k, d) => { try { return GM_getValue(k, d); } catch { return d; } },
    set: (k, v) => { try { GM_setValue(k, v); } catch { /* storage unavailable: the panel still works for this visit */ } },
  };

  /* ------------------------------------------------------------------ talking to SM (reads only) */
  class Plain extends Error { }          // an error whose message is already written for the person

  async function smJson(path) {
    let res;
    try { res = await fetch(path, { credentials: "same-origin", method: "GET" }); }
    catch { throw new Plain("Không gọi được SM. Kiểm tra mạng rồi thử lại."); }
    const type = res.headers.get("content-type") || "";
    if (res.status === 401 || res.status === 403 || res.redirected && /\/login/.test(res.url) || !/json/.test(type)) {
      throw new Plain("Chưa đăng nhập SM trong tab này. Hãy đăng nhập SM rồi bấm lại.");
    }
    if (!res.ok) throw new Plain(`SM trả lỗi ${res.status}. Thử lại sau ít phút.`);
    return res.json();
  }
  const listOf = (d) => (Array.isArray(d) ? d : (d.objects || d.items || d.data || d.results || []));

  /* The only write this script makes to SM: one new box, same site as the page, called only from the confirm button's flow.
     borrow_now is always false here, so SM never lends anything because of this script. */
  async function smCreate(payload) {
    let res;
    try {
      res = await fetch("/api/boxes", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "Accept": "application/json" }, body: JSON.stringify(payload),
      });
    } catch { throw new Plain("Không gọi được SM. Kiểm tra mạng rồi thử lại."); }
    const type = res.headers.get("content-type") || "";
    const signedOut = () => { const e = new Plain("Chưa đăng nhập SM trong tab này. Hãy đăng nhập SM rồi bấm lại."); e.stop = true; return e; };
    if (res.status === 401 || res.redirected && /\/login/.test(res.url)) throw signedOut();
    let json = null;
    if (/json/.test(type)) { try { json = await res.json(); } catch { /* not json */ } }
    if (!res.ok) {
      if (!json && /html/.test(type) && res.status !== 403) throw signedOut();
      const why = detailText(json);
      throw new Plain(why ? `SM từ chối: ${why}` : (res.status === 403 ? "SM từ chối (403). Tài khoản này có thể không được tạo hộp." : `SM trả lỗi ${res.status}.`));
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
  function compact(o) {
    const idDigits = digits(o.id);
    const id = idDigits.length >= 1 && idDigits.length <= 6 ? o.id : (o.iid != null ? o.iid : o.id);   // SM ids look like O004719
    return {
      id: String(id == null ? "" : id), name: String(o.name || ""), vi: pickVi(o), type: String(o.type || ""),
      status: String(o.status || ""), avail: availOf(o), qty: o.quantity == null ? null : o.quantity,
      loc: String(o.location || o.root_slot || ""), sector: String(o.sector || ""), image_url: String(o.image_url || ""),
    };
  }

  /* ------------------------------------------------------------------ talking to Box builder (Tampermonkey's request, not fetch) */
  function app(method, path, body) {
    const base = String(store.get("bb_app", DEFAULT_APP) || "").trim().replace(/\/+$/, "");
    if (!/^https?:\/\//i.test(base)) return Promise.reject(new Plain("Chưa nhập địa chỉ Box builder (bắt đầu bằng https:// hoặc http://)."));
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method, url: base + path, timeout: APP_TIMEOUT,
        headers: { "Content-Type": "application/json", "X-Sync-Token": String(store.get("bb_token", "") || "") },
        data: body == null ? undefined : JSON.stringify(body),
        onload: (r) => {
          let json = null;
          try { json = JSON.parse(r.responseText); } catch { /* not json */ }
          resolve({ status: r.status, json, text: r.responseText || "" });
        },
        onerror: () => reject(new Plain(`Không kết nối được tới ${base}. Kiểm tra địa chỉ và mạng (máy chủ miễn phí có thể đang ngủ, thử lại sau 1 phút).`)),
        ontimeout: () => reject(new Plain("Box builder không trả lời kịp. Nếu dùng máy chủ miễn phí, nó cần khoảng 1 phút để thức dậy: bấm lại.")),
      });
    });
  }
  async function wake() {                      // the first request wakes a sleeping server
    for (let i = 0; i < 2; i++) {
      try { const r = await app("GET", "/api/config"); if (r.status === 200) return r.json; } catch (e) { if (i === 1) throw e; }
    }
    throw new Plain("Box builder chưa sẵn sàng. Thử lại sau 1 phút.");
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
  const ENDED = ["deactivated", "returned", "cancelled", "canceled", "discarded"];   // the same list the app uses
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
    say(create.length
      ? `Sẽ tạo ${create.length} hộp, bỏ qua ${skip.size} hộp. Chưa tạo gì. Bấm "Xác nhận tạo ${create.length} hộp" để tạo.`
      : `Không có hộp nào cần tạo (bỏ qua ${skip.size} hộp). Chưa tạo gì.`, create.length ? "ok" : "warn");
    return { ready: now.boxes, create, skip };
  }

  async function createBoxes(say, todo) {      // step two: runs only from the confirm button
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
    await wake();
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

  async function sync(say) {
    const info = await check(say);
    const sectors = String(store.get("bb_sectors", "Hanoi-GPI") || "").split(",").map((x) => x.trim()).filter(Boolean);
    say("Đang đọc kho từ SM…");
    const rows = [];
    let page = 1, pages = 1;
    do {
      const d = await smJson(`/api/objects?page=${page}`);
      pages = Math.min(PAGE_CAP, Number(d.total_pages) || 1);
      for (const o of listOf(d)) {
        const row = compact(o);
        if (!sectors.length || sectors.includes(row.sector)) rows.push(row);
      }
      say(`Đã đọc trang ${page}/${pages} (${rows.length} món thuộc khu ${sectors.join(", ") || "tất cả"}).`, "progress");
      page += 1;
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
      #bb-note{margin-top:8px;color:#48575f;font-size:12px}`);

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
    const intro = document.createElement("div"); intro.id = "bb-note"; intro.textContent = "Đọc kho và hộp từ SM (chỉ đọc) và gửi cho Box builder. Chỉ tạo hộp mới trong SM khi bạn bấm xác nhận.";
    panel.append(title, intro);
    const fApp = field("bb-app", "Địa chỉ Box builder", "text", store.get("bb_app", DEFAULT_APP) || "", "https://box-builder-guwc.onrender.com");
    const fTok = field("bb-token", "Mã đồng bộ (SYNC_TOKEN)", "password", store.get("bb_token", "") || "");
    const fSec = field("bb-sectors", "Khu (Sector)", "text", store.get("bb_sectors", "Hanoi-GPI") || "Hanoi-GPI", "Hanoi-GPI");
    const row = document.createElement("div"); row.className = "row";
    const bCheck = button("bb-check", "Kiểm tra kết nối");
    const bSync = button("bb-sync", "Đồng bộ kho ngay", "go");
    const bBoxes = button("bb-boxes", "Đọc hộp trong SM");
    row.append(bCheck, bSync, bBoxes);

    const readyBox = document.createElement("div"); readyBox.id = "bb-ready";
    const readyCount = document.createElement("div"); readyCount.id = "bb-ready-count";
    const readyList = document.createElement("ul"); readyList.id = "bb-ready-list";
    const readyRow = document.createElement("div"); readyRow.className = "row";
    const bView = button("bb-view", "Xem và tạo trong SM");
    const bConfirm = button("bb-confirm", "Xác nhận tạo 0 hộp", "go"); bConfirm.hidden = true;
    const bRefresh = button("bb-refresh", "Làm mới");
    readyRow.append(bView, bConfirm, bRefresh);
    readyBox.append(readyCount, readyList, readyRow);

    const log = document.createElement("div"); log.id = "bb-log"; log.setAttribute("role", "log"); log.setAttribute("aria-live", "polite");
    const last = document.createElement("div"); last.id = "bb-note";
    const showLast = () => { last.textContent = store.get("bb_last", "") ? `Lần đồng bộ gần nhất: ${store.get("bb_last", "")}.` : "Chưa đồng bộ lần nào."; };
    showLast();
    const foot = document.createElement("div"); foot.id = "bb-note";
    foot.textContent = "Chỉ đọc từ SM. Việc duy nhất ghi vào SM là tạo hộp mới sau khi bạn bấm Xác nhận. Không sửa, giao, mượn hay xoá gì.";
    panel.append(row, readyBox, log, last, foot);
    document.body.append(btn, panel);

    let busy = false, ready = [], plan = [], skip = new Map(), seq = 0;
    const all = [bCheck, bSync, bBoxes, bView, bRefresh];
    const say = (text, kind) => {
      const d = document.createElement("div");
      if (kind) d.className = kind;
      d.textContent = text;
      if (kind === "progress" && log.lastChild && log.lastChild.className === "progress") log.lastChild.replaceWith(d); else log.append(d);
      log.scrollTop = log.scrollHeight;
    };
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
    };
    const run = (fn, opts) => async () => {
      if (busy) return;
      busy = true;                              // set before the first await, so a second press in the same instant finds it set
      save(); if (!(opts && opts.keep)) log.replaceChildren();
      all.forEach((b) => { b.disabled = true; }); bConfirm.disabled = true;
      let ok = false;
      try { await fn(say); ok = true; say("Hoàn tất.", "ok"); }
      catch (e) { say(e instanceof Plain ? e.message : `Có lỗi không rõ: ${e && e.message ? e.message : e}`, "bad"); }
      finally { busy = false; all.forEach((b) => { b.disabled = false; }); showLast(); showConfirm(); }
      if (ok && !(opts && opts.own)) await refreshReady();
    };
    btn.addEventListener("click", () => { panel.hidden = !panel.hidden; if (!panel.hidden && !busy) refreshReady(); });
    bCheck.addEventListener("click", run(async (s) => { await check(s); }));
    bSync.addEventListener("click", run(async (s) => { await sync(s); }));
    bBoxes.addEventListener("click", run(async (s) => { await syncSmBoxes(s); }));
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
  }

  const ensure = () => { if (document.body && !document.getElementById("bb-btn")) build(); };
  console.log(`[Box builder] script v${VERSION} đang chạy trên ${location.host}`);
  if (document.body) ensure(); else window.addEventListener("DOMContentLoaded", ensure);
  setInterval(ensure, 3000);                  // some pages redraw the whole body: put the button back
})();

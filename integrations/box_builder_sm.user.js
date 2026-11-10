// ==UserScript==
// @name         Box builder: đồng bộ kho với SM
// @namespace    box-builder
// @version      0.1.0
// @description  Đọc kho đồ từ Scenario Manager (chỉ đọc) và gửi cho Box builder. Không tạo, sửa hay xoá gì trong SM.
// @match        https://sm.config.inc/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @connect      box-builder-guwc.onrender.com
// @connect      localhost
// @connect      127.0.0.1
// @run-at       document-idle
// ==/UserScript==

/* What this does, in one paragraph: it runs in your own signed-in SM tab, reads SM's object list with GET requests only,
   and posts a compact copy to Box builder (the address and sync code are typed once into the panel and kept in the
   browser's own storage). Nothing is written to SM. The app's own checks decide what is kept (sector, active, stock). */
(function () {
  "use strict";
  if (window.__bbSm) return;
  window.__bbSm = true;

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
    const base = String(store.get("bb_app", "") || "").trim().replace(/\/+$/, "");
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

  /* ------------------------------------------------------------------ the two actions */
  async function check(say) {
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
      return;
    }
    if (r.status === 422 && r.json && r.json.detail) throw new Plain(String(r.json.detail));
    const problem = explainCheck(r);
    throw new Plain(problem && problem !== "NOCHECK" ? problem : `Box builder trả lỗi ${r.status}.`);
  }

  /* ------------------------------------------------------------------ the panel */
  function build() {
    const css = document.createElement("style");
    css.textContent = `
      #bb-btn{position:fixed;right:16px;bottom:16px;z-index:2147483000;padding:9px 14px;border:0;border-radius:99px;background:#18232b;color:#fff;font:600 13px system-ui,sans-serif;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.3)}
      #bb-panel{position:fixed;right:16px;bottom:64px;z-index:2147483000;width:min(420px,calc(100vw - 32px));max-height:80vh;overflow:auto;background:#fff;color:#18232b;border:1px solid #c4cdca;border-radius:12px;padding:14px;font:13px/1.45 system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.3)}
      #bb-panel[hidden]{display:none}
      #bb-panel h3{margin:0 0 4px;font-size:15px}
      #bb-panel label{display:block;margin:8px 0 2px;font-weight:600}
      #bb-panel input[type=text],#bb-panel input[type=password]{width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid #98a5a0;border-radius:6px}
      #bb-panel .row{display:flex;gap:8px;margin-top:10px}
      #bb-panel button{padding:7px 12px;border:1px solid #98a5a0;border-radius:8px;background:#fff;font-weight:600;cursor:pointer}
      #bb-panel button.go{background:#f0691a;border-color:#d95a10}
      #bb-panel button:disabled{opacity:.5;cursor:default}
      #bb-log{margin-top:10px;min-height:40px;max-height:240px;overflow:auto;border:1px solid #dbe1de;border-radius:8px;padding:6px 8px;background:#f8faf9}
      #bb-log .ok{color:#0c4a3e}#bb-log .warn{color:#7a4a00}#bb-log .bad{color:#a8231b;font-weight:600}#bb-log .progress{color:#48575f}
      #bb-note{margin-top:8px;color:#48575f;font-size:12px}`;
    document.head.append(css);

    const btn = document.createElement("button");
    btn.id = "bb-btn"; btn.type = "button"; btn.textContent = "Box builder";
    const panel = document.createElement("div");
    panel.id = "bb-panel"; panel.hidden = true; panel.setAttribute("role", "dialog"); panel.setAttribute("aria-label", "Box builder và SM");
    const field = (id, label, type, value, ph) => {
      const l = document.createElement("label"); l.htmlFor = id; l.textContent = label;
      const i = document.createElement("input"); i.id = id; i.type = type; i.value = value; i.placeholder = ph || ""; i.autocomplete = "off";
      panel.append(l, i); return i;
    };
    const title = document.createElement("h3"); title.textContent = "Box builder và SM";
    const intro = document.createElement("div"); intro.id = "bb-note"; intro.textContent = "Đọc kho từ SM (chỉ đọc) và gửi cho Box builder.";
    panel.append(title, intro);
    const fApp = field("bb-app", "Địa chỉ Box builder", "text", store.get("bb_app", "") || "", "https://box-builder-guwc.onrender.com");
    const fTok = field("bb-token", "Mã đồng bộ (SYNC_TOKEN)", "password", store.get("bb_token", "") || "");
    const fSec = field("bb-sectors", "Khu (Sector)", "text", store.get("bb_sectors", "Hanoi-GPI") || "Hanoi-GPI", "Hanoi-GPI");
    const row = document.createElement("div"); row.className = "row";
    const bCheck = document.createElement("button"); bCheck.id = "bb-check"; bCheck.type = "button"; bCheck.textContent = "Kiểm tra kết nối";
    const bSync = document.createElement("button"); bSync.id = "bb-sync"; bSync.type = "button"; bSync.className = "go"; bSync.textContent = "Đồng bộ kho ngay";
    row.append(bCheck, bSync);
    const log = document.createElement("div"); log.id = "bb-log"; log.setAttribute("role", "log"); log.setAttribute("aria-live", "polite");
    const last = document.createElement("div"); last.id = "bb-note";
    const showLast = () => { last.textContent = store.get("bb_last", "") ? `Lần đồng bộ gần nhất: ${store.get("bb_last", "")}.` : "Chưa đồng bộ lần nào."; };
    showLast();
    const foot = document.createElement("div"); foot.id = "bb-note";
    foot.textContent = "Chỉ đọc từ SM. Không tạo, sửa hay xoá gì trong SM.";
    panel.append(row, log, last, foot);
    document.body.append(btn, panel);

    const say = (text, kind) => {
      const d = document.createElement("div");
      if (kind) d.className = kind;
      d.textContent = text;
      if (kind === "progress" && log.lastChild && log.lastChild.className === "progress") log.lastChild.replaceWith(d); else log.append(d);
      log.scrollTop = log.scrollHeight;
    };
    const save = () => { store.set("bb_app", fApp.value.trim()); store.set("bb_token", fTok.value); store.set("bb_sectors", fSec.value.trim()); };
    const run = (fn) => async () => {
      save(); log.replaceChildren(); bCheck.disabled = bSync.disabled = true;
      try { await fn(say); say("Hoàn tất.", "ok"); }
      catch (e) { say(e instanceof Plain ? e.message : `Có lỗi không rõ: ${e && e.message ? e.message : e}`, "bad"); }
      finally { bCheck.disabled = bSync.disabled = false; showLast(); }
    };
    btn.addEventListener("click", () => { panel.hidden = !panel.hidden; });
    bCheck.addEventListener("click", run(async (s) => { await check(s); }));
    bSync.addEventListener("click", run(async (s) => { await sync(s); }));
  }

  if (document.body) build(); else window.addEventListener("DOMContentLoaded", build);
})();

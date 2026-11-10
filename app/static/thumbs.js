"use strict";
/* Thumbnails for catalog objects. Pictures come from Scenario Manager, so the URL may be empty, dead or need a login.
   BBThumbs.thumb(obj, { size: "sm" | "md" }) returns a <span>. obj = { id, name, vi, img, detail }. Styles: thumbs.css. */
window.BBThumbs = (function () {
  const NS = "http://www.w3.org/2000/svg";
  const PX = { sm: 36, md: 64 };
  const failed = new Set();          // URLs that failed once: never requested again in this page session
  let skew = 0;                      // server clock minus this browser's clock, in seconds: img_exp is a server time, a fast laptop clock must not blank the pictures
  const stats = { expired: 0 };      // thumbnails left as a placeholder because their link ran out (the page shows one tip for them)
  let dlg = null, opener = null;

  function h(tag, attrs, ...kids) {  // same shape as the helper in app.js
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const kid of kids) if (kid != null) el.append(kid);
    return el;
  }
  const label = (o) => o.vi || o.name || String(o.id ?? "");
  const isWeb = (u) => { try { return /^https?:$/.test(new URL(u, location.href).protocol); } catch { return false; } };

  function placeholder() {           // small box icon, built with createElementNS
    const s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", "0 0 16 16");
    s.setAttribute("class", "bbt-ph");
    s.setAttribute("aria-hidden", "true");
    for (const d of ["M2 4.8 8 2l6 2.8v6.4L8 14l-6-2.8z", "M2 4.8l6 2.8 6-2.8M8 7.6V14"]) {
      const p = document.createElementNS(NS, "path");
      p.setAttribute("d", d);
      s.append(p);
    }
    return s;
  }

  function thumb(obj, opts) {
    obj = obj || {};
    const size = opts && opts.size === "md" ? "md" : "sm";
    const id = String(obj.id ?? ""), name = label(obj);
    const src = typeof obj.img === "string" ? obj.img.trim() : "";
    const el = h("span", { "data-id": id });
    let live = false;
    const none = (why) => {          // no usable picture: same size, not a button, id as tooltip
      live = false;
      el.className = "bbt bbt-" + size + (why === "expired" ? " bbt-expired" : "");
      el.title = why === "expired" ? id + " · Ảnh hết hạn. Đồng bộ lại kho trong SM để xem ảnh." : id;
      for (const a of ["role", "tabindex", "aria-label", "aria-haspopup"]) el.removeAttribute(a);
      el.replaceChildren(placeholder());
    };
    // The server works out when a signed SM link stops working (obj.img_exp, epoch seconds). Past that, the picture is not even asked for.
    if (src && typeof obj.img_exp === "number" && Date.now() / 1000 + skew >= obj.img_exp) { stats.expired += 1; none("expired"); return el; }
    if (!src || failed.has(src)) { none(); return el; }
    const bad = () => { failed.add(src); none(); };
    const img = h("img", {
      alt: name, width: PX[size], height: PX[size], loading: "lazy", decoding: "async", referrerpolicy: "no-referrer",
      draggable: "false", onerror: bad, onload: () => img.naturalWidth || bad(), src,
    });
    live = true;
    el.className = `bbt bbt-${size} is-img`;
    el.title = id + " · Bấm để xem ảnh lớn";
    for (const [k, v] of Object.entries({ role: "button", tabindex: "0", "aria-haspopup": "dialog", "aria-label": "Xem ảnh lớn: " + name })) el.setAttribute(k, v);
    const go = () => live && show(obj, src, el);
    el.addEventListener("click", go);
    el.addEventListener("keydown", (e) => {   // like a native button: Enter on keydown, Space on keyup
      if (e.key === "Enter" && !e.repeat) { e.preventDefault(); go(); }
      else if (e.key === " ") e.preventDefault();
    });
    el.addEventListener("keyup", (e) => { if (e.key === " ") go(); });
    el.append(img);
    return el;
  }

  function build() {                 // one dialog for the whole page, created on first use and reused
    if (dlg && dlg.isConnected) return;
    dlg = h("dialog", { class: "bbt-dlg", "aria-labelledby": "bbt-vi" },
      h("div", { class: "bbt-body" },
        h("div", { class: "bbt-fig" }, h("img", { class: "bbt-big", alt: "", decoding: "async", referrerpolicy: "no-referrer" })),
        h("div", {}, h("h2", { class: "bbt-vi", id: "bbt-vi" }), h("p", { class: "bbt-en" }), h("p", { class: "bbt-id" })),
        h("div", { class: "bbt-act" },
          h("a", { class: "btn small", target: "_blank", rel: "noopener noreferrer" }, "Mở trong SM"),
          h("button", { class: "btn small", type: "button", autofocus: true, onclick: () => dlg.close() }, "Đóng"))));
    dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });   // padding is on .bbt-body, so this is the backdrop
    dlg.addEventListener("close", () => {
      dlg.querySelector(".bbt-big").removeAttribute("src");
      // the table may have been re-rendered while open, so fall back to a thumbnail with the same id
      const back = opener && (opener.isConnected ? opener : document.querySelector(`.bbt[role="button"][data-id="${CSS.escape(opener.dataset.id)}"]`));
      opener = null;
      if (back) back.focus();
    });
    document.body.append(dlg);
  }

  function show(obj, src, from) {
    build();
    opener = from;
    const q = (c) => dlg.querySelector(c);
    const name = label(obj), en = obj.vi && obj.name && obj.name !== obj.vi ? obj.name : "";
    const big = q(".bbt-big");
    big.alt = name;
    big.src = src;
    q(".bbt-vi").textContent = name;
    q(".bbt-en").textContent = en;
    q(".bbt-en").hidden = !en;
    q(".bbt-id").textContent = "ID " + String(obj.id ?? "");
    const a = q("a"), url = typeof obj.detail === "string" ? obj.detail.trim() : "";
    a.hidden = !(url && isWeb(url));    // only http(s) links; an empty string must not resolve to this page
    if (a.hidden) a.removeAttribute("href"); else a.href = url;
    dlg.showModal();
  }

  // The server sends its own time with every answer that carries img_exp; call this before drawing thumbnails from that answer.
  const setServerNow = (n) => { if (typeof n === "number" && isFinite(n)) skew = n - Date.now() / 1000; };
  return { thumb, setServerNow, expiredCount: () => stats.expired, resetExpired: () => { stats.expired = 0; } };
})();

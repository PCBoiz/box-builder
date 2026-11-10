/* Login page. Sends the company ID (and the admin code, if the person typed one) and goes where the server says. */
(() => {
  const $ = (s) => document.querySelector(s);
  const form = $("#login-form"), err = $("#login-err"), go = $("#login-go");

  const nextPath = () => {                       // only a path on this site, never another address
    const v = new URLSearchParams(location.search).get("next") || "";
    if (!v.startsWith("/") || v.startsWith("//") || /[\u0000-\u0020\u007f\\]/.test(v)) return "";          // a tab or a space inside "/ /evil.example" is read by a browser as "//evil.example"
    try {                                                                                                  // "/.//evil.example" resolves to "//evil.example": check the resolved path too
      const u = new URL(v, location.origin), p = u.pathname + u.search + u.hash;
      return u.origin === location.origin && p.startsWith("/") && !p.startsWith("//") ? p : "";
    } catch { return ""; }
  };
  const show = (msg) => { err.textContent = msg; err.hidden = !msg; };

  async function json(path, opts) {
    const res = await fetch(path, { credentials: "same-origin", ...opts });
    let body = null;
    try { body = await res.json(); } catch { /* empty body */ }
    return { res, body };
  }

  async function start() {
    try {
      const { body } = await json("/api/auth/status");
      if (body && !body.enforced) { form.hidden = true; $("#login-off").hidden = false; return; }
      if (body && body.person) { location.replace(nextPath() || (body.person.admin ? "/" : "/dung")); return; }
    } catch { /* offline: the form still works once the network is back */ }
    $("#login-id").focus();
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    show("");
    const id = $("#login-id").value.trim();
    if (!id) { show("Nhập ID công ty."); $("#login-id").focus(); return; }
    go.disabled = true;
    try {
      const { res, body } = await json("/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, code: $("#login-code").value }),
      });
      if (res.ok && body && body.person) { location.replace(nextPath() || body.go || "/dung"); return; }
      show(body && body.detail && res.status < 500 && res.status !== 403 ? body.detail : "Chưa vào được. Thử lại.");      // a refusal or a wait is the server's own sentence; anything else is only "not done"
      if (res.status === 401) $("#login-code").value = "";
    } catch {
      show("Không kết nối được. Kiểm tra mạng.");
    } finally { go.disabled = false; }
  });

  start();
})();

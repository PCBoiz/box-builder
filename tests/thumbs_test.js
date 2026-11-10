// Thumbnails: an expired SM picture link is never requested and shows a clear placeholder; other links behave as before.
// Run: node tests/thumbs_test.js   (a tiny fake page stands in for the browser)
const fs = require('fs'); const vm = require('vm');
const fail = (m) => { throw new Error(m); };
const eq = (a, b, m) => { if (JSON.stringify(a) !== JSON.stringify(b)) fail(m + ': got ' + JSON.stringify(a) + ' expected ' + JSON.stringify(b)); };

class El {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.kids = []; this.listeners = {}; this._cls = ''; this.title = ''; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  append(...k) { this.kids.push(...k); }
  replaceChildren(...k) { this.kids = k; }
  set className(v) { this._cls = v; } get className() { return this._cls; }
  find(tag) { return this.kids.find((k) => k.tag === tag); }
  fire(t, ev) { (this.listeners[t] || []).forEach((f) => f(ev || {})); }
  get naturalWidth() { return 10; }
}
const doc = { createElement: (t) => new El(t), createElementNS: (_n, t) => new El(t) };
const win = {};
const ctx = { window: win, document: doc, console, URL, Date, Math, Set, Object, String, Number, CSS: { escape: (s) => s } };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('app/static/thumbs.js', 'utf8'), ctx);
const T = win.BBThumbs;
if (!T || typeof T.thumb !== 'function' || typeof T.expiredCount !== 'function' || typeof T.resetExpired !== 'function') fail('API');

const now = () => Date.now() / 1000;
const has = (el) => !!el.find('img');
const base = { id: '004702', name: 'Frying pan', vi: 'Chảo' };

// no picture: a placeholder, nothing counted as expired
T.resetExpired();
let el = T.thumb({ ...base, img: '' });
if (has(el) || el.className.indexOf('is-img') >= 0 || el.className.indexOf('bbt-expired') >= 0) fail('no picture');
eq(T.expiredCount(), 0, 'no picture is not "expired"');

// a link with no expiry information (not signed): shown
el = T.thumb({ ...base, img: 'https://sm.config.inc/media/a.png', img_exp: null });
if (!has(el) || el.className.indexOf('is-img') < 0) fail('plain link must show its picture');
el = T.thumb({ ...base, img: 'https://sm.config.inc/media/a.png' });                                       // the field missing altogether
if (!has(el)) fail('a missing img_exp keeps the picture');
eq(T.expiredCount(), 0, 'plain links never count');

// a signed link that still has time: shown
el = T.thumb({ ...base, img: 'https://sm.config.inc/media/b.png?Signature=x', img_exp: now() + 1800 });
if (!has(el)) fail('a link with time left must show its picture');
eq(T.expiredCount(), 0, 'fresh link');

// an expired link: not even an <img> element exists (so the browser asks for nothing), a placeholder and a clear tooltip
el = T.thumb({ ...base, img: 'https://sm.config.inc/media/c.png?Signature=x', img_exp: now() - 5 });
if (has(el)) fail('an expired link must not create an <img>');
if (el.className.indexOf('bbt-expired') < 0 || el.className.indexOf('is-img') >= 0) fail('expired class ' + el.className);
if (!/hết hạn/.test(el.title) || !/Đồng bộ lại kho trong SM/.test(el.title) || el.title.indexOf('004702') < 0) fail('tooltip ' + el.title);
if (el.getAttribute('role') !== undefined || el.getAttribute('tabindex') !== undefined) fail('a placeholder is not a button');
if (!el.find('svg')) fail('placeholder icon');
eq(T.expiredCount(), 1, 'one expired thumbnail counted');
T.thumb({ ...base, id: '1', img: 'https://sm.config.inc/m.png?Signature=x', img_exp: now() - 1 });
T.thumb({ ...base, id: '2', img: 'https://sm.config.inc/m.png?Signature=x', img_exp: 0 });
eq(T.expiredCount(), 3, 'every expired thumbnail is counted');
T.resetExpired();
eq(T.expiredCount(), 0, 'reset');

// the boundary: exactly now counts as expired; one second ahead does not
el = T.thumb({ ...base, img: 'https://sm.config.inc/m.png?Signature=x', img_exp: now() - 0.001 });
if (has(el)) fail('at the boundary the link is expired');
el = T.thumb({ ...base, img: 'https://sm.config.inc/m.png?Signature=x', img_exp: now() + 2 });
if (!has(el)) fail('two seconds before the end the link is still used');

// junk in img_exp never hides a picture (only a number can)
T.resetExpired();
for (const odd of ['soon', null, undefined, true, {}, [], NaN]) {
  el = T.thumb({ ...base, img: 'https://sm.config.inc/m.png', img_exp: odd });
  if (!has(el)) fail('img_exp ' + String(odd) + ' must not hide the picture');
}
eq(T.expiredCount(), 0, 'junk is not expired');

// an expired link without any img (nothing to ask for) is the plain placeholder
el = T.thumb({ ...base, img: '', img_exp: now() - 100 });
if (el.className.indexOf('bbt-expired') >= 0) fail('no link, so nothing expired');

// a picture that failed before is a plain placeholder, as ever
const bad = 'https://sm.config.inc/media/dead.png';
el = T.thumb({ ...base, img: bad });
el.find('img').listeners.error[0]();                                                                       // the browser reports it failed
if (has(el)) fail('failed picture falls back');
el = T.thumb({ ...base, img: bad });
if (has(el)) fail('a failed URL is not asked for again');
// the server clock decides, not the laptop's: img_exp is a server time (found by the review)
if (typeof T.setServerNow !== 'function') fail('setServerNow missing');
T.resetExpired();
// a laptop whose clock is two hours FAST: the server says it is now S, the link has 30 minutes left
let serverNow = now() - 7200;
T.setServerNow(serverNow);
el = T.thumb({ ...base, img: 'https://sm.config.inc/m.png?Signature=x', img_exp: serverNow + 1800 });
if (!has(el)) fail('a fast laptop clock must not hide a picture that still has time on the server clock');
eq(T.expiredCount(), 0, 'fast clock: nothing expired');
el = T.thumb({ ...base, img: 'https://sm.config.inc/m.png?Signature=x', img_exp: serverNow - 5 });
if (has(el)) fail('but a link that ran out on the server clock is still expired');
eq(T.expiredCount(), 1, 'fast clock: the really expired one counts');
// a laptop clock two hours SLOW: a link that ran out on the server clock must not be asked for
T.resetExpired();
serverNow = now() + 7200;
T.setServerNow(serverNow);
el = T.thumb({ ...base, img: 'https://sm.config.inc/m.png?Signature=x', img_exp: serverNow - 10 });
if (has(el)) fail('a slow laptop clock must not keep asking for a link that ran out');
// nonsense never changes the offset
for (const odd of ['now', null, undefined, NaN, Infinity, {}]) T.setServerNow(odd);
el = T.thumb({ ...base, img: 'https://sm.config.inc/m.png?Signature=x', img_exp: serverNow - 10 });
if (has(el)) fail('a bad server time must leave the last good offset in place');
T.setServerNow(now());                                                                                     // back to "clocks agree"
T.resetExpired();
console.log('thumbs tests OK');

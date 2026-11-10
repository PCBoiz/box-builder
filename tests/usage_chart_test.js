// Usage chart: the numbers, the sentences, the structure of the page it builds, and the colour contrast of its stylesheet.
// Run: node tests/usage_chart_test.js   (a tiny fake page stands in for the browser; tests/usage_chart_e2e.py uses a real one)
const fs = require('fs'); const path = require('path'); const vm = require('vm');
const fail = (m) => { throw new Error(m); };
const eq = (a, b, m) => { if (JSON.stringify(a) !== JSON.stringify(b)) fail(m + ': got ' + JSON.stringify(a) + ' expected ' + JSON.stringify(b)); };
const ok = (c, m) => { if (!c) fail(m); };
const DASHES = String.fromCharCode(0x2014, 0x2013);   // em dash and en dash, written as codes so this file has none
const SRC = path.join(__dirname, '..', 'app', 'static', 'usage_chart.js');
const CSS = path.join(__dirname, '..', 'app', 'static', 'usage_chart.css');

// ---- a fake page: just what the component calls. Styles may only be set through setProperty (so bar widths never become inline style text).
class Text { constructor(t) { this.nodeType = 3; this.data = t; } get textContent() { return this.data; } }
class El {
  constructor(tag, doc) {
    this.nodeType = 1; this.tag = tag; this.ownerDocument = doc; this.attrs = {}; this.kids = []; this.vars = {};
    this.style = new Proxy({ setProperty: (k, v) => { this.vars[k] = v; } }, { set() { throw new Error('style must go through setProperty'); } });
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(k) { this.kids.push(k); return k; }
  set textContent(v) { this.kids = v === '' || v == null ? [] : [new Text(String(v))]; }
  get textContent() { return this.kids.map((k) => k.textContent).join(''); }
  get cls() { return (this.attrs['class'] || '').split(/\s+/).filter(Boolean); }
  has(c) { return this.cls.includes(c); }
}
const doc = { createElement: (t) => new El(t, doc), createTextNode: (t) => new Text(t) };
const walk = (el, f) => { if (el.nodeType !== 1) return; f(el); el.kids.forEach((k) => walk(k, f)); };
const all = (el, pred) => { const out = []; walk(el, (e) => { if (pred(e)) out.push(e); }); return out; };
const byTag = (el, t) => all(el, (e) => e.tag === t);
const byCls = (el, c) => all(el, (e) => e.has(c));
const box = () => new El('div', doc);

// ---- load the file twice: as a module (no window) and as a page script (with a window)
const U = require(SRC);
if (!U || typeof U.render !== 'function' || typeof U.model !== 'function') fail('module.exports API');
const win = {};
vm.runInContext(fs.readFileSync(SRC, 'utf8'), vm.createContext({ window: win, document: doc, console, Number, Math, Array, Object, String, isFinite }));
if (!win.BBUsageChart || typeof win.BBUsageChart.render !== 'function') fail('window.BBUsageChart.render missing');
ok(Number(1533).toLocaleString('vi-VN') === '1.533', 'this Node has no vi-VN data (needs full ICU); the dot separator test cannot run');

// ---- sample data: the shape of GET /api/usage
const top = (rows) => rows.map(([id, name, vi, uses, weight]) => ({ id, name, vi, uses, age_days: 210, weight }));
const SAMPLE = {
  enabled: true, known: 1490, total: 1533, soft_start: 500, hard_limit: 0, half_life: 300, age_effect: 50, min_pool: 100, over_start: 46, excluded: 0, reduced: 46, free_over_start: 3,
  bands: [{ from: 0, to: 99, count: 1210 }, { from: 100, to: 249, count: 190 }, { from: 250, to: 499, count: 87 }, { from: 500, to: 999, count: 44 }, { from: 1000, to: null, count: 2 }],
  unknown: 43,
  top: top([['000724', 'Stainless steel tongs', 'Kẹp inox', 1587, 0.22], ['001203', 'Cast iron pan', 'Chảo gang', 1083, 0.27], ['000311', 'Wooden spoon', 'Muỗng gỗ', 892, 0.41],
    ['002250', 'Glass jar', '', 825, 0.45], ['000990', 'Kettle', null, 499, 1]]),
};
const mk = (o) => Object.assign({}, SAMPLE, o);
const render = (usage) => { const c = box(); const root = U.render(c, usage); return { c, root }; };

// ---- one source of truth: the sentence and the bands add up
let m = U.model(SAMPLE);
eq(m.lede, 'Có 46 trong 1.533 món đã đạt từ 500 lượt mượn trở lên.', 'sentence');
eq(m.state, 'over', 'state');
eq(m.bands.filter((b) => b.over).reduce((s, b) => s + b.n, 0), SAMPLE.over_start, 'bands from the threshold up add up to over_start');
eq(m.bands.map((b) => b.label), ['Dưới 100', '100 đến 249', '250 đến 499', '500 đến 999', 'Từ 1.000 trở lên'], 'band labels');
eq(m.bands.map((b) => b.over), [false, false, false, true, true], 'which bands are over');
eq(m.divider.index, 3, 'the divider sits before the first band that is over'); eq(m.divider.text, 'Mức bắt đầu giảm: 500 lượt mượn', 'divider text'); eq(m.divider.inside, false, 'not inside a band');
eq(m.unk.n, 43, 'unknown row'); eq(m.unk.label, 'Chưa có số lượt mượn', 'unknown label');
eq(m.bands[0].w, 1, 'the biggest band is the full track'); eq(Math.round(m.bands[3].w * 1210), 44, 'widths are linear in the count');
eq(m.top.map((t) => t.uses), [1587, 1083, 892, 825, 499], 'top is sorted, highest first');
eq(m.top[3].name, 'Glass jar', 'no Vietnamese name: English fallback'); eq(m.top[3].lang, 'en', 'fallback is marked as English'); eq(m.top[0].lang, '', 'Vietnamese name has no lang');
eq(m.top[4].name, 'Kettle', 'null vi'); eq(m.top.map((t) => t.over), [true, true, true, true, false], 'rows over the threshold');
eq(m.scaleMax, 1587, 'scale reaches the top'); eq(Math.round(m.t * 100) / 100, Math.round(500 / 1587 * 10000) / 100, 'threshold line at soft/scale'); eq(m.h, null, 'no hard line when the limit is 0');
eq(m.top.map((t) => t.fx), ['Còn 22% khả năng được chọn', 'Còn 27% khả năng được chọn', 'Còn 41% khả năng được chọn', 'Còn 45% khả năng được chọn', null], 'effect in words, nothing at weight 1');

// the number in the sentence is the configured one, never a hard-coded 500
m = U.model(mk({ soft_start: 750, over_start: 12 }));
eq(m.lede, 'Có 12 trong 1.533 món đã đạt từ 750 lượt mượn trở lên.', 'configured threshold'); ok(!/500/.test(m.lede + m.divider.text), 'no 500 anywhere');
eq(m.divider.inside, true, '750 falls inside the 500 to 999 band'); eq(m.divider.text, 'Mức bắt đầu giảm: 750 lượt mượn, nằm trong dòng trên', 'divider says the line is in the row above');
eq(m.divider.index, 4, 'divider after the band that holds the threshold'); eq(m.bands.map((b) => b.over), [false, false, false, false, true], 'only the band wholly past it is over');
m = U.model(mk({ soft_start: 5000 }));
eq(m.divider.index, 5, 'threshold past every band: divider last'); eq(m.scaleMax, 5000, 'scale reaches the threshold when no object does'); eq(m.t, 100, 'line at the end of the track');
eq(U.model(mk({ soft_start: 100 })).divider.inside, false, 'threshold on a band edge');
m = U.model(mk({ soft_start: 1000000 }));
eq(m.divider.inside, true, 'open last band holds a threshold past it');

// number format: the same as the rest of the app
eq(U.model(mk({ total: 12345 })).lede, 'Có 46 trong 12.345 món đã đạt từ 500 lượt mượn trở lên.', 'thousands use a dot');

// ---- what happens to picking: sentence two
eq(U.model(SAMPLE).effect, '46 món được chọn ít hơn. Mức loại đang là 0, nên không món nào bị loại.', 'limit 0');
eq(U.model(mk({ hard_limit: 1000, excluded: 2 })).effect, '46 món được chọn ít hơn. 2 món bị loại vì có từ 1.000 lượt mượn trở lên.', 'limit set and used');
eq(U.model(mk({ hard_limit: 1000, excluded: 0 })).effect, '46 món được chọn ít hơn. Chưa món nào bị loại. Món có từ 1.000 lượt mượn trở lên sẽ bị loại.', 'limit set, none yet');
eq(U.model(mk({ reduced: 0 })).effect, 'Chưa có món nào được chọn ít hơn. Mức loại đang là 0, nên không món nào bị loại.', 'none picked less');
eq(U.model(mk({ hard_limit: 0, excluded: 3 })).effect, '46 món được chọn ít hơn. 3 món bị loại.', 'excluded without a limit (should not happen): the count, no invented limit');
m = U.model(mk({ enabled: false }));
eq(m.effect, 'Công tắc Giảm món đã dùng nhiều đang tắt, nên không món nào được chọn ít hơn hay bị loại.', 'switch off');
ok(m.top.every((t) => t.fx === null), 'switch off: no effect words on any object');

// ---- weights in words
const E = U.effectOf;
eq([0, -1, -0.5].map(E), ['Loại: không được chọn', 'Loại: không được chọn', 'Loại: không được chọn'], 'weight 0 or below: loại');
eq([1, 1.5, 100, Infinity].map(E), [null, null, null, null], 'weight 1 or above: nothing');
eq([null, undefined, '', 'x', true, [], {}, NaN].map(E), [null, null, null, null, null, null, null, null], 'unknown weight: nothing');
eq([0.22, '0.5', 0.004, 0.0049, 0.995, 0.999, 0.5].map(E), ['Còn 22% khả năng được chọn', 'Còn 50% khả năng được chọn', 'Còn dưới 1% khả năng được chọn', 'Còn dưới 1% khả năng được chọn', 'Còn trên 99% khả năng được chọn', 'Còn trên 99% khả năng được chọn', 'Còn 50% khả năng được chọn'], 'percent, rounded, never 0% or 100% while some is left');

// ---- states
m = U.model(mk({ over_start: 0, reduced: 0 }));
eq([m.state, m.lede, m.effect, m.warn], ['none', 'Chưa có món nào đạt từ 500 lượt mượn trở lên.', '', false], 'no object over');
ok(m.bands.length === 5 && m.top.length === 5, 'no object over: the charts stay');
m = U.model(mk({ known: 0, over_start: 0, bands: [], top: [] }));
eq([m.state, m.lede], ['nodata', 'Chưa có số lượt mượn của món nào. Cập nhật công cụ đồng bộ trong SM lên bản 0.3.0, rồi bấm "Đồng bộ kho ngay".'], 'known 0 says there is no data and to update the SM tool');
ok(m.bands.length === 0 && m.top.length === 0 && m.effect === '', 'known 0: nothing else');
eq(U.model(mk({ soft_start: 0 })).state, 'nosoft', 'no threshold'); eq(U.model(mk({ soft_start: null })).lede, 'Chưa đặt mức bắt đầu giảm.', 'no threshold sentence');
eq(U.model(null).state, 'empty', 'null usage'); eq(U.model('x').state, 'empty', 'string usage'); eq(U.model(undefined).state, 'empty', 'undefined usage');

// a marker for the hard limit only when it is inside the scale
m = U.model(mk({ hard_limit: 1000 })); eq(Math.round(m.h * 100) / 100, Math.round(1000 / 1587 * 10000) / 100, 'hard line at hard/scale');
eq(U.model(mk({ hard_limit: 2000 })).h, null, 'hard limit past the scale: no line (the sentence says it)');
eq(U.model(mk({ hard_limit: 1587 })).h, 100, 'hard limit at the top of the scale: line at the end');

// ---- the page it builds
let { c, root } = render(SAMPLE);
eq(c.kids.length, 1, 'the container holds one root'); ok(root.has('bbu'), 'root class');
const lede = byCls(root, 'bbu-lede')[0];
eq(lede.textContent, 'Cảnh báo: Có 46 trong 1.533 món đã đạt từ 500 lượt mượn trở lên.', 'screen readers hear the warning prefix, then the sentence');
eq(lede.kids[0].has('bbu-vh') && lede.kids[0].textContent, 'Cảnh báo: ', 'the prefix is the visually hidden span');
eq(byCls(root, 'bbu-effect')[0].textContent, U.model(SAMPLE).effect, 'sentence two');
const table = byTag(root, 'table')[0], h3 = byTag(root, 'h3'), notes = byCls(root, 'bbu-note');
eq(h3.map((h) => h.textContent), ['Số món theo số lượt mượn', '5 món có nhiều lượt mượn nhất'], 'headings');
eq(table.getAttribute('aria-labelledby'), h3[0].getAttribute('id'), 'the table is named by its visible heading');
eq(table.getAttribute('aria-describedby'), notes[0].getAttribute('id'), 'the table is described by the reading note');
const list = byTag(root, 'ol')[0];
eq(list.getAttribute('aria-labelledby'), h3[1].getAttribute('id'), 'the list is named by its heading'); eq(list.getAttribute('aria-describedby'), notes[1].getAttribute('id'), 'the list is described by its note');
eq(list.getAttribute('role'), 'list', 'role=list keeps the semantics in Safari');
eq(byTag(table, 'th').filter((t) => t.getAttribute('scope') === 'col').map((t) => t.textContent), ['Số lượt mượn', 'Số món'], 'column headers');
eq(byTag(table, 'th').filter((t) => t.getAttribute('scope') === 'row').map((t) => t.textContent), ['Dưới 100', '100 đến 249', '250 đến 499', '500 đến 999', 'Từ 1.000 trở lên', 'Chưa có số lượt mượn'], 'row headers');
eq(byTag(table, 'th').filter((t) => t.getAttribute('scope') === 'rowgroup').map((t) => [t.textContent, t.getAttribute('colspan')]), [['Mức bắt đầu giảm: 500 lượt mượn', '2']], 'the labelled divider');
eq(byCls(table, 'bbu-num').map((n) => n.textContent), ['1.210', '190', '87', '44', '2', '43'], 'every count is printed');
eq(byTag(table, 'tbody').map((t) => t.cls.join(' ')), ['', 'bbu-over', 'bbu-unk'], 'groups: under the threshold, from it, no data');
eq(byTag(byTag(table, 'tbody')[1], 'tr')[0].has('bbu-thr'), true, 'the divider is the first row of the group from the threshold up');
ok(all(root, (e) => e.has('bbu-track')).every((t) => t.getAttribute('aria-hidden') === 'true'), 'every bar track is aria-hidden');
eq(all(root, (e) => e.has('bbu-fill')).filter((f) => !f.vars['--w']).length, 0, 'every bar width is a CSS custom property');
eq(byCls(table, 'bbu-fill').map((f) => f.cls.filter((k) => k.indexOf('is-') === 0)[0]), ['is-under', 'is-under', 'is-under', 'is-over', 'is-over', 'is-unk'], 'bar kinds: solid, hatched, hollow');
eq(byCls(table, 'bbu-fill').map((f) => f.vars['--w']), ['100.00%', '15.70%', '7.19%', '3.64%', '0.17%', '3.55%'], 'bar widths in percent of the longest');
const lis = byTag(list, 'li');
eq(lis.map((l) => byCls(l, 'bbu-id')[0].textContent), ['000724', '001203', '000311', '002250', '000990'], 'ids');
eq(lis.map((l) => byCls(l, 'bbu-name')[0].textContent), ['Kẹp inox', 'Chảo gang', 'Muỗng gỗ', 'Glass jar', 'Kettle'], 'names');
eq(lis.map((l) => byCls(l, 'bbu-val')[0].textContent), ['1.587 lượt mượn', '1.083 lượt mượn', '892 lượt mượn', '825 lượt mượn', '499 lượt mượn'], 'uses with the unit');
eq(lis.map((l) => byCls(l, 'bbu-fx').length), [1, 1, 1, 1, 0], 'no effect line at weight 1');
eq(byCls(lis[3], 'bbu-name')[0].getAttribute('lang'), 'en', 'English fallback name carries lang=en'); eq(byCls(lis[0], 'bbu-name')[0].getAttribute('lang'), undefined, 'no lang on Vietnamese');
eq([list.vars['--t']], ['31.51%'], 'threshold line position'); eq(list.vars['--h'], undefined, 'no hard line position'); eq(list.has('has-hard'), false, 'no hard class');
eq(byCls(root, 'bbu-tag').map((t) => [t.textContent, t.cls.slice(1).join(' ')]), [['Giảm từ 500', 'is-soft at-start']], 'one marker label, starting at its line (the line is at 31.5 percent)');
eq(byCls(lis[0], 'bbu-tag').length, 1, 'the soft label sits in the first row');

// hard limit: second label under the last row, solid line
({ c, root } = render(mk({ hard_limit: 1000, excluded: 2, top: top([['1', 'A', 'Một', 1587, 0], ['2', 'B', 'Hai', 1083, 0], ['3', 'C', 'Ba', 600, 0.3]]) })));
const hl = byTag(root, 'ol')[0], hlis = byTag(hl, 'li');
eq(hl.has('has-hard'), true, 'has-hard class'); eq(hl.vars['--h'], '63.01%', 'hard line position');
eq(byCls(hlis[2], 'bbu-tag').map((t) => t.textContent), ['Loại từ 1.000'], 'the hard label is in the last row'); eq(byCls(hlis[0], 'bbu-tag').map((t) => t.textContent), ['Giảm từ 500'], 'the soft label is in the first row');
eq(byCls(hlis[0], 'bbu-fx')[0].textContent, 'Loại: không được chọn', 'weight 0 in words');
ok(/Đường liền là mức loại\./.test(byCls(root, 'bbu-note')[1].textContent), 'the note names the solid line');
({ root } = render(mk({ top: top([['1', 'A', 'Một', 700, 0.5]]), hard_limit: 400 })));
eq(byCls(root, 'bbu-tag').map((t) => t.textContent), ['Giảm từ 500', 'Loại từ 400'], 'a limit below the threshold but inside the scale still gets its label (odd setting, no crash)');

// the marker label starts at its line when the line is in the left 40 percent, and ends at it otherwise (so it stays on the chart)
const side = (maxUses) => { const r = render(mk({ top: top([['1', 'A', 'Một', maxUses, 1]]) })).root; return byCls(r, 'bbu-tag')[0].cls.filter((k) => k.indexOf('at-') === 0)[0]; };
eq([side(5000), side(1250), side(1000), side(900)], ['at-start', 'at-start', 'at-end', 'at-end'], 'label side: line at 10, 40, 50 and 55.6 percent');

// states on the page
({ root } = render(mk({ over_start: 0, reduced: 0 })));
eq(byCls(root, 'bbu-lede')[0].textContent, 'Chưa có món nào đạt từ 500 lượt mượn trở lên.', 'calm sentence has no warning prefix'); eq(byCls(root, 'bbu-vh').length, 0, 'no hidden prefix when nothing is over');
eq(byCls(root, 'bbu-effect').length, 0, 'no second sentence'); eq(byTag(root, 'table').length + byTag(root, 'ol').length, 2, 'the charts stay');
({ root } = render(mk({ known: 0, over_start: 0, bands: [], top: [] })));
eq(byTag(root, 'table').length + byTag(root, 'ol').length + byTag(root, 'h3').length, 0, 'known 0: no chart, no heading'); ok(/0\.3\.0/.test(root.textContent), 'known 0 names the tool version');
({ root } = render(mk({ unknown: 0 }))); eq(byCls(root, 'bbu-unk').length, 0, 'no unknown row when unknown is 0');
({ root } = render(mk({ bands: [], unknown: 0 }))); eq(byTag(root, 'table').length, 0, 'no bands and no unknown: no table'); eq(byTag(root, 'ol').length, 1, 'but the list stays');
({ root } = render(mk({ bands: [], unknown: 5 }))); eq(byTag(root, 'table').length, 1, 'only an unknown row: still a table'); eq(byTag(root, 'th').filter((t) => t.getAttribute('scope') === 'rowgroup').length, 0, 'no divider without bands');
({ root } = render(mk({ top: [] }))); eq(byTag(root, 'ol').length, 0, 'no top: no list'); eq(byTag(root, 'h3').length, 1, 'no top: no list heading');
eq(render(null).root, null, 'null usage draws nothing'); eq(render({}).root && 0, 0, 'empty object does not throw');
eq(U.render(null, SAMPLE), null, 'no container: nothing happens');

// a second render replaces the first, with new ids
const again = box(); U.render(again, SAMPLE); const idA = byTag(again, 'h3')[0].getAttribute('id'); U.render(again, SAMPLE);
eq(again.kids.length, 1, 'render replaces the content'); ok(byTag(again, 'h3')[0].getAttribute('id') !== idA, 'ids are not reused');
const dirty = box(); dirty.kids = [new Text('old')]; U.render(dirty, SAMPLE); ok(dirty.textContent.indexOf('old') < 0, 'old content is gone');
U.render(dirty, null); eq(dirty.kids.length, 0, 'null usage clears the container');

// object names are text, never markup: nothing in the file parses HTML
const names = ['<img src=x onerror=alert(1)>', '"><script>alert(1)</script>', '&amp; <b>x</b>'];
({ root } = render(mk({ top: top(names.map((n, i) => ['id' + i, n, '', 900 - i, 1])) })));
eq(byCls(root, 'bbu-name').map((n) => n.textContent), names, 'markup in a name is kept as text');
eq(all(root, (e) => /^(img|script|b)$/.test(e.tag)).length, 0, 'no element made from a name');
const src = fs.readFileSync(SRC, 'utf8');
ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write|\.cssText|\.style\.(width|left|right|height)\b/.test(src.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '')), 'no HTML parsing and no inline style text in the script');

// odd input never throws
const bigN = Number.MAX_SAFE_INTEGER * 1e6;
const bigCounts = mk({ known: bigN, total: bigN, over_start: bigN, unknown: bigN, excluded: bigN, reduced: bigN, hard_limit: bigN, soft_start: 500 });
const bigRows = mk({ bands: [{ from: 0, to: 99, count: bigN }, { from: 1000, to: null, count: -4 }], top: top([['x', 'n', 'v', bigN, 0.5], ['y', 'n', 'v', -9, -9], ['z', null, null, NaN, NaN], [null, null, null, null, null]]) });
const manyRows = mk({ top: Array.from({ length: 200 }, (_, i) => ({ id: String(i), vi: 'Món ' + i, uses: (i * 37) % 1700, weight: i / 200 })) });
const odd = [undefined, null, 0, 1, 'x', true, [], [1, 2], {}, { top: 'x' }, { bands: 5, top: 5 }, { bands: [null, 1, 'x', {}, { from: 'a' }, { from: 0, to: 'z', count: 'q' }], top: [null, 'x', 5, [], {}] },
  { known: NaN, soft_start: Infinity, total: -5, over_start: -1, excluded: 'a', reduced: {}, unknown: [], hard_limit: -3 },
  bigCounts, bigRows,
  mk({ top: [{ id: { toString() { throw new Error('boom'); } }, name: Symbol('s'), vi: 10n, uses: Symbol('u'), weight: Symbol('w') }] }),
  manyRows,
  mk({ bands: [{ from: 500, to: 999, count: 5 }, { from: 0, to: 499, count: 1 }, { from: 1000, to: null, count: 1 }, { from: 1000, to: null, count: 1 }] }),
  { soft_start: 500, known: 3, total: 3, bands: [{ from: 0, to: null, count: 3 }] }, { soft_start: 500, known: 3, bands: [{ from: 500, to: null, count: 3 }] }];
odd.forEach((u, i) => { try { render(u); } catch (e) { fail('odd input #' + i + ' threw: ' + e.message); } });
({ root } = render(manyRows)); eq(byTag(root, 'li').length, 15, 'at most 15 rows however many come in');
const best = manyRows.top.map((t) => t.uses).sort((x, y) => y - x).slice(0, 15).map((n) => n.toLocaleString('vi-VN') + ' lượt mượn');
eq(byCls(root, 'bbu-val').map((v) => v.textContent), best, 'the 15 rows are the 15 highest, sorted, whatever the order they came in');
({ root } = render(bigRows));
eq(byCls(byTag(root, 'table')[0], 'bbu-fill').map((f) => f.vars['--w']).every((w) => /^\d+(\.\d+)?%$/.test(w) && parseFloat(w) <= 100), true, 'widths stay between 0 and 100 percent');
({ root } = render(bigCounts)); ok(all(root, (e) => e.has('bbu-fill')).every((f) => /^\d+(\.\d+)?%$/.test(f.vars['--w'])), 'huge numbers still give widths');
({ root } = render(mk({ top: top([['a', 'A', 'a', 0, 1], ['b', 'B', 'b', 0, 1]]) })));
eq(all(root, (e) => e.has('bbu-fill')).length, 6, 'a count of 0 gets no bar (5 bands and the unknown row do, the 2 rows with 0 uses do not)');

// ---- the words: one object, one use, one kind of store; no filler, dashes, emoji or exclamation marks
const words = Object.values(U.W).join('\n');
ok(!new RegExp('[' + DASHES + ']|!').test(words), 'no em dash, en dash or exclamation mark'); ok(!/\p{Extended_Pictographic}/u.test(words), 'no emoji');
ok(!/mạnh mẽ|toàn diện|tối ưu|mượt mà|vô cùng|hết sức|đa dạng|phong phú|dễ dàng|nhanh chóng|rất /i.test(words), 'no marketing words');
ok(!/lượt dùng|lần dùng|lần mượn|đồ vật|đối tượng|vật dụng/.test(words), 'one word per thing: món and lượt mượn');
ok(/lượt mượn/.test(U.W.lede + U.W.thrRow + U.W.uses), 'the use is called lượt mượn');
eq(Object.values(U.W).filter((s) => typeof s !== 'string').length, 0, 'W holds only strings');
ok(!new RegExp('[' + DASHES + ']').test(src) && !new RegExp('[' + DASHES + ']').test(fs.readFileSync(CSS, 'utf8')), 'no em or en dash in the script or the stylesheet');

// ---- the stylesheet: contrast measured on the panel colour of the app (--case #f3f5f4), and no motion
const css = fs.readFileSync(CSS, 'utf8');
const rules = css.replace(/\/\*[\s\S]*?\*\//g, '');
const tok = {}; for (const [, k, v] of rules.matchAll(/--bbu-([a-z]+):\s*(#[0-9a-f]{6})/gi)) tok[k] = v;
const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
const lum = (h) => { const n = parseInt(h.slice(1), 16); return 0.2126 * lin(n >> 16 & 255) + 0.7152 * lin(n >> 8 & 255) + 0.0722 * lin(n & 255); };
const ratio = (a, b) => { const x = lum(a), y = lum(b); return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05); };
const PANEL = '#f3f5f4';
for (const k of ['ink', 'soft', 'blue', 'warn', 'stripe', 'track']) ok(tok[k], 'token --bbu-' + k + ' in the stylesheet');
const need = (name, a, b, min) => { const r = ratio(a, b); ok(r >= min, name + ' ' + r.toFixed(2) + ' is below ' + min); return r.toFixed(2); };
const got = {
  ink: need('ink text on the panel', tok.ink, PANEL, 4.5), soft: need('soft text on the panel', tok.soft, PANEL, 4.5), softOnTrack: need('soft text on the track', tok.soft, tok.track, 4.5),
  blue: need('blue bar on the panel', tok.blue, PANEL, 3), blueTrack: need('blue bar on the track', tok.blue, tok.track, 3),
  warn: need('orange outline on the panel', tok.warn, PANEL, 3), warnTrack: need('orange outline on the track', tok.warn, tok.track, 3), hatch: need('hatch stripes', tok.warn, tok.stripe, 3),
  line: need('divider and lines on the panel', tok.ink, PANEL, 3), lineTrack: need('lines on the track', tok.ink, tok.track, 3),
};
ok(ratio('#f0691a', PANEL) < 3, 'the brand orange really fails 3:1 on the panel, which is why it is not used'); ok(!/f0691a/i.test(rules), 'the brand orange is not in the stylesheet');
ok(!/@keyframes|transition|animation/.test(rules), 'no animation and no transition in the stylesheet (reduced motion is respected by having none)');
ok(/@media \(forced-colors: active\)/.test(rules) && /CanvasText/.test(rules), 'forced colours block present');
ok(/@container/.test(rules) && /container-type: inline-size/.test(rules), 'layout follows the box width');
ok(!/@media[^{]*max-width/.test(rules), 'no layout decided by the window width (the panel may be narrow on a wide screen)');
console.log('contrast on ' + PANEL + ': ' + JSON.stringify(got));
console.log('usage chart tests OK');

/**
 * Box builder -> Google Sheet (Shift B Objects Preparation).
 *
 * What it does: reads the APPROVED boxes from the Box builder app and writes their Object IDs and quantities
 * into the box block of the matching tab (tab = Module, e.g. M8 / M17).
 *
 * Safety rules (hard-coded, not settings):
 *   - It only READS from the app. It never changes the app and never talks to Scenario Manager.
 *   - It writes ONLY into cells that are completely empty. A block that already holds anything is skipped and reported.
 *   - It never clears, deletes or overwrites a cell, and never touches any other cell.
 *   - Every run first shows a preview and asks for confirmation (menu use). The optional timer skips the question
 *     but follows exactly the same rules.
 *
 * Install: Extensions > Apps Script > paste this file > set APP_URL (and ACCESS_CODE if the app has one) > Save.
 * Reload the sheet; a "Box builder" menu appears. The first run asks you to authorise it (it needs this spreadsheet
 * and permission to call the app's address).
 */

const CONFIG = {
  APP_URL: 'https://box-builder-guwc.onrender.com',   // no trailing slash
  ACCESS_CODE: '',                                     // leave empty if the app has no access code
  // Where each box stage lives on the tab. firstRow..firstRow+count-1 in idCol / qtyCol.
  // Box A1 matches the layout used so far (Object IDs in S4:S24, quantity in V4:V24). Add other stages here
  // only when you know their columns, e.g.  B1: { idCol: 'W', qtyCol: 'Z', firstRow: 4 }
  STAGES: {
    A1: { idCol: 'S', qtyCol: 'V', firstRow: 4 },
  },
  MAX_QTY: 3,                                          // same cap as the earlier Make scenario
};

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Box builder')
    .addItem('Điền các box đã duyệt vào bảng (xem trước)', 'fillFromApp')
    .addItem('Bật tự động mỗi 30 phút', 'installTimer')
    .addItem('Tắt tự động', 'removeTimer')
    .addToUi();
}

/** Menu entry: preview, ask, then write. */
function fillFromApp() {
  const ui = SpreadsheetApp.getUi();
  const plan = buildPlan_(fetchBoxes_());
  const text = describe_(plan);
  if (!plan.writes.length) { ui.alert('Box builder', text, ui.ButtonSet.OK); return; }
  if (ui.alert('Box builder: sẽ ghi các box sau', text + '\n\nChỉ ghi vào ô TRỐNG. Tiếp tục?', ui.ButtonSet.YES_NO) !== ui.Button.YES) return;
  const done = apply_(plan);
  ui.alert('Box builder', 'Đã ghi ' + done + ' box.', ui.ButtonSet.OK);
}

/** Timer entry: same rules, no question. */
function fillFromAppAuto() {
  const plan = buildPlan_(fetchBoxes_());
  const done = apply_(plan);
  console.log(describe_(plan) + '\nĐã ghi ' + done + ' box.');
}

function installTimer() {
  removeTimer();
  ScriptApp.newTrigger('fillFromAppAuto').timeBased().everyMinutes(30).create();
  SpreadsheetApp.getUi().alert('Đã bật. Chỉ ghi vào ô trống, không bao giờ ghi đè.');
}

function removeTimer() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'fillFromAppAuto') ScriptApp.deleteTrigger(t);
  });
}

// ------------------------------------------------------------------ app access (read only)
function fetchBoxes_() {
  const headers = CONFIG.ACCESS_CODE ? { 'X-Access-Code': CONFIG.ACCESS_CODE } : {};
  const res = UrlFetchApp.fetch(CONFIG.APP_URL + '/api/taskboxes?status=approved', { headers: headers, muteHttpExceptions: true });
  if (res.getResponseCode() === 401) throw new Error('Box builder cần mã truy cập: điền ACCESS_CODE trong CONFIG.');
  if (res.getResponseCode() !== 200) throw new Error('Không tải được Box builder (HTTP ' + res.getResponseCode() + '). Máy chủ miễn phí có thể đang thức dậy, thử lại sau 1 phút.');
  return JSON.parse(res.getContentText()).boxes || [];
}

// ------------------------------------------------------------------ planning (pure, no writes)
/**
 * For every approved box decide: write / already there / skipped (with the reason).
 * `read(tab, a1)` returns a 2-D array of values or null when the tab does not exist; injected so it can be tested.
 */
function buildPlan_(boxes, read) {
  read = read || readRange_;
  const plan = { writes: [], same: [], skipped: [] };
  boxes.forEach(function (b) {
    const meta = b.builder || {};
    const tab = meta.module || b.module;
    const stage = b.stage;
    const name = tab + ' / ' + (meta.label || stage);
    const cfg = CONFIG.STAGES[stage];
    if (!cfg) { plan.skipped.push(name + ': chưa cấu hình cột cho ' + stage); return; }
    const n = b.contents.length;
    const first = cfg.firstRow, last = first + n - 1;
    const idRange = cfg.idCol + first + ':' + cfg.idCol + last;
    const qtyRange = cfg.qtyCol + first + ':' + cfg.qtyCol + last;
    const ids = read(tab, idRange), qtys = read(tab, qtyRange);
    if (!ids || !qtys) { plan.skipped.push(name + ': không có tab "' + tab + '"'); return; }
    const flat = function (v) { return v.map(function (r) { return String(r[0]).trim(); }); };
    const curIds = flat(ids), curQty = flat(qtys);
    const want = b.contents.map(function (c) { return pad6_(c.object_id); });
    const empty = curIds.every(function (x) { return x === ''; }) && curQty.every(function (x) { return x === ''; });
    if (empty) {
      plan.writes.push({
        name: name, tab: tab, idRange: idRange, qtyRange: qtyRange,
        ids: want.map(function (x) { return [x]; }),
        qtys: b.contents.map(function (c) { return [Math.min(CONFIG.MAX_QTY, Math.max(1, Number(c.quantity) || 1))]; }),
      });
    } else if (curIds.map(pad6_).join(',') === want.join(',')) {
      plan.same.push(name + ': đã có sẵn đúng ' + n + ' món');
    } else {
      plan.skipped.push(name + ': ' + idRange + ' đã có dữ liệu khác, không ghi đè');
    }
  });
  return plan;
}

function pad6_(x) { x = String(x).trim(); return /^\d+$/.test(x) ? ('000000' + x).slice(-6) : x; }

function readRange_(tab, a1) {
  const sh = SpreadsheetApp.getActive().getSheetByName(tab);
  return sh ? sh.getRange(a1).getValues() : null;
}

function describe_(plan) {
  const lines = [];
  plan.writes.forEach(function (w) { lines.push('GHI   ' + w.name + '  →  ' + w.idRange + ' & ' + w.qtyRange); });
  plan.same.forEach(function (s) { lines.push('ĐÃ CÓ ' + s); });
  plan.skipped.forEach(function (s) { lines.push('BỎ QUA ' + s); });
  return lines.length ? lines.join('\n') : 'Chưa có box nào ở trạng thái Đã duyệt.';
}

// ------------------------------------------------------------------ the only place that writes
function apply_(plan) {
  const ss = SpreadsheetApp.getActive();
  let n = 0;
  plan.writes.forEach(function (w) {
    const sh = ss.getSheetByName(w.tab);
    const idR = sh.getRange(w.idRange), qR = sh.getRange(w.qtyRange);
    // re-check right before writing: someone may have typed in the block since the preview
    const stillEmpty = idR.getValues().concat(qR.getValues()).every(function (r) { return String(r[0]).trim() === ''; });
    if (!stillEmpty) return;
    idR.setNumberFormat('@').setValues(w.ids);          // plain text keeps the leading zeros (004702)
    qR.setValues(w.qtys);
    n++;
  });
  return n;
}

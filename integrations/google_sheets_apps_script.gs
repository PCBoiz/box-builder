/**
 * Box builder -> Google Sheet (Shift B Objects Preparation).
 *
 * What it does: reads the APPROVED boxes from the Box builder app and writes their Object IDs and quantities
 * into the box block of the matching tab (tab = Module, e.g. M8 / M17).
 *
 * It also has one read-only helper: the menu item "Lấy ID đã dùng trong bảng" (showUsedIds) collects every Object ID
 * already sitting in the Box B2 / B3 / A1 blocks of all module tabs (M1, M2, ...) and shows them in a dialog, one per line,
 * to copy into the app's box "Đã dùng trong bảng tính". It only reads the sheet; it never writes, clears or changes anything.
 *
 * Safety rules (hard-coded, not settings):
 *   - It only READS from the app. It never changes the app and never talks to Scenario Manager.
 *   - It writes ONLY into cells that are completely empty. A block that already holds anything is skipped and reported.
 *   - It never clears, deletes or overwrites a cell, and never touches any other cell.
 *   - Every run first shows a preview and asks for confirmation (menu use). The optional timer skips the question
 *     but follows exactly the same rules.
 *   - The used-IDs helper (collectUsedIds_, showUsedIds) calls no write method at all.
 *
 * Install: Extensions > Apps Script > paste this file > set APP_URL (and ACCESS_CODE if the app has one) > Save.
 * Reload the sheet; a "Box builder" menu appears. The first run asks you to authorise it (it needs this spreadsheet
 * and permission to call the app's address).
 */

const CONFIG = {
  APP_URL: 'https://box-builder-guwc.onrender.com',   // no trailing slash
  ACCESS_CODE: '',                                     // leave empty if the app has no access code
  // Where each box stage lives on the tab: the column of the Object ID and of the quantity (SL), checked against the real workbook
  // (every module tab: block title in row 2 of each section, sub-headers in row 3, data from row 4):
  //   Box B2 = columns C:I   (Object ID in E, SL in H)
  //   Box B3 = columns J:P   (Object ID in L, SL in O)
  //   Box A1 = columns Q:W   (Object ID in S, SL in V)
  // Add other stages here only when you know their columns.
  STAGES: {
    B2: { idCol: 'E', qtyCol: 'H' },
    B3: { idCol: 'L', qtyCol: 'O' },
    A1: { idCol: 'S', qtyCol: 'V' },
  },
  // A tab holds several sections one under another (M8-01, M8-02, ...), each with its own B2, B3 and A1 blocks. The spacing is NOT
  // the same on every tab (25 rows on most, 26 on M8 and M17, 23 after the fifth section of M5), so a section is found by counting
  // the block titles ("Box B2 - 21 Objects") down the tab: the box's section code (for example M8-02) picks the 2nd title of its stage,
  // and its rows start two rows under that title. A section whose title is not there is skipped and reported, never guessed.
  SCAN_ROWS: 1600,                                     // how far down a tab the block titles are looked for (60 sections of 26 rows)
  MAX_QTY: 3,                                          // same cap as the earlier Make scenario
  // Used only by the read-only "used IDs" helper: a block has 21 data rows, and at most 60 sections of a tab are read.
  BLOCK_ROWS: 21,
  MAX_SECTIONS: 60,
};

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Box builder')
    .addItem('Điền các box đã duyệt vào bảng (xem trước)', 'fillFromApp')
    .addItem('Bật tự động mỗi 30 phút', 'installTimer')
    .addItem('Tắt tự động', 'removeTimer')
    .addSeparator()
    .addItem('Lấy ID đã dùng trong bảng', 'showUsedIds')
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
  // /api/sheet-boxes answers with an empty list when the "Cấp danh sách cho bảng tính" switch is off in the app
  const res = UrlFetchApp.fetch(CONFIG.APP_URL + '/api/sheet-boxes', { headers: headers, muteHttpExceptions: true });
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
    const sec = sectionOf_(b.module, tab);               // b.module is the SM section code, e.g. M8-02
    if (sec === null) { plan.skipped.push(name + ': mã section "' + b.module + '" không khớp tab ' + tab); return; }
    const label = name + (sec > 1 ? ' (section ' + sec + ')' : '');
    const starts = blockStarts_(tab, stage, read);
    if (starts === null) { plan.skipped.push(name + ': không có tab "' + tab + '"'); return; }
    if (sec > starts.length) {
      plan.skipped.push(label + ': không thấy khối "Box ' + stage + '" của section ' + sec + ' (tab chỉ có ' + starts.length + ' khối) nên không ghi'); return;
    }
    const first = starts[sec - 1], last = first + n - 1;
    if (starts[sec] !== undefined && last >= starts[sec] - 2) {          // the next block's title would be overwritten
      plan.skipped.push(label + ': ' + n + ' món không vừa khối này (khối sau bắt đầu ở dòng ' + (starts[sec] - 2) + ') nên không ghi'); return;
    }
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
        name: label, tab: tab, idRange: idRange, qtyRange: qtyRange,
        ids: want.map(function (x) { return [x]; }),
        qtys: b.contents.map(function (c) { return [Math.min(CONFIG.MAX_QTY, Math.max(1, Number(c.quantity) || 1))]; }),
      });
    } else if (curIds.map(pad6_).join(',') === want.join(',')) {
      plan.same.push(label + ': đã có sẵn đúng ' + n + ' món');
    } else {
      plan.skipped.push(label + ': ' + idRange + ' đã có dữ liệu khác, không ghi đè');
    }
  });
  return plan;
}

/**
 * First data row of every block of one stage on a tab, top to bottom: the row two under each block title ("Box B2 - 21 Objects"),
 * looked for in the column left of the Object ID column. Counting titles (not a fixed row step) is what makes tabs with different
 * spacing work. Returns null when the tab does not exist, [] when it has no title for the stage.
 */
function blockStarts_(tab, stage, read) {
  const cfg = CONFIG.STAGES[stage];
  const col = colShift_(cfg.idCol, -1);
  const rows = read(tab, col + '1:' + col + CONFIG.SCAN_ROWS);
  if (rows === null || rows === undefined) return null;
  const want = 'box ' + String(stage).toLowerCase();
  const starts = [];
  rows.forEach(function (r, i) {
    const text = r && r[0] !== null && r[0] !== undefined ? String(r[0]).trim().toLowerCase() : '';
    if (text.indexOf(want) === 0) starts.push(i + 3);                  // row index i+1 is the title, the data starts two rows lower
  });
  return starts.slice(0, CONFIG.MAX_SECTIONS);
}

/** 'M8-02' on tab 'M8' -> 2. No code, or the code equals the tab -> 1. A code for another tab -> null (never guess). */
function sectionOf_(code, tab) {
  code = String(code || '').trim();
  if (!code || code === tab) return 1;
  const m = /^(.*)-(\d{1,2})$/.exec(code);
  if (!m || m[1] !== tab) return null;
  const n = parseInt(m[2], 10);
  return n >= 1 ? n : null;
}

/** Column letter shifted by delta: ('E', -1) -> 'D'. Works for A..ZZ. */
function colShift_(col, delta) {
  let n = 0;
  for (let i = 0; i < col.length; i++) n = n * 26 + (col.charCodeAt(i) - 64);
  n += delta;
  let out = '';
  while (n > 0) { const r = (n - 1) % 26; out = String.fromCharCode(65 + r) + out; n = Math.floor((n - 1) / 26); }
  return out;
}

function pad6_(x) { x = String(x).trim(); return /^\d+$/.test(x) ? ('000000' + x).slice(-6) : x; }

function readRange_(tab, a1) {
  const sh = SpreadsheetApp.getActive().getSheetByName(tab);
  if (!sh) return null;
  const m = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(a1);
  const maxRows = sh.getMaxRows();
  if (m && Number(m[4]) > maxRows) {                       // a range past the end of the grid would throw: read only what exists
    return Number(m[2]) > maxRows ? [] : sh.getRange(m[1] + m[2] + ':' + m[3] + maxRows).getValues();
  }
  return sh.getRange(a1).getValues();
}

function describe_(plan) {
  const lines = [];
  plan.writes.forEach(function (w) { lines.push('GHI   ' + w.name + '  →  ' + w.idRange + ' & ' + w.qtyRange); });
  plan.same.forEach(function (s) { lines.push('ĐÃ CÓ ' + s); });
  plan.skipped.forEach(function (s) { lines.push('BỎ QUA ' + s); });
  return lines.length ? lines.join('\n') : 'Chưa có box nào ở trạng thái Đã duyệt.';
}

// ------------------------------------------------------------------ used Object IDs (read only, never writes)
/**
 * Every Object ID already in the workbook, so the team can paste them into the app's "Đã dùng trong bảng tính" box.
 * For each tab and each stage in CONFIG.STAGES it finds the blocks by their titles ("Box B2 - 21 Objects", see blockStarts_;
 * at most CONFIG.MAX_SECTIONS per stage), reads the Object ID column once, and in each block keeps the 5 or 6 digit values of
 * the 21 ID cells. Two reads per tab and stage, so a whole workbook takes about a hundred reads.
 * `read(tab, a1)` returns a 2-D array, or null when the tab does not exist; injected so it can be tested.
 * Returns { ids: sorted unique 6-digit strings, perTab: { tab: number of distinct IDs on that tab }, tabsMissing: [tabs not found] }.
 */
function collectUsedIds_(tabNames, read) {
  read = read || readRange_;
  const all = {}, perTab = {}, tabsMissing = [];
  (tabNames || []).forEach(function (tab) {
    const own = {};
    let exists = true;
    Object.keys(CONFIG.STAGES).forEach(function (stage) {
      if (!exists) return;
      const cfg = CONFIG.STAGES[stage];
      const starts = blockStarts_(tab, stage, read);
      if (starts === null) { exists = false; return; }                               // no such tab
      if (!starts.length) return;                                                    // no block of this stage on the tab
      const column = read(tab, cfg.idCol + '1:' + cfg.idCol + CONFIG.SCAN_ROWS) || [];
      starts.forEach(function (first) {
        for (let i = first - 1; i < first - 1 + CONFIG.BLOCK_ROWS && i < column.length; i++) {
          const id = usedIdOf_(column[i] && column[i][0]);
          if (id) { own[id] = true; all[id] = true; }
        }
      });
    });
    if (exists) perTab[tab] = Object.keys(own).length; else tabsMissing.push(tab);
  });
  return { ids: Object.keys(all).sort(), perTab: perTab, tabsMissing: tabsMissing };
}

/** A cell value -> 6-digit ID, or '' when it is not one. Accepts 5 or 6 digits, with an optional leading apostrophe and spaces. */
function usedIdOf_(v) {
  const s = (v === null || v === undefined ? '' : String(v)).trim().replace(/^'/, '').trim();
  return /^\d{5,6}$/.test(s) ? pad6_(s) : '';
}

/** Only module tabs (M1, M8, M17 ...) are scanned; anything else (REF, notes, ...) is left alone. */
function moduleTabNames_(names) {
  return names.filter(function (n) { return /^M\d+$/.test(String(n)); });
}

function escapeHtml_(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

/** The dialog body for a collectUsedIds_ result. Everything that comes from the sheet is escaped. */
function usedIdsHtml_(res) {
  const ids = res.ids || [], tabs = Object.keys(res.perTab || {}), missing = res.tabsMissing || [];
  const withIds = tabs.filter(function (t) { return res.perTab[t] > 0; }).length;
  const head = ids.length
    ? 'Tìm thấy ' + ids.length + ' ID từ ' + withIds + ' tab (đã quét ' + tabs.length + ' tab).'
    : 'Không tìm thấy ID nào (đã quét ' + tabs.length + ' tab).';
  return '<div style="font-family:Arial,sans-serif;font-size:13px">' +
    '<p>' + escapeHtml_(head) + '</p>' +
    '<p>Bấm "Chọn tất cả", sao chép (Ctrl+C), rồi dán vào ô "Đã dùng trong bảng tính" của app.</p>' +
    '<textarea id="ids" readonly style="width:100%;height:190px;font-family:monospace">' + escapeHtml_(ids.join('\n')) + '</textarea>' +
    '<p><button type="button" onclick="var t=document.getElementById(\'ids\');t.focus();t.select();">Chọn tất cả</button></p>' +
    (missing.length ? '<p>Không đọc được tab: ' + escapeHtml_(missing.join(', ')) + '</p>' : '') +
    '</div>';
}

/** Menu entry: show every Object ID already used in the workbook. Reads only. */
function showUsedIds() {
  const names = SpreadsheetApp.getActive().getSheets().map(function (s) { return s.getName(); });
  const res = collectUsedIds_(moduleTabNames_(names));
  const html = HtmlService.createHtmlOutput(usedIdsHtml_(res)).setWidth(420).setHeight(360);
  SpreadsheetApp.getUi().showModalDialog(html, 'ID đã dùng trong bảng tính');
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

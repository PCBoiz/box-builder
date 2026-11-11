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
 * It also reports the result of the supporters' check back to the app (menu "Gửi kết quả kiểm cho Box builder (xem trước)";
 * the 30-minute timer does the same after its fill step). For every approved box it READS from the box's block: the SC tick
 * (checkbox in the title row), the supporters' tick (checkbox right under the last data row) and the Object IDs and SL that
 * are still there (supporters blank the cells of missing objects). A tick counts only when the cell holds a real true / false;
 * anything else is sent as unknown (null). It sends those, and nothing else, to <APP_URL>/api/sheet/checks with the header
 * X-Sheet-Token. The app records them; it never sends anything back into the sheet.
 *
 * Safety rules (hard-coded, not settings):
 *   - It READS the approved boxes from the app (GET /api/sheet-boxes). The only thing it SENDS to the app is the check report
 *     above (ticks, Object IDs and SL of the blocks), through that one route and only with the token. It never talks to
 *     Scenario Manager.
 *   - It writes into the sheet ONLY into cells that are completely empty. A block that already holds anything is skipped and reported.
 *   - It never clears, deletes or overwrites a cell, and never touches any other cell.
 *   - Every run first shows a preview and asks for confirmation (menu use). The optional timer skips the question
 *     but follows exactly the same rules.
 *   - The used-IDs helper (collectUsedIds_, showUsedIds) and the checking code (collectChecks_, checksPreview_, sendChecks_,
 *     sendChecks, sendChecksAuto_) call no write method on the sheet at all. The only write besides the fill is the token,
 *     stored once into Script Properties by the menu item "Đặt mã Box builder" (setAppToken).
 *   - The token is never in this file. Script Properties can be read by everyone who can edit this script, so give edit
 *     access only to people who may know the code.
 *
 * Install: Extensions > Apps Script > paste this file > set APP_URL (and ACCESS_CODE if the app has one) > Save.
 * Reload the sheet; a "Box builder" menu appears. The first run asks you to authorise it (it needs this spreadsheet
 * and permission to call the app's address). To report check results, run "Đặt mã Box builder" once and type the code
 * the app's admin gives you.
 */

const CONFIG = {
  APP_URL: 'https://box-builder-guwc.onrender.com',   // no trailing slash
  ACCESS_CODE: '',                                     // leave empty if the app has no access code
  // Where each box stage lives on the tab: the column of the Object ID and of the quantity (SL), checked against the real workbook
  // (every module tab: block title in row 2 of each section, sub-headers in row 3, data from row 4):
  //   Box B2 = columns C:I   (Object ID in E, SL in H)
  //   Box B3 = columns J:P   (Object ID in L, SL in O)
  //   Box A1 = columns Q:W   (Object ID in S, SL in V)
  // The two ticks (used only by the read-only check report): scCol = the SC tick, a checkbox in the block's title row;
  // supCol = the supporters' tick, a checkbox in the row right under the last data row (SUPPORT_TICK_ROWS below the title row).
  // Add other stages here only when you know their columns.
  STAGES: {
    B2: { idCol: 'E', qtyCol: 'H', scCol: 'C', supCol: 'D' },
    B3: { idCol: 'L', qtyCol: 'O', scCol: 'J', supCol: 'K' },
    A1: { idCol: 'S', qtyCol: 'V', scCol: 'Q', supCol: 'R' },
  },
  // A tab holds several sections one under another (M8-01, M8-02, ...), each with its own B2, B3 and A1 blocks. The spacing is NOT
  // the same on every tab (25 rows on most, 26 on M8 and M17, 23 after the fifth section of M5), so a section is found by counting
  // the block titles ("Box B2 - 21 Objects") down the tab: the box's section code (for example M8-02) picks the 2nd title of its stage,
  // and its rows start two rows under that title. A section whose title is not there is skipped and reported, never guessed.
  SCAN_ROWS: 1600,                                     // how far down a tab the block titles are looked for (60 sections of 26 rows)
  MAX_QTY: 3,                                          // same cap as the earlier Make scenario
  // Used only by the two read-only helpers (used IDs, check report): a block has 21 data rows, and at most 60 sections of a tab are read.
  BLOCK_ROWS: 21,
  MAX_SECTIONS: 60,
  // The check report. The supporters' tick sits SUPPORT_TICK_ROWS under the block's title row (title row + 23). When the next block's
  // title is at or above that row, or the tab ends before it, the tick is reported as unknown (null), never guessed.
  SUPPORT_TICK_ROWS: 23,
  CHECK_MAX_BOXES: 200,                                // the app takes at most this many boxes per request ...
  CHECK_MAX_ROWS: 30,                                  // ... and this many rows per box (BLOCK_ROWS must stay below it)
  TOKEN_PROPERTY: 'SHEET_TOKEN',                       // the NAME of the Script Property that holds the app's code; the code itself is never written here
};

function onOpen() {
  SpreadsheetApp.getUi().createMenu('Box builder')
    .addItem('Điền các box đã duyệt vào bảng (xem trước)', 'fillFromApp')
    .addItem('Bật tự động mỗi 30 phút', 'installTimer')
    .addItem('Tắt tự động', 'removeTimer')
    .addSeparator()
    .addItem('Gửi kết quả kiểm cho Box builder (xem trước)', 'sendChecks')
    .addItem('Đặt mã Box builder', 'setAppToken')
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

/**
 * Timer entry: same rules, no question. The fill goes first; then the check report is sent. The two never hide each other:
 * if the fill throws, the error is logged and the report is still sent (the supporters' work is still in the sheet), and the error
 * is raised again at the very end so the run still counts as failed in Apps Script; if the report fails, the fill's result is already logged.
 */
function fillFromAppAuto() {
  const boxes = fetchBoxes_();
  let failed = null;
  try {
    const plan = buildPlan_(boxes);
    const done = apply_(plan);
    console.log(describe_(plan) + '\nĐã ghi ' + done + ' box.');
  } catch (e) {
    failed = e;
    console.log('Không điền được box vào bảng: ' + clean_(e && e.message ? e.message : e, ''));
  }
  try { sendChecksAuto_(boxes); }                       // reports its own problems in the log and does not throw; this is only the last safety net
  catch (e) { console.log('Không gửi được kết quả kiểm.'); }
  if (failed) throw failed;
}

function installTimer() {
  removeTimer();
  ScriptApp.newTrigger('fillFromAppAuto').timeBased().everyMinutes(30).create();
  SpreadsheetApp.getUi().alert('Đã bật. Mỗi 30 phút: điền box mới vào ô trống (không bao giờ ghi đè), rồi gửi kết quả kiểm cho Box builder nếu đã đặt mã.');
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

// ------------------------------------------------------------------ check report (reads the sheet, never writes it)
/**
 * For every approved box the app handed out (builder.box_id), find its block the way the fill does (tab = module, the section code
 * picks the Nth title of the stage, see buildPlan_ / blockStarts_) and read: the SC tick, the supporters' tick and the non-blank
 * (Object ID, SL) rows of the block, in one read of the block per box. Calls no write method; `read(tab, a1)` is injected so it can be tested.
 * Returns { items: [{ name, tab, missing, check: { box_id, sc_tick, supporter_tick, rows: [{ id, qty }] } }], skipped: [text] }.
 *   - sc_tick / supporter_tick: true or false only when the cell holds a real boolean, else null (unknown). The supporters' tick
 *     is null as well when its row would meet the next block's title or fall outside the tab.
 *   - rows: the rows whose ID cell is not blank; id = 6 digits as text, qty = a whole number (0 means the supporters have none: the app reads it as
 *     gone), or null when SL is blank or not one.
 *   - `missing` (local only, never sent): how many of the app's own objects are no longer in the block.
 * A box whose block is not found, or whose block has an ID cell that is not a number, is listed in `skipped` and is not sent.
 */
function collectChecks_(boxes, read) {
  boxes = boxes || fetchBoxes_();
  read = read || readRange_;
  const res = { items: [], skipped: [] };
  const starts = {};                                     // blockStarts_ once per tab and stage
  boxes.forEach(function (b) {
    const meta = b.builder || {};
    const tab = meta.module || b.module;
    const stage = b.stage;
    const name = tab + ' / ' + (meta.label || stage);
    let label = name;
    const skip = function (why) { res.skipped.push(label + ': ' + why); };
    if (!Number.isSafeInteger(meta.box_id) || meta.box_id < 1) { skip('Box builder không gửi mã hộp'); return; }
    const cfg = CONFIG.STAGES[stage];
    if (!cfg) { skip('chưa cấu hình cột cho ' + stage); return; }
    const sec = sectionOf_(b.module, tab);               // b.module is the SM section code, e.g. M8-02
    if (sec === null) { skip('không thấy trong bảng (mã section "' + b.module + '" không khớp tab ' + tab + ')'); return; }
    label = name + (sec > 1 ? ' (section ' + sec + ')' : '');
    const key = tab + '|' + stage;
    if (!(key in starts)) starts[key] = blockStarts_(tab, stage, read);
    const found = starts[key];
    if (found === null) { skip('không thấy trong bảng (không có tab "' + tab + '")'); return; }
    if (sec > found.length) {
      skip('không thấy trong bảng (khối "Box ' + stage + '" của section ' + sec + ', tab chỉ có ' + found.length + ' khối)'); return;
    }
    const first = found[sec - 1], titleRow = first - 2;
    const nextTitle = found[sec] !== undefined ? found[sec] - 2 : null;
    let last = first + CONFIG.BLOCK_ROWS - 1;
    if (nextTitle !== null && last >= nextTitle) last = nextTitle - 1;          // never read into the next block
    if (last < first) { skip('khối sau nằm quá sát khối này, không đọc được'); return; }
    const supRow = titleRow + CONFIG.SUPPORT_TICK_ROWS;
    const supOk = nextTitle === null || supRow < nextTitle;                      // else that row is the next block's title: unknown, and not even read
    // one read per box: the block from the SC tick column to the SL column, from the title row down to the last row we need
    const rect = read(tab, cfg.scCol + titleRow + ':' + cfg.qtyCol + (supOk ? Math.max(last, supRow) : last));
    if (!rect) { skip('không thấy trong bảng (không có tab "' + tab + '")'); return; }
    const left = colNum_(cfg.scCol);
    const at = function (row, col) { const r = rect[row - titleRow]; return r ? r[colNum_(col) - left] : undefined; };   // undefined: past the end of the tab
    const rows = [];
    for (let row = first; row <= last; row++) {
      const id = checkIdOf_(at(row, cfg.idCol));
      if (id === '') continue;                           // blank: the supporters took this object out
      if (id === null) {
        const shown = String(at(row, cfg.idCol)).replace(/\s+/g, ' ').trim().slice(0, 20);
        skip('ô ' + cfg.idCol + row + ' có "' + shown + '", không phải ID: sửa hoặc xoá ô đó, chưa gửi'); return;
      }
      rows.push({ id: id, qty: checkQtyOf_(at(row, cfg.qtyCol)) });
    }
    const have = {};
    rows.forEach(function (r) { have[r.id] = true; });
    const missing = (b.contents || []).filter(function (c) { return !have[pad6_(c.object_id)]; }).length;
    res.items.push({
      name: label, tab: tab, missing: missing,
      check: {
        box_id: meta.box_id,
        sc_tick: boolOf_(at(titleRow, cfg.scCol)),
        supporter_tick: supOk ? boolOf_(at(supRow, cfg.supCol)) : null,
        rows: rows,
      },
    });
  });
  return res;
}

/** A checkbox cell: true / false when the value is a real boolean, else null (blank, text, a number, a cell past the end of the tab). */
function boolOf_(v) { return typeof v === 'boolean' ? v : null; }

/** Column letter -> number: 'A' -> 1, 'E' -> 5, 'AA' -> 27. */
function colNum_(col) {
  let n = 0;
  for (let i = 0; i < col.length; i++) n = n * 26 + (col.charCodeAt(i) - 64);
  return n;
}

/** An Object ID cell: '' when blank, the 6-digit text when it is 1 to 6 digits (a number is fine too), null when it is anything else. */
function checkIdOf_(v) {
  const s = (v === null || v === undefined ? '' : String(v)).trim().replace(/^'/, '').trim();
  if (s === '') return '';
  return /^\d{1,6}$/.test(s) ? pad6_(s) : null;
}

/**
 * An SL cell: a whole number from 0 up (5, '5', ' 5 ', 0, '0', ' 0 '), else null (blank, text, negative, decimal: unknown).
 * A supporter who types 0 and leaves the ID means "we do not have this object": the app reads a quantity of 0 as gone,
 * so 0 must reach it as 0 and never as unknown.
 */
function checkQtyOf_(v) {
  if (typeof v === 'number') return Number.isSafeInteger(v) && v >= 0 ? (v === 0 ? 0 : v) : null;
  const s = typeof v === 'string' ? v.trim() : '';
  if (!/^\d{1,9}$/.test(s)) return null;
  return parseInt(s, 10);
}

/** The text of the preview: what will be sent (counts per module, SC ticks, supporters' ticks, boxes with missing objects) and what will not. */
function checksPreview_(res) {
  const items = res.items || [], lines = [];
  if (items.length) {
    const perTab = {};
    items.forEach(function (it) { perTab[it.tab] = (perTab[it.tab] || 0) + 1; });
    const num = function (t) { const m = /\d+/.exec(t); return m ? Number(m[0]) : 0; };
    const tabs = Object.keys(perTab).sort(function (a, b) { return num(a) - num(b) || (a < b ? -1 : a > b ? 1 : 0); });
    const count = function (f) { return items.filter(f).length; };
    lines.push('Sẽ gửi kết quả kiểm của ' + items.length + ' hộp (' + tabs.map(function (t) { return t + ': ' + perTab[t]; }).join(', ') + ').');
    lines.push('SC đã tick "cần": ' + count(function (it) { return it.check.sc_tick === true; }) + ' hộp.');
    lines.push('Người hỗ trợ đã tick: ' + count(function (it) { return it.check.supporter_tick === true; }) + ' hộp.');
    lines.push('Hộp thiếu món (ô ID bị bỏ trống): ' + count(function (it) { return it.missing > 0; }) + ' hộp.');
    const empty = count(function (it) { return !it.check.rows.length; });
    if (empty) lines.push('Khối còn trống hoàn toàn: ' + empty + ' hộp.');
    const noSc = count(function (it) { return it.check.sc_tick === null; }), noSup = count(function (it) { return it.check.supporter_tick === null; });
    if (noSc || noSup) lines.push('Không đọc được ô tick (gửi là chưa biết): SC ' + noSc + ' hộp, người hỗ trợ ' + noSup + ' hộp.');
  }
  (res.skipped || []).forEach(function (s) { lines.push('KHÔNG GỬI ' + s); });
  return lines.length ? lines.join('\n') : 'Chưa có box nào ở trạng thái Đã duyệt.';
}

// ------------------------------------------------------------------ check report: sending (the only thing sent to the app)
const NO_TOKEN_TEXT = 'Chưa đặt mã Box builder. Chọn "Đặt mã Box builder" trong menu rồi nhập mã người quản lý đưa.';

/** The app's code, read from Script Properties (never from CONFIG or this file). '' when it was not set. */
function getToken_() {
  const v = PropertiesService.getScriptProperties().getProperty(CONFIG.TOKEN_PROPERTY);
  return v ? String(v).trim() : '';
}

/** A message from outside, made safe to show: no token, one line, short. */
function clean_(text, token) {
  let s = String(text === null || text === undefined ? '' : text);
  if (token) s = s.split(token).join('***');
  return s.replace(/\s+/g, ' ').trim().slice(0, 160);
}

/**
 * POST the checks (an array of { box_id, sc_tick, supporter_tick, rows }) to <APP_URL>/api/sheet/checks, at most CONFIG.CHECK_MAX_BOXES per request.
 * Never throws. Returns { ok, text, saved, skipped }: `text` is the Vietnamese message to show or log.
 */
function sendChecks_(checks) {
  if (!checks.length) return { ok: true, text: 'Không có hộp nào để gửi.', saved: 0, skipped: 0 };
  const token = getToken_();
  if (!token) return { ok: false, text: NO_TOKEN_TEXT, saved: 0, skipped: 0 };
  if (!/^https:\/\//.test(CONFIG.APP_URL)) return { ok: false, text: 'APP_URL phải bắt đầu bằng https:// thì mới gửi được mã. Sửa APP_URL trong CONFIG.', saved: 0, skipped: 0 };
  let saved = 0, skipped = 0, sent = 0;
  const stop = function (text) {
    return { ok: false, text: (sent ? 'Đã gửi ' + sent + ' hộp trước đó. ' : '') + text, saved: saved, skipped: skipped };
  };
  for (let i = 0; i < checks.length; i += CONFIG.CHECK_MAX_BOXES) {
    const part = checks.slice(i, i + CONFIG.CHECK_MAX_BOXES);
    let res;
    try {
      res = UrlFetchApp.fetch(CONFIG.APP_URL + '/api/sheet/checks', {
        method: 'post', contentType: 'application/json', headers: { 'X-Sheet-Token': token },
        payload: JSON.stringify({ checks: part }), muteHttpExceptions: true, followRedirects: false,
      });
    } catch (e) {
      return stop('Không kết nối được Box builder (' + clean_(e && e.message ? e.message : e, token) + '). Máy chủ miễn phí có thể đang thức dậy, thử lại sau 1 phút.');
    }
    const code = res.getResponseCode();
    if (code === 401) return stop('Mã Box builder sai (HTTP 401). Chọn "Đặt mã Box builder" và nhập lại mã đúng.');
    if (code === 503) return stop('Box builder chưa đặt mã nhận kết quả, hoặc máy chủ đang thức dậy (HTTP 503). Thử lại sau 1 phút; nếu vẫn vậy, nhờ người quản lý đặt mã trên máy chủ.');
    let body = null;
    try { body = JSON.parse(res.getContentText()); } catch (e) { body = null; }
    if (code !== 200) {
      const detail = body && typeof body.detail === 'string' ? ': ' + clean_(body.detail, token) : '';
      return stop('Gửi không được (HTTP ' + code + detail + '). Thử lại sau.');
    }
    if (!body || body.ok !== true) return stop('Box builder trả lời không đúng dạng, không rõ đã ghi chưa. Thử lại sau.');
    saved += Number(body.saved) || 0;
    skipped += Number(body.skipped) || 0;
    sent += part.length;
  }
  return {
    ok: true, saved: saved, skipped: skipped,
    text: 'Đã gửi ' + sent + ' hộp: Box builder ghi nhận ' + saved + ', bỏ qua ' + skipped + (skipped ? ' (hộp đã xoá, chưa duyệt hoặc đã lên SM)' : '') + '.',
  };
}

/** Menu entry: read, show what will be sent, ask, then send. Writes nothing into the sheet. */
function sendChecks() {
  const ui = SpreadsheetApp.getUi();
  if (!getToken_()) { ui.alert('Box builder', NO_TOKEN_TEXT, ui.ButtonSet.OK); return; }
  let res;
  try { res = collectChecks_(); } catch (e) { ui.alert('Box builder', String(e && e.message ? e.message : e), ui.ButtonSet.OK); return; }
  const text = checksPreview_(res);
  if (!res.items.length) { ui.alert('Box builder', text, ui.ButtonSet.OK); return; }
  if (ui.alert('Box builder: sẽ gửi kết quả kiểm', text + '\n\nChỉ gửi cho Box builder, không ghi gì vào bảng tính. Tiếp tục?', ui.ButtonSet.YES_NO) !== ui.Button.YES) return;
  ui.alert('Box builder', sendChecks_(res.items.map(function (it) { return it.check; })).text, ui.ButtonSet.OK);
}

/** Timer part: no question, never throws (a failed report must not break the fill or fill the inbox with failure mails). Logs the outcome. */
function sendChecksAuto_(boxes) {
  try {
    if (!getToken_()) { console.log('Chưa đặt mã Box builder: không gửi kết quả kiểm.'); return; }
    const res = collectChecks_(boxes);
    console.log(checksPreview_(res));
    if (!res.items.length) return;
    console.log(sendChecks_(res.items.map(function (it) { return it.check; })).text);
  } catch (e) {
    console.log('Không gửi được kết quả kiểm: ' + clean_(e && e.message ? e.message : e, getToken_()));
  }
}

/** Menu entry: type the app's code once. It goes into Script Properties only; it is never shown again and never written into the sheet or this file. */
function setAppToken() {
  const ui = SpreadsheetApp.getUi();
  const r = ui.prompt('Đặt mã Box builder', 'Nhập mã nhận kết quả kiểm (người quản lý Box builder đưa). Mã chỉ được lưu trong phần cài đặt của script này.', ui.ButtonSet.OK_CANCEL);
  if (r.getSelectedButton() !== ui.Button.OK) return;
  const token = String(r.getResponseText() || '').trim();
  if (!token) { ui.alert('Box builder', 'Chưa nhập mã, giữ nguyên mã cũ.', ui.ButtonSet.OK); return; }
  if (!/^[\x21-\x7e]{1,200}$/.test(token)) { ui.alert('Box builder', 'Mã chỉ gồm chữ, số và ký hiệu, không có dấu cách hay xuống dòng. Chưa lưu.', ui.ButtonSet.OK); return; }
  PropertiesService.getScriptProperties().setProperty(CONFIG.TOKEN_PROPERTY, token);
  ui.alert('Box builder', 'Đã lưu mã.', ui.ButtonSet.OK);
}

// ------------------------------------------------------------------ the only place that writes into the sheet
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

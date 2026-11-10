# Box builder

A shared web page for building robot-scenario boxes from the object catalog. Several people open the same
page, each generates their own boxes, and **an object can only ever sit in one box**. The page shows live who
holds what.

## Run it

```bash
cp .env.example .env          # add DEEPSEEK_API_KEY (optional, see below)
docker compose up -d --build
```
On Windows just double-click `start.bat` (uses Docker if installed, otherwise Python). Open `http://<this-machine>:8080`. Teammates on the same network use the same address.

Without Docker: `pip install -r requirements.txt` then
`DB_PATH=data/boxes.db CATALOG_PATH=data/catalog.csv uvicorn app.main:app --host 0.0.0.0 --port 8080`.

With no `DEEPSEEK_API_KEY` the page runs on offline test picks (a keyword matcher, not a model) so you can try
the whole flow for free. The picks are not meaningful. Add the key for real selections.

## Using it

1. **New box**: module (M8), box name (Box A1), a one-line setting (or press a preset), Generate.
2. Each box is a tray with one cut-out per object (STT 1 to 21, read down the columns like the sheet).
   - **Lock** an object to keep it when you press **Regenerate**; **Replace** swaps one object; **x** removes it.
   - The quantity chip cycles 1, 2, 3 (never above the object's stock).
   - **Add an object** by name or ID with the search box under the tray (it is locked automatically).
3. **Approve** when the box is final; objects stay reserved. **Reopen** to edit again. **Discard** releases them.
4. **Copy IDs** and **Copy quantities** give 21 lines each, in STT order, ready to paste into the Object ID
   and SL columns. Set the Object ID cells to *Plain text* first so `004702` keeps its zeros.
5. **Already used in the sheet**: paste IDs that are already in your workbook so new boxes avoid them.
6. **Export CSV** (top right) lists every box and object.

## How duplicates are prevented

- Claiming objects happens inside one database transaction, and the database has a UNIQUE index on the object
  ID, so two boxes cannot hold the same object even if two people click in the same millisecond.
- Boxes pick one at a time (queue), so each pick already sees what the others took. No model call is wasted on
  clashing choices.
- Whatever the model answers is checked by code: unknown IDs, repeats and objects held elsewhere are dropped,
  quantity is capped, and missing slots are re-requested (twice) and then filled by code. Boxes filled by code
  are marked with a dashed edge.
- Drafts nobody touches for `DRAFT_TTL_HOURS` release their objects. Approved boxes never expire.

## Sending approved boxes to SM TaskBox

`GET /api/taskboxes` returns every **approved** box in the exact shape the SM TaskBox userscript builds from
the sheet (title `M8-01 - Large object - A1`, anchor, contents, quantities). Userscript v2.20.0
(`sm-userscript/`) accepts this page's link in its existing source box (paste `https://your-app/` instead of the
Google Sheet link; add `?access_code=CODE` if you set one), then the usual Scan / Sync buttons work unchanged.
Fill **Section code** and **Large object** when creating a box so the TaskBox title comes out right.

## Put it online (Neon + Render + Vercel)

Everything runs from one Render service (it serves the page and the API). Neon keeps the shared ledger so
nothing is lost when Render sleeps or redeploys. Vercel is optional: it gives you a short, always-up address
that forwards to Render.

1. **Neon** (database). Create a project, then copy the **pooled** connection string (host contains `-pooler`,
   ends with `?sslmode=require`). Tables are created automatically on first start.
2. **GitHub**. Push this folder to a **private** repo (`data/catalog.csv` is your inventory).
3. **Render**. New > Blueprint > pick the repo (it reads `render.yaml`). Paste `DATABASE_URL` (the Neon string)
   and, when asked, `DEEPSEEK_API_KEY` and an optional `ACCESS_CODE`. Free instances sleep after about 15
   minutes idle, so the first visit after a break takes up to a minute; the data is safe in Neon.
4. **Vercel** (optional). Import the same repo. In `vercel.json` replace `box-builder.onrender.com` with your real
   Render address, then deploy. Leave the framework preset on *Other*.
5. Open the address, create a box. With no DeepSeek key the page uses offline test picks.

Same code, three ways to store: a file (`DB_PATH`), Docker volume, or Neon (`DATABASE_URL`). If `DATABASE_URL`
is set it wins.

## Live catalog from Scenario Manager

SM has no export, so the SM TaskBox userscript (v2.21+, signed in as you) reads every object from `/api/objects`
and pushes a compact list to `POST /api/catalog/sync`. The page then picks only from real stock (`available`,
which already subtracts borrowed items) and shows "Kho SM: cap nhat X phut truoc". Rules: type Obj, ACTIVE, has a
Vietnamese name, available above 0. Categories still come from `data/catalog.csv` (SM has no category field);
new objects show as "Chua phan loai".

- Set `SYNC_TOKEN` on the server (Render Environment tab). Without it the endpoint is closed (503).
- In the userscript panel enter the app link and the same token, press "Dong bo kho ngay", and tick the auto box
  to refresh every 10 minutes while an SM tab is open. A list shorter than 50 items is refused, and the
  userscript only sends when every page downloaded.
- The snapshot is stored in the database, so a restart keeps the last list.

## The catalog

`data/catalog.csv` is your sheetsync export. Rules: status ACTIVE, has a Vietnamese name, quantity above 0
(1,681 usable of 1,704 rows). Replace the file at any time; the page reloads it automatically.

## Limits

- The page does not write to Google Sheets. It gives you the IDs to paste. A Make webhook that writes the
  cells is the natural next step.
- Quality of real selections depends on the model and the setting text; judge a few boxes before relying on it.
- Run a single server process (the default). The database file lives in `data/`, back it up like any file.

## Objects view and automations

Three tabs: **Hộp** (the boxes), **Kho đồ** (the catalog) and **Tự động hoá** (switches and queues).

**Kho đồ.** Search by name or ID, filter by category, sort, and see the status of every object: free, in a draft box,
approved, pushed to SM, or already used in the workbook. Bars show how much of each category is still free. Tick free
objects and add them to a draft box in one go (they are locked, so Regenerate keeps them).

**Tự động hoá.** Each switch is stored in the database and applies to everyone:

| Switch | What it does | Default |
|---|---|---|
| Tạo nhiều hộp bằng AI | One brief becomes several box proposals. You review and edit the plan, then they are built one at a time, no duplicates. With no `DEEPSEEK_API_KEY` the plan just splits your lines. | on |
| Tự duyệt hộp khi đã chọn đủ | A freshly built box is approved when the model filled every slot. A box with any code-filled slot always waits for a person. Regenerate and hand edits are never auto-approved. | **off** |
| Cấp danh sách cho bảng tính Google | `/api/sheet-boxes` serves approved boxes to the sheet script. Off = it gets an empty list. | on |
| Theo dõi kho đồ | Alerts: catalog never synced or older than N hours, objects in boxes that SM no longer lists, categories running low. | on |
| Tự thay món biến mất khỏi kho SM | After a catalog sync, objects SM no longer lists are swapped out of boxes that are not in SM yet. The other objects stay. An approved box goes back to Nháp for a person to approve again, unless Tự duyệt is on. Boxes already pushed are never touched. | **off** |
| Hàng chờ đẩy lên SM | `/api/taskboxes?pending=true` (what the userscript reads) only returns approved, not-yet-pushed boxes that pass checks: every object still in stock, quantity within the cap, title not used by another box. Blocked boxes are listed with the reason. Off = the userscript sees nothing. | on |

**Việc cần làm.** The top of the Tự động hoá tab lists the steps that still need a person: drafts to approve,
the SM push to confirm, the sheet script to run, a catalog sync. The app cannot do these itself (the rules keep SM and
the sheet out of its reach), so it says which one is waiting. The number on the tab counts these plus the alerts.

**Sections.** A tab in the sheet holds several sections (M8-01, M8-02, ...), each with its own Box B2, Box B3 and Box A1.
In the batch form, "Một section: B2, B3, A1" fills the three names and "Mã section" takes the code (for example M8-02).
The sheet script then writes that section's rows (25 rows per section) and checks first that the block title is
really there, so a tab with different spacing is skipped, not written to the wrong place.

**One-click batch.** In "Tạo nhiều hộp bằng AI", tick "Duyệt luôn..." to approve each box as soon as the model has filled
every slot. Only that batch is affected, and boxes with code-filled slots still wait for a person.

**Pictures.** Each object row can carry a picture URL. The userscript reads it from SM's object list and sends it with the
catalog sync (any of the keys `image_url`, `img`, `thumbnail`, `image`, `photo`, `images`...; see `app/images.py`). The server
keeps it only if it is an https URL on an allowed host (`IMAGE_HOSTS`, default the SM host) or a path on that host. It never
fetches the picture: the viewer's browser loads it, so a picture that needs an SM login shows only for people signed in to SM.
A missing or broken picture shows a neutral icon. Every object also has an "Mở trong SM" link to its SM page.

Safety is unchanged: the server never writes to SM and holds no SM credentials (`tests/test_safety.py` enforces it).
The userscript still creates TaskBoxes only after your confirm click; the sheet script still writes only empty cells.
A short activity log shows what the automations did.

Browser test: `pip install playwright && python -m playwright install chromium && python tests/ui_automation_e2e.py`.

## Tests

`pip install -r requirements-dev.txt && pytest` (SQLite, 17 tests; point `TEST_DATABASE_URL` at a Postgres to run the same suite there: simultaneous boxes, bad model output, stock cap,
workbook IDs, locks, approval, expiry, access code, DeepSeek client against a fake API).

## Full automation chain (Hanoi-GPI only, nothing in SM is ever edited or deleted)

1. **Available objects.** The userscript (signed in to SM) reads `/api/objects`, keeps only the sector in its "Sector"
   box (default `Hanoi-GPI`) and sends them to the app with the `SYNC_TOKEN`. The app drops anything outside
   `SECTORS` (env, default `Hanoi-GPI`) again on its side, so other sectors are never stored.
2. **Boxes.** The team builds boxes on the page; nothing is duplicated (database-level guarantee).
3. **Google Sheet.** Paste `integrations/google_sheets_apps_script.gs` into the sheet's Apps Script. A menu fills the
   approved boxes into the block of the matching tab. It writes only into completely empty cells, re-checks right
   before writing, never overwrites, and shows a preview first. Timer is optional (same rules).
4. **Push to SM.** In the userscript, paste the app link in the source field and press "Quét dữ liệu", review the table,
   then press "Đồng bộ". For boxes from the app it is create-only: it never updates a box that already exists in SM.
   After each confirmed create it tells the app (`POST /api/boxes/{id}/pushed`, needs `SYNC_TOKEN`), the box shows
   "Đã đẩy lên SM", and it is skipped from then on, even on later days. A pushed box cannot be reopened or discarded
   in the app. An admin can undo the mark with `DELETE /api/boxes/{id}/pushed` and the sync token.

No SM password or token is stored on the server. The only secret the server holds for SM is the shared `SYNC_TOKEN`,
which can only replace the catalog snapshot and mark boxes as pushed.

Tests: `python -m pytest tests` (add `TEST_DATABASE_URL=postgresql://...` to run them on Postgres too),
`node tests/apps_script_test.js` for the sheet script.

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

## The catalog

`data/catalog.csv` is your sheetsync export. Rules: status ACTIVE, has a Vietnamese name, quantity above 0
(1,681 usable of 1,704 rows). Replace the file at any time; the page reloads it automatically.

## Limits

- The page does not write to Google Sheets. It gives you the IDs to paste. A Make webhook that writes the
  cells is the natural next step.
- Quality of real selections depends on the model and the setting text; judge a few boxes before relying on it.
- Run a single server process (the default). The database file lives in `data/`, back it up like any file.

## Tests

`pip install -r requirements-dev.txt && pytest` (SQLite, 17 tests; point `TEST_DATABASE_URL` at a Postgres to run the same suite there: simultaneous boxes, bad model output, stock cap,
workbook IDs, locks, approval, expiry, access code, DeepSeek client against a fake API).

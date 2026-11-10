# Box builder: handoff (state on 2026-11-10, last commit 8c40d3a)

Written so the work can continue on another laptop. Read this first, then `docs/SC_PROCESS.md` (the daily process the app serves),
`docs/WRITING.md` (how the Vietnamese text must read) and `docs/SM_SYNC.md` (the SM sync tool).

## 1. What this is
Box builder helps a Scenario Creator (SC) make the practice boxes B2, B3 and A1 for Scenario Manager (SM) TaskBoxes. It picks objects from the
SM kho, makes sure no object sits in two boxes, lets a person check and approve, and then the approved boxes go (a) into the Google sheet
(through a script that lives in the sheet) and (b) into SM as TaskBoxes (through a Tampermonkey script in the person's own signed-in SM tab).

- Code: GitHub `PCBoiz/box-builder`, branch `main`. Live: https://box-builder-guwc.onrender.com (Render, free plan, auto-deploys on every push, about 40 to 60 s). Database: Neon Postgres through `DATABASE_URL` set in Render. No login (the team decided; see section 8).
- Stack: FastAPI plus vanilla JS (no build step). SQLite for a local run, Postgres when `DATABASE_URL` is a postgres URL. Tests: pytest, a Node test for the sheet script and the thumbnails, Playwright (Firefox) browser tests with axe-core.

## 2. Hard rules from the user (do not break them)
- The server never writes, edits or deletes anything in SM and holds no SM credentials. SM is reached only from the user's own signed-in browser through the userscript: read-only for the catalog and the boxes, create-only plus a confirm click for TaskBoxes. The "fake SM" (`SM_PUSH_MODE=mock`) is in-memory code only; there is no real mode.
- Only the Hanoi-GPI sector is used. Other sectors are dropped, never stored. Nothing is deleted in SM.
- Never enter or ask for passwords or tokens. The person types their own sync code into the tool panel. Do not touch the user's other Render services (`pe-hsa-backend`, `pe_hsa`).
- No ACCESS_CODE or login (internal team tool, decided by the team). Do not add or propose one without being asked.
- UI text is plain Vietnamese for people with little technical skill (`docs/WRITING.md`). The user writes English, the app speaks Vietnamese.
- No commit or push unless the user asks. Before any push: all checks green, an independent review for anything substantial, a secret scan of the diff. After a push, verify the live site read-only.
- Nothing is written to the Google sheet, COS or Render by Claude; the sheet is filled by its own script after the person confirms.

## 3. Where things are
| Path | What it holds |
|---|---|
| `app/main.py` | all routes and middleware (cross-site write check, sync-token routes) |
| `app/service.py` | domain logic: boxes, the claim ledger, catalog statuses, SM snapshot, reuse rule, backup, CSV import |
| `app/db.py` | SQLite/Postgres layer, global write lock, UNIQUE index (an object is in one box), `executemany` batches |
| `app/catalog.py`, `app/images.py` | the live kho snapshot, picture links and their expiry |
| `app/picker.py` | the LLM pick (DeepSeek or a mock) and the code that fills slots |
| `app/automation.py` | switches: ai_batch, auto_approve, sheet_export, catalog_watch, **reuse_old**, auto_heal, sm_queue; alerts and the to-do list |
| `app/sm_client.py`, `app/sm_push.py` | the fake SM and the push pipeline against it |
| `app/printview.py` | printable pick list (`/print/boxes`) |
| `app/static/` | `index.html`, `app.js`, `app.css`, `thumbs.js`, `thumbs.css` (three tabs: Hộp, Kho đồ, Tự động hoá) |
| `integrations/box_builder_sm.user.js` | Tampermonkey tool v0.2.0 (catalog sync, read SM boxes, push with confirm); served by the app |
| `integrations/google_sheets_apps_script.gs` | the sheet script (fill approved boxes into empty cells, read used IDs) |
| `data/catalog.csv` | the kho as a CSV: also the only source of Vietnamese names (SM sends none) |
| `tests/` | pytest files, `apps_script_test.js`, `thumbs_test.js`, and browser tests `ui_automation_e2e.py`, `userscript_sync_e2e.py`, `userscript_push_e2e.py` |
| `docs/handoff/` | `LOOP.md` (how the work loop ran), `kz.py` (client for the local Kanzo board), `sm_explore.py` and `sm_usage_probe.py` (read-only SM look scripts) |

Settings (environment variables, see `app/settings.py`): `DATABASE_URL` or `DB_PATH`, `CATALOG_PATH`, `LLM_MODE`, `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`,
`DEFAULT_COUNT`, `STAGE_COUNTS` (A1 12, B2 21, B3 21), `MAX_QTY`, `DRAFT_TTL_HOURS`, `MAX_PARALLEL_LLM`, `ACCESS_CODE` (unused), `SYNC_TOKEN`, `SECTORS`,
`SM_PUSH_MODE` (`mock` or off), `IMAGE_HOSTS`, `IMAGE_TTL_MINUTES` (default 55). Secrets (`DEEPSEEK_API_KEY`, `DATABASE_URL`, `SYNC_TOKEN`) live only in Render's Environment page.

## 4. Set up on a new laptop
```
git clone https://github.com/PCBoiz/box-builder.git && cd box-builder
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m playwright install firefox          # on Linux only Firefox works with the browser tests
# run the app locally with the fake SM and a throw-away database:
env -u DATABASE_URL DB_PATH=data/local-test.db CATALOG_PATH=data/catalog.csv SYNC_TOKEN=local-test-token SM_PUSH_MODE=mock \
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8080
```
You also need Node (for `tests/*.js`), `npm install axe-core` somewhere (point `AXE_JS` at `node_modules/axe-core/axe.min.js`), and for the Postgres run a local Postgres:
`initdb -D ~/pgdata-test -U pgtest --auth=trust`, `pg_ctl -D ~/pgdata-test -o '-p 5433 -c listen_addresses=127.0.0.1 -k /tmp' start`, `createdb -h 127.0.0.1 -p 5433 -U pgtest boxtest`.

## 5. The checks (run all before a push; counts as of this commit)
```
.venv/bin/python -m pytest tests -q                                   # 534 pass on SQLite
TEST_DATABASE_URL=postgresql://pgtest@127.0.0.1:5433/boxtest .venv/bin/python -m pytest tests -q    # 695 pass on Postgres
node tests/apps_script_test.js && node tests/thumbs_test.js           # both print OK
env -i HOME=$HOME PATH=/usr/bin:/bin LANG=C.UTF-8 AXE_JS=/path/to/axe.min.js .venv/bin/python -I tests/ui_automation_e2e.py        # 141 checks
env -i HOME=$HOME PATH=/usr/bin:/bin LANG=C.UTF-8 .venv/bin/python -I tests/userscript_sync_e2e.py     # 42
env -i HOME=$HOME PATH=/usr/bin:/bin LANG=C.UTF-8 .venv/bin/python -I tests/userscript_push_e2e.py     # 69
```
Browsers started from a shell inside VS Code's snap need the clean `env -i` environment (add `DISPLAY` and `WAYLAND_DISPLAY` when a window is needed); otherwise they hang or crash.

## 6. What is done and live (six pushes: 4629348, 996e5fc, 597c8bc, a9bbf97, bb45ee4, 8c40d3a)
- Box making: AI batch, hand-pick from the Kho đồ table, stage sizes, anchor-aware picking, categories, locks, replace, approve, discard, section codes (M8-02), printable pick list, copy IDs and quantities.
- Kho from SM: the Tampermonkey tool reads SM objects (Hanoi-GPI only), keeps the Vietnamese names from `data/catalog.csv`, guards against an empty or half kho. First real sync done: 1,533 usable objects.
- SM boxes read: the tool reads active boxes (id, title, status, created date, object ids and quantities, never a person) so objects in SM boxes are not picked again. Done live: 288 boxes, 1,362 objects held; free 396.
- Fake SM: push pipeline and the other SM steps against an in-memory mock; the tool's push (create-only, confirm click) is tested on a mock SM page. The real push has not been tried: first real test must be ONE throwaway box.
- Backup export and import (JSON), CSV import of boxes (drafts only), used-IDs list for the sheet, sheet script (fill empty cells, show used IDs), install of the tool (link or copy-paste page `/box_builder_sm.txt`).
- Reuse rule (switch "Dùng lại món của hộp cũ", on, 30 days): an object held only by an SM box made more than N days ago, or listed as used in the sheet with a day older than N, is free again. SM dates the app cannot read keep the box holding. IDs pasted with no day are never reused.
- Picture links from SM expire after about an hour: the page shows a placeholder and one tip instead of broken thumbnails; the print list leaves them out.
- Speed fix: saving hundreds of SM boxes was one database statement per row (over a minute on a hosted database); now batched (0.6 s).

## 7. What is next (the board is local to the old laptop; these are the cards in order)
1. **Usage visual and a softer pick rule (card 42, the user's latest request).** Show how many times each object was borrowed (SM shows it as LIFETIME USE, for example "1587 borrows at least, 1611 items total, Heavily used"). From 500 up an object should be picked less, and at a high number left out. Known: SM objects carry `_usage_count`, `_activeness_score`, `_inventory`, `last_activity`; the shape of `_usage_count` is not known yet. Run `docs/handoff/sm_usage_probe.py` once (a Firefox window opens, the person signs in, GET only, stores only numbers and shapes) to get the shape and a histogram, then: send `uses` in the catalog sync (bump the userscript version, the user must update it), store it, show a bar column, a band chart and a sort in Kho đồ, add a switch with two numbers (start reducing at 500, leave out at a number chosen from the histogram; 0 = never leave out), apply it to the AI shortlist and the code filler. Do not starve the pool: only 396 of 1,533 objects are free today.
2. **User page (card 43, the user's latest request).** The current app is the manager page (the Tự động hoá tab says so). Build a separate simple page for people with little technical skill: no tabs, no settings, three big steps (Tạo hộp, Xem và duyệt, Lấy đồ), a progress strip, large text, plain Vietnamese, one confirmation plus undo for approve, one-click copy of IDs and quantities, print. Same data and API as the manager page. Acceptance: phone width, keyboard, axe clean, a browser test that makes, approves and copies a box using only this page, a test that no manager control appears, an independent review. Confirm the route (`/dung`, or the root with the manager page under `/quan-ly`) with the user first.
3. **Link the Google sheet copy and test one fill (card 44).** The user shared a copy of the workbook (title "Bản sao của Shift B Objects Preparation (IHDC) v4"; ask for the link). Claude can read it through the Drive connector but there is no Sheets editing tool in the chat. Either the user turns on the Google Sheets connector (the + next to the message box, Connectors) or pastes `integrations/google_sheets_apps_script.gs` into Extensions, Apps Script. Test plan: block M5 section 7 Box B2 is empty (rows 150 to 170, IDs in column E, quantities in H); create one approved test box (module M5, code M5-07, label Box B2, 21 items); run the Box builder menu preview, it must say `GHI M5 / Box B2 (section 7) → E150:E170 & H150:H170`; confirm; check; discard the test box. Run "Lấy ID đã dùng trong bảng": 1,237 IDs expected.
4. **Card 41**: show how old the active SM boxes are (bands) so the reuse days can be chosen with numbers in sight. On live, with 30 days only 6 of 288 boxes are old, so only 6 objects come free; the user will likely try 7 or 14 days.
5. Then: 17 click an ID to copy; 37 Vietnamese names from SM (needs a read-only look at SM's EN/VI toggle); 19 to 22 units per module, day planner, end-of-day report, schedule strip; 30 day runner on the fake SM; 40 optional picture refresh in the tool (needs the user to update the userscript).
6. Follow-ups not on the board yet: retire old boxes made in this app (the UNIQUE index keeps their objects reserved for ever); a used-IDs dialog in the sheet script that lists IDs per tab so each tab can be pasted with its own day.

## 8. Decisions and risks waiting for the user
- **The GitHub repository is public** and it tracks `data/catalog.csv` (the inventory: about 1,700 objects with shelf slots and quantities) since the first upload; the README says it should be private. Making it private may break Render's auto-deploy if the service was connected by public URL; check Render's connection first. Nothing secret is in the repo (scans before every push), but the inventory and all code are visible.
- **The live app has no login and Render is on the public internet.** Anyone with the link can read and change boxes, and `/api/objects` carries SM's temporary signed picture links (about an hour). The team decided on no access code because the tool is internal; the user should confirm that this holds now that SM data is on the live site.
- Reuse rule: default days (30 is conservative; 7 or 14 free more), and whether the same module or shift should wait longer.
- Whether to load the sheet's 1,237 used IDs into the live app. If all are marked used, only about 432 of 1,540 objects stay free. With the reuse rule each paste can carry its own day, so paste tab by tab with the right day.
- Cards 2, 12, 13 wait on the user's team script v2.23.0; card 3: the COS password was typed in a chat earlier, treat it as exposed and change it; card 6: M8 names or IDs; card 14: should the app push to SM itself (current answer: no, browser only); card 23: confirm the SC rules and the TaskBox name format.

## 9. Lessons that cost time (do not repeat)
- Tampermonkey on Firefox can cancel the install link and show a blank page without opening its install tab; the copy-paste route (`/box_builder_sm.txt`) always works. If the script shows "Cài lại" it is already installed.
- On a hosted database every statement is a round trip: use `Conn.executemany`. Postgres `REAL` is 4 bytes: epoch seconds need `DOUBLE PRECISION` (the migration code picks the type per engine).
- Dates in tests must be relative to now (fixed dates age into failures once the reuse rule looks at them). The fake SM must be given a real clock.
- SM facts: objects `GET /api/objects?page=N` (30 per page, `total_pages`), boxes `GET /api/boxes?limit=500&page=N`, `created_at` is ISO and readable, picture links are signed S3 links with `Expires=<epoch>`, a box's contents carry `borrows`, `borrow_units`, `borrows_undercounted`. A TaskBox is created by a same-origin `POST /api/boxes`. The same object can sit in several SM boxes at once (quantities), the SC rule is stricter than SM.
- Review pattern that worked well: a workflow of three read-only reviewers (invariants, data, person using it) whose findings are each challenged by a skeptic; every confirmed finding then got a test. Skeptics may read code that was already fixed, so recheck each confirmed finding against the current code.
- `pkill -f` inside a shell command can kill the shell itself; kill by PID. Never put secrets, the sheet link, internal addresses or account names in the public repo.

# Box builder: handoff (state on 2026-11-11, after the work of that day: commits 5dc0ef0 and 86b0838, plus the pre-trial polish)

Written so the work can continue on another laptop. **Start with `docs/LAM_TIEP.md`** (Vietnamese: everything done on 2026-11-11, the owner's decisions and the open tasks in order), then read this, then `docs/SC_PROCESS.md` (the daily process the app serves),
`docs/WRITING.md` (how the Vietnamese text must read), `docs/SM_SYNC.md` (the SM sync tool) and `docs/LOGIN.md` (login by company ID, for the owner).

**The team already has its own Tampermonkey tool** (v2.23.0, "Sondeptraidatimthayban", kept in the owner's own GitHub repo, runs on every `sm.config.inc` page).
It is the real way boxes reach SM: it reads approved boxes from `GET /api/taskboxes?status=approved`, creates them in SM with `POST /api/boxes`,
assigns them, reports them with `POST /api/boxes/<id>/pushed` (sync token) and sends the catalog to `POST /api/catalog/sync` (sync token). The sheet's
Apps Script reads `GET /api/sheet-boxes`. None of these can log in, so **every change to the app must leave those routes working exactly as they were**
(`app/auth.py` lists them in `MACHINE_EXACT`; `tests/test_auth_app.py` fails if the list drifts). Older copies of this project under the owner's work
folder are early snapshots, not newer work.

## 1. What this is
Box builder helps a Scenario Creator (SC) make the practice boxes B2, B3 and A1 for Scenario Manager (SM) TaskBoxes. It picks objects from the
SM kho, makes sure no object sits in two boxes, lets a person check and approve, and then the approved boxes go (a) into the Google sheet
(through a script that lives in the sheet) and (b) into SM as TaskBoxes (through a Tampermonkey script in the person's own signed-in SM tab).

- Code: GitHub `PCBoiz/box-builder`, branch `main`. Live: https://box-builder-guwc.onrender.com (Render, free plan, auto-deploys on every push, about 40 to 60 s). Database: Neon Postgres through `DATABASE_URL` set in Render.
- Two pages: `/` is the admin page (approve, catalog, automations, people), `/dung` is the page for people who make boxes (create, look, copy, print; no Approve). Login by company ID exists since 2026-11-11 and is **off until the owner sets `ADMIN_IDS` in Render** (see `docs/LOGIN.md`); with it unset the app is open as before.
- Stack: FastAPI plus vanilla JS (no build step). SQLite for a local run, Postgres when `DATABASE_URL` is a postgres URL. Tests: pytest, a Node test for the sheet script and the thumbnails, Playwright (Firefox) browser tests with axe-core.

## 2. Hard rules from the user (do not break them)
- The server never writes, edits or deletes anything in SM and holds no SM credentials. SM is reached only from the user's own signed-in browser through the userscript: read-only for the catalog and the boxes, create-only plus a confirm click for TaskBoxes. The "fake SM" (`SM_PUSH_MODE=mock`) is in-memory code only; there is no real mode.
- Only the Hanoi-GPI sector is used. Other sectors are dropped, never stored. Nothing is deleted in SM.
- Never enter or ask for passwords or tokens. The person types their own sync code into the tool panel. Do not touch the user's other Render services (`pe-hsa-backend`, `pe_hsa`).
- Login: the team first decided on no login; on 2026-11-11 the owner asked for login by company ID (admin-managed list, admin role by ID for watching, people approve their own boxes on `/dung`), so it exists now. Keep its safety properties: **off until `ADMIN_IDS` is set** (a deploy must never lock the admin out), **default deny** for any route not listed in `app/auth.py`, the machine routes above never gated, only a hash of the session token stored, the typed ID of a failed login never stored. An ID is a name tag, not a lock: say so in the UI and docs, do not oversell it. `ACCESS_CODE` (a shared code) still works as before and is separate.
- UI text is plain Vietnamese for people with little technical skill (`docs/WRITING.md`). The user writes English, the app speaks Vietnamese.
- No commit or push unless the user asks. On 2026-11-11 the owner asked to go to production the same day: still only after all checks are green, an independent review, and a secret scan of the diff. After a push, verify the live site read-only. The sheet link, the sheet ID, company IDs and internal addresses never go in this public repo.
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
| `app/static/` | `index.html`, `app.js`, `app.css`, `thumbs.js`, `thumbs.css` (admin page, three tabs: Hộp, Kho đồ, Tự động hoá); `usage_chart.js/css` (the chart in Cảnh báo kho); `login.html/js/css`; `dung.html/js/css` (the user page) |
| `app/auth.py` | login by company ID: sessions (hash only), delay after failures (database counters), the gate (`klass()` = public / machine / user / admin, default deny), the people list, audit |
| `app/user_routes.py` | `/dung`, `/api/my/*`, `/my/print`: a person sees and changes only the boxes that carry their ID, and approves their own; the day plan, the report and the themes made lately |
| `app/sheet_panel.py` | the "Bảng tính Google" box: sheet link (kept only when login is on, never in the repo), when the sheet script last asked, the script with this app's address |
| `app/usage.py` | borrow counts (LIFETIME USE): weight formula, bands, top list, pool floor, weighted sampling; used by `picker.py` and `service.usage_report` |
| `integrations/box_builder_sm.user.js` | Tampermonkey tool v0.3.0 (catalog sync with `uses` and `created`, read SM boxes, push with confirm; also loads on `/object`); served by the app |
| `integrations/google_sheets_apps_script.gs` | the sheet script (fill approved boxes into empty cells, read used IDs) |
| `data/catalog.csv` | the kho as a CSV: also the only source of Vietnamese names (SM sends none) |
| `tests/` | pytest files, `apps_script_test.js`, `thumbs_test.js`, and browser tests `ui_automation_e2e.py`, `userscript_sync_e2e.py`, `userscript_push_e2e.py` |
| `docs/handoff/` | `LOOP.md` (how the work loop ran), `kz.py` (client for the local Kanzo board), `sm_explore.py` and `sm_usage_probe.py` (read-only SM look scripts) |

Settings (environment variables, see `app/settings.py`): `DATABASE_URL` or `DB_PATH`, `CATALOG_PATH`, `LLM_MODE`, `DEEPSEEK_API_KEY`, `DEEPSEEK_MODEL`,
`DEFAULT_COUNT`, `STAGE_COUNTS` (A1 12, B2 21, B3 21), `MAX_QTY`, `DRAFT_TTL_HOURS`, `BOX_DONE_DAYS` (default 1; days after which an approved box is finished and its objects are free), `MAX_PARALLEL_LLM`, `SYNC_TOKEN`, `SHEET_TOKEN` (the sheet script's own secret: it can only record the supporters' check), `ENABLE_DOCS=1` (turns the FastAPI docs back on; off by default), `ALLOWED_APP_HOSTS` (hosts besides this one that the tool's `?app=` link may name), `SECTORS`,
`SM_PUSH_MODE` (`mock` or off), `IMAGE_HOSTS`, `IMAGE_TTL_MINUTES` (default 55), `ADMIN_IDS` (company IDs of the admins, comma list; empty = login off), `ADMIN_CODE` (optional second secret an admin types), `USER_IDS` (ordinary users who may sign in before any list is pasted), `USER_PASSWORD` (optional shared password of ordinary users; changing it signs everyone out).
Secrets (`DEEPSEEK_API_KEY`, `DATABASE_URL`, `SYNC_TOKEN`, `SHEET_TOKEN`, `ADMIN_CODE`, `USER_PASSWORD`) live only in Render's Environment page, never in the repo (it is public; if `render.yaml` ever declares `USER_PASSWORD` it must use `sync: false`, never `value:`). See `docs/LOGIN.md`.

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

## 5. The checks (run all before a push; counts as of 2026-11-11)
Linux or macOS:
```
.venv/bin/python -m pytest tests -q                                   # 880 pass on SQLite, also on Python 3.12 (534 before 2026-11-11)
TEST_DATABASE_URL=postgresql://pgtest@127.0.0.1:5433/boxtest .venv/bin/python -m pytest tests -q    # 695 passed before 2026-11-11; NOT run since (see section 7, item 6)
node tests/apps_script_test.js && node tests/thumbs_test.js && node tests/usage_chart_test.js     # all print OK
env -i HOME=$HOME PATH=/usr/bin:/bin LANG=C.UTF-8 AXE_JS=/path/to/axe.min.js .venv/bin/python -I tests/ui_automation_e2e.py        # 141 checks
env -i HOME=$HOME PATH=/usr/bin:/bin LANG=C.UTF-8 AXE_JS=/path/to/axe.min.js .venv/bin/python -I tests/admin_e2e.py                 # 48 (login, people, sheet box, chart, age tables, borrow column, names per day)
env -i HOME=$HOME PATH=/usr/bin:/bin LANG=C.UTF-8 AXE_JS=/path/to/axe.min.js .venv/bin/python -I tests/dung_e2e.py                  # 647 (the user page: login with a password, boxes, the day panel, days of a box, plain wording)
env -i HOME=$HOME PATH=/usr/bin:/bin LANG=C.UTF-8 AXE_JS=/path/to/axe.min.js .venv/bin/python -I tests/usage_chart_e2e.py           # 95
env -i HOME=$HOME PATH=/usr/bin:/bin LANG=C.UTF-8 .venv/bin/python -I tests/userscript_sync_e2e.py     # 90
env -i HOME=$HOME PATH=/usr/bin:/bin LANG=C.UTF-8 .venv/bin/python -I tests/userscript_push_e2e.py     # 69
```
Windows (PowerShell, as run on 2026-11-11): `.venv\Scripts\python.exe -m pytest tests -q -p no:cacheprovider`, and `$env:AXE_JS="<path>xe.min.js"; .venv\Scripts\python.exe -I testsdmin_e2e.py` for each browser test. They start their own server on their own port with `sys.executable -m uvicorn`. They need `pip install playwright` and `python -m playwright install chromium`; axe-core comes from `npm install axe-core`.
Before a commit: `git status --short` must show no `??` under `app/`, `docs/`, `tests/` or `integrations/` (new modules such as `app/auth.py` are imported by `app/main.py`; a commit that leaves one out cannot start). Run the secret scan, then `git add -A app docs tests integrations Dockerfile README.md`, and check the committed tree from a fresh clone: `python -c "import app.main"`.
The suite also ran green on Python 3.12 (the version Render uses) in a second virtual environment. Browsers started from a shell inside VS Code's snap on Linux need the clean `env -i` environment (add `DISPLAY` and `WAYLAND_DISPLAY` when a window is needed); otherwise they hang or crash.

## 6. What is done (six pushes up to 8c40d3a, plus the 2026-11-11 work: see the git log for the commit that carries it)
- Box making: AI batch, hand-pick from the Kho đồ table, stage sizes, anchor-aware picking, categories, locks, replace, approve, discard, section codes (M8-02), printable pick list, copy IDs and quantities.
- Kho from SM: the Tampermonkey tool reads SM objects (Hanoi-GPI only), keeps the Vietnamese names from `data/catalog.csv`, guards against an empty or half kho. First real sync done: 1,533 usable objects.
- SM boxes read: the tool reads active boxes (id, title, status, created date, object ids and quantities, never a person) so objects in SM boxes are not picked again. Done live: 288 boxes, 1,362 objects held; free 396.
- Fake SM: push pipeline and the other SM steps against an in-memory mock; the tool's push (create-only, confirm click) is tested on a mock SM page. The real push has not been tried: first real test must be ONE throwaway box.
- Backup export and import (JSON), CSV import of boxes (drafts only), used-IDs list for the sheet, sheet script (fill empty cells, show used IDs), install of the tool (link or copy-paste page `/box_builder_sm.txt`).
- Reuse rule (switch "Dùng lại món của hộp cũ", on, 30 days): an object held only by an SM box made more than N days ago, or listed as used in the sheet with a day older than N, is free again. SM dates the app cannot read keep the box holding. IDs pasted with no day are never reused.
- Picture links from SM expire after about an hour: the page shows a placeholder and one tip instead of broken thumbnails; the print list leaves them out.
- Speed fix: saving hundreds of SM boxes was one database statement per row (over a minute on a hosted database); now batched (0.6 s).

- 2026-11-11, box life and the day: an approved box is finished `BOX_DONE_DAYS` (1, first 2, lowered by the owner) Vietnam days after the day it was made (`service.retire_done`, the line is kept 30 days in `box_history`), so its objects come back; the person's day (`plans` table: units and shifts, `GET/PUT /api/my/plan`), the boxes to hand back this morning, the end-of-day report, the themes made lately (`GET /api/my/recent`). Rules: `docs/SC_PROCESS.md`.
- 2026-11-11: login by company ID (`docs/LOGIN.md`), the user page `/dung`, borrow counts (userscript v0.3.0, storage, chart in Cảnh báo kho, borrow column and filters in Kho đồ, the switch "Giảm món đã dùng nhiều" and the picker rule, carry-over of counts when a tool sends none), age bands and the "what if N days" table for SM boxes, the Google Sheet box on the Automations tab, the sheet script served with this app's address.

## 7. What is next (the Kanzo project `box-builder` on the laptop used on 2026-11-11 holds the cards; the old laptop's board is not there)
Done on 2026-11-11 (code and tests; nothing pushed until the owner's release step): card 41 age bands for SM boxes, card 42 borrow counts (userscript v0.3.0, storage, chart, column, switch, picker), card 43 the user page `/dung`, login by company ID, the sheet box. What still needs the owner's hands or a decision:
1. **Set `ADMIN_IDS` (and better `ADMIN_CODE`) in Render** after the deploy, then sign in, paste the people list (Tự động hoá, "Người dùng và quyền"), then paste the sheet link. Steps: `docs/LOGIN.md`. Until then the app is open as before.
2. **Update the userscript to v0.3.0** (or apply `docs/handoff/team_script_usage_patch.md` to the team script v2.23.0) and run one catalog sync. The real shape of SM's `_usage_count` and the creation date field names are **unverified**: the tool says plainly when it could not read a number and prints the shape SM sent ("Dạng SM gửi"). If it says so, send those two lines to the developer; `docs/handoff/sm_usage_probe.py` is the heavier, read-only alternative (Firefox window, the person signs in).
3. **Choose `hard_limit`** (leave out from N borrows; 0 = never) after looking at the chart and the band counts, and check the defaults (500, halving every 300, age effect 50%, floor 100 candidates). The formula is a design judgement, not calibrated; set the age effect to 0 to use the count alone.
4. **Try ONE box on the real SM** (checklist in `docs/SM_SYNC.md`, "Thử MỘT hộp trên SM thật"). The owner clicks; nobody else can sign in to SM.
5. **Test one fill of the sheet copy** (card 44). Read-only check done through Drive on 2026-11-11: M5 section 7 of Box B2 is empty (E150:E170, H150:H170), the menu preview should say `GHI M5 / Box B2 (section 7) → E150:E170 & H150:H170`, the used-IDs helper gives 1,237 IDs. Box B3 section 7 of M5 is NOT empty (5 IDs) and an A1 of 21 objects does not fit (row 21 holds a label): the script skips both. Paste the script (button "Sao chép script bảng tính"), run the preview, confirm, check, discard the test box.
6. **Run the Postgres test suite** (see section 5). It could not be run on the Windows laptop used on 2026-11-11 (no Postgres there, and downloading one was blocked), so the new tables (`people`, `sessions`, `login_fail`, `audit_log`) and the sheet/usage keys in `catalog_snapshot` were only checked on SQLite and by a reviewer reading the SQL.
7. Then: 17 click an ID to copy; 37 Vietnamese names from SM (needs a read-only look at SM's EN/VI toggle); 30 day runner on the fake SM; 40 optional picture refresh in the tool (needs the user to update the userscript).
8. Left over (small, none blocks the release): the user's print page prints a draft without a "NHÁP" line; the day list times are in `app/static/dung.js` (the owner set printing and the report to 9:00 to 10:00, B3 from 9:30 to the end of the shift); cache the session lookup of the login gate for a few seconds if the database is slow; `/api/my/summary` could filter by owner in SQL instead of building the whole state.
9. Follow-ups not on the board yet: a used-IDs dialog in the sheet script that lists IDs per tab so each tab can be pasted with its own day.

## 8. Decisions and risks waiting for the user
- **The GitHub repository is public** and it tracks `data/catalog.csv` (the inventory: about 1,700 objects with shelf slots and quantities) since the first upload; the README says it should be private. Making it private may break Render's auto-deploy if the service was connected by public URL; check Render's connection first. Nothing secret is in the repo (scans before every push), but the inventory and all code are visible.
- **Login by company ID is only as strong as the secrecy of the IDs** (4-digit IDs can be guessed: with about 40 valid IDs in 10,000, a random guess works about 0.4% of the time; the delay after failures slows this, it does not stop it). Decisions made on 2026-11-11: an admin-managed list (no PIN, no invite link), admin by ID, optional `ADMIN_CODE`. The research report (`reports/Giảm món và đăng nhập ID.md`, kept outside the repo in the owner's work folder) says the best low-friction upgrade is a one-time invite link per person; a person-chosen PIN conflicts with a literal "no passwords" policy, so that is the owner's call. Set `ADMIN_CODE`: an admin who can approve boxes is the one account worth protecting, because the team's script pushes approved boxes to SM.
- **People approve their own boxes** (owner's decision, 2026-11-11, corrected the same day: first it was built admin-only). The admin page only watches (`/api/admin/overview`, panel "Tiến độ các hộp") and can still approve, reopen or delete. The shared access code `ACCESS_CODE` was removed on the same day (internal tool); the app ignores that variable. A person's draft is deleted after `DRAFT_TTL_HOURS` (12 by default) without a touch: the user page says how long is left.
- The two sync tools (the team's v2.23.0 and `box_builder_sm.user.js`) can run side by side. Only the second (or the patched first) sends borrow counts; a sync that sends none keeps the last counts, and the chart says when SM last sent them. Pick one tool for the catalog sync when convenient.
- **The live app has no login until `ADMIN_IDS` is set, and Render is on the public internet.** Anyone with the link can read and change boxes, and `/api/objects` carries SM's temporary signed picture links (about an hour). The team decided on no access code because the tool is internal; the user should confirm that this holds now that SM data is on the live site.
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
- 2026-11-11, login: `app.include_router` in the FastAPI used here wraps routes in `_IncludedRouter` objects, so a test that lists `app.routes` must walk `original_router.routes` (see `tests/test_auth_app.py`). A function inside `create_app` is named `auth` (the shared access code), so the login module is imported as `login` in `main.py`. Starlette sets `Secure` on a cookie only if told to; the `__Host-` prefix needs `Secure`, `Path=/` and no `Domain`.
- 2026-11-11, sync: every catalog sync replaces the whole catalog, and the team script sends no borrow counts. Without the carry-over in `catalog_sync` one run of that script erased them. Whenever a field is added to the catalog, ask which tools send it.
- 2026-11-11, drafts: `service.expire` deletes drafts nobody touched for `DRAFT_TTL_HOURS`. That was invisible while one person made and approved a box; it matters once approval is a different person's job.
- 2026-11-11, tests: a browser test that checks "no failed requests" must name the refusals it provokes on purpose. A table that is not redrawn while a button inside it has focus needs an explicit redraw after the focus moves (a deleted row stayed on screen until the e2e caught it). axe `label-content-name-mismatch`: an `aria-label` must contain the visible text of the button.
- 2026-11-11, second review: a name that is unique for the 2 days a box lives lets a unit make its B2 only every second day (now unique per Vietnam day, `boxes.day`). A route that needs no login must never carry a company ID: an ID is the login (`/api/taskboxes` lost `builder.owner` and ignores `status`; people list and log are empty while login is off). Free text from people reaches the team script, which writes it as HTML: `service.create_box` refuses `< > " ' ` and control characters (`service.has_markup`); the script should still escape (tell the owner of the team script). `next` after login must reject tabs and spaces (a browser reads `/<tab>/evil.example` as `//evil.example`). The report to the manager is empty until the day's plan is saved.
- 2026-11-11, environment: on Windows run `.venv\\Scripts\\python.exe`, start servers with `sys.executable -m uvicorn` (the browser tests do), stop them with `taskkill /T`. There was no local Postgres and downloading one was blocked, so Postgres runs belong on the laptop that has one.

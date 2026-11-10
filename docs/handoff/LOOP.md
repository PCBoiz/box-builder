# How the /loop session works on this project

Work comes from a local Kanzo board (http://127.0.0.1:4242, project `box-builder`). The board binds to localhost only.
Talk to it with `python3 -I tools/kz.py call <tool> '<json>'` (`python3 -I tools/kz.py tools` lists the tools).

Read card box-builder#31 (STATE) first. Ticks run every minute.

Each tick:

1. `next_task` with `{"project":"box-builder","filter":"tag:sm-mock"}` while any sm-mock card is left, then plain `{"project":"box-builder"}`. It takes the top todo task that does not wait on an unfinished one and moves it to doing.
   Human tasks are hidden from it. Do not touch them.
2. Do the task. Keep the change small and in the project's style (`docs/WRITING.md` for text).
3. Run the checks: `.venv/bin/python -m pytest tests -q`, the same with `TEST_DATABASE_URL=postgresql://pgtest@127.0.0.1:5433/boxtest`
   (start the test Postgres first if it is down), `node tests/apps_script_test.js`, and `.venv/bin/python -I tests/ui_automation_e2e.py`.
4. `add_comment` with what changed and the test result, then `update_task` to `review`. Never move a task to `done`: the user does that.
5. Restart the local app on port 8080 so it runs the new code.
6. Add one line to card #31 (what was done, what is next) and save a copy of the board: `~/.local/bin/kanzo export -o ~/kanzo-backup/board.md`.

Stay inside these limits:

- No commit, push or deploy unless the user asks. One push was made on the user's request after a full check (commit 4629348, 2026-11-10); later work stays uncommitted until they ask again.
- Nothing is written to Scenario Manager, the COS app, the Google Sheet or Render. No logins, no tokens.
- No new external software without the user's say. Kanzo itself was installed on the user's request, from a verified download.
- A task that needs an answer or a file from the user: comment what is missing, set it to `review`, and move on.
- The team uses the app on the internal network only and decided not to use ACCESS_CODE (removed on 2026-11-11). Login by company ID exists since then and is off until ADMIN_IDS is set (docs/LOGIN.md). The cross-site
  write check stays, because it matters most on an internal network.
- Read docs/SC_PROCESS.md before touching anything that creates or fills boxes: it holds the daily process and the box sizes.

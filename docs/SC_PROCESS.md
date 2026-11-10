# SC (Scenario Creator) daily process, as the app should support it

Source: the team's process document (Google Doc "quy trình và nội dung làm việc"). The loop works from this file.

## What SC produces
Boxes B2, B3 and A1 for every unit a collector works at, so collectors can film everyday scenarios.
A unit has one anchor object (a big object such as a fridge, microwave, bed or washing machine). In the sheet a unit is a section
(M8-01, M8-02, ...) and the anchor is the "Large object" column.

## How to choose objects
- Pick the theme first (for example cooking), then objects that relate to each other and to the anchor.
- Objects must allow meaningful actions, for example put the pan on the stove, switch the stove on, pour oil into the pan.
- A B2 or B3 box must let a collector think of 10 to 12 meaningful actions.
- B2 and B3 are for the evening shift and hold many objects (the sheet block has 21 rows).
- A1 is for the next morning's shift and holds about 10 to 14 objects.
- Do the order B2, then B3, then A1.

## Reusing objects (rule given by the user, 2026-11-10)
An object that sits in an older box may be used again when that box is far enough from the day the new box is made (for example one week
or one month). The app does this with the switch "Dùng lại món của hộp cũ" (Automations tab): on by default, 30 days, 1 to 365 days, off means
nothing is ever reused.
- Boxes read from SM: the box's created date decides. A box made more than N days ago holds nothing. A date the app cannot read keeps the box holding.
- IDs used in the sheet: the sheet has no dates, so the paste form asks for the day the boxes were made (empty = today). An ID with no known day
  is never reused. Pasting an ID again with a later day refreshes its day, never moves it back.
- Boxes made in this app keep their objects until the box is finished (next section): the database refuses one object in two boxes.
- Not decided yet: whether the same module or shift should wait longer than the rest of the team.

## A box is finished two days after the day it was made (rule given by the user, 2026-11-11)
An approved box is finished two Vietnam days after the day it was made (made on the 11th, finished on the 13th, from midnight). The objects of a
finished box are free again and the box leaves the lists. A line stays in `box_history` for 30 days so the
page can still say what was done on recent days. The number of days is the setting `BOX_DONE_DAYS` (2; 0 = never). Drafts are not finished by the
calendar: they expire after `DRAFT_TTL_HOURS` without a touch. Without this rule every box a person approves would hold its objects for ever and the
kho (about 400 free objects) would be empty after a few days.
- A box name (module + box name, for example `M8-02 Box B2`) is used once per Vietnam day, not for the two days a box lives: a unit needs a new B2
  every day. The database keeps this with a unique index on module, name and day (`boxes.day`); the same name on the same day is refused.
  The sheet script gets only the newest approved box of a unit and kind.
- The page "Hôm nay" lists the boxes to hand back in SM each morning: those of yesterday and of the day before. The app only reminds; the boxes are
  handed back in SM with the team's script.

## The day of the person who makes boxes (the owner's process document, in the app since 2026-11-11)
- Work day 2:30 to 11:30 (Vietnam time). Start: hand back the boxes of yesterday and the day before, ask the module leader which units work today.
  B1 3:30 to 5:30 (not made in this app), B2 6:00 to 7:15 and 8:15 to 9:30, B3 the rest of the shift, 10:00 print the paper for the supporters, end of
  shift: send the report of the units that did not work. The page shows this as a short list with the current item marked.
- The module changes every week; a unit is one collector position with one anchor object (a section such as M8-02). The person lists the units once,
  the plan of the next day starts from the last one with everyone on a full day, and each day the person sets who works and how.
- Boxes a unit needs by how its collector works: full day B2, B3, A1; first half B2 (can be skipped) and A1; second half only B3 (B1 is pushed up to
  A1, no new A1); only B1 and B2: B2 is reused as B3, then A1; absent: none (B1 is pushed up to A1 tomorrow). B1 is not made in this app: "pushing B1
  up to A1" means the physical box is reused.
- End of day report: the units that did not work a full day, ready to copy and send to the manager (Anh Lâm).
- "Gần đây ở ...": the themes of the boxes made lately for the module and section, so the same scene is not made twice.

## Objects borrowed many times (rule given by the user, 2026-11-11)
SM counts how often each object was borrowed (LIFETIME USE). An object at 500 borrows or more is picked less, and from a number the team chooses it
is left out. The app does this with the switch "Giảm món đã dùng nhiều" (Automations tab, on by default).
- From 500 borrows the chance of being picked halves for every 300 more. An object that reached the count quickly (a young object, by its creation date
  in SM) is reduced harder, an old one less (the setting "Ảnh hưởng của tuổi món", 0 to 100%, 0 = the count alone).
- "Mức loại hẳn" is the number of borrows from which an object is left out. 0 (the default) leaves nothing out. Choose the number after looking at the
  chart in "Cảnh báo kho".
- The pool is never emptied: at least "Số món tối thiểu còn để chọn" objects stay (floor weight), because only a part of the catalog is free at any time.
- An object SM sent no count for is never reduced. A person can still add a reduced or left-out object by hand.
- Not decided: whether the count alone is enough, or whether recent use should weigh more (that needs the date of each borrow, which the sync does not carry).

## Times (work day 2:30 to 11:30)
| Box | Collector works with it |
|---|---|
| B1 | 3:30 to 5:30 |
| B2 | 6:00 to 7:15 and 8:15 to 9:30 |
| B3 | the rest of the day |
10:00: start printing paper for the supporters.

## Daily routine
- Start of day: return yesterday's A3, B3 boxes (two days back) in SM. Ask the module leader which units work today.
- Talk to collectors about what they did in the last days, to avoid repeats. Talk to supporters about how many there are per module.
- Units that do not work: the supporter keeps the box. B1 is pushed up to become A1 the next morning. Box B is not put away.
- End of day: send the report of absent units to the team lead. Write down and prepare scenarios for the collectors.

## Attendance changes what SC makes
| Collector's day | SC makes |
|---|---|
| Full day | B2, B3, A1 |
| First half only | B2 (or none) and A1 for the morning collector |
| Second half only | B3 only. B1 is pushed up to be A1, so no new A1 |
| Absent | nothing new. B1 becomes A1 tomorrow |
| Does only B1 and B2 | B2 can be reused as B3, then make A1. Less time spent |

## Tools the team uses today
- Warehouse 1: https://sm.config.inc/object (English search, ID opens a picture).
- Warehouse 2: the Vietnamese COS app (search, category filter, one click on an ID copies it).
- The sheet "Shift B Objects Preparation (IHDC) v4": pick the module tab, find the unit's B2, B3 or A1 block, paste the IDs.
- SM TaskBox: a TaskBox is created per box in sm.config.inc.

## Assumptions made while reading (the user confirms or corrects, board card "Confirm the SC rules")
1. B2 and B3 hold 21 objects, A1 holds 12 (inside 10 to 14).
2. "First half only": make B2 and A1; B2 can be skipped.
3. The title of a TaskBox stays "<section code> - <anchor name> - <stage>", for example "M8-02 - Tủ lạnh - B2".
4. B1 is not in the sheet and not made by this app (confirmed by the owner on 2026-11-11).

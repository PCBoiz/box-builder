"""In-memory fake of Scenario Manager (SM), so the push automation can be built and tested offline.

Nothing here talks to the real SM, and there is no real client in this module. The fake mirrors what
was learned about SM read-only on 2026-11-10: the TaskBox fields, the five statuses and the moves
the SM userscript performs through POST /api/boxes/<id>/transition. The fake is stricter than SM in a
few places (unknown fields, duplicate objects in one box) so mistakes in the caller show up in tests.

Pure Python, standard library only: no network, no sockets, no files, no environment.
"""
from __future__ import annotations

import copy
import re
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import Callable

CREATED = "created"
ASSIGNED = "assigned"
COLLECTED = "collected"
# The UI has "Send to the floor" between Borrowed and Returned. SM's real status name for it is not
# known, so "on_floor" is our own name.
ON_FLOOR = "on_floor"
DEACTIVATED = "deactivated"

STATUSES = (CREATED, ASSIGNED, COLLECTED, ON_FLOOR, DEACTIVATED)

# Labels the SM UI shows. Only Unassigned, Borrowed and Returned were seen; the other two are guesses.
STATUS_LABELS = {
    CREATED: "Unassigned",
    ASSIGNED: "Assigned",
    COLLECTED: "Borrowed",
    ON_FLOOR: "On the floor",
    DEACTIVATED: "Returned",
}

# Moves allowed from each status. Anything else is refused as bad_move.
MOVES = {
    CREATED: (ASSIGNED,),
    ASSIGNED: (CREATED, COLLECTED),
    COLLECTED: (ON_FLOOR, DEACTIVATED),
    ON_FLOOR: (DEACTIVATED,),
    DEACTIVATED: (),
}

# Statuses where the box holds its objects, so they leave the available stock.
HOLDING = (COLLECTED, ON_FLOOR)
# Statuses where the box has not borrowed yet, so "short" can still apply.
WAITING = (CREATED, ASSIGNED)
HAND_OVER = (ASSIGNED, COLLECTED, ON_FLOOR)

# Field the caller must pass for a move, and the message when it is missing.
_REQUIRED = {
    ASSIGNED: ("assignee", "Cần nhập người được giao."),
    COLLECTED: ("borrower", "Cần nhập người mượn."),
    DEACTIVATED: ("returned_by", "Cần nhập người trả."),
}

_TRANSITION_FIELDS = ("assignee", "borrower", "returned_by", "note")
_PAYLOAD_KEYS = ("title", "anchor_object_id", "note", "contents", "borrow_now", "assignee", "borrower")
_LINE_KEYS = ("object_id", "quantity")
_OBJECT_ID = re.compile(r"[0-9]{6}")

_DEFAULT_MESSAGES = {
    "bad_move": "Không chuyển trạng thái hộp được.",
    "missing": "Không có hộp này trong SM.",
    "invalid": "Dữ liệu gửi lên SM không đúng.",
    "short": "Không mượn được vì thiếu món.",
}


class SMError(Exception):
    """An SM refusal. `code` is for programs (bad_move, missing, invalid, short); `message` is for people."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = message or _DEFAULT_MESSAGES.get(code, "SM từ chối yêu cầu.")
        super().__init__(self.message)


class SMClient(ABC):
    """What the push automation needs from SM. MockSM implements it; a real client would too."""

    @abstractmethod
    def create_box(self, payload: dict) -> str:
        """Create a box and return its id."""

    @abstractmethod
    def transition(self, box_id: str, to: str, **fields) -> dict:
        """Move a box to status `to` and return the updated box."""

    @abstractmethod
    def hand_over(self, box_id: str, receiver: str, note: str = "") -> dict:
        """Give a box to another person. The status does not change; who holds it does."""

    @abstractmethod
    def list_boxes(self, status: str | None = None) -> list[dict]:
        """Return all boxes, or only those in `status`."""

    @abstractmethod
    def get_box(self, box_id: str) -> dict:
        """Return one box."""


class MockSM(SMClient):
    """Fake SM held in memory. Each instance has its own state. Returned dicts are deep copies."""

    def __init__(self, inventory: dict[str, int], names: dict[str, str] | None = None,
                 clock: Callable[[], str] | None = None):
        self._stock = dict(inventory)
        self._names = dict(names or {})
        self._clock = clock
        self._boxes: dict[str, dict] = {}
        self._ticks = 0

    # Public API

    def add_stock(self, inventory: dict[str, int], names: dict[str, str] | None = None) -> None:
        """Add objects the fake does not know yet (the app's catalog can grow). Known objects keep what they have."""
        for oid, units in inventory.items():
            self._stock.setdefault(oid, units)
        for oid, name in (names or {}).items():
            self._names.setdefault(oid, name)

    def create_box(self, payload: dict) -> str:
        """Validate and store a box. With borrow_now it also borrows, unless an object is short.

        A short object does not refuse the box: the box is made, nothing is borrowed, and the
        short flags show it. A borrower is required with borrow_now, as the move needs one.
        """
        clean = self._validate(payload)
        number = len(self._boxes) + 1
        box_id = f"MBX-{number:04d}"
        assignee = clean["assignee"]
        box = {
            "box_id": box_id,
            "title": clean["title"],
            "anchor_object_id": clean["anchor_object_id"],
            "note": clean["note"],
            "contents": clean["contents"],
            "status": ASSIGNED if assignee else CREATED,
            "assignee": assignee,
            "holder": None,
            "created_at": self._stamp(),
            "history": [{"to": CREATED, "note": clean["note"]}],
        }
        if assignee:
            box["history"].append({"to": ASSIGNED, "note": "", "assignee": assignee})
        self._boxes[box_id] = box
        if clean["borrow_now"] and not self._shortfalls(box):
            box["status"] = COLLECTED
            box["holder"] = clean["borrower"]
            box["history"].append({"to": COLLECTED, "note": "", "borrower": clean["borrower"]})
        return box_id

    def transition(self, box_id: str, to: str, **fields) -> dict:
        """Move a box. Accepts assignee, borrower, returned_by and note; others are refused."""
        box = self._find(box_id)
        unknown = sorted(set(fields) - set(_TRANSITION_FIELDS))
        if unknown:
            raise SMError("invalid", f"Chuyển trạng thái có trường lạ: {', '.join(unknown)}.")
        if not isinstance(to, str) or to not in MOVES[box["status"]]:
            now = STATUS_LABELS[box["status"]]
            target = STATUS_LABELS.get(to, to) if isinstance(to, str) else str(to)
            raise SMError("bad_move", f"Hộp {box['box_id']} đang ở trạng thái {now}, "
                                      f"không chuyển sang {target} được.")
        note = _text(fields.get("note"), "Ghi chú phải là chữ.") or ""
        entry = {"to": to, "note": note}
        required = _REQUIRED.get(to)
        person = None
        if required:
            field, message = required
            person = _text(fields.get(field), f"Giá trị {field} phải là chữ.")
            if not person:
                raise SMError("invalid", message)
            entry[field] = person
        if to == COLLECTED:
            short = self._shortfalls(box)
            if short:
                raise SMError("short", f"Không mượn được hộp {box['box_id']}. Thiếu {self._describe(short)}. "
                                       "Chờ có hàng rồi mượn lại.")
        box["status"] = to
        if to == ASSIGNED:
            box["assignee"] = person
        elif to == CREATED:
            box["assignee"] = None
        elif to == COLLECTED:
            box["holder"] = person
        elif to == DEACTIVATED:
            box["holder"] = None
        box["history"].append(entry)
        return self._view(box)

    def hand_over(self, box_id: str, receiver: str, note: str = "") -> dict:
        """Hand a box to `receiver`: allowed where may_hand_over is true (assigned, collected, on_floor). Assigned boxes change
        their assignee, boxes that are held change their holder. The status stays."""
        box = self._find(box_id)
        who = _text(receiver, "Người nhận phải là chữ.")
        if not who:
            raise SMError("invalid", "Cần có tên người nhận để bàn giao.")
        if box["status"] not in HAND_OVER:
            raise SMError("bad_move", f"Hộp {box['box_id']} đang ở trạng thái {STATUS_LABELS[box['status']]}, chưa bàn giao được.")
        if box["status"] == ASSIGNED:
            box["assignee"] = who
        else:
            box["holder"] = who
        box["history"].append({"to": box["status"], "note": _text(note, "Ghi chú phải là chữ.") or "", "receiver": who})
        return self._view(box)

    def list_boxes(self, status: str | None = None) -> list[dict]:
        if status is not None and status not in STATUSES:
            raise SMError("invalid", f"Trạng thái {status} không có trong SM.")
        return [self._view(b) for b in self._boxes.values() if status is None or b["status"] == status]

    def get_box(self, box_id: str) -> dict:
        return self._view(self._find(box_id))

    def available(self, object_id: str) -> int:
        """Units in stock minus units held by boxes in collected or on_floor."""
        held = sum(line["quantity"] for b in self._boxes.values() if b["status"] in HOLDING
                   for line in b["contents"] if line["object_id"] == object_id)
        return self._stock.get(object_id, 0) - held

    # Internals

    def _find(self, box_id: str) -> dict:
        box = self._boxes.get(box_id) if isinstance(box_id, str) else None
        if box is None:
            raise SMError("missing", f"Không có hộp {box_id} trong SM. Kiểm tra lại mã hộp.")
        return box

    def _stamp(self) -> str:
        if self._clock is not None:
            return str(self._clock())
        self._ticks += 1
        return (datetime(2026, 1, 1) + timedelta(seconds=self._ticks)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _shortfalls(self, box: dict) -> list[tuple[str, int, int]]:
        """(object_id, wanted, available) for each line the box wants more of than exists now."""
        out = []
        for line in box["contents"]:
            have = self.available(line["object_id"])
            if line["quantity"] > have:
                out.append((line["object_id"], line["quantity"], have))
        return out

    def _label(self, object_id: str) -> str:
        name = self._names.get(object_id)
        return f"{name} ({object_id})" if name else object_id

    def _describe(self, short: list[tuple[str, int, int]]) -> str:
        return ", ".join(f"{self._label(o)} cần {want}, còn {max(have, 0)}" for o, want, have in short)

    def _view(self, box: dict) -> dict:
        """The TaskBox as SM shows it. Short applies only before the box has borrowed."""
        waiting = box["status"] in WAITING
        contents = []
        for line in box["contents"]:
            have = self.available(line["object_id"])
            contents.append({
                "object_id": line["object_id"],
                "quantity": line["quantity"],
                "name": self._names.get(line["object_id"], line["object_id"]),
                "available": have,
                "short": waiting and line["quantity"] > have,
            })
        view = {
            "box_id": box["box_id"],
            "title": box["title"],
            "status": box["status"],
            "status_label": STATUS_LABELS[box["status"]],
            "anchor_object_id": box["anchor_object_id"],
            "note": box["note"],
            "contents": contents,
            "holder": box["holder"],
            "assignee": box["assignee"],
            "next_actions": list(MOVES[box["status"]]),
            "may_hand_over": box["status"] in HAND_OVER,
            "short_count": sum(1 for c in contents if c["short"]),
            "created_at": box["created_at"],
            "history": box["history"],
        }
        return copy.deepcopy(view)

    def _validate(self, payload: dict) -> dict:
        """Check a create payload and return a clean copy. Raises SMError('invalid') on the first problem."""
        if not isinstance(payload, dict):
            raise SMError("invalid", "Dữ liệu tạo hộp không đúng dạng.")
        unknown = sorted(set(payload) - set(_PAYLOAD_KEYS))
        if unknown:
            raise SMError("invalid", f"Dữ liệu tạo hộp có trường lạ: {', '.join(unknown)}.")
        title = payload.get("title")
        if not isinstance(title, str) or not title.strip():
            raise SMError("invalid", "Hộp cần có tên.")
        title = title.strip()
        contents = self._validate_contents(payload.get("contents"))
        anchor = payload.get("anchor_object_id")
        if anchor is not None and anchor != "":
            self._check_object(anchor)
        else:
            anchor = None
        note = _text(payload.get("note"), "Ghi chú phải là chữ.") or ""
        borrow_now = payload.get("borrow_now", False)
        if not isinstance(borrow_now, bool):
            raise SMError("invalid", "borrow_now chỉ nhận đúng hoặc sai.")
        assignee = _text(payload.get("assignee"), "Giá trị assignee phải là chữ.")
        borrower = _text(payload.get("borrower"), "Giá trị borrower phải là chữ.")
        if borrow_now and not borrower:
            raise SMError("invalid", "Mượn ngay cần nhập người mượn.")
        # Whitespace and case are ignored so a near-copy of a title is still caught.
        taken = {b["title"].casefold() for b in self._boxes.values() if b["status"] != DEACTIVATED}
        if title.casefold() in taken:
            raise SMError("invalid", f'Đã có hộp tên "{title}" chưa trả. Đổi tên hoặc trả hộp đó trước.')
        return {"title": title, "anchor_object_id": anchor, "note": note, "contents": contents,
                "borrow_now": borrow_now, "assignee": assignee, "borrower": borrower}

    def _validate_contents(self, contents) -> list[dict]:
        if not isinstance(contents, list) or not contents:
            raise SMError("invalid", "Hộp cần ít nhất 1 món.")
        clean, seen = [], set()
        for line in contents:
            if not isinstance(line, dict):
                raise SMError("invalid", "Mỗi món cần có object_id và quantity.")
            unknown = sorted(set(line) - set(_LINE_KEYS))
            if unknown:
                raise SMError("invalid", f"Món có trường lạ: {', '.join(unknown)}.")
            object_id, quantity = line.get("object_id"), line.get("quantity")
            self._check_object(object_id)
            if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
                raise SMError("invalid", f"Món {object_id} cần số lượng từ 1 trở lên.")
            if object_id in seen:
                raise SMError("invalid", f"Món {object_id} có hai dòng. Gộp thành một dòng.")
            seen.add(object_id)
            clean.append({"object_id": object_id, "quantity": quantity})
        return clean

    def _check_object(self, object_id) -> None:
        if not isinstance(object_id, str) or not _OBJECT_ID.fullmatch(object_id):
            raise SMError("invalid", f"Mã món {object_id} không đúng, cần đủ 6 chữ số.")
        if object_id not in self._stock:
            raise SMError("invalid", f"Món {object_id} không có trong kho.")


def _text(value, message: str) -> str | None:
    """A stripped string, or None when the value is missing or blank. Other types are refused."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise SMError("invalid", message)
    return value.strip() or None

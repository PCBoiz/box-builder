"""Object catalog loaded from the sheetsync CSV. Hot-reloads when the file changes."""
import csv
import os
import threading
import unicodedata
from dataclasses import dataclass


def fold(s: str) -> str:
    """Lower-case and strip accents so 'Đồ nhà bếp' matches 'do nha bep'."""
    s = unicodedata.normalize("NFD", (s or "").replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def display_name(en: str, vi: str) -> str:
    """The sheet's naming style: 'English (Tiếng Việt)', or just one name when both are the same."""
    en, vi = (en or "").strip(), (vi or "").strip()
    if not vi or fold(en) == fold(vi):
        return en or vi
    return f"{en} ({vi})"


def pad_id(x) -> str | None:
    """'4702' -> '004702'. Returns None when x has no digits or more than 6."""
    digits = "".join(ch for ch in str(x) if ch.isdigit())
    if not digits or len(digits) > 6:
        return None
    return digits.zfill(6)


@dataclass(frozen=True)
class Item:
    id: str
    name: str      # English item name
    vi: str        # Vietnamese name (what the sheet shows)
    category: str
    slot: str
    stock: int
    blob: str      # folded text used for search


class Catalog:
    """Usable objects only: ACTIVE, has a Vietnamese name, stock > 0 (same rules as the Make blueprint)."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._mtime = None
        self._items: dict[str, Item] = {}
        self._load()

    def _load(self):
        mtime = os.stat(self.path).st_mtime
        items: dict[str, Item] = {}
        with open(self.path, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        for r in rows[1:]:
            if len(r) < 8:
                continue
            oid = pad_id(r[0])
            if oid is None:
                continue
            status, vi, qty = r[6].strip(), r[2].strip(), r[7].strip()
            if status != "ACTIVE" or not vi or not qty.isdigit() or int(qty) <= 0:
                continue
            name, cat, slot = r[1].strip(), r[3].strip(), r[5].strip()
            items[oid] = Item(oid, name, vi, cat, slot, int(qty), fold(f"{name} {vi} {cat}"))
        self._items, self._mtime = items, mtime

    def _fresh(self):
        try:
            if os.stat(self.path).st_mtime != self._mtime:
                with self._lock:
                    self._load()
        except OSError:
            pass

    def items(self) -> dict[str, Item]:
        self._fresh()
        return self._items

    def get(self, oid: str) -> Item | None:
        return self.items().get(oid)

    def categories(self) -> list[tuple[str, int]]:
        counts: dict[str, int] = {}
        for it in self.items().values():
            counts[it.category or "(no category)"] = counts.get(it.category or "(no category)", 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    def search(self, q: str, exclude=(), limit: int = 20) -> list[Item]:
        words = [w for w in fold(q).split() if w]
        if not words:
            return []
        out = []
        for it in self.items().values():
            if it.id in exclude:
                continue
            if all(w in it.blob or w in it.id for w in words):
                out.append(it)
                if len(out) >= limit:
                    break
        return out

"""Object catalog loaded from the sheetsync CSV. Hot-reloads when the file changes."""
import csv
import os
import threading
import unicodedata
from dataclasses import dataclass

from . import images


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
    img: str = ""  # cleaned image URL pushed by the userscript (empty when SM sent none or it failed the checks)


UNCATEGORISED = "Chưa phân loại"
LIVE_TYPES = ("Obj",)          # SM object types that can go in a box (Env = environments, Meta = system)


class Catalog:
    """Usable objects only: ACTIVE, has a Vietnamese name, stock > 0 (same rules as the Make blueprint).

    Two sources. The CSV export is the fallback and also supplies categories (SM has no category field).
    Once the userscript has pushed a live snapshot from Scenario Manager, that snapshot is the catalog."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._mtime = None
        self._items: dict[str, Item] = {}
        self._cat_of: dict[str, str] = {}
        self._vi_of: dict[str, str] = {}
        self._live: dict[str, Item] | None = None
        self.sectors: tuple[str, ...] = ()        # empty = every sector; the app sets it from SECTORS
        self.image_hosts: tuple[str, ...] = images.DEFAULT_HOSTS
        self.image_ttl_s: int = 55 * 60           # the app sets it from IMAGE_TTL_MINUTES
        self._live_info: dict = {}
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
        # categories for every CSV row, even ones that are out of stock today
        cat_of = {}
        for r in rows[1:]:
            if len(r) >= 8 and pad_id(r[0]) and r[3].strip():
                cat_of[pad_id(r[0])] = r[3].strip()
        # Vietnamese names for every CSV row: SM's own object list carries no Vietnamese name, so a live sync falls back to these
        vi_of = {pad_id(r[0]): r[2].strip() for r in rows[1:] if len(r) >= 3 and pad_id(r[0]) and r[2].strip()}
        self._items, self._cat_of, self._vi_of, self._mtime = items, cat_of, vi_of, mtime

    def _fresh(self):
        try:
            if os.stat(self.path).st_mtime != self._mtime:
                with self._lock:
                    self._load()
        except OSError:
            pass

    def items(self) -> dict[str, Item]:
        self._fresh()
        return self._live if self._live is not None else self._items

    # ---------------------------------------------------------------- live snapshot from Scenario Manager
    def set_live(self, rows: list[dict], synced_at: float) -> dict:
        """Replace the catalog with SM's current objects. Returns counts so the caller can show what was skipped."""
        items, skipped = self._build_live(rows)
        self._live = items
        self._live_info = {"synced_at": synced_at, "received": len(rows), "skipped": skipped}
        return {**self._live_info, "usable": len(items)}

    def preview_live(self, rows: list[dict]) -> dict:
        """What set_live would give, without changing anything: lets the caller refuse a sync that would empty the catalog."""
        items, skipped = self._build_live(rows)
        return {"received": len(rows), "usable": len(items), "skipped": skipped}

    def _build_live(self, rows: list[dict]) -> tuple[dict[str, Item], dict]:
        items: dict[str, Item] = {}
        skipped = {"other_sector": 0, "other_type": 0, "inactive": 0, "no_vietnamese_name": 0, "out_of_stock": 0, "bad_id": 0}
        for o in rows:
            oid = pad_id(o.get("id", ""))
            if oid is None:
                skipped["bad_id"] += 1
                continue
            if not self.allowed_sector(o):
                skipped["other_sector"] += 1
                continue
            if str(o.get("type", "Obj")) not in LIVE_TYPES:
                skipped["other_type"] += 1
                continue
            if str(o.get("status", "ACTIVE")) != "ACTIVE":
                skipped["inactive"] += 1
                continue
            vi = str(o.get("vi") or "").strip() or self._vi_of.get(oid, "")
            if not vi:
                skipped["no_vietnamese_name"] += 1
                continue
            stock = o.get("avail")
            if stock is None:
                stock = o.get("qty")
            try:
                stock = int(stock)
            except (TypeError, ValueError):
                stock = 0
            if stock <= 0:
                skipped["out_of_stock"] += 1
                continue
            name = str(o.get("name") or "").strip()
            cat = self._cat_of.get(oid, UNCATEGORISED)
            items[oid] = Item(oid, name, vi, cat, str(o.get("loc") or "").strip(), stock, fold(f"{name} {vi} {cat}"),
                              images.pick_image(o, hosts=self.image_hosts) or "")
        return items, skipped

    def allowed_sector(self, o: dict) -> bool:
        """Rows from other sectors, or with no sector at all, are dropped (when a sector filter is set)."""
        return not self.sectors or str(o.get("sector") or "") in self.sectors

    def info(self) -> dict:
        n = len(self.items())
        if self._live is None:
            return {"source": "csv", "usable": n, "synced_at": None}
        return {"source": "live", "usable": n, **self._live_info}

    def image_expiry(self, url) -> float | None:
        """When this picture link should stop being asked for (epoch seconds), or None if it never expires."""
        synced = self._live_info.get("synced_at") if self._live is not None else None
        try:
            return images.expiry_for(url, synced, self.image_ttl_s)
        except Exception:                      # whatever a link looks like, the object list must still be served
            return None

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

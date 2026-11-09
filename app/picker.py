"""Choosing the objects for a box: prompt, LLM backends (DeepSeek or a mock), parsing, validation,
repair and the final atomic claim. The LLM only ever *suggests*; code decides what is accepted."""
import contextlib
import json
import random
import re
import threading
import urllib.error
import urllib.request

from . import service
from .catalog import Catalog, Item, fold
from .db import connect
from .settings import Settings

MAX_ATTEMPTS = 3          # 1 normal call + 2 repair calls, then code fills the rest
POOL_LIMIT = 2500         # never send more catalog lines than this to the model

SYSTEM = (
    "You prepare object lists for robot manipulation scenarios. Choose exactly {need} objects{for_box}.\n"
    "A good box is a set of versatile objects that can be reused in many different scenarios: any few of its "
    "objects can be combined into a new scenario (for example: pick and place items into a container, sort or "
    "stack items, set up a table, scoop or pour, use a tool on an item, hand an item over).\n\n"
    "Selection rules:\n"
    "- {setting}\n"
    "- Choose versatile objects, not one-off or highly specialised ones. Include a few containers or surfaces "
    "(tray, bowl, basket, box, cup, plate) and a few tools (spoon, scoop, tongs, brush), plus smaller items that "
    "can go into, onto or be used with them.\n"
    "- Every object must be usable together with at least three other objects in the box, so it appears in "
    "several different scenarios. No isolated objects.\n"
    "- Every object must be small and light enough for a robot arm to pick up.\n"
    "- Use only IDs that appear in the catalog. Do not pick the same object twice.\n\n"
    "Output format: exactly {need} lines and nothing else. Each line is: ID|quantity\n"
    "ID is copied exactly from the first field of a catalog line (6 digits, keep the leading zeros). quantity is "
    "a small whole number: use 1 for almost every object, and 2 or 3 only when a scenario really needs several "
    "identical items (for example spoons, cups or balls). No names, no numbering, no markdown, no code fences, "
    "no explanations."
)


def build_prompts(pool: list[Item], current: list[Item], need: int, count: int, theme: str, module: str, label: str):
    setting = (f"All objects come from one coherent everyday setting: {theme.strip()}." if theme.strip()
               else "All objects come from one coherent everyday setting that you choose yourself.")
    for_box = f" to complete ONE box of {count} objects" if current else " for ONE box"
    system = SYSTEM.format(need=need, setting=setting, for_box=for_box)
    user = f"Task: choose {need} objects for {label} of module {module}.\n"
    if current:
        user += ("\nThe box already contains these objects (do not repeat them). Choose objects that complement "
                 "them so the whole box stays one coherent set:\n"
                 + "\n".join(f"{c.id}|{c.name}|{c.category}" for c in current) + "\n")
    user += "\nCatalog, one object per line: ID|name|category\n" + "\n".join(f"{p.id}|{p.name}|{p.category}" for p in pool)
    return system, user


# ----------------------------------------------------------------------------- LLM backends
class LLMError(Exception):
    pass


class DeepSeekLLM:
    name = "deepseek"

    def __init__(self, s: Settings):
        self.s = s

    def complete(self, system: str, user: str, ctx: dict) -> str:
        body = {
            "model": self.s.deepseek_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "thinking": {"type": "disabled"},
            "temperature": 0.5,
            "max_tokens": 1024,
        }
        req = urllib.request.Request(
            self.s.deepseek_base + "/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.s.deepseek_key})
        last = None
        for _ in range(2):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    data = json.load(r)
                return data["choices"][0]["message"]["content"] or ""
            except urllib.error.HTTPError as e:
                last = LLMError(f"Lỗi DeepSeek HTTP {e.code}: {e.read()[:200].decode(errors='replace')}")
                if e.code in (400, 401, 402, 403, 422):   # retrying will not help
                    break
            except Exception as e:                           # timeouts, resets, bad JSON
                last = LLMError(f"Gọi DeepSeek thất bại: {e}")
        raise last


class MockLLM:
    """Offline stand-in used for testing the whole pipeline without an API key or cost.
    Scores objects by theme words, spreads them over categories, answers in the same ID|qty format."""
    name = "mock"

    def complete(self, system: str, user: str, ctx: dict) -> str:
        pool, need, theme = ctx["pool"], ctx["need"], ctx["theme"]
        rng = random.Random(ctx.get("seed", 0))
        words = [w for w in fold(theme).split() if len(w) >= 3]
        scored = [(sum(w in p.blob for w in words), p) for p in pool]
        hits = [p for s, p in sorted(scored, key=lambda t: -t[0]) if s > 0]
        if len(hits) < need:                      # no/low theme match: draw from a few random categories
            cats = sorted({p.category for p in pool})
            rng.shuffle(cats)
            keep = set(cats[:4])
            seen = {p.id for p in hits}
            extra = [p for p in pool if p.category in keep and p.id not in seen]
            rng.shuffle(extra)
            hits += extra
        per_cat: dict[str, int] = {}
        chosen: list[Item] = []
        for p in hits:
            if per_cat.get(p.category, 0) >= max(3, need // 4):
                continue
            per_cat[p.category] = per_cat.get(p.category, 0) + 1
            chosen.append(p)
            if len(chosen) >= need:
                break
        if len(chosen) < need:                    # category cap was too strict: top up, still no duplicates
            have = {p.id for p in chosen}
            rest = [p for p in hits + pool if p.id not in have]
            seen = set()
            for p in rest:
                if p.id not in seen and len(chosen) < need:
                    seen.add(p.id)
                    chosen.append(p)
        return "\n".join(f"{p.id}|{2 if rng.random() < 0.1 and p.stock > 1 else 1}" for p in chosen)


def make_llm(s: Settings):
    if s.llm_mode == "mock":
        return MockLLM()
    if s.llm_mode == "deepseek" or s.deepseek_key:
        if not s.deepseek_key:
            raise SystemExit("LLM_MODE=deepseek needs DEEPSEEK_API_KEY")
        return DeepSeekLLM(s)
    return MockLLM()


# ----------------------------------------------------------------------------- parsing and validation
def parse_reply(text: str) -> list[tuple[str, int]]:
    """Tolerant parser: accepts 'ID|qty', numbered lines, bullets, fences, unpadded IDs."""
    out = []
    for raw in (text or "").splitlines():
        s = re.sub(r"^[>*\-•\s]+", "", raw.strip().strip("`").strip())
        s = re.sub(r"^\d{1,2}[.)]\s+", "", s)
        m = re.match(r"^(\d{1,6})(?!\d)\s*(?:[|,;:\t ]\s*(\d{1,3})(?!\d))?", s)
        if m:
            out.append((m.group(1).zfill(6), int(m.group(2) or 1)))
    return out


def validate(picks: list[tuple[str, int]], pool_by_id: dict[str, Item], need: int, max_qty: int):
    """Keep only real, free, not-yet-seen IDs; cap quantity by MAX_QTY and by the object's stock."""
    seen, ok = set(), []
    for oid, qty in picks:
        it = pool_by_id.get(oid)
        if it is None or oid in seen:
            continue
        seen.add(oid)
        ok.append((it, max(1, min(max_qty, it.stock, qty))))
        if len(ok) >= need:
            break
    return ok


# ----------------------------------------------------------------------------- the generation job
_sem_lock = threading.Lock()
_sems: dict[int, threading.Semaphore] = {}


def _semaphore(n: int) -> threading.Semaphore:
    with _sem_lock:
        return _sems.setdefault(n, threading.Semaphore(n))


def available_pool(conn, catalog: Catalog, categories: list[str], avoid=()) -> list[Item]:
    taken = service.taken_ids(conn)
    cats = set(categories)
    out = []
    for it in catalog.items().values():
        if it.id in taken or it.id in avoid:
            continue
        if cats and (it.category or "(no category)") not in cats:
            continue
        out.append(it)
    return out


@contextlib.contextmanager
def _db(s: Settings):
    """A short-lived connection. Never held across a model call, so slow LLM replies cannot starve the pool."""
    conn = connect(s.db_path)
    try:
        yield conn
    finally:
        conn.close()


def _free_positions(box, have, positions):
    used = {r["pos"] for r in have}
    return [p for p in range(1, box["count"] + 1) if p not in used and (positions is None or p in positions)]


def run_generation(s: Settings, catalog: Catalog, llm, box_id: int, avoid=(), positions=None) -> None:
    """Fill the empty slots of a box (all of them, or only `positions`). Never raises: problems are
    written to the box so the page can show them."""
    try:
        with _db(s) as conn:
            box = service.get_box(conn, box_id)
        cats = json.loads(box["categories"])
        info = []
        sem = _semaphore(s.max_parallel_llm)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            # One box picks at a time (default) so each pick sees every object the others already took.
            # Without this, boxes generated at the same moment all ask for the same "best" objects and
            # all but one pay for an LLM call that gets thrown away.
            if not sem.acquire(blocking=False):
                with _db(s) as conn:
                    service.set_status(conn, box_id, "generating", "Đang chờ hộp khác chọn xong...")
                sem.acquire()
            try:
                with _db(s) as conn:
                    have = service.box_items(conn, box_id)
                    free = _free_positions(box, have, positions)
                    if not free:
                        break
                    pool = available_pool(conn, catalog, cats, avoid)
                if len(pool) < len(free):
                    raise service.Invalid(
                        f"Chỉ còn {len(pool)} món trống khớp bộ lọc nhưng cần {len(free)} món. "
                        "Hãy mở rộng nhóm đồ hoặc giải phóng bớt hộp.")
                rng = random.Random(box_id * 1000 + attempt)
                sample = pool if len(pool) <= POOL_LIMIT else rng.sample(pool, POOL_LIMIT)
                current = [catalog.get(r["object_id"]) for r in have if catalog.get(r["object_id"])]
                system, user = build_prompts(sample, current, len(free), box["count"], box["theme"], box["module"], box["label"])
                ctx = {"pool": sample, "need": len(free), "theme": box["theme"], "current": current,
                       "seed": box_id * 1000 + attempt}
                reply = llm.complete(system, user, ctx)                      # no DB connection is held here
                good = validate(parse_reply(reply), {p.id: p for p in sample}, len(free), s.max_qty)
                with _db(s) as conn:
                    filled = service.claim(conn, box_id, [(pos, it, q, "llm") for pos, (it, q) in zip(free, good)])
                    service.get_box(conn, box_id)          # raises NotFound if someone discarded the box meanwhile
                info.append(f"{llm.name} lần {attempt}: nhận {len(filled)}/{len(free)} món")
            finally:
                sem.release()

        # Anything still empty: fill by code, preferring categories already in the box.
        with _db(s) as conn:
            have = service.box_items(conn, box_id)
            free = _free_positions(box, have, positions)
            if free:
                pool = available_pool(conn, catalog, cats, avoid)
                rng = random.Random(box_id)
                rng.shuffle(pool)
                cur_cats = {catalog.get(r["object_id"]).category for r in have if catalog.get(r["object_id"])}
                pool.sort(key=lambda it: 0 if it.category in cur_cats else 1)
                picks = [(it, 1) for it in pool[:len(free)]]
                service.claim(conn, box_id, [(pos, it, q, "fill") for pos, (it, q) in zip(free, picks)])
                info.append(f"{len(picks)} món do hệ thống tự điền (mô hình trả về thiếu món hợp lệ)")
            left = box["count"] - len(service.box_items(conn, box_id))
            if left:
                service.set_status(conn, box_id, "error", f"Còn {left} ô chưa điền được. " + "; ".join(info))
            else:
                service.set_status(conn, box_id, "draft", "; ".join(info))
    except service.NotFound:
        pass                                    # the box was discarded while generating
    except Exception as e:                      # LLM down, bad key, filters too narrow, ...
        try:
            with _db(s) as conn:
                service.set_status(conn, box_id, "error", str(e)[:300])
        except Exception:
            pass


def start_generation(s: Settings, catalog: Catalog, llm, box_id: int, avoid=(), positions=None) -> threading.Thread:
    t = threading.Thread(target=run_generation, args=(s, catalog, llm, box_id, avoid, positions), daemon=True)
    t.start()
    return t

"""Turn one free-text brief into several box proposals. The model only proposes settings (one line each);
labels, counts and every limit are decided by code, and a human reviews the plan before anything is created."""
import json
import re

MAX_BOXES = 12
THEME_MAX = 300

SYSTEM = (
    "You plan sets of robot-manipulation object boxes. Split the brief into exactly {n} DIFFERENT everyday settings, "
    "one per box, so the boxes do not overlap. Answer with a JSON array of {n} strings and nothing else. "
    "Each string is one short line (under 200 characters) describing the everyday setting of that box, "
    "written in the same language as the brief.\n"
    "Style: name the place and the things in it (furniture, tools, food, containers), in plain words. "
    "No praise or marketing adjectives (diverse, modern, perfect, ideal), no opening phrase like "
    "'a setting where', no list of exactly three adjectives, no dashes. Example: "
    "'bàn bếp và bàn ăn: bát đĩa, thớt, dao, hộp đựng, trái cây'."
)


def next_labels(start: str, n: int, taken: set[str]) -> list[str]:
    """'Box A1' -> Box A1, Box B1, Box C1 ... skipping names already taken. Falls back to a number suffix."""
    start = start.strip() or "Box A1"
    m = re.match(r"^(.*?)([A-Za-z])(\d+)$", start)
    out, i = [], 0
    while len(out) < n and i < 200:
        if m:
            letter = ord(m.group(2).upper()) + i
            if letter > ord("Z"):
                break
            cand = f"{m.group(1)}{chr(letter)}{m.group(3)}"
        else:
            cand = start if i == 0 else f"{start} {i + 1}"
        i += 1
        if cand not in taken:
            out.append(cand)
    return out


def _themes_from_lines(brief: str, n: int) -> list[str]:
    parts = [re.sub(r"^[\s\-*•\d.)]+", "", p).strip() for p in re.split(r"[\n;]+", brief)]
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return [parts[0] if parts else ""] * n
    return parts[:MAX_BOXES]


def _themes_from_model(engine, brief: str, n: int) -> list[str] | None:
    reply = engine.complete(SYSTEM.format(n=n), f"Brief:\n{brief}", {"pool": [], "need": n, "theme": brief, "current": []})
    m = re.search(r"\[.*\]", reply or "", re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    out = [str(x).strip() for x in data if isinstance(x, (str, int, float)) and str(x).strip()]
    return out or None


def clean_labels(labels) -> list[str]:
    """Box names typed by the user, e.g. 'Box B2, Box B3, Box A1': trimmed, no blanks, no repeats, at most MAX_BOXES."""
    out = []
    for x in labels or []:
        x = " ".join(str(x).split())[:40]
        if x and x not in out:
            out.append(x)
    return out[:MAX_BOXES]


def plan(engine, brief: str, module: str, start_label: str, n: int, default_count: int, taken_labels: set[str],
         labels=None, code: str = "", categories=None) -> dict:
    """Returns {"planner": "deepseek"|"lines", "boxes": [{module,label,theme,count,code}]}. Never creates anything.
    With `labels` the box names are exactly those (one section = Box B2, Box B3, Box A1); otherwise they count up from
    start_label. `code` is the SM section code (for example M8-02) that every box in the plan carries.
    `categories` limits every box to those object categories (empty = all)."""
    brief = brief.strip()
    wanted = clean_labels(labels)
    n = len(wanted) if wanted else max(1, min(MAX_BOXES, n))
    themes, who = None, "lines"
    if engine.name == "deepseek" and brief:
        try:
            themes, who = _themes_from_model(engine, brief, n), "deepseek"
        except Exception:                              # model down: fall back to splitting the lines
            themes = None
    if not themes:
        themes, who = _themes_from_lines(brief, n), "lines"
    if who == "deepseek":
        themes = (themes + [themes[-1]] * n)[:n]       # the model returned fewer lines than asked
    names = wanted[:len(themes)] if wanted else next_labels(start_label, len(themes), taken_labels)
    size = default_count if callable(default_count) else (lambda _label: default_count)    # a number, or one size per box name
    boxes = [{"module": module.strip(), "label": lab, "theme": t[:THEME_MAX], "count": size(lab), "code": code.strip(),
              "categories": list(categories or [])}
             for lab, t in zip(names, themes)]
    return {"planner": who, "boxes": boxes}

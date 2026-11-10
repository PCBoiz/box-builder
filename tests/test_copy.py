"""Keeps filler out of the shipped text (docs/WRITING.md, rules 4 and 9). A human still reads the rest."""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
FILES = ["README.md", "app/static/index.html", "app/static/app.js", "app/automation.py", "app/planner.py",
         "app/service.py", "app/picker.py", "app/main.py", "integrations/google_sheets_apps_script.gs"]

ENGLISH = r"seamless(ly)?|robust|leverag(e|ing)|delve|tapestry|cutting-edge|game-?changer|empower|streamlin(e|ed)|holistic|" \
          r"comprehensive|effortless(ly)?|elevate|it'?s worth noting|in today'?s|a testament to"
VIETNAMESE = r"mạnh mẽ|toàn diện|tối ưu hoá|mượt mà|vô cùng|hết sức|đa dạng và phong phú|dễ dàng nhanh chóng"


def _text(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def test_no_filler_words_in_shipped_text():
    pattern = re.compile(rf"\b({ENGLISH})\b|{VIETNAMESE}", re.I)
    hits = [f"{n}: {m.group(0)}" for n in FILES for m in pattern.finditer(_text(n))]
    assert not hits, hits


def test_no_em_dashes_in_shipped_text():
    assert not [n for n in FILES if "—" in _text(n)]


def test_ui_text_has_no_exclamation_marks():
    for name in ("app/static/index.html", "app/automation.py"):
        body = re.sub(r"<!--.*?-->|<script.*?</script>", "", _text(name), flags=re.S)
        assert "!" not in re.sub(r"<!doctype html>|!=|!important", "", body, flags=re.I), name

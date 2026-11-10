"""Keeps filler out of the shipped text (docs/WRITING.md, rules 4 and 9). A human still reads the rest."""
import os
import re

ROOT = os.path.join(os.path.dirname(__file__), "..")
FILES = ["README.md", "app/static/index.html", "app/static/app.js", "app/automation.py", "app/planner.py",
         "app/service.py", "app/picker.py", "app/main.py", "integrations/google_sheets_apps_script.gs",
         "app/auth.py", "app/user_routes.py", "app/sheet_panel.py", "app/usage.py", "app/static/login.html", "app/static/login.js",
         "app/static/dung.html", "app/static/dung.js", "app/static/usage_chart.js", "docs/LOGIN.md", "docs/SM_SYNC.md", "docs/SC_PROCESS.md"]

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


# The pages people with little technical skill read. Stricter than the list above: padding, apologies, and the old names
# ("Mở lại" is "Bỏ duyệt", "quản trị viên" is "admin", "mô hình" is "AI"). "Hãy" is cut as well: a bare verb is shorter.
PLAIN_FILES = ["app/static/login.html", "app/static/login.js", "app/user_routes.py",
               "app/static/dung.html", "app/static/dung.js"]
PLAIN_BANNED = r"vui lòng|rất tiếc|xin lỗi|thành công|tiến hành|một cách|quản trị viên|mở lại|mô hình|hãy"


def test_plain_pages_have_no_padding_or_old_names():
    pattern = re.compile(PLAIN_BANNED, re.I)
    hits = [f"{n}: {m.group(0)}" for n in PLAIN_FILES for m in pattern.finditer(_text(n))]
    assert not hits, hits

"""Guard rails from the project's hard rules, enforced by the test run so they cannot erode quietly:
the server never talks to Scenario Manager, holds no SM credentials, and only calls the LLM host it is configured for."""
import glob
import os
import re

APP = os.path.join(os.path.dirname(__file__), "..", "app")


def _sources():
    for path in glob.glob(os.path.join(APP, "*.py")):
        with open(path, encoding="utf-8") as f:
            yield os.path.basename(path), f.read()


# images.py names the SM host only as a string: the allowed image host and the base of "open in SM" links.
# It makes no requests (the next test covers that), so the browser, not the server, is what loads those URLs.
HOST_MENTION_OK = {"images.py"}


def test_server_code_never_references_scenario_manager():
    for name, text in _sources():
        if name not in HOST_MENTION_OK:
            assert not re.search(r"sm\.config\.inc|scenario[\s_-]*manager\.", text, re.I), f"{name} mentions an SM host"
        assert not re.search(r"SM_(PASSWORD|TOKEN|USER|COOKIE)", text), f"{name} reads an SM credential"


def test_only_the_picker_makes_outgoing_http_calls():
    for name, text in _sources():
        if name == "picker.py":
            continue
        assert not re.search(r"urlopen|urllib\.request|import requests|import httpx|http\.client|aiohttp", text), \
            f"{name} makes outgoing HTTP calls; only picker.py (the LLM) may"


def test_picker_only_calls_the_configured_llm_base():
    text = dict(_sources())["picker.py"]
    assert len(re.findall(r"urllib\.request\.Request\(", text)) == 1
    assert "self.s.deepseek_base" in text

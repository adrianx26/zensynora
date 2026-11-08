"""2026-11-08: in Chrome the open list of every dropdown was white with light (unreadable) text.

The selects have see-through backgrounds and the page never said it is dark, so Chrome drew
its native list on white. Each theme now sets color-scheme, and options have solid colours."""

import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "webui" / "src" / "index.css").read_text(encoding="utf-8")


def _block(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\}", CSS, re.S)
    assert m, selector
    return m.group(1)


def test_each_theme_tells_the_browser_its_colour_scheme():
    assert "color-scheme: dark" in _block(":root")
    assert "color-scheme: light" in _block('[data-theme="light"]')


def test_options_have_solid_colours():
    rule = _block("select option, select optgroup")
    assert "background-color: var(--bg-color)" in rule and "color: var(--text-primary)" in rule

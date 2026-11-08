"""2026-11-08: "Saved." and other success messages stayed on the screen forever.

Every page that shows a message clears a success message by itself within 3 seconds
(webui/src/notice.ts: useAutoClear); errors stay until the next action."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "webui" / "src"


def test_the_timer_is_at_most_three_seconds():
    m = re.search(r"export const NOTICE_MS = (\d+);", (SRC / "notice.ts").read_text(encoding="utf-8"))
    assert m and int(m.group(1)) <= 3000


def test_every_page_with_a_message_clears_it():
    pages = [p for p in (SRC / "components").glob("*.tsx")
             if "const [notice, setNotice] = useState" in p.read_text(encoding="utf-8")]
    assert len(pages) >= 14
    missing = [p.name for p in pages if "useAutoClear(notice, setNotice)" not in p.read_text(encoding="utf-8")]
    assert not missing, missing

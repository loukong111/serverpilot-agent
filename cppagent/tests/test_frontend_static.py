from __future__ import annotations

import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ElementCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.elements: list[dict[str, str]] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append({name: value or "" for name, value in attrs})


class FrontendStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "webui" / "static" / "index.html").read_text(encoding="utf-8")
        cls.javascript = (ROOT / "webui" / "static" / "app.js").read_text(encoding="utf-8")
        parser = ElementCollector()
        parser.feed(cls.html)
        cls.elements = parser.elements
        cls.by_id = {item["id"]: item for item in cls.elements if item.get("id")}

    def test_javascript_dom_references_exist(self) -> None:
        references = set(re.findall(r'\$\("([^"]+)"\)', self.javascript))
        self.assertEqual([], sorted(references - self.by_id.keys()))

    def test_tabs_have_bidirectional_accessibility_links(self) -> None:
        tabs = [item for item in self.elements if item.get("role") == "tab"]
        self.assertGreaterEqual(len(tabs), 5)
        for tab in tabs:
            tab_id = tab.get("id")
            panel_id = tab.get("aria-controls")
            self.assertTrue(tab_id)
            self.assertIn(panel_id, self.by_id)
            self.assertEqual("tabpanel", self.by_id[panel_id].get("role"))
            self.assertEqual(tab_id, self.by_id[panel_id].get("aria-labelledby"))


if __name__ == "__main__":
    unittest.main()

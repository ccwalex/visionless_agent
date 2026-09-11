#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from adblock import strip_ads  # noqa: E402
from adblock_parse import merge_parsed, parse_filter_list  # noqa: E402
from affordances import inventory_from_html  # noqa: E402
from tab_registry import (  # noqa: E402
    merge_targets,
    parse_tab_header,
    prepend_tab_header,
    render_tab_header,
)


def fixture(name: str) -> str:
    path = os.path.join(ROOT, "tests", "fixtures", name)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TabRegistryTests(unittest.TestCase):
    def test_merge_assigns_stable_ids(self) -> None:
        registry = {"next_index": 0, "tabs": {}}
        targets = [
            {"id": "A", "url": "https://a.test", "title": "A"},
            {"id": "B", "url": "https://b.test", "title": "B"},
        ]
        tabs = merge_targets(registry, targets, {"B": "A"})
        self.assertEqual([t["id"] for t in tabs], [0, 1])
        self.assertEqual(tabs[1]["opened_from"], 0)
        self.assertEqual(registry["next_index"], 2)

    def test_merge_keeps_ids_for_existing_targets(self) -> None:
        registry = {"next_index": 2, "tabs": {"A": {"index": 0, "opener_index": None}}}
        targets = [{"id": "A", "url": "https://a.test/2", "title": "A2"}]
        tabs = merge_targets(registry, targets)
        self.assertEqual(tabs[0]["id"], 0)
        self.assertEqual(tabs[0]["url"], "https://a.test/2")

    def test_parse_tab_header(self) -> None:
        html = render_tab_header({"id": 1, "opened_from": 0, "url": "https://x.test"}) + "<p>body</p>"
        meta, body = parse_tab_header(html)
        self.assertIsNotNone(meta)
        assert meta is not None
        self.assertEqual(meta["id"], 1)
        self.assertEqual(meta["opened_from"], 0)
        self.assertIn("<p>body</p>", body)

    def test_prepend_tab_header(self) -> None:
        out = prepend_tab_header("<html></html>", {"id": 0, "opened_from": None, "url": "https://z.test"})
        self.assertIn('data-inspect-tab="0"', out)
        self.assertIn("tab 0 origin", out)


class AdblockTests(unittest.TestCase):
    def test_parse_easylist_sample(self) -> None:
        sample = fixture("easylist_sample.txt")
        parsed = parse_filter_list(sample, source_id="sample")
        self.assertIn("doubleclick.net", parsed["hosts"])
        self.assertIn("googlesyndication.com", parsed["hosts"])
        self.assertIn("cdn.example.com", parsed["exception_hosts"])
        self.assertIn("adsbygoogle", parsed["cosmetic_classes"])
        merged = merge_parsed(parsed)
        self.assertNotIn("cdn.example.com", merged["hosts"])

    def test_strip_ads_removes_known_markers(self) -> None:
        html = fixture("ads_and_content.html")
        result = strip_ads(html)
        self.assertNotIn("adsbygoogle", result["html"])
        self.assertNotIn("doubleclick.net", result["html"])
        self.assertGreater(len(result["removed"]), 0)

    def test_strip_ads_keeps_content(self) -> None:
        html = fixture("ads_and_content.html")
        result = strip_ads(html)
        self.assertIn("Real article title", result["html"])
        self.assertIn("Read more", result["html"])


class TabHeaderInventoryTests(unittest.TestCase):
    def test_tab_header_not_a_control(self) -> None:
        inv = inventory_from_html(fixture("tab_header.html"), url="https://example.test/page")
        self.assertEqual(inv["tab"]["id"], 1)
        self.assertEqual(inv["tab"]["opened_from"], 0)
        selectors = [c["selector"] for c in inv["controls"]]
        self.assertFalse(any("data-inspect-tab" in s for s in selectors))
        self.assertEqual(inv["controls"][0]["selector"], "#go")


class FullInventoryTests(unittest.TestCase):
    def test_uncapped_links(self) -> None:
        inv = inventory_from_html(fixture("ads_and_content.html"), url="https://article.test/")
        self.assertEqual(len(inv["links"]), 3)

    def test_contents_present(self) -> None:
        inv = inventory_from_html(fixture("ads_and_content.html"), url="https://article.test/")
        self.assertEqual(inv["contents"]["headings"][0]["text"], "Real article title")
        self.assertIn("article body", inv["contents"]["text"])
        self.assertGreater(inv["contents"]["text_chars"], 10)

    def test_adblock_inventory_field(self) -> None:
        html = fixture("ads_and_content.html")
        stripped = strip_ads(html)
        inv = inventory_from_html(
            stripped["html"],
            url="https://article.test/",
            adblock={"purpose": "llm_readable_source", "enabled": True, "removed_count": len(stripped["removed"]), "removed": stripped["removed"]},
        )
        self.assertEqual(inv["adblock"]["purpose"], "llm_readable_source")
        self.assertTrue(inv["adblock"]["enabled"])
        self.assertGreater(inv["adblock"]["removed_count"], 0)


class CliTabTests(unittest.TestCase):
    def test_close_tab_flag_in_help(self) -> None:
        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "inspect.py"), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("--close-tab", proc.stdout)


if __name__ == "__main__":
    unittest.main()

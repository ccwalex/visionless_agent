#!/usr/bin/env python3
"""Parser unit tests plus a live DuckDuckGo Lite search (no clicks)."""

from __future__ import annotations

import json
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import text_search  # noqa: E402


DDG_SAMPLE = """
<table>
  <tr>
    <td>
      <a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fcursor.com%2Fdocs%2Fskills"
         class='result-link'>Agent Skills | Cursor Docs</a>
    </td>
  </tr>
  <tr>
    <td>&nbsp;</td>
    <td class='result-snippet'>Extend AI <b>agents</b> with specialized capabilities.</td>
  </tr>
</table>
"""

WIKI_SAMPLE = {
    "query": {
        "search": [
            {
                "title": "Skill",
                "snippet": "A <span>skill</span> is the learned ability to perform.",
            }
        ]
    }
}


class ParserTests(unittest.TestCase):
    def test_ddg_lite_extracts_decoded_url(self) -> None:
        hits = text_search.parse_ddg_lite(DDG_SAMPLE)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].title, "Agent Skills | Cursor Docs")
        self.assertEqual(hits[0].url, "https://cursor.com/docs/skills")
        self.assertIn("agents", hits[0].snippet)

    def test_wikipedia_api(self) -> None:
        hits = text_search.parse_wikipedia_api(json.dumps(WIKI_SAMPLE))
        self.assertEqual(hits[0].title, "Skill")
        self.assertTrue(hits[0].url.endswith("/wiki/Skill"))

    def test_google_js_shell_is_blocked(self) -> None:
        html = "<html><head><title>Google Search</title></head><body><a href='/'>G</a></body></html>"
        self.assertEqual(
            text_search.looks_blocked(html, 200),
            "google_js_shell_no_results",
        )

    def test_results_urls_are_gettable(self) -> None:
        url = text_search.results_url("duckduckgo", "hello world")
        self.assertIn("lite.duckduckgo.com", url)
        self.assertIn("q=hello+world", url)
        g = text_search.results_url("google", "hello")
        self.assertIn("gbv=1", g)


class LiveSearchTests(unittest.TestCase):
    def test_live_duckduckgo_or_wikipedia(self) -> None:
        payload = text_search.run_chain(
            "cursor editor agent skills",
            ["duckduckgo", "wikipedia", "hackernews"],
            "http",
            "",
            None,
        )
        self.assertTrue(payload["ok"], msg=json.dumps(payload["attempts"], indent=2))
        hits = payload["result"]["hits"]
        self.assertGreaterEqual(len(hits), 1)
        self.assertTrue(hits[0]["url"].startswith("http"))


if __name__ == "__main__":
    unittest.main()

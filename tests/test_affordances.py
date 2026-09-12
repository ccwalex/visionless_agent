#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from affordances import inventory_from_html, project_inventory  # noqa: E402


def fixture(name: str) -> str:
    path = os.path.join(ROOT, "tests", "fixtures", name)
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class LoginFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inv = inventory_from_html(fixture("login.html"), url="https://example.test/login")

    def test_auth_handoff(self) -> None:
        self.assertTrue(self.inv["auth"]["likely"])
        self.assertIn("password_input", self.inv["auth"]["signals"])
        kinds = [p["kind"] for p in self.inv["pathways"]]
        self.assertIn("handoff", kinds)

    def test_login_form_fields(self) -> None:
        form = self.inv["forms"][0]
        self.assertEqual(form["selector"], "#login")
        self.assertEqual(form["method"], "post")
        names = {f["name"] for f in form["fields"]}
        self.assertEqual(names, {"email", "password"})
        email = next(f for f in form["fields"] if f["name"] == "email")
        self.assertEqual(email["selector"], "#email")
        self.assertTrue(email["required"])

    def test_oauth_button_exposed(self) -> None:
        texts = [b["text"] for b in self.inv["buttons"]]
        self.assertTrue(any("Google" in t for t in texts))


class SearchFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inv = inventory_from_html(fixture("search_form.html"), url="https://library.test/")

    def test_no_login(self) -> None:
        self.assertFalse(self.inv["auth"]["likely"])

    def test_search_pathway_prefers_get(self) -> None:
        search = next(p for p in self.inv["pathways"] if p["id"].startswith("search-form"))
        self.assertEqual(search["kind"], "fill_and_submit")
        self.assertEqual(search["fill"]["selector"], "#q")
        self.assertIn("GET", search["get_shortcut"])
        self.assertIn("q=", search["get_shortcut"])

    def test_filter_form_and_loose_button(self) -> None:
        ids = {f["id"] for f in self.inv["forms"]}
        self.assertEqual(ids, {"search", "filters"})
        export = next(b for b in self.inv["buttons"] if b["id"] == "export")
        self.assertEqual(export["text"], "Export CSV")
        hrefs = [l["href"] for l in self.inv["links"]]
        self.assertIn("/advanced", hrefs)


class CliTests(unittest.TestCase):
    def test_inspect_html_json(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(ROOT, "scripts", "inspect.py"),
                "--html",
                os.path.join(ROOT, "tests", "fixtures", "search_form.html"),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertGreaterEqual(len(data["forms"]), 2)
        self.assertFalse(data["handoff"]["required"])

    def test_inspect_login_exit_code(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(ROOT, "scripts", "inspect.py"),
                "--html",
                os.path.join(ROOT, "tests", "fixtures", "login.html"),
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 3, proc.stdout)
        data = json.loads(proc.stdout)
        self.assertTrue(data["handoff"]["required"])
        self.assertIn("--cdp", " ".join(data["handoff"]["resume"]))


class FilterViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inv = inventory_from_html(fixture("search_form.html"), url="https://library.test/")

    def test_next_drops_chrome(self) -> None:
        view = project_inventory(self.inv, filter_spec="next")
        self.assertIn("pathways", view)
        self.assertIn("auth", view)
        self.assertNotIn("buttons", view)
        self.assertNotIn("links", view)
        self.assertNotIn("forms", view)
        self.assertEqual(view["counts"]["forms"], 2)
        self.assertEqual(view["counts"]["buttons"], 1)

    def test_search_keeps_query_form_only(self) -> None:
        view = project_inventory(self.inv, filter_spec="search")
        ids = {f["id"] for f in view["forms"]}
        self.assertEqual(ids, {"search"})
        kinds = {p["kind"] for p in view["pathways"]}
        self.assertIn("fill_and_submit", kinds)
        self.assertNotIn("activate", kinds)

    def test_limit_caps_buttons(self) -> None:
        view = project_inventory(self.inv, limit=0)
        self.assertEqual(view["buttons"], [])
        self.assertEqual(view["counts"]["buttons"], 1)
        self.assertEqual(view["limit"], 0)

    def test_unknown_token(self) -> None:
        with self.assertRaises(ValueError):
            project_inventory(self.inv, filter_spec="screenshots")


class CliFilterTests(unittest.TestCase):
    def test_filter_next_json(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(ROOT, "scripts", "inspect.py"),
                "--html",
                os.path.join(ROOT, "tests", "fixtures", "search_form.html"),
                "--json",
                "--filter",
                "next",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["filter"], ["next"])
        self.assertIn("pathways", data)
        self.assertNotIn("links", data)


class LynxIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.inv = inventory_from_html(fixture("dense_chrome.html"), url="https://market.test/")
        self.search = inventory_from_html(fixture("search_form.html"), url="https://library.test/")

    def test_hidden_and_chrome_elided(self) -> None:
        texts = [b["text"] for b in self.inv["buttons"]]
        self.assertNotIn("Hidden ghost", texts)
        self.assertNotIn("Aria ghost", texts)
        hrefs = [l["href"] for l in self.inv["links"]]
        self.assertNotIn("/secret", hrefs)
        self.assertTrue(any("facebook" in h for h in hrefs) is False)
        self.assertIn("/news/diesel", hrefs)

    def test_activate_skips_chrome_keeps_export(self) -> None:
        activate = [p for p in self.inv["pathways"] if p["kind"] == "activate"]
        texts = [p.get("text") for p in activate]
        self.assertNotIn("Accept all cookies", texts)
        self.assertNotIn("Share", texts)
        self.assertIn("Export CSV", texts)

    def test_search_ranked_before_activate(self) -> None:
        kinds = [p["kind"] for p in self.inv["pathways"]]
        self.assertEqual(kinds[0], "fill_and_submit")
        self.assertTrue(self.inv["pathways"][0]["id"].startswith("search-form"))

    def test_refs_are_monotonic(self) -> None:
        refs = [p["ref"] for p in self.search["pathways"]]
        self.assertEqual(refs, list(range(1, len(refs) + 1)))
        export = next(b for b in self.search["buttons"] if b["id"] == "export")
        self.assertIsInstance(export["ref"], int)

    def test_links_only_renumbers(self) -> None:
        view = project_inventory(self.inv, filter_spec="links-only")
        self.assertNotIn("pathways", view)
        self.assertNotIn("buttons", view)
        self.assertEqual(view["links"][0]["ref"], 1)
        self.assertTrue(all("/news/" in l["href"] or l["href"].startswith("/") for l in view["links"]))


class CliLinksOnlyTests(unittest.TestCase):
    def test_links_only_flag(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                os.path.join(ROOT, "scripts", "inspect.py"),
                "--html",
                os.path.join(ROOT, "tests", "fixtures", "search_form.html"),
                "--json",
                "--links-only",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["filter"], ["links-only"])
        self.assertIn("links", data)
        self.assertNotIn("pathways", data)
        self.assertEqual(data["links"][0]["ref"], 1)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Inspect a Chrome page (or saved HTML) for buttons, forms, and login handoff.

This is not a text-only browser. It prints a deterministic interaction map so
agents can choose selectors/pathways without screenshots or mouse coordinates.

  python3 scripts/inspect.py --html tests/fixtures/login.html
  python3 scripts/inspect.py --url https://example.com
  python3 scripts/inspect.py --cdp 9222
  python3 scripts/inspect.py --cdp 9222 --list-tabs
  python3 scripts/inspect.py --cdp 9222 --tab 1
  python3 scripts/inspect.py --cdp 9222 --new-tab https://example.com
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from adblock import strip_ads  # noqa: E402
from affordances import inventory_from_html  # noqa: E402
from tab_registry import (  # noqa: E402
    parse_tab_header,
    prepend_tab_header,
)

HANDOFF_EXIT = 3


def find_chrome() -> str:
    for candidate in (
        os.environ.get("CHROME_BIN"),
        "/usr/local/bin/google-chrome",
        "/usr/bin/google-chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "google-chrome",
        "chromium",
        "chromium-browser",
    ):
        if not candidate:
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        which = subprocess.run(["which", candidate], capture_output=True, text=True)
        if which.returncode == 0 and which.stdout.strip():
            return which.stdout.strip()
    raise FileNotFoundError("Chrome/Chromium not found; set CHROME_BIN")


def dump_dom(url: str, chrome_bin: str) -> str:
    user_data = tempfile.mkdtemp(prefix="inspect-chrome-")
    cmd = [
        chrome_bin,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-background-networking",
        f"--user-data-dir={user_data}",
        "--dump-dom",
        url,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    finally:
        subprocess.run(["rm", "-rf", user_data], check=False)
    html = proc.stdout or ""
    if not html.strip():
        raise RuntimeError(proc.stderr.strip() or f"chrome dump-dom failed ({proc.returncode})")
    return html


def fetch_html(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/128.0.0.0 Safari/537.36",
            "Accept": "text/html",
        },
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", "replace")


def cdp_call(
    port: int,
    *,
    navigate: str | None = None,
    target: str | None = None,
    tab: int | None = None,
    list_tabs: bool = False,
    new_tab: str | None = None,
    include_hidden: bool = False,
    fill: str | None = None,
    value: str | None = None,
    submit_form: str | None = None,
    live_dom: bool = False,
    no_adblock: bool = False,
) -> dict:
    cmd = [
        "node",
        os.path.join(SCRIPT_DIR, "inspect_cdp.mjs"),
        "--port",
        str(port),
    ]
    if list_tabs:
        cmd.append("--list-tabs")
    if tab is not None:
        cmd += ["--tab", str(tab)]
    if new_tab:
        cmd += ["--new-tab", new_tab]
    if navigate:
        cmd += ["--navigate", navigate]
    if target:
        cmd += ["--target", target]
    if include_hidden:
        cmd.append("--include-hidden")
    if fill:
        cmd += ["--fill", fill, "--value", value or ""]
    if submit_form:
        cmd += ["--submit-form", submit_form]
    if live_dom:
        cmd.append("--live-dom")
    if no_adblock:
        cmd.append("--no-adblock")
    if list_tabs or (not fill and not new_tab):
        pass
    elif fill and not list_tabs:
        pass
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    stdout = proc.stdout.strip()
    stderr = proc.stderr.strip()
    payload = stdout or stderr
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError(stderr or stdout or str(exc)) from exc
    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data.get("message") or data["error"])
    if proc.returncode != 0:
        raise RuntimeError(stderr or payload or f"inspect_cdp exited {proc.returncode}")
    return data


def build_inventory(
    html: str,
    url: str,
    *,
    tab: dict | None = None,
    no_adblock: bool = False,
    max_links: int | None = None,
    max_text: int = 20000,
    source: str = "html-dump",
) -> tuple[str, dict]:
    parsed_tab, body = parse_tab_header(html)
    tab_meta = tab or parsed_tab or {"id": 0, "opened_from": None, "url": url}
    if tab and parsed_tab is None:
        tab_meta = {**tab_meta, "url": tab_meta.get("url") or url}

    adblock_info: dict
    if no_adblock:
        adblock_info = {"enabled": False, "removed_count": 0, "removed": []}
        clean_html = body
    else:
        stripped = strip_ads(body)
        clean_html = stripped["html"]
        adblock_info = {
            "enabled": True,
            "removed_count": len(stripped["removed"]),
            "removed": stripped["removed"],
        }

    inv = inventory_from_html(
        clean_html,
        url=url,
        tab=tab_meta,
        adblock=adblock_info,
        max_links=max_links,
        max_text=max_text,
        source=source,
    )
    return clean_html, inv


def attach_handoff(inv: dict, args: argparse.Namespace) -> dict:
    auth = inv.get("auth") or {}
    needs = bool(auth.get("likely") or auth.get("captcha"))
    resume = ["python3", "scripts/inspect.py", "--cdp", str(args.cdp or 9222), "--json"]
    if args.tab is not None:
        resume += ["--tab", str(args.tab)]
    elif args.url:
        resume += ["--target", args.url]
    inv["handoff"] = {
        "required": needs,
        "kinds": [p["reason"] for p in inv.get("pathways") or [] if p.get("kind") == "handoff"],
        "instruction": (
            "Complete login/captcha in the headed Chrome window that has "
            f"--remote-debugging-port={args.cdp or 9222}, then re-run: "
            + " ".join(resume)
        )
        if needs
        else None,
        "resume": resume if needs else None,
    }
    return inv


def print_human(inv: dict) -> None:
    tab = inv.get("tab") or {}
    if tab:
        opened = tab.get("opened_from")
        origin = f" from tab {opened}" if opened is not None else ""
        print(f"tab: {tab.get('id')}{origin}  url: {tab.get('url') or inv.get('url')}")
    print(f"source: {inv.get('source')}  url: {inv.get('url')}")
    print(f"title: {inv.get('title')}")
    adblock = inv.get("adblock") or {}
    if adblock.get("enabled"):
        print(f"adblock: removed {adblock.get('removed_count', 0)} nodes")
    auth = inv.get("auth") or {}
    print(
        f"auth.likely: {auth.get('likely')}  captcha: {auth.get('captcha')}  "
        f"signals: {', '.join(auth.get('signals') or []) or 'none'}"
    )
    handoff = inv.get("handoff") or {}
    if handoff.get("required"):
        print("HANDOFF REQUIRED")
        print(f"  {handoff.get('instruction')}")
    contents = inv.get("contents") or {}
    print(f"controls: {len(inv.get('controls') or [])}  links: {len(inv.get('links') or [])}")
    print(f"text_chars: {contents.get('text_chars', 0)}")
    print("pathways:")
    if not inv.get("pathways"):
        print("  (none)")
    for p in inv.get("pathways") or []:
        print(f"  - [{p.get('kind')}] {p.get('id')}: {p.get('summary')}")
    print("forms:")
    for form in inv.get("forms") or []:
        print(
            f"  - {form.get('selector')} {form.get('method', '').upper()} {form.get('action') or '.'}"
        )
        for field in form.get("fields") or []:
            req = " required" if field.get("required") else ""
            print(
                f"      field {field.get('selector')} type={field.get('type')} "
                f"name={field.get('name')} label={field.get('label')!r}{req}"
            )
        for sub in form.get("submits") or []:
            print(f"      submit {sub.get('selector')} {sub.get('text')!r}")
    loose = inv.get("loose_fields") or []
    if loose:
        print("loose_fields:")
        for field in loose:
            print(f"  - {field.get('selector')} type={field.get('type')} label={field.get('label')!r}")
    print("buttons:")
    for button in inv.get("buttons") or []:
        print(f"  - {button.get('selector')} {button.get('kind')} {button.get('text')!r}")
    links = inv.get("links") or []
    if links:
        print("links (sample):")
        for link in links[:12]:
            print(f"  - {link.get('text')!r} -> {link.get('href')}")


def run(args: argparse.Namespace) -> tuple[dict, int]:
    if args.cdp and args.list_tabs:
        data = cdp_call(args.cdp, list_tabs=True)
        return data, 0

    if args.html:
        with open(args.html, encoding="utf-8") as fh:
            html = fh.read()
        _, inv = build_inventory(
            html,
            url=args.url or args.html,
            no_adblock=args.no_adblock,
            max_links=args.max_links,
            max_text=args.max_text,
        )
        inv = attach_handoff(inv, args)
        return inv, HANDOFF_EXIT if inv["handoff"]["required"] and not args.ignore_handoff else 0

    if args.cdp:
        deadline = time.time() + args.wait_login if args.wait_login else None
        navigate = None
        if args.navigate:
            navigate = args.navigate
        elif args.url and args.force_navigate:
            navigate = args.url
        while True:
            data = cdp_call(
                args.cdp,
                navigate=navigate,
                target=args.target or (None if args.tab is not None else args.url),
                tab=args.tab,
                new_tab=args.new_tab,
                include_hidden=args.include_hidden,
                fill=args.fill,
                value=args.value,
                submit_form=args.submit_form,
                live_dom=args.live_dom,
                no_adblock=args.no_adblock,
            )
            if args.live_dom and data.get("source") == "live-dom":
                inv = attach_handoff(data, args)
                return inv, HANDOFF_EXIT if inv["handoff"]["required"] and not args.ignore_handoff else 0

            if data.get("mode") == "html_for_inventory":
                tab = data.get("tab") or {}
                url = data.get("url") or tab.get("url") or args.url or ""
                title = data.get("title") or tab.get("title") or ""
                raw_html = data.get("raw_html") or ""
                _, inv = build_inventory(
                    raw_html,
                    url=url,
                    tab=tab,
                    no_adblock=args.no_adblock or data.get("no_adblock"),
                    max_links=args.max_links,
                    max_text=args.max_text,
                    source="cdp-html",
                )
                inv["title"] = inv.get("title") or title
                if args.save_html:
                    tab_meta = inv.get("tab") or tab
                    clean = raw_html if args.no_adblock else strip_ads(raw_html)["html"]
                    with open(args.save_html, "w", encoding="utf-8") as fh:
                        fh.write(prepend_tab_header(clean, tab_meta))
                    inv["saved_html"] = args.save_html
            else:
                inv = attach_handoff(data, args)
                return inv, HANDOFF_EXIT if inv["handoff"]["required"] and not args.ignore_handoff else 0

            inv = attach_handoff(inv, args)
            if not inv["handoff"]["required"]:
                return inv, 0
            if deadline is None:
                return inv, HANDOFF_EXIT
            if time.time() >= deadline:
                inv["handoff"]["timed_out"] = True
                return inv, HANDOFF_EXIT
            print(
                f"waiting for user login in Chrome :{args.cdp} "
                f"({int(deadline - time.time())}s left)…",
                file=sys.stderr,
            )
            time.sleep(3)
            navigate = None
            args.new_tab = None

    url = args.url
    if not url:
        raise SystemExit("Provide --url, --html, or --cdp")

    html = None
    source_note = None
    if args.dump_dom:
        html = dump_dom(url, find_chrome())
        source_note = "chrome-dump-dom"
    else:
        html = fetch_html(url)
        source_note = "http"
    _, inv = build_inventory(
        html,
        url=url,
        no_adblock=args.no_adblock,
        max_links=args.max_links,
        max_text=args.max_text,
        source=source_note,
    )
    inv["fetch"] = source_note
    inv = attach_handoff(inv, args)
    code = HANDOFF_EXIT if inv["handoff"]["required"] and not args.ignore_handoff else 0
    if args.save_html:
        tab = inv.get("tab") or {"id": 0, "opened_from": None, "url": url}
        out_html = prepend_tab_header(html if args.no_adblock else strip_ads(html)["html"], tab)
        with open(args.save_html, "w", encoding="utf-8") as fh:
            fh.write(out_html)
        inv["saved_html"] = args.save_html
    return inv, code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Map buttons/forms/pathways from Chrome DOM or HTML. No screenshots, no clicks."
    )
    p.add_argument("--url", help="Page URL (dump-dom / HTTP) or CDP target hint")
    p.add_argument("--html", help="Inspect a saved HTML file instead of Chrome")
    p.add_argument("--cdp", type=int, metavar="PORT", help="Attach to Chrome remote debugging port")
    p.add_argument("--list-tabs", action="store_true", help="List all CDP tabs with stable ids")
    p.add_argument("--tab", type=int, metavar="N", help="Inspect tab N (activates it)")
    p.add_argument("--new-tab", metavar="URL", help="Open URL in a child tab of --tab (default 0)")
    p.add_argument("--target", help="Substring match for an existing CDP tab URL/title")
    p.add_argument("--navigate", help="Ask the attached Chrome tab to open this URL first")
    p.add_argument(
        "--force-navigate",
        action="store_true",
        help="With --cdp --url, navigate that tab (default is follow whatever tab is open)",
    )
    p.add_argument(
        "--wait-login",
        type=int,
        metavar="SECONDS",
        help="If login/captcha is detected, poll CDP until it clears or timeout (user completes it in headed Chrome)",
    )
    p.add_argument("--dump-dom", action="store_true", help="Fetch --url via headless Chrome --dump-dom instead of HTTP")
    p.add_argument("--fill", metavar="SELECTOR", help="With --cdp: set this field's value (native setter + input/change events)")
    p.add_argument("--value", help="Value for --fill")
    p.add_argument(
        "--submit-form",
        metavar="SELECTOR",
        help="With --fill: requestSubmit() this form (no mouse click)",
    )
    p.add_argument("--include-hidden", action="store_true")
    p.add_argument("--live-dom", action="store_true", help="Use live DOM extractor instead of cleaned HTML inventory")
    p.add_argument("--no-adblock", action="store_true", help="Do not strip ad markup before inventory")
    p.add_argument("--max-links", type=int, metavar="N", help="Cap link count in inventory (default: no cap)")
    p.add_argument("--max-text", type=int, default=20000, help="Max visible text chars in contents.text")
    p.add_argument("--ignore-handoff", action="store_true", help="Exit 0 even when login/captcha is present")
    p.add_argument("--json", action="store_true")
    p.add_argument("--save-html", metavar="PATH")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        inv, code = run(args)
    except Exception as exc:
        err = {"error": "inspect_failed", "message": str(exc)}
        if getattr(args, "json", False):
            json.dump(err, sys.stdout, indent=2)
            sys.stdout.write("\n")
        else:
            print(f"inspect_failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        json.dump(inv, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        if args.list_tabs:
            for tab in inv.get("tabs") or []:
                opened = tab.get("opened_from")
                origin = f" from tab {opened}" if opened is not None else ""
                print(f"tab {tab['id']}{origin}: {tab.get('title')!r} {tab.get('url')}")
        else:
            print_human(inv)
    return code


if __name__ == "__main__":
    raise SystemExit(main())

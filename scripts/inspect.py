#!/usr/bin/env python3
"""Inspect a Chrome page (or saved HTML) for buttons, forms, and login handoff.

This is not a text-only browser. It prints a deterministic interaction map so
agents can choose selectors/pathways without screenshots or mouse coordinates.

  python3 scripts/inspect.py --html tests/fixtures/login.html
  python3 scripts/inspect.py --url https://example.com
  python3 scripts/inspect.py --cdp 9222
  python3 scripts/inspect.py --cdp 9222 --wait-login
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

from affordances import inventory_from_html  # noqa: E402

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


def cdp_inspect(port: int, navigate: str | None, target: str | None, include_hidden: bool) -> dict:
    cmd = [
        "node",
        os.path.join(SCRIPT_DIR, "inspect_cdp.mjs"),
        "--port",
        str(port),
    ]
    if navigate:
        cmd += ["--navigate", navigate]
    if target:
        cmd += ["--target", target]
    if include_hidden:
        cmd.append("--include-hidden")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
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


def attach_handoff(inv: dict, args: argparse.Namespace) -> dict:
    auth = inv.get("auth") or {}
    needs = bool(auth.get("likely") or auth.get("captcha"))
    resume = ["python3", "scripts/inspect.py", "--cdp", str(args.cdp or 9222), "--json"]
    if args.url:
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
    print(f"source: {inv.get('source')}  url: {inv.get('url')}")
    print(f"title: {inv.get('title')}")
    auth = inv.get("auth") or {}
    print(
        f"auth.likely: {auth.get('likely')}  captcha: {auth.get('captcha')}  "
        f"signals: {', '.join(auth.get('signals') or []) or 'none'}"
    )
    handoff = inv.get("handoff") or {}
    if handoff.get("required"):
        print("HANDOFF REQUIRED")
        print(f"  {handoff.get('instruction')}")
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
    if args.html:
        with open(args.html, encoding="utf-8") as fh:
            html = fh.read()
        inv = inventory_from_html(html, url=args.url or args.html)
        inv = attach_handoff(inv, args)
        return inv, HANDOFF_EXIT if inv["handoff"]["required"] and not args.ignore_handoff else 0

    if args.cdp:
        deadline = time.time() + args.wait_login if args.wait_login else None
        navigate = args.navigate or (args.url if args.cdp and args.navigate else None)
        # Only auto-navigate when explicitly asked; attaching should follow the user's tab.
        if args.navigate:
            navigate = args.navigate
        elif args.url and args.force_navigate:
            navigate = args.url
        else:
            navigate = None
        while True:
            inv = cdp_inspect(args.cdp, navigate, args.target or args.url, args.include_hidden)
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
            navigate = None  # do not reload the login page while the user is signing in

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
    inv = inventory_from_html(html, url=url)
    inv["fetch"] = source_note
    inv = attach_handoff(inv, args)
    code = HANDOFF_EXIT if inv["handoff"]["required"] and not args.ignore_handoff else 0
    if args.save_html:
        with open(args.save_html, "w", encoding="utf-8") as fh:
            fh.write(html)
        inv["saved_html"] = args.save_html
    return inv, code


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Map buttons/forms/pathways from Chrome DOM or HTML. No screenshots, no clicks."
    )
    p.add_argument("--url", help="Page URL (dump-dom / HTTP) or CDP target hint")
    p.add_argument("--html", help="Inspect a saved HTML file instead of Chrome")
    p.add_argument("--cdp", type=int, metavar="PORT", help="Attach to Chrome remote debugging port")
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
    p.add_argument("--include-hidden", action="store_true")
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
        print_human(inv)
    if args.save_html and args.cdp:
        # live DOM path has no raw dump unless we add one later
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())

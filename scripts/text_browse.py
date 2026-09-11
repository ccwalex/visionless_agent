#!/usr/bin/env python3
"""Fetch a page by URL and inspect source/text/links — no clicks, no screenshots."""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse

from text_search import http_get, looks_blocked, visible_text


def extract_links(html: str, base: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for href, inner in re.findall(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', html, re.I | re.S):
        text = re.sub(r"<[^>]+>", "", inner).strip()
        url = urllib.parse.urljoin(base, href)
        if url in seen or href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        seen.add(url)
        links.append({"text": text[:200], "url": url})
        if len(links) >= 80:
            break
    return links


def page_title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a URL from page source (text-first).")
    parser.add_argument("url")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--save-html", metavar="PATH")
    parser.add_argument("--max-text", type=int, default=4000)
    args = parser.parse_args(argv)

    status, final, body = http_get(args.url)
    blocked = looks_blocked(body, status)
    title = page_title(body)
    text = visible_text(body)
    links = extract_links(body, final)
    report = {
        "requested_url": args.url,
        "final_url": final,
        "status": status,
        "blocked": bool(blocked),
        "block_reason": blocked,
        "title": title,
        "source_chars": len(body),
        "text": text[: args.max_text],
        "links": links[:40],
    }
    if args.save_html:
        with open(args.save_html, "w", encoding="utf-8") as fh:
            fh.write(body)
        report["saved_html"] = args.save_html

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"status: {status} blocked={bool(blocked)} reason={blocked}")
        print(f"title: {title}")
        print(f"final_url: {final}")
        print(f"source_chars: {len(body)}")
        print("--- text ---")
        print(text[: args.max_text])
        print("--- links ---")
        for item in links[:25]:
            print(f"- {item['text'][:80]} {item['url']}")
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())

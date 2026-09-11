#!/usr/bin/env python3
"""URL-only web search for agents that cannot (or should not) use vision.

Navigate by constructing a results URL. Never click a Search button.
Inspect HTML/source, then parse structured hits.

Datacenter IPs are often blocked by Google. Prefer DuckDuckGo Lite, Wikipedia,
or Hacker News, or re-run with --chrome / --cdp on a residential machine.
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Iterable

from html_text import visible_text

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 25
CTX = ssl.create_default_context()

BLOCK_MARKERS = (
    "unusual traffic",
    "enable javascript",
    "detected unusual",
    "captcha",
    "are you a robot",
    "sorry/index",
    "our systems have detected",
)


@dataclass
class Hit:
    title: str
    url: str
    snippet: str = ""
    engine: str = ""


@dataclass
class SearchReport:
    query: str
    engine: str
    results_url: str
    final_url: str
    status: int
    blocked: bool
    block_reason: str | None
    hits: list[Hit]
    source_chars: int
    fetch_mode: str
    notes: list[str]


def looks_blocked(html: str, status: int) -> str | None:
    if status in {403, 429, 503}:
        return f"http_{status}"
    low = html.lower()
    for marker in BLOCK_MARKERS:
        if marker in low:
            return marker
    # Google gbv=1 often returns a JS shell with almost no result anchors.
    if "<title>google search</title>" in low and html.count("<a href") < 5:
        return "google_js_shell_no_results"
    if "to continue, please enable javascript" in low:
        return "javascript_wall"
    return None


def http_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> tuple[int, str, str]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, resp.geturl(), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace") if exc.fp else ""
        return exc.code, url, body


def chrome_dump_dom(url: str, chrome_bin: str) -> tuple[int, str, str]:
    cmd = [
        chrome_bin,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--disable-background-networking",
        f"--user-agent={USER_AGENT}",
        "--dump-dom",
        url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    html = proc.stdout or ""
    if proc.returncode != 0 and not html:
        raise RuntimeError(proc.stderr.strip() or f"chrome exited {proc.returncode}")
    return 200, url, html


def decode_ddg_redirect(href: str) -> str:
    if href.startswith("//"):
        href = "https:" + href
    parsed = urllib.parse.urlparse(href)
    qs = urllib.parse.parse_qs(parsed.query)
    if "uddg" in qs:
        return urllib.parse.unquote(qs["uddg"][0])
    return href


def parse_ddg_lite(html: str) -> list[Hit]:
    hits: list[Hit] = []
    q = r"['\"]"
    pattern = re.compile(
        rf"<a[^>]*class={q}result-link{q}[^>]*href={q}([^'\"]+){q}[^>]*>(.*?)</a>"
        rf".*?class={q}result-snippet{q}[^>]*>(.*?)</td>",
        re.I | re.S,
    )
    alt = re.compile(
        rf"<a[^>]*href={q}([^'\"]+){q}[^>]*class={q}result-link{q}[^>]*>(.*?)</a>"
        rf".*?class={q}result-snippet{q}[^>]*>(.*?)</td>",
        re.I | re.S,
    )
    for rx in (pattern, alt):
        for href, title, snippet in rx.findall(html):
            title_txt = re.sub(r"<[^>]+>", "", title).strip()
            snippet_txt = re.sub(r"<[^>]+>", "", snippet).strip()
            url = decode_ddg_redirect(html_lib.unescape(href))
            if title_txt and url:
                hits.append(Hit(title_txt, url, snippet_txt, "duckduckgo"))
        if hits:
            break
    return _dedupe(hits)


def parse_wikipedia_api(body: str) -> list[Hit]:
    data = json.loads(body)
    hits: list[Hit] = []
    for item in data.get("query", {}).get("search", []):
        title = item.get("title") or ""
        snippet = re.sub(r"<[^>]+>", "", item.get("snippet") or "")
        slug = urllib.parse.quote(title.replace(" ", "_"))
        hits.append(
            Hit(title, f"https://en.wikipedia.org/wiki/{slug}", snippet, "wikipedia")
        )
    return hits


def parse_hn(body: str) -> list[Hit]:
    data = json.loads(body)
    hits: list[Hit] = []
    for item in data.get("hits", []):
        title = item.get("title") or ""
        url = item.get("url") or (
            f"https://news.ycombinator.com/item?id={item.get('objectID')}"
        )
        snippet = (item.get("story_text") or "")[:240]
        if title:
            hits.append(Hit(title, url, snippet, "hackernews"))
    return hits


def parse_yahoo(html: str) -> list[Hit]:
    hits: list[Hit] = []
    # Classic Yahoo SERP: h3 > a, plus .compText / .fc-falcon snippets.
    for m in re.finditer(
        r'<h3[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S
    ):
        href, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if not title or "yahoo.com" in href:
            continue
        hits.append(Hit(title, html_lib.unescape(href), "", "yahoo"))
    return _dedupe(hits)[:12]


def parse_google(html: str) -> list[Hit]:
    hits: list[Hit] = []
    # gbv=1 organic links often look like /url?q=https://...
    for m in re.finditer(
        r'<a[^>]+href="(/url\?q=[^"]+|https?://[^"]+)"[^>]*>\s*(?:<h3[^>]*>)?([^<]{4,200})',
        html,
        re.I,
    ):
        href, title = m.group(1), m.group(2).strip()
        if href.startswith("/url?"):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = qs.get("q", [href])[0]
        if any(s in href for s in ("google.com", "accounts.google", "webcache")):
            continue
        if title.lower() in {"cached", "similar", "translate this page"}:
            continue
        hits.append(Hit(title, href, "", "google"))
    return _dedupe(hits)


def _dedupe(hits: Iterable[Hit]) -> list[Hit]:
    seen: set[str] = set()
    out: list[Hit] = []
    for hit in hits:
        key = hit.url.split("#")[0]
        if key in seen:
            continue
        seen.add(key)
        out.append(hit)
    return out


def results_url(engine: str, query: str) -> str:
    q = urllib.parse.quote_plus(query)
    if engine == "duckduckgo":
        return f"https://lite.duckduckgo.com/lite/?q={q}"
    if engine == "google":
        return f"https://www.google.com/search?q={q}&gbv=1&hl=en&num=10"
    if engine == "yahoo":
        return f"https://search.yahoo.com/search?p={q}"
    if engine == "wikipedia":
        return (
            "https://en.wikipedia.org/w/api.php?action=query&list=search"
            f"&srsearch={q}&srlimit=8&format=json&utf8=1"
        )
    if engine == "hackernews":
        return (
            "https://hn.algolia.com/api/v1/search"
            f"?query={q}&tags=story&hitsPerPage=8"
        )
    raise ValueError(engine)


def parse_hits(engine: str, body: str) -> list[Hit]:
    if engine == "duckduckgo":
        return parse_ddg_lite(body)
    if engine == "google":
        return parse_google(body)
    if engine == "yahoo":
        return parse_yahoo(body)
    if engine == "wikipedia":
        return parse_wikipedia_api(body)
    if engine == "hackernews":
        return parse_hn(body)
    raise ValueError(engine)


def search_one(
    engine: str,
    query: str,
    fetch_mode: str,
    chrome_bin: str,
) -> SearchReport:
    url = results_url(engine, query)
    notes: list[str] = []
    if fetch_mode == "chrome":
        status, final, body = chrome_dump_dom(url, chrome_bin)
        mode = "chrome-dump-dom"
    else:
        try:
            status, final, body = http_get(url)
            mode = "http"
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            return SearchReport(
                query=query,
                engine=engine,
                results_url=url,
                final_url=url,
                status=int(getattr(exc, "code", 0) or 0),
                blocked=True,
                block_reason=str(exc),
                hits=[],
                source_chars=0,
                fetch_mode=fetch_mode,
                notes=[str(exc)],
            )

    reason = looks_blocked(body, status)
    hits: list[Hit] = []
    parse_error = None
    try:
        if not reason:
            hits = parse_hits(engine, body)
    except Exception as exc:
        parse_error = str(exc)
        notes.append(f"parse_error: {exc}")

    blocked = bool(reason) or (not hits and engine in {"google", "yahoo"})
    if not hits and not reason:
        reason = parse_error or "zero_hits"
        if engine in {"google", "yahoo"}:
            blocked = True
        notes.append("no_hits_parsed")

    return SearchReport(
        query=query,
        engine=engine,
        results_url=url,
        final_url=final,
        status=status,
        blocked=blocked,
        block_reason=reason if blocked else None,
        hits=hits,
        source_chars=len(body),
        fetch_mode=mode,
        notes=notes,
    )


DEFAULT_CHAIN = ("duckduckgo", "wikipedia", "hackernews", "yahoo", "google")


def run_chain(
    query: str,
    engines: list[str],
    fetch_mode: str,
    chrome_bin: str,
    save_html: str | None,
) -> dict:
    attempts: list[SearchReport] = []
    chosen: SearchReport | None = None
    for engine in engines:
        report = search_one(engine, query, fetch_mode, chrome_bin)
        attempts.append(report)
        if report.hits and not report.blocked:
            chosen = report
            break
    if chosen is None:
        # Accept the first attempt that produced any hits even if flagged.
        chosen = next((a for a in attempts if a.hits), attempts[-1])

    if save_html and chosen:
        # Re-fetch chosen engine source for inspection dumps.
        url = chosen.results_url
        if fetch_mode == "chrome":
            _, _, body = chrome_dump_dom(url, chrome_bin)
        else:
            _, _, body = http_get(url)
        os.makedirs(os.path.dirname(os.path.abspath(save_html)) or ".", exist_ok=True)
        with open(save_html, "w", encoding="utf-8") as fh:
            fh.write(body)

    return {
        "ok": bool(chosen and chosen.hits),
        "query": query,
        "used_engine": chosen.engine if chosen else None,
        "result": asdict(chosen) if chosen else None,
        "attempts": [
            {
                "engine": a.engine,
                "blocked": a.blocked,
                "block_reason": a.block_reason,
                "hit_count": len(a.hits),
                "status": a.status,
                "fetch_mode": a.fetch_mode,
                "notes": a.notes,
            }
            for a in attempts
        ],
    }


def find_chrome() -> str:
    for candidate in (
        os.environ.get("CHROME_BIN"),
        "/usr/local/bin/google-chrome",
        "/usr/bin/google-chrome",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "google-chrome",
        "chromium",
    ):
        if not candidate:
            continue
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        which = subprocess.run(["which", candidate], capture_output=True, text=True)
        if which.returncode == 0:
            return which.stdout.strip()
    raise FileNotFoundError("Chrome/Chromium not found; set CHROME_BIN")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Search the web by URL + page-source parsing. No button clicks, no screenshots."
    )
    parser.add_argument("query", nargs="+", help="Search query")
    parser.add_argument(
        "--engine",
        action="append",
        choices=["duckduckgo", "wikipedia", "hackernews", "yahoo", "google"],
        help="Engine to try (repeatable). Default: duckduckgo → wikipedia → hackernews → yahoo → google",
    )
    parser.add_argument(
        "--chrome",
        action="store_true",
        help="Fetch via local Chrome --dump-dom instead of urllib (use on your machine if Google blocks datacenter IPs)",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON only")
    parser.add_argument("--save-html", metavar="PATH", help="Write chosen engine HTML to PATH")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args(argv)

    query = " ".join(args.query)
    engines = args.engine or list(DEFAULT_CHAIN)
    fetch_mode = "chrome" if args.chrome else "http"
    chrome_bin = find_chrome() if args.chrome else ""

    payload = run_chain(query, engines, fetch_mode, chrome_bin, args.save_html)
    result = payload.get("result") or {}
    hits = (result.get("hits") or [])[: args.limit]
    result["hits"] = hits
    payload["result"] = result

    if args.json:
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0 if payload["ok"] else 2

    print(f"query: {query}")
    print(f"used_engine: {payload.get('used_engine')}")
    print("attempts:")
    for att in payload["attempts"]:
        flag = "blocked" if att["blocked"] else "ok"
        print(
            f"  - {att['engine']}: {flag}"
            f" hits={att['hit_count']} status={att['status']}"
            f" reason={att['block_reason']}"
        )
    if result.get("results_url"):
        print(f"results_url: {result['results_url']}")
        print(f"fetch: {result.get('fetch_mode')} source_chars={result.get('source_chars')}")
    if not hits:
        print("no hits parsed")
        return 2
    print("hits:")
    for i, hit in enumerate(hits, 1):
        print(f"{i}. {hit['title']}")
        print(f"   {hit['url']}")
        if hit.get("snippet"):
            print(f"   {hit['snippet']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

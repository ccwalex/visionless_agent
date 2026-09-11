#!/usr/bin/env python3
"""Fetch and cache EasyList / Adblock Plus filter lists for source adblock."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from adblock_parse import merge_parsed, parse_filter_list  # noqa: E402

LISTS_PATH = os.path.join(SCRIPT_DIR, "adblock_lists.json")
BUILTIN_RULES_PATH = os.path.join(SCRIPT_DIR, "adblock_rules.json")
USER_AGENT = "visionless_agent/1.0 (+https://github.com/ccwalex/visionless_agent)"
CTX = ssl.create_default_context()


def default_cache_dir() -> str:
    return os.environ.get(
        "VISIONLESS_ADBLOCK_CACHE",
        os.path.join(os.path.expanduser("~"), ".cache", "visionless-agent", "adblock"),
    )


def load_manifest(path: str | None = None) -> dict[str, Any]:
    with open(path or LISTS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def load_builtin_rules() -> dict[str, Any]:
    with open(BUILTIN_RULES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _http_get(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/plain,*/*"},
    )
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
        return resp.read().decode("utf-8", "replace")


def _list_cache_path(cache_dir: str, source_id: str) -> str:
    return os.path.join(cache_dir, f"{source_id}.txt")


def _compiled_cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, "compiled.json")


def _is_fresh(path: str, ttl_hours: float) -> bool:
    if not os.path.isfile(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < ttl_hours * 3600


def fetch_source(source: dict[str, Any], cache_dir: str, force: bool = False) -> str:
    sid = source["id"]
    cache_path = _list_cache_path(cache_dir, sid)
    os.makedirs(cache_dir, exist_ok=True)
    ttl = source.get("ttl_hours")
    if not force and _is_fresh(cache_path, ttl or 24):
        with open(cache_path, encoding="utf-8") as fh:
            return fh.read()
    text = _http_get(source["url"])
    with open(cache_path, "w", encoding="utf-8") as fh:
        fh.write(text)
    meta = {
        "id": sid,
        "url": source["url"],
        "fetched_at": int(time.time()),
        "bytes": len(text),
    }
    with open(cache_path + ".meta.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    return text


def compile_rules(
    cache_dir: str | None = None,
    *,
    force_fetch: bool = False,
    include_optional: bool = False,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    cache_dir = cache_dir or default_cache_dir()
    manifest = load_manifest()
    ttl_hours = manifest.get("cache_ttl_hours") or 24
    compiled_path = _compiled_cache_path(cache_dir)

    if not force_fetch and _is_fresh(compiled_path, ttl_hours):
        with open(compiled_path, encoding="utf-8") as fh:
            cached = json.load(fh)
        cached["from_cache"] = True
        return cached

    selected = []
    for src in manifest.get("sources") or []:
        if source_ids and src["id"] not in source_ids:
            continue
        if src.get("optional") and not include_optional:
            continue
        selected.append(src)

    parsed_chunks = []
    fetch_errors: list[dict[str, str]] = []
    for src in selected:
        try:
            text = fetch_source(src, cache_dir, force=force_fetch)
            parsed_chunks.append(parse_filter_list(text, source_id=src["id"]))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            cache_path = _list_cache_path(cache_dir, src["id"])
            if os.path.isfile(cache_path):
                with open(cache_path, encoding="utf-8") as fh:
                    text = fh.read()
                parsed_chunks.append(parse_filter_list(text, source_id=src["id"]))
                fetch_errors.append({"id": src["id"], "error": str(exc), "used_stale_cache": "true"})
            else:
                fetch_errors.append({"id": src["id"], "error": str(exc), "used_stale_cache": "false"})

    merged = merge_parsed(*parsed_chunks)
    builtin = load_builtin_rules()

    # Merge built-in readable rules as bootstrap / offline fallback
    merged["hosts"] = sorted(set(merged.get("hosts") or []) | set(builtin.get("hosts") or []))
    merged["cosmetic_classes"] = sorted(
        set(merged.get("cosmetic_classes") or []) | set(builtin.get("id_class_patterns") or [])
    )
    merged["tag_patterns"] = list(builtin.get("tag_patterns") or []) + list(merged.get("tag_patterns") or [])
    merged["id_class_patterns"] = merged["cosmetic_classes"]  # backward compat for JS extractor
    merged["compiled_at"] = int(time.time())
    merged["cache_dir"] = cache_dir
    merged["list_sources"] = [
        {"id": s["id"], "name": s["name"], "url": s["url"]} for s in selected
    ]
    merged["fetch_errors"] = fetch_errors
    merged["ghostery_note"] = manifest.get("ghostery_note")
    merged["from_cache"] = False

    os.makedirs(cache_dir, exist_ok=True)
    with open(compiled_path, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2)

    return merged


def ensure_compiled_rules(**kwargs: Any) -> dict[str, Any]:
    return compile_rules(**kwargs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch EasyList / Adblock Plus lists and compile HTML-strip rules."
    )
    parser.add_argument("--cache-dir", default=default_cache_dir())
    parser.add_argument("--force", action="store_true", help="Bypass TTL and re-fetch lists")
    parser.add_argument("--include-optional", action="store_true")
    parser.add_argument("--source", action="append", dest="sources", help="Only fetch these list ids")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    rules = compile_rules(
        args.cache_dir,
        force_fetch=args.force,
        include_optional=args.include_optional,
        source_ids=args.sources,
    )
    summary = {
        "compiled_at": rules.get("compiled_at"),
        "sources": rules.get("list_sources"),
        "hosts": len(rules.get("hosts") or []),
        "cosmetic_classes": len(rules.get("cosmetic_classes") or []),
        "cosmetic_ids": len(rules.get("cosmetic_ids") or []),
        "parsed_lines": rules.get("parsed_lines"),
        "fetch_errors": rules.get("fetch_errors"),
        "cache_dir": args.cache_dir,
    }
    if args.json:
        json.dump(summary, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(f"compiled: {summary['compiled_at']} cache={args.cache_dir}")
        print(f"sources: {[s['id'] for s in summary['sources'] or []]}")
        print(
            f"hosts={summary['hosts']} cosmetic_classes={summary['cosmetic_classes']} "
            f"cosmetic_ids={summary['cosmetic_ids']} parsed_lines={summary['parsed_lines']}"
        )
        if summary["fetch_errors"]:
            print("fetch_errors:", summary["fetch_errors"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

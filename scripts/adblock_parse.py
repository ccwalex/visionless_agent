"""Parse EasyList / Adblock Plus filter syntax into HTML-strip rules."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Cosmetic selectors we can match without a full CSS engine.
_SIMPLE_CLASS = re.compile(r"^\.([a-zA-Z0-9_-]+)$")
_SIMPLE_ID = re.compile(r"^#([a-zA-Z0-9_-]+)$")
_SIMPLE_TAG_CLASS = re.compile(r"^([a-zA-Z0-9]+)\.([a-zA-Z0-9_-]+)$")
_NETWORK = re.compile(r"^\|\|([^$|^/]+)")
_EXCEPTION = re.compile(r"^@@\|\|([^$|^/]+)")


def _domain_from_network(line: str) -> str | None:
    m = _EXCEPTION.match(line) or _NETWORK.match(line)
    if not m:
        return None
    domain = m.group(1).strip().lower()
    if domain.startswith("http:") or domain.startswith("https:"):
        parsed = urlparse(domain)
        domain = parsed.netloc or domain
    domain = domain.split("/")[0]
    if not domain or "/" in domain or domain.startswith("."):
        return None
    return domain


def _is_supported_cosmetic(selector: str) -> bool:
    if not selector or selector.startswith("+js") or ":has(" in selector:
        return False
    if any(tok in selector for tok in (":", "[", ">", "+", "~", "*")):
        return False
    return bool(_SIMPLE_CLASS.match(selector) or _SIMPLE_ID.match(selector) or _SIMPLE_TAG_CLASS.match(selector))


def parse_filter_list(text: str, source_id: str = "") -> dict[str, Any]:
    hosts: set[str] = set()
    exception_hosts: set[str] = set()
    cosmetic_classes: set[str] = set()
    cosmetic_ids: set[str] = set()
    tag_patterns: list[dict[str, str]] = []
    domain_cosmetic_classes: dict[str, set[str]] = {}
    domain_cosmetic_ids: dict[str, set[str]] = {}
    skipped = 0
    parsed_lines = 0

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("!") or line.startswith("["):
            continue
        if line.startswith("@@"):
            domain = _domain_from_network(line)
            if domain:
                exception_hosts.add(domain)
            continue
        if "/adblock" in line or line.startswith("/") and line.endswith("/"):
            skipped += 1
            continue

        if "##" in line:
            domain_part, selector = line.split("##", 1)
            domain_part = domain_part.strip().lower()
            if domain_part.startswith("~"):
                skipped += 1
                continue
            if not _is_supported_cosmetic(selector):
                skipped += 1
                continue
            parsed_lines += 1
            m_id = _SIMPLE_ID.match(selector)
            m_cls = _SIMPLE_CLASS.match(selector)
            m_tc = _SIMPLE_TAG_CLASS.match(selector)
            if m_id:
                ident = m_id.group(1)
                if domain_part:
                    domain_cosmetic_ids.setdefault(domain_part, set()).add(ident)
                else:
                    cosmetic_ids.add(ident)
            elif m_cls:
                cls = m_cls.group(1)
                if domain_part:
                    domain_cosmetic_classes.setdefault(domain_part, set()).add(cls)
                else:
                    cosmetic_classes.add(cls)
            elif m_tc:
                tag, cls = m_tc.group(1), m_tc.group(2)
                tag_patterns.append(
                    {"tag": tag.lower(), "class_contains": cls, "reason": source_id or "easylist"}
                )
            continue

        if line.startswith("||") or line.startswith("|http"):
            if "$" in line and any(
                mod in line
                for mod in (
                    "$redirect",
                    "$csp",
                    "$removeparam",
                    "$popup",
                    "$document",
                    "+js",
                )
            ):
                skipped += 1
                continue
            domain = _domain_from_network(line)
            if domain:
                hosts.add(domain)
                parsed_lines += 1
            continue

        skipped += 1

    return {
        "source_id": source_id,
        "hosts": sorted(hosts),
        "exception_hosts": sorted(exception_hosts),
        "cosmetic_classes": sorted(cosmetic_classes),
        "cosmetic_ids": sorted(cosmetic_ids),
        "domain_cosmetic_classes": {k: sorted(v) for k, v in domain_cosmetic_classes.items()},
        "domain_cosmetic_ids": {k: sorted(v) for k, v in domain_cosmetic_ids.items()},
        "tag_patterns": tag_patterns,
        "parsed_lines": parsed_lines,
        "skipped_lines": skipped,
    }


def merge_parsed(*chunks: dict[str, Any]) -> dict[str, Any]:
    hosts: set[str] = set()
    exception_hosts: set[str] = set()
    cosmetic_classes: set[str] = set()
    cosmetic_ids: set[str] = set()
    tag_patterns: list[dict[str, str]] = []
    domain_cosmetic_classes: dict[str, set[str]] = {}
    domain_cosmetic_ids: dict[str, set[str]] = {}
    sources: list[str] = []
    parsed_lines = 0

    for chunk in chunks:
        if not chunk:
            continue
        sid = chunk.get("source_id") or "unknown"
        sources.append(sid)
        hosts.update(chunk.get("hosts") or [])
        exception_hosts.update(chunk.get("exception_hosts") or [])
        cosmetic_classes.update(chunk.get("cosmetic_classes") or [])
        cosmetic_ids.update(chunk.get("cosmetic_ids") or [])
        tag_patterns.extend(chunk.get("tag_patterns") or [])
        for dom, ids in (chunk.get("domain_cosmetic_classes") or {}).items():
            domain_cosmetic_classes.setdefault(dom, set()).update(ids)
        for dom, ids in (chunk.get("domain_cosmetic_ids") or {}).items():
            domain_cosmetic_ids.setdefault(dom, set()).update(ids)
        parsed_lines += chunk.get("parsed_lines") or 0

    # Exceptions win over block rules
    hosts -= exception_hosts

    return {
        "sources": sources,
        "hosts": sorted(hosts),
        "exception_hosts": sorted(exception_hosts),
        "cosmetic_classes": sorted(cosmetic_classes),
        "cosmetic_ids": sorted(cosmetic_ids),
        "domain_cosmetic_classes": {k: sorted(v) for k, v in domain_cosmetic_classes.items()},
        "domain_cosmetic_ids": {k: sorted(v) for k, v in domain_cosmetic_ids.items()},
        "tag_patterns": tag_patterns,
        "parsed_lines": parsed_lines,
    }

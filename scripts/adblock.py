"""Strip ad/tracker markup from HTML source for LLM-readable inventory parsing.

This is not a browser extension and does not block network requests. It removes
noise from saved/live HTML before affordance and contents extraction.
"""

from __future__ import annotations

import json
import os
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

SKIP_STRIP_TAGS = {"script", "link", "meta", "noscript", "style", "template"}
RULES_PATH = os.path.join(os.path.dirname(__file__), "adblock_rules.json")


def load_rules(path: str | None = None) -> dict[str, Any]:
    """Load compiled remote-list rules, falling back to built-in JSON."""
    if path:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    try:
        from adblock_fetch import ensure_compiled_rules

        return ensure_compiled_rules()
    except Exception:
        with open(RULES_PATH, encoding="utf-8") as fh:
            return json.load(fh)


def _attr(attrs: list[tuple[str, str | None]], name: str) -> str:
    want = name.lower()
    for key, value in attrs:
        if key.lower() == want:
            return value or ""
    return ""


def _page_host(page_url: str) -> str:
    if not page_url:
        return ""
    parsed = urlparse(page_url if "://" in page_url else f"https://{page_url}")
    return (parsed.netloc or "").lower()


def _host_suffix_match(url: str, hosts: set[str]) -> str | None:
    if not url or not hosts:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or "").lower()
    if not host:
        return None
    parts = host.split(".")
    for i in range(len(parts) - 1):
        suffix = ".".join(parts[i:])
        if suffix in hosts:
            return suffix
    if host in hosts:
        return host
    return None


def _id_class_match(ident: str, patterns: list[str]) -> str | None:
    blob = ident.lower()
    for pat in patterns:
        if pat.lower() in blob:
            return pat
    return None


def _cosmetic_sets(rules: dict[str, Any], page_url: str) -> tuple[set[str], set[str]]:
    classes = set(rules.get("cosmetic_classes") or rules.get("id_class_patterns") or [])
    ids = set(rules.get("cosmetic_ids") or [])
    host = _page_host(page_url)
    if host:
        for dom, cls_list in (rules.get("domain_cosmetic_classes") or {}).items():
            if host == dom or host.endswith("." + dom):
                classes.update(cls_list)
        for dom, id_list in (rules.get("domain_cosmetic_ids") or {}).items():
            if host == dom or host.endswith("." + dom):
                ids.update(id_list)
    return classes, ids


class AdStripParser(HTMLParser):
    def __init__(self, rules: dict[str, Any], page_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.rules = rules
        self.page_url = page_url
        self.cosmetic_classes, self.cosmetic_ids = _cosmetic_sets(rules, page_url)
        self.blocked_hosts = set(rules.get("hosts") or [])
        self.exception_hosts = set(rules.get("exception_hosts") or [])
        self.removed: list[dict[str, str]] = []
        self._skip_depth = 0
        self._skip_tag: str | None = None
        self.parts: list[str] = []

    def _should_remove(self, tag: str, attrs: list[tuple[str, str | None]]) -> tuple[bool, str, str]:
        tag = tag.lower()
        if tag in SKIP_STRIP_TAGS:
            return False, "", ""

        ident_id = _attr(attrs, "id")
        ident_class = _attr(attrs, "class")
        src = _attr(attrs, "src") or _attr(attrs, "href")

        if ident_id and ident_id in self.cosmetic_ids:
            return True, "easylist_cosmetic_id", f"{tag}#{ident_id}"

        for cls_token in ident_class.split():
            if cls_token in self.cosmetic_classes:
                return True, "easylist_cosmetic_class", f"{tag}.{cls_token}"

        ident = " ".join(filter(None, [ident_id, ident_class, src]))
        hit = _id_class_match(ident, self.rules.get("id_class_patterns") or [])
        if hit:
            sel = ident_id or ident_class or tag
            return True, "id_class_pattern", f"{tag}.{hit} ({sel})"

        exception_hosts = self.exception_hosts
        host = _host_suffix_match(src, self.blocked_hosts)
        if host and host not in exception_hosts:
            return True, "easylist_host", f"{tag}[src~={host}]"

        for pat in self.rules.get("tag_patterns") or []:
            want_tag = (pat.get("tag") or "").lower()
            if want_tag and want_tag != tag:
                continue
            cls = ident_class.lower()
            ident_id_l = ident_id.lower()
            src_l = src.lower()
            if pat.get("class_contains") and pat["class_contains"].lower() in cls:
                return True, pat.get("reason") or "tag_pattern", f"{tag}.{pat['class_contains']}"
            if pat.get("src_contains") and pat["src_contains"].lower() in src_l:
                return True, pat.get("reason") or "tag_pattern", f"{tag}[src*={pat['src_contains']}]"
            prefix = pat.get("id_prefix") or pat.get("class_prefix") or ""
            if prefix:
                if pat.get("id_prefix") and ident_id_l.startswith(prefix.lower()):
                    return True, pat.get("reason") or "tag_pattern", f"{tag}#{ident_id_l}"
                if pat.get("class_prefix") and any(
                    c.startswith(prefix.lower()) for c in cls.split()
                ):
                    return True, pat.get("reason") or "tag_pattern", f"{tag}.{cls}"
        return False, "", ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            self._skip_depth += 1
            return
        remove, reason, selector = self._should_remove(tag, attrs)
        if remove:
            self._skip_depth = 1
            self._skip_tag = tag.lower()
            self.removed.append({"reason": reason, "selector_or_host": selector})
            return
        attr_str = "".join(
            f' {k}="{v}"' if v is not None else f" {k}"
            for k, v in attrs
        )
        self.parts.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        if self._skip_depth:
            self._skip_depth -= 1
            if self._skip_depth == 0:
                self._skip_tag = None
            return
        self.parts.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth:
            return
        remove, reason, selector = self._should_remove(tag, attrs)
        if remove:
            self.removed.append({"reason": reason, "selector_or_host": selector})
            return
        attr_str = "".join(
            f' {k}="{v}"' if v is not None else f" {k}"
            for k, v in attrs
        )
        self.parts.append(f"<{tag}{attr_str} />")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)

    def handle_entityref(self, name: str) -> None:
        if not self._skip_depth:
            self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if not self._skip_depth:
            self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(f"<!--{data}-->")

    def get_html(self) -> str:
        return "".join(self.parts)


def strip_ads(
    html: str,
    rules: dict[str, Any] | None = None,
    *,
    page_url: str = "",
) -> dict[str, Any]:
    rules = rules or load_rules()
    parser = AdStripParser(rules, page_url=page_url)
    parser.feed(html)
    parser.close()
    out = {
        "html": parser.get_html(),
        "removed": parser.removed,
    }
    if rules.get("list_sources"):
        out["list_sources"] = rules.get("list_sources")
    return out

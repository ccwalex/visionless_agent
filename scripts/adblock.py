"""Deterministic ad stripping from HTML source before inventory parsing."""

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
    with open(path or RULES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _attr(attrs: list[tuple[str, str | None]], name: str) -> str:
    want = name.lower()
    for key, value in attrs:
        if key.lower() == want:
            return value or ""
    return ""


def _host_match(url: str, hosts: list[str]) -> str | None:
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or parsed.path or "").lower()
    for needle in hosts:
        if needle in host or host.endswith(needle):
            return needle
    return None


def _id_class_match(ident: str, patterns: list[str]) -> str | None:
    blob = ident.lower()
    for pat in patterns:
        if pat.lower() in blob:
            return pat
    return None


class AdStripParser(HTMLParser):
    def __init__(self, rules: dict[str, Any]) -> None:
        super().__init__(convert_charrefs=True)
        self.rules = rules
        self.removed: list[dict[str, str]] = []
        self._skip_depth = 0
        self._skip_tag: str | None = None
        self.parts: list[str] = []

    def _should_remove(self, tag: str, attrs: list[tuple[str, str | None]]) -> tuple[bool, str, str]:
        tag = tag.lower()
        if tag in SKIP_STRIP_TAGS:
            return False, "", ""
        ident = " ".join(
            filter(
                None,
                [
                    _attr(attrs, "id"),
                    _attr(attrs, "class"),
                    _attr(attrs, "src"),
                    _attr(attrs, "href"),
                ],
            )
        )
        hit = _id_class_match(ident, self.rules.get("id_class_patterns") or [])
        if hit:
            sel = _attr(attrs, "id") or _attr(attrs, "class") or tag
            return True, "id_class_pattern", f"{tag}.{hit} ({sel})"

        src = _attr(attrs, "src") or _attr(attrs, "href")
        host = _host_match(src, self.rules.get("hosts") or [])
        if host:
            return True, "host", f"{tag}[src~={host}]"

        for pat in self.rules.get("tag_patterns") or []:
            want_tag = (pat.get("tag") or "").lower()
            if want_tag and want_tag != tag:
                continue
            cls = _attr(attrs, "class").lower()
            ident_id = _attr(attrs, "id").lower()
            src_l = src.lower()
            if pat.get("class_contains") and pat["class_contains"].lower() in cls:
                return True, pat.get("reason") or "tag_pattern", f"{tag}.{pat['class_contains']}"
            if pat.get("src_contains") and pat["src_contains"].lower() in src_l:
                return True, pat.get("reason") or "tag_pattern", f"{tag}[src*={pat['src_contains']}]"
            prefix = pat.get("id_prefix") or pat.get("class_prefix") or ""
            if prefix:
                if pat.get("id_prefix") and ident_id.startswith(prefix.lower()):
                    return True, pat.get("reason") or "tag_pattern", f"{tag}#{ident_id}"
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


def strip_ads(html: str, rules: dict[str, Any] | None = None) -> dict[str, Any]:
    rules = rules or load_rules()
    parser = AdStripParser(rules)
    parser.feed(html)
    parser.close()
    return {
        "html": parser.get_html(),
        "removed": parser.removed,
    }

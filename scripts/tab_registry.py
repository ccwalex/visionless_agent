"""Stable integer tab ids and opener lineage for Chrome CDP sessions."""

from __future__ import annotations

import json
import os
import re
from typing import Any

TAB_HEADER_RE = re.compile(
    r"<header\b[^>]*\bdata-inspect-tab\s*=\s*[\"'](\d+)[\"']"
    r"[^>]*\bdata-opened-from\s*=\s*[\"'](\d*)[\"']"
    r"(?:[^>]*\bdata-url\s*=\s*[\"']([^\"']*)[\"'])?",
    re.I | re.S,
)


def registry_path(port: int) -> str:
    return f"/tmp/visionless-tabs-{port}.json"


def load_registry(port: int) -> dict[str, Any]:
    path = registry_path(port)
    if not os.path.isfile(path):
        return {"next_index": 0, "tabs": {}}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("next_index", 0)
    data.setdefault("tabs", {})
    return data


def save_registry(port: int, data: dict[str, Any]) -> None:
    path = registry_path(port)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)


def merge_targets(
    registry: dict[str, Any],
    targets: list[dict[str, Any]],
    opener_by_cdp_id: dict[str, str | None] | None = None,
    forced_opener_by_cdp_id: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Assign stable ids to CDP page targets. Mutates registry in place."""
    opener_by_cdp_id = opener_by_cdp_id or {}
    forced_opener_by_cdp_id = forced_opener_by_cdp_id or {}
    tabs: dict[str, dict[str, Any]] = registry.setdefault("tabs", {})
    next_index = registry.setdefault("next_index", 0)

    # Drop closed tabs
    live_ids = {t["id"] for t in targets}
    for cdp_id in list(tabs.keys()):
        if cdp_id not in live_ids:
            del tabs[cdp_id]

    for target in targets:
        cdp_id = target["id"]
        if cdp_id not in tabs:
            opener_cdp = forced_opener_by_cdp_id.get(cdp_id) or opener_by_cdp_id.get(cdp_id)
            opener_index = None
            if opener_cdp and opener_cdp in tabs:
                opener_index = tabs[opener_cdp]["index"]
            tabs[cdp_id] = {
                "index": next_index,
                "opener_index": opener_index,
            }
            next_index += 1
        tabs[cdp_id]["url"] = target.get("url") or ""
        tabs[cdp_id]["title"] = target.get("title") or ""

    registry["next_index"] = next_index

    out: list[dict[str, Any]] = []
    for target in targets:
        cdp_id = target["id"]
        rec = tabs[cdp_id]
        out.append(
            {
                "id": rec["index"],
                "opened_from": rec.get("opener_index"),
                "url": rec.get("url") or target.get("url") or "",
                "title": rec.get("title") or target.get("title") or "",
                "cdp_id": cdp_id,
            }
        )
    out.sort(key=lambda t: t["id"])
    return out


def find_tab(tabs: list[dict[str, Any]], tab_id: int) -> dict[str, Any] | None:
    for tab in tabs:
        if tab["id"] == tab_id:
            return tab
    return None


def parse_tab_header(html: str) -> tuple[dict[str, Any] | None, str]:
    """Return (tab_meta, html_without_header)."""
    m = TAB_HEADER_RE.search(html[:2000])
    if not m:
        return None, html
    tab_id = int(m.group(1))
    opened_raw = m.group(2)
    opened_from = int(opened_raw) if opened_raw else None
    url = m.group(3) or ""
    meta = {"id": tab_id, "opened_from": opened_from, "url": url}
    cleaned = html[: m.start()] + html[m.end() :]
    return meta, cleaned.lstrip()


def render_tab_header(tab: dict[str, Any]) -> str:
    tab_id = tab.get("id", 0)
    opened = tab.get("opened_from")
    url = tab.get("url") or ""
    opened_attr = "" if opened is None else str(opened)
    if opened is None:
        text = f"tab {tab_id} origin"
    else:
        text = f"tab {tab_id} originated from tab {opened}"
    return (
        f'<header data-inspect-tab="{tab_id}" data-opened-from="{opened_attr}" '
        f'data-url="{url}">\n{text}\n</header>\n'
    )


def prepend_tab_header(html: str, tab: dict[str, Any]) -> str:
    _, body = parse_tab_header(html)
    return render_tab_header(tab) + body

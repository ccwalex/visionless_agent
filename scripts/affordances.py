"""Deterministic HTML → interaction inventory (no vision, no clicking)."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

LOGIN_RE = re.compile(
    r"\b(log\s*in|sign\s*in|sign\s*on|sign\s*up|create account|"
    r"continue with (google|apple|github|microsoft)|authenticate|sso)\b",
    re.I,
)
SEARCH_NAME_RE = re.compile(r"^(q|query|search|s)$", re.I)
CAPTCHA_RE = re.compile(r"recaptcha|h-captcha|cf-turnstile|g-recaptcha|captcha", re.I)
SKIP_TAGS = {"script", "style", "noscript", "template"}
VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


def _attr(attrs: list[tuple[str, str | None]], name: str) -> str:
    want = name.lower()
    for key, value in attrs:
        if key.lower() == want:
            return value or ""
    return ""


def _selector(tag: str, attrs: list[tuple[str, str | None]]) -> str:
    ident = _attr(attrs, "id")
    if ident:
        return f"#{ident}"
    testid = _attr(attrs, "data-testid") or _attr(attrs, "data-test")
    if testid:
        return f'[data-testid="{testid}"]'
    name = _attr(attrs, "name")
    if name:
        return f'{tag}[name="{name}"]'
    aria = _attr(attrs, "aria-label")
    if aria:
        return f'{tag}[aria-label="{aria}"]'
    typ = _attr(attrs, "type")
    if typ:
        return f'{tag}[type="{typ}"]'
    return tag


def _bool_attr(attrs: list[tuple[str, str | None]], name: str) -> bool:
    raw = _attr(attrs, name)
    if name in {k.lower() for k, _ in attrs} and raw == "":
        return True
    return raw.lower() in {"true", "disabled", "required", name}


def compact(text: str, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def _is_hidden_attrs(attrs: list[tuple[str, str | None]]) -> bool:
    if _bool_attr(attrs, "hidden"):
        return True
    if _attr(attrs, "aria-hidden").lower() == "true":
        return True
    style = re.sub(r"\s+", "", _attr(attrs, "style").lower())
    return "display:none" in style or "visibility:hidden" in style


class AffordanceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.in_title = False
        self.skip = 0
        self.forms: list[dict[str, Any]] = []
        self.form_stack: list[dict[str, Any]] = []
        self.buttons: list[dict[str, Any]] = []
        self.loose_fields: list[dict[str, Any]] = []
        self.links: list[dict[str, str]] = []
        self.captcha = False
        self._open: list[tuple[str, dict[str, Any] | None, list[str]]] = []
        self._label_for_stack: list[tuple[str, list[str]]] = []
        self.labels_by_id: dict[str, str] = {}
        self._text_targets: list[list[str]] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        blob = " ".join(f"{k}={v}" for k, v in attrs if v)
        if CAPTCHA_RE.search(tag + blob):
            self.captcha = True
        if tag in SKIP_TAGS:
            self.skip += 1
            self._open.append((tag, None, []))
            return
        if self.skip:
            self._open.append((tag, None, []))
            return

        hidden_here = _is_hidden_attrs(attrs)
        if hidden_here:
            self.hidden_depth += 1
        if self.hidden_depth:
            self._open.append((tag, {"_hidden_mark": hidden_here} if hidden_here else None, []))
            return

        if tag == "title":
            self.in_title = True

        if tag == "label":
            parts: list[str] = []
            self._label_for_stack.append((_attr(attrs, "for"), parts))
            self._text_targets.append(parts)
            self._open.append((tag, None, parts))
            return

        if tag == "form":
            rec = {
                "index": len(self.forms),
                "selector": _selector(tag, attrs),
                "id": _attr(attrs, "id"),
                "name": _attr(attrs, "name"),
                "action": _attr(attrs, "action"),
                "method": (_attr(attrs, "method") or "get").lower(),
                "fields": [],
                "submits": [],
            }
            self.forms.append(rec)
            self.form_stack.append(rec)
            self._open.append((tag, rec, []))
            return

        if tag in {"input", "textarea", "select", "button"}:
            rec = self._control(tag, attrs)
            text_buf: list[str] = rec.setdefault("_text", [])
            if rec.get("_role") == "submit" or tag == "button" or rec.get("type") in {
                "submit",
                "button",
                "reset",
                "image",
            }:
                target = self.form_stack[-1]["submits"] if self.form_stack else self.buttons
                button = {
                    "kind": "submit" if rec.get("type") in {"submit", "image", ""} and tag in {"button", "input"} else rec.get("kind", "button"),
                    "text": rec.get("label") or rec.get("placeholder") or "",
                    "selector": rec["selector"],
                    "id": rec.get("id") or "",
                    "name": rec.get("name") or "",
                    "type": rec.get("type") or tag,
                    "href": "",
                    "disabled": rec.get("disabled", False),
                    "_text": text_buf,
                }
                if tag == "button" and rec.get("type") in {"", "submit"}:
                    button["kind"] = "submit"
                target.append(button)
                if tag == "input" and rec.get("type") in {"submit", "button", "reset", "image"}:
                    self._open.append((tag, None, []))
                    if tag in VOID:
                        pass
                    return
                if tag == "button":
                    self._text_targets.append(text_buf)
                    self._open.append((tag, button, text_buf))
                    return
            if rec.get("type") not in {"submit", "button", "reset", "image"}:
                if self.form_stack:
                    self.form_stack[-1]["fields"].append(rec)
                else:
                    self.loose_fields.append(rec)
            self._open.append((tag, rec, text_buf))
            if tag == "textarea":
                self._text_targets.append(text_buf)
            return

        if tag == "a":
            href = _attr(attrs, "href")
            rec = {
                "text": _attr(attrs, "aria-label"),
                "href": href,
                "selector": _selector(tag, attrs),
                "_text": [],
            }
            role = _attr(attrs, "role").lower()
            self._text_targets.append(rec["_text"])
            self._open.append((tag, rec, rec["_text"]))
            if role == "button":
                self.buttons.append(
                    {
                        "kind": "link-button",
                        "text": rec["text"],
                        "selector": rec["selector"],
                        "id": _attr(attrs, "id"),
                        "name": "",
                        "type": "a",
                        "href": href,
                        "disabled": _attr(attrs, "aria-disabled").lower() == "true",
                        "_text": rec["_text"],
                    }
                )
            return

        if tag == "summary" or _attr(attrs, "role").lower() == "button":
            buf: list[str] = []
            rec = {
                "kind": "disclosure" if tag == "summary" else "button",
                "text": _attr(attrs, "aria-label"),
                "selector": _selector(tag, attrs),
                "id": _attr(attrs, "id"),
                "name": _attr(attrs, "name"),
                "type": tag,
                "href": "",
                "disabled": _bool_attr(attrs, "disabled"),
                "_text": buf,
            }
            self.buttons.append(rec)
            self._text_targets.append(buf)
            self._open.append((tag, rec, buf))
            return

        self._open.append((tag, None, []))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        # pop matching
        while self._open:
            opened, rec, buf = self._open.pop()
            if opened in SKIP_TAGS:
                if opened == tag:
                    if self.skip:
                        self.skip -= 1
                    break
                continue
            if opened != tag:
                continue
            if rec is not None and rec.get("_hidden_mark"):
                if self.hidden_depth:
                    self.hidden_depth -= 1
            if tag == "form" and self.form_stack:
                self.form_stack.pop()
            if tag == "label" and self._label_for_stack:
                for_id, parts = self._label_for_stack.pop()
                text = compact("".join(parts))
                if for_id:
                    self.labels_by_id[for_id] = text
                if self._text_targets and self._text_targets[-1] is parts:
                    self._text_targets.pop()
            if rec is not None and "_text" in rec:
                text = compact("".join(rec["_text"]))
                if text:
                    if "text" in rec:
                        rec["text"] = rec.get("text") or text
                    if rec.get("label") == "" or rec.get("label") is None:
                        if "label" in rec:
                            rec["label"] = text
                if self._text_targets and rec.get("_text") is self._text_targets[-1]:
                    self._text_targets.pop()
            if tag == "a" and rec is not None and rec.get("href"):
                rec["text"] = compact(rec.get("text") or "".join(rec.get("_text") or []))
                if rec["text"] and rec["href"] not in {"", "#"} and not rec["href"].lower().startswith("javascript:"):
                    if len(self.links) < 40:
                        self.links.append(
                            {"text": rec["text"], "href": rec["href"], "selector": rec["selector"]}
                        )
            break

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.skip:
            return
        if self._text_targets:
            self._text_targets[-1].append(data)

    def _control(self, tag: str, attrs: list[tuple[str, str | None]]) -> dict[str, Any]:
        typ = (_attr(attrs, "type") or ("textarea" if tag == "textarea" else "select" if tag == "select" else "text")).lower()
        ident = _attr(attrs, "id")
        return {
            "tag": tag,
            "type": typ,
            "name": _attr(attrs, "name"),
            "id": ident,
            "selector": _selector(tag, attrs),
            "label": _attr(attrs, "aria-label") or self.labels_by_id.get(ident, ""),
            "placeholder": _attr(attrs, "placeholder"),
            "autocomplete": _attr(attrs, "autocomplete"),
            "required": _bool_attr(attrs, "required") or _attr(attrs, "aria-required").lower() == "true",
            "disabled": _bool_attr(attrs, "disabled"),
            "value_present": bool(_attr(attrs, "value")) and typ != "password",
            "kind": "button" if typ in {"button", "reset"} else "submit" if typ in {"submit", "image"} else "field",
        }

    def finalize(self) -> None:
        for group in (self.buttons, *[f["submits"] for f in self.forms]):
            for rec in group:
                extra = compact("".join(rec.get("_text") or []))
                rec["text"] = rec.get("text") or extra
        for field in [*[f for form in self.forms for f in form["fields"]], *self.loose_fields]:
            extra = compact("".join(field.get("_text") or []))
            if extra and not field.get("label"):
                field["label"] = extra


def _strip_private(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_private(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_private(x) for x in obj]
    return obj


def inventory_from_html(html: str, url: str = "") -> dict[str, Any]:
    parser = AffordanceParser()
    parser.feed(html)
    parser.close()
    parser.finalize()
    title = compact("".join(parser.title_parts))
    forms = _strip_private(parser.forms)
    buttons = _strip_private(parser.buttons)
    loose = _strip_private(parser.loose_fields)
    # Attach labels that appeared after inputs (for=)
    for field in [*[f for form in forms for f in form["fields"]], *loose]:
        ident = field.get("id") or ""
        if ident and parser.labels_by_id.get(ident) and not field.get("label"):
            field["label"] = parser.labels_by_id[ident]

    password_fields = [f for form in forms for f in form["fields"] if f.get("type") == "password"]
    password_fields += [f for f in loose if f.get("type") == "password"]
    login_buttons = [
        b
        for b in [*[s for form in forms for s in form["submits"]], *buttons]
        if LOGIN_RE.search(b.get("text") or "")
    ]
    captcha = parser.captcha or bool(CAPTCHA_RE.search(html))
    auth_likely = bool(
        password_fields
        or login_buttons
        or LOGIN_RE.search(title)
        or re.search(r"/(login|signin|sign-in|signup|auth|session)\b", url, re.I)
    )

    search_forms = []
    for form in forms:
        if any(
            f.get("type") == "search"
            or SEARCH_NAME_RE.match(f.get("name") or "")
            or LOGIN_RE.search(f.get("label") or "") is None
            and (
                re.search(r"search|find|query", f.get("label") or "", re.I)
                or re.search(r"search|find|query", f.get("placeholder") or "", re.I)
            )
            for f in form["fields"]
        ):
            search_forms.append(form)
        elif any(SEARCH_NAME_RE.match(f.get("name") or "") or f.get("type") == "search" for f in form["fields"]):
            search_forms.append(form)

    # Tighten search form detection
    search_forms = [
        form
        for form in forms
        if any(
            f.get("type") == "search"
            or SEARCH_NAME_RE.match(f.get("name") or "")
            or re.search(r"search|find|query", (f.get("label") or "") + (f.get("placeholder") or ""), re.I)
            for f in form["fields"]
        )
    ]

    pathways: list[dict[str, Any]] = []
    if auth_likely:
        pathways.append(
            {
                "id": "handoff-login",
                "kind": "handoff",
                "reason": "password_field" if password_fields else "login_cta",
                "summary": (
                    "This page looks like it needs an account. Do not guess credentials. "
                    "Hand the headed Chrome window to the user, then re-run inspect on the same CDP session."
                ),
            }
        )
    if captcha:
        pathways.append(
            {
                "id": "handoff-captcha",
                "kind": "handoff",
                "reason": "captcha",
                "summary": "Captcha markup is present. Hand the browser to the user, then re-inspect.",
            }
        )
    for i, form in enumerate(search_forms):
        field = next(
            (
                f
                for f in form["fields"]
                if f.get("type") == "search" or SEARCH_NAME_RE.match(f.get("name") or "")
            ),
            form["fields"][0] if form["fields"] else None,
        )
        submit = form["submits"][0] if form["submits"] else None
        pathways.append(
            {
                "id": f"search-form-{i}",
                "kind": "fill_and_submit",
                "form_selector": form["selector"],
                "fill": (
                    {"selector": field["selector"], "name": field.get("name"), "label": field.get("label")}
                    if field
                    else None
                ),
                "submit": (
                    {"selector": submit["selector"], "text": submit.get("text")}
                    if submit
                    else {"how": "form.submit() or Enter"}
                ),
                "get_shortcut": (
                    f"GET {form.get('action') or url} ?{field.get('name') or 'q'}="
                    if form.get("method") == "get" and field
                    else None
                ),
                "summary": (
                    f"Fill {field['selector'] if field else 'the query field'} then submit"
                    f"{' via ' + submit['selector'] if submit else ''}."
                    " Prefer encoding the query in the URL when method=GET."
                ),
            }
        )
    for i, form in enumerate(forms):
        if form in search_forms:
            continue
        visible_fields = [f for f in form["fields"] if f.get("type") != "hidden"]
        if not visible_fields:
            continue
        submit = form["submits"][0] if form["submits"] else None
        pathways.append(
            {
                "id": f"form-{i}",
                "kind": "fill_and_submit",
                "form_selector": form["selector"],
                "method": form.get("method"),
                "action": form.get("action"),
                "fields": [
                    {
                        "selector": f["selector"],
                        "name": f.get("name"),
                        "type": f.get("type"),
                        "label": f.get("label"),
                        "required": f.get("required"),
                    }
                    for f in visible_fields
                ],
                "submit": (
                    {"selector": submit["selector"], "text": submit.get("text")}
                    if submit
                    else {"how": "form.submit() or Enter"}
                ),
                "summary": (
                    f"Form {form.get('selector') or i} "
                    f"({(form.get('method') or 'get').upper()} {form.get('action') or '.'}) "
                    f"— fill listed fields, activate {submit['selector'] if submit else 'submit'}."
                ),
            }
        )
    for i, button in enumerate(buttons[:15]):
        if LOGIN_RE.search(button.get("text") or ""):
            continue
        pathways.append(
            {
                "id": f"button-{i}",
                "kind": "activate",
                "selector": button.get("selector"),
                "text": button.get("text"),
                "summary": f"Activate {button.get('selector')} (“{button.get('text') or button.get('kind')}”).",
            }
        )

    return finalize_inventory(
        {
            "source": "html-dump",
            "url": url,
            "title": title,
            "auth": {
                "likely": auth_likely,
                "captcha": captcha,
                "password_fields": len(password_fields),
                "login_buttons": [b.get("text") for b in login_buttons],
                "signals": [
                    s
                    for s in [
                        "password_input" if password_fields else None,
                        "login_cta" if login_buttons else None,
                        "title" if LOGIN_RE.search(title) else None,
                        "captcha" if captcha else None,
                    ]
                    if s
                ],
            },
            "forms": forms,
            "buttons": buttons,
            "loose_fields": loose,
            "links": parser.links,
            "pathways": pathways,
        }
    )


FILTER_SECTIONS = frozenset(
    {
        "pathways",
        "forms",
        "buttons",
        "links",
        "loose_fields",
        "auth",
        "handoff",
    }
)
FILTER_PRESETS = {
    "next": ("auth", "handoff", "pathways"),
    "search": ("auth", "handoff", "pathways", "forms"),
    "interactive": ("auth", "handoff", "pathways", "forms", "buttons", "loose_fields"),
    "links-only": ("links",),
    "index": ("links",),
}
FILTER_ALIASES = {
    "fields": "loose_fields",
    "actions": "pathways",
}
_CORE_KEYS = ("source", "url", "title", "fetch", "saved_html")

ACTIVATE_PATHWAY_CAP = 5
LINK_TEXT_LIMIT = 80

BUTTON_CHROME_RE = re.compile(
    r"cookie|consent|accept all|reject all|agree (and|&)|manage (cookies|preferences)|"
    r"do not sell|privacy settings|subscribe|newsletter|skip to( main)? content|"
    r"\bclose\b|\bdismiss\b|\bmenu\b|hamburger|notifications?|\bshare\b|"
    r"get \w+ plus|\badvertis",
    re.I,
)
LINK_CHROME_RE = re.compile(
    r"cookie|consent|skip to( main)? content|facebook\.com|twitter\.com|"
    r"(^|/)x\.com|instagram\.|pinterest\.|tiktok\.|whatsapp",
    re.I,
)


def is_chrome_button(rec: dict[str, Any]) -> bool:
    blob = " ".join(
        str(rec.get(k) or "") for k in ("text", "id", "selector", "name", "href")
    )
    return bool(BUTTON_CHROME_RE.search(blob))


def is_chrome_link(rec: dict[str, Any]) -> bool:
    blob = " ".join(str(rec.get(k) or "") for k in ("text", "href", "selector"))
    return bool(LINK_CHROME_RE.search(blob))


def _pathway_rank(pathway: dict[str, Any]) -> int:
    kind = pathway.get("kind")
    ident = str(pathway.get("id") or "")
    if kind == "handoff":
        return 0
    if ident.startswith("search-form") or pathway.get("get_shortcut"):
        return 1
    if kind == "fill_and_submit":
        return 2
    return 3


def assign_refs(inv: dict[str, Any]) -> dict[str, Any]:
    """Lynx-style ordinals. Prefer compact numbers on the visible action surface."""
    n = 1
    by_sel: dict[str, int] = {}

    def take(selector: str | None = None) -> int:
        nonlocal n
        if selector and selector in by_sel:
            return by_sel[selector]
        ref = n
        n += 1
        if selector:
            by_sel[selector] = ref
        return ref

    for pathway in inv.get("pathways") or []:
        fill = pathway.get("fill") if isinstance(pathway.get("fill"), dict) else {}
        sel = (
            pathway.get("selector")
            or (fill or {}).get("selector")
            or pathway.get("form_selector")
        )
        pathway["ref"] = take(sel)
    for form in inv.get("forms") or []:
        for field in form.get("fields") or []:
            if field.get("type") == "hidden":
                continue
            field["ref"] = take(field.get("selector"))
        for sub in form.get("submits") or []:
            sub["ref"] = take(sub.get("selector"))
    for button in inv.get("buttons") or []:
        button["ref"] = take(button.get("selector"))
    for field in inv.get("loose_fields") or []:
        field["ref"] = take(field.get("selector"))
    for link in inv.get("links") or []:
        link["ref"] = take(link.get("selector"))
    inv["ref_count"] = n - 1
    return inv


def finalize_inventory(inv: dict[str, Any]) -> dict[str, Any]:
    """Rank pathways, drop chrome/nav activate noise, assign refs. Safe to call twice."""
    for button in inv.get("buttons") or []:
        button["chrome"] = is_chrome_button(button)
    kept_links: list[dict[str, Any]] = []
    for link in inv.get("links") or []:
        link["text"] = compact(link.get("text") or "", LINK_TEXT_LIMIT)
        link["chrome"] = is_chrome_link(link)
        if link["chrome"]:
            continue
        kept_links.append(link)
    inv["links"] = kept_links

    kept = [p for p in inv.get("pathways") or [] if p.get("kind") != "activate"]
    activate: list[dict[str, Any]] = []
    for i, button in enumerate(inv.get("buttons") or []):
        if button.get("chrome") or button.get("disabled"):
            continue
        if LOGIN_RE.search(button.get("text") or ""):
            continue
        activate.append(
            {
                "id": f"button-{i}",
                "kind": "activate",
                "selector": button.get("selector"),
                "text": button.get("text"),
                "summary": (
                    f"Activate {button.get('selector')} "
                    f"(“{button.get('text') or button.get('kind')}”)."
                ),
            }
        )
        if len(activate) >= ACTIVATE_PATHWAY_CAP:
            break
    inv["pathways"] = sorted(kept, key=_pathway_rank) + activate
    return assign_refs(inv)


def parse_filter_spec(raw: str | None) -> tuple[frozenset[str], bool]:
    """Return (sections to keep, slim_to_search). Empty spec means keep everything."""
    if not raw or not str(raw).strip():
        return FILTER_SECTIONS, False
    tokens = [part.strip().lower() for part in str(raw).split(",") if part.strip()]
    if not tokens:
        return FILTER_SECTIONS, False
    sections: set[str] = set()
    slim_search = False
    unknown: list[str] = []
    for token in tokens:
        if token in FILTER_PRESETS:
            sections.update(FILTER_PRESETS[token])
            if token == "search":
                slim_search = True
            continue
        token = FILTER_ALIASES.get(token, token)
        if token in FILTER_SECTIONS:
            sections.add(token)
        else:
            unknown.append(token)
    if unknown:
        allowed = ", ".join(sorted(FILTER_SECTIONS | set(FILTER_PRESETS) | set(FILTER_ALIASES)))
        raise ValueError(f"Unknown --filter token(s): {', '.join(unknown)}. Use: {allowed}")
    return frozenset(sections), slim_search


def _search_form_selectors(inv: dict[str, Any]) -> set[str]:
    selectors: set[str] = set()
    for pathway in inv.get("pathways") or []:
        if pathway.get("id", "").startswith("search-form") or pathway.get("get_shortcut"):
            sel = pathway.get("form_selector")
            if sel:
                selectors.add(sel)
    return selectors


def project_inventory(
    inv: dict[str, Any],
    filter_spec: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Shrink a full inventory for agent stdout. Counts always reflect the unfiltered page."""
    sections, slim_search = parse_filter_spec(filter_spec)
    filtering = bool(filter_spec and str(filter_spec).strip())
    capping = limit is not None and limit >= 0
    if not filtering and not capping:
        return inv

    counts = {
        "forms": len(inv.get("forms") or []),
        "buttons": len(inv.get("buttons") or []),
        "links": len(inv.get("links") or []),
        "loose_fields": len(inv.get("loose_fields") or []),
        "pathways": len(inv.get("pathways") or []),
    }
    out: dict[str, Any] = {key: inv[key] for key in _CORE_KEYS if key in inv}
    if filtering:
        out["filter"] = [part.strip() for part in str(filter_spec).split(",") if part.strip()]
    if capping:
        out["limit"] = limit
    out["counts"] = counts

    pathways = list(inv.get("pathways") or [])
    forms = list(inv.get("forms") or [])
    if slim_search:
        search_sels = _search_form_selectors(inv)
        pathways = [
            p
            for p in pathways
            if p.get("kind") == "handoff"
            or str(p.get("id") or "").startswith("search-form")
            or p.get("get_shortcut")
        ]
        if search_sels:
            forms = [f for f in forms if f.get("selector") in search_sels]
        else:
            forms = []

    if capping:
        keep_pathways: list[dict[str, Any]] = []
        activate_kept = 0
        for pathway in pathways:
            if pathway.get("kind") == "activate":
                if activate_kept >= limit:
                    continue
                activate_kept += 1
            keep_pathways.append(pathway)
        pathways = keep_pathways

    def cap(items: list[Any]) -> list[Any]:
        if not capping:
            return items
        return items[:limit]

    visible_buttons = [b for b in (inv.get("buttons") or []) if not b.get("chrome")]
    visible_links = list(inv.get("links") or [])

    if "auth" in sections:
        out["auth"] = inv.get("auth")
    if "handoff" in sections and "handoff" in inv:
        out["handoff"] = inv["handoff"]
    if "pathways" in sections:
        out["pathways"] = pathways
    if "forms" in sections:
        out["forms"] = forms
    if "buttons" in sections:
        out["buttons"] = cap(visible_buttons)
    if "links" in sections:
        out["links"] = cap(visible_links)
    if "loose_fields" in sections:
        out["loose_fields"] = cap(list(inv.get("loose_fields") or []))
    return assign_refs(out)


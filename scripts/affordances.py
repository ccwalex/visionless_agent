"""Deterministic HTML → interaction inventory (no vision, no clicking)."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

from html_text import visible_text

from tab_registry import parse_tab_header

LOGIN_RE = re.compile(
    r"\b(log\s*in|sign\s*in|sign\s*on|sign\s*up|create account|"
    r"continue with (google|apple|github|microsoft)|authenticate|sso)\b",
    re.I,
)
SEARCH_NAME_RE = re.compile(r"^(q|query|search|s)$", re.I)
CAPTCHA_RE = re.compile(r"recaptcha|h-captcha|cf-turnstile|g-recaptcha|captcha", re.I)
SKIP_TAGS = {"script", "style", "noscript", "template"}
LANDMARK_TAGS = {"main", "nav", "article", "aside", "header", "footer"}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
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


def compact(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()[:240]


def _role_for(tag: str, typ: str, attrs: list[tuple[str, str | None]]) -> str:
    role = _attr(attrs, "role").lower()
    if role:
        return role
    if tag == "a":
        return "link"
    if tag == "select":
        return "combobox"
    if tag == "textarea":
        return "textbox"
    if tag == "summary":
        return "disclosure"
    if tag == "button":
        return "submit" if typ in {"", "submit"} else "button"
    if tag == "input":
        if typ in {"checkbox"}:
            return "checkbox"
        if typ in {"radio"}:
            return "radio"
        if typ in {"range"}:
            return "slider"
        if typ in {"submit", "image"}:
            return "submit"
        if typ in {"button", "reset"}:
            return "button"
        return "textbox"
    return "button"


class AffordanceParser(HTMLParser):
    def __init__(self, max_links: int | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.max_links = max_links
        self.title_parts: list[str] = []
        self.in_title = False
        self.skip = 0
        self.forms: list[dict[str, Any]] = []
        self.form_stack: list[dict[str, Any]] = []
        self.buttons: list[dict[str, Any]] = []
        self.loose_fields: list[dict[str, Any]] = []
        self.links: list[dict[str, str]] = []
        self.controls: list[dict[str, Any]] = []
        self.headings: list[dict[str, Any]] = []
        self.landmarks: list[dict[str, Any]] = []
        self.media: list[dict[str, Any]] = []
        self.captcha = False
        self._open: list[tuple[str, dict[str, Any] | None, list[str]]] = []
        self._label_for_stack: list[tuple[str, list[str]]] = []
        self.labels_by_id: dict[str, str] = {}
        self._text_targets: list[list[str]] = []
        self._landmark_buf: list[str] | None = None
        self._landmark_tag: str | None = None
        self._landmark_sel: str | None = None

    def _is_synthetic_header(self, tag: str, attrs: list[tuple[str, str | None]]) -> bool:
        return tag == "header" and _attr(attrs, "data-inspect-tab") != ""

    def _build_controls(self) -> None:
        self.controls = []
        seen_links: set[str] = set()

        for form in self.forms:
            fi = form["index"]
            for field in form["fields"]:
                if field.get("type") == "hidden":
                    continue
                text = field.get("label") or field.get("placeholder") or ""
                self.controls.append(
                    {
                        "id": len(self.controls),
                        "role": _role_for(field.get("tag") or "input", field.get("type") or "text", []),
                        "tag": field.get("tag") or "input",
                        "selector": field["selector"],
                        "name": field.get("name") or "",
                        "text": text,
                        "label": field.get("label") or "",
                        "href": "",
                        "disabled": field.get("disabled", False),
                        "form": fi,
                    }
                )
            for submit in form["submits"]:
                self.controls.append(
                    {
                        "id": len(self.controls),
                        "role": "submit",
                        "tag": submit.get("type") or "button",
                        "selector": submit["selector"],
                        "name": submit.get("name") or "",
                        "text": submit.get("text") or "",
                        "label": submit.get("text") or "",
                        "href": "",
                        "disabled": submit.get("disabled", False),
                        "form": fi,
                    }
                )

        for field in self.loose_fields:
            text = field.get("label") or field.get("placeholder") or ""
            self.controls.append(
                {
                    "id": len(self.controls),
                    "role": _role_for(field.get("tag") or "input", field.get("type") or "text", []),
                    "tag": field.get("tag") or "input",
                    "selector": field["selector"],
                    "name": field.get("name") or "",
                    "text": text,
                    "label": field.get("label") or "",
                    "href": "",
                    "disabled": field.get("disabled", False),
                    "form": None,
                }
            )

        for button in self.buttons:
            self.controls.append(
                {
                    "id": len(self.controls),
                    "role": "button" if button.get("kind") != "link-button" else "button",
                    "tag": button.get("type") or "button",
                    "selector": button["selector"],
                    "name": button.get("name") or "",
                    "text": button.get("text") or "",
                    "label": button.get("text") or "",
                    "href": button.get("href") or "",
                    "disabled": button.get("disabled", False),
                    "form": None,
                }
            )

        for link in self.links:
            key = link.get("selector", "") + link.get("href", "")
            if key in seen_links:
                continue
            seen_links.add(key)
            self.controls.append(
                {
                    "id": len(self.controls),
                    "role": "link",
                    "tag": "a",
                    "selector": link["selector"],
                    "name": "",
                    "text": link.get("text") or "",
                    "label": link.get("text") or "",
                    "href": link.get("href") or "",
                    "disabled": False,
                    "form": None,
                }
            )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._is_synthetic_header(tag, attrs):
            self.skip += 1
            self._open.append((tag, None, []))
            return

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

        if tag == "title":
            self.in_title = True

        if tag in HEADING_TAGS:
            level = int(tag[1])
            buf: list[str] = []
            sel = _selector(tag, attrs)
            self.headings.append({"level": level, "text": "", "selector": sel, "_text": buf})
            self._text_targets.append(buf)
            self._open.append((tag, self.headings[-1], buf))
            return

        if tag in LANDMARK_TAGS and not _attr(attrs, "data-inspect-tab"):
            buf = []
            sel = _selector(tag, attrs)
            self._landmark_buf = buf
            self._landmark_tag = tag
            self._landmark_sel = sel
            self._text_targets.append(buf)
            self._open.append((tag, None, buf))
            return

        if tag in {"img", "video"}:
            self.media.append(
                {
                    "tag": tag,
                    "selector": _selector(tag, attrs),
                    "alt": _attr(attrs, "alt"),
                    "src": _attr(attrs, "src"),
                }
            )

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
            form_index = self.form_stack[-1]["index"] if self.form_stack else None
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
        while self._open:
            opened, rec, buf = self._open.pop()
            if opened in SKIP_TAGS or (opened == "header" and self.skip):
                if opened == tag:
                    if self.skip:
                        self.skip -= 1
                    break
                continue
            if opened != tag:
                continue
            if tag == "form" and self.form_stack:
                self.form_stack.pop()
            if tag == "label" and self._label_for_stack:
                for_id, parts = self._label_for_stack.pop()
                text = compact("".join(parts))
                if for_id:
                    self.labels_by_id[for_id] = text
                if self._text_targets and self._text_targets[-1] is parts:
                    self._text_targets.pop()
            if tag in HEADING_TAGS and rec is not None:
                text = compact("".join(buf))
                rec["text"] = text
                if self._text_targets and buf is self._text_targets[-1]:
                    self._text_targets.pop()
            if tag in LANDMARK_TAGS and self._landmark_buf is buf:
                text = compact("".join(buf))
                if text and not _attr([], "data-inspect-tab"):
                    self.landmarks.append(
                        {
                            "tag": self._landmark_tag or tag,
                            "selector": self._landmark_sel or tag,
                            "text": text,
                        }
                    )
                self._landmark_buf = None
                self._landmark_tag = None
                self._landmark_sel = None
                if self._text_targets and buf is self._text_targets[-1]:
                    self._text_targets.pop()
            if rec is not None and isinstance(rec, dict) and "_text" in rec:
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
                    if self.max_links is None or len(self.links) < self.max_links:
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
        for heading in self.headings:
            if "_text" in heading:
                heading["text"] = heading.get("text") or compact("".join(heading["_text"]))
        self._build_controls()


def _strip_private(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_private(v) for k, v in obj.items() if not k.startswith("_")}
    if isinstance(obj, list):
        return [_strip_private(x) for x in obj]
    return obj


def _build_pathways(
    forms: list[dict[str, Any]],
    buttons: list[dict[str, Any]],
    auth_likely: bool,
    captcha: bool,
    password_fields: list,
    url: str,
) -> list[dict[str, Any]]:
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
    for i, button in enumerate(buttons):
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
    return pathways


def inventory_from_html(
    html: str,
    url: str = "",
    *,
    tab: dict[str, Any] | None = None,
    adblock: dict[str, Any] | None = None,
    max_links: int | None = None,
    max_text: int = 20000,
    source: str = "html-dump",
) -> dict[str, Any]:
    if tab is None:
        parsed_tab, html = parse_tab_header(html)
        if parsed_tab:
            tab = parsed_tab
            if not url:
                url = parsed_tab.get("url") or url
    parser = AffordanceParser(max_links=max_links)
    parser.feed(html)
    parser.close()
    parser.finalize()
    title = compact("".join(parser.title_parts))
    forms = _strip_private(parser.forms)
    buttons = _strip_private(parser.buttons)
    loose = _strip_private(parser.loose_fields)
    controls = _strip_private(parser.controls)
    headings = _strip_private(parser.headings)
    landmarks = _strip_private(parser.landmarks)
    media = _strip_private(parser.media)

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

    page_text = visible_text(html)
    text_chars = len(page_text)
    pathways = _build_pathways(forms, buttons, auth_likely, captcha, password_fields, url)

    out: dict[str, Any] = {
        "source": source,
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
        "controls": controls,
        "forms": forms,
        "buttons": buttons,
        "loose_fields": loose,
        "links": parser.links,
        "contents": {
            "headings": headings,
            "landmarks": landmarks,
            "media": media,
            "text": page_text[:max_text],
            "text_chars": text_chars,
        },
        "pathways": pathways,
    }
    if tab is not None:
        out["tab"] = tab
    if adblock is not None:
        out["adblock"] = adblock
    return out

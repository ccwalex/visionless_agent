---
name: chrome-affordance
description: Inspect a live Chrome page or saved HTML for buttons, forms, fields, and next-step pathways without screenshots or mouse clicks. Use when an agent needs to know what to click or fill, when following up a Chrome session via CDP, or when a site requires the user to log in before the agent continues.
---

# Chrome affordance inspect

Complement a Grok-style browser agent: **Chrome loads the page, this script reads the DOM**, and the agent gets a deterministic list of selectors and pathways. It is not a text-only browser and it does not click.

```bash
python3 scripts/inspect.py --html tests/fixtures/search_form.html
python3 scripts/inspect.py --url https://example.com
python3 scripts/inspect.py --cdp 9222 --json
python3 scripts/inspect.py --cdp 9222 --list-tabs --json
python3 scripts/inspect.py --cdp 9222 --tab 1 --json
python3 scripts/inspect.py --url https://example.com --dump-dom
```

## Workflow

1. Open (or attach to) Chrome. Prefer a **deep link**, not “find the Search button.”
2. Run `inspect.py` on that document.
3. Read `controls`, `contents`, `pathways`, `forms`, `buttons`, `links`. Each control has a CSS `selector`.
4. Choose **one** pathway. Prefer `GET` query URLs when `method=get`. Otherwise fill via CDP/`document.querySelector` + `Enter`/`form.submit()` — still no screenshot loop.
5. Re-inspect after navigation. Selectors are not stable across document reloads.

## Multi-tab

List tabs with stable integer ids and opener lineage:

```bash
python3 scripts/inspect.py --cdp 9222 --list-tabs --json
python3 scripts/inspect.py --cdp 9222 --tab 0 --json
python3 scripts/inspect.py --cdp 9222 --tab 0 --new-tab https://example.com --json
```

Each inventory includes `tab: { id, opened_from, url, title, cdp_id }`. Tab 1 opened from tab 0 shows `"opened_from": 0`. Saved HTML dumps prepend `<header data-inspect-tab="…" data-opened-from="…">` so the source is self-describing.

## Follow an existing Chrome session (login-capable)

User or agent starts Chrome with a debugging port (real profile = real cookies):

```bash
# macOS example
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222
```

Then, after the human has browsed to the interesting page:

```bash
python3 scripts/inspect.py --cdp 9222 --json
python3 scripts/inspect.py --cdp 9222 --target github.com --json
python3 scripts/inspect.py --cdp 9222 --tab 1 --json
```

`--cdp` **does not navigate** unless you pass `--navigate URL` or `--force-navigate` with `--url`. Default is “tell me what this tab can do.”

## Login / captcha handoff

If the DOM has a password field, a Sign in CTA, or captcha markup:

- `auth.likely` is true
- `pathways` includes `kind: handoff`
- CLI exits **3**
- `handoff.instruction` tells the user to finish in the headed window
- `handoff.resume` is the command to re-inspect the **same** CDP session

Do **not** type credentials. Do **not** screenshot the login form on a loop.

Wait while they sign in:

```bash
python3 scripts/inspect.py --cdp 9222 --wait-login 180 --json
```

That polls until `auth.likely` is false or the timeout hits.

## What the inventory means

| Field | Use |
|---|---|
| `controls[]` | Full map of interactive elements (`id`, `role`, `selector`, `text`, `form`) |
| `contents.text` | Visible page text after ad strip |
| `contents.headings` | `h1`–`h6` |
| `contents.landmarks` | `main`, `nav`, `article`, etc. |
| `contents.media` | Images and video |
| `adblock` | Nodes stripped from source (`scripts/adblock_rules.json`) |
| `forms[].fields[].selector` | Where to type |
| `forms[].submits[].selector` | Submit control for that form |
| `pathways[].kind = fill_and_submit` | Ordered fill plan + optional `get_shortcut` |
| `pathways[].kind = activate` | A button that is not part of a form |
| `pathways[].kind = handoff` | Stop; give the browser to the human |
| `buttons` / `links` | Views over `controls` |

Default CDP path dumps HTML, strips ads, and parses with `scripts/affordances.py` (deterministic). Use `--live-dom` for computed visibility via `scripts/extract_affordances.js`. Use `--no-adblock` to skip stripping.

Fill a mapped field without a mouse:

```bash
python3 scripts/inspect.py --cdp 9222 \
  --fill '#ybar-sbq' --value AAPL --submit-form '#ybar-sf' --json
```

That sets the input with the native value setter, fires `input`/`change`, then `form.requestSubmit()` — not a screenshot click. Re-read the inventory after navigation; selectors can change.

## Anti-patterns

- Screenshot, click at (x, y), screenshot again
- Opening a homepage only to hunt for a Search button when `?q=` exists
- Filling a password field from the agent
- Reusing selectors after a full navigation without re-inspect
- Reading only `pathways` when you need the full `controls` + `contents` map

---
name: chrome-affordance
description: Inspect a live Chrome page or saved HTML for buttons, forms, fields, and next-step pathways without screenshots or mouse clicks. Use when an agent needs to know what to click or fill, when following up a Chrome session via CDP, or when a site requires the user to log in before the agent continues.
---

# Chrome affordance inspect

Complement a Grok-style browser agent: **Chrome loads the page, this script reads the DOM**, and the agent gets a deterministic list of selectors and pathways. It is not a text-only browser and it does not click.

```bash
python3 scripts/inspect.py --html tests/fixtures/search_form.html
python3 scripts/inspect.py --url https://example.com
python3 scripts/inspect.py --cdp 9222 --json --filter next
python3 scripts/inspect.py --cdp 9222 --json --links-only
python3 scripts/inspect.py --url https://example.com --dump-dom
```

## Workflow

1. Open (or attach to) Chrome. Prefer a **deep link**, not “find the Search button.”
2. Run `inspect.py` on that document. Default dump is a full inventory. On dense pages use **`--filter next`** (auth + ranked pathways + `counts` + Lynx-style `ref` numbers). Widen only if needed: `--filter search`, `--filter interactive`, `--links-only`, `--filter buttons,links --limit 8`.
3. Act by `ref` or CSS `selector`. `ref` is the compact ordinal for this inspect (like Lynx `[3]`); selectors survive better if you fill via CDP. Unfiltered `buttons` / `links` are expensive on chrome-heavy sites.
4. Choose **one** pathway. Prefer `GET` query URLs when `method=get`. Otherwise fill via CDP/`document.querySelector` + `Enter`/`form.submit()` — still no screenshot loop.
5. Re-inspect after navigation. Refs and selectors are not stable across document reloads. Prefer `--filter next` again rather than a full dump. Use `--links-only` when you need the link index (Lynx `L`), not the whole page.

## Follow an existing Chrome session (login-capable)

User or agent starts Chrome with a debugging port (real profile = real cookies):

```bash
# macOS example
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222
```

Then, after the human has browsed to the interesting page:

```bash
python3 scripts/inspect.py --cdp 9222 --json --filter next
python3 scripts/inspect.py --cdp 9222 --target github.com --json --filter next
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
| `pathways` | Ranked next actions (`ref` 1…n) — start here (`--filter next`) |
| `ref` | Lynx-style ordinal for this inspect; use with selector |
| `counts` | Unfiltered sizes so you can widen the filter |
| `forms[].fields[].selector` | Where to type |
| `forms[].submits[].selector` | Submit control for that form |
| `pathways[].kind = fill_and_submit` | Ordered fill plan + optional `get_shortcut` |
| `pathways[].kind = activate` | A non-chrome button (cookie/nav/share omitted; cap 5) |
| `pathways[].kind = handoff` | Stop; give the browser to the human |
| `buttons` / `links` | Leftover chrome; `--links-only` for the compact index |

Live Chrome (`--cdp`) uses in-page `innerText`, labels, disabled, and visibility (`scripts/extract_affordances.js`). `--html` / `--url` uses a deterministic HTML parser (`scripts/affordances.py`) on dump-dom or saved source.

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

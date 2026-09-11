# text-browse

Deterministic **Chrome DOM inspect** for agents that should not screenshot or aim a mouse.

Chrome (or saved HTML) is the source of truth. The script prints buttons, form fields, selectors, and ranked pathways — including an explicit **handoff** when the page needs a human login.

This is not a text-only browser.

## Inspect

```bash
# Saved source (fully deterministic; used in tests)
python3 scripts/inspect.py --html tests/fixtures/search_form.html
python3 scripts/inspect.py --html tests/fixtures/login.html --json
# exit code 3 = hand off login to the user

# Load a URL over HTTP and parse source (no Chrome needed)
python3 scripts/inspect.py --url https://example.com --json

# Follow a tab you already have open (cookies, login, SPA state) — preferred
python3 scripts/inspect.py --cdp 9222 --json
python3 scripts/inspect.py --cdp 9222 --wait-login 180

# Optional: headless Chrome dump-dom when HTTP source is a JS shell
python3 scripts/inspect.py --url https://example.com --dump-dom --json
```

Start headed Chrome so a person can sign in:

```bash
google-chrome --remote-debugging-port=9222
# macOS:
# "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222
```

## Agent skill

`.cursor/skills/chrome-affordance/SKILL.md` — attach to Chrome, read the inventory, prefer GET shortcuts, never loop on screenshots, hand login back to the user.

## Layout

| Path | Role |
|---|---|
| `scripts/inspect.py` | CLI |
| `scripts/affordances.py` | HTML → inventory |
| `scripts/extract_affordances.js` | Live DOM extractor (evaluated in Chrome) |
| `scripts/inspect_cdp.mjs` | CDP attach / `Runtime.evaluate` |
| `tests/fixtures/` | Login + search forms |
| `tests/test_affordances.py` | Parser + CLI exit codes |

Optional leftover from an earlier experiment: `scripts/text_search.py` constructs search URLs when you already know the engine. Prefer inspect + `get_shortcut` on the live form.

## Tests

```bash
python3 -m unittest tests.test_affordances tests.test_parsers -v
```

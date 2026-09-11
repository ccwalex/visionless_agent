# visionless_agent

Deterministic **Chrome DOM inspect** for agents that should not screenshot or aim a mouse.

Chrome (or saved HTML) is the source of truth. The script prints buttons, form fields, selectors, and ranked pathways — including an explicit **handoff** when the page needs a human login.

This is not a text-only browser.

## Clone / create repo

From your machine (cloud agents cannot create repos in your namespace):

```bash
# Install the Origin CLI
curl -fsSL https://downloads.cursor.com/origin/install.sh | sh
origin auth login

# Create the repo (Origin — Cursor's git hosting)
origin repo create visionless_agent

# Clone empty repo, or add remote to an existing checkout
origin repo clone chiu-wang-chau/visionless_agent
cd visionless_agent
```

Or on **GitHub** (Option B — run on your Mac):

```bash
# One-time GitHub CLI login
gh auth login

# From this repo root — creates github.com/<you>/visionless_agent and pushes main
bash scripts/push_to_github.sh
```

Manual steps if you prefer:

```bash
gh auth login
gh repo create visionless_agent --public \
  --description "Chrome affordance inspect for visionless agents"

cd /path/to/this/project
git remote add github git@github.com:YOUR_GITHUB_USER/visionless_agent.git
git push -u github main
```

Browse URL after push: `https://github.com/YOUR_GITHUB_USER/visionless_agent`

If `origin` is not found after install:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Origin CLI docs: https://cursor.com/docs/origin/cli

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
python3 scripts/inspect.py --cdp 9222 --list-tabs --json
python3 scripts/inspect.py --cdp 9222 --tab 1 --json
python3 scripts/inspect.py --cdp 9222 --tab 0 --new-tab https://example.com --json
python3 scripts/inspect.py --cdp 9222 --wait-login 180

# Fill a field from the inventory, submit the form (no mouse)
python3 scripts/inspect.py --cdp 9222 --fill '#ybar-sbq' --value AAPL --submit-form '#ybar-sf' --json

# Optional: headless Chrome dump-dom when HTTP source is a JS shell
python3 scripts/inspect.py --url https://example.com --dump-dom --json

# Adblock is on by default (readable rules in scripts/adblock_rules.json)
python3 scripts/inspect.py --html tests/fixtures/ads_and_content.html --json
python3 scripts/inspect.py --url https://example.com --no-adblock --json
```

Start headed Chrome so a person can sign in:

```bash
google-chrome --remote-debugging-port=9222
# macOS:
# "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --remote-debugging-port=9222
```

## Worked example: Yahoo Finance → AAPL

Against a Chrome session (`--remote-debugging-port`):

1. Inspect `https://finance.yahoo.com/` — inventory shows GET form `#ybar-sf` /lookup, field `#ybar-sbq` (`name=p`), submit `#ybar-search`.
2. Fill via CDP, not a click: `--fill '#ybar-sbq' --value AAPL --submit-form '#ybar-sf'`.
3. Yahoo navigates to `https://finance.yahoo.com/quote/AAPL/`. Re-inspect; quote tabs (`Summary`, `Chart`, `Financials`, …) are links. Sign-in is present but optional for the quote (`auth.likely` stayed false).

## Multi-tab inspect

Each CDP session gets stable integer tab ids (`0`, `1`, …) persisted under `/tmp/visionless-tabs-<port>.json`. When tab 1 is opened from tab 0, the inventory includes:

```json
"tab": { "id": 1, "opened_from": 0, "url": "...", "title": "...", "cdp_id": "..." }
```

Saved HTML dumps prepend a self-describing header:

```html
<header data-inspect-tab="1" data-opened-from="0" data-url="https://example.com/b">
tab 1 originated from tab 0
</header>
```

List tabs, switch, or open a child:

```bash
python3 scripts/inspect.py --cdp 9222 --list-tabs
python3 scripts/inspect.py --cdp 9222 --tab 1 --json
python3 scripts/inspect.py --cdp 9222 --tab 0 --new-tab https://example.com --json
```

## Full inventory

Inspect JSON now includes a complete map (not samples):

| Field | Use |
|---|---|
| `controls[]` | Every interactive element with `id`, `role`, `selector`, `text`, `form` |
| `contents.headings` | `h1`–`h6` text |
| `contents.landmarks` | `main`, `nav`, `article`, etc. |
| `contents.media` | `img` / `video` alt and src |
| `contents.text` | Visible page text (default 20k chars) |
| `adblock` | What was stripped from source before parsing |
| `pathways` | Ranked suggestions — not the full inventory |

Adblock rules live in `scripts/adblock_rules.json` (readable, deterministic — not EasyList). Use `--no-adblock` to skip stripping.

## Agent skill

`.cursor/skills/chrome-affordance/SKILL.md` — attach to Chrome, read the inventory, prefer GET shortcuts, never loop on screenshots, hand login back to the user.

## Layout

| Path | Role |
|---|---|
| `scripts/inspect.py` | CLI |
| `scripts/affordances.py` | HTML → inventory |
| `scripts/adblock.py` | Source ad stripping before parse |
| `scripts/adblock_rules.json` | Readable ad/tracker rules |
| `scripts/tab_registry.py` | Stable tab ids + HTML header |
| `scripts/html_text.py` | Visible text extraction |
| `scripts/extract_affordances.js` | Live DOM extractor (evaluated in Chrome) |
| `scripts/inspect_cdp.mjs` | CDP attach / multi-tab / evaluate |
| `tests/fixtures/` | Login, search, ads, tab header |
| `tests/test_affordances.py` | Parser + CLI exit codes |
| `tests/test_tab_adblock.py` | Tab registry, adblock, full inventory |

Optional leftover from an earlier experiment: `scripts/text_search.py` constructs search URLs when you already know the engine. Prefer inspect + `get_shortcut` on the live form.

## Tests

```bash
python3 -m unittest tests.test_affordances tests.test_parsers tests.test_tab_adblock -v
```

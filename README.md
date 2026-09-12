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
python3 scripts/inspect.py --cdp 9222 --json --filter next
python3 scripts/inspect.py --cdp 9222 --wait-login 180

# Dense pages: print only next actions, or search box, or cap chrome
python3 scripts/inspect.py --html tests/fixtures/search_form.html --filter next
python3 scripts/inspect.py --cdp 9222 --json --filter search
python3 scripts/inspect.py --cdp 9222 --json --links-only
python3 scripts/inspect.py --cdp 9222 --json --filter next --links-only
python3 scripts/inspect.py --cdp 9222 --json --filter buttons,links --limit 8

# Fill a field from the inventory, submit the form (no mouse)
python3 scripts/inspect.py --cdp 9222 --fill '#ybar-sbq' --value AAPL --submit-form '#ybar-sf' --json

# Optional: headless Chrome dump-dom when HTTP source is a JS shell
python3 scripts/inspect.py --url https://example.com --dump-dom --json
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

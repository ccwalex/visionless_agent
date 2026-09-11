---
name: no-vision-browse
description: Run browser and web-search tasks without vision or screenshots. Prefer constructing result URLs over clicking buttons, then inspect page source, visible text, and links. Use for Google/Yahoo/DuckDuckGo search, scraping SERPs, reading pages as HTML, and datacenter-block fallbacks (local Chrome, self-hosted worker, Wikipedia, Hacker News).
---

# No-vision browse

Agents in this workflow **must not** use screenshots, pixel coordinates, or vision models. Page source, accessibility text, and HTTP responses are enough.

Prefer the scripts in this repo:

```bash
python3 scripts/text_search.py "your query"
python3 scripts/text_browse.py https://example.com --json
```

## Hard rules

1. **Do not click Search.** Build the results URL and `GET` it.
2. **Do not screenshot.** Dump HTML or visible text (`scripts/text_browse.py`, `browse get html`, `browse get text`, `browse snapshot` if a real browser session is required).
3. **Do not aim at buttons with a mouse.** If a form must be submitted, `GET`/`POST` the action URL with query parameters instead.
4. After every navigation, **re-read source**. Treat consent walls, captchas, and JS shells as blocks and switch engines — do not retry the same blocked URL.

## Search without a homepage

| Engine | Results URL (no UI) | Notes from this environment |
|---|---|---|
| DuckDuckGo Lite | `https://lite.duckduckgo.com/lite/?q={query}` | Works over plain HTTP from datacenter IPs. **Default.** |
| Wikipedia | MediaWiki `action=query&list=search` | JSON API; no SERP chrome. |
| Hacker News | `https://hn.algolia.com/api/v1/search?query=` | JSON API. |
| Yahoo | `https://search.yahoo.com/search?p={query}` | Often 307-loops from datacenters. Try `--chrome` on the user's machine. |
| Google HTML | `https://www.google.com/search?q={query}&gbv=1&hl=en` | Frequently a JS shell or captcha from cloud IPs. Do not click through `google.com` homepage. |

Default chain in `scripts/text_search.py`:

`duckduckgo → wikipedia → hackernews → yahoo → google`

```bash
# First engine that returns parseable hits wins
python3 scripts/text_search.py "agent skills cursor"

# Force a single engine
python3 scripts/text_search.py --engine wikipedia "agent skills"

# Inspect the HTML that was parsed
python3 scripts/text_search.py --save-html /tmp/serp.html --json "agent skills"

# Local Chrome DOM dump (use on a residential / user machine)
python3 scripts/text_search.py --chrome --engine google "agent skills"
```

## If Google (or Yahoo) blocks the bot

Order of fallbacks:

1. **Stay on DuckDuckGo Lite / Wikipedia / HN** — these are enough for most research tasks.
2. **Local Chrome on this VM** — `python3 scripts/text_search.py --chrome --engine google "…"` uses `google-chrome --dump-dom`. This still fails when the *IP* is flagged, not the User-Agent.
3. **User's computer** — Chiu's Mac Studio may be attached as a self-hosted Cursor worker. Re-run the same script there (home IP + real Chrome profile) instead of from the cloud VM. Attach an already-running Chrome with remote debugging if cookies/consent matter:

   ```bash
   # On the user's machine
   /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
   python3 scripts/text_browse.py "https://www.google.com/search?q=example&gbv=1"
   ```

4. **Do not** keep retrying Google unchanged. A JS shell with `<title>Google Search</title>` and almost no `<a href>` is a block.

## Reading a page (still no vision)

```bash
python3 scripts/text_browse.py https://cursor.com/docs/skills --json
```

The report includes HTTP status, title, stripped visible text, and outbound links from the source. Use `--save-html` when you need to grep the raw markup.

If a site only hydrates in a real browser, use a **snapshot/text** API (`browse snapshot`, `browse get text body`, `browse get html`), never a screenshot. Reconstruct the next action from refs, hrefs, or form `action` URLs.

## Interaction pattern (when a browser session is unavoidable)

1. Open the **deep link**, not a homepage that requires a click.
2. Read `title`, `url`, HTML, and link list.
3. If the next step is another document, `GET` that href directly.
4. If the next step is a query, encode it in the URL (`?q=`, `?p=`, `?search=`).
5. Stop when source shows a captcha/consent wall; switch engine or machine.

## Anti-patterns

- Opening `https://www.google.com/` then typing into a box and clicking Search.
- `browse screenshot` / computer-use screenshot loops to "see" the SERP.
- Clicking cookie banners when `gbv=1`, DDG Lite, or an API already avoids them.
- Assuming HTTP 200 means results exist — always parse hits or inspect source length/markers.

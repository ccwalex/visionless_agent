# text-browse

A **no-vision** skill and CLI for agents that need to search the web and read pages.

They construct a results URL (no Search-button click), download HTML or JSON, and parse hits from page source. Screenshots are out of scope.

## Why this exists

Cloud agents hitting Google often get a JavaScript shell or captcha. Yahoo may 307-loop. DuckDuckGo Lite, the Wikipedia API, and Hacker News Algolia still return usable source from a datacenter IP. If Google is required, run the same script on a residential machine (local Chrome / self-hosted worker) instead of retrying the blocked URL.

## Search (no clicks)

```bash
python3 scripts/text_search.py "cursor agent skills"
python3 scripts/text_search.py --json --save-html /tmp/serp.html "cursor agent skills"
python3 scripts/text_search.py --engine wikipedia "lisp"
```

Default engine chain: **DuckDuckGo Lite → Wikipedia → Hacker News → Yahoo → Google**.

`--chrome` uses `google-chrome --dump-dom` on the machine where the script runs. Use that on your own computer when Google blocks the cloud IP.

## Inspect any URL from source

```bash
python3 scripts/text_browse.py https://example.com
python3 scripts/text_browse.py https://example.com --json --save-html /tmp/page.html
```

## Agent skill

Project skill: `.cursor/skills/no-vision-browse/SKILL.md`

Cursor loads it automatically. It tells agents to prefer URL construction, source inspection, and engine fallbacks over vision or UI clicking.

## Tests

```bash
python3 -m unittest tests.test_parsers -v
```

Stdlib only (Python 3.10+). No extra packages.

## Local Google (when the cloud IP is blocked)

On your Mac, with Chrome installed:

```bash
python3 scripts/text_search.py --chrome --engine google "your query"
```

Or open Chrome with remote debugging and fetch `https://www.google.com/search?q=…&gbv=1` directly — still no homepage and no Search click.

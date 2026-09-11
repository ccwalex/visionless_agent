#!/usr/bin/env node
/**
 * Attach to Chrome DevTools Protocol, evaluate extract_affordances.js in the page.
 * Usage:
 *   node scripts/inspect_cdp.mjs --port 9222
 *   node scripts/inspect_cdp.mjs --port 9222 --target https://example.com
 *   node scripts/inspect_cdp.mjs --port 9222 --navigate https://example.com
 */
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function parseArgs(argv) {
  const out = { port: 9222, host: "127.0.0.1", target: null, navigate: null, includeHidden: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--port") out.port = Number(argv[++i]);
    else if (a === "--host") out.host = argv[++i];
    else if (a === "--target") out.target = argv[++i];
    else if (a === "--navigate") out.navigate = argv[++i];
    else if (a === "--include-hidden") out.includeHidden = true;
  }
  return out;
}

function httpGetJson(url) {
  return new Promise((resolve, reject) => {
    http
      .get(url, (res) => {
        let data = "";
        res.on("data", (c) => (data += c));
        res.on("end", () => {
          try {
            resolve(JSON.parse(data));
          } catch (err) {
            reject(new Error(`CDP HTTP ${url} returned non-JSON: ${data.slice(0, 200)}`));
          }
        });
      })
      .on("error", reject);
  });
}

function cdpSession(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    let nextId = 0;
    const pending = new Map();
    ws.addEventListener("open", () => {
      resolve({
        send(method, params = {}) {
          const id = ++nextId;
          return new Promise((res, rej) => {
            const t = setTimeout(() => {
              pending.delete(id);
              rej(new Error(`CDP timeout: ${method}`));
            }, 20000);
            pending.set(id, (msg) => {
              clearTimeout(t);
              if (msg.error) rej(new Error(`${method}: ${msg.error.message}`));
              else res(msg.result);
            });
            ws.send(JSON.stringify({ id, method, params }));
          });
        },
        close() {
          ws.close();
        },
      });
    });
    ws.addEventListener("error", (ev) => reject(ev.error || new Error("WebSocket error")));
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(String(ev.data));
      if (msg.id && pending.has(msg.id)) {
        const fn = pending.get(msg.id);
        pending.delete(msg.id);
        fn(msg);
      }
    });
  });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const listUrl = `http://${args.host}:${args.port}/json/list`;
  let targets;
  try {
    targets = await httpGetJson(listUrl);
  } catch (err) {
    console.error(
      JSON.stringify({
        error: "cdp_unavailable",
        message: `Cannot reach Chrome at ${listUrl}. Start Chrome with --remote-debugging-port=${args.port}`,
        detail: String(err.message || err),
      })
    );
    process.exit(2);
  }
  const pages = (targets || []).filter((t) => t.type === "page" && t.webSocketDebuggerUrl);
  if (!pages.length) {
    console.error(JSON.stringify({ error: "no_page_targets", message: "Chrome is up but has no inspectable tabs." }));
    process.exit(2);
  }
  let page = pages[0];
  const needle = args.navigate || args.target;
  if (needle) {
    page =
      pages.find((p) => (p.url || "").includes(needle)) ||
      pages.find((p) => (p.title || "").includes(needle)) ||
      page;
  }

  const session = await cdpSession(page.webSocketDebuggerUrl);
  try {
    await session.send("Runtime.enable");
    await session.send("Page.enable");
    if (args.navigate) {
      await session.send("Page.navigate", { url: args.navigate });
      for (let i = 0; i < 25; i++) {
        const ready = await session.send("Runtime.evaluate", {
          expression: "document.readyState",
          returnByValue: true,
        });
        const state = ready.result && ready.result.value;
        if (state === "interactive" || state === "complete") break;
        await new Promise((r) => setTimeout(r, 200));
      }
      await new Promise((r) => setTimeout(r, 300));
    }
    const extractSrc = fs.readFileSync(path.join(__dirname, "extract_affordances.js"), "utf8");
    const hidden = args.includeHidden ? "true" : "false";
    const expression = `${extractSrc}\nextractAffordances({ includeHidden: ${hidden} });`;
    const result = await session.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.text || "evaluate failed");
    }
    const value = result.result && result.result.value;
    process.stdout.write(JSON.stringify(value, null, 2) + "\n");
  } finally {
    session.close();
  }
}

main().catch((err) => {
  console.error(JSON.stringify({ error: "cdp_eval_failed", message: String(err.message || err) }));
  process.exit(2);
});

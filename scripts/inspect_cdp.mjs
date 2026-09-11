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
  const out = {
    port: 9222,
    host: "127.0.0.1",
    target: null,
    navigate: null,
    includeHidden: false,
    fill: null,
    value: "",
    submitForm: null,
    inspect: true,
  };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--port") out.port = Number(argv[++i]);
    else if (a === "--host") out.host = argv[++i];
    else if (a === "--target") out.target = argv[++i];
    else if (a === "--navigate") out.navigate = argv[++i];
    else if (a === "--include-hidden") out.includeHidden = true;
    else if (a === "--fill") out.fill = argv[++i];
    else if (a === "--value") out.value = argv[++i];
    else if (a === "--submit-form") out.submitForm = argv[++i];
    else if (a === "--no-inspect") out.inspect = false;
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
    if (args.fill) {
      const fillExpr = `(() => {
        const sel = ${JSON.stringify(args.fill)};
        const value = ${JSON.stringify(args.value)};
        const formSel = ${JSON.stringify(args.submitForm || "")};
        const el = document.querySelector(sel);
        if (!el) return { ok: false, error: "no_element", sel };
        el.focus();
        const proto = el instanceof HTMLInputElement
          ? HTMLInputElement.prototype
          : HTMLTextAreaElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
        if (setter) setter.call(el, value);
        else el.value = value;
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        if (formSel) {
          const form = document.querySelector(formSel);
          if (!form) return { ok: false, error: "no_form", formSel, value: el.value };
          if (typeof form.requestSubmit === "function") form.requestSubmit();
          else form.submit();
        }
        return { ok: true, filled: sel, value: el.value, url: location.href };
      })()`;
      const filled = await session.send("Runtime.evaluate", {
        expression: fillExpr,
        returnByValue: true,
      });
      if (filled.exceptionDetails) {
        throw new Error(filled.exceptionDetails.text || "fill evaluate failed");
      }
      const fillValue = filled.result && filled.result.value;
      if (!fillValue || !fillValue.ok) {
        process.stdout.write(JSON.stringify({ error: "fill_failed", detail: fillValue }, null, 2) + "\n");
        process.exit(2);
      }
      const before = fillValue.url;
      for (let i = 0; i < 40; i++) {
        const loc = await session.send("Runtime.evaluate", {
          expression: "location.href",
          returnByValue: true,
        });
        const href = loc.result && loc.result.value;
        if (href && href !== before) break;
        await new Promise((r) => setTimeout(r, 150));
      }
      await new Promise((r) => setTimeout(r, 400));
      if (!args.inspect) {
        process.stdout.write(JSON.stringify({ action: fillValue, url: (await session.send("Runtime.evaluate", { expression: "location.href", returnByValue: true })).result.value }, null, 2) + "\n");
        return;
      }
    }
    if (!args.inspect) return;
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

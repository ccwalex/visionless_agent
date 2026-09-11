#!/usr/bin/env node
/**
 * Attach to Chrome DevTools Protocol: multi-tab registry, inspect, fill.
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
    listTabs: false,
    tab: null,
    newTab: null,
    liveDom: false,
    noAdblock: false,
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
    else if (a === "--list-tabs") out.listTabs = true;
    else if (a === "--tab") out.tab = Number(argv[++i]);
    else if (a === "--new-tab") out.newTab = argv[++i];
    else if (a === "--live-dom") out.liveDom = true;
    else if (a === "--no-adblock") out.noAdblock = true;
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

function registryPath(port) {
  return `/tmp/visionless-tabs-${port}.json`;
}

function loadRegistry(port) {
  const p = registryPath(port);
  if (!fs.existsSync(p)) return { next_index: 0, tabs: {} };
  return JSON.parse(fs.readFileSync(p, "utf8"));
}

function saveRegistry(port, data) {
  fs.writeFileSync(registryPath(port), JSON.stringify(data, null, 2));
}

function mergeTargets(registry, targets, openerByCdpId = {}, forcedOpenerByCdpId = {}) {
  const tabs = registry.tabs || (registry.tabs = {});
  let nextIndex = registry.next_index || 0;
  const liveIds = new Set(targets.map((t) => t.id));
  for (const cdpId of Object.keys(tabs)) {
    if (!liveIds.has(cdpId)) delete tabs[cdpId];
  }
  for (const target of targets) {
    const cdpId = target.id;
    if (!tabs[cdpId]) {
      const openerCdp =
        forcedOpenerByCdpId[cdpId] || openerByCdpId[cdpId] || null;
      let openerIndex = null;
      if (openerCdp && tabs[openerCdp]) openerIndex = tabs[openerCdp].index;
      tabs[cdpId] = { index: nextIndex++, opener_index: openerIndex };
    }
    tabs[cdpId].url = target.url || "";
    tabs[cdpId].title = target.title || "";
  }
  registry.next_index = nextIndex;
  const out = targets.map((target) => {
    const rec = tabs[target.id];
    return {
      id: rec.index,
      opened_from: rec.opener_index ?? null,
      url: rec.url || target.url || "",
      title: rec.title || target.title || "",
      cdp_id: target.id,
    };
  });
  out.sort((a, b) => a.id - b.id);
  return out;
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

async function fetchPages(host, port) {
  const listUrl = `http://${host}:${port}/json/list`;
  const targets = await httpGetJson(listUrl);
  return (targets || []).filter((t) => t.type === "page" && t.webSocketDebuggerUrl);
}

async function getBrowserWs(host, port) {
  const ver = await httpGetJson(`http://${host}:${port}/json/version`);
  return ver.webSocketDebuggerUrl;
}

async function getOpenerMap(browserSession, pages) {
  const openerByCdpId = {};
  for (const page of pages) {
    try {
      const info = await browserSession.send("Target.getTargetInfo", { targetId: page.id });
      openerByCdpId[page.id] = info?.targetInfo?.openerId || null;
    } catch {
      openerByCdpId[page.id] = null;
    }
  }
  return openerByCdpId;
}

function pickPage(pages, tabs, args) {
  if (args.tab != null && !Number.isNaN(args.tab)) {
    const hit = tabs.find((t) => t.id === args.tab);
    if (!hit) throw new Error(`tab_not_found: no tab with id ${args.tab}`);
    const page = pages.find((p) => p.id === hit.cdp_id);
    if (!page) throw new Error(`tab_not_found: CDP target gone for tab ${args.tab}`);
    return { page, tab: hit };
  }
  const needle = args.navigate || args.target;
  if (needle) {
    const page =
      pages.find((p) => (p.url || "").includes(needle)) ||
      pages.find((p) => (p.title || "").includes(needle)) ||
      pages[0];
    const tab = tabs.find((t) => t.cdp_id === page.id) || tabs[0];
    return { page, tab };
  }
  return { page: pages[0], tab: tabs[0] };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  let pages;
  try {
    pages = await fetchPages(args.host, args.port);
  } catch (err) {
    console.error(
      JSON.stringify({
        error: "cdp_unavailable",
        message: `Cannot reach Chrome at http://${args.host}:${args.port}/json/list. Start Chrome with --remote-debugging-port=${args.port}`,
        detail: String(err.message || err),
      })
    );
    process.exit(2);
  }
  if (!pages.length) {
    console.error(JSON.stringify({ error: "no_page_targets", message: "Chrome is up but has no inspectable tabs." }));
    process.exit(2);
  }

  const browserWs = await getBrowserWs(args.host, args.port);
  const browserSession = await cdpSession(browserWs);
  try {
    const openerByCdpId = await getOpenerMap(browserSession, pages);
    const registry = loadRegistry(args.port);
    let tabs = mergeTargets(registry, pages, openerByCdpId);
    saveRegistry(args.port, registry);

    if (args.listTabs) {
      process.stdout.write(JSON.stringify({ tabs }, null, 2) + "\n");
      return;
    }

    let { page, tab } = pickPage(pages, tabs, args);

    if (args.newTab) {
      const parentCdpId = page.id;
      const parentTabId = tab.id;
      const knownBefore = new Set(Object.keys(registry.tabs || {}));
      await browserSession.send("Target.createTarget", {
        url: args.newTab,
        newWindow: false,
        background: false,
        openerId: parentCdpId,
      });
      await new Promise((r) => setTimeout(r, 800));
      pages = await fetchPages(args.host, args.port);
      const openerMap = await getOpenerMap(browserSession, pages);
      const forced = {};
      for (const p of pages) {
        if (!knownBefore.has(p.id) && p.id !== parentCdpId) {
          forced[p.id] = parentCdpId;
        }
      }
      tabs = mergeTargets(registry, pages, openerMap, forced);
      saveRegistry(args.port, registry);
      const child = tabs.filter((t) => t.opened_from === parentTabId).sort((a, b) => b.id - a.id)[0];
      if (child) {
        tab = child;
        page = pages.find((p) => p.id === child.cdp_id) || pages[pages.length - 1];
      } else {
        tab = tabs[tabs.length - 1];
        page = pages.find((p) => p.id === tab.cdp_id) || pages[pages.length - 1];
      }
    }

    if (args.tab != null) {
      await browserSession.send("Target.activateTarget", { targetId: page.id });
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
          process.stdout.write(
            JSON.stringify(
              {
                action: fillValue,
                url: (await session.send("Runtime.evaluate", { expression: "location.href", returnByValue: true }))
                  .result.value,
              },
              null,
              2
            ) + "\n"
          );
          return;
        }
      }

      if (!args.inspect) return;

      const loc = await session.send("Runtime.evaluate", {
        expression: "({ url: location.href, title: document.title })",
        returnByValue: true,
      });
      const liveMeta = loc.result && loc.result.value;
      if (liveMeta) {
        tab = { ...tab, url: liveMeta.url || tab.url, title: liveMeta.title || tab.title };
      }

      if (args.liveDom) {
        const extractSrc = fs.readFileSync(path.join(__dirname, "extract_affordances.js"), "utf8");
        const rulesSrc = fs.readFileSync(path.join(__dirname, "adblock_rules.json"), "utf8");
        const hidden = args.includeHidden ? "true" : "false";
        const noAdblock = args.noAdblock ? "true" : "false";
        const expression = `${extractSrc}\nextractAffordances({ includeHidden: ${hidden}, adblockRules: ${noAdblock ? "null" : rulesSrc}, tab: ${JSON.stringify(tab)} });`;
        const result = await session.send("Runtime.evaluate", {
          expression,
          returnByValue: true,
          awaitPromise: true,
        });
        if (result.exceptionDetails) {
          throw new Error(result.exceptionDetails.text || "evaluate failed");
        }
        process.stdout.write(JSON.stringify(result.result && result.result.value, null, 2) + "\n");
        return;
      }

      const htmlResult = await session.send("Runtime.evaluate", {
        expression: "document.documentElement.outerHTML",
        returnByValue: true,
      });
      const rawHtml = (htmlResult.result && htmlResult.result.value) || "";

      process.stdout.write(
        JSON.stringify(
          {
            mode: "html_for_inventory",
            tab,
            url: tab.url,
            title: tab.title,
            raw_html: rawHtml,
            no_adblock: args.noAdblock,
          },
          null,
          2
        ) + "\n"
      );
    } finally {
      session.close();
    }
  } finally {
    browserSession.close();
  }
}

main().catch((err) => {
  console.error(JSON.stringify({ error: "cdp_eval_failed", message: String(err.message || err) }));
  process.exit(2);
});

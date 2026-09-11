/**
 * In-page extractor. Evaluated inside Chrome (live DOM) so labels, disabled,
 * and visibility come from the rendered tree — not from pixels.
 */
(function (root) {
  function cssEscape(value) {
    if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(String(value));
    return String(value).replace(/[^a-zA-Z0-9_-]/g, "\\$&");
  }

  function compact(text) {
    return String(text || "")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 240);
  }

  function isVisible(el, includeHidden) {
    if (includeHidden) return true;
    if (!el || el.nodeType !== 1) return false;
    if (el.hasAttribute("hidden") || el.getAttribute("aria-hidden") === "true") return false;
    if (el.getAttribute("data-adblock-hidden") === "true") return false;
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (type === "hidden") return false;
    const st = root.getComputedStyle ? root.getComputedStyle(el) : null;
    if (st) {
      if (st.display === "none" || st.visibility === "hidden") return false;
      if (parseFloat(st.opacity || "1") === 0) return false;
    }
    const r = el.getBoundingClientRect ? el.getBoundingClientRect() : { width: 1, height: 1 };
    return r.width > 0 || r.height > 0;
  }

  function selectorFor(el) {
    if (el.id) return `#${cssEscape(el.id)}`;
    const testid = el.getAttribute("data-testid") || el.getAttribute("data-test");
    if (testid) return `[data-testid="${cssEscape(testid)}"]`;
    const name = el.getAttribute("name");
    const tag = el.tagName.toLowerCase();
    if (name) return `${tag}[name="${cssEscape(name)}"]`;
    const aria = el.getAttribute("aria-label");
    if (aria) return `${tag}[aria-label="${cssEscape(aria)}"]`;
    const type = el.getAttribute("type");
    if (type) return `${tag}[type="${cssEscape(type)}"]`;
    return tag;
  }

  function labelFor(el) {
    const doc = el.ownerDocument || root.document;
    const id = el.id;
    if (id && doc && doc.querySelector) {
      const byFor = doc.querySelector(`label[for="${cssEscape(id)}"]`);
      if (byFor) return compact(byFor.innerText);
    }
    const wrapping = el.closest && el.closest("label");
    if (wrapping) return compact(wrapping.innerText);
    return compact(
      el.getAttribute("aria-label") ||
        el.getAttribute("placeholder") ||
        el.getAttribute("title") ||
        ""
    );
  }

  function roleFor(el) {
    const role = (el.getAttribute("role") || "").toLowerCase();
    if (role) return role;
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (tag === "a") return "link";
    if (tag === "select") return "combobox";
    if (tag === "textarea") return "textbox";
    if (tag === "summary") return "disclosure";
    if (tag === "button") return type === "submit" || type === "" ? "submit" : "button";
    if (tag === "input") {
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (type === "range") return "slider";
      if (type === "submit" || type === "image") return "submit";
      if (type === "button" || type === "reset") return "button";
      return "textbox";
    }
    return "button";
  }

  function fieldRecord(el) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || (tag === "textarea" ? "textarea" : tag === "select" ? "select" : "text")).toLowerCase();
    const options =
      tag === "select"
        ? Array.from(el.options || [])
            .slice(0, 30)
            .map((o) => compact(o.text || o.value))
            .filter(Boolean)
        : undefined;
    return {
      tag,
      type,
      name: el.getAttribute("name") || "",
      id: el.id || "",
      selector: selectorFor(el),
      label: labelFor(el),
      placeholder: el.getAttribute("placeholder") || "",
      autocomplete: el.getAttribute("autocomplete") || "",
      required: el.required || el.getAttribute("aria-required") === "true",
      disabled: !!el.disabled,
      value_present: type === "password" ? false : Boolean(el.value),
      options,
    };
  }

  function buttonKind(el) {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    const role = (el.getAttribute("role") || "").toLowerCase();
    if (tag === "input" && (type === "submit" || type === "image")) return "submit";
    if (tag === "button" && (type === "submit" || type === "")) return "submit";
    if (tag === "input" && type === "button") return "button";
    if (role === "button") return "button";
    if (tag === "a") return "link-button";
    if (tag === "summary") return "disclosure";
    return "button";
  }

  function buttonRecord(el) {
    const text = compact(
      el.innerText ||
        el.getAttribute("value") ||
        el.getAttribute("aria-label") ||
        el.getAttribute("title") ||
        ""
    );
    return {
      kind: buttonKind(el),
      text,
      selector: selectorFor(el),
      id: el.id || "",
      name: el.getAttribute("name") || "",
      type: el.getAttribute("type") || el.tagName.toLowerCase(),
      href: el.getAttribute("href") || "",
      disabled: !!(el.disabled || el.getAttribute("aria-disabled") === "true"),
    };
  }

  function shouldHideAd(el, rules) {
    if (!rules || !el || el.nodeType !== 1) return null;
    if (el.getAttribute("data-inspect-tab")) return null;
    const tag = el.tagName.toLowerCase();
    const id = (el.id || "").toLowerCase();
    const cls = (el.className || "").toLowerCase();
    const src = (el.getAttribute("src") || el.getAttribute("href") || "").toLowerCase();
    const blob = `${id} ${cls} ${src}`;
    for (const pat of rules.id_class_patterns || []) {
      if (blob.includes(pat.toLowerCase())) return { reason: "id_class_pattern", selector_or_host: `${tag}.${pat}` };
    }
    for (const host of rules.hosts || []) {
      if (src.includes(host)) return { reason: "host", selector_or_host: `${tag}[src~=${host}]` };
    }
    for (const pat of rules.tag_patterns || []) {
      if (pat.tag && pat.tag.toLowerCase() !== tag) continue;
      if (pat.class_contains && cls.includes(pat.class_contains.toLowerCase()))
        return { reason: pat.reason || "tag_pattern", selector_or_host: `${tag}.${pat.class_contains}` };
      if (pat.src_contains && src.includes(pat.src_contains.toLowerCase()))
        return { reason: pat.reason || "tag_pattern", selector_or_host: `${tag}[src*=${pat.src_contains}]` };
      const prefix = pat.id_prefix || pat.class_prefix || "";
      if (pat.id_prefix && id.startsWith(prefix.toLowerCase()))
        return { reason: pat.reason || "tag_pattern", selector_or_host: `${tag}#${id}` };
      if (pat.class_prefix && cls.split(/\s+/).some((c) => c.startsWith(prefix.toLowerCase())))
        return { reason: pat.reason || "tag_pattern", selector_or_host: `${tag}.${cls}` };
    }
    return null;
  }

  function hideAds(doc, rules) {
    const removed = [];
    if (!rules) return removed;
    doc.querySelectorAll("*").forEach((el) => {
      const hit = shouldHideAd(el, rules);
      if (hit) {
        el.setAttribute("data-adblock-hidden", "true");
        removed.push(hit);
      }
    });
    return removed;
  }

  function visibleText(doc) {
    const skip = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "SVG", "TEMPLATE"]);
    const parts = [];
    function walk(node) {
      if (node.nodeType === 3) {
        parts.push(node.textContent || "");
        return;
      }
      if (node.nodeType !== 1) return;
      if (skip.has(node.tagName)) return;
      if (node.getAttribute("data-adblock-hidden") === "true") return;
      if (node.getAttribute("data-inspect-tab")) return;
      const block = new Set(["P", "BR", "TR", "LI", "H1", "H2", "H3", "H4", "H5", "H6", "DIV"]);
      if (block.has(node.tagName)) parts.push("\n");
      for (const child of node.childNodes) walk(child);
    }
    walk(doc.body || doc.documentElement);
    return String(parts.join(""))
      .replace(/[ \t]+/g, " ")
      .replace(/\n{3,}/g, "\n\n")
      .trim();
  }

  const LOGIN_RE =
    /\b(log\s*in|sign\s*in|sign\s*on|sign\s*up|create account|continue with (google|apple|github|microsoft)|authenticate|sso)\b/i;
  const SEARCH_RE = /\b(search|find|query|go)\b/i;
  const CAPTCHA_RE = /recaptcha|h-captcha|cf-turnstile|captcha/i;

  function extractAffordances(options) {
    options = options || {};
    const includeHidden = !!options.includeHidden;
    const doc = root.document || root;
    const body = doc.body || doc.documentElement;
    if (!body) {
      return { error: "no_document" };
    }

    const adblockRules = options.adblockRules || null;
    const removed = hideAds(doc, adblockRules);
    const adblock = {
      enabled: !!adblockRules,
      removed_count: removed.length,
      removed,
    };

    const controls = [];
    function addControl(rec, formIndex) {
      rec.form = formIndex;
      rec.id = controls.length;
      controls.push(rec);
    }

    const forms = [];
    const formEls = Array.from(doc.querySelectorAll("form"));
    const formControls = new Set();

    formEls.forEach((form, index) => {
      const fields = [];
      form.querySelectorAll("input, textarea, select").forEach((el) => {
        formControls.add(el);
        const type = (el.getAttribute("type") || "").toLowerCase();
        if (type === "hidden") {
          fields.push({
            tag: "input",
            type: "hidden",
            name: el.getAttribute("name") || "",
            id: el.id || "",
            selector: selectorFor(el),
            label: "",
            placeholder: "",
            autocomplete: "",
            required: false,
            disabled: !!el.disabled,
            value_present: Boolean(el.value),
          });
          return;
        }
        if (!isVisible(el, includeHidden)) return;
        const rec = fieldRecord(el);
        fields.push(rec);
        addControl(
          {
            role: roleFor(el),
            tag: rec.tag,
            selector: rec.selector,
            name: rec.name,
            text: rec.label || rec.placeholder,
            label: rec.label,
            href: "",
            disabled: rec.disabled,
          },
          index
        );
      });
      const submits = [];
      form
        .querySelectorAll(
          'button, input[type="submit"], input[type="button"], input[type="image"], [role="button"]'
        )
        .forEach((el) => {
          formControls.add(el);
          if (!isVisible(el, includeHidden)) return;
          const rec = buttonRecord(el);
          submits.push(rec);
          addControl(
            {
              role: roleFor(el),
              tag: el.tagName.toLowerCase(),
              selector: rec.selector,
              name: rec.name,
              text: rec.text,
              label: rec.text,
              href: "",
              disabled: rec.disabled,
            },
            index
          );
        });
      forms.push({
        index,
        selector: selectorFor(form),
        id: form.id || "",
        name: form.getAttribute("name") || "",
        action: form.getAttribute("action") || "",
        method: (form.getAttribute("method") || "get").toLowerCase(),
        fields,
        submits,
      });
    });

    const buttons = [];
    const buttonSel =
      'button, input[type="submit"], input[type="button"], input[type="reset"], input[type="image"], [role="button"], summary';
    doc.querySelectorAll(buttonSel).forEach((el) => {
      if (formControls.has(el)) return;
      if (!isVisible(el, includeHidden)) return;
      const rec = buttonRecord(el);
      buttons.push(rec);
      addControl(
        {
          role: roleFor(el),
          tag: el.tagName.toLowerCase(),
          selector: rec.selector,
          name: rec.name,
          text: rec.text,
          label: rec.text,
          href: rec.href,
          disabled: rec.disabled,
        },
        null
      );
    });

    const loose_fields = [];
    doc.querySelectorAll("input, textarea, select").forEach((el) => {
      if (formControls.has(el)) return;
      const type = (el.getAttribute("type") || "").toLowerCase();
      if (type === "hidden") return;
      if (!isVisible(el, includeHidden)) return;
      if (["submit", "button", "reset", "image"].includes(type)) return;
      const rec = fieldRecord(el);
      loose_fields.push(rec);
      addControl(
        {
          role: roleFor(el),
          tag: rec.tag,
          selector: rec.selector,
          name: rec.name,
          text: rec.label || rec.placeholder,
          label: rec.label,
          href: "",
          disabled: rec.disabled,
        },
        null
      );
    });

    const links = [];
    doc.querySelectorAll("a[href]").forEach((el) => {
      if (!isVisible(el, includeHidden)) return;
      const href = el.getAttribute("href") || "";
      if (!href || href.startsWith("javascript:") || href === "#") return;
      const text = compact(el.innerText || el.getAttribute("aria-label") || "");
      if (!text) return;
      links.push({ text, href, selector: selectorFor(el) });
      if ((el.getAttribute("role") || "").toLowerCase() !== "button") {
        addControl(
          {
            role: "link",
            tag: "a",
            selector: selectorFor(el),
            name: "",
            text,
            label: text,
            href,
            disabled: false,
          },
          null
        );
      }
    });

    const headings = [];
    doc.querySelectorAll("h1,h2,h3,h4,h5,h6").forEach((el) => {
      if (!isVisible(el, includeHidden)) return;
      const tag = el.tagName.toLowerCase();
      headings.push({
        level: parseInt(tag[1], 10),
        text: compact(el.innerText),
        selector: selectorFor(el),
      });
    });

    const landmarks = [];
    doc.querySelectorAll("main,nav,article,aside,header,footer").forEach((el) => {
      if (el.getAttribute("data-inspect-tab")) return;
      if (!isVisible(el, includeHidden)) return;
      landmarks.push({
        tag: el.tagName.toLowerCase(),
        selector: selectorFor(el),
        text: compact(el.innerText),
      });
    });

    const media = [];
    doc.querySelectorAll("img,video").forEach((el) => {
      if (!isVisible(el, includeHidden)) return;
      media.push({
        tag: el.tagName.toLowerCase(),
        selector: selectorFor(el),
        alt: el.getAttribute("alt") || "",
        src: el.getAttribute("src") || "",
      });
    });

    const passwordFields = [...forms.flatMap((f) => f.fields), ...loose_fields].filter((f) => f.type === "password");
    const loginButtons = [...forms.flatMap((f) => f.submits), ...buttons].filter((b) => LOGIN_RE.test(b.text));
    const html = (doc.documentElement && doc.documentElement.outerHTML) || "";
    const captcha =
      CAPTCHA_RE.test(html) || !!doc.querySelector("[data-sitekey], .g-recaptcha, iframe[src*='recaptcha']");
    const title = compact(doc.title || "");
    const url = (doc.location && doc.location.href) || "";
    const authLikely =
      passwordFields.length > 0 ||
      loginButtons.length > 0 ||
      LOGIN_RE.test(title) ||
      /\/(login|signin|sign-in|signup|auth|session)\b/i.test(url);

    const searchForms = forms.filter((f) =>
      f.fields.some(
        (field) =>
          field.type === "search" ||
          /^(q|query|search|s)$/i.test(field.name) ||
          SEARCH_RE.test(field.label) ||
          SEARCH_RE.test(field.placeholder)
      )
    );

    const pathways = [];
    if (authLikely) {
      pathways.push({
        id: "handoff-login",
        kind: "handoff",
        reason: passwordFields.length ? "password_field" : "login_cta",
        summary:
          "This page looks like it needs an account. Do not guess credentials. Hand the headed Chrome window to the user, then re-run inspect on the same CDP session.",
      });
    }
    if (captcha) {
      pathways.push({
        id: "handoff-captcha",
        kind: "handoff",
        reason: "captcha",
        summary: "Captcha markup is present. Hand the browser to the user, then re-inspect.",
      });
    }
    searchForms.forEach((f, i) => {
      const field = f.fields.find((x) => x.type === "search" || /^(q|query|search|s)$/i.test(x.name)) || f.fields[0];
      const submit = f.submits[0];
      pathways.push({
        id: `search-form-${i}`,
        kind: "fill_and_submit",
        form_selector: f.selector,
        fill: field ? { selector: field.selector, name: field.name, label: field.label } : null,
        submit: submit ? { selector: submit.selector, text: submit.text } : { how: "form.submit() or Enter" },
        get_shortcut: f.method === "get" && f.action != null ? `GET ${f.action || url} ?${field && field.name ? field.name : "q"}=` : null,
        summary: `Fill ${field ? field.selector : "the query field"} then submit${submit ? " via " + submit.selector : ""}. Prefer encoding the query in the URL when method=GET.`,
      });
    });
    forms.forEach((f, i) => {
      if (searchForms.includes(f)) return;
      if (!f.fields.some((x) => x.type !== "hidden")) return;
      const submit = f.submits[0];
      pathways.push({
        id: `form-${i}`,
        kind: "fill_and_submit",
        form_selector: f.selector,
        method: f.method,
        action: f.action,
        fields: f.fields.filter((x) => x.type !== "hidden").map((x) => ({
          selector: x.selector,
          name: x.name,
          type: x.type,
          label: x.label,
          required: x.required,
        })),
        submit: submit ? { selector: submit.selector, text: submit.text } : { how: "form.submit() or Enter" },
        summary: `Form ${f.selector || "#" + i} (${f.method.toUpperCase()} ${f.action || "."}) — fill listed fields, activate ${submit ? submit.selector : "submit"}.`,
      });
    });
    buttons.forEach((b, i) => {
      if (LOGIN_RE.test(b.text)) return;
      pathways.push({
        id: `button-${i}`,
        kind: "activate",
        selector: b.selector,
        text: b.text,
        summary: `Activate ${b.selector} (“${b.text || b.kind}”).`,
      });
    });

    const pageText = visibleText(doc);
    const maxText = options.maxText || 20000;
    const tab = options.tab || null;

    const out = {
      source: "live-dom",
      url,
      title,
      auth: {
        likely: authLikely,
        captcha,
        password_fields: passwordFields.length,
        login_buttons: loginButtons.map((b) => b.text),
        signals: [
          passwordFields.length ? "password_input" : null,
          loginButtons.length ? "login_cta" : null,
          LOGIN_RE.test(title) ? "title" : null,
          captcha ? "captcha" : null,
        ].filter(Boolean),
      },
      adblock,
      controls,
      forms,
      buttons,
      loose_fields,
      links,
      contents: {
        headings,
        landmarks,
        media,
        text: pageText.slice(0, maxText),
        text_chars: pageText.length,
      },
      pathways,
    };
    if (tab) out.tab = tab;
    return out;
  }

  root.extractAffordances = extractAffordances;
  if (typeof module !== "undefined" && module.exports) {
    module.exports = { extractAffordances };
  }
})(typeof globalThis !== "undefined" ? globalThis : this);

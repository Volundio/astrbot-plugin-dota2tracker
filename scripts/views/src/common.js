/* ---- shared helpers (identical in every dota2tracker view) ---- */
const bridge = window.AstrBotPluginView || null;

/** Translate a key relative to the plugin i18n root; `{name}` placeholders are filled from vars. */
function t(key, vars, fallback) {
  let text = bridge ? bridge.t(key, "") : "";
  if (!text) text = fallback !== undefined ? fallback : key;
  if (vars) {
    text = text.replace(/\{(\w+)\}/g, (m, name) => (vars[name] === undefined || vars[name] === null ? m : String(vars[name])));
  }
  return text;
}

function hasKey(key) {
  return Boolean(bridge && bridge.t(key, ""));
}

function locale() {
  return (bridge && bridge.getLocale()) || "zh-CN";
}

/** Apply data-i18n / data-i18n-title / data-i18n-placeholder / data-i18n-aria attributes. */
function applyI18n(root) {
  const scope = root || document;
  scope.querySelectorAll("[data-i18n]").forEach((node) => { node.textContent = t(node.dataset.i18n); });
  scope.querySelectorAll("[data-i18n-title]").forEach((node) => { node.title = t(node.dataset.i18nTitle); });
  scope.querySelectorAll("[data-i18n-placeholder]").forEach((node) => { node.placeholder = t(node.dataset.i18nPlaceholder); });
  scope.querySelectorAll("[data-i18n-aria]").forEach((node) => { node.setAttribute("aria-label", t(node.dataset.i18nAria)); });
  document.documentElement.lang = locale();
}

/** Build DOM safely: strings become text nodes, never HTML. */
function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (name === "class") node.className = value;
    else if (name === "text") node.textContent = value;
    else if (name.startsWith("on") && typeof value === "function") node.addEventListener(name.slice(2), value);
    else if (name in node && typeof value !== "string") node[name] = value;
    else node.setAttribute(name, value === true ? "" : String(value));
  }
  append(node, children);
  return node;
}

function append(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    node.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function clear(node) {
  while (node.firstChild) node.removeChild(node.firstChild);
  return node;
}

function setContent(node, ...children) {
  return append(clear(node), children);
}

function fmtTime(unixSeconds) {
  if (unixSeconds === undefined || unixSeconds === null || unixSeconds === 0) return t("views.common.never");
  const date = new Date(Number(unixSeconds) * 1000);
  if (Number.isNaN(date.getTime())) return String(unixSeconds);
  try {
    return new Intl.DateTimeFormat(locale(), { dateStyle: "medium", timeStyle: "medium" }).format(date);
  } catch {
    return date.toLocaleString();
  }
}

function fmtNumber(value) {
  if (value === undefined || value === null) return "-";
  try {
    return new Intl.NumberFormat(locale()).format(value);
  } catch {
    return String(value);
  }
}

function fmtBytes(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${fmtNumber(n)} B`;
  if (n < 1024 * 1024) return `${fmtNumber(Math.round(n / 102.4) / 10)} KB`;
  return `${fmtNumber(Math.round(n / 104857.6) / 10)} MB`;
}

function badge(text, kind) {
  return h("span", { class: `badge ${kind || ""}`.trim(), text });
}

function yesNoBadge(value, onKey, offKey, offKind) {
  return value ? badge(t(onKey || "views.common.enabled"), "ok") : badge(t(offKey || "views.common.disabled"), offKind || "");
}

/** Map a bridge error (message is a backend error code) to translated text. */
function errorText(error) {
  const code = String((error && error.message) || error || "").trim();
  if (/^[a-z0-9_]+$/.test(code) && hasKey(`views.common.errors.${code}`)) return t(`views.common.errors.${code}`);
  if (/^invalid_[a-z0-9_]+$/.test(code)) return t("views.common.errors.invalid_param", { name: code.slice(8) });
  return t("views.common.errors.unknown", { message: code || "?" });
}

/** Loading / empty / error placeholder. */
function stateBox(kind, text, retry) {
  const box = h("div", { class: `state ${kind}`, role: kind === "error" ? "alert" : "status" });
  if (kind === "loading") box.appendChild(h("span", { class: "spinner", "aria-hidden": "true" }));
  box.appendChild(document.createTextNode(text || t(`views.common.${kind}`)));
  if (retry) box.append(h("br"), h("button", { type: "button", onclick: retry, text: t("views.common.retry") }));
  return box;
}

let toastArea = null;
function toast(text, kind) {
  if (!toastArea) {
    toastArea = h("div", { class: "toast-area", role: "status", "aria-live": "polite" });
    document.body.appendChild(toastArea);
  }
  const node = h("div", { class: `toast ${kind || ""}`, text });
  toastArea.appendChild(node);
  setTimeout(() => node.remove(), kind === "danger" ? 6000 : 3000);
}

/** In-page confirmation dialog; resolves true/false. */
function askConfirm(message, { title, confirmText, danger = true } = {}) {
  return new Promise((resolve) => {
    const previous = document.activeElement;
    const close = (result) => {
      overlay.remove();
      document.removeEventListener("keydown", onKey);
      if (previous && previous.focus) previous.focus();
      resolve(result);
    };
    const onKey = (event) => { if (event.key === "Escape") close(false); };
    const ok = h("button", { type: "button", class: danger ? "danger" : "primary", text: confirmText || t("views.common.confirm"), onclick: () => close(true) });
    const overlay = h("div", { class: "overlay", onclick: (event) => { if (event.target === overlay) close(false); } },
      h("div", { class: "dialog", role: "alertdialog", "aria-modal": "true" },
        h("h3", { text: title || t("views.common.confirmTitle") }),
        h("p", { text: message }),
        h("div", { class: "toolbar" },
          h("button", { type: "button", text: t("views.common.cancel"), onclick: () => close(false) }),
          ok)));
    document.addEventListener("keydown", onKey);
    document.body.appendChild(overlay);
    ok.focus();
  });
}

/** Disable a button while an async action runs. */
async function busy(button, action) {
  if (button) button.disabled = true;
  try {
    return await action();
  } finally {
    if (button) button.disabled = false;
  }
}

/** Start the view: wait for the bridge context, then re-render on every language/theme change. */
async function boot(onContext) {
  if (!bridge) {
    document.body.textContent = "";
    document.body.appendChild(stateBox("error", "AstrBot plugin view bridge is unavailable. Open this page from the AstrBot dashboard."));
    return;
  }
  await bridge.ready();
  bridge.onContext(() => {
    applyI18n();
    onContext();
  });
}

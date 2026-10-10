// Settings: localStorage["tcmstudio.settings"] (CONTRACTS §8). Keys stay in this browser; with rememberKeys off they
// live in sessionStorage only and are gone when the tab closes.

import { Emitter } from "./events.js";
import { storage } from "./util.js";

export const SETTINGS_KEY = "tcmstudio.settings";
export const SESSION_KEYS_KEY = "tcmstudio.keys";
export const LANG_KEY = "tcmscience.lang";

export const DEFAULTS = Object.freeze({
  lang: "zh",
  theme: "system",
  provider: "tao",
  models: {},
  baseUrls: {},
  keys: {},
  rememberKeys: true,
  customProviders: [],
  route: "auto",
  thinking: true,
  temperature: null,
  maxTokens: 8192,
  maxSteps: 16,
  compute: "auto",
  browserAcceleration: "auto",
  runner: { url: "http://127.0.0.1:8765", token: "" },
  web: false,
  onboarded: false,
  inspectorWidth: 400,
  sidebarCollapsed: false,
});

const ENUMS = {
  lang: ["zh", "en"],
  theme: ["system", "light", "dark"],
  route: ["auto", "direct", "runner"],
  compute: ["auto", "browser", "runner"],
  browserAcceleration: ["auto", "cpu", "reference"],
};

const bus = new Emitter();

/** A fresh, complete settings object: what was saved, over DEFAULTS, with nested objects copied. */
export function loadSettings() {
  const saved = storage.getJSON(SETTINGS_KEY) || {};
  const s = normalize(saved);
  if (!s.rememberKeys) s.keys = { ...(storage.getJSON(SESSION_KEYS_KEY, "session") || {}) };
  return s;
}

export function saveSettings(next) {
  const prev = loadSettings();
  const s = normalize(next);
  const toStore = clone(s);
  if (!s.rememberKeys) {
    storage.setJSON(SESSION_KEYS_KEY, s.keys, "session");
    toStore.keys = {};
  } else {
    storage.remove(SESSION_KEYS_KEY, "session");
  }
  storage.setJSON(SETTINGS_KEY, toStore);
  storage.set(LANG_KEY, s.lang);
  bus.emit("change", { settings: clone(s), previous: prev, changed: changedKeys(prev, s) });
  return s;
}

/** Merge a patch (one level deep for the map-like fields) into the saved settings and save. */
export function updateSettings(patch) {
  const cur = loadSettings();
  const next = { ...cur, ...patch };
  for (const k of ["models", "baseUrls", "keys", "runner"]) {
    if (patch && patch[k] && typeof patch[k] === "object" && !Array.isArray(patch[k])) next[k] = { ...cur[k], ...patch[k] };
  }
  return saveSettings(next);
}

/** fn({settings, previous, changed}) on every save in this tab, and on saves in other tabs. */
export function onSettingsChange(fn) {
  return bus.on("change", fn);
}

/** Forget every API key (both storages). */
export function clearKeys() {
  storage.remove(SESSION_KEYS_KEY, "session");
  const s = loadSettings();
  s.keys = {};
  return saveSettings(s);
}

function normalize(raw) {
  const src = raw && typeof raw === "object" ? raw : {};
  const s = { ...clone(DEFAULTS), ...clone(src) };
  for (const [k, allowed] of Object.entries(ENUMS)) if (!allowed.includes(s[k])) s[k] = DEFAULTS[k];
  for (const k of ["models", "baseUrls", "keys"]) if (!s[k] || typeof s[k] !== "object" || Array.isArray(s[k])) s[k] = {};
  if (!Array.isArray(s.customProviders)) s.customProviders = [];
  s.runner = { ...DEFAULTS.runner, ...(s.runner && typeof s.runner === "object" ? s.runner : {}) };
  s.runner.url = String(s.runner.url || DEFAULTS.runner.url);
  s.runner.token = String(s.runner.token || "");
  s.maxTokens = posInt(s.maxTokens, DEFAULTS.maxTokens);
  s.maxSteps = Math.min(64, posInt(s.maxSteps, DEFAULTS.maxSteps));
  s.inspectorWidth = posInt(s.inspectorWidth, DEFAULTS.inspectorWidth);
  s.temperature = s.temperature === null || s.temperature === "" || s.temperature === undefined || !Number.isFinite(Number(s.temperature))
    ? null : Number(s.temperature);
  for (const k of ["rememberKeys", "thinking", "web", "onboarded", "sidebarCollapsed"]) s[k] = Boolean(s[k]);
  if (typeof s.provider !== "string" || !s.provider) s.provider = DEFAULTS.provider;
  return s;
}

function posInt(v, dflt) {
  const n = Math.floor(Number(v));
  return Number.isFinite(n) && n > 0 ? n : dflt;
}

function clone(v) {
  return v === undefined ? undefined : JSON.parse(JSON.stringify(v));
}

function changedKeys(a, b) {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].filter((k) => JSON.stringify(a[k]) !== JSON.stringify(b[k]));
}

// another tab saved: tell this tab's listeners, so a key or provider change is not silently overwritten later
try {
  globalThis.addEventListener?.("storage", (e) => {
    if (e.key !== SETTINGS_KEY) return;
    const settings = loadSettings();
    bus.emit("change", { settings, previous: null, changed: ["*"], external: true });
  });
} catch { /* not a window */ }

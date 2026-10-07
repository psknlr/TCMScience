import { resetStorage } from "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import { DEFAULTS, LANG_KEY, SESSION_KEYS_KEY, SETTINGS_KEY, clearKeys, loadSettings, onSettingsChange, saveSettings, updateSettings } from "../js/core/settings.js";

beforeEach(() => resetStorage());

test("defaults are the contract's", () => {
  const s = loadSettings();
  assert.deepEqual(s, JSON.parse(JSON.stringify(DEFAULTS)));
  assert.equal(s.provider, "tao");
  assert.equal(s.maxSteps, 16);
  assert.equal(s.maxTokens, 8192);
  assert.deepEqual(s.runner, { url: "http://127.0.0.1:8765", token: "" });
  assert.equal(s.web, false);
  s.models.x = "mutated";
  assert.deepEqual(loadSettings().models, {}, "each load is a fresh copy");
});

test("save, load, update; invalid values fall back; the language is mirrored", () => {
  saveSettings({ ...loadSettings(), lang: "en", theme: "neon", compute: "gpu", maxSteps: "abc", temperature: "0.7", models: { deepseek: "deepseek-chat" } });
  const s = loadSettings();
  assert.equal(s.lang, "en");
  assert.equal(s.theme, "system");
  assert.equal(s.compute, "auto");
  assert.equal(s.maxSteps, 16);
  assert.equal(s.temperature, 0.7);
  assert.equal(globalThis.localStorage.getItem(LANG_KEY), "en");
  updateSettings({ models: { qwen: "qwen-plus" }, runner: { token: "tok" } });
  const u = loadSettings();
  assert.deepEqual(u.models, { deepseek: "deepseek-chat", qwen: "qwen-plus" });
  assert.deepEqual(u.runner, { url: "http://127.0.0.1:8765", token: "tok" });
  globalThis.localStorage.setItem(SETTINGS_KEY, "{broken");
  assert.equal(loadSettings().provider, "tao", "a corrupt record gives the defaults");
});

test("rememberKeys off: keys live in sessionStorage only", () => {
  saveSettings({ ...loadSettings(), rememberKeys: false, keys: { openai: "sk-secret" } });
  assert.doesNotMatch(globalThis.localStorage.getItem(SETTINGS_KEY), /sk-secret/);
  assert.match(globalThis.sessionStorage.getItem(SESSION_KEYS_KEY), /sk-secret/);
  assert.equal(loadSettings().keys.openai, "sk-secret");
  saveSettings({ ...loadSettings(), rememberKeys: true });
  assert.match(globalThis.localStorage.getItem(SETTINGS_KEY), /sk-secret/);
  assert.equal(globalThis.sessionStorage.getItem(SESSION_KEYS_KEY), null);
  clearKeys();
  assert.deepEqual(loadSettings().keys, {});
});

test("listeners hear what changed", () => {
  const seen = [];
  const off = onSettingsChange((e) => seen.push(e.changed));
  updateSettings({ compute: "browser" });
  off();
  updateSettings({ compute: "runner" });
  assert.deepEqual(seen, [["compute"]]);
});

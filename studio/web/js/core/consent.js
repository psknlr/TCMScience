// The visitor's choices about what this site may record, kept in this browser only (localStorage "tcmstudio.consent";
// studio/docs/TELEMETRY.md). Nothing here is sent anywhere by itself.
//
//   content    true: agreed to the conversation-record notice of CONSENT_VERSION; false: declined or withdrew; null: not
//              asked under the current notice (a new version of the notice asks again). Records are sent only while
//              it is true, and never by default.
//   analytics  usage statistics (counts, no text) are on unless switched off here; a browser that sends Do Not Track or
//              Global Privacy Control has them off whatever is stored.
//   key        the deletion key, made at the first agreement and kept after a withdrawal: presenting it deletes every
//              record made with it (the records carry only its SHA-256, telemetry-contract.js ownerOf).

import { CONSENT_VERSION, OWNER_KEY, ownerOf } from "./telemetry-contract.js";
import { storage } from "./util.js";

export const CONSENT_KEY = "tcmstudio.consent";

function read() {
  const v = storage.getJSON(CONSENT_KEY);
  return v && typeof v === "object" && !Array.isArray(v) ? v : {};
}

/** 32 random bytes as base64url (43 characters). */
export function newKey() {
  const b = new Uint8Array(32);
  globalThis.crypto.getRandomValues(b);
  return btoa(String.fromCharCode(...b)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** {content: true|false|null, at, version, analytics, hasKey}. */
export function consentState() {
  const c = read();
  return {
    content: c.v === CONSENT_VERSION && typeof c.content === "boolean" ? c.content : null,
    version: Number.isInteger(c.v) ? c.v : null,
    at: typeof c.at === "string" ? c.at : null,
    analytics: c.analytics !== false,
    hasKey: OWNER_KEY.test(String(c.key || "")),
  };
}

export const contentConsent = () => consentState().content;

/** Agree (true) or decline / withdraw (false) under the current notice. Agreeing makes the deletion key if there is
 * none; withdrawing keeps it, so what was recorded can still be deleted. */
export function setContentConsent(agreed) {
  const c = read();
  const key = OWNER_KEY.test(String(c.key || "")) ? c.key : agreed ? newKey() : undefined;
  const next = { ...c, v: CONSENT_VERSION, content: Boolean(agreed), at: new Date().toISOString() };
  if (key) next.key = key;
  else delete next.key;
  storage.setJSON(CONSENT_KEY, next);
  return consentState();
}

/** Usage statistics on or off (the visitor's own switch). */
export function setAnalytics(on) {
  storage.setJSON(CONSENT_KEY, { ...read(), analytics: Boolean(on) });
  return consentState();
}

/** Does the browser ask not to be tracked (Global Privacy Control, or Do Not Track)? */
export function browserOptOut(nav = globalThis.navigator) {
  try {
    if (nav?.globalPrivacyControl === true) return true;
    const dnt = nav?.doNotTrack ?? globalThis.doNotTrack;
    return dnt === "1" || dnt === "yes";
  } catch {
    return false;
  }
}

/** May usage statistics be sent from this browser now? */
export function analyticsAllowed(nav) {
  return consentState().analytics && !browserOptOut(nav);
}

export const deletionKey = () => (OWNER_KEY.test(String(read().key || "")) ? read().key : null);

/** The digest records carry as their owner, or null without a key. */
export async function ownerHash() {
  const key = deletionKey();
  return key ? ownerOf(key) : null;
}

/** Forget every choice and the key (the "clear all data" of Settings → General). */
export function forgetConsent() {
  storage.remove(CONSENT_KEY);
}

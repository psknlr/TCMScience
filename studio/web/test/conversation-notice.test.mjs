// A failed turn's notice (ui/conversation.js): a key or an account the service refuses points to the model settings,
// whatever words the service uses ("invalid x-api-key", out of credit); an error a retry may fix keeps its Retry; a
// permanent one that the settings cannot fix offers neither. The DOM is studio/web/dev/test/setup.mjs's (linkedom).
import { resetStorage } from "./fixtures/setup.mjs";
import { window } from "../dev/test/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";

import { App } from "../js/ui/app.js";
import { mountConversation } from "../js/ui/conversation.js";
import { registerUiStrings } from "../js/ui/strings.js";

registerUiStrings();
window.HTMLElement.prototype.scrollTo ||= function scrollTo() {};   // the thread keeps to its bottom; linkedom has no layout

/** The labels of the buttons in the notice of a conversation whose answer failed with `error`. */
function noticeActions(error) {
  resetStorage();
  const app = new App();
  const at = "2026-11-06T00:00:00.000Z";
  app.state.conversation = { id: "c1", projectId: "p1", title: "t", leafId: "a1" };
  app.state.messages = [
    { id: "u1", conversationId: "c1", parentId: null, role: "user", content: "hi", createdAt: at },
    { id: "a1", conversationId: "c1", parentId: "u1", role: "assistant", content: "", status: "error", error, createdAt: at },
  ];
  const main = document.createElement("main");
  mountConversation(app, main);
  const alert = main.querySelector("[data-msg=\"a1\"] [role=\"alert\"]");
  assert.ok(alert, "the failed answer has a notice");
  return [...alert.querySelectorAll("button")].map((b) => b.textContent.trim());
}

test("a refused key or account opens the model settings; a transient error keeps Retry; other permanent errors offer nothing", () => {
  const settings = "打开模型设置";
  // Anthropic's 401: its words say x-api-key, its type says what it is
  assert.deepEqual(noticeActions({ message: "invalid x-api-key", kind: "model", status: 401, type: "authentication_error", permanent: true }), [settings]);
  assert.deepEqual(noticeActions({ message: "You exceeded your current quota", kind: "model", status: 429, type: "insufficient_quota", permanent: true }), [settings]);
  assert.deepEqual(noticeActions({ message: "Your credit balance is too low", kind: "model", status: 402, type: "billing_error", permanent: true }), [settings]);
  assert.deepEqual(noticeActions({ message: "Incorrect API key provided", kind: "model", status: 401, type: "invalid_request_error", permanent: true }), [settings]);
  assert.deepEqual(noticeActions({ message: "The model service sent nothing for 90 s", kind: "network", type: "stream_stalled" }), ["重试"]);
  assert.deepEqual(noticeActions({ message: "今日额度已用完", kind: "model", status: 429, type: "daily_limit", permanent: true }), []);
});

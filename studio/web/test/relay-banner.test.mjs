// The composer's banner while the Tao-S1 relay's upstream breaker is open (ui/composer.js relayBanner): "Tao-S1 is
// unavailable until HH:MM", why, and a one-click switch of this conversation's model when the user has another one set
// up; nothing switches by itself. The DOM is studio/web/dev/test/setup.mjs's (linkedom).
import "./fixtures/setup.mjs";
import "../dev/test/setup.mjs";
import assert from "node:assert/strict";
import { test } from "node:test";

import { setLang } from "../js/core/i18n.js";
import { activeProvider } from "../js/core/providers.js";
import { clockTime, relayBanner } from "../js/ui/composer.js";
import { registerUiStrings } from "../js/ui/strings.js";

registerUiStrings();
const text = (el) => el.textContent.replace(/\s+/g, " ").trim();
const NOW = new Date(2026, 10, 4, 9, 0, 0).getTime(); // local time: the banner speaks the user's clock
const UNTIL = new Date(2026, 10, 4, 9, 30, 0);

/** The few parts of the App the banner reads: the relay's health, the settings, the model in use, chooseModel. */
function fakeApp({ relay, settings = {}, conversation = {} } = {}) {
  const chosen = [];
  return {
    chosen,
    state: { relay, settings: { provider: "tao", ...settings }, localDetected: [], conversation },
    provider() { return activeProvider(this.state.settings, { provider: this.state.conversation.provider }); },
    chooseModel(choice) {
      chosen.push(choice);
      this.state.conversation = { ...this.state.conversation, ...choice };
    },
  };
}
const paused = (reason = "upstream_quota") => ({ ok: false, reason, until: UNTIL.toISOString(), model: "Tao-S1" });

test("a spent upstream quota and a second provider with a stored key: the banner says until when, and offers the switch", () => {
  const app = fakeApp({ relay: paused(), settings: { keys: { deepseek: "sk-test" } } });
  const el = relayBanner(app, NOW);
  assert.ok(el, "a banner");
  assert.equal(el.getAttribute("role"), "status");
  assert.match(text(el), /Tao-S1 在 09:30 前不可用/);
  assert.match(text(el), /额度已用完/);
  const button = el.querySelector('[data-key="relay-switch"]');
  assert.ok(button, "the switch");
  assert.match(text(button), /本对话改用 deepseek-chat/);
  assert.deepEqual(app.chosen, [], "nothing switches by itself");
  button.dispatchEvent(new window.Event("click"));
  assert.deepEqual(app.chosen, [{ provider: "deepseek", model: "deepseek-chat" }]);
  // switched: this conversation no longer asks Tao-S1, so the banner has nothing to say
  assert.equal(relayBanner(app, NOW), null);
});

test("with no second provider the banner shows no switch, and says where to connect one", () => {
  const app = fakeApp({ relay: paused("upstream_error") });
  const el = relayBanner(app, NOW);
  assert.ok(el);
  assert.equal(el.querySelector('[data-key="relay-switch"]'), null);
  assert.match(text(el), /接连出错/);
  assert.match(text(el), /设置 → 模型/);
  setLang("en");
  try {
    const en = text(relayBanner(app, NOW));
    assert.match(en, /Tao-S1 is unavailable until 09:30/);
    assert.match(en, /kept failing/);
    assert.match(en, /connect your own model in Settings → Model/);
    const withKey = relayBanner(fakeApp({ relay: paused(), settings: { keys: { deepseek: "sk-test" } } }), NOW);
    assert.match(text(withKey.querySelector('[data-key="relay-switch"]')), /Use deepseek-chat in this conversation/);
  } finally {
    setLang("zh");
  }
});

test("no banner while the relay is well, once its time has passed, or for a conversation on another model", () => {
  assert.equal(relayBanner(fakeApp({ relay: { ok: true, model: "Tao-S1" } }), NOW), null);
  assert.equal(relayBanner(fakeApp({ relay: { ok: false } }), NOW), null, "off for another reason: Settings says so, not this banner");
  assert.equal(relayBanner(fakeApp({ relay: null }), NOW), null);
  assert.equal(relayBanner(fakeApp({ relay: paused() }), UNTIL.getTime()), null);
  assert.equal(relayBanner(fakeApp({ relay: paused(), settings: { keys: { deepseek: "k" } }, conversation: { provider: "deepseek" } }), NOW), null);
});

test("clockTime: HH:MM on the user's clock, with the date when it is not today", () => {
  assert.equal(clockTime(UNTIL.getTime(), NOW), "09:30");
  assert.equal(clockTime(new Date(2026, 10, 5, 8, 5).getTime(), NOW), "11-05 08:05");
});

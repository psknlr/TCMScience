// First run (DESIGN §3.9): three quiet, skippable steps — 语言 · 模型 (Tao-S1 is ready) · 计算 (the browser is ready;
// the runner is optional). Nothing here is required; closing it at any step counts as done.

import { PRESETS } from "../core/providers.js";
import { lang, setLang, t } from "../core/i18n.js";
import { fill, h } from "./dom.js";
import { icon } from "./icons.js";
import { openDialog } from "./overlay.js";
import { relayErrorLine } from "./panels.js";
import { button, copyButton, statusDot } from "./primitives.js";

const STEPS = ["language", "model", "compute"];

export function openOnboarding(app) {
  let step = 0;
  let d;
  const finish = () => { app.setSetting({ onboarded: true }); d.close(); };
  const back = button({ label: t("ui.onb.back"), variant: "ghost", onClick: () => { step = Math.max(0, step - 1); render(); } });
  const skip = button({ label: t("ui.onb.skip"), variant: "ghost", onClick: finish });
  const next = button({ label: t("ui.onb.next"), variant: "primary", onClick: () => { if (step < STEPS.length - 1) { step++; render(); } else finish(); } });
  const stepper = h("ol.onb__steps", { "aria-label": t("ui.onb.progress") });
  const body = h("div.onb__body", { "aria-live": "polite" });

  function render() {
    fill(stepper, ...STEPS.map((s, i) => h("li", { class: ["onb__step", i === step && "is-on", i < step && "is-done"], "aria-current": i === step ? "step" : null },
      h("span.onb__dot", i < step ? icon("check", { size: 10 }) : String(i + 1)), h("span", t(`ui.onb.step.${s}`)))));
    fill(body, content(STEPS[step]));
    back.hidden = step === 0;
    next.querySelector(".btn__label").textContent = step === STEPS.length - 1 ? t("ui.onb.start") : t("ui.onb.next");
    skip.querySelector(".btn__label").textContent = t("ui.onb.skip");
    back.querySelector(".btn__label").textContent = t("ui.onb.back");
  }

  function content(id) {
    if (id === "language") {
      const pick = (l) => h("button", { type: "button", class: ["onb__choice", lang() === l && "is-on"], "aria-pressed": String(lang() === l), lang: l === "zh" ? "zh-Hans" : "en", onClick: () => { setLang(l); render(); body.querySelector(".onb__choice.is-on")?.focus({ preventScroll: true }); } },
        h("span.onb__choice-title", l === "zh" ? "中文" : "English"),
        h("span.onb__choice-sub", l === "zh" ? "界面与说明使用中文" : "Interface and explanations in English"));
      return h("div.stack",
        h("h3.onb__title.serif", t("ui.onb.language_title")),
        h("p.onb__text", t("ui.onb.language_text")),
        h("div.onb__choices", pick("zh"), pick("en")));
    }
    if (id === "model") {
      const tao = PRESETS.find((p) => p.relay);
      const relay = app.state.relay;
      // the heading says what is true now: ready only when the relay answered; down → add a model of your own
      const state = !relay || relay.checking ? "checking" : relay.ok ? "ready" : "down";
      const manage = state === "down"
        ? button({ label: t("ui.model.manage"), variant: "secondary", iconAfter: "chevronRight", onClick: () => { finish(); app.navigate({ name: "settings", tab: "models" }); } })
        : h("a.panel__link", { href: "#/settings/models", onClick: finish }, t("ui.model.manage"), icon("chevronRight", { size: 14 }));
      return h("div.stack",
        h("h3.onb__title.serif", t(`ui.onb.model_title.${state}`)),
        h("div.onb__card",
          h("div.onb__card-head", h("span.provider-card__mark", h("span.wordmark__dot", "·"), "S1"), h("p.onb__card-name", "Tao-S1"),
            state === "checking" ? h("span.onb__card-state.muted", t("ui.model.relay_checking"))
              : h("span.onb__card-state", statusDot(relay.ok ? "ok" : "busy"), relay.ok ? t("ui.onb.ready") : t("ui.model.relay_down"))),
          h("p.onb__text", tao.help[lang()] || tao.help.zh),
          state === "down" && relay.error ? relayErrorLine(relay, { tag: "p.onb__text.muted" }) : null),
        h("p.onb__text.muted", state === "down" ? t("ui.onb.model_down") : t("ui.onb.model_more")),
        manage);
    }
    return h("div.stack",
      h("h3.onb__title.serif", t("ui.onb.compute_title")),
      h("div.onb__card",
        h("div.onb__card-head", icon("globe"), h("p.onb__card-name", t("ui.compute.browser_runtime")), h("span.onb__card-state", statusDot("ok"), t("ui.onb.ready"))),
        h("p.onb__text", t("ui.onb.browser_text"))),
      h("div.onb__card.onb__card--optional",
        h("div.onb__card-head", icon("laptop"), h("p.onb__card-name", t("ui.compute.runner_runtime")), h("span.onb__card-state.muted", t("ui.onb.optional"))),
        h("p.onb__text", t("ui.onb.runner_text")),
        h("div.engines__cmd", h("code", "tcmstudio serve"), copyButton("tcmstudio serve")),
        h("p.onb__text.muted", t("ui.onb.runner_after"))));
  }

  d = openDialog({
    title: t("ui.onb.title"), size: "md", className: "dialog--onboarding",
    body: h("div.onb", stepper, body),
    actions: [skip, h("span.spacer"), back, next],
    onClose: () => app.setSetting({ onboarded: true }),
    initialFocus: next,
  });
  render();
  // the relay check may finish while the dialog is open: the model step follows it
  const off = app.on("relay", () => { if (d.el?.isConnected && STEPS[step] === "model") render(); else if (!d.el?.isConnected) off(); });
}

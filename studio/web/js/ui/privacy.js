// What this site records, said where the visitor decides (studio/docs/TELEMETRY.md): the notice asked before the first
// question when the site takes conversation records (askConsent), and Settings → Privacy, where both choices can be
// changed at any time and what was recorded deleted (privacyTab). Declining, or closing the notice, is a refusal and
// changes nothing else: every feature works the same.

import { consentState, ownerHash, setAnalytics, setContentConsent, browserOptOut } from "../core/consent.js";
import { t } from "../core/i18n.js";
import { CONSENT_VERSION } from "../core/telemetry-contract.js";
import { formatDateTime, h } from "./dom.js";
import { confirmDialog, openDialog, toast } from "./overlay.js";
import { button, notice, section, switchControl } from "./primitives.js";

const days = (app, k, fallback) => app.telemetry?.config?.retention?.[k] ?? fallback;

/** The notice's paragraphs (the same words in the dialog and in Settings → Privacy). */
export function noticeBody(app) {
  return h("div.consent",
    h("p.consent__lede", t("ui.consent.lede")),
    h("dl.consent__list",
      h("dt", t("ui.consent.what")), h("dd", t("ui.consent.what_text")),
      h("dt", t("ui.consent.never")), h("dd", t("ui.consent.never_text")),
      h("dt", t("ui.consent.use")), h("dd", t("ui.consent.use_text", { days: days(app, "conversations", 90) }))),
    h("p.consent__note", t("ui.consent.change")),
    h("p.consent__note", t("ui.consent.usage")));
}

/** Record the visitor's answer (a usage event says only which, never who). */
function decide(app, agreed) {
  const before = consentState().content;
  setContentConsent(agreed);
  app.telemetry?.track("consent", { decision: agreed ? "granted" : before === true ? "withdrawn" : "declined", version: CONSENT_VERSION });
  if (!agreed) app.telemetry?.purgeRecords();
  app.emit("privacy", consentState());
}

/**
 * Ask once, before the first question, when the site takes conversation records and the visitor has not answered the
 * current notice. Resolves true (agreed) or false (declined, or closed). Recording stays off until "agree".
 */
export function askConsent(app) {
  return new Promise((resolve) => {
    let answer = false;
    const agree = h("button.btn.btn--md.btn--primary", { type: "button", "data-consent": "agree", onClick: () => { answer = true; d.close(true); } }, t("ui.consent.agree"));
    const decline = h("button.btn.btn--md.btn--secondary", { type: "button", "data-consent": "decline", onClick: () => d.close(false) }, t("ui.consent.decline"));
    const d = openDialog({
      title: t("ui.consent.title"), size: "md", className: "dialog--consent", body: noticeBody(app),
      // the two answers side by side, neither preselected; focus starts on the refusal, the choice that records nothing
      actions: [decline, agree], initialFocus: decline,
      onClose: () => {
        decide(app, answer);
        toast(answer ? t("ui.consent.agreed_toast") : t("ui.consent.declined_toast"), { tone: answer ? "ok" : "neutral" });
        resolve(answer);
      },
    });
  });
}

/** Settings → Privacy: the conversation-record choice with deletion, the usage-statistics switch, the security log. */
export function privacyTab(app, rerender) {
  const tel = app.telemetry;
  const state = consentState();
  const available = Boolean(tel?.conversationsAvailable);
  const here = Boolean(tel?.here);
  const status = state.content === true ? t("ui.privacy.state.agreed", { date: state.at ? formatDateTime(state.at) : "—" })
    : state.content === false ? t("ui.privacy.state.declined") : t("ui.privacy.state.unset");

  const ref = h("p.field__help.privacy__ref");
  if (state.hasKey) ownerHash().then((o) => { if (o) ref.textContent = t("ui.privacy.reference", { ref: o.slice(0, 12) }); });

  const remove = async ({ withdraw }) => {
    const ok = await confirmDialog({ title: t("ui.privacy.delete_confirm_title"), body: t("ui.privacy.delete_confirm_body"), confirmLabel: withdraw ? t("ui.privacy.withdraw_delete") : t("ui.privacy.delete"), danger: true });
    if (!ok) return;
    if (withdraw) decide(app, false);
    const res = await tel.deleteMine();
    if (res.ok) {
      tel.track("consent", { decision: "deleted", version: CONSENT_VERSION });
      toast(t("ui.privacy.deleted", { n: res.deleted }), { tone: "ok" });
    } else {
      toast(t("ui.privacy.delete_failed", { reason: res.reason === "network" ? t("ui.privacy.reason_network") : t("ui.privacy.reason_server") }), { tone: "warn", timeout: 6000 });
    }
    rerender();
  };

  const records = section({
    title: t("ui.privacy.records"),
    children: h("div.stack",
      !available ? notice({ tone: "info", icon: "info", body: t("ui.privacy.unavailable") }) : null,
      available ? switchControl({
        label: t("ui.privacy.records_switch"), description: t("ui.privacy.records_desc"), checked: state.content === true,
        onChange: (on) => { decide(app, on); toast(on ? t("ui.consent.agreed_toast") : t("ui.privacy.withdrawn_toast"), { tone: on ? "ok" : "neutral" }); rerender(); },
      }) : null,
      h("p.muted.small.privacy__state", { "data-state": state.content === true ? "agreed" : state.content === false ? "declined" : "unset" }, status),
      h("div.data-row.data-row--danger",
        h("div", h("p.field__label", t("ui.privacy.delete")), h("p.field__help", state.hasKey ? t("ui.privacy.delete_help") : t("ui.privacy.no_key")), ref),
        h("div.row",
          state.content === true ? button({ label: t("ui.privacy.withdraw_delete"), variant: "secondary", disabled: !here, onClick: () => remove({ withdraw: true }) }) : null,
          button({ label: t("ui.privacy.delete"), variant: "danger", icon: "trash", disabled: !state.hasKey || !here, attrs: { "data-key": "privacy-delete" }, onClick: () => remove({ withdraw: false }) }))),
      h("details.privacy__notice", h("summary", t("ui.privacy.what")), noticeBody(app))),
  });

  const blocked = browserOptOut();
  const usage = section({
    title: t("ui.privacy.usage"),
    children: h("div.stack",
      !tel?.config?.analytics ? notice({ tone: "info", icon: "info", body: t("ui.privacy.usage_unavailable") }) : null,
      blocked ? notice({ tone: "info", icon: "shieldCheck", body: t("ui.privacy.usage_browser_off") }) : null,
      switchControl({
        label: t("ui.privacy.usage_switch"), description: t("ui.privacy.usage_desc"), checked: state.analytics && !blocked, disabled: blocked,
        onChange: (on) => {
          setAnalytics(on);
          if (!on) tel?.discardEvents(); // what was waiting to be sent is not sent: nothing after the switch goes off
          toast(on ? t("ui.privacy.usage_on_toast") : t("ui.privacy.usage_off_toast"), { tone: "neutral" });
          rerender();
        },
      })),
  });

  const security = section({
    title: t("ui.privacy.security"),
    children: h("p.muted.small", t("ui.privacy.security_text", { days: days(app, "security", 30) })),
  });

  return h("div.stack.stack--xl.privacy", records, usage, security);
}

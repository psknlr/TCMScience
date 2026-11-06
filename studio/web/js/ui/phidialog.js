// Before a question leaves this computer for a model service (Tao-S1 through its relay, or a cloud API), what that
// service would receive and has not received yet is read with the rules that keep a conversation out of the records
// (core/sensitive.js phiReason: sensitiveReason, with age bands and record sections asked about as a topic set aside):
// the message, the project files the system prompt inlines (core/prompt.js), and the earlier messages of the branch it
// was never sent (the model was switched; one this check held counts whatever it was sent as). When one of them
// identifies a person or reads as a patient's case, nothing is sent until the person chooses, with the exact text of
// each choice in front of them:
//
//   redacted    the message with its identifiers replaced ([redacted:id_number] …): offered only when the message alone
//               tripped the check and redactText finds something in it. Names in running text stay, and an 11-digit
//               number such as a PubChem CID may be taken for a mobile number: the preview says both.
//   as_written  the text as typed; not asked again in this conversation for this model service
//   local       the text as typed, to a local model the person has set up (the conversation switches to it). Not
//               offered for a Continue: an answer goes on only on the model that wrote it (core/agent.js canContinue),
//               so the dialog says a Retry asks the local model afresh instead
//   edit        nothing is sent; the text stays in the box
//
// The choice, never the text, is kept on the question (message.phi) and counted in turn_end (core/turnrecord.js). A
// local model (at a loopback address, whatever its preset) is never asked about: nothing leaves the computer.

import { lang, t } from "../core/i18n.js";
import { INLINE_LIMIT, INLINE_TOTAL } from "../core/prompt.js";
import { activeProvider } from "../core/providers.js";
import { phiReason, redactText } from "../core/sensitive.js";
import { isLoopbackUrl, utf8Bytes } from "../core/util.js";
import { h } from "./dom.js";
import { inlinedInPrompt } from "./files.js";
import { openDialog } from "./overlay.js";
import { availableProviders } from "./panels.js";
import { disclosure } from "./primitives.js";

/**
 * Does the model run on this computer? Only at a loopback address: Tao-S1 never does, wherever its relay is served
 * from, and a preset marked local (Ollama, vLLM …) whose address was changed to another host (a GPU server on the LAN)
 * is a model outside this computer like any other.
 */
export function isLocalModel(provider) {
  if (!provider || provider.relay) return false;
  return isLoopbackUrl(provider.baseUrl || provider.base_url || "");
}

/** The knowledge files whose text the system prompt inlines, as core/prompt.js chooses them (size, then budget). */
export function inlinedKnowledge(knowledge = []) {
  let budget = INLINE_TOTAL;
  const out = [];
  for (const f of knowledge || []) {
    if (!f || typeof f.text !== "string") continue;
    const size = utf8Bytes(f.text);
    if (size < INLINE_LIMIT && size <= budget) {
      out.push(f);
      budget -= size;
    }
  }
  return out;
}

/** Attachments about to be added to the project, as knowledge records (with their text when the prompt inlines it). */
export async function pendingKnowledge(files = []) {
  const out = [];
  for (const f of files || []) {
    if (f?.id && f.sha256 && f.blob) continue; // stored already: among the project's files
    const file = f?.file || f;
    if (!file) continue;
    const rec = { name: file.name || "", bytes: file.size, type: file.type || "" };
    if (inlinedInPrompt(rec)) {
      try { rec.text = await file.text(); } catch { /* binary after all */ }
    }
    out.push(rec);
  }
  return out;
}

/**
 * The questions of `path` (a branch, root first) that model service `providerId` was never sent: those after the last
 * one it answered (an answer's request carries the branch up to its question), in any branch of `messages`.
 */
export function unseenBy(messages = [], path = [], providerId = "") {
  const answered = new Set(messages.filter((m) => m.role === "assistant" && m.provider === providerId).map((m) => m.parentId));
  let from = 0;
  path.forEach((m, i) => { if (m.role === "user" && answered.has(m.id)) from = i + 1; });
  return path.slice(from).filter((m) => m.role === "user" && typeof m.content === "string");
}

/**
 * What would identify a person among `text` (the message), `knowledge` (the project's files) and `history` (earlier
 * questions the service has not seen) → null, or {reason, message, files: [names], history: [texts], redacted}.
 * `redacted` is the message as redactText leaves it, when only the message tripped the check and something was
 * replaced; otherwise null (redacting the message would not keep the rest from being sent).
 */
export function phiCheck({ text = "", knowledge = [], history = [] } = {}) {
  const message = phiReason(text || "");
  const files = inlinedKnowledge(knowledge).map((f) => [f.name, phiReason(f.text)]).filter(([, why]) => why);
  // an earlier question held by this check identified a person as it was typed, whatever it was sent as (redacted, a
  // name in running text may still be there)
  const earlier = (history || []).map((m) => [m.content, m.phi?.reason || phiReason(m.content)]).filter(([, why]) => why);
  const reason = message || files[0]?.[1] || earlier[0]?.[1] || null;
  if (!reason) return null;
  const r = message && !files.length && !earlier.length ? redactText(text) : null;
  return { reason, message: Boolean(message), files: files.map(([name]) => name), history: earlier.map(([s]) => s), redacted: r?.hits ? r.text : null };
}

/**
 * The first local model the person has set up (as the model picker lists them) → {provider, model, label}, or null:
 * at the address the settings give it (isLocalModel), so that "only to this computer" is true of it.
 */
export function localModel(app) {
  const s = app.state.settings || {};
  for (const p of availableProviders(app)) {
    if (!(p.local || p.custom) || !isLocalModel(activeProvider(s, { provider: p.id }))) continue;
    const detected = (app.state.localDetected || []).find((x) => x.id === p.id && x.ok)?.models?.[0];
    const model = s.models?.[p.id] || detected || p.default_model || "";
    if (model) return { provider: p.id, model, label: p.label };
  }
  return null;
}

/** {phiAsWritten} for a conversation that now has "as written" for `providerId` too. */
export function rememberAsWritten(conv, providerId) {
  return { phiAsWritten: [...new Set([...(conv?.phiAsWritten || []), providerId])] };
}

/**
 * The check before a question (or a regenerated answer: `text` null) goes to the model in use. → {content, phi?,
 * remember?, local?} to go on with — `content` the text to send, `phi` {reason, choice[, remembered]} for the
 * question, `remember` the service "as written" now holds for in this conversation, `local` {provider, model} to
 * switch the conversation to — or null when the person goes back to edit. `continued`: the check before a Continue,
 * which goes on only on the model that wrote the answer: no local model is offered (the dialog names the one set up,
 * for a Retry), and `local` is never returned.
 */
export async function phiGate(app, { text = null, knowledge = [], path = [], continued = false } = {}) {
  const provider = app.provider();
  if (isLocalModel(provider)) return { content: text };
  const check = phiCheck({ text: text || "", knowledge, history: unseenBy(app.state.messages || [], path, provider.id) });
  if (!check) return { content: text };
  if ((app.state.conversation?.phiAsWritten || []).includes(provider.id)) {
    return { content: text, phi: { reason: check.reason, choice: "as_written", remembered: true } };
  }
  const set = localModel(app);
  const local = continued ? null : set;
  const choice = await askPhi({ check, text, provider, local, continued, retryLocal: continued ? set : null });
  const phi = { reason: check.reason, choice };
  if (choice === "redacted" && check.redacted !== null) return { content: check.redacted, phi };
  if (choice === "as_written") return { content: text, phi, remember: provider.id };
  if (choice === "local" && local) return { content: text, phi, local: { provider: local.provider, model: local.model } };
  return null;
}

const modelName = (p) => (p.relay ? "Tao-S1" : p.model && p.model !== p.label ? `${p.label} · ${p.model}` : p.label || p.model || "");
const list = (names) => names.join(lang() === "zh" ? "、" : ", ");

/**
 * The dialog: why, where it was found, and each choice with the exact text it sends. Resolves "redacted" |
 * "as_written" | "local" | "edit" (closing it, or Esc, is "edit": nothing is sent). `continued`: before a Continue
 * (what is sent again is the branch with the part of the answer already written); `retryLocal`: the local model set
 * up, which a Continue cannot go on with and a Retry can ask.
 */
export function askPhi({ check, text = null, provider, local = null, continued = false, retryLocal = null }) {
  return new Promise((resolve) => {
    let choice = "edit";
    const name = modelName(provider);
    const localName = local ? `${local.label} · ${local.model}` : "";
    const retryName = retryLocal ? `${retryLocal.label} · ${retryLocal.model}` : "";
    const pick = (c) => () => { choice = c; d.close(c); };
    // the choice that sends nothing has the focus; one that removes or keeps the text here is the primary
    const primary = check.redacted !== null ? "redacted" : local ? "local" : null;
    const btn = (c, label) => h("button", { type: "button", class: ["btn", "btn--md", c === primary ? "btn--primary" : "btn--secondary"], "data-phi": c, onClick: pick(c) }, label);
    // a regenerated answer (no new message) has nothing to go back to: the same choice is a plain cancel
    const edit = btn("edit", t(text === null ? "ui.action.cancel" : "ui.phi.edit"));
    const actions = [
      edit,
      local ? btn("local", t("ui.phi.local", { name: localName })) : null,
      btn("as_written", t("ui.phi.as_written")),
      check.redacted !== null ? btn("redacted", t("ui.phi.redacted")) : null,
    ].filter(Boolean);
    const d = openDialog({
      title: t("ui.phi.title"), size: "lg", className: "dialog--phi", body: phiBody({ check, text, name, localName, retryName, continued, relay: Boolean(provider.relay) }), actions,
      initialFocus: edit, onClose: () => resolve(choice),
    });
  });
}

function phiBody({ check, text, name, localName, retryName = "", continued = false, relay }) {
  const where = [
    check.message ? t("ui.phi.in_message") : null,
    check.files.length ? t("ui.phi.in_files", { files: list(check.files) }) : null,
    check.history.length ? t("ui.phi.in_history", { n: check.history.length, name }) : null,
  ].filter(Boolean);
  const preview = (s) => h("pre.code-block.code-block--wrap.phi__preview", s);
  const note = (s) => h("p.phi__note", s);
  const choice = (c, title, ...body) => h("section.phi__choice", { "data-choice": c }, h("h3.phi__choice-title", title), ...body);
  // what goes along with the message whichever way it is sent: the files and earlier questions that tripped the check
  const along = [
    check.files.length ? note(t("ui.phi.with_files", { files: list(check.files) })) : null,
    check.history.length ? disclosure({
      summary: t("ui.phi.with_history", { n: check.history.length }),
      content: () => h("div.phi__history", check.history.map((s) => preview(s))),
    }) : null,
  ];
  return h("div.phi",
    h("p.phi__lede", t(`ui.phi.reason.${check.reason}`), lang() === "zh" ? "" : " ", t(relay ? "ui.phi.leaves_relay" : "ui.phi.leaves", { name })),
    h("p.phi__where", t("ui.phi.found", { where: list(where) })),
    check.redacted !== null ? choice("redacted", t("ui.phi.redacted"),
      preview(check.redacted), note(t("ui.phi.redacted_names")), note(t("ui.phi.redacted_digits"))) : null,
    choice("as_written", t("ui.phi.as_written"),
      text ? preview(text) : note(t(continued ? "ui.phi.no_message_continue" : "ui.phi.no_message")), ...along, note(t("ui.phi.as_written_note", { name }))),
    localName ? choice("local", t("ui.phi.local", { name: localName }), note(t("ui.phi.local_note", { name: localName }))) : null,
    text === null
      ? choice("edit", t("ui.action.cancel"), note(t("ui.phi.cancel_note")), retryName ? note(t("ui.phi.continue_local", { name: retryName })) : null)
      : choice("edit", t("ui.phi.edit"), note(t("ui.phi.edit_note"))));
}

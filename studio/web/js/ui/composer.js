// The composer (DESIGN §4.5, §9.4): an auto-growing textarea that never sends while an IME is composing, attachments
// hashed on entry (they stay in this browser), the model chip, the compute chip, web access, and send / stop.

import { lang, t } from "../core/i18n.js";
import { fill, formatBytes, h, shortcutLabel, uid } from "./dom.js";
import { icon } from "./icons.js";
import { openDialog, toast } from "./overlay.js";
import { computeChipLabel, computePanel, modelChipLabel, modelPanel, openComputePopover, openModelPopover, runnerTone } from "./panels.js";
import { iconButton, progressBar, statusDot, switchControl } from "./primitives.js";

const BROWSER_SIZE_GUARD = 200 * 1024 * 1024;

/**
 * mountComposer(app, {variant: "dock"|"hero", placeholder, autofocus}) → {el, setText, focus, destroy}.
 * The composer sends through app.send; while a turn runs in this conversation the send button is a stop button.
 */
export function mountComposer(app, { variant = "dock", autofocus = false, initialText = "" } = {}) {
  const attachments = []; // {id, file, status: "hashing"|"ready"|"error", progress, sha256}
  const ta = h("textarea#composer-input.composer__input", {
    rows: "1", "aria-label": t("ui.composer.label"), placeholder: t("ui.composer.placeholder"), spellcheck: "true",
    enterkeyhint: "send", autocomplete: "off",
  });
  ta.value = initialText;
  const pills = h("div.composer__attachments", { hidden: true, role: "list", "aria-label": t("ui.composer.attachments") });
  const fileInput = h("input", { type: "file", multiple: true, hidden: true, "aria-hidden": "true", tabindex: "-1" });
  const sendBtn = h("button.composer__send", { type: "button" });
  const chips = h("div.composer__chips");
  const drop = h("div.composer__drop", { hidden: true, "aria-hidden": "true" },
    icon("upload", { size: 20 }), h("p.composer__drop-title", t("ui.composer.drop")), h("p.composer__drop-note", t("ui.composer.drop_note")));
  const box = h("div.composer__box",
    pills,
    ta,
    h("div.composer__bar",
      h("div.composer__tools",
        iconButton({ icon: "paperclip", label: t("ui.composer.attach"), kbd: "mod+u", onClick: () => fileInput.click() }),
        chips),
      sendBtn),
    drop);
  const el = h("div", { class: ["composer", `composer--${variant}`] }, box, fileInput,
    h("p.composer__foot", t("ui.composer.foot")));

  // ------------------------------------------------------------------------------------------------ text

  const grow = () => {
    ta.style.height = "auto";
    const max = Math.round(window.innerHeight * (variant === "hero" ? 0.32 : 0.4));
    ta.style.height = `${Math.min(ta.scrollHeight, max)}px`;
    ta.style.overflowY = ta.scrollHeight > max ? "auto" : "hidden";
    renderSend();
  };
  ta.addEventListener("input", grow);
  ta.addEventListener("keydown", (e) => {
    // the most common bug in Chinese chat UIs: Enter that confirms an IME candidate must not send (DESIGN §9.4)
    if (e.isComposing || e.keyCode === 229) return;
    if (e.key === "Enter" && !e.shiftKey && !e.altKey && !e.ctrlKey && !e.metaKey) {
      e.preventDefault();
      submit();
      return;
    }
    if (e.key === "ArrowUp" && !ta.value && !e.shiftKey && !e.altKey) {
      if (app.state.route.name === "conversation") {
        e.preventDefault();
        app.emit("edit-last");
      }
    }
  });
  ta.addEventListener("paste", (e) => {
    const files = [...(e.clipboardData?.files || [])];
    if (files.length) {
      e.preventDefault();
      addFiles(files);
    }
  });

  function streamingHere() {
    const turn = app.state.turn;
    return Boolean(turn && (!app.state.conversation || turn.conversationId === app.state.conversation.id));
  }

  async function submit() {
    if (streamingHere()) return;
    const text = ta.value.trim();
    if (!text) return;
    if (attachments.some((a) => a.status === "hashing")) {
      toast(t("ui.composer.wait_hash"));
      return;
    }
    const files = attachments.filter((a) => a.status === "ready").map((a) => a.file);
    const ok = await app.send(text, { files });
    if (ok !== false) {
      ta.value = "";
      attachments.length = 0;
      renderPills();
      grow();
    }
  }

  function renderSend() {
    const streaming = streamingHere();
    const empty = !ta.value.trim();
    sendBtn.className = ["composer__send", streaming ? "is-stop" : "", empty && !streaming ? "is-empty" : ""].filter(Boolean).join(" ");
    fill(sendBtn, icon(streaming ? "stop" : "arrowUp", { size: 16, stroke: streaming ? 2.5 : 2 }));
    sendBtn.setAttribute("aria-label", streaming ? t("ui.composer.stop") : t("ui.composer.send"));
    sendBtn.dataset.tip = streaming ? t("ui.composer.stop_tip") : `${t("ui.composer.send")}  ${shortcutLabel("enter")}`;
    sendBtn.disabled = !streaming && empty;
    ta.placeholder = streaming ? t("ui.composer.placeholder_streaming") : variant === "hero" ? t("ui.composer.placeholder_hero") : t("ui.composer.placeholder");
  }
  sendBtn.addEventListener("click", () => (streamingHere() ? app.stop() : submit()));

  // ------------------------------------------------------------------------------------------------ chips

  // the chips are built once per layout and updated in place, so a popover anchored to one survives updates
  let chipMode = null;
  let chipEls = null;
  function renderChips() {
    const mobile = app.state.layout === "mobile";
    const mode = mobile ? "mobile" : "desktop";
    if (mode !== chipMode) {
      chipMode = mode;
      if (mobile) {
        const label = h("span.composer-chip__label");
        chipEls = { mobileLabel: label };
        fill(chips, h("button.composer-chip", {
          type: "button", "aria-label": t("ui.composer.options"), "data-tip": t("ui.composer.options"),
          onClick: () => openOptionsSheet(app),
        }, icon("sliders", { size: 14 }), label));
      } else {
        const modelLabel = h("span.composer-chip__label");
        const computeLabel = h("span.composer-chip__label");
        const dotSlot = h("span.composer-chip__dot");
        const webIcon = h("span.composer-chip__icon");
        const webLabel = h("span.composer-chip__label");
        const model = h("button.composer-chip", { type: "button", "aria-haspopup": "dialog", "data-tip": t("ui.model.chip_tip"), onClick: (e) => openModelPopover(app, e.currentTarget) },
          icon("sparkles", { size: 14 }), modelLabel, icon("chevronDown", { size: 12, className: "composer-chip__chev" }));
        const compute = h("button.composer-chip", { type: "button", "aria-haspopup": "dialog", "data-tip": t("ui.compute.chip_tip"), onClick: (e) => openComputePopover(app, e.currentTarget) },
          dotSlot, computeLabel, icon("chevronDown", { size: 12, className: "composer-chip__chev" }));
        const web = h("button.composer-chip.composer-chip--toggle", { type: "button", onClick: () => app.setWeb(!app.webOn()) }, webIcon, webLabel);
        chipEls = { model, modelLabel, compute, computeLabel, dotSlot, web, webIcon, webLabel };
        fill(chips, model, compute, web);
      }
    }
    if (mobile) {
      chipEls.mobileLabel.textContent = `${modelChipLabel(app)} · ${computeChipLabel(app)}`;
      return;
    }
    const on = app.webOn();
    chipEls.modelLabel.textContent = modelChipLabel(app);
    chipEls.computeLabel.textContent = computeChipLabel(app);
    chipEls.compute.setAttribute("aria-label", `${t("ui.compute.title")}：${computeChipLabel(app)}`);
    fill(chipEls.dotSlot, statusDot(runnerTone(app)));
    chipEls.web.classList.toggle("is-on", on);
    chipEls.web.setAttribute("aria-pressed", String(on));
    chipEls.web.dataset.tip = on ? t("ui.web.on_tip") : t("ui.web.off_tip");
    fill(chipEls.webIcon, icon(on ? "globe" : "wifiOff", { size: 14 }));
    chipEls.webLabel.textContent = on ? t("ui.web.on") : t("ui.web.off");
  }

  // ------------------------------------------------------------------------------------------------ attachments

  fileInput.addEventListener("change", () => {
    addFiles([...fileInput.files]);
    fileInput.value = "";
  });

  async function addFiles(files) {
    for (const file of files) {
      const a = { id: uid("att"), file, status: "hashing", progress: 0, sha256: "" };
      attachments.push(a);
      renderPills();
      hashWithProgress(file, (p) => { a.progress = p; updatePill(a); })
        .then((sha) => { a.sha256 = sha; a.status = "ready"; updatePill(a); })
        .catch(() => { a.status = "error"; updatePill(a); });
    }
  }

  function renderPills() {
    pills.hidden = !attachments.length;
    fill(pills, ...attachments.map((a) => pill(a)));
  }

  function updatePill(a) {
    const old = pills.querySelector(`[data-att="${a.id}"]`);
    if (old) old.replaceWith(pill(a));
  }

  function pill(a) {
    const big = a.file.size > BROWSER_SIZE_GUARD;
    return h("div", { class: ["att", `is-${a.status}`, big && "is-big"], role: "listitem", "data-att": a.id, title: a.sha256 ? `sha256 ${a.sha256}` : a.file.name },
      icon(/^image\//.test(a.file.type) ? "image" : /json/.test(a.file.type) ? "fileJson" : /csv|tsv|sheet|excel/.test(a.file.type + a.file.name) ? "fileTable" : "fileText", { size: 16, className: "att__icon" }),
      h("div.att__text",
        h("p.att__name", a.file.name),
        h("p.att__meta",
          h("span.num", formatBytes(a.file.size)),
          a.status === "hashing" ? [h("span.dot-sep", "·"), h("span", t("ui.composer.hashing", { p: Math.round(a.progress * 100) }))] : null,
          a.status === "ready" ? [h("span.dot-sep", "·"), h("span.mono", `sha256:${a.sha256.slice(0, 8)}…`)] : null,
          a.status === "error" ? [h("span.dot-sep", "·"), h("span", t("ui.composer.hash_failed"))] : null),
        a.status === "hashing" ? progressBar({ value: a.progress || null, className: "att__progress" }) : null,
        big ? h("p.att__warn", t("ui.composer.big_file")) : null),
      iconButton({ icon: "x", size: "sm", label: t("ui.composer.remove", { name: a.file.name }), onClick: () => {
        const i = attachments.indexOf(a);
        if (i >= 0) attachments.splice(i, 1);
        renderPills();
      } }));
  }

  // drag and drop anywhere over the page
  let dragDepth = 0;
  const onDragEnter = (e) => {
    if (![...(e.dataTransfer?.types || [])].includes("Files")) return;
    dragDepth++;
    drop.hidden = false;
    el.classList.add("is-dragging");
  };
  const onDragLeave = () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (!dragDepth) { drop.hidden = true; el.classList.remove("is-dragging"); }
  };
  const onDragOver = (e) => { if ([...(e.dataTransfer?.types || [])].includes("Files")) e.preventDefault(); };
  const onDrop = (e) => {
    if (!e.dataTransfer?.files?.length) return;
    e.preventDefault();
    dragDepth = 0;
    drop.hidden = true;
    el.classList.remove("is-dragging");
    addFiles([...e.dataTransfer.files]);
  };
  window.addEventListener("dragenter", onDragEnter);
  window.addEventListener("dragleave", onDragLeave);
  window.addEventListener("dragover", onDragOver);
  window.addEventListener("drop", onDrop);

  const offs = [
    app.on("turn", (e) => { if (e.type === "start" || e.type === "end") renderSend(); }),
    app.on("model", renderChips), app.on("runtime", renderChips), app.on("settings", renderChips), app.on("web", renderChips),
    app.on("project", renderChips), app.on("layout", renderChips), app.on("conversation", renderChips),
    app.on("attach", () => fileInput.click()),
  ];
  renderChips();
  renderSend();
  requestAnimationFrame(grow);
  if (autofocus && app.state.layout !== "mobile") requestAnimationFrame(() => ta.focus({ preventScroll: true }));

  return {
    el,
    focus: () => ta.focus(),
    addFiles,
    setText(text, { send = false } = {}) {
      ta.value = text;
      grow();
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
      if (send) submit();
    },
    destroy() {
      offs.forEach((off) => off());
      window.removeEventListener("dragenter", onDragEnter);
      window.removeEventListener("dragleave", onDragLeave);
      window.removeEventListener("dragover", onDragOver);
      window.removeEventListener("drop", onDrop);
    },
  };
}

/** On phones the mode/model/compute chips collapse into one sheet (DESIGN §8.3). */
function openOptionsSheet(app) {
  const d = openDialog({
    title: t("ui.composer.options"), size: "sm", className: "dialog--sheet",
    body: () => h("div.stack.stack--lg",
      modelPanel(app, { onPick: () => d.close(), onNavigate: () => d.close() }),
      h("hr"),
      computePanel(app, { compact: true, onNavigate: () => d.close() })),
  });
}

/** SHA-256 with read progress: the file is read in chunks (progress), then digested by Web Crypto. */
export async function hashWithProgress(file, onProgress) {
  const CHUNK = 4 * 1024 * 1024;
  const total = file.size;
  if (total <= CHUNK) {
    onProgress?.(0.5);
    const buf = await file.arrayBuffer();
    const d = await crypto.subtle.digest("SHA-256", buf);
    onProgress?.(1);
    return hex(d);
  }
  const bytes = new Uint8Array(total);
  for (let off = 0; off < total; off += CHUNK) {
    const part = new Uint8Array(await file.slice(off, off + CHUNK).arrayBuffer());
    bytes.set(part, off);
    onProgress?.(Math.min(0.95, (off + part.length) / total));
    await new Promise((r) => setTimeout(r, 0));
  }
  const d = await crypto.subtle.digest("SHA-256", bytes);
  onProgress?.(1);
  return hex(d);
}

function hex(buf) {
  return [...new Uint8Array(buf)].map((x) => x.toString(16).padStart(2, "0")).join("");
}

export { lang };

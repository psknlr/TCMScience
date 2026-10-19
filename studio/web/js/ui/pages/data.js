// Settings → 数据 Data: the registered datasets of the data hub, as the runtime that holds them says (CONTRACTS §4
// /api/datasets; tcmdb.datasets and tcmdb.status in the browser). Per dataset: its source, release (or 未注明版本),
// licence (未声明许可 when it states none) with its reuse class, commercial use and what is known about
// redistribution ("unknown — downloading is not permission to redistribute" without a reviewed record), access, size,
// download status (files present of those expected, the missing ones), how each kept file was verified (checksum,
// size, mismatch, unverified; files with a reviewed checksum; the CI live-download record when the answer carries one),
// its readiness (the six states), the one next step, and the catalog entries that read it.
//
// Nothing here computes or downloads on its own. The runner's list is read when the runner is connected (read-only
// GETs); a dataset's files are read when its row is opened. The browser's own hub (per project) is read only on a
// button press, because reading it loads the Python runtime (Pyodide), which an ordinary visit never starts. An
// acquisition is the runner's tcmdb.fetch job: asked for only after a confirmation that names the hosts it reaches and
// the size expected, gated by the runner's own network switch, and followed live (Job.progress: stage, bytes, files,
// how they were verified) with a cancel.
//
// Every address here comes from the runtime's answer (a dataset's homepage, a CI record's run): it is a link only when
// it is an http(s) address (core/governance.js safeUrl), else plain text.

import { safeUrl } from "../../core/governance.js";
import { t } from "../../core/i18n.js";
import { fill, formatBytes, h } from "../dom.js";
import { icon } from "../icons.js";
import { confirmDialog, toast } from "../overlay.js";
import { button, chip, disclosure, keyValue, notice, section, spinner } from "../primitives.js";
import { JobTracker } from "../jobs.js";

/** The six readiness states, in order (tcmstudio.dispatch READINESS): only "built" answers queries. */
export const DATA_READINESS = ["catalogued", "live", "supported", "partial", "downloaded", "built"];
const READY_CHIP = {
  catalogued: { tone: "slate", icon: "circleDashed", dashed: true },
  live: { tone: "navy", icon: "globe" },
  supported: { tone: "slate", icon: "download" },
  partial: { tone: "ochre", icon: "circleHalf" },
  downloaded: { tone: "navy", icon: "hardDrive" },
  built: { tone: "jade", icon: "check" },
};
const VERIFY = ["checksum", "size", "mismatch", "unverified"];
const REDISTRIBUTION_UNKNOWN = "unknown — downloading is not permission to redistribute";
const ACTIVE = ["queued", "running"];
/** A manifest being read (st.manifests[key] while its GET is out): a second press does not send another. */
const LOADING = Object.freeze({ loading: true });

/** The readiness chip of a dataset (icon + words; jade only for built, the state that answers queries). */
export function readinessChip(state) {
  const s = DATA_READINESS.includes(state) ? state : "catalogued";
  const c = READY_CHIP[s];
  return chip({ label: t(`ui.data.readiness.${s}`), tone: c.tone, icon: c.icon, dashed: Boolean(c.dashed), className: "data-ready", attrs: { "data-readiness": s } });
}

/**
 * The catalog entries that read a dataset: those that name it in `data`, take it as their dataset argument
 * (data_from, with the key among its values), or are its connector; `any` counts the entries that read any built
 * store (data "*"). → {named: [ids], any: n}.
 */
export function downstreamOf(catalog, key) {
  const named = [];
  let any = 0;
  for (const e of catalog?.entries || []) {
    const data = Array.isArray(e.data) ? e.data.map(String) : [];
    const param = e.data_from && typeof e.data_from === "object" ? e.data_from.param : null;
    const values = param ? e.parameters?.properties?.[param]?.enum : null;
    if (data.includes(key) || (param && Array.isArray(values) && values.includes(key)) || e.connector?.key === key) named.push(e.id);
    else if (data.includes("*") || (param && !Array.isArray(values))) any += 1;
  }
  return { named, any };
}

/**
 * One row's facts from what a runtime answered: a /api/datasets row (its `local`: what the runner holds; + its manifest
 * once read), or the browser's tcmdb.datasets row (+ its tcmdb.status row). → {key, name, homepage, version, license, licence_class,
 * commercial_use, redistribution, access, bytes, hosts, files: {present, expected, missing}|null, verification:
 * {checksum, size, mismatch, unverified}|null, reviewed: {n, of}|null, live_record, readiness, next_step, acquirable}.
 */
export function datasetFacts(row, { manifest = null, status = null } = {}) {
  const r = row && typeof row === "object" ? row : {};
  // a runner's list row says what it holds (local: the same fields as tcmdb.status); the manifest, once read, says more
  if (!status && r.local && typeof r.local === "object") status = r.local;
  const out = {
    key: String(r.key || r.dataset || ""), name: r.name || r.key || "", homepage: manifest?.homepage || r.homepage || null,
    version: (manifest?.version ?? r.version) || null,
    license: (manifest?.license ?? r.license) || "",
    licence_class: manifest?.licence_class || r.licence_class || "unknown",
    commercial_use: manifest?.commercial_use || r.commercial_use || "unknown",
    redistribution: manifest?.redistribution || r.redistribution || REDISTRIBUTION_UNKNOWN,
    access: manifest?.access || r.access || "download",
    bytes: Number.isFinite(r.bytes_default) ? r.bytes_default : Number.isFinite(r.expected_bytes_default) ? r.expected_bytes_default : null,
    hosts: Array.isArray(manifest?.hosts) ? manifest.hosts : Array.isArray(r.hosts) ? r.hosts : [],
    files: null, verification: null, reviewed: null,
    live_record: manifest?.live_record || r.live_record || null,
    readiness: r.readiness?.state || "catalogued", next_step: r.readiness?.next_step || "",
    acquirable: r.acquirable ?? (r.access === "download"),
  };
  if (manifest && Array.isArray(manifest.files)) {
    const wanted = manifest.files.filter((f) => f && !f.optional);
    out.files = { present: wanted.filter((f) => f.local?.present).length, expected: wanted.length, missing: wanted.filter((f) => !f.local?.present).map((f) => f.name) };
    out.verification = countVerification(manifest.files.filter((f) => f?.local?.present).map((f) => f.local.verification));
    out.reviewed = { n: manifest.files.filter((f) => f?.reviewed_checksum).length, of: manifest.files.length };
  } else if (status) {
    out.files = { present: Number(status.files_present) || 0, expected: Number(status.files_default) || 0, missing: Array.isArray(status.missing_default) ? status.missing_default : [] };
    out.verification = status.verification && typeof status.verification === "object" ? countVerification(Object.values(status.verification)) : (Number(status.files_present) ? null : countVerification([]));
    if (Array.isArray(r.files)) out.reviewed = { n: r.files.filter((f) => f?.checksum).length, of: r.files.length };
  } else if (Array.isArray(r.files)) {
    out.reviewed = { n: r.files.filter((f) => f?.checksum).length, of: r.files.length };
  }
  return out;
}

function countVerification(list) {
  const out = Object.fromEntries(VERIFY.map((k) => [k, 0]));
  for (const v of list) out[VERIFY.includes(v) ? v : "unverified"] += 1;
  return out;
}

/** The licence line: the licence (or 未声明许可), its reuse class, commercial use. */
function licenceView(f) {
  return h("span.data-licence",
    f.license ? h("span", f.license) : h("span.data-licence__none", t("ui.source.unlicensed")),
    h("span.muted", ` · ${t(`ui.data.class.${["open", "share-alike", "non-commercial", "no-derivatives"].includes(f.licence_class) ? f.licence_class : "unknown"}`)}`),
    h("span.muted", ` · ${t(`ui.data.commercial.${["allowed", "forbidden"].includes(f.commercial_use) ? f.commercial_use : "unknown"}`)}`));
}

/** What is known about redistribution; unknown is said as unknown (downloading is not permission). */
function redistributionView(f) {
  if (!f.redistribution || f.redistribution === REDISTRIBUTION_UNKNOWN || /^unknown\b/i.test(f.redistribution)) return h("span.data-redist", t("ui.data.redistribution_unknown"));
  return h("span.data-redist", { lang: "en" }, f.redistribution);
}

function verificationView(f) {
  const parts = [];
  if (f.verification) parts.push(t("ui.data.verified_counts", f.verification));
  if (f.reviewed) parts.push(t("ui.data.reviewed", { n: f.reviewed.n, of: f.reviewed.of }));
  if (f.live_record && typeof f.live_record === "object") {
    const lr = f.live_record;
    const run = safeUrl(lr.run);
    const words = t("ui.data.live_record", { pass: lr.files_pass ?? "?", of: lr.files_recorded ?? lr.files ?? "?", at: lr.at || "—" });
    parts.push(run ? h("a", { href: run, target: "_blank", rel: "noopener noreferrer" }, words) : words);
  }
  if (!parts.length) return h("span.muted", t("ui.data.not_read"));
  return h("span", parts.flatMap((p, i) => (i ? [h("span.dot-sep", " · "), p] : [p])));
}

function filesView(f) {
  if (!f.files) return h("span.muted", t("ui.data.not_read"));
  return h("span", t("ui.data.files_present", { present: f.files.present, expected: f.files.expected }),
    f.files.missing.length ? h("span.muted", " · ", t("ui.data.missing"), " ", h("code", f.files.missing.slice(0, 12).join(", ")), f.files.missing.length > 12 ? ` +${f.files.missing.length - 12}` : "") : null);
}

/**
 * Settings → Data. app: the App; opts.confirm (tests) replaces the confirmation dialog. Returns the element; a
 * re-render (the settings page's) mounts it again from what was already read (kept on app.state.dataTab).
 */
export function dataTab(app, rerender, { jobs, confirm = confirmDialog } = {}) {
  const st = (app.state.dataTab ||= { runner: null, runnerError: null, loading: false, browser: null, browserError: null, browserLoading: false, manifests: {}, open: {}, filter: "", acquisitions: {} });
  st.open ||= {};
  const runner = app.runtimes.runner;
  const ready = app.state.runner.status === "ready" && Boolean(runner?.datasets);
  // the settings page's tracker; on its own (a test, the gallery) one kept with the tab's state
  const tracker = jobs || (st.tracker ||= new JobTracker(app));

  // a runner that went away (or another one): what was read from it is forgotten, and read again once connected
  const runnerUrl = ready ? runner.url || "" : null;
  if (st.runnerUrl !== runnerUrl) { st.runner = null; st.runnerError = null; st.manifests = {}; st.open = {}; st.acquisitions = {}; st.runnerUrl = runnerUrl; }

  // the runner's list: read once per connection (GET /api/datasets is read-only), with its acquisitions
  if (ready && !st.runner && !st.loading && !st.runnerError) {
    st.loading = true;
    Promise.all([runner.datasets.list(), runner.datasets.acquisitions?.({}).catch(() => null)]).then(([list, acq]) => {
      st.runner = list || { datasets: [] };
      for (const a of acq?.acquisitions || []) {
        const cur = st.acquisitions[a.dataset];
        if (!cur || ACTIVE.includes(a.state) || !ACTIVE.includes(cur.state)) st.acquisitions[a.dataset] = { id: a.id, state: a.state, job: a.job || null };
      }
    }).catch((err) => { st.runnerError = err?.message || String(err); })
      .finally(() => { st.loading = false; rerender(); });
  }

  const blocks = [];
  blocks.push(notice({ tone: "info", icon: "database", body: t("ui.data.lede") }));

  // the runner's data directory
  const runnerBody = [];
  if (!ready) runnerBody.push(h("p.muted.small", t("ui.data.needs_runner"), " ", h("a", { href: "#/settings/compute" }, t("ui.data.connect"))));
  else if (st.runnerError) runnerBody.push(notice({ tone: "warn", body: st.runnerError }));
  else if (!st.runner) runnerBody.push(h("p.muted.small", spinner({ size: 12 }), " ", t("ui.files.loading")));
  else {
    runnerBody.push(h("div.row", button({ label: t("ui.data.refresh"), icon: "refresh", size: "sm", variant: "ghost", onClick: () => { st.runner = null; st.manifests = {}; rerender(); } })));
    if (st.runner.network && st.runner.network.enabled === false) runnerBody.push(notice({ tone: "neutral", icon: "wifiOff", body: t("ui.data.runner_network_off") }));
    runnerBody.push(datasetList(app, st, (st.runner.datasets || []).map((r) => ({ row: r, where: "runner" })), { tracker, confirm, rerender }));
  }
  blocks.push(section({ title: t("ui.data.runner_title"), children: runnerBody }));

  // this browser's hub (per project), only on a press: reading it loads the Python runtime
  const project = app.state.project;
  const browserBody = [];
  if (st.browser) {
    browserBody.push(h("p.muted.small", t("ui.data.browser_scope", { name: st.browser.projectName || t("ui.data.no_project") })));
    const statuses = new Map((st.browser.status?.datasets || []).map((s) => [s.dataset, s]));
    browserBody.push(datasetList(app, st, (st.browser.datasets?.datasets || []).map((r) => ({ row: r, where: "browser", status: statuses.get(r.key) })), { tracker, confirm, rerender }));
  } else {
    if (st.browserError) browserBody.push(notice({ tone: "warn", body: st.browserError }));
    browserBody.push(h("p.muted.small", t("ui.data.browser_note")));
    browserBody.push(h("div.row", button({
      label: st.browserLoading ? t("ui.data.browser_loading") : t("ui.data.browser_check"), icon: "globe", size: "sm",
      disabled: st.browserLoading || !app.runtimes.browser?.call || app.state.browser.status === "offline",
      attrs: { "data-key": "data-browser-check" },
      onClick: () => checkBrowser(app, st, project, rerender),
    })));
  }
  blocks.push(section({ title: t("ui.data.browser_title"), children: browserBody }));
  return h("div.stack.stack--xl.data-tab", blocks);
}

/** The browser's tcmdb.datasets and tcmdb.status, read on the person's press (this loads Pyodide). */
async function checkBrowser(app, st, project, rerender) {
  const rt = app.runtimes.browser;
  if (!rt?.call) return;
  st.browserLoading = true;
  st.browserError = null;
  rerender();
  try {
    const ctx = { project_id: project?.id || null, network: false };
    const datasets = await rt.call("tcmdb.datasets", {}, ctx);
    const status = await rt.call("tcmdb.status", {}, ctx);
    if (datasets?.status !== "succeeded") throw new Error(datasets?.error?.message || datasets?.summary || t("ui.data.browser_failed"));
    st.browser = { datasets: datasets.result || { datasets: [] }, status: status?.status === "succeeded" ? status.result : null, projectName: project?.name || null };
    // what this browser's hub has built: the catalog's readiness reads it (core/catalog.js entryState ctx.built)
    app.state.browserData = { built: Array.isArray(st.browser.status?.built) ? st.browser.status.built : [], projectId: project?.id || null };
    app.emit("evidence", { data: true });
  } catch (err) {
    st.browserError = err?.message || String(err);
  } finally {
    st.browserLoading = false;
    rerender();
  }
}

function datasetList(app, st, items, ctx) {
  if (!items.length) return h("p.muted.small", t("ui.data.none"));
  const counts = Object.fromEntries(DATA_READINESS.map((s) => [s, 0]));
  for (const it of items) counts[DATA_READINESS.includes(it.row.readiness?.state) ? it.row.readiness.state : "catalogued"] += 1;
  const shown = st.filter ? items.filter((it) => (it.row.readiness?.state || "catalogued") === st.filter) : items;
  const chips = h("div.filter-chips", { role: "group", "aria-label": t("ui.data.filter") },
    [["", t("ui.data.filter_all"), items.length], ...DATA_READINESS.filter((s) => counts[s]).map((s) => [s, t(`ui.data.readiness.${s}`), counts[s]])].map(([id, label, n]) => h("button", {
      type: "button", class: ["filter-chip", st.filter === id && "is-on"], "aria-pressed": String(st.filter === id),
      onClick: () => { st.filter = id; ctx.rerender(); },
    }, h("span", label), h("span.filter-chip__n.num", String(n)))));
  return h("div.stack",
    chips,
    h("p.muted.small", t("ui.data.count", { n: shown.length, total: items.length })),
    h("ul.dataset-list", { role: "list" }, shown.map((it) => datasetRow(app, st, it, ctx))));
}

function datasetRow(app, st, { row, where, status }, { tracker, confirm, rerender }) {
  const key = String(row.key || "");
  const manifest = where === "runner" ? st.manifests[key] || null : null;
  const loaded = manifest && !manifest.error && !manifest.loading ? manifest : null;
  const f = datasetFacts(row, { manifest: loaded, status });
  const down = downstreamOf(app.catalog, key);
  const acq = where === "runner" ? st.acquisitions[key] : null;
  const runner = app.runtimes.runner;
  const moving = ["supported", "partial"].includes(f.readiness);
  const canAcquire = where === "runner" && f.acquirable && f.access === "download" && moving && !(acq && ACTIVE.includes(acq.state));

  const facts = keyValue([
    [t("ui.data.release"), f.version ? h("span.mono", f.version) : h("span.muted", t("ui.data.no_version"))],
    [t("ui.data.licence"), licenceView(f)],
    [t("ui.data.redistribution"), redistributionView(f)],
    [t("ui.data.access"), t(`ui.data.access_v.${["download", "manual", "live"].includes(f.access) ? f.access : "download"}`)],
    [t("ui.data.size"), f.bytes ? formatBytes(f.bytes) : h("span.muted", t("ui.data.size_unknown"))],
    [t("ui.data.download_status"), filesView(f)],
    [t("ui.data.verification"), verificationView(f)],
    [t("ui.data.next_step"), f.next_step ? h("code.kv__code", { lang: "en" }, f.next_step) : "—"],
    [t("ui.data.downstream"), down.named.length || down.any
      ? h("span", down.named.length ? h("code", down.named.slice(0, 12).join(", ")) : null, down.named.length > 12 ? ` +${down.named.length - 12}` : null,
        down.any ? h("span.muted", `${down.named.length ? " · " : ""}${t("ui.data.downstream_any", { n: down.any })}`) : null)
      : h("span.muted", t("ui.data.downstream_none"))],
  ], { className: "dataset-row__facts" });

  // the reviewed files and how each kept one was verified: read when the row is opened (GET …/manifest, once; a
  // refresh reads it again). Whether it is open is the person's (st.open), kept across re-renders.
  const loadManifest = () => {
    if (st.manifests[key]) return; // read, being read, or failed (a refresh clears it)
    st.manifests[key] = LOADING;
    runner.datasets.manifest(key).then((m) => { st.manifests[key] = m || { error: t("ui.data.no_files") }; rerender(); })
      .catch((err) => { st.manifests[key] = { error: err?.message || String(err) }; rerender(); });
  };
  const open = where === "runner" && Boolean(st.open[key]);
  if (open) loadManifest();
  const filesBlock = where === "runner" ? disclosure({
    summary: t("ui.data.files_title"), className: "data-files",
    open,
    onToggle: (v) => { st.open[key] = v; if (v) loadManifest(); },
    content: () => manifest?.error ? notice({ tone: "warn", body: manifest.error }) : loaded ? manifestFiles(loaded) : h("p.muted.small", spinner({ size: 12 }), " ", t("ui.files.loading")),
  }) : null;
  // its own key: the settings page re-opens disclosures by key after a re-render (else by their summary, which every
  // row shares)
  if (filesBlock) filesBlock.setAttribute("data-key", `data-files:${key}`);

  const actions = [];
  if (canAcquire) {
    actions.push(button({
      label: t("ui.data.acquire"), icon: "download", size: "sm", attrs: { "data-acquire": key },
      onClick: () => acquire(app, st, f, { confirm, rerender }),
    }));
  }
  // the acquisition's job card, followed live by the tracker (which knows its state better than the list read once)
  const job = acq?.job ? tracker.node(acq.job) : null;
  return h("li.dataset-row", { "data-dataset": key, "data-where": where },
    h("div.dataset-row__head",
      h("div.dataset-row__title",
        safeUrl(f.homepage) ? h("a.dataset-row__name", { href: safeUrl(f.homepage), target: "_blank", rel: "noopener noreferrer" }, f.name, icon("external", { size: 12 })) : h("span.dataset-row__name", f.name),
        h("code.dataset-row__key", key)),
      readinessChip(f.readiness)),
    facts,
    filesBlock,
    actions.length ? h("div.dataset-row__actions", actions) : null,
    job);
}

function manifestFiles(m) {
  const files = Array.isArray(m.files) ? m.files : [];
  if (!files.length) return h("p.muted.small", t("ui.data.no_files"));
  return h("div.tablewrap", { tabindex: "0", role: "region", "aria-label": t("ui.data.files_title") }, h("table.data-files__table",
    h("thead", h("tr", h("th", t("ui.data.col.file")), h("th", t("ui.data.col.host")), h("th", t("ui.data.col.size")), h("th", t("ui.data.col.checksum")), h("th", t("ui.data.col.local")))),
    h("tbody", files.map((x) => h("tr",
      h("td", h("code", x.name), x.optional ? h("span.muted", ` (${t("ui.data.optional")})`) : null),
      h("td", h("code", x.host || "—")),
      h("td.num", Number.isFinite(x.expected_bytes) ? formatBytes(x.expected_bytes) : t("ui.data.size_unknown")),
      h("td", x.reviewed_checksum ? t("ui.data.reviewed_yes") : h("span.muted", t("ui.data.reviewed_no"))),
      h("td", x.local?.present ? t(`ui.data.verify.${VERIFY.includes(x.local.verification) ? x.local.verification : "unverified"}`) : Number.isFinite(x.local?.part_bytes) ? t("ui.data.partial_bytes", { bytes: formatBytes(x.local.part_bytes) }) : h("span.muted", t("ui.data.absent"))))))));
}

/**
 * Ask, then acquire: a confirmation that names the hosts it reaches and the size expected (and that the runner's
 * network switch and its size gate still apply), then POST /api/datasets/acquisitions {dataset, project_id}. A refusal
 * (network off, already being acquired, not downloadable) is said as the runner said it.
 */
async function acquire(app, st, f, { confirm, rerender }) {
  const body = h("div.stack",
    h("p.dialog__text", t("ui.data.acquire_body", { name: f.name, size: f.bytes ? formatBytes(f.bytes) : t("ui.data.size_unknown") })),
    h("p.dialog__text", t("ui.data.acquire_hosts")), h("ul.data-hosts", { role: "list" }, (f.hosts.length ? f.hosts : ["—"]).map((x) => h("li", h("code", x)))),
    h("p.dialog__text.muted", t("ui.data.acquire_note")),
    h("p.dialog__text.muted", t("ui.data.acquire_licence")));
  const ok = await confirm({ title: t("ui.data.acquire_title", { name: f.name }), body, confirmLabel: t("ui.data.acquire") });
  if (!ok) return;
  const runner = app.runtimes.runner;
  try {
    const res = await runner.datasets.acquire({ dataset: f.key, project_id: app.state.project?.id || undefined });
    const a = res?.acquisition || res;
    if (a?.id) st.acquisitions[f.key] = { id: a.id, state: a.state, job: a.job || { id: a.id, kind: "tcmdb.fetch", state: a.state || "queued", params: { dataset: f.key } } };
    toast(t("ui.data.acquire_started", { name: f.name }), { tone: "ok" });
  } catch (err) {
    toast(err?.message || String(err), { tone: "warn", timeout: 6000 });
  }
  rerender();
}


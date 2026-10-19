// Settings → Data → "Research data in this browser": the files the research snapshots are built from, kept in this
// origin's private storage (OPFS) for the browser's own research jobs (studio/docs/BROWSER_JOBS.md). Per file: its
// project, size, licence and what is known about redistribution (the registry's words; "unknown — downloading is not
// permission to redistribute" without a reviewed record), and whether it is here and how it was verified. Three acts,
// each the person's: import files they obtained elsewhere (held to the same reviewed checksums when the snapshots are
// built), download and build the snapshots in this browser (a research.snapshot job, after a confirmation that names
// the hosts and the size), and check this browser against the research jobs' known answer. Nothing starts on its own;
// reading the list reads only this browser's storage.

import { t } from "../../core/i18n.js";
import { formatBytes, h } from "../dom.js";
import { icon } from "../icons.js";
import { confirmDialog, toast } from "../overlay.js";
import { button, chip, keyValue, notice, section, spinner } from "../primitives.js";

const REDISTRIBUTION_UNKNOWN = "unknown — downloading is not permission to redistribute";

/** A file row's status chip: here and verified (checksum or size), here unverified, partly downloaded, or not here. */
function fileChip(f) {
  if (f.present) {
    const v = f.meta?.verified || null;
    if (v === "checksum") return chip({ label: t("ui.bresearch.verified_checksum"), tone: "jade", icon: "check" });
    if (v === "size") return chip({ label: t("ui.bresearch.verified_size"), tone: "navy", icon: "check" });
    return chip({ label: t("ui.bresearch.here_unverified"), tone: "ochre", icon: "circleHalf" });
  }
  if (f.partial_bytes) return chip({ label: t("ui.bresearch.partial", { done: formatBytes(f.partial_bytes) }), tone: "ochre", icon: "download" });
  return chip({ label: t("ui.bresearch.missing"), tone: "slate", icon: "circleDashed", dashed: true });
}

export function browserResearchSection(app, { tracker, confirm = confirmDialog, rerender, st }) {
  const jobs = app.runtimes?.jobs;
  const body = [];
  if (!jobs || jobs.status !== "ready") {
    body.push(notice({ tone: "neutral", icon: "hardDrive", body: t("ui.bresearch.unavailable", { reason: jobs?.reason || t("ui.bresearch.no_service") }) }));
    return section({ title: t("ui.bresearch.title"), children: body });
  }
  const service = jobs.service;
  const datasets = service.datasets;
  const box = (st.bresearch ||= { plan: null, error: null, loading: false, estimate: null, jobs: [] });

  if (!box.plan && !box.loading && !box.error) {
    box.loading = true;
    Promise.all([datasets.plan(), service.store.estimate?.().catch(() => null), service.list?.({ limit: 10 }).catch(() => [])])
      .then(([plan, estimate, list]) => { box.plan = plan; box.estimate = estimate; box.jobs = (list || []).filter((j) => ["research.snapshot", "research.run", "browser.selftest"].includes(j.kind)); })
      .catch((err) => { box.error = err?.message || String(err); })
      .finally(() => { box.loading = false; rerender(); });
  }
  body.push(h("p.muted.small", t("ui.bresearch.lede")));
  if (box.error) {
    body.push(notice({ tone: "warn", body: box.error }));
    return section({ title: t("ui.bresearch.title"), children: body });
  }
  if (!box.plan) {
    body.push(h("p.muted.small", spinner({ size: 12 }), " ", t("ui.files.loading")));
    return section({ title: t("ui.bresearch.title"), children: body });
  }
  const plan = box.plan;
  const est = box.estimate;
  const built = app.state.browserJobs?.built || null;
  body.push(keyValue([
    [t("ui.bresearch.files"), t("ui.bresearch.files_v", { here: plan.files.filter((f) => f.present).length, all: plan.files.length })],
    plan.download_bytes ? [t("ui.bresearch.to_download"), formatBytes(plan.download_bytes)] : null,
    est && Number.isFinite(est.usage) ? [t("ui.bresearch.storage"), t("ui.bresearch.storage_v", { used: formatBytes(est.usage), quota: Number.isFinite(est.quota) ? formatBytes(est.quota) : "?", kept: est.persisted ? t("ui.bresearch.persisted") : t("ui.bresearch.may_be_evicted") })] : null,
    [t("ui.bresearch.snapshots"), built ? t("ui.bresearch.snapshots_built", { at: String(built.at || "").slice(0, 16).replace("T", " "), job: built.job_id }) : t("ui.bresearch.snapshots_none")],
    [t("ui.bresearch.workers"), t("ui.bresearch.workers_v", { n: jobs.pool?.size ?? 1, why: jobs.pool?.reason || "" })],
  ]));

  const rows = h("ul.file-list", { role: "list" }, plan.files.map((f) => h("li.file-row", { "data-file": f.name },
    icon("file", { size: 16, className: "file-row__icon" }),
    h("div.file-row__main",
      h("p.file-row__name.mono", f.name),
      h("p.file-row__meta", [f.project, f.bytes ? formatBytes(f.bytes) : t("ui.bresearch.size_unreviewed"), f.license].filter(Boolean).join(" · ")),
      h("p.file-row__meta.muted", { lang: "en" }, `${t("ui.data.redistribution")}: ${f.redistribution || REDISTRIBUTION_UNKNOWN}`)),
    fileChip(f))));
  body.push(rows);

  // the person's acts
  const picker = h("input", { type: "file", multiple: true, hidden: true, "aria-hidden": "true" });
  picker.addEventListener("change", async () => {
    const files = new Map();
    const known = new Set(plan.files.map((f) => f.name));
    const skipped = [];
    for (const file of picker.files || []) (known.has(file.name) ? files.set(file.name, file) : skipped.push(file.name));
    picker.value = "";
    if (!files.size) { toast(t("ui.bresearch.import_none"), { tone: "warn" }); return; }
    toast(t("ui.bresearch.importing", { n: files.size }));
    try {
      const out = await datasets.acquire({ names: [...files.keys()], files });
      const bad = out.filter((r) => !["PASS", "UNVERIFIED"].includes(r.status));
      toast(bad.length ? t("ui.bresearch.import_failed", { n: bad.length, first: `${bad[0].file}: ${bad[0].reason}` }) : t("ui.bresearch.imported", { n: out.length }), { tone: bad.length ? "warn" : "ok" });
    } catch (err) { toast(err?.message || String(err), { tone: "warn" }); }
    if (skipped.length) toast(t("ui.bresearch.import_skipped", { names: skipped.slice(0, 5).join(", ") }), { tone: "warn" });
    box.plan = null;
    rerender();
  });
  const build = async () => {
    const missing = plan.files.filter((f) => !f.present);
    const ok = await confirm({
      title: t("ui.bresearch.build_title"),
      body: missing.length ? t("ui.bresearch.build_body_download", { n: missing.length, size: plan.download_bytes ? formatBytes(plan.download_bytes) : "?", hosts: (plan.hosts || []).join(", ") }) : t("ui.bresearch.build_body_local"),
      confirmLabel: t("ui.bresearch.build"),
    });
    if (!ok) return;
    try {
      const job = await service.submit("research.snapshot", {}, { project_id: app.state.project?.id || null });
      box.jobs = [job, ...box.jobs];
      rerender();
    } catch (err) { toast(err?.message || String(err), { tone: "warn" }); }
  };
  const selftest = async () => {
    try {
      const job = await service.submit("browser.selftest", {}, { project_id: null });
      box.jobs = [job, ...box.jobs];
      rerender();
    } catch (err) { toast(err?.message || String(err), { tone: "warn" }); }
  };
  body.push(h("div.row",
    button({ label: t("ui.bresearch.import"), icon: "upload", size: "sm", onClick: () => picker.click() }),
    button({ label: t("ui.bresearch.build"), icon: "workflow", size: "sm", onClick: build }),
    button({ label: t("ui.bresearch.check"), icon: "fingerprint", size: "sm", variant: "ghost", onClick: selftest }),
    est && !est.persisted ? button({ label: t("ui.bresearch.persist"), icon: "hardDrive", size: "sm", variant: "ghost", onClick: async () => {
      const kept = await service.store.persist?.().catch(() => false);
      toast(kept ? t("ui.bresearch.persist_ok") : t("ui.bresearch.persist_refused"), { tone: kept ? "ok" : "warn" });
      box.estimate = null; box.plan = null; rerender();
    } }) : null,
    button({ label: t("ui.data.refresh"), icon: "refresh", size: "sm", variant: "ghost", onClick: () => { box.plan = null; rerender(); } }),
    picker));
  if (box.jobs.length && tracker) body.push(h("div.stack", box.jobs.slice(0, 6).map((j) => tracker.node(j, { fresh: true }))));
  return section({ title: t("ui.bresearch.title"), children: body });
}


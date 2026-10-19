// The network-pharmacology research jobs, run in this browser (studio/docs/BROWSER_JOBS.md). They are the runner's
// research.snapshot and research.run (studio/runner/src/tcmstudio/kinds.py) with the same Python doing the work:
// tcmstudio.browser_jobs calls bioagent.sources.pipeline.build_research_snapshots and bioagent.research.run_research
// inside a job worker (../engines/science.worker.js), so a browser run and a runner run of the same inputs write the
// same tables. What differs is only where the files live (this origin's private file system) and how the analysis
// is isolated: a browser worker cannot start processes, so the governed analysis runs in-process through the same
// PSH broker, and the artifact says so (provenance.execution.*.isolation "in_process").
//
// research.snapshot  plan → acquire → build → persist: the raw files (downloaded, relayed or imported, each held to its
//                    reviewed checksum by the build's integrity stage), parsed into snapshots with a fresh ledger
// research.run       prepare → research → verify: the sixteen-stage workflow and its nine exports, then the exports
//                    recomputed (np_exports verify) and checked like the runner checks them

const GROUP = "research";
const DATA = ["tcmstudio", "datasets", GROUP];
const RAW = [...DATA, "raw"];
const SNAPSHOTS = [...DATA, "snapshots"];
const SNAPSHOTS_NEXT = [...DATA, "snapshots.next"];
const REPORT = [...DATA, "snapshot_report.json"];
/** Pyodide packages the snapshot store needs (bioagent.sources.snapshot reads and writes Parquet). */
const PACKAGES = ["pyarrow"];

export const RESEARCH_EXPORTS = ["compounds.tsv", "targets.tsv", "disease_targets.tsv", "enrichment.tsv", "network.tsv",
  "provenance.json", "run_manifest.json", "limitations.md", "report.html"];

function refusal(type, message, need, hint = "") {
  const e = new Error(message);
  e.type = type;
  e.need = need;
  e.hint = hint;
  return e;
}

function work(job) {
  return `/work/${job.id}`;
}

/** What a stage worker call returned, or a thrown error carrying Python's own type and message. */
function unwrap(text, what) {
  const doc = typeof text === "string" ? JSON.parse(text) : text;
  if (doc && doc.ok === false) {
    const e = new Error(`${what}: ${doc.error?.message || "failed"}`);
    e.type = doc.error?.type || "runtime_error";
    throw e;
  }
  return doc && "result" in doc ? doc.result : doc;
}

async function storageRefusal(store, needBytes) {
  if (!store) return refusal("unavailable", "This browser has no Origin Private File System for the job's files.", "storage", "Use a current Chrome, Edge, Firefox or Safari, outside a private window.");
  if (!needBytes) return null;
  const est = await store.estimate?.().catch(() => null);
  if (est && Number.isFinite(est.quota) && Number.isFinite(est.usage) && est.quota - est.usage < needBytes) {
    return refusal("unavailable", `The job needs about ${(needBytes / 1e6).toFixed(0)} MB of browser storage; ${((est.quota - est.usage) / 1e6).toFixed(0)} MB is free.`, "storage", "Free some space, or allow the site to store more.");
  }
  return null;
}

/** research.snapshot: the raw files, verified, parsed into the research snapshots and their ledger. */
export const researchSnapshot = {
  kind: "research.snapshot",
  title: { zh: "构建研究快照（浏览器）", en: "Build research snapshots (browser)" },
  stages: [
    { id: "plan", title: { zh: "核对所需文件", en: "Check the files needed" } },
    { id: "acquire", title: { zh: "获取原始文件", en: "Obtain the raw files" } },
    { id: "build", title: { zh: "校验并构建快照", en: "Verify and build the snapshots" } },
    { id: "persist", title: { zh: "保存快照与账本", en: "Keep the snapshots and ledger" } },
  ],

  async preflight(params, svc) {
    if (!svc.store) throw await storageRefusal(null);
    if (!svc.datasets) throw refusal("unavailable", "The dataset registry (runtime/datasets.json) is not available in this build.", "dataset");
    const plan = await svc.datasets.plan();
    const refused = await storageRefusal(svc.store, (plan.download_bytes || 0) * 1.1);
    if (refused) throw refused;
    if (params?.transport && !["auto", "direct", "relay"].includes(params.transport)) {
      throw refusal("bad_arguments", `transport must be auto, direct or relay, not ${params.transport}`, "arguments");
    }
  },

  async run(ctx) {
    const { params, datasets, pool } = ctx;
    let plan = null;
    await ctx.stage("plan", async () => {
      plan = await datasets.plan();
      ctx.log("info", `${plan.files.length} files needed; ${plan.missing.length} not in this browser yet`);
      return { detail: `${plan.files.length} files, ${plan.missing.length} to obtain` };
    });

    await ctx.stage("acquire", async (s) => {
      if (!plan.missing.length) return { detail: "every file is already here", reused: true };
      const rows = await datasets.acquire({
        names: plan.missing, transport: params.transport || "auto", signal: ctx.signal,
        onProgress: (p) => ctx.progress(0.05 + 0.45 * (p.fraction ?? 0), p.message || `${p.file || ""}`),
      });
      s.record({ files: rows });
      const bad = rows.filter((r) => !["PASS", "UNVERIFIED"].includes(r.status));
      if (bad.length) {
        const e = new Error(`${bad.length} file(s) could not be obtained: ${bad.map((r) => `${r.file} (${r.reason || r.status})`).join("; ")}`);
        e.type = "unavailable";
        e.hint = "Download them where their hosts can be reached and import them on the Data page; they are held to the same checksums.";
        throw e;
      }
      return { detail: `${rows.length} file(s) obtained` };
    });

    const names = (await datasets.plan()).files.filter((f) => f.present).map((f) => f.name);
    const lease = await pool.lease({ packages: PACKAGES });
    try {
      const root = work(ctx.job);
      await ctx.stage("build", async (s) => {
        const m = await lease.call("mount", { root: `${root}/raw`, dir: RAW, files: names }, { signal: ctx.signal });
        ctx.log("info", `${m.files.length} raw files mounted (${(m.bytes / 1e6).toFixed(0)} MB, ${m.mode})`);
        const report = unwrap(await lease.call("run", {
          fn: "build_snapshots",
          args: {
            raw_dir: `${root}/raw`, snapshot_root: `${root}/snapshots`, ledger: `${root}/snapshots/ledger.jsonl`,
            sources: params.sources || undefined, network: params.network !== false,
            report_path: `${root}/snapshot_report.json`,
          },
        }, { signal: ctx.signal, onEvent: (ev) => onPythonEvent(ctx, ev, 0.5, 0.4) }), "build_snapshots");
        s.record({ report_status: report.status, worker: lease.id });
        if (report.status === "FAIL" || report.status === "BLOCKED" || report.status === "SKIPPED") {
          const last = (report.stages || []).at(-1) || {};
          const e = new Error(`the build stopped at ${last.stage || "?"}: ${last.detail || report.next_step || report.status}`);
          e.type = "failed";
          e.hint = report.next_step || "";
          throw e;
        }
        ctx.state.report = report;
        return { detail: `${Object.keys(report.snapshots || {}).length} snapshots (${report.status})`, worker: lease.id };
      });

      await ctx.stage("persist", async () => {
        const snaps = await lease.call("persist", { from: `${root}/snapshots`, dir: SNAPSHOTS_NEXT, clean: true }, { signal: ctx.signal });
        const rep = await lease.call("persist", { from: root, dir: [...ctx.dir, "out"], clean: false, only: ["snapshot_report.json"] }, { signal: ctx.signal });
        await ctx.store.replaceDir(SNAPSHOTS_NEXT, SNAPSHOTS);
        const report = rep.files.find((f) => f.path.join("/") === "snapshot_report.json");
        if (report) {
          await ctx.store.copyFile([...ctx.dir, "out", "snapshot_report.json"], REPORT);
          ctx.artefact({ name: "snapshot_report.json", path: ["out", "snapshot_report.json"], bytes: report.bytes, sha256: report.sha256 });
        }
        const ledger = snaps.files.find((f) => f.path.join("/") === "ledger.jsonl");
        if (ledger) ctx.artefact({ name: "ledger.jsonl", path: [...SNAPSHOTS, "ledger.jsonl"], bytes: ledger.bytes, sha256: ledger.sha256, shared: true });
        return { detail: `${snaps.files.length} files kept in this browser`, worker: lease.id };
      });
    } finally {
      await lease.call("free", { paths: [work(ctx.job)] }).catch(() => {});
      lease.release();
    }
    const report = ctx.state.report;
    return {
      result: { status: report.status, snapshots: report.snapshots, reproduce: report.reproduce, report: "snapshot_report.json" },
    };
  },
};

/** research.run: the sixteen-stage network-pharmacology workflow over the snapshots this browser built. */
export const researchRun = {
  kind: "research.run",
  title: { zh: "网络药理学研究（浏览器）", en: "Network-pharmacology research (browser)" },
  stages: [
    { id: "prepare", title: { zh: "载入快照", en: "Load the snapshots" } },
    { id: "research", title: { zh: "十六阶段研究流程", en: "The sixteen-stage workflow" } },
    { id: "verify", title: { zh: "重新计算并核对", en: "Recompute and check" } },
  ],

  async preflight(params, svc) {
    if (!svc.store) throw await storageRefusal(null);
    if (!params || typeof params.question !== "string" || !params.question.trim()) {
      throw refusal("bad_arguments", "a research question is required", "arguments");
    }
    if (!(await svc.store.exists([...SNAPSHOTS, "ledger.jsonl"]))) {
      throw refusal("unavailable", "No research snapshots in this browser yet: the research job reads them through their ledger.", "dataset",
        "Build them first (research.snapshot: the NPASS, CMAUP and LOTUS files plus STRING and Reactome), or connect a runner that has them.");
    }
  },

  async run(ctx) {
    const { params, pool } = ctx;
    const root = work(ctx.job);
    const lease = await pool.lease({ packages: PACKAGES });
    let summary = null;
    try {
      await ctx.stage("prepare", async () => {
        const r = await lease.call("restore", { dir: SNAPSHOTS, to: `${root}/snapshots` }, { signal: ctx.signal });
        return { detail: `${r.files} snapshot files (${(r.bytes / 1e6).toFixed(1)} MB)`, worker: lease.id };
      });

      await ctx.stage("research", async (s) => {
        const out = unwrap(await lease.call("run", {
          fn: "run_research",
          args: {
            question: params.question, formula: params.formula, disease: params.disease,
            activity: params.activity, optional: params.optional, background: params.background, hits: params.hits,
            permutations: params.permutations, accept_review: Boolean(params.accept_review),
            snapshot_root: `${root}/snapshots`, ledger: `${root}/snapshots/ledger.jsonl`,
            state_dir: `${root}/state`, out_dir: `${root}/out`,
          },
        }, { signal: ctx.signal, onEvent: (ev) => onPythonEvent(ctx, ev, 0.1, 0.6) }), "run_research");
        s.record({ exit: out.exit, failed: out.failed, worker: lease.id });
        if (out.exit === 2) {
          const e = new Error(`refused: ${out.refused || "the question or a stage was refused"}`);
          e.type = "refused";
          throw e;
        }
        summary = out.summary;
        const kept = await lease.call("persist", { from: `${root}/out`, dir: [...ctx.dir, "out"], clean: true }, { signal: ctx.signal });
        await lease.call("persist", { from: `${root}/state`, dir: [...ctx.dir, "state"], clean: true }, { signal: ctx.signal });
        for (const f of kept.files) ctx.artefact({ name: f.path.join("/"), path: ["out", ...f.path], bytes: f.bytes, sha256: f.sha256 });
        if (out.exit === 3) {
          const e = new Error(`a workflow stage failed its own check: ${(out.failed || []).join("; ")}`);
          e.type = "failed";
          throw e;
        }
        return { detail: `${summary?.released ? "released" : "not released"}; ${summary?.stages?.length ?? 0} stages`, worker: lease.id };
      });

      await ctx.stage("verify", async (s) => {
        const v = unwrap(await lease.call("run", {
          fn: "verify_research",
          args: { out_dir: `${root}/out`, snapshot_root: `${root}/snapshots`, ledger: `${root}/snapshots/ledger.jsonl` },
        }, { signal: ctx.signal, onEvent: (ev) => onPythonEvent(ctx, ev, 0.7, 0.25) }), "verify_research");
        s.record({ status: v.status, worker: lease.id });
        ctx.outcome({ checks: v.checks || [], problems: v.problems || [] });
        if (v.status !== "PASS") {
          const e = new Error(`the exports did not recompute: ${(v.problems || []).slice(0, 3).join("; ") || v.status}`);
          e.type = "failed";
          throw e;
        }
        return { detail: "every digest, p and q recomputed; the analysis re-ran to the same tables", worker: lease.id, verified: true };
      });
    } finally {
      await lease.call("free", { paths: [root] }).catch(() => {});
      lease.release();
    }
    return { result: summary };
  },
};

function onPythonEvent(ctx, ev, base, span) {
  if (!ev) return;
  if (ev.kind === "log" && ev.text) ctx.log(ev.level || "info", String(ev.text));
  else if (ev.kind === "progress") ctx.progress(base + span * Math.max(0, Math.min(1, Number(ev.fraction) || 0)), ev.message || "");
}

export const HANDLERS = [researchSnapshot, researchRun];

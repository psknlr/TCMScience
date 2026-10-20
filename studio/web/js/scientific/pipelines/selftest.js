// "Check this browser": the research jobs' known answer, run as a browser job (tcmstudio.browser_selftest). A job worker
// builds the planted world (bioagent.research.planted: synthetic snapshots whose right answer is known) and studies it
// the way research.run does — the snapshot store, the governed analysis with its seeded permutation null, the rebuttals,
// the nine exports and their recomputation — and its five tables are compared with the answer native CPython committed.
// It downloads nothing and takes seconds once the runtime is loaded. Not a catalog entry and not offered to the model:
// a person runs it (Settings → Compute), and its record says what this browser was shown to compute.

export const SELFTEST_KIND = "browser.selftest";

export const browserSelfTest = {
  kind: SELFTEST_KIND,
  title: { zh: "检查本浏览器（已知答案）", en: "Check this browser (known answer)" },
  internal: true,
  stages: [{ id: "study", title: { zh: "研究模拟世界并核对答案", en: "Study the planted world and compare" } }],

  async preflight(params, svc) {
    if (!svc.pool) {
      const e = new Error("This browser cannot start job workers.");
      e.type = "unavailable";
      e.need = "job_service";
      throw e;
    }
  },

  async run(ctx) {
    const lease = await ctx.pool.lease({ packages: ["pyarrow"] });
    let report = null;
    try {
      await ctx.stage("study", async (s) => {
        const text = await lease.call("run", { fn: "self_test", args: { work_dir: `/work/${ctx.job.id}` } }, { signal: ctx.signal });
        const doc = typeof text === "string" ? JSON.parse(text) : text;
        if (doc?.ok === false) {
          const e = new Error(`the self-test did not run: ${doc.error?.message || "failed"}`);
          e.type = doc.error?.type || "runtime_error";
          throw e;
        }
        report = doc?.result ?? doc;
        s.record?.({ status: report?.status, worker: lease.id });
        ctx.outcome?.({
          checks: Object.entries(report?.tables || {}).map(([name, t]) => `${name}: ${t.status}`),
          problems: report?.status === "PASS" ? [] : Object.entries(report?.tables || {}).filter(([, t]) => !["identical", "within_tolerance"].includes(t.status)).map(([name, t]) => `${name}: ${t.status}`),
        });
        if (report?.status !== "PASS") {
          const e = new Error(`this browser did not reproduce the known answer (${Object.entries(report?.tables || {}).map(([n, t]) => `${n} ${t.status}`).join(", ")}; verify ${report?.verify?.status})`);
          e.type = "failed";
          throw e;
        }
        return { detail: Object.values(report.tables).every((t) => t.status === "identical") ? "the five tables are the native bytes" : "the five tables agree within 1e-12", worker: lease.id, verified: true };
      });
    } finally {
      await lease.call("free", { paths: [`/work/${ctx.job.id}`] }).catch(() => {});
      lease.release();
    }
    return { result: report };
  },
};

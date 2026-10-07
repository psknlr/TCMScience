// Demo data for design QA: one project with conversations whose tool results are the fixture envelopes (real
// TCMScience runs, dev/fixtures/make_fixtures.py). seedDemo(store) writes them into a Store (use an in-memory store,
// openStore({memory: true}), unless you mean to add them to this browser's data).

export const FIXTURE_NAMES = [
  "envelope_safety", "envelope_normalize", "envelope_network", "envelope_network_unattested", "envelope_claims", "envelope_compatibility",
  "envelope_herb", "envelope_revcomp", "envelope_egfr", "envelope_needs_runner", "envelope_network_off", "envelope_denied", "envelope_job",
  "jobs", "approvals", "runner_info",
];

export async function loadFixtures(base = new URL("./fixtures/", import.meta.url)) {
  const out = {};
  // the built catalog, when this site has one: tool cards then show human titles, as in the app
  try {
    const { loadCatalog } = await import("../js/core/catalog.js");
    const cat = await loadCatalog({});
    out.catalog = cat.entries.length ? cat : null;
  } catch { out.catalog = null; }
  await Promise.all(FIXTURE_NAMES.map(async (n) => {
    const r = await fetch(new URL(`${n}.json`, base));
    if (r.ok) out[n] = await r.json();
  }));
  return out;
}

const T0 = Date.parse("2026-10-07T09:40:00Z");

/** Seed one project and two conversations; returns {project, conversations: [main, second]}. */
export async function seedDemo(store, FIX, { name = "葛根芩连汤 · 复核" } = {}) {
  const project = await store.projects.create({
    name, description: "从经典记载到可核验的证据：配伍、靶点网络与主张复核。",
    instructions: "回答时区分记载、预测与实测；引用工具返回的证据编号；说明被拒绝的主张及其代码。",
  });
  await store.files.add(project.id, new Blob(["gene\tlog2fc\tpadj\nTNF\t-1.42\t0.003\nIL6\t-0.97\t0.021\nPTGS2\t-0.51\t0.180\n"], { type: "text/tab-separated-values" }), { name: "deg_lps_vs_puerarin.tsv" });
  await store.files.add(project.id, new Blob(["# 葛根芩连汤\n葛根 15 g，黄芩 9 g，黄连 9 g，炙甘草 6 g。\n出处：《伤寒论》。\n"], { type: "text/markdown" }), { name: "formula_notes.md" });

  const conv = await store.conversations.create({ projectId: project.id, title: "甘草与甘遂同用，有哪些记载？" });
  let t = T0;
  const put = (m) => store.messages.put({ conversationId: conv.id, createdAt: (t += 4000), ...m });
  const turn = async (parentId, userText, steps, text, extra = {}) => {
    const user = await put({ parentId, role: "user", content: userText });
    const toolCalls = [];
    const segments = [];
    const toolMsgs = [];
    let n = 0;
    for (const [i, step] of steps.entries()) {
      if (step.think) segments.push({ type: "reasoning", step: i + 1, text: step.think });
      const ids = [];
      for (const [name, args, env] of step.tools || []) {
        const id = `call_${conv.id.slice(0, 4)}_${userText.length}_${n++}`;
        ids.push(id);
        toolCalls.push({ id, name, args });
        toolMsgs.push({ role: "tool", toolCallId: id, name, args, content: env.text, envelope: env, step: i + 1, where: env.receipt?.where, status: env.status });
      }
      if (ids.length) segments.push({ type: "tools", step: i + 1, callIds: ids });
    }
    segments.push({ type: "text", step: steps.length + 1, text });
    const assistant = await put({
      parentId: user.id, role: "assistant", content: text, reasoning: steps.map((s) => s.think).filter(Boolean).join("\n\n"),
      segments, toolCalls, provider: "tao", model: "Tao-S1", status: "ok", usage: { input: 11840, output: 812 },
      ui: { thinkingMs: { 1: 8200 }, approvals: extra.approvals || {}, startedAt: t, endedAt: t + (extra.ms || 14600) },
    });
    for (const m of toolMsgs) await put({ ...m, parentId: assistant.id });
    return { user, assistant };
  };

  const a = await turn(null, "甘草与甘遂同用，有哪些记载？", [
    { think: "用户问的是十八反配伍。先查两味药的配伍关系，再查甘草的安全性记录。\n\n记载不是临床安全性结论；无记录也不等于安全。", tools: [["tcm_compatibility", { herbs: ["甘草", "甘遂"] }, FIX.envelope_compatibility], ["tcm_safety_report", { subject: "甘草", co_administered: ["甘遂"] }, FIX.envelope_safety]] },
  ], "甘草与甘遂同用，在种子语料中**记载**为十八反配伍禁忌（「藻戟遂芫俱战草」）[E1]。这是一条经典记载，不是临床安全性结论。\n\n甘草另有 4 条安全性记录，其中 3 条为高或严重级别 [E2]；这些是名医经验层级的记载，**无记录不等于安全**。\n\n内核判定本次运行六项发布状态均已满足（已准予发布）——这说明检查都已通过，不说明结论正确。", { approvals: {} });

  const b = await turn(a.assistant.id, "规范化：姜、白芍", [
    { tools: [["tcm_normalize", { names: ["姜", "白芍"] }, FIX.envelope_normalize]] },
  ], "「白芍」唯一匹配。「姜」有歧义：可能是**生姜**，也可能是**干姜** [E1]，两者性味功效不同；工具保留了两个候选，没有替你选择。请说明你指的是哪一个。", { ms: 6100 });

  const c1 = await turn(b.assistant.id, "葛根能减轻炎症吗？请复核现有主张。", [
    { think: "复核主张：一条疗效主张只有细胞实验支撑，一条把对接预测当作事实。还需要文献，但联网未开启、Runner 也未连接。", tools: [["call_tool", { tool: "study.review_claims", arguments: { question: "葛根能否减轻炎症？" } }, FIX.envelope_claims], ["literature_search", { query: "Puerariae Lobatae Radix inflammation" }, FIX.envelope_needs_runner]] },
  ], "内核复核了 3 条主张：\n\n1. 「葛根可减轻患者的炎症反应」——**已拒绝（CLM005）**：疗效主张需要随机对照试验或系统评价，本主张最弱的证据是临床前研究 [E1]。\n2. 「葛根有效，因为葛根素与 TNF 结合」——**已拒绝（CLM004）**：临床主张只依赖分子对接**预测** [E2]。\n3. 「葛根素可能与 TNF 结合」——允许，作为**机制假说**，附说明。\n\n文献检索没有运行：它需要本机 Runner。", { ms: 21800 });

  // an edited question: a sibling branch (‹2/2›) with a job and a denied job
  const c2user = await put({ parentId: b.assistant.id, role: "user", content: "用分子对接检验葛根素与 TNF 的结合，并跑一次葛根芩连汤的研究闭环。" });
  const jobCall = "call_job_dock";
  const denyCall = "call_job_deny";
  const asst = await put({
    parentId: c2user.id, role: "assistant", provider: "tao", model: "Tao-S1", status: "ok",
    content: "对接任务已在本机 Runner 上提交（CUDA:0）。任务完成并核验之前，它的输出不会作为结果。\n\n研究闭环没有运行：你拒绝了这次任务。",
    segments: [
      { type: "tools", step: 1, callIds: [jobCall, denyCall] },
      { type: "text", step: 2, text: "对接任务已在本机 Runner 上提交（CUDA:0）。任务完成并核验之前，它的输出不会作为结果。\n\n研究闭环没有运行：你拒绝了这次任务。" },
    ],
    toolCalls: [{ id: jobCall, name: "run_pipeline", args: { pipeline: "dock", arguments: { ligand: "puerarin", receptor: "upload:u_7f3a" } } }, { id: denyCall, name: "network_pharmacology_run", args: { formula: "葛根芩连汤", disease: "2 型糖尿病" } }],
    ui: { thinkingMs: {}, approvals: { [jobCall]: "once", [denyCall]: "deny" }, startedAt: t, endedAt: t + 4200 },
  });
  await put({ parentId: asst.id, role: "tool", toolCallId: jobCall, name: "run_pipeline", content: FIX.envelope_job.text, envelope: FIX.envelope_job, step: 1, where: "runner", status: "job_submitted" });
  await put({ parentId: asst.id, role: "tool", toolCallId: denyCall, name: "network_pharmacology_run", content: FIX.envelope_denied.text, envelope: FIX.envelope_denied, step: 1, where: "runner", status: "refused" });
  await store.conversations.update(conv.id, { leafId: c1.assistant.id, title: "甘草与甘遂同用，有哪些记载？" });

  const conv2 = await store.conversations.create({ projectId: project.id, title: "桂枝汤的靶点网络" });
  t = T0 - 86400000 * 2;
  const put2 = (m) => store.messages.put({ conversationId: conv2.id, createdAt: (t += 4000), ...m });
  const u = await put2({ parentId: null, role: "user", content: "桂枝汤的靶点网络，预测与实测分开列出" });
  const id = "call_net";
  const asst2 = await put2({
    parentId: u.id, role: "assistant", provider: "tao", model: "Tao-S1", status: "ok",
    content: "网络中的每一条边都是语料记载的关系或网络推断，没有一条是结合实验的测量结果；它最多支撑**机制假说**。",
    segments: [{ type: "tools", step: 1, callIds: [id] }, { type: "text", step: 2, text: "网络中的每一条边都是语料记载的关系或网络推断，没有一条是结合实验的测量结果；它最多支撑**机制假说**。" }],
    toolCalls: [{ id, name: "tcm_network_hypothesis", args: { formula_name: "桂枝汤" } }], ui: { thinkingMs: {}, approvals: {}, startedAt: t, endedAt: t + 3900 },
  });
  await put2({ parentId: asst2.id, role: "tool", toolCallId: id, name: "tcm_network_hypothesis", content: FIX.envelope_network_unattested.text, envelope: FIX.envelope_network_unattested, step: 1, where: "browser", status: "succeeded" });
  await store.conversations.update(conv2.id, { leafId: asst2.id, updatedAt: T0 - 86400000 * 2 });
  return { project, conversations: [await store.conversations.get(conv.id), await store.conversations.get(conv2.id)] };
}

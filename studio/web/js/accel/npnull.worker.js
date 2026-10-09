// A plain module worker of the js-workers engine (workers.js): it receives the plan once, then computes ranges of
// pathways with the exact kernel (npnull.js) and answers with their counts. No Python, no DOM.
//
//   ← {type: "plan", plan}            → {type: "ready"}
//   ← {type: "range", from, to}       → {type: "counts", from, counts}
//   any failure                       → {type: "error", message}

import { countRange, makeWork } from "./npnull.js";

let plan = null;
let work = null;

self.onmessage = async ({ data }) => {
  try {
    if (data?.type === "plan") {
      plan = data.plan;
      work = makeWork(plan);
      self.postMessage({ type: "ready" });
    } else if (data?.type === "range") {
      if (!plan) throw new Error("no plan yet");
      const counts = await countRange(plan, data.from, data.to, { work });
      self.postMessage({ type: "counts", from: data.from, counts });
    }
  } catch (err) {
    self.postMessage({ type: "error", message: String(err?.message || err) });
  }
};

// A router whose every call succeeds with a result of a set size (scripts/probe-growth.mjs's pattern): for the cases
// that measure how a turn's requests grow, where what the tool says does not matter and its size does. Everything
// else in the turn (the loop, the model client, the relay or fault server) is the real one.

import { synthEnvelope } from "../../web/js/core/router.js";

const FILL = { ascii: "x", cjk: "中" };

export function stubRouter({ result_chars = 16000, charset = "ascii" } = {}) {
  const calls = [];
  const fill = FILL[charset] || String(charset).slice(0, 1) || "x";
  return {
    calls,
    resolve: (name) => ({ target: { kind: "connector", id: `stub.${name}` }, entry: { id: `stub.${name}` }, name }),
    where: () => "runner",
    async call(name, args) {
      calls.push({ name, args });
      const env = await synthEnvelope({ tool: name, args, status: "succeeded", where: "runner", summary: `${name}: ${result_chars} characters` });
      return { ...env, ok: true, status: "succeeded", text: fill.repeat(result_chars) };
    },
  };
}

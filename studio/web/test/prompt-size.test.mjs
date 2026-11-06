// What every model call re-sends: the system prompt and the core tool schemas. At 530031f they came to 49,013
// characters (49,725 UTF-8 bytes) for the Tao-S1 prompt in Chinese with browser jobs ready (the agent-framework
// review's measure.mjs, reproduced below). Nothing may grow that by more than 2 %, and the part of the system prompt
// that stays the same from call to call (everything before its Environment section) must be most of it.
import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { toProviderTools } from "../js/core/catalog.js";
import { buildSystemPrompt, splitSystemPrompt } from "../js/core/prompt.js";
import { activeProvider } from "../js/core/providers.js";

const BASELINE_CHARS = 49_013;
const BASELINE_BYTES = 49_725;
const GROWTH = 1.02;
const STABLE_SHARE = 0.9;

/** The core tools as tcmstudio.core_tools defines them in this checkout (standard library only), or null. */
function coreTools() {
  const src = fileURLToPath(new URL("../../runner/src", import.meta.url));
  const script = "import json, sys; sys.path.insert(0, sys.argv[1]); from tcmstudio.core_tools import CORE_TOOLS; print(json.dumps(CORE_TOOLS))";
  // $PYTHON first (CI's convention, as the other tests that read the runner's Python), then python3
  for (const python of [process.env.PYTHON, "python3"].filter(Boolean)) {
    try {
      return JSON.parse(execFileSync(python, ["-I", "-c", script, src], { encoding: "utf8", stdio: ["ignore", "pipe", "ignore"], timeout: 60000 }));
    } catch { /* the next one */ }
  }
  return null;
}

const core = coreTools();
const bytes = (s) => Buffer.byteLength(s, "utf8");

test("fixed size per call: the system prompt and the core tool schemas do not grow", { skip: core ? false : "no Python for $PYTHON or python3" }, () => {
  // as measure.mjs built it
  const sys = buildSystemPrompt({ lang: "zh", provider: { relay: true }, project: { name: "p", defaults: { web: true } }, knowledge: [], env: { date: "2026-11-06", jobs: { status: "ready", workers: 2, snapshots: false } } });
  const tools = JSON.stringify(toProviderTools(core, "openai"));
  const chars = sys.length + tools.length;
  const utf8 = bytes(sys) + bytes(tools);
  assert.ok(chars <= BASELINE_CHARS * GROWTH, `${chars} characters per call (baseline ${BASELINE_CHARS})`);
  assert.ok(utf8 <= BASELINE_BYTES * GROWTH, `${utf8} bytes per call (baseline ${BASELINE_BYTES})`);
});

test("the stable prefix is at least 90 % of the system prompt's bytes, whatever is connected", () => {
  const relay = activeProvider({ provider: "tao" });
  const deepseek = activeProvider({ provider: "deepseek", models: { deepseek: "deepseek-reasoner" } });
  const ollama = activeProvider({ provider: "ollama", models: { ollama: "qwen3" } });
  const envs = [
    { date: "2026-11-06", jobs: { status: "ready", workers: 2, snapshots: false } },
    { date: "2026-11-06", browser: { status: "ready" }, runner: { status: "ready", url: "http://127.0.0.1:8765", version: "0.1.1", devices: [{ id: "cuda:0", name: "RTX 4090", available: true }, { id: "cpu", available: true }] }, jobs: { status: "ready", workers: 4, snapshots: true }, compute: "runner" },
    { date: "2026-11-06", runner: { status: "offline" }, jobs: { status: "unavailable" }, web: false },
  ];
  for (const lang of ["zh", "en"]) {
    for (const provider of [relay, deepseek, ollama]) {
      for (const env of envs) {
        const sys = buildSystemPrompt({ lang, provider, project: { name: "p", defaults: { web: true } }, knowledge: [], env });
        const [prefix] = splitSystemPrompt(sys);
        const share = bytes(prefix) / bytes(sys);
        assert.ok(share >= STABLE_SHARE, `${lang} ${provider.id} ${JSON.stringify(env)}: ${(share * 100).toFixed(1)} %`);
      }
    }
  }
});

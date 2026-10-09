// The pipeline's own rules (.github/workflows/studio.yml), checked without a browser: a change to anything the site
// bundles runs it; the check after a deployment reads the persistent pause (RELAY = "off") as the relay reads it; and
// in CI a Worker that cannot start fails the relay spec instead of skipping it. The workflow's steps are run here as
// GitHub runs them (bash -eo pipefail), with the tools they call replaced by recorders.

import { spawnSync } from "node:child_process";
import { chmodSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { expect, test } from "@playwright/test";
import { REPO, STUDIO, scratch, workerSkipReason } from "./lib/servers.mjs";

const WORKFLOW = readFileSync(path.join(REPO, ".github", "workflows", "studio.yml"), "utf8");
const LINES = WORKFLOW.split("\n");

/** The `paths:` filter of one event (`push`, `pull_request`), as written: a list of quoted globs. */
function pathsOf(event) {
  const start = LINES.findIndex((l) => l === `  ${event}:`);
  expect(start, `on.${event} in studio.yml`).toBeGreaterThan(-1);
  const at = LINES.findIndex((l, i) => i > start && /^ {4}paths:/.test(l));
  expect(at, `on.${event}.paths in studio.yml`).toBeGreaterThan(start);
  const out = [];
  for (const l of LINES.slice(at + 1)) {
    const m = /^ {6}- "([^"]+)"\s*$/.exec(l);
    if (!m) break;
    out.push(m[1]);
  }
  return out;
}

/** GitHub's path filter glob: `**` crosses directories, `*` does not. */
function matches(glob, file) {
  const re = glob.split("**").map((part) => part.split("*").map((s) => s.replace(/[.+?^${}()|[\]\\]/g, "\\$&")).join("[^/]*")).join(".*");
  return new RegExp(`^${re}$`).test(file);
}

/** The `run: |` script of the step named `name`. */
function runOf(name) {
  const at = LINES.findIndex((l) => l.trim() === `- name: ${name}`);
  expect(at, `step "${name}" in studio.yml`).toBeGreaterThan(-1);
  const indent = LINES[at].indexOf("-");
  const run = LINES.findIndex((l, i) => i > at && l.trim() === "run: |");
  const next = LINES.findIndex((l, i) => i > at && l.indexOf("-") === indent && l.trim().startsWith("- "));
  expect(run > at && (next < 0 || run < next), `step "${name}" has a run: | script`).toBe(true);
  const body = [];
  for (const l of LINES.slice(run + 1)) {
    if (l.trim() && l.search(/\S/) <= indent + 2) break;
    body.push(l.slice(indent + 4));
  }
  const script = body.join("\n");
  expect(script, `step "${name}" uses no \${{ }} inside its script`).not.toContain("${{");
  return script;
}

/** Runs a step's script as GitHub does; returns its outputs, stdout and the arguments each recorded tool was called with. */
function runStep(script, { cwd, env = {}, record = [] }) {
  const bin = path.join(cwd, ".bin");
  mkdirSync(bin, { recursive: true });
  const calls = path.join(cwd, ".calls");
  writeFileSync(calls, "");
  for (const tool of record) {
    const p = path.join(bin, tool);
    writeFileSync(p, `#!/bin/sh\nprintf '%s' "${tool}" >> "${calls}"\nfor a in "$@"; do printf ' %s' "$a" >> "${calls}"; done\necho >> "${calls}"\n`);
    chmodSync(p, 0o755);
  }
  const output = path.join(cwd, ".output");
  writeFileSync(output, "");
  const file = path.join(cwd, ".step.sh");
  writeFileSync(file, script);
  const r = spawnSync("bash", ["--noprofile", "--norc", "-eo", "pipefail", file], {
    cwd, encoding: "utf8", env: { PATH: `${bin}:${process.env.PATH}`, HOME: process.env.HOME, GITHUB_OUTPUT: output, ...env },
  });
  expect(r.status, `${r.stdout}\n${r.stderr}`).toBe(0);
  const outputs = Object.fromEntries(readFileSync(output, "utf8").split("\n").filter(Boolean).map((l) => l.split(/=(.*)/s).slice(0, 2)));
  return { outputs, stdout: r.stdout, calls: readFileSync(calls, "utf8").split("\n").filter(Boolean) };
}

test("a change to anything the site bundles or tcmstudio imports runs the workflow, on push and on pull requests", () => {
  // what studio/scripts/build_web.py puts on the path and tcmstudio.webbuild bundles (skills/ and registry/ go in
  // as bioagent/_bundled), and what pip reads to install those packages
  const build = readFileSync(path.join(STUDIO, "scripts", "build_web.py"), "utf8");
  const roots = [...build.matchAll(/(REPO|STUDIO) \/ "([^"]+)" \/ "src"/g)].map((m) => (m[1] === "STUDIO" ? `studio/${m[2]}` : m[2]));
  expect(roots.sort()).toEqual(["BioScience-Harness", "PSH-Harness", "studio/runner"]);
  const webbuild = readFileSync(path.join(STUDIO, "runner", "src", "tcmstudio", "webbuild.py"), "utf8");
  const bundled = /for name in \(([^)]*)\):/.exec(webbuild)?.[1].match(/"([^"]+)"/g).map((s) => s.slice(1, -1));
  expect(bundled).toEqual(["skills", "registry"]);
  const changed = [
    ...roots.map((r) => `${r}/src/pkg/module.py`),
    ...bundled.map((d) => `BioScience-Harness/${d}/some/file.yaml`),
    "PSH-Harness/pyproject.toml", "BioScience-Harness/pyproject.toml", "BioScience-Harness/setup.py",
    "studio/web/index.html", ".github/workflows/studio.yml",
  ];
  for (const event of ["push", "pull_request"]) {
    const globs = pathsOf(event);
    for (const file of changed) expect(globs.some((g) => matches(g, file)), `${event}: ${file} runs the workflow`).toBe(true);
    // the harnesses' docs, tests and data are not in the site: no rebuild, no redeploy
    for (const file of ["BioScience-Harness/README.md", "PSH-Harness/docs/x.md", "BioScience-Harness/tests/test_x.py", "BioScience-Harness/data/x.csv"]) {
      expect(globs.some((g) => matches(g, file)), `${event}: ${file} does not`).toBe(false);
    }
  }
});

test("the check after a deployment requires Tao-S1 only when its key was put and RELAY is not off", async () => {
  const secrets = runOf("Are the secrets set?");
  const check = runOf("Check the site, source gateway, and one tiny Tao-S1 call");
  const toml = readFileSync(path.join(STUDIO, "edge", "wrangler.toml"), "utf8");
  expect(toml).toMatch(/^RELAY = .*$/m);
  // the relay's own reading of RELAY decides what "off" is
  const { config } = await import(pathToFileURL(path.join(STUDIO, "edge", "src", "relay.js")).href);
  const { sourceConfig } = await import(pathToFileURL(path.join(STUDIO, "edge", "src", "sources.js")).href);

  const deploy = (relay, key, sources = '"on"') => {
    const cwd = scratch("tcmstudio-ci");
    mkdirSync(path.join(cwd, "studio", "edge"), { recursive: true });
    writeFileSync(path.join(cwd, "studio", "edge", "wrangler.toml"), toml.replace(/^RELAY = .*$/m, () => `RELAY = ${relay}`).replace(/^SOURCES = .*$/m, () => `SOURCES = ${sources}`));
    const s = runStep(secrets, { cwd, env: { CF_TOKEN: "t", CF_ACCOUNT: "a", KEY: key } });
    const c = runStep(check, { cwd, env: { MODEL: s.outputs.model, RELAY_OFF: s.outputs.relay_off, SOURCES_OFF: s.outputs.sources_off, SITE_URL: "https://science.impf.ai" }, record: ["node"] });
    expect(c.calls, "check.mjs runs once").toHaveLength(1);
    expect(c.calls[0]).toMatch(/^node studio\/edge\/scripts\/check\.mjs --url https:\/\/science\.impf\.ai /);
    return { ...s.outputs, required: / --require-model\b/.test(c.calls[0]), requiredSources: / --require-sources\b/.test(c.calls[0]), stdout: s.stdout };
  };

  for (const value of ["on", "off", "OFF", "0", "1", "false", "true", "no", "yes", "offline"]) {
    const off = !config({ RELAY: value }).enabled;
    for (const relay of [`"${value}"`, `'${value}'`]) {
      const r = deploy(relay, "sk-test");
      expect(r.ready).toBe("1");
      // the key is put either way: unpausing is RELAY back to "on", nothing else
      expect(r.model, `RELAY = ${relay}: the key is still put`).toBe("1");
      expect(r.relay_off, `RELAY = ${relay}`).toBe(off ? "1" : "0");
      expect(r.required, `RELAY = ${relay}: --require-model`).toBe(!off);
      expect(r.requiredSources, "source gateway health stays required independently of model pause").toBe(true);
      if (off) expect(r.stdout).toMatch(/::notice::RELAY is off/);
    }
  }
  expect(deploy('"off" # paused for the night', "sk-test").required).toBe(false);
  // no key in the repository: the site is deployed, Tao-S1 is not required
  const nokey = deploy('"on"', "  \n");
  expect(nokey.model).toBe("0");
  expect(nokey.required).toBe(false);
  expect(nokey.requiredSources, "database access does not depend on a model key").toBe(true);
  for (const value of ["on", "off", "OFF", "0", "1", "false", "true", "no", "yes", "offline"]) {
    const off = !sourceConfig({ SOURCES: value }).enabled;
    for (const sources of [`"${value}"`, `'${value}'`]) {
      const r = deploy('"on"', "sk-test", sources);
      expect(r.sources_off, `SOURCES = ${sources}`).toBe(off ? "1" : "0");
      expect(r.requiredSources, `SOURCES = ${sources}: --require-sources`).toBe(!off);
      expect(r.required, "source pause never relaxes Tao-S1 validation when its key was put").toBe(true);
      if (off) expect(r.stdout).toMatch(/::notice::SOURCES is off/);
    }
  }
  expect(deploy('"on"', "sk-test", '"off" # maintenance').requiredSources).toBe(false);
});

test("the deployment claims no DNS safeguard that wrangler in CI does not give", () => {
  // without a terminal wrangler overrides an existing record for the custom domain; it never fails on one
  expect(WORKFLOW).not.toMatch(/externally managed DNS record/i);
});

test("a Worker that cannot start fails the relay spec in CI; outside CI only a missing npm registry skips it", () => {
  const boot = new Error("timed out waiting for wrangler dev: wrangler exited (1)\n✘ [ERROR] No such compatibility flag: no_such_flag_xyz\nThe Workers runtime failed to start.");
  const offline = new Error("timed out waiting for wrangler dev: wrangler exited (1)\nnpm error code ECONNREFUSED\nnpm error syscall connect\nnpm error FetchError: request to http://127.0.0.1:9/wrangler failed, reason: connect ECONNREFUSED 127.0.0.1:9");
  const proxy = new Error("timed out waiting for wrangler dev: wrangler exited (1)\nnpm error code E403\nnpm error 403 Forbidden - GET https://registry.npmjs.org/wrangler");
  const crash = new Error("timed out waiting for wrangler dev: wrangler exited (1)\nUncaught Error: connect ECONNREFUSED 127.0.0.1:9229\n  at worker.js:1");
  for (const CI of ["true", "1"]) {
    for (const err of [boot, offline, proxy, crash]) expect(workerSkipReason(err, { CI }), `CI=${CI}`).toBeNull();
  }
  for (const env of [{}, { CI: "" }, { CI: "false" }, { CI: "0" }]) {
    expect(workerSkipReason(boot, env)).toBeNull();
    expect(workerSkipReason(crash, env)).toBeNull();
    expect(workerSkipReason(offline, env)).toMatch(/npm/);
    expect(workerSkipReason(proxy, env)).toMatch(/npm/);
  }
  // and the spec acts on it: it rethrows, it no longer turns every failure into a skip
  const spec = readFileSync(path.join(STUDIO, "e2e", "relay.spec.mjs"), "utf8");
  expect(spec).toMatch(/skipReason = workerSkipReason\(err\);\s*if \(!skipReason\) throw err;/);
});

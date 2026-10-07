// Once per run: the built site (from $STUDIO_SITE, or built now with studio/scripts/build_web.py) for the specs that
// serve it, and a check that the runner's Python is importable, so a missing install fails here with one clear line.

import { spawnSync } from "node:child_process";
import { PYTHON, REPO, siteDir } from "./lib/servers.mjs";

export default async function globalSetup() {
  const r = spawnSync(PYTHON, ["-c", "import tcmstudio, bioagent, psh"], { cwd: REPO, encoding: "utf8" });
  if (r.status !== 0) {
    throw new Error(`the runner is not installed for ${PYTHON}: pip install -e PSH-Harness -e BioScience-Harness -e studio/runner\n${r.stderr}`);
  }
  process.env.STUDIO_SITE = siteDir();
}

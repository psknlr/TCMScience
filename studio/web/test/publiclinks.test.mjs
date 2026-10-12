// The project's own pages are not shown in Studio (core/publiclinks.js): the About page links nowhere, runtime text
// (citations, tool results, answers) is scrubbed of the repository and its GitHub Pages, and nothing becomes a link to
// them; third-party links are left alone. Also a scan of everything the site publishes from studio/web.
import "./fixtures/setup.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { parseHTML } from "linkedom";

import { citationsOf, evidenceOf, safeUrl, sourceView } from "../js/core/governance.js";
import { renderMarkdown } from "../js/core/markdown.js";
import { isProjectUrl, scrubProjectRefs } from "../js/core/publiclinks.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, "..");
const REPO_URL = ["https:", "", "github.com", "psknlr", "TCMScience"].join("/");
const HOME_URL = ["https:", "", "psknlr.github.io", "TCMScience", ""].join("/");
const ARENA_URL = `${HOME_URL}arena/`;

test("the project's repository and pages are recognised, in any form; other projects' are not", () => {
  for (const u of [REPO_URL, `${REPO_URL}/`, `${REPO_URL}.git`, `${REPO_URL}/actions/runs/1`, `${REPO_URL}/blob/main/README.md`,
    REPO_URL.replace("TCMScience", "tcmscience"), HOME_URL, ARENA_URL, `${HOME_URL}studio/index.html`, REPO_URL.replace("https://", ""),
    REPO_URL.replace("https://", "http://www."), "https://raw.githubusercontent.com/psknlr/TCMScience/main/x.json",
    "https://api.github.com/repos/psknlr/TCMScience", "https://psknlr.github.io/"]) {
    assert.equal(isProjectUrl(u), true, u);
  }
  for (const u of ["https://github.com/HUPO-PSI/proxi-schemas", "https://psicquic.github.io/PsicquicSpec_1_4_Rest.html", "https://github.com/psknlr/other",
    "https://example.org/psknlr/TCMScience", "https://science.impf.ai/", "https://pubmed.ncbi.nlm.nih.gov/1/", "", null, 42, "not a url"]) {
    assert.equal(isProjectUrl(u), false, String(u));
  }
});

test("scrubProjectRefs takes out only this project's addresses and tidies the citation around them", () => {
  const card = `TCMScience · TCMScience TCM seed corpus 0.2.6 · ${REPO_URL} · snapshot b376a9d13224 at 2026-09-25T00:00:00Z`;
  assert.equal(scrubProjectRefs(card), "TCMScience · TCMScience TCM seed corpus 0.2.6 · snapshot b376a9d13224 at 2026-09-25T00:00:00Z");
  assert.equal(scrubProjectRefs(`seed corpus · ${REPO_URL}`), "seed corpus");
  assert.equal(scrubProjectRefs(`${REPO_URL} · seed corpus`), "seed corpus");
  assert.equal(scrubProjectRefs(`见项目主页（${HOME_URL}）。`), "见项目主页。");
  assert.equal(scrubProjectRefs(`source: <${REPO_URL}>.`), "source: .");
  // JSON stays JSON: a value is emptied, the quotes stay
  const json = JSON.stringify({ home_url: REPO_URL, citation: card, other: "https://github.com/HUPO-PSI/proxi-schemas" });
  const out = JSON.parse(scrubProjectRefs(json));
  assert.equal(out.home_url, "");
  assert.doesNotMatch(out.citation, /github/i);
  assert.equal(out.other, "https://github.com/HUPO-PSI/proxi-schemas");
  // untouched text comes back as the same string
  const plain = "桂枝汤 · see https://github.com/rdkit/rdkit and https://doi.org/10.1/x";
  assert.equal(scrubProjectRefs(plain), plain);
  assert.equal(scrubProjectRefs(""), "");
  assert.equal(scrubProjectRefs(undefined), undefined);
});

test("a source card, its evidence and its citations show no repository address and never link to it", () => {
  const envelope = {
    status: "succeeded",
    governance: {
      artifact: { sources: [{ id: "tcm-seed", name: "TCMScience TCM seed corpus", home_url: REPO_URL, citation: `TCMScience · seed · ${REPO_URL} · snapshot x` }] },
      evidence: [{ id: "ev1", design: "classical_record", citation: `本草 · ${REPO_URL}`, source_card_id: "tcm-seed" }],
    },
    citations: [{ id: "E1", label: `seed corpus · ${HOME_URL}`, url: REPO_URL, evidence_ref: "ev1" }],
  };
  const src = sourceView(envelope.governance.artifact.sources[0]);
  assert.equal(src.home_url, "");
  assert.doesNotMatch(JSON.stringify(src), /github/i);
  const ev = evidenceOf(envelope);
  assert.doesNotMatch(JSON.stringify(ev), /psknlr/i);
  const cites = citationsOf(envelope);
  assert.equal(cites[0].url, "");
  assert.equal(cites[0].label, "seed corpus");
  assert.equal(safeUrl(ARENA_URL), "");
  assert.equal(safeUrl("https://pubmed.ncbi.nlm.nih.gov/1/"), "https://pubmed.ncbi.nlm.nih.gov/1/");
});

test("markdown never links to the project's pages: their links lose the anchor, bare and <…> addresses disappear", () => {
  const { document } = parseHTML("<!doctype html><html><body></body></html>");
  const md = [
    `项目主页：[TCMScience](${HOME_URL}) 与 [Arena](${ARENA_URL})。`,
    `源代码 ${REPO_URL} 。`,
    `<${REPO_URL}>`,
    "其他：[RDKit](https://github.com/rdkit/rdkit) https://doi.org/10.1/x",
  ].join("\n\n");
  const box = document.createElement("div");
  box.append(renderMarkdown(md, { document }));
  const hrefs = [...box.querySelectorAll("a")].map((a) => a.getAttribute("href"));
  assert.deepEqual(hrefs, ["https://github.com/rdkit/rdkit", "https://doi.org/10.1/x"]);
  assert.doesNotMatch(box.textContent, /psknlr|github\.io/i);
  assert.match(box.textContent, /TCMScience/); // the words of the link stay
  assert.match(box.textContent, /Arena/);
});

/** Every file the build publishes from studio/web (not the test or design pages, which a --dev build alone ships). */
function published(dir = web, rel = "") {
  const out = [];
  for (const name of fs.readdirSync(path.join(dir, rel))) {
    const r = path.join(rel, name);
    if (!rel && ["test", "dev", "runtime", "node_modules"].includes(name)) continue;
    if (fs.statSync(path.join(dir, r)).isDirectory()) out.push(...published(dir, r));
    else out.push(r);
  }
  return out;
}

test("nothing the site publishes from studio/web names the repository, its homepage or the Arena", () => {
  // licence notices of bundled fonts and icons name their upstream projects and must stay (they are legal notices)
  const LICENCES = new Set([path.join("assets", "fonts", "LICENSE.md"), path.join("assets", "ICONS-LICENSE")]);
  const files = published().filter((f) => !LICENCES.has(f) && /\.(js|mjs|html|css|json|webmanifest|svg|txt|md)$/.test(f));
  assert.ok(files.length > 50, "the scan sees the app");
  const hits = [];
  for (const f of files) {
    const text = fs.readFileSync(path.join(web, f), "utf8");
    if (/https?:\/\/(?:www\.)?github\.com\/psknlr|psknlr\.github\.io|github\.com\/psknlr\/TCMScience/i.test(text)) hits.push(f);
    if (/href=["'][^"']*github/i.test(text)) hits.push(`${f} (a GitHub href)`);
  }
  assert.deepEqual(hits, []);
});

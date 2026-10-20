"""The exports of a network-pharmacology run: nine files a reader can check and re-derive.

=====================  ===================================================================
``compounds.tsv``      every constituent: its id, standard InChIKey and connectivity block
                       (the key's first 14 characters), how it is identified
                       (``inchikey`` or ``source_local``), composition level, herbs, sources
``targets.tsv``        every target: ``evidence_class`` (``measured``: an in-vitro potency
                       at or below the cut-off, or a depositor's active screening call),
                       measurements, distinct publications, compounds, herbs, whether it is
                       in the background, its disease score, degree and betweenness
``disease_targets.tsv``  the indication's whole gene set (header only without a disease)
``enrichment.tsv``     every pathway tested, with its library, p, BH q and permutation p
``network.tsv``        the PPI edge list: every STRING edge at or above the cut-off between
                       two targets, with its score and record (header only without STRING)
``provenance.json``    snapshot ids, parameters, digests, coverage, and every edge the
                       tables rest on, as (snapshot id, source record id)
``limitations.md``     one limitation per line, the run's own
``report.html``        the stages, coverage, results and limitations as one page
``run_manifest.json``  ``tcmscience.run-manifest/1``: the sixteen workflow stages, every
                       output's SHA-256 and size, the snapshot ids, parameters, seed, code
                       digest, protocol digest, and the command that reproduces the run
=====================  ===================================================================

No export carries a timestamp, so two runs on the same snapshots give the same bytes.
What identifies one execution (the research loop's audit-chain head, the digest of its
``artifact.json``, the ledger head a skill run read) is kept apart, in the manifest's
``execution`` block; everything else in the manifest is byte-identical between runs.

:func:`verify` re-checks a run directory: every digest the manifest records, and, given
the snapshot store, a recomputation of the analysis from the snapshots the manifest names
(the TSVs must come out byte for byte) and of each enrichment p- and q-value from the
saved tables. ``python -m bioagent.analysis.np_exports verify RUN --snapshots S --ledger L``
exits 0 when every check passed, 1 when one failed, 3 when none failed but one could not be
made (no snapshots given; an empty enrichment table, nothing to recompute).

The HTML renderer is the pipelines' (:mod:`bioagent.report_html`, standard library only),
imported when a report is written: neither importing this module nor writing the exports
loads numpy or scipy, so they run where those are not installed (the browser's Python).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = ["EXPORTS", "DATA_EXPORTS", "MANIFEST_FILE", "REPORT_FILE", "MANIFEST_SCHEMA",
           "PROVENANCE_SCHEMA", "COLUMNS", "Exports", "write_exports", "write_manifest",
           "tsv_text", "structure_report", "manifest_problems", "verify", "main"]

MANIFEST_FILE = "run_manifest.json"
REPORT_FILE = "report.html"
MANIFEST_SCHEMA = "tcmscience.run-manifest/1"
PROVENANCE_SCHEMA = "tcmscience.np-provenance/1"
STAGES_PIPELINE = "bioagent.analysis.network_pharmacology"

#: The words a reproduce command uses for paths, which differ from machine to machine and
#: are kept out of the manifest so that it stays byte-identical between runs.
PLACEHOLDERS = {
    "SNAPSHOTS": "the snapshot store that holds the snapshot ids this manifest lists",
    "LEDGER": "that store's snapshot ledger",
    "OUT": "this run's directory",
    "OUT2": "a new output directory",
    "STATE": "a new state directory (the research loop's checkpoints and audit chain)",
}

#: Written first and checked by workflow stage 16, in this order.
DATA_EXPORTS = ("compounds.tsv", "targets.tsv", "disease_targets.tsv", "enrichment.tsv",
                "network.tsv", "provenance.json", "limitations.md")
#: The nine exports of every run.
EXPORTS = (*DATA_EXPORTS, REPORT_FILE, MANIFEST_FILE)

COLUMNS: dict[str, list[str]] = {
    "compounds.tsv": ["compound", "name", "inchikey", "connectivity", "id_kind", "level",
                      "herbs", "sources"],
    "targets.tsv": ["target", "name", "evidence_class", "measurements", "publications",
                    "compounds", "herbs", "in_background", "disease_score", "degree",
                    "betweenness"],
    "disease_targets.tsv": ["target", "name", "score", "datatypes", "is_measured_target",
                            "in_background"],
    "enrichment.tsv": ["library", "pathway", "name", "size", "in_background", "overlap",
                       "p_value", "q_value", "empirical_p", "fold_enrichment", "targets"],
    "network.tsv": ["a", "b", "score", "record"],
}


# --------------------------------------------------------------------------- tables

def tsv_text(rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> str:
    """Tab-separated text: a header, then one line per row; a list is joined with ``;``
    and None is empty."""
    buf = io.StringIO()
    w = csv.writer(buf, delimiter="\t", lineterminator="\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([";".join(map(str, r[c])) if isinstance(r.get(c), (list, tuple))
                    else r.get(c) for c in columns])
    return buf.getvalue()


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _key(compound: str) -> tuple[str, str, str]:
    """(InChIKey, connectivity block, id kind) of a compound id."""
    if compound.startswith("inchikey:"):
        key = compound.split(":", 1)[1]
        return key, key.split("-", 1)[0], "inchikey"
    return "", "", "source_local"


def structure_report(compounds: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How the compounds were identified and deduplicated (the analysis's own account,
    ``network_pharmacology._identity``): by identical source-supplied InChIKey only; keys
    that share a connectivity block are counted and listed, never merged."""
    from .network_pharmacology import _identity
    return _identity(compounds)


def _tables(result: Mapping[str, Any]) -> dict[str, str]:
    """The five tables. A target's evidence class is ``measured`` either way: a curated
    potency at or below the cut-off, or (``hits='screening'``) a depositor's recorded active
    call; predicted edges never make a target."""
    compounds = []
    for c in result.get("compounds") or ():
        key, block, kind = _key(str(c["compound"]))
        compounds.append({**c, "inchikey": key, "connectivity": block, "id_kind": kind})
    targets = [{**t, "evidence_class": "measured", "publications": t.get("sources")}
               for t in result.get("targets") or ()]
    genes = list((result.get("disease") or {}).get("genes") or ())
    library = "reactome"
    enrichment = [{**r, "library": r.get("library") or library}
                  for r in result.get("enrichment") or ()]
    edges = list((result.get("network") or {}).get("edge_list") or ())
    return {
        "compounds.tsv": tsv_text(compounds, COLUMNS["compounds.tsv"]),
        "targets.tsv": tsv_text(targets, COLUMNS["targets.tsv"]),
        "disease_targets.tsv": tsv_text(genes, COLUMNS["disease_targets.tsv"]),
        "enrichment.tsv": tsv_text(enrichment, COLUMNS["enrichment.tsv"]),
        "network.tsv": tsv_text(edges, COLUMNS["network.tsv"]),
    }


# --------------------------------------------------------------------------- provenance

def _formula(formula: Any) -> dict[str, Any]:
    get = (formula.get if isinstance(formula, Mapping)
           else lambda k, d=None: getattr(formula, k, d))
    return {"id": get("id"), "chinese": get("chinese"), "source": get("source"),
            "fingerprint": get("fingerprint"),
            "components": [list(c) for c in get("components") or ()],
            "license": get("license", ""), "primary_source": get("primary_source", ""),
            "record_ids": list(get("record_ids", ()) or ()),
            "written": list(get("written", ()) or ())}


def _edges(result: Mapping[str, Any], formula: Mapping[str, Any],
           snaps: Mapping[str, Any] | None) -> dict[str, Any]:
    """Every edge the tables rest on, as the snapshot record it was read from."""
    herbs = (snaps or {}).get("tcm_herbs")
    composition = []
    if herbs is not None:
        parts = {e.get("object") for e in herbs.edges
                 if e.get("subject") == formula["id"] and e.get("predicate") == "contains"}
        for e in herbs.edges:
            if ((e.get("subject") == formula["id"] and e.get("predicate") == "contains")
                    or (e.get("subject") in parts and e.get("predicate") == "has_base_species")):
                composition.append({"subject": e["subject"], "predicate": e["predicate"],
                                    "object": e["object"], "snapshot": herbs.snapshot_id,
                                    "record": str(e.get("source_record_id"))})
        composition.sort(key=lambda d: (d["predicate"], d["subject"], d["object"], d["record"]))
    reactome = (snaps or {}).get("reactome")
    membership: list[dict[str, Any]] = []
    if reactome is not None:
        wanted = {(t, r["pathway"]) for r in result.get("enrichment") or ()
                  for t in r.get("targets") or ()}
        for e in reactome.edges:
            pair = (e.get("subject"), e.get("object"))
            if e.get("predicate") == "participates_in" and pair in wanted:
                membership.append({"target": pair[0], "pathway": pair[1],
                                   "snapshot": reactome.snapshot_id,
                                   "record": str(e.get("source_record_id"))})
        membership.sort(key=lambda d: (d["pathway"], d["target"], d["record"]))
    network = result.get("network") or {}
    disease = result.get("disease") or {}
    return {
        "composition": composition if herbs is not None else None,
        "constituents": [{"compound": c["compound"], "snapshot": sid, "record": rid}
                         for c in result.get("compounds") or () for sid, rid in c.get("records") or ()],
        "activities": [{"target": t["target"], "snapshot": sid, "record": rid}
                       for t in result.get("targets") or () for sid, rid in t.get("records") or ()],
        "pathway_membership": membership if reactome is not None else None,
        "interactions": [{"a": e["a"], "b": e["b"], "snapshot": network.get("ppi_source"),
                          "record": e["record"]} for e in network.get("edge_list") or ()],
        "disease_associations": [{"target": g["target"], "snapshot": sid, "record": rid}
                                 for g in disease.get("genes") or ()
                                 for sid, rid in g.get("records") or ()],
        "note": ("each row of the tables rests on the snapshot records listed here; a list "
                 "that is null was not resolved because the snapshot was not given"),
    }


def _provenance(result: Mapping[str, Any], formula: Mapping[str, Any],
                snaps: Mapping[str, Any] | None, extra: Mapping[str, Any] | None) -> dict[str, Any]:
    release = result.get("release") or {}
    doc: dict[str, Any] = {
        "schema": PROVENANCE_SCHEMA,
        "formula": {k: formula[k] for k in ("id", "chinese", "source", "fingerprint")},
        "dataset_hashes": dict(result.get("snapshots") or {}),
        "parameters": result.get("parameters"),
        "random_seed": (result.get("parameters") or {}).get("seed"),
        "code_digest": result.get("code_digest"), "result_digest": result.get("digest"),
        "coverage": result.get("coverage") or {},
        "excluded": result.get("excluded") or {},
        "background": result.get("background") or {},
        "network": {k: v for k, v in (result.get("network") or {}).items()
                    if k not in ("hubs", "edge_list")},
        "disease": {k: v for k, v in (result.get("disease") or {}).items()
                    if k not in ("targets", "genes")},
        "claims": {"candidates": len(result.get("claims") or ()),
                   "released": len(release.get("released") or ()),
                   "refused": len(release.get("refused") or ())},
    }
    doc.update(dict(extra or {}))
    doc["edges"] = _edges(result, formula, snaps)
    return doc


# --------------------------------------------------------------------------- the report

def _cell(value: Any) -> str:
    text = ";".join(map(str, value)) if isinstance(value, (list, tuple)) else (
        "" if value is None else str(value))
    return text.replace("|", "/").replace("\n", " ")


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3g}"
    return _cell(value)


def _report_md(result: Mapping[str, Any], formula: Mapping[str, Any],
               stages: Sequence[Mapping[str, Any]], limitations: Sequence[str],
               files: Mapping[str, Mapping[str, Any]], reproduce: Sequence[str],
               question: str, rebuttal: Mapping[str, Any] | None) -> str:
    cov = result.get("coverage") or {}
    rows = result.get("enrichment") or []
    claims = result.get("claims") or []
    network = result.get("network") or {}
    disease = result.get("disease") or {}
    background = result.get("background") or {}
    lines = [f"# Network pharmacology: {formula['chinese']} ({formula['source']})", ""]
    if question:
        lines += [f"Question: {question}", ""]
    lines += [f"Formula `{formula['id']}`, composition `{formula['fingerprint']}`. Analysis code "
              f"`{result.get('code_digest')}`; result `{result.get('digest')}`. A computational "
              "analysis of public snapshots: every claim stops at a mechanism hypothesis.", "",
              "## Workflow stages", "", "| # | Stage | Status | Reason |", "|---|---|---|---|"]
    lines += [f"| {s['n']} | {_cell(s['title'])} | {s['status']} | {_cell(s['reason'])} |"
              for s in stages]
    herbs, comps, targets = cov.get("herbs") or {}, cov.get("compounds") or {}, cov.get("targets") or {}
    ident = cov.get("identity") or {}
    lines += ["", "## Coverage", "", "| What | Count |", "|---|---|",
              f"| components | {herbs.get('total', 0)} |",
              f"| components with a verified species | {herbs.get('with_species', 0)} |",
              f"| components with a constituent in these snapshots | {herbs.get('with_compound', 0)} |",
              f"| constituents | {comps.get('total', 0)} |",
              f"| constituents with a standard InChIKey | {comps.get('with_inchikey', 0)} |",
              f"| constituents with a measured target | {comps.get('with_measured_target', 0)} |",
              f"| measured targets | {targets.get('measured', 0)} |",
              f"| measured targets in the background | {targets.get('in_background', 0)} |",
              f"| background ({_cell(background.get('kind'))}) proteins | {background.get('proteins', 0)} |",
              f"| pathways tested | {(cov.get('pathways') or {}).get('tested', 0)} |",
              f"| distinct keys sharing a connectivity block (kept separate) | "
              f"{ident.get('compounds_in_connectivity_groups', 0)} |"]
    lines += ["", "## Enrichment", ""]
    if rows:
        lines += [f"{len(rows)} pathways tested; the first {min(len(rows), 25)} by p-value "
                  "(all of them in enrichment.tsv).", "",
                  "| Pathway | Name | Overlap / in background | p | q (BH) | permutation p |",
                  "|---|---|---|---|---|---|"]
        lines += [f"| {_cell(r['pathway'])} | {_cell(r.get('name'))} | {r.get('overlap')} / "
                  f"{r.get('in_background')} | {_fmt(r.get('p_value'))} | {_fmt(r.get('q_value'))} | "
                  f"{_fmt(r.get('empirical_p'))} |" for r in rows[:25]]
    else:
        lines.append("No pathway was tested: nothing could be tested, which is an empty result, "
                     "not evidence of inactivity.")
    lines += ["", "## Hypotheses", ""]
    released = {c.get("object") for c in (result.get("release") or {}).get("released") or ()}
    if rebuttal is not None:                  # the research loop: what survived the rebuttals
        released &= set(rebuttal.get("survived") or ())
    kept = [c for c in claims if c["object"] in released]
    if kept:
        lines += [f"- **{_cell(c.get('pathway'))}** (`{c['object']}`): {c.get('statement')}"
                  for c in kept]
    else:
        lines.append("No hypothesis is made (the stages and the limitations say why).")
    for r in (rebuttal or {}).get("refuted") or ():
        lines.append(f"- refuted at rebuttal: `{r.get('object')}`: "
                     f"{_cell('; '.join(r.get('reasons') or ()))}")
    if disease:
        lines += ["", "## Indication overlap (descriptive)", "",
                  f"{disease.get('overlap', 0)} of the measured targets in the background are among "
                  f"the {disease.get('disease_genes', 0)} genes of {_cell(disease.get('name'))} "
                  f"({disease.get('disease')}); {_cell(disease.get('interpretation'))}"]
    lines += ["", "## Network (descriptive)", "",
              (f"{network.get('nodes', 0)} targets, {network.get('edges', 0)} STRING edges at score >= "
               f"{network.get('string_min_score')} from `{network.get('ppi_source')}`; "
               f"{_cell(network.get('interpretation'))}.") if network.get("ppi_source") else
              "No PPI snapshot was read: the network has no edges because none were read, not "
              "because none exist."]
    lines += ["", "## Limitations", ""] + [f"- {_cell(line)}" for line in limitations]
    lines += ["", "## Snapshots", "", "| Source | Snapshot id |", "|---|---|"]
    lines += [f"| {k} | `{v}` |" for k, v in sorted((result.get("snapshots") or {}).items())]
    lines += ["", "## Files", "", "| File | SHA-256 | Bytes |", "|---|---|---|"]
    lines += [f"| {name} | `{entry['sha256']}` | {entry['bytes']} |"
              for name, entry in files.items()]
    if reproduce:
        from ..research.workflow import reproduce_command
        lines += ["", "## Reproduce", "", "```", reproduce_command(reproduce), "```"]
    return "\n".join(lines) + "\n"


def _html(md: str) -> str:
    from ..report_html import render                  # the pipelines' renderer: no numpy
    return render(md, {})


# --------------------------------------------------------------------------- writing

@dataclass
class Exports:
    """What :func:`write_exports` wrote: the eight files before the manifest, the sixteen
    stage records, and the limitations as written."""

    out: Path
    files: dict[str, dict[str, Any]]
    records: list[Any]
    limitations: list[str]
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def stages(self) -> list[dict[str, Any]]:
        from ..research.workflow import manifest_stages
        return manifest_stages(self.records)


def _write(out: Path, name: str, text: str) -> dict[str, Any]:
    data = text.encode("utf-8")
    (out / name).write_bytes(data)
    return {"sha256": _digest(data), "bytes": len(data)}


def _limitation_lines(lines: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for line in lines:
        text = " ".join(str(line).split())
        if text and text not in seen:
            seen.append(text)
    return seen


def _snap_map(snaps: Any) -> dict[str, Any] | None:
    if snaps is None:
        return None
    if isinstance(snaps, Mapping):
        return dict(snaps)
    return {s.key: s for s in snaps}


def write_exports(result: Mapping[str, Any], out: str | Path, *, formula: Any,
                  snaps: Any = None, provenance: Mapping[str, Any] | None = None,
                  limitations: Sequence[str] | None = None, disease: str = "",
                  requested: Sequence[str] | None = None,
                  deviations: Sequence[Mapping[str, Any]] = (),
                  rebuttal: Mapping[str, Any] | None = None, no_organism: Sequence[str] = (),
                  reproduce: Sequence[str] = (), question: str = "",
                  manifest: Mapping[str, Any] | None = None,
                  refused: Mapping[str, str] | None = None) -> Exports:
    """Write the eight exports before the manifest; :func:`write_manifest` writes the
    ninth.

    ``result`` is the analysis result as JSON data with its ``digest``. ``limitations``
    are the run's own lines (default: the analysis's); a line for each workflow stage that
    is not PASS is added after them, and ``limitations.md`` holds exactly the lines
    returned in :attr:`Exports.limitations`. ``provenance`` adds fields to
    ``provenance.json``. ``manifest`` holds fields for the manifest (protocol digest,
    question, ...). ``refused`` (reference -> reason) names the sources the run's contract
    requested and the run refused: each stage that reads one says so, and one line of
    ``limitations.md`` lists them. The other arguments are
    :func:`bioagent.research.workflow.stage_records`'s.
    """
    from ..research.workflow import (export_record, refused_limitations, reproduce_command,
                                     stage_limitations, stage_records)

    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    snap_map = _snap_map(snaps)
    f = _formula(formula)
    command = reproduce_command(reproduce) if reproduce else ""
    records = stage_records(result, formula=formula, disease=disease, snaps=snap_map,
                            requested=requested, deviations=deviations, rebuttal=rebuttal,
                            no_organism=no_organism, reproduce=command, refused=refused)
    lines = _limitation_lines([*(result.get("limitations") or () if limitations is None
                                 else limitations), *refused_limitations(refused),
                               *stage_limitations(records)])
    files: dict[str, dict[str, Any]] = {}
    for name, text in _tables(result).items():
        files[name] = _write(out, name, text)
    files["provenance.json"] = _write(out, "provenance.json", json.dumps(
        _provenance(result, f, snap_map, provenance), ensure_ascii=False, indent=2,
        sort_keys=True, default=str) + "\n")
    files["limitations.md"] = _write(out, "limitations.md", "".join(f"{t}\n" for t in lines))
    records.append(export_record(out, files, snapshots=dict(result.get("snapshots") or {}),
                                 reproduce=command))
    exports = Exports(out=out, files=files, records=records, limitations=lines)
    files[REPORT_FILE] = _write(out, REPORT_FILE, _html(_report_md(
        result, f, exports.stages, lines, {k: files[k] for k in DATA_EXPORTS}, reproduce,
        question, rebuttal)))
    exports.manifest = {
        "schema": MANIFEST_SCHEMA, "analysis": STAGES_PIPELINE,
        **dict(manifest or {}),
        "formula": f, "disease": disease or (result.get("disease") or {}).get("disease") or "",
        "snapshots": dict(result.get("snapshots") or {}),
        "parameters": result.get("parameters"),
        "seed": (result.get("parameters") or {}).get("seed"),
        "code_digest": result.get("code_digest"), "result_digest": result.get("digest"),
        "coverage": result.get("coverage") or {},
        "reproduce": {"argv": list(reproduce), "command": command,
                      "placeholders": PLACEHOLDERS,
                      "verify": ["python", "-m", "bioagent.analysis.np_exports", "verify", "OUT",
                                 "--snapshots", "SNAPSHOTS", "--ledger", "LEDGER"]},
    }
    return exports


def write_manifest(exports: Exports, *, outputs: Mapping[str, Mapping[str, Any]] | None = None,
                   execution: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Write ``run_manifest.json`` (and the run record ``stages.json``) last.

    ``outputs`` adds other deterministic files of the run (name -> {sha256, bytes}).
    ``execution`` holds what identifies this one execution (an audit head, the digest of
    an ``artifact.json`` that carries a timestamp, a ledger head): it is the only part of
    the manifest that may differ between two runs of the same inputs.
    """
    from ..research.workflow import runrecord_stages
    from ..runrecord import SCHEMA as STAGES_SCHEMA, STAGES_FILE, complete, problems

    files = {**exports.files, **dict(outputs or {})}
    stages = runrecord_stages(exports.records, engine=STAGES_PIPELINE,
                              version=exports.manifest.get("code_digest"), outputs=files)
    doc = {**exports.manifest, "stages": exports.stages,
           "complete": complete(stages), "problems": problems(stages),
           "outputs": {k: dict(files[k]) for k in sorted(files)},
           "execution": {**dict(execution or {}),
                         "note": "identifies this execution and may differ between runs of "
                                 "the same inputs (the same snapshots, read through the same "
                                 "ledger, with the same parameters); everything else in this "
                                 "manifest, and every output it lists, is byte-identical "
                                 "between them"}}
    (exports.out / MANIFEST_FILE).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8")
    (exports.out / STAGES_FILE).write_text(json.dumps(
        {"schema": STAGES_SCHEMA, "pipeline": STAGES_PIPELINE,
         "planned": [s["name"] for s in stages], "running": None, "stages": stages,
         "complete": doc["complete"], "problems": doc["problems"]},
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return doc


# --------------------------------------------------------------------------- checking

def manifest_problems(run_dir: str | Path) -> list[str]:
    """Why ``run_manifest.json`` in ``run_dir`` does not hold: not readable, another
    schema, an export not listed, or an output whose bytes differ from its record."""
    run = Path(run_dir)
    try:
        doc = json.loads((run / MANIFEST_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{MANIFEST_FILE}: not readable ({exc})"]
    if doc.get("schema") != MANIFEST_SCHEMA:
        return [f"{MANIFEST_FILE}: schema {doc.get('schema')!r} is not {MANIFEST_SCHEMA}"]
    outputs = doc.get("outputs") or {}
    found = [f"{name}: not listed in the manifest" for name in EXPORTS
             if name != MANIFEST_FILE and name not in outputs]
    root = run.resolve()
    listed = dict(outputs)
    artifact = (doc.get("execution") or {}).get("artifact")
    if isinstance(artifact, Mapping) and artifact.get("path"):
        listed[str(artifact["path"])] = artifact
    for name, entry in sorted(listed.items()):
        path = (run / name).resolve()
        if root not in path.parents:
            found.append(f"{name}: outside the run directory")
            continue
        try:
            data = path.read_bytes()
        except OSError:
            found.append(f"{name}: missing")
            continue
        if _digest(data) != entry.get("sha256") or len(data) != entry.get("bytes"):
            found.append(f"{name}: differs from the digest the manifest records")
    return found


def _formula_version(fields: Mapping[str, Any]) -> Any:
    from ..sources.herbs import FormulaVersion
    return FormulaVersion(
        str(fields["id"]), str(fields["chinese"]), str(fields["source"]),
        tuple(tuple(c) for c in fields.get("components") or ()),
        license=str(fields.get("license") or "CC0-1.0"),
        primary_source=str(fields.get("primary_source") or ""),
        record_ids=tuple(str(r) for r in fields.get("record_ids") or ()),
        written=tuple(str(w) for w in fields.get("written") or ()))


def _read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _enrichment_arithmetic(run: Path, background: Mapping[str, Any],
                           params: Mapping[str, Any]) -> tuple[str, str]:
    """Each p-value of enrichment.tsv recomputed from its own columns (overlap, members in
    the background) and the background's size, and each q-value as BH over the whole
    table: (status, detail)."""
    from ..tools.stats import benjamini_hochberg, hypergeometric_test

    rows = _read_tsv(run / "enrichment.tsv")
    if not rows:
        return "UNVERIFIED", "enrichment.tsv holds no pathway: nothing to recompute"
    if params.get("hits") == "screening":
        return "UNVERIFIED", ("screening p-values are empirical (a permutation of protein "
                              "sets); they are recomputed by the re-run, not from the table")
    query = int(background.get("potent_in_background") or 0)
    proteins = int(background.get("proteins") or 0)
    bad = []
    p_values = []
    for r in rows:
        p = float(r["p_value"])
        p_values.append(p)
        again = hypergeometric_test(int(r["overlap"]), query, int(r["in_background"]),
                                    proteins)["p_value"] if query else 1.0
        if abs(again - p) > 1e-12 * max(1.0, abs(p)):
            bad.append(f"{r['pathway']}: p {p} != {again}")
    for r, q in zip(rows, benjamini_hochberg(p_values)["q_values"]):
        if abs(float(r["q_value"]) - q) > 1e-9:
            bad.append(f"{r['pathway']}: q {r['q_value']} != {q}")
    if bad:
        return "FAIL", "; ".join(bad[:5])
    return "PASS", (f"{len(rows)} p-values recomputed from the table's overlaps against a "
                    f"background of {proteins} with {query} targets, and the BH q-values over "
                    "all of them, match")


def verify(run_dir: str | Path, *, snapshot_root: str | Path | None = None,
           ledger: str | Path | None = None) -> dict[str, Any]:
    """Re-check a run directory: (1) every digest ``run_manifest.json`` records; (2) the
    enrichment's p- and q-values from the saved tables; and, given the snapshot store,
    (3) the analysis recomputed from the snapshots the manifest names: the same result
    digest and, byte for byte, the same tables. Returns {status, checks}."""
    from ..validation import FAIL, PASS, UNVERIFIED, aggregate

    run = Path(run_dir)
    checks: list[dict[str, Any]] = []

    def check(name: str, status: str, detail: str, **extra: Any) -> None:
        checks.append({"name": name, "status": status, "detail": detail, **extra})

    found = manifest_problems(run)
    check("manifest_digests", FAIL if found else PASS,
          "; ".join(found[:10]) if found else
          "every output the manifest lists has the SHA-256 and size it records")
    try:
        doc = json.loads((run / MANIFEST_FILE).read_text(encoding="utf-8"))
        provenance = json.loads((run / "provenance.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        check("readable", FAIL, f"{exc}")
        return {"status": FAIL, "checks": checks}
    params = doc.get("parameters") or {}
    status, detail = _enrichment_arithmetic(run, provenance.get("background") or {}, params)
    check("enrichment_arithmetic", status, detail)
    if snapshot_root is None or ledger is None:
        check("recompute", UNVERIFIED, "no snapshot store was given to recompute the analysis from")
        return {"status": aggregate(c["status"] for c in checks), "checks": checks}
    from ..sources.ledger import SnapshotLedger
    from ..sources.snapshot import load_snapshot
    from .network_pharmacology import Parameters, run_network_pharmacology

    book = SnapshotLedger(ledger)
    book.verify()
    versions = {e.snapshot_id: e.version for e in book.entries()}
    snaps = []
    for key, sid in sorted((doc.get("snapshots") or {}).items()):
        if sid not in versions:
            check("recompute", FAIL, f"{key}: snapshot {sid} is not in the ledger")
            return {"status": FAIL, "checks": checks}
        snaps.append(load_snapshot(snapshot_root, key, versions[sid], expected_id=sid,
                                   ledger=book, accept_review=True))
    p = Parameters(**{k: tuple(v) if isinstance(v, list) else v for k, v in params.items()})
    result = run_network_pharmacology(snaps, formula=_formula_version(doc["formula"]), params=p)
    data = json.loads(json.dumps(result.as_dict(), ensure_ascii=False, default=str))
    data["digest"] = result.digest()
    if result.code_digest != doc.get("code_digest"):
        check("code", UNVERIFIED, f"the analysis code here ({result.code_digest}) is not the "
              f"code that made the run ({doc.get('code_digest')}); the recomputation below is "
              "by different code")
    else:
        check("code", PASS, "the analysis code here is the code that made the run")
    check("result_digest", PASS if data["digest"] == doc.get("result_digest") else FAIL,
          f"recomputed result {data['digest']}" + (
              "" if data["digest"] == doc.get("result_digest") else
              f", recorded {doc.get('result_digest')}"))
    outputs = doc.get("outputs") or {}
    differ = []
    for name, text in _tables(data).items():
        if _digest(text.encode("utf-8")) != (outputs.get(name) or {}).get("sha256"):
            differ.append(name)
    check("tables", FAIL if differ else PASS,
          f"tables recomputed from the snapshots differ: {differ}" if differ else
          f"the {len(COLUMNS)} tables recomputed from the snapshots are byte-identical to the "
          "saved ones (their SHA-256 match the manifest)", recomputed=sorted(COLUMNS))
    return {"status": aggregate(c["status"] for c in checks), "checks": checks}


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="python -m bioagent.analysis.np_exports",
                                 description="check a network-pharmacology run's exports")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="re-check the manifest digests and recompute the "
                                      "enrichment (and, given the snapshots, the analysis)")
    v.add_argument("run_dir")
    v.add_argument("--snapshots", default=None)
    v.add_argument("--ledger", default=None)
    a = ap.parse_args(argv)
    if bool(a.snapshots) != bool(a.ledger):
        print("give both --snapshots and --ledger, or neither", file=sys.stderr)
        return 2
    report = verify(a.run_dir, snapshot_root=a.snapshots, ledger=a.ledger)
    print(json.dumps(report, indent=1, ensure_ascii=False))
    # 0: every check passed; 1: one contradicts the run (an edited file, a p-value or a
    # table that does not recompute); 3: none contradicts it, but one could not be made
    # (no snapshots given, an empty enrichment table, screening p-values)
    return {"PASS": 0, "FAIL": 1}.get(report["status"], 3)


if __name__ == "__main__":
    raise SystemExit(main())

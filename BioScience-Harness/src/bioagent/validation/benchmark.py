"""The Guizhi Decoction and palpitations benchmark: one research question, twelve stages.

    "Investigate the potential biological mechanisms through which Guizhi Decoction may
    influence palpitations, using traditional medical literature, pharmacological
    databases, experimental evidence, and computational analysis."

The definition (``data/benchmark_guizhi_palpitations.json``) fixes, before any data are
read, what each stage must produce, what it needs, how it is validated and the
limitations it may end with, together with the cardiac pathway set and the literature
queries. :func:`run_benchmark` executes the stages on what is here and records each as a
validation :class:`~bioagent.validation.Record`:

1-2   the formula version and its herbs, from the formula workbook, checked against the
      curated 伤寒论 formula;
3-7   compounds, measured targets, the measured/predicted split, the cardiac pathways and
      the controlled enrichment, all from one research-loop run on snapshots built from
      the real NPASS, CMAUP and LOTUS files (verified against their reviewed checksums);
8     the literature, from pre-registered Europe PMC and ClinicalTrials.gov queries;
9-10  the evidence graded by type with the contradictions found, and the hypotheses that
      survived, each labelled a mechanism hypothesis;
11    桂枝汤 against 桂枝甘草汤: the palpitation record (心下悸) belongs to the latter;
12    one hashed artifact per stage, and with ``repeat`` a second run that must match.

A stage that cannot run here says exactly why (no file, a refused host, web access not
requested) and how to reproduce it; it is never PASS. Nothing in a stage's output is
written from memory: every compound, target and pathway is read from a snapshot edge, and
every hypothesis is the research loop's own, with the edges it cites.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import (BLOCKED, FAIL, NOT_APPLICABLE, PASS, SKIPPED, UNVERIFIED, Record, aggregate,
               report, write_report)

__all__ = ["DEFINITION_FILE", "definition", "run_benchmark", "STAGE_IDS"]

DEFINITION_FILE = Path(__file__).resolve().parents[1] / "data" / "benchmark_guizhi_palpitations.json"
COMPONENT = "benchmark.guizhi-palpitations"
#: Stages whose artifacts must be identical on a repeat (literature answers change).
_LIVE_STAGES = ("literature",)


def definition(path: str | Path | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DEFINITION_FILE).read_text(encoding="utf-8"))


STAGE_IDS = tuple(s["id"] for s in definition()["stages"])


# --------------------------------------------------------------------------- helpers

def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1, default=str)
            + "\n").encode("utf-8")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


_CN = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _amount(dose: str) -> tuple[float | None, str]:
    """(number, unit) of a simple dose, Arabic or Chinese: 3两, 三两, 十二枚, 1.5g."""
    text = (dose or "").strip()
    m = re.match(r"^([\d.]+)\s*(\D*)$", text)
    if m:
        return float(m.group(1)), m.group(2)
    m = re.match(r"^([一二三四五六七八九十]+)(.*)$", text)
    if not m:
        return None, text
    digits, unit = m.group(1), m.group(2)
    if "十" in digits:
        tens, _, ones = digits.partition("十")
        value = (_CN.get(tens, 1) if tens else 1) * 10 + (_CN.get(ones, 0) if ones else 0)
    else:
        value = _CN.get(digits)
    return (float(value) if value is not None else None), unit


class _Run:
    """What the stages share: the inputs, the formula table and the research run."""

    def __init__(self, work: Path, defn: Mapping[str, Any], *, live: bool, raw: Path,
                 snapshot_root: Path | None, ledger: Path | None, parameters: Any,
                 log: Callable[[str], None], backend: Any = None) -> None:
        self.work, self.defn, self.live, self.raw = work, defn, live, raw
        self.snapshot_root, self.ledger = snapshot_root, ledger
        self.parameters, self.log, self.backend = parameters, log, backend
        self.artifacts = work / "artifacts"
        self.table: Any = None
        self.table_error = ""
        self.record: Any = None            # 桂枝汤《伤寒论》 in the workbook
        self.related: Any = None           # 桂枝甘草汤《伤寒论》
        self.acquisition: dict[str, Any] = {}
        self.data_status = SKIPPED
        self.data_reason = ""
        self.research: Any = None
        self.research_error = ""
        self.research_status = SKIPPED
        self.result: dict[str, Any] = {}
        self.rebuttal: dict[str, Any] = {}
        self.snaps: dict[str, Any] = {}
        self.outputs: dict[str, dict[str, Any]] = {}

    # the stages' artifacts, by stage id
    def write(self, stage: Mapping[str, Any], content: Mapping[str, Any]) -> dict[str, Any]:
        self.artifacts.mkdir(parents=True, exist_ok=True)
        name = f"{stage['n']:02d}_{stage['id']}.json"
        data = _canonical(content)
        (self.artifacts / name).write_bytes(data)
        entry = {"file": f"artifacts/{name}", "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                 "bytes": len(data)}
        self.outputs[stage["id"]] = {**entry, "content": content}
        return entry


def _record(stage: Mapping[str, Any], reproduce: str) -> Record:
    return Record(f"{COMPONENT}.{stage['n']:02d}.{stage['id']}", "benchmark_stage",
                  input={"requirement": stage["requirement"], "needs": stage["needs"],
                         "validation": stage["validation"]},
                  reproduce=reproduce)


# --------------------------------------------------------------------------- 1. formula

def _load_table(run: _Run) -> None:
    from ..research.loop import QuestionRefused, parse_question
    from ..research.loop import _table as default_table
    run.table = default_table()
    if run.table is None:
        run.table_error = ("the formula workbook (中医方剂数据表.xlsx, or $BIOAGENT_FORMULA_TABLE) "
                           "is not here or not readable; install bioagent[formulas] and put the "
                           "workbook at the repository root")
        return
    for attr, name in (("record", run.defn["formula"]), ("related", run.defn["related_formula"])):
        try:
            q = parse_question(name, table=run.table)
            setattr(run, attr, run.table.by_id(q.formula_id))
        except QuestionRefused as exc:
            run.table_error += f"{name}: {exc}. "


def _formula_version(run: _Run, stage: Mapping[str, Any]) -> Record:
    from ..sources.formulas import TABLE_LICENSE, source_matches, split_source
    from ..tcm import default_knowledge

    rec = _record(stage, "bioagent validate benchmark --out OUT")
    if run.table is None:
        rec.add("workbook", BLOCKED, run.table_error)
        return rec
    if run.record is None:
        rec.add("version_resolved", FAIL, run.table_error.strip())
        return rec
    r = run.record
    bare, books = split_source(run.defn["formula"])
    rec.add("version_resolved", PASS if source_matches(r.source, books) else FAIL,
            f"{run.defn['formula']} is workbook row {r.row}, {r.id}, source {r.source}",
            formula_id=r.id, row=r.row)
    versions = run.table.named(bare)
    distinct = {tuple((c.drug, c.name) for c in v.components) for v in versions}
    rec.add("name_alone_ambiguous", PASS if len(distinct) > 1 else UNVERIFIED,
            f"{bare} alone names {len(versions)} workbook versions with {len(distinct)} different "
            "compositions: the source book is what selects one", versions=len(versions),
            compositions=len(distinct))
    kb = default_knowledge()
    try:
        seed = kb.require(bare, kind="formula")
    except (KeyError, LookupError, ValueError) as exc:
        rec.add("curated_crosscheck", UNVERIFIED, f"no curated formula to check against: {exc}")
        run.write(stage, {"formula": run.defn["formula"], "formula_id": r.id, "row": r.row,
                          "source": r.source, "composition_as_written": r.composition})
        return rec
    seed_drugs = {i.herb_id.split(".", 1)[-1]: i.dose for i in seed.ingredients}
    wb = {c.drug: c.dose for c in r.components}
    same_drugs = set(seed_drugs) == set(wb)
    doses = {d: {"curated": seed_drugs.get(d), "workbook": wb.get(d),
                 "agree": _amount(seed_drugs.get(d, ""))[0] == _amount(wb.get(d, ""))[0]}
             for d in sorted(set(seed_drugs) | set(wb))}
    agree = same_drugs and source_matches(seed.source, books) and all(
        x["agree"] for x in doses.values())
    rec.add("curated_crosscheck", PASS if agree else FAIL,
            (f"the curated {seed.chinese} ({seed.source}) has the same {len(wb)} crude drugs and "
             "doses as the workbook record") if agree else
            f"the curated formula and the workbook record disagree: {doses}", doses=doses)
    passages = [{"id": p.id, "source": p.source, "chapter": p.chapter, "text": p.text}
                for p in kb.passages_mentioning(seed.id)]
    run.write(stage, {
        "formula": run.defn["formula"], "formula_id": r.id, "row": r.row,
        "name_as_written": r.written_name, "source": r.source, "composition_as_written": r.composition,
        "actions_as_written": r.actions, "cautions_as_written": r.cautions,
        "fingerprint": r.version().fingerprint, "versions_of_the_name": len(versions),
        "distinct_compositions": len(distinct),
        "curated": {"id": seed.id, "source": seed.source, "doses": doses, "passages": passages},
        "licence": {"workbook": TABLE_LICENSE,
                    "curated": "MIT (the seed corpus, as its source card declares)"}})
    return rec


# --------------------------------------------------------------------------- 2. herbs

def _herbs(run: _Run, stage: Mapping[str, Any]) -> Record:
    from ..sources.materia import MATERIA, crude_drugs

    rec = _record(stage, "bioagent validate benchmark --out OUT")
    if run.record is None:
        rec.add("formula", BLOCKED if run.table is None else FAIL,
                run.table_error or "the formula did not resolve")
        return rec
    verified = crude_drugs()
    herbs, missing, unresolved = [], [], []
    for c, written in zip(run.record.components, run.record.written_components):
        entry = MATERIA.get(c.drug) if c.drug else None
        drug = verified.get(f"tcm:herb.{c.drug}") if c.drug else None
        if entry is None:
            unresolved.append(c.name or c.written)
        species = [{"taxid": s.taxid, "name": s.name} for s in (drug.species if drug else ())]
        if entry is not None and not species:
            missing.append(entry.chinese)
        herbs.append({"herb": f"tcm:herb.{c.drug}" if c.drug else None,
                      "chinese": entry.chinese if entry else c.name, "latin": entry.latin if entry else "",
                      "written": written.name or written.written, "dose": c.dose,
                      "processing": c.processing, "species": species})
    rec.add("resolved", PASS if not unresolved else FAIL,
            "every ingredient resolves to a crude drug: " + "、".join(h["chinese"] for h in herbs)
            if not unresolved else f"unresolved ingredients: {unresolved}")
    rec.add("verified_species", PASS if not missing else UNVERIFIED,
            "each crude drug has an NCBI-verified source species" if not missing else
            f"{'、'.join(missing)} have no NCBI-verified species and contribute no constituents")
    run.write(stage, {"formula_id": run.record.id, "herbs": herbs})
    return rec


# --------------------------------------------------------------------------- data

def _acquire(run: _Run) -> None:
    """The snapshots stages 3-10 read: given ones, or built from the raw files."""
    if run.snapshot_root is not None:
        run.acquisition = {"route": "given", "snapshot_root": str(run.snapshot_root),
                           "ledger": str(run.ledger)}
        run.data_status = UNVERIFIED
        run.data_reason = ("the snapshots were given, not built here from verified raw files; "
                           "their data provenance is the builder's")
        return
    from ..sources import pipeline
    if run.record is None or run.related is None:
        run.data_status, run.data_reason = BLOCKED, run.table_error or "the formulas did not resolve"
        return
    snap = run.work / "lake" / "snapshots"
    doc = pipeline.build_research_snapshots(
        run.raw, snap, snap / "ledger.jsonl", sources=("npass", "cmaup", "lotus"), network=True,
        formulas=[run.defn["formula"], run.defn["related_formula"]], offline=not run.live,
        report_path=run.work / "snapshot_report.json", log=run.log)
    run.acquisition = {"route": "built", "status": doc["status"], "next_step": doc.get("next_step", ""),
                       "reproduce": doc.get("reproduce", ""),
                       "stages": [{k: s.get(k) for k in ("stage", "status", "detail", "files")
                                   if k in s} for s in doc["stages"]],
                       "snapshots": {k: v.get("snapshot_id") for k, v in (doc.get("snapshots") or {}).items()}}
    run.data_status = doc["status"]
    if doc["status"] in (PASS, UNVERIFIED):
        run.snapshot_root, run.ledger = snap, snap / "ledger.jsonl"
        run.data_reason = "" if doc["status"] == PASS else next(
            (s["detail"] for s in doc["stages"] if s["status"] == UNVERIFIED), "")
    else:
        last = doc["stages"][-1] if doc["stages"] else {}
        run.data_reason = (f"{last.get('stage', 'acquisition')}: {last.get('detail', '')}; "
                           f"next: {doc.get('next_step', '')}")


def _research(run: _Run) -> None:
    from ..research.loop import (QuestionRefused, ResearchRefused, default_protocol,
                                 parse_question, run_research)
    if run.snapshot_root is None:
        run.research_status, run.research_error = (run.data_status if run.data_status != PASS
                                                   else BLOCKED), run.data_reason
        return
    p = run.defn["protocol"]
    try:
        q = parse_question(run.defn["formula"], table=run.table)
        protocol = default_protocol(q, activity=tuple(p["activity_sources"]),
                                    optional=tuple(p["optional_sources"]),
                                    parameters=run.parameters)
        t0 = time.perf_counter()
        run.research = run_research(q, protocol=protocol, snapshot_root=run.snapshot_root,
                                    ledger_path=run.ledger, state_dir=run.work / "state",
                                    output_dir=run.work / "research_run")
        run.research_runtime = time.perf_counter() - t0
    except (QuestionRefused, ResearchRefused) as exc:
        text = str(exc)
        run.research_status = (BLOCKED if re.search(r"no snapshot|not importable|review", text)
                               else FAIL)
        run.research_error = f"{type(exc).__name__}: {text}"
        return
    except Exception as exc:                                    # noqa: BLE001
        # a defect, not a reason: recorded as one, and the other stages still report
        run.research_status = FAIL
        run.research_error = f"{type(exc).__name__}: {exc} ({_where(exc)})"
        return
    out = Path(run.research.output_dir)
    run.result = json.loads((out / "result.json").read_text(encoding="utf-8"))
    run.rebuttal = json.loads((out / "rebuttal.json").read_text(encoding="utf-8"))
    run.research_status = PASS
    from ..sources.ledger import SnapshotLedger
    from ..sources.snapshot import load_snapshot
    by_id = {e.snapshot_id: e for e in SnapshotLedger(run.ledger).entries()}
    for key, sid in run.research.snapshots.items():
        entry = by_id[sid]
        run.snaps[key] = load_snapshot(run.snapshot_root, key, entry.version, expected_id=sid)


def _not_run(rec: Record, run: _Run) -> Record:
    status = run.research_status if run.research_status != PASS else BLOCKED
    rec.add("research_run", status, run.research_error or run.data_reason or
            "the research loop did not run")
    if run.acquisition.get("route") == "built" and run.acquisition.get("reproduce"):
        rec.notes.append(f"reproduce the data step: {run.acquisition['reproduce']}")
    return rec


#: The upstream projects whose files each data stage reads.
_DEPENDS = {
    "compounds": ("NPASS", "CMAUP", "LOTUS"),
    "targets": ("NPASS", "CMAUP"),
    "measured_vs_predicted": ("NPASS", "CMAUP", "STRING", "Reactome", "UniProt"),
    "pathways": ("Reactome", "UniProt"),
    "enrichment": ("NPASS", "CMAUP", "LOTUS", "Reactome", "UniProt"),
    "evidence": ("NPASS", "CMAUP", "LOTUS", "STRING", "Reactome", "UniProt"),
    "hypotheses": ("NPASS", "CMAUP", "LOTUS", "Reactome", "UniProt"),
}


def _provenance(rec: Record, run: _Run, stage_id: str) -> None:
    """Whether the files this stage's data came from are the reviewed ones."""
    snapshots = dict(run.research.snapshots) if run.research else {}
    if run.acquisition.get("route") != "built":
        rec.add("data_provenance", UNVERIFIED, run.data_reason, snapshots=snapshots)
        return
    from ..sources import pipeline
    projects = _DEPENDS[stage_id]
    integrity = next((s for s in run.acquisition.get("stages", ()) if s["stage"] == "integrity"), {})
    rows = [f for f in integrity.get("files") or ()
            if getattr(pipeline._SPECS.get(f["file"]), "source_project", "") in projects]
    status = aggregate(f["status"] for f in rows) if rows else UNVERIFIED
    held = [f["file"] for f in rows if f["status"] == PASS]
    loose = [f for f in rows if f["status"] != PASS]
    rec.add("data_provenance", status,
            f"{len(held)} file(s) from {', '.join(projects)} hold to their reviewed checksum or size"
            + ("; " + "; ".join(f"{f['file']}: {f.get('reason', f['status'])} ({f.get('sha256', '')})"
                                for f in loose) if loose else ""),
            files={f["file"]: {"status": f["status"], "sha256": f.get("sha256")} for f in rows},
            snapshots=snapshots)


def _activity_edges(run: _Run, compounds: set[str]) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for key, snap in sorted(run.snaps.items()):
        for e in snap.edges:
            if e.get("predicate") in ("targets", "tested_against") and e.get("subject") in compounds:
                out.append((key, e))
    return out


# --------------------------------------------------------------------------- 3. compounds

def _compounds(run: _Run, stage: Mapping[str, Any]) -> Record:
    rec = _record(stage, "bioagent validate benchmark --out OUT --live")
    if run.research is None:
        return _not_run(rec, run)
    _provenance(rec, run, "compounds")
    herbs = {h["herb"]: h for h in run.outputs.get("herbs", {}).get("content", {}).get("herbs", ())}
    rows = run.result.get("compounds") or []
    per_herb = {h: 0 for h in herbs}
    per_source: dict[str, int] = {}
    for row in rows:
        for h in row.get("herbs") or ():
            per_herb[h] = per_herb.get(h, 0) + 1
        for s in row.get("sources") or ():
            per_source[s] = per_source.get(s, 0) + 1
    with_species = [h for h, v in herbs.items() if v.get("species")]
    none = [herbs[h]["chinese"] for h in with_species if not per_herb.get(h)]
    rec.add("compounds_per_herb", PASS if rows and not none else UNVERIFIED,
            f"{len(rows)} constituents: " + ", ".join(
                f"{herbs[h]['chinese']} {per_herb.get(h, 0)}" for h in herbs)
            + (f"; no constituent is recorded for {'、'.join(none)}'s species in these sources"
               if none else ""), per_herb=per_herb, per_source=per_source)
    gold = next((s for s in run.acquisition.get("stages", ()) if s["stage"] == "build"), None)
    if gold is not None:
        rec.add("gold_markers", PASS if gold["status"] in (PASS, UNVERIFIED) else FAIL,
                gold["detail"])
    run.write(stage, {"formula_id": run.record.id if run.record else run.result.get("formula"),
                      "count": len(rows), "per_herb": per_herb, "per_source": per_source,
                      "compounds": [{k: row.get(k) for k in ("compound", "name", "herbs", "level",
                                                               "sources")} for row in rows]})
    return rec


# --------------------------------------------------------------------------- 4. targets

def _targets(run: _Run, stage: Mapping[str, Any]) -> Record:
    from ..analysis.network_pharmacology import Parameters, _passes_potency

    rec = _record(stage, "bioagent validate benchmark --out OUT --live")
    if run.research is None:
        return _not_run(rec, run)
    _provenance(rec, run, "targets")
    params = run.parameters or Parameters()
    targets = run.result.get("targets") or []
    compounds = {r["compound"] for r in run.result.get("compounds") or ()}
    measured: dict[str, list[dict[str, Any]]] = {}
    for key, e in _activity_edges(run, compounds):
        if e.get("predicate") == "targets" and e.get("knowledge_level") != "prediction" \
                and _passes_potency(e.get("measure"), params):
            measured.setdefault(e["object"], []).append({"source": key, "record": e.get("source_record_id"),
                                                         "compound": e["subject"],
                                                         "measure": e.get("measure")})
    unsupported = [t["target"] for t in targets if t["target"] not in measured]
    rec.add("targets_measured", FAIL if unsupported else PASS,
            f"{len(targets)} human proteins measured at or below {params.activity_max_nm:g} nM, each "
            "resting on a measured activity edge in a verified snapshot" if not unsupported else
            f"{len(unsupported)} target(s) rest on no measured edge: {unsupported[:10]}",
            targets=len(targets), measurements=sum(len(v) for v in measured.values()))
    top = sorted(targets, key=lambda t: (-t.get("measurements", 0), t["target"]))
    run.write(stage, {"cut_off_nm": params.activity_max_nm, "activity_types": list(params.activity_types),
                      "count": len(targets),
                      "targets": [{"target": t["target"], "name": t.get("name"),
                                   "compounds": t.get("compounds"), "measurements": t.get("measurements"),
                                   "sources": t.get("sources"),
                                   "records": sorted((m["source"], str(m["record"]))
                                                     for m in measured.get(t["target"], ()))[:20]}
                                  for t in top]})
    return rec


# --------------------------------------------------------------------------- 5. measured vs predicted

def _measured_vs_predicted(run: _Run, stage: Mapping[str, Any]) -> Record:
    rec = _record(stage, "bioagent validate benchmark --out OUT --live")
    if run.research is None:
        return _not_run(rec, run)
    _provenance(rec, run, "measured_vs_predicted")
    compounds = {r["compound"] for r in run.result.get("compounds") or ()}
    targets = {t["target"] for t in run.result.get("targets") or ()}
    counts: dict[str, dict[str, int]] = {}

    def count(cls: str, e: Mapping[str, Any]) -> None:
        kind = "predicted" if e.get("knowledge_level") == "prediction" else "measured"
        counts.setdefault(cls, {"measured": 0, "predicted": 0})[kind] += 1

    for _, e in _activity_edges(run, compounds):
        count("compound_target", e)
    for e in getattr(run.snaps.get("string"), "edges", ()):
        if e.get("subject") in targets and e.get("object") in targets:
            count("protein_protein (STRING)", e)
    for e in getattr(run.snaps.get("reactome"), "edges", ()):
        if e.get("predicate") == "participates_in" and e.get("subject") in targets:
            count("pathway_membership (Reactome)", e)
    edges = {(s.snapshot_id, str(e.get("source_record_id"))): e
             for s in run.snaps.values() for e in s.edges}
    survived = set(run.rebuttal.get("survived") or ())
    bad = []
    for claim in run.result.get("claims") or ():
        if claim["object"] not in survived:
            continue
        for sid, rid in claim.get("support") or ():
            e = edges.get((sid, str(rid)))
            if e is None or (e.get("predicate") == "targets" and (
                    e.get("knowledge_level") == "prediction" or not e.get("measure"))):
                bad.append((claim["object"], sid, rid))
    predicted_left_out = int((run.result.get("excluded") or {}).get(
        "predicted target edge (not used by this skill)", 0))
    measured_targets = {e["object"] for _, e in _activity_edges(run, compounds)
                        if e.get("predicate") == "targets" and e.get("knowledge_level") != "prediction"
                        and e.get("measure")}
    predicted_only = sorted(targets - measured_targets)
    rec.add("targets_from_measurements", FAIL if predicted_only else PASS,
            "every target has a measured activity edge; "
            f"{predicted_left_out} predicted compound-target edge(s) were left out of the analysis"
            if not predicted_only else
            f"{len(predicted_only)} target(s) rest on predicted edges only: {predicted_only[:10]}",
            predicted_left_out=predicted_left_out)
    rec.add("hypotheses_cite_measured_edges", FAIL if bad else PASS,
            "every edge a surviving hypothesis cites is in a snapshot, and its compound-target "
            "step is a measured activity" if not bad else f"unsupported citations: {bad[:5]}")
    topology = (run.result.get("network") or {}).get("interpretation", "")
    rec.add("topology_descriptive", PASS if "descriptive" in topology else FAIL,
            f"STRING edges are predictions and feed only the descriptive topology: {topology}")
    run.write(stage, {"edge_classes": counts, "predicted_compound_target_left_out": predicted_left_out,
                      "rule": "a target, an enrichment input and a hypothesis rest on measured "
                              "activities; predictions (STRING, inferred annotations) are counted "
                              "and labelled, never promoted"})
    return rec


# --------------------------------------------------------------------------- 6. pathways

def _pathways(run: _Run, stage: Mapping[str, Any]) -> Record:
    rec = _record(stage, "bioagent validate benchmark --out OUT --live")
    if run.research is None:
        return _not_run(rec, run)
    _provenance(rec, run, "pathways")
    wanted = run.defn["pathways"]["cardiac_electrophysiology"]
    reactome = run.snaps.get("reactome")
    nodes = {n["id"]: n for n in getattr(reactome, "nodes", ()) if n.get("category") == "pathway"}
    by_name = {str(n.get("name", "")).strip().lower(): pid for pid, n in nodes.items()}
    members: dict[str, set[str]] = {}
    for e in getattr(reactome, "edges", ()):
        if e.get("predicate") == "participates_in":
            members.setdefault(e["object"], set()).add(e["subject"])
    targets = {t["target"]: t for t in run.result.get("targets") or ()}
    enrichment = {r["pathway"]: r for r in run.result.get("enrichment") or ()}
    found, absent = [], []
    for w in wanted:
        pid = f"reactome:{w['id']}" if f"reactome:{w['id']}" in nodes else by_name.get(w["name"].lower())
        if pid is None:
            absent.append(w["name"])
            continue
        hit = sorted(members.get(pid, set()) & set(targets))
        row = enrichment.get(pid) or {}
        found.append({"pathway": pid, "name": nodes[pid].get("name"), "members": len(members.get(pid, ())),
                      "measured_targets": [{"target": t, "name": targets[t].get("name")} for t in hit],
                      "tested": bool(row), "q_value": row.get("q_value"),
                      "empirical_p": row.get("empirical_p")})
    rec.add("pathway_set_in_release", PASS if found else UNVERIFIED,
            f"{len(found)} of the {len(wanted)} pre-registered pathway(s) found in this Reactome release"
            + (f"; not found: {', '.join(absent)}" if absent else ""), absent=absent)
    with_targets = [f for f in found if f["measured_targets"]]
    rec.add("membership_identified", PASS,
            (f"{len(with_targets)} pre-registered pathway(s) contain measured targets: " + "; ".join(
                f"{f['name']} ({', '.join(t['name'] or t['target'] for t in f['measured_targets'][:6])})"
                for f in with_targets[:8])) if with_targets else
            "none of the formula's measured targets is annotated to the pre-registered cardiac "
            "and electrophysiological pathways")
    run.write(stage, {"pre_registered": wanted, "found": found, "not_in_release": absent,
                      "interpretation": "membership of measured targets in Reactome pathways; a "
                                        "pathway listed here is not a mechanism"})
    return rec


# --------------------------------------------------------------------------- 7. enrichment

def _enrichment(run: _Run, stage: Mapping[str, Any]) -> Record:
    rec = _record(stage, "bioagent validate benchmark --out OUT --live")
    if run.research is None:
        return _not_run(rec, run)
    _provenance(rec, run, "enrichment")
    params = run.result.get("parameters") or {}
    rows = run.result.get("enrichment") or []
    fdr, alpha = params.get("fdr", 0.05), params.get("permutation_alpha", 0.05)
    significant = [r for r in rows if r.get("q_value") is not None and r["q_value"] <= fdr
                   and r.get("empirical_p") is not None and r["empirical_p"] <= alpha]
    background = run.result.get("background") or {}
    query = int(background.get("potent_in_background",
                               len([t for t in run.result.get("targets") or () if t.get("in_background")])))
    rec.add("analysis_ran", PASS if run.research.verdict is not None else FAIL,
            f"the governed analysis ran to its verdict in {getattr(run, 'research_runtime', 0):.0f} s; "
            f"released: {run.research.released}", released=run.research.released,
            governed=bool((run.research.artifact.provenance or {}).get("governed_execution"))
            if run.research.artifact else False)
    controls = params.get("background") == "assayed" and all("q_value" in r for r in rows)
    rec.add("controls", PASS if controls else UNVERIFIED,
            f"{len(rows)} pathways tested against the {params.get('background')} background "
            f"({background.get('proteins', '?')} proteins) with BH over all "
            f"of them and a degree-matched permutation null ({params.get('permutations')} draws); "
            f"{len(significant)} pass both",
            tested=len(rows), significant=len(significant), query=query)
    if query < 3:
        rec.add("supported_by_data", NOT_APPLICABLE,
                f"{query} measured target(s) in the background: too few for an over-representation "
                "test to say anything")
    not_run = run.rebuttal.get("not_run") or []
    limiting = [r for r in not_run if not _moot(r)]
    rec.add("rebuttals", UNVERIFIED if limiting else PASS,
            f"{len(run.rebuttal.get('survived') or [])} survived, {len(run.rebuttal.get('refuted') or [])} "
            f"refuted" + (f"; could not run: {limiting}" if limiting else "")
            + (f"; not applicable: {[r for r in not_run if _moot(r)]}" if len(limiting) < len(not_run)
               else ""))
    run.write(stage, {
        "parameters": params, "background": background, "tested": len(rows),
        "significant": [{k: r.get(k) for k in ("pathway", "name", "overlap", "in_background", "p_value",
                                               "q_value", "empirical_p", "fold_enrichment", "targets")}
                        for r in significant],
        "rebuttal": {"survived": run.rebuttal.get("survived") or [],
                     "refuted": run.rebuttal.get("refuted") or [], "not_run": not_run},
        "result_digest": run.result.get("digest")})
    return rec


def _moot(not_run: Any) -> bool:
    """A rebuttal that did not run because the primary analysis already is its conservative
    variant (the assayed background); one that could not run (a single activity source) is a
    limitation instead."""
    return isinstance(not_run, Mapping) and "already" in str(not_run.get("why", ""))


# --------------------------------------------------------------------------- 8. literature

def _literature(run: _Run, stage: Mapping[str, Any]) -> Record:
    rec = _record(stage, "bioagent validate benchmark --out OUT --live")
    lit = run.defn["literature"]
    if not run.live:
        rec.add("queries", SKIPPED, "web access was not requested (--live); no query was sent")
        run.write(stage, {"queries": [], "sent": False})
        return rec
    from ..backends.http import HTTPBackend
    from ..providers.public_apis import PublicAPIProvider, render_call

    manifests = {m.id.rsplit(".", 1)[-1]: m for m in PublicAPIProvider().discover()}
    backend = run.backend or HTTPBackend(cache_dir=None, timeout_s=30, max_retries=2)
    answers = []
    plan = [("europepmc", q["id"], {"query": q["query"], "page_size": lit["page_size"]})
            for q in lit["europepmc"]]
    plan += [("clinicaltrials", q["id"], {"term": q["term"], "page_size": lit["page_size"]})
             for q in lit["clinicaltrials"]]
    for source, qid, args in plan:
        t0 = time.perf_counter()
        res = backend.invoke(manifests[source], use_cache=False, **render_call(source, "search", **args))
        meta = res.metadata or {}
        status = res.status.value
        entry = {"source": source, "id": qid, "arguments": args, "status": status,
                 "http_status": meta.get("http_status"), "sent_at": time.strftime(
                     "%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "latency_s": round(time.perf_counter() - t0, 2)}
        if status == "SUCCEEDED":
            entry.update(_europepmc(res.value) if source == "europepmc" else _trials(res.value))
            rec.add(f"query:{qid}", PASS, f"{source}: {entry['returned']} record(s) returned, "
                    f"{entry['count']} {entry['count_is']}", count=entry["count"])
        else:
            text = res.error or ""
            refused = re.search(r"403|407|proxy|policy|tunnel|forbidden|denied|resolve|timed out|"
                                r"unreachable|5\d\d", text, re.I) or status in ("DENIED", "TIMEOUT")
            entry["error"] = text[:300]
            rec.add(f"query:{qid}", BLOCKED if refused else FAIL, f"{source}: {status} {text[:200]}")
        answers.append(entry)
    run.write(stage, {"queries": answers, "sent": True,
                      "note": "retrieved by pre-registered queries and typed by their metadata; "
                              "the findings were not read or graded"})
    return rec


def _europepmc(value: Any) -> dict[str, Any]:
    """Europe PMC's answer: ``hitCount`` is the total; a record's publication types come as
    ``pubTypeList`` (the core result type) or a ``;``-separated ``pubType`` (the lite one)."""
    value = value if isinstance(value, Mapping) else {}
    items = ((value.get("resultList") or {}).get("result")) or []
    records = []
    for r in items:
        listed = (r.get("pubTypeList") or {}).get("pubType") if isinstance(r.get("pubTypeList"), Mapping) else None
        if isinstance(listed, str):
            types = [listed]
        elif isinstance(listed, list):
            types = [str(t) for t in listed]
        else:
            types = [t.strip() for t in str(r.get("pubType") or "").split(";") if t.strip()]
        records.append({"id": r.get("id"), "source": r.get("source"), "pmid": r.get("pmid"),
                        "pmcid": r.get("pmcid"), "doi": r.get("doi"), "title": r.get("title"),
                        "year": r.get("pubYear"), "journal": r.get("journalTitle"),
                        "publication_types": types})
    total = value.get("hitCount")
    return {"count": int(total) if total is not None else len(records),
            "count_is": "total" if total is not None else "returned", "returned": len(records),
            "records": records}


def _trials(value: Any) -> dict[str, Any]:
    """ClinicalTrials.gov's answer: a page of studies; the total only when it was asked for."""
    value = value if isinstance(value, Mapping) else {}
    records = []
    for s in value.get("studies") or ():
        p = s.get("protocolSection") or {}
        records.append({"nct_id": (p.get("identificationModule") or {}).get("nctId"),
                        "title": (p.get("identificationModule") or {}).get("briefTitle"),
                        "status": (p.get("statusModule") or {}).get("overallStatus"),
                        "phases": (p.get("designModule") or {}).get("phases") or [],
                        "conditions": (p.get("conditionsModule") or {}).get("conditions") or []})
    total = value.get("totalCount")
    return {"count": int(total) if total is not None else len(records),
            "count_is": "total" if total is not None else "returned (more may exist)",
            "returned": len(records), "more": bool(value.get("nextPageToken")),
            "records": records}


# --------------------------------------------------------------------------- 9. evidence

def _evidence(run: _Run, stage: Mapping[str, Any]) -> Record:
    from ..analysis.network_pharmacology import Parameters, _passes_potency
    from ..tcm.model import CLAIM_SUPPORT, EvidenceTier, licenses

    rec = _record(stage, "bioagent validate benchmark --out OUT --live")
    kinds = sorted(CLAIM_SUPPORT)

    def licensed(tier: EvidenceTier) -> list[str]:
        return [k for k in kinds if licenses(tier, k)]

    evidence: list[dict[str, Any]] = []
    contradictions: list[dict[str, Any]] = []
    limitations: list[dict[str, Any]] = []
    if run.record is not None:
        rows = [("formula", run.defn["formula"], run.record), ("related", run.defn["related_formula"], run.related)]
        for role, name, r in rows:
            if r is None:
                continue
            evidence.append({"type": "classical_text", "about": name, "record": r.actions,
                             "mentions_palpitation": "悸" in (r.actions or ""),
                             "tier": EvidenceTier.CLASSICAL_TEXT.name,
                             "licenses": licensed(EvidenceTier.CLASSICAL_TEXT)})
        own = "悸" in (run.record.actions or "")
        related = run.related is not None and "悸" in (run.related.actions or "")
        if related and not own:
            contradictions.append({
                "kind": "indication_recorded_for_a_related_formula",
                "detail": f"心下悸 is recorded for {run.defn['related_formula']}, not for "
                          f"{run.defn['formula']}; an answer about {run.defn['formula']} and "
                          "palpitations that uses it reasons across formulas"})
        rec.add("classical_records", PASS,
                f"{run.defn['formula']} records {'' if own else 'no '}palpitation (悸); "
                f"{run.defn['related_formula']} records {'心下悸' if related else 'none'}")
    else:
        rec.add("classical_records", BLOCKED if run.table is None else FAIL, run.table_error)
    if run.research is not None:
        _provenance(rec, run, "evidence")
        params = run.parameters or Parameters()
        targets = run.result.get("targets") or []
        evidence.append({"type": "in_vitro_activity", "about": "the constituents' measured targets",
                         "count": len(targets), "tier": EvidenceTier.PRECLINICAL.name,
                         "licenses": licensed(EvidenceTier.PRECLINICAL),
                         "scope": "a compound measured against a protein in an assay; not the "
                                  "decoction, not in the body"})
        evidence.append({"type": "computational_enrichment", "about": "pathway over-representation",
                         "survived": run.rebuttal.get("survived") or [],
                         "tier": EvidenceTier.COMPUTATIONAL_PREDICTION.name,
                         "licenses": licensed(EvidenceTier.COMPUTATIONAL_PREDICTION)})
        compounds = {r["compound"] for r in run.result.get("compounds") or ()}
        pairs: dict[tuple[str, str], dict[str, list[str]]] = {}
        for key, e in _activity_edges(run, compounds):
            if e.get("predicate") != "targets" or e.get("knowledge_level") == "prediction" or not e.get("measure"):
                continue
            side = "potent" if _passes_potency(e["measure"], params) else "not_potent"
            pairs.setdefault((e["subject"], e["object"]), {"potent": [], "not_potent": []})[side].append(
                f"{key}:{e.get('source_record_id')}")
        mixed = sorted((k for k, v in pairs.items() if v["potent"] and v["not_potent"]))
        if mixed:
            contradictions.append({"kind": "mixed_measurements", "count": len(mixed),
                                   "detail": "one compound-target pair measured both at and above "
                                             f"the {params.activity_max_nm:g} nM cut-off",
                                   "examples": [{"compound": c, "target": t, **pairs[(c, t)]}
                                                for c, t in mixed[:20]]})
        for r in run.rebuttal.get("refuted") or ():
            contradictions.append({"kind": "refuted_by_rebuttal", "pathway": r.get("object"),
                                   "reasons": r.get("reasons")})
        limitations.extend({"kind": "rebuttal_not_run", "detail": r}
                           for r in run.rebuttal.get("not_run") or () if not _moot(r))
        rec.add("measured_evidence", PASS, f"{len(targets)} measured targets graded as preclinical; "
                f"{len(mixed)} compound-target pair(s) with mixed measurements")
    else:
        rec.add("measured_evidence", run.research_status if run.research_status != PASS else BLOCKED,
                run.research_error or run.data_reason or "the research loop did not run")
    lit = (run.outputs.get("literature") or {}).get("content") or {}
    if lit.get("sent"):
        by_type: dict[str, int] = {}
        for q in lit.get("queries") or ():
            for r in q.get("records") or ():
                for t in r.get("publication_types") or ["(untyped)"]:
                    by_type[t] = by_type.get(t, 0) + 1
                if q["source"] == "clinicaltrials":
                    by_type["registered trial"] = by_type.get("registered trial", 0) + 1
        evidence.append({"type": "literature", "about": "records returned by the pre-registered queries",
                         "by_publication_type": by_type, "tier": None, "licenses": [],
                         "scope": "retrieved, not appraised: a record's existence licenses no claim"})
        rec.add("literature_evidence", PASS, f"{sum(by_type.values())} records typed by their metadata")
    else:
        rec.add("literature_evidence", SKIPPED, "the literature was not queried (--live)")
    limitations += [{"kind": "analysis", "detail": line} for line in run.result.get("limitations") or ()]
    run.write(stage, {"evidence": evidence, "contradictions": contradictions, "limitations": limitations,
                      "rule": "each type licenses only the claim kinds its tier admits "
                              "(bioagent.tcm.model.CLAIM_SUPPORT)"})
    return rec


# --------------------------------------------------------------------------- 10. hypotheses

def _hypotheses(run: _Run, stage: Mapping[str, Any]) -> Record:
    from ..contracts.claim_language import overreaching_language

    rec = _record(stage, "bioagent validate benchmark --out OUT --live")
    if run.research is None:
        return _not_run(rec, run)
    _provenance(rec, run, "hypotheses")
    survived = set(run.rebuttal.get("survived") or ())
    cardiac = {f"reactome:{p['id']}" for p in run.defn["pathways"]["cardiac_electrophysiology"]}
    cardiac |= {f["pathway"] for f in ((run.outputs.get("pathways") or {}).get("content") or {}).get("found", ())}
    released = {c.object for c in (run.research.artifact.claims if run.research.artifact else ())}
    hypotheses, problems = [], []
    for c in run.result.get("claims") or ():
        if c["object"] not in survived:
            continue
        findings = overreaching_language(c.get("statement", ""), c.get("kind", ""))
        if c.get("kind") != "mechanism_hypothesis":
            problems.append(f"{c['object']} is a {c.get('kind')} claim")
        if findings:
            problems.append(f"{c['object']}: wording claims more than a hypothesis: "
                            f"{[f.phrase for f in findings]}")
        hypotheses.append({
            "label": "mechanism hypothesis (computational; not an established mechanism)",
            "pathway": c["object"], "name": c.get("pathway"), "statement": c.get("statement"),
            "released": c["object"] in released, "q_value": c.get("q_value"),
            "empirical_p": c.get("empirical_p"), "rests_on": c.get("support"),
            "path_licenses": c.get("path_licenses"), "why_not_stronger": c.get("why_not_stronger"),
            "bears_on_palpitations": c["object"] in cardiac,
            "would_be_tested_by": "an experiment in which the formula, or the cited constituent, "
                                  "changes the activity of this pathway's measured targets in a "
                                  "human cardiac model, with a heart-rhythm endpoint"})
    rec.add("labelled", FAIL if problems else PASS,
            "; ".join(problems) if problems else
            (f"{len(hypotheses)} hypotheses, each of kind mechanism_hypothesis, worded as one"
             if hypotheses else "no pathway survived analysis and rebuttal: no mechanism "
             "hypothesis is licensed by these data, and that negative result is the finding"))
    verdict = run.research.verdict
    rec.add("released", PASS if run.research.released else UNVERIFIED,
            "the research artifact passed its release validation" if run.research.released else
            f"the artifact was not authorized for release: {list(getattr(verdict, 'codes', ()))}")
    run.write(stage, {"hypotheses": hypotheses,
                      "bearing_on_palpitations": [h["pathway"] for h in hypotheses if h["bears_on_palpitations"]],
                      "none_licensed": not hypotheses})
    return rec


# --------------------------------------------------------------------------- 11. distinction

def _distinction(run: _Run, stage: Mapping[str, Any]) -> Record:
    from ..sources.materia import MATERIA

    rec = _record(stage, "bioagent validate benchmark --out OUT")
    if run.record is None or run.related is None:
        rec.add("both_resolved", BLOCKED if run.table is None else FAIL, run.table_error.strip())
        return rec
    a, b = run.record, run.related

    def comp(r: Any) -> dict[str, dict[str, str]]:
        return {c.drug: {"chinese": MATERIA[c.drug].chinese, "dose": c.dose, "processing": c.processing}
                for c in r.components}

    ca, cb = comp(a), comp(b)
    shared = sorted(set(ca) & set(cb))
    rec.add("both_resolved", PASS, f"{run.defn['formula']} is {a.id}; {run.defn['related_formula']} "
            f"is {b.id}" + (f", read through {b.erratum.id} ({b.erratum.status})" if b.erratum else ""))
    own, related = "悸" in (a.actions or ""), "悸" in (b.actions or "")
    rec.add("palpitation_record_attributed", PASS if related and not own else UNVERIFIED,
            f"心下悸 is recorded for {run.defn['related_formula']}" + ("" if not own else
            f" and 悸 for {run.defn['formula']} too") if related else
            "neither record mentions palpitation (悸)")
    research = {}
    if run.research is not None:
        herbs_b = {f"tcm:herb.{d}" for d in cb}
        comps_b = {r["compound"] for r in run.result.get("compounds") or ()
                   if set(r.get("herbs") or ()) & herbs_b}
        targets_b = sorted(t["target"] for t in run.result.get("targets") or ()
                           if set(t.get("compounds") or ()) & comps_b)
        research = {"compounds_from_shared_herbs": len(comps_b),
                    "targets_reached_by_shared_herbs": len(targets_b),
                    "targets_of_the_formula": len(run.result.get("targets") or ()),
                    "note": f"{run.defn['related_formula']}'s herbs are all in {run.defn['formula']}, so "
                            "its constituents and measured targets are a subset of the formula's; "
                            "the analysis of the formula covers them, but not the related formula's "
                            "doses or its absence of the other herbs"}
    shared_zh = "、".join(cb[d]["chinese"] for d in cb if d in ca)
    extra_zh = "、".join(ca[d]["chinese"] for d in ca if d not in cb)
    statement = (f"心下悸 (palpitations below the heart) is recorded for {run.defn['related_formula']}, "
                 f"not for {run.defn['formula']}. {run.defn['formula']} contains both of its herbs "
                 f"({shared_zh}) and also {extra_zh}, and gives 桂枝 "
                 f"{ca.get('guizhi', {}).get('dose', '?')} where {run.defn['related_formula']} gives "
                 f"{cb.get('guizhi', {}).get('dose', '?')}. Carrying the palpitation record over to "
                 f"{run.defn['formula']} is reasoning across formulas and must be marked so."
                 ) if related and not own else ""
    rec.add("cross_formula_marked", PASS if statement or own else UNVERIFIED,
            statement or "no cross-formula reasoning is needed: the formula records it itself")
    run.write(stage, {
        "formula": {"name": run.defn["formula"], "id": a.id, "composition": ca, "actions": a.actions,
                    "fingerprint": a.version().fingerprint},
        "related": {"name": run.defn["related_formula"], "id": b.id, "composition": cb, "actions": b.actions,
                    "composition_as_written": b.composition, "fingerprint": b.version().fingerprint,
                    "erratum": b.erratum.as_dict() if b.erratum else None},
        "shared_herbs": shared, "only_in_formula": sorted(set(ca) - set(cb)),
        "only_in_related": sorted(set(cb) - set(ca)),
        "palpitation_recorded_for": [n for n, has in ((run.defn["formula"], own),
                                                      (run.defn["related_formula"], related)) if has],
        "research": research, "statement": statement})
    return rec


# --------------------------------------------------------------------------- 12. artifacts

def _artifacts(run: _Run, stage: Mapping[str, Any], repeat: Mapping[str, str] | None) -> Record:
    rec = _record(stage, "bioagent validate benchmark --out OUT --repeat")
    manifest = {sid: {k: v for k, v in o.items() if k != "content"} for sid, o in sorted(run.outputs.items())}
    rec.add("written", PASS if manifest else FAIL,
            f"{len(manifest)} stage artifacts written and hashed under artifacts/")
    if repeat is None:
        rec.add("repeat", SKIPPED, "not repeated (--repeat runs the deterministic stages again "
                                   "in a fresh state directory and compares the artifacts)")
    else:
        compared = {k: v for k, v in manifest.items() if k not in _LIVE_STAGES}
        differ = sorted(k for k, v in compared.items() if repeat.get(k) != v["sha256"])
        rec.add("repeat", FAIL if differ else PASS,
                f"a second run gave byte-identical artifacts for {len(compared)} stages"
                if not differ else f"artifacts differ on a repeat: {differ}", differ=differ)
    run.artifacts.mkdir(parents=True, exist_ok=True)
    (run.artifacts / "manifest.json").write_bytes(_canonical({"benchmark": COMPONENT, "artifacts": manifest}))
    return rec


# --------------------------------------------------------------------------- the run

def _where(exc: BaseException) -> str:
    """The innermost frame an exception came from, for a record a reader can act on."""
    import traceback
    frames = traceback.extract_tb(exc.__traceback__)
    return f"{Path(frames[-1].filename).name}:{frames[-1].lineno}" if frames else "?"


def _guarded(fn: Callable[..., Record], run: _Run, stage: Mapping[str, Any]) -> Record:
    """A stage's record; a stage that raises is recorded as FAIL with the exception, so one
    defect does not take the other stages' reports with it."""
    t0 = time.perf_counter()
    try:
        rec = fn(run, stage)
    except Exception as exc:                                    # noqa: BLE001
        rec = _record(stage, "bioagent validate benchmark --out OUT")
        rec.add("stage", FAIL, f"{type(exc).__name__}: {exc} ({_where(exc)})")
    rec.runtime_s = time.perf_counter() - t0
    if stage["id"] in run.outputs:
        rec.provenance = {k: v for k, v in run.outputs[stage["id"]].items() if k != "content"}
    return rec


def _stages(run: _Run, repeat: Mapping[str, str] | None = None) -> list[Record]:
    defn = {s["id"]: s for s in run.defn["stages"]}
    try:
        _load_table(run)
    except Exception as exc:                                    # noqa: BLE001
        run.table, run.table_error = None, f"{type(exc).__name__}: {exc} ({_where(exc)})"
    out = [_guarded(_formula_version, run, defn["formula_version"]),
           _guarded(_herbs, run, defn["herbs"])]
    try:
        _acquire(run)
    except Exception as exc:                                    # noqa: BLE001
        run.data_status, run.data_reason = FAIL, f"{type(exc).__name__}: {exc} ({_where(exc)})"
    _research(run)
    for sid, fn in (("compounds", _compounds), ("targets", _targets),
                    ("measured_vs_predicted", _measured_vs_predicted), ("pathways", _pathways),
                    ("enrichment", _enrichment), ("literature", _literature),
                    ("evidence", _evidence), ("hypotheses", _hypotheses),
                    ("formula_distinction", _distinction)):
        out.append(_guarded(fn, run, defn[sid]))
    out.append(_artifacts(run, defn["artifacts"], repeat))
    return out


def run_benchmark(work: str | Path, *, raw: str | Path | None = None,
                  snapshot_root: str | Path | None = None, ledger: str | Path | None = None,
                  live: bool = False, repeat: bool = False, parameters: Any = None,
                  log: Callable[[str], None] = print, backend: Any = None,
                  definition_path: str | Path | None = None) -> tuple[dict[str, Any], list[Record]]:
    """Run the twelve stages; (the research_pipeline_report document, its records).

    ``raw`` holds the research raw files (default ``WORK/raw``); with ``live`` the missing
    ones are downloaded from their servers and the literature is queried. ``snapshot_root``
    and ``ledger`` use snapshots built elsewhere instead (their provenance is then
    UNVERIFIED). ``parameters`` replaces the pre-registered analysis parameters; the report
    records them. ``repeat`` runs everything but the downloads a second time, in a fresh
    state directory on the same snapshots, and compares the artifacts byte for byte.
    """
    work = Path(work).resolve()
    defn = definition(definition_path)
    raw_dir = Path(raw).resolve() if raw else work / "raw"

    def make(w: Path, snaps: Path | None, led: Path | None, is_live: bool) -> _Run:
        return _Run(w, defn, live=is_live, raw=raw_dir, snapshot_root=snaps, ledger=led,
                    parameters=parameters, log=log, backend=backend)

    t0 = time.perf_counter()
    first = make(work, Path(snapshot_root).resolve() if snapshot_root else None,
                 Path(ledger).resolve() if ledger else None, live)
    records = _stages(first)
    repro: list[Record] = []
    if repeat:
        again_dir = work / "repeat"
        shutil.rmtree(again_dir, ignore_errors=True)
        second = make(again_dir, first.snapshot_root, first.ledger, False)
        _stages(second)
        digests = {k: v["sha256"] for k, v in second.outputs.items()}
        records[-1] = _artifacts(first, {s["id"]: s for s in defn["stages"]}["artifacts"], digests)
        for sid, o in sorted(first.outputs.items()):
            r = Record(f"{COMPONENT}.{sid}", "reproducibility",
                       reproduce="bioagent validate benchmark --out OUT --repeat")
            if sid in _LIVE_STAGES:
                r.add("artifact_digest", NOT_APPLICABLE, "live answers change between requests")
            elif sid not in digests:
                r.add("artifact_digest", FAIL, "the repeat did not produce this artifact")
            else:
                same = digests[sid] == o["sha256"]
                r.add("artifact_digest", PASS if same else FAIL,
                      "identical on a second run" if same else "differs on a second run",
                      first=o["sha256"], second=digests[sid])
            repro.append(r)
    statuses = [r.status for r in records]
    completed = all(s == PASS for s in statuses)
    acceptance = {"met": FAIL not in statuses,
                  "how": "completed" if completed else "explained" if FAIL not in statuses else "not met",
                  "completed": completed,
                  "not_completed": [{"stage": r.component.rsplit(".", 1)[-1], "status": r.status,
                                     "why": r.failure_reason} for r in records if r.status != PASS],
                  "rule": defn["acceptance"]}
    from .environment import fingerprint
    doc = report("research_pipeline_report", records, tier="live" if live else "offline",
                 environment=fingerprint({"work": work}),
                 notes=["A stage is PASS only when it ran on verified data and its validation "
                        "held; a stage that could not run here says why and is never counted "
                        "as passed."],
                 extra={"benchmark": {"id": defn["id"], "version": defn["version"],
                                      "question": defn["question"], "question_zh": defn["question_zh"],
                                      "definition_digest": _digest(defn),
                                      "parameters": "pre-registered" if parameters is None else str(parameters),
                                      "acquisition": first.acquisition,
                                      "runtime_s": round(time.perf_counter() - t0, 1)},
                        "acceptance": acceptance})
    if repro:
        doc["reproducibility"] = report("scientific_reproducibility_report", repro,
                                        tier="live" if live else "offline")
    return doc, records


def write(doc: Mapping[str, Any], out: str | Path) -> list[Path]:
    """The report (and the reproducibility report, when repeated) under ``out``."""
    out = Path(out)
    doc = dict(doc)
    repro = doc.pop("reproducibility", None)
    paths = [write_report(out / "research_pipeline_report.json", doc)]
    if repro:
        paths.append(write_report(out / "scientific_reproducibility_report.json", repro))
    return paths


def summary_lines(records: Iterable[Record]) -> list[str]:
    return [f"{r.status:10} {r.component.split('.', 2)[-1]}: {r.failure_reason or ''}"[:300]
            for r in records]


def aggregate_status(records: Sequence[Record]) -> str:
    return aggregate(r.status for r in records)

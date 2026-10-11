"""The agent regression suite: twelve research scenarios, run through the dispatcher.

Each scenario is a task an agent is given in the Studio, written as the tool calls a correct
answer needs, in order, each step's result feeding the next. It states what it must produce,
the data it needs, how its result is checked (against references established outside the
code under test wherever one exists: a classical text, a standard table, SciPy) and the
limitations an answer may honestly end with. The calls go through
:func:`tcmstudio.dispatch.call`, exactly as an agent's do.

A step that cannot run here is classified the way the agent must classify it — the tool or
its engine is not here, the data are not, the network is off for this tier — and the
scenario stops there with that reason. Such a scenario is BLOCKED or SKIPPED, never PASS;
the outcome classes are the ones the Studio agent reads under each result
(``studio/web/js/core/agent.js``, ``outcomeOf``).
"""

from __future__ import annotations

import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

__all__ = ["SCENARIOS", "Scenario", "outcome_of", "run_scenarios"]

BERBERINE = "inchikey:YBHILYKTIRIUTE-UHFFFAOYSA-N"
COPTIS = "ncbitaxon:261450"                      # Coptis chinensis, a source species of 黄连

#: Monoisotopic residue masses (Da), the standard table (Unimod / NIST): independent of the
#: code that computes peptide masses.
_RESIDUE = {"G": 57.02146, "A": 71.03711, "S": 87.03203, "P": 97.05276, "V": 99.06841,
            "T": 101.04768, "C": 103.00919, "L": 113.08406, "I": 113.08406, "N": 114.04293,
            "D": 115.02694, "Q": 128.05858, "K": 128.09496, "E": 129.04259, "M": 131.04049,
            "H": 137.05891, "F": 147.06841, "R": 156.10111, "Y": 163.06333, "W": 186.07931}
_WATER, _PROTON = 18.010565, 1.007276


def outcome_of(env: Mapping[str, Any]) -> str | None:
    """The class of a result that is not an ordinary answer (agent.js ``outcomeOf``)."""
    err = env.get("error") or {}
    msg, kind, status = str(err.get("message") or ""), err.get("type"), env.get("status")
    if status in ("failed", "refused"):
        if kind == "not_found" and re.match(r"unknown tool", msg, re.I):
            return "tool_unavailable"
        if kind == "unavailable":
            return ("data_unavailable" if re.search(
                r"not built|no store|store is|import (it|a store)|not downloaded|no files", msg, re.I)
                else "tool_unavailable")
        if kind == "network_off":
            return "data_unavailable"
        return {"not_found": "no_record", "bad_arguments": "input_error", "refused": "not_permitted",
                "declined": "not_permitted"}.get(kind, "tool_failed")
    return None


@dataclass(frozen=True)
class Scenario:
    n: int
    id: str
    title: str
    task: str
    datasets: tuple[str, ...]
    expected: tuple[str, ...]
    criteria: tuple[str, ...]
    limitations: tuple[str, ...]
    run: Callable[["_Run"], None]

    def as_dict(self) -> dict[str, Any]:
        return {"n": self.n, "id": self.id, "title": self.title, "task": self.task,
                "datasets": list(self.datasets), "expected": list(self.expected),
                "criteria": list(self.criteria), "acceptable_limitations": list(self.limitations)}


class _Stop(Exception):
    """A step could not run; the scenario ends with its reason."""


class _Run:
    def __init__(self, rec: Any, ctx: Mapping[str, Any], tier: str) -> None:
        self.rec, self.ctx, self.tier = rec, dict(ctx), tier
        self.n = 0
        self.calls: list[dict[str, Any]] = []

    def step(self, tool: str, args: Mapping[str, Any], *, network: bool = False,
             optional: bool = False) -> dict[str, Any] | None:
        """Call ``tool``; its result, or None (and the scenario stops unless ``optional``)."""
        from bioagent.validation import BLOCKED, FAIL, PASS, SKIPPED, UNVERIFIED

        from .dispatch import call
        self.n += 1
        name = f"step {self.n}: {tool}"
        if network and self.tier != "live":
            self.rec.add(name, SKIPPED, "needs the network: run in the live tier")
            self.calls.append({"tool": tool, "arguments": dict(args), "status": "not sent"})
            if optional:
                return None
            raise _Stop
        t0 = time.perf_counter()
        env = call(tool, dict(args), {**self.ctx, "network": bool(network and self.tier == "live")})
        took = time.perf_counter() - t0
        cls = outcome_of(env)
        self.calls.append({"tool": tool, "arguments": dict(args), "status": env.get("status"),
                           "outcome": cls, "seconds": round(took, 2)})
        if env.get("status") == "succeeded":
            self.rec.add(name, PASS, env.get("summary_en") or env.get("summary") or "succeeded",
                         duration_s=took)
            return env
        if env.get("status") == "job_submitted":
            # accepted, not done: a job has no result until it has finished and been verified,
            # and this suite does not follow jobs
            self.rec.add(name, UNVERIFIED, f"submitted as job {(env.get('job') or {}).get('id')}; "
                         "this suite does not follow runner jobs, so nothing it computes is "
                         "checked here", duration_s=took)
            return env
        err = env.get("error") or {}
        status = (BLOCKED if cls in ("tool_unavailable", "data_unavailable", "not_permitted")
                  else FAIL)
        self.rec.add(name, status, f"{cls}: {err.get('message', '')}"[:400]
                     + (f" — {err['hint']}" if err.get("hint") else ""), outcome=cls,
                     duration_s=took)
        if optional:
            return None
        raise _Stop

    def expect(self, name: str, ok: bool, detail: str, **evidence: Any) -> bool:
        from bioagent.validation import FAIL, PASS
        self.rec.add(name, PASS if ok else FAIL, detail, **evidence)
        return ok

    def blocked(self, name: str, detail: str) -> None:
        from bioagent.validation import BLOCKED
        self.rec.add(name, BLOCKED, detail)
        raise _Stop

    def unverified(self, name: str, detail: str) -> None:
        from bioagent.validation import UNVERIFIED
        self.rec.add(name, UNVERIFIED, detail)


def _result(env: Mapping[str, Any] | None) -> dict[str, Any]:
    value = (env or {}).get("result")
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- the scenarios

def _formula_provenance(x: _Run) -> None:
    f = x.step("tcm_formula", {"name": "桂枝汤《伤寒论》"})
    records = (_result(f).get("repository_records") or {}).get("records") or []
    x.expect("one_version", len(records) == 1, f"{len(records)} workbook version(s) cite 《伤寒论》")
    rec = records[0] if records else {}
    drugs = sorted(c.get("drug") or "?" for c in rec.get("components") or ())
    # 《伤寒论》第12条: 桂枝三两（去皮）、芍药三两、甘草二两（炙）、生姜三两（切）、大枣十二枚（擘）
    x.expect("composition", drugs == ["baishao", "dazao", "gancao", "guizhi", "shengjiang"],
             f"crude drugs {drugs} against 《伤寒论》第12条 (桂枝、芍药、甘草、生姜、大枣)")
    passages = _result(f).get("passages") or []
    x.expect("classical_source", any("第12条" in str(p.get("chapter", "")) for p in passages)
             and "伤寒论" in str(rec.get("source")),
             f"source {rec.get('source')}; passages {[p.get('chapter') for p in passages]}")
    x.expect("licence_stated", any(lic.get("asset") == "repository_formulas"
                                   for lic in (f.get("governance") or {}).get("licences") or ()),
             "the workbook's unstated licence is carried with the answer")
    herbs = [c.get("name") for c in rec.get("components") or ()]
    c = x.step("tcm_compatibility", {"herbs": herbs})
    conflicts = [k for k in _result(c).get("conflicts") or () if k.get("kind") == "incompatibility"]
    x.expect("no_recorded_incompatibility", not conflicts,
             "no 十八反 or 十九畏 pair among the five herbs (absence of a record, not safety)"
             if not conflicts else f"conflicts: {conflicts}")


def _hub_rows(env: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    return list(_result(env).get("rows") or ())


def _context(row: Mapping[str, Any]) -> dict[str, Any]:
    """A hub row's context: stored as compact JSON (``tcmdb.rowkit.ctx``)."""
    c = row.get("context")
    if isinstance(c, str):
        try:
            c = json.loads(c)
        except ValueError:
            return {}
    return dict(c) if isinstance(c, Mapping) else {}


def _needs_built(x: _Run, env: Mapping[str, Any] | None, datasets: tuple[str, ...]) -> None:
    built = set(_result(env).get("built") or ())
    if not built & set(datasets):
        x.blocked("data", f"none of {', '.join(datasets)} is built in this hub (built: "
                          f"{sorted(built) or 'none'}): fetch and build them (job.tcmdb.fetch, "
                          "job.tcmdb.build) or run `bioagent validate data --live`, then pass "
                          "--tcmdb-root")


def _np_compounds(x: _Run) -> None:
    r = x.step("tcmdb.relations", {"kind": "organism_compound", "subject": COPTIS, "limit": 500})
    _needs_built(x, r, ("npass", "cmaup", "lotus"))
    rows = _hub_rows(r)
    hits = [row for row in rows if row.get("object_id") == BERBERINE]
    x.expect("marker_compound", bool(hits),
             f"berberine (YBHILYKTIRIUTE-UHFFFAOYSA-N, checked against PubChem) is among the "
             f"{len(rows)} constituents recorded for Coptis chinensis" if hits else
             f"berberine is not among the {len(rows)} constituents recorded for Coptis chinensis",
             sources=sorted({row.get("source") for row in hits}))
    x.expect("provenance", all(row.get("source") and (row.get("reference") or row.get("note"))
                               for row in hits) if hits else False,
             "each record names its source and its reference (a PMID or DOI, or the database it "
             "relays)", sources=sorted({row.get("source") for row in hits}))
    p = x.step("connector.pubchem.cid_by_name", {"name": "berberine"}, network=True, optional=True)
    if p is not None:
        cids = json.dumps(_result(p))
        x.expect("pubchem_identity", "2353" in cids, "PubChem resolves berberine to CID 2353")


def _compound_targets(x: _Run) -> None:
    r = x.step("tcmdb.relations", {"kind": "compound_target", "subject": BERBERINE, "limit": 500})
    _needs_built(x, r, ("npass", "cmaup"))
    rows = _hub_rows(r)
    if not rows:
        x.blocked("data", "no compound-target record of berberine in the built datasets")
    measured = [row for row in rows if row.get("evidence") != "predicted"]
    with_measure = [row for row in measured
                    if _context(row).get("measure") and _context(row).get("value") is not None]
    x.expect("measured", len(measured) == len(rows),
             f"{len(rows)} records, all measured activities" if len(measured) == len(rows) else
             f"{len(rows) - len(measured)} of {len(rows)} records are predictions")
    x.expect("measure_recorded", len(with_measure) == len(measured),
             f"{len(with_measure)} of {len(measured)} records carry the measurement (type and value)")
    x.expect("referenced", all(row.get("reference") for row in measured),
             "each measurement cites its publication (PMID or DOI)")
    x.expect("human_proteins", any(str(row.get("object_id", "")).startswith("uniprot:") for row in rows),
             "targets are identified as UniProt proteins")


def _mechanism_hypothesis(x: _Run) -> None:
    from bioagent.contracts.claim_language import overreaching_language
    h = x.step("tcm_network_hypothesis", {"formula_name": "桂枝汤"})
    claims = ((_result(h).get("artifact") or {}).get("claims")) or []
    limits = " ".join((h.get("governance") or {}).get("limitations") or ())
    over = [f.phrase for c in claims for f in overreaching_language(c.get("text", ""), c.get("claim_kind", ""))]
    x.expect("only_hypotheses", all(c.get("claim_kind") == "mechanism_hypothesis" for c in claims) and not over,
             f"{len(claims)} claims, each a mechanism hypothesis worded as one" if claims else
             "the seed corpus records no molecular target for 桂枝汤, and the skill makes no claim "
             "rather than inventing one")
    x.expect("seed_labelled", "Seed corpus only" in limits and ("no claim is made" in limits or claims),
             "the run says it is on the illustrative seed corpus and why it claims nothing")
    x.unverified("measured_hypotheses",
                 "hypotheses on measured targets come from the research loop on snapshots of the real "
                 "files: the Guizhi Decoction benchmark (bioagent validate benchmark --live) runs them")


def _enrichment(x: _Run) -> None:
    from scipy.stats import hypergeom
    genes = ["KCNH2", "SCN5A", "KCNQ1", "RYR2", "ATP2A2", "ADRB1"]
    sets = {"cardiac conduction (fixture)": ["KCNH2", "SCN5A", "KCNQ1", "KCNJ2", "CACNA1C", "RYR2",
                                             "ATP2A2", "GJA1"],
            "glycolysis (fixture)": ["HK1", "PFKM", "ALDOA", "GAPDH", "PKM", "ENO1"],
            "adrenergic (fixture)": ["ADRB1", "ADRB2", "ADRA1A", "GNAS"]}
    e = x.step("native.enrichment_analysis", {"genes": genes, "gene_sets": sets, "background": 20000})
    rows = {r.get("term") or r.get("name") or r.get("set"): r
            for r in (_result(e).get("results") or _result(e).get("terms") or ())}
    ok, worst = True, 0.0
    for name, members in sets.items():
        k = len(set(genes) & set(members))
        if k == 0:
            continue
        expected = float(hypergeom.sf(k - 1, 20000, len(members), len(genes)))
        got = (rows.get(name) or {}).get("p_value")
        if got is None:
            ok = False
            continue
        rel = abs(got - expected) / max(expected, 1e-300)
        worst = max(worst, rel)
        ok = ok and rel < 1e-6
    x.expect("p_values", ok and bool(rows), f"each set's p equals SciPy's hypergeometric survival "
                                            f"function (worst relative error {worst:.1e})")
    h = x.step("native.hypergeometric_test", {"overlap": 5, "query_size": 6, "set_size": 8, "background": 20000})
    p = _result(h).get("p_value")
    ref = float(hypergeom.sf(4, 20000, 8, 6))
    x.expect("hypergeometric", p is not None and abs(p - ref) / ref < 1e-6,
             f"P(X ≥ 5) = {p} against SciPy {ref:.6g}")


def _protein_sequence(x: _Run) -> None:
    dna = "GATCGTGTGTATATTCATCCGTTT"                  # codes for angiotensin II, DRVYIHPF
    t = x.step("native.translate", {"sequence": dna})
    protein = str(_result(t).get("protein") or _result(t).get("translation") or "").rstrip("*")
    x.expect("translation", protein == "DRVYIHPF", f"translated {protein!r} (standard code: DRVYIHPF)")
    m = x.step("native.peptide_mass", {"sequence": protein or "DRVYIHPF", "charges": [1, 2]})
    mono = sum(_RESIDUE[a] for a in "DRVYIHPF") + _WATER
    got = _result(m).get("monoisotopic_mass") or _result(m).get("monoisotopic")
    x.expect("monoisotopic_mass", got is not None and abs(float(got) - mono) < 2e-3,
             f"{got} Da against {mono:.4f} from the standard residue table")
    mz2 = (_result(m).get("mz") or {}).get("2")
    x.expect("charge_states", mz2 is not None and abs(float(mz2) - (mono + 2 * _PROTON) / 2) < 2e-3,
             f"[M+2H]2+ {mz2} against {(mono + 2 * _PROTON) / 2:.4f}")
    p = x.step("native.protein_properties", {"sequence": protein or "DRVYIHPF"})
    x.expect("length", _result(p).get("length") == 8, f"length {_result(p).get('length')}")


def _engine_scenario(skill: str, args: Mapping[str, Any], where_checked: str) -> Callable[[_Run], None]:
    """An engine that runs as a runner job the user approves. Whether it is installed is
    asked the way the agent asks (capabilities_status); when it is not, the skill is called
    and the dispatcher's own refusal, with its remedy, is the result. When it is, nothing is
    submitted: the validator approves no job on a person's behalf, and the engine's reference
    run is the CI job named."""
    def run(x: _Run) -> None:
        from .catalog import entries

        heavy = next((e.get("heavy") or [] for e in entries("runner", probe=False)
                      if e["id"] == skill), [])
        have = _result(x.step("capabilities_status", {})).get("dependencies") or {}
        if heavy and all(have.get(d) for d in heavy):
            x.unverified("reference_run", f"{', '.join(heavy)} are installed here; a run is a "
                                          f"runner job the user approves, and its reference run "
                                          f"is {where_checked}")
            return
        if x.step(skill, args, optional=True) is None:
            raise _Stop
        x.unverified("reference_run", f"the engine answered; its reference run is {where_checked}")
    return run


def _transcriptomics(x: _Run) -> None:
    from scipy.stats import ttest_ind
    a = [5.1, 5.3, 4.9, 5.6, 5.2]
    b = [6.8, 7.1, 6.5, 7.4, 6.9]
    w = x.step("native.welch_t_test", {"x": a, "y": b})
    ref = ttest_ind(a, b, equal_var=False)
    got = _result(w)
    x.expect("welch", abs(float(got.get("t", math.nan)) - ref.statistic) < 1e-6
             and abs(float(got.get("p_value", math.nan)) - ref.pvalue) < 1e-6,
             f"t {got.get('t')}, p {got.get('p_value')} against SciPy t {ref.statistic:.6g}, "
             f"p {ref.pvalue:.6g}")
    # the pipelines run as jobs on the runner; here, whether their packages import and compute
    from bioagent.validation.dependencies import load_environments, readiness
    env = load_environments()
    group = {"groups": {k: v for k, v in (env.get("groups") or {}).items() if k == "transcriptomics"}}
    rec, = readiness(group) or [None]
    if rec is None:
        x.unverified("pipeline_packages", "no transcriptomics group in the environments file")
        return
    from bioagent.validation import PASS
    if rec.status == PASS:
        x.unverified("pipeline_reference", "the pipelines' packages import and compute here; their "
                                           "reference comparisons are CI's analysis-tools job")
    else:
        x.blocked("pipeline_packages", f"the transcriptomics packages are not ready here "
                                       f"({rec.failure_reason}); provision them with job.env.provision "
                                       f"(group transcriptomics): {rec.reproduce}")


def _literature(x: _Run) -> None:
    lit = x.step("literature_search", {"query": "\"Guizhi decoction\" palpitation", "source": "europepmc",
                                       "limit": 10}, network=True)
    res = _result(lit)
    hits = res.get("hits")
    x.expect("records_identified", isinstance(hits, list)
             and all(h.get("pmid") or h.get("doi") or h.get("pmcid") for h in hits),
             f"{len(hits or [])} of {res.get('total')} records returned, each with a PMID, PMCID or DOI"
             + ("; none matched, which is not evidence that none exists" if not hits else ""))
    t = x.step("literature_search", {"query": "Guizhi", "source": "clinicaltrials", "limit": 10},
               network=True)
    trials = _result(t).get("hits")
    x.expect("trials_identified", isinstance(trials, list)
             and all(str(h.get("nct") or "").startswith("NCT") for h in trials),
             f"{len(trials or [])} registered studies, each with its NCT id")


def _evidence_synthesis(x: _Run) -> None:
    a = x.step("tcm_applicability", {"subject": "桂枝汤", "object": "太阳中风证", "claim_kind": "attribution"})
    x.expect("attribution_licensed", _result(a).get("licensed") is True,
             "《伤寒论》第12条 licenses the attribution 桂枝汤主之 太阳中风")
    e = x.step("tcm_applicability", {"subject": "桂枝汤", "object": "太阳中风证", "claim_kind": "efficacy"})
    x.expect("efficacy_not_licensed", _result(e).get("licensed") is False,
             "a classical record does not license an efficacy claim (that needs a trial)")
    s = x.step("tcm_safety_report", {"subject": "甘草"})
    text = json.dumps(_result(s), ensure_ascii=False)
    found = [h for h in ("甘遂", "大戟", "海藻", "芫花") if h in text]
    x.expect("eighteen_incompatibilities", len(found) == 4,
             f"十八反 (藻戟遂芫俱战草): {found} named against 甘草")


def _integration(x: _Run) -> None:
    r = x.step("tcmdb.consensus", {"kind": "organism_compound", "object": BERBERINE, "limit": 500})
    built = set(_result(r).get("sources") or ())
    probe = x.step("tcmdb.relations", {"kind": "organism_compound", "object": BERBERINE, "limit": 1})
    _needs_built(x, probe, ("npass", "cmaup", "lotus"))
    support = json.dumps(_result(r), ensure_ascii=False)
    agree = [s for s in ("npass", "cmaup", "lotus") if s in support]
    x.expect("sources_agree", len(agree) >= 2,
             f"berberine in its source species is recorded by {agree}: the sources meet on one "
             "InChIKey", sources=sorted(built))


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(1, "formula_provenance", "TCM formula composition and provenance",
             "What is in 桂枝汤 as 《伤寒论》 gives it, where is it recorded, and do its herbs carry "
             "a recorded incompatibility?",
             ("the formula workbook", "the curated seed corpus"),
             ("the 《伤寒论》 version among the workbook's versions", "its five crude drugs",
              "the classical passage", "the compatibility check"),
             ("composition equals 《伤寒论》第12条", "the source cites 《伤寒论》",
              "no 十八反/十九畏 pair, reported as absence of a record, not safety"),
             ("the workbook's origin and licence are not stated",), _formula_provenance),
    Scenario(2, "np_compound_identification", "Natural-product compound identification",
             "Which constituents do the databases record for 黄连's source species, and is its "
             "marker compound berberine among them?",
             ("NPASS, CMAUP or LOTUS built in the data hub", "PubChem (live tier)"),
             ("the constituents recorded for Coptis chinensis", "berberine by its InChIKey",
              "its PubChem CID"),
             ("berberine (YBHILYKTIRIUTE-UHFFFAOYSA-N) is among them", "every record names its source",
              "PubChem gives CID 2353"),
             ("species-level composition, not the decoction",), _np_compounds),
    Scenario(3, "compound_target_retrieval", "Compound-protein target retrieval",
             "Which human proteins has berberine been measured against?",
             ("NPASS or CMAUP built in the data hub",),
             ("measured activities with type, value and unit", "UniProt targets"),
             ("no prediction is reported as a measurement", "every record carries its measurement"),
             ("an assay potency is not an effect in the body",), _compound_targets),
    Scenario(4, "mechanism_hypothesis", "Molecular mechanism hypothesis generation",
             "Propose mechanism hypotheses for 桂枝汤, labelled as such.",
             ("the curated seed corpus (governed skill)", "research snapshots for measured data"),
             ("hypotheses of kind mechanism_hypothesis, or no claim where no target is recorded",
              "the corpus they rest on, named"),
             ("kind mechanism_hypothesis", "no overreaching wording", "no claim without a target",
              "the seed corpus labelled"),
             ("on the seed corpus it is a demonstration; on measured data it is the benchmark",),
             _mechanism_hypothesis),
    Scenario(5, "pathway_enrichment", "Pathway enrichment analysis",
             "Is a set of measured targets over-represented in a pathway?",
             (), ("over-representation p-values", "a hypergeometric test"),
             ("p-values equal SciPy's hypergeometric survival function",),
             ("the gene sets here are fixtures; the benchmark runs Reactome",), _enrichment),
    Scenario(6, "protein_sequence", "Protein sequence analysis",
             "Translate a coding sequence and give the peptide's mass and properties.",
             (), ("the translation", "monoisotopic mass and charge states", "length"),
             ("DRVYIHPF (angiotensin II) by the standard code", "mass equals the standard residue table"),
             (), _protein_sequence),
    Scenario(7, "molecular_docking", "Molecular docking",
             "Dock a ligand into a receptor and report the poses.",
             ("AutoDock Vina, meeko, RDKit, gemmi (env.provision group docking)",),
             ("poses and scores", "the engine's version"),
             ("redocking reproduces the crystal pose (CI's molecular-tools job)",),
             ("needs a provisioned docking environment",),
             # trypsin and its co-crystal benzamidine: the redocking CI's job checks
             _engine_scenario("skill.dock-ligands", {"receptor": "PDB:3PTB", "ligands": "NC(=N)c1ccccc1",
                                                     "site_ligand": "BEN", "allow_remote": True},
                              "CI's molecular-tools job (tests/test_docking.py, redocking a crystal pose)")),
    Scenario(8, "admet", "Drug-likeness and ADMET evaluation",
             "Predict ADMET properties for a molecule.",
             ("RDKit, scikit-learn (env.provision group admet)",),
             ("properties per molecule", "the model's applicability domain"),
             ("models trained from an archive with known answers (CI's molecular-tools job)",),
             ("needs a provisioned ADMET environment",),
             _engine_scenario("skill.predict-admet", {"molecules": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"},
                              "CI's molecular-tools job (tests/test_admet.py)")),
    Scenario(9, "transcriptomics", "Transcriptomic and single-cell data analysis",
             "Test a gene's expression between two groups; run the single-cell atlas.",
             ("Scanpy and the analysis extras for the single-cell pipeline",),
             ("Welch's t statistic and p-value", "the cell atlas"),
             ("t and p equal SciPy's", "the pipelines' reference comparisons (CI's analysis-tools job)"),
             ("the pipelines run as runner jobs and need their optional packages",),
             _transcriptomics),
    Scenario(10, "clinical_literature", "Clinical literature and trial retrieval",
             "Find publications on 桂枝汤 and palpitations, and registered trials of it.",
             ("web access to www.ebi.ac.uk and clinicaltrials.gov",),
             ("records with PMIDs or DOIs", "studies with NCT ids"),
             ("every record is identified", "an empty answer is reported as no record, not absence"),
             ("records are retrieved, not appraised",), _literature),
    Scenario(11, "evidence_synthesis", "Evidence synthesis and structured reporting",
             "What may and may not be claimed for 桂枝汤 and 太阳中风证, and what does 甘草 conflict with?",
             ("the curated seed corpus",),
             ("the licence of each claim kind", "the 十八反 records for 甘草"),
             ("attribution licensed by 《伤寒论》第12条", "efficacy not licensed by a classical record",
              "甘遂, 大戟, 海藻, 芫花 named (十八反)"),
             ("a record's absence is not safety",), _evidence_synthesis),
    Scenario(12, "cross_database_integration", "Cross-database knowledge integration",
             "Do NPASS, CMAUP and LOTUS agree on where berberine occurs?",
             ("two or more of NPASS, CMAUP, LOTUS built in the data hub",),
             ("the sources that record berberine in its species", "one identity (InChIKey) across them"),
             ("two or more sources agree",), ("the sources use different curation",), _integration),
)


def run_scenarios(ctx: Mapping[str, Any], *, tier: str = "offline",
                  log: Callable[[str], None] = lambda m: None) -> list[Any]:
    """One validation record per scenario."""
    from bioagent.validation import Record

    out = []
    for s in SCENARIOS:
        rec = Record(f"scenario.{s.n:02d}.{s.id}", "scenario", "", input=s.as_dict(),
                     reproduce=f"tcmstudio validate --out OUT --tier {tier}")
        x = _Run(rec, ctx, tier)
        t0 = time.perf_counter()
        try:
            s.run(x)
        except _Stop:
            pass
        except Exception as exc:                                    # noqa: BLE001
            rec.add("scenario", "FAIL", f"{type(exc).__name__}: {exc}")
        rec.runtime_s = time.perf_counter() - t0
        rec.provenance = {"calls": x.calls}
        log(f"scenario {s.n:2d} {s.id}: {rec.status}")
        out.append(rec)
    return out

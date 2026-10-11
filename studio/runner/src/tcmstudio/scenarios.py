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

The engines (docking, ADMET, the RNA-seq and single-cell pipelines) run as runner jobs a
person approves. With an operator's approval (``tcmstudio validate --approve-jobs``) the
scenarios are run against tcmstudio's own runner over HTTP (``validate_jobs.AgentRunner``):
files are uploaded as a user uploads them, ``run_pipeline`` submits the job, ``job_status``
follows it to the end, and the finished result is checked against a reference established
outside the code: the crystal pose of benzamidine in trypsin (PDB 3PTB), molecular weights
from IUPAC atomic weights and polar surface areas as PubChem gives them, TDC's split sizes,
the genes planted in a simulated experiment, and the composition of human blood. Without
the approval no job is submitted, and those checks are UNVERIFIED with the reason.
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
    def __init__(self, rec: Any, ctx: Mapping[str, Any], tier: str, *, runner: Any = None,
                 jobs_timeout_s: float = 3600.0) -> None:
        self.rec, self.ctx, self.tier = rec, dict(ctx), tier
        #: tcmstudio's runner over HTTP, when the operator approved jobs (validate_jobs)
        self.runner = runner
        self.jobs_timeout_s = jobs_timeout_s
        self.n = 0
        self.calls: list[dict[str, Any]] = []

    def upload(self, path: Any, name: str | None = None) -> dict[str, Any]:
        """A file added to the runner as a user adds one (``POST /api/uploads``)."""
        meta = self.runner.upload(path, name=name)
        self.calls.append({"upload": meta.get("name"), "id": meta.get("id"),
                           "bytes": meta.get("size") or meta.get("bytes")})
        return meta

    def step(self, tool: str, args: Mapping[str, Any], *, network: bool = False,
             optional: bool = False, job: bool = False) -> dict[str, Any] | None:
        """Call ``tool``; its result, or None (and the scenario stops unless ``optional``).
        A job (``job``) is followed with ``job_status`` until it has finished, and its
        result is the finished job's."""
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
        if self.runner is not None:
            from .validate_jobs import approvals_for
            approvals = approvals_for(tool, args, job=job,
                                      network=bool(network and self.tier == "live"))
            env = self.runner.call(tool, dict(args), approvals=approvals)
        else:
            approvals = []
            env = call(tool, dict(args), {**self.ctx,
                                          "network": bool(network and self.tier == "live")})
        took = time.perf_counter() - t0
        cls = outcome_of(env)
        self.calls.append({"tool": tool, "arguments": dict(args), "status": env.get("status"),
                           "outcome": cls, "seconds": round(took, 2),
                           **({"approvals": approvals} if approvals else {})})
        if env.get("status") == "succeeded":
            self.rec.add(name, PASS, env.get("summary_en") or env.get("summary") or "succeeded",
                         duration_s=took)
            return env
        if env.get("status") == "job_submitted":
            job_id = (env.get("job") or {}).get("id")
            if self.runner is None:
                # accepted, not done: a job has no result until it has finished and been
                # verified, and without an approved runner this suite does not follow jobs
                self.rec.add(name, UNVERIFIED, f"submitted as job {job_id}; without "
                             "--approve-jobs this suite does not follow runner jobs, so nothing "
                             "it computes is checked here", duration_s=took)
                return env
            return self._follow(name, str(job_id), approvals, took, optional)
        err = env.get("error") or {}
        status = (BLOCKED if cls in ("tool_unavailable", "data_unavailable", "not_permitted")
                  else FAIL)
        self.rec.add(name, status, f"{cls}: {err.get('message', '')}"[:400]
                     + (f" — {err['hint']}" if err.get("hint") else ""), outcome=cls,
                     duration_s=took)
        if optional:
            return None
        raise _Stop

    def _follow(self, name: str, job_id: str, approvals: list[str], took: float,
                optional: bool) -> dict[str, Any] | None:
        """``job_status`` until the job has finished; the step is the job's outcome."""
        from bioagent.validation import BLOCKED, FAIL, PASS

        t0 = time.perf_counter()
        polls = [0]

        def count(_env: Mapping[str, Any]) -> None:
            polls[0] += 1
        env = self.runner.follow(job_id, timeout_s=self.jobs_timeout_s, on_poll=count)
        job = _result(env)
        waited = time.perf_counter() - t0
        state = job.get("state")
        self.calls.append({"tool": "job_status", "job_id": job_id, "polls": polls[0],
                           "state": state, "seconds": round(waited, 2)})
        evidence = {"job_id": job_id, "kind": job.get("kind"), "state": state,
                    "approvals": approvals, "artefacts": [a.get("name") or a.get("path")
                                                          for a in job.get("artefacts") or ()
                                                          if isinstance(a, Mapping)],
                    "duration_s": took + waited}
        if state == "succeeded":
            self.rec.add(name, PASS, f"job {job_id} ({job.get('kind')}) succeeded and its "
                         "outputs were verified by the runner", **evidence)
            return env
        if state is None:
            err = env.get("error") or {}
            self.rec.add(name, FAIL, f"job {job_id}: job_status answered "
                         f"{env.get('status')}: {err.get('message', '')}"[:400], **evidence)
        elif state in ("queued", "running"):
            self.rec.add(name, BLOCKED, f"job {job_id} was still {state} after "
                         f"{self.jobs_timeout_s:.0f} s (--jobs-timeout)", **evidence)
        else:
            outcome = job.get("outcome") or {}
            self.rec.add(name, FAIL, f"job {job_id} {state}: "
                         f"{outcome.get('error') or outcome}"[:600], **evidence)
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


# ------------------------------------------------------------ the engines, as approved jobs
def _job_result(env: Mapping[str, Any] | None) -> dict[str, Any]:
    """What a finished job computed: the job kind's own result, as job_status gives it."""
    value = _result(env).get("result")
    return value if isinstance(value, dict) else {}


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _pocket() -> Any:
    """Trypsin's S1 pocket with its co-crystal benzamidine (PDB 3PTB, CC0; bioagent's data)."""
    from importlib.resources import files
    from pathlib import Path
    return Path(str(files("bioagent.data") / "3ptb_pocket.pdb"))


def _crystal_centroid(pdb: Any, resname: str) -> tuple[float, ...]:
    """The centroid of a co-crystal ligand's heavy atoms, read from the PDB coordinates."""
    xyz = [(float(line[30:38]), float(line[38:46]), float(line[46:54]))
           for line in pdb.read_text().splitlines()
           if line.startswith("HETATM") and line[17:20].strip() == resname
           and line[76:78].strip() != "H"]
    return tuple(sum(c[i] for c in xyz) / len(xyz) for i in range(3)) if xyz else ()


#: benzamidine is 3PTB's co-crystal ligand; 4-methylbenzamidine is a trypsin inhibitor of
#: the same class; octane has no amidine to anchor at Asp189 in the S1 pocket
_DOCK_LIGANDS = (("benzamidine", "NC(=N)c1ccccc1"), ("4-methylbenzamidine", "Cc1ccc(cc1)C(N)=N"),
                 ("octane", "CCCCCCCC"))


def _docking(x: _Run) -> None:
    if x.runner is None:
        return _DOCK_REFERENCE(x)
    import tempfile
    from pathlib import Path
    pocket = _pocket()
    with tempfile.TemporaryDirectory(prefix="dock-") as tmp:
        ligands = Path(tmp) / "ligands.csv"
        ligands.write_text("name,smiles\n" + "".join(f"{n},{s}\n" for n, s in _DOCK_LIGANDS),
                           encoding="utf-8")
        receptor, table = x.upload(pocket), x.upload(ligands)
    env = x.step("run_pipeline", {"pipeline": "dock", "arguments": {
        "receptor": receptor["id"], "ligands": table["id"], "site_ligand": "BEN",
        "site_ligand_smiles": _DOCK_LIGANDS[0][1], "seed": 42}}, job=True)
    res = _job_result(env)
    v = res.get("validation") or {}
    rmsd = _float(v.get("top_pose_rmsd"))
    x.expect("redocking", bool(v.get("passed")) and rmsd <= 2.0,
             f"benzamidine redocked {rmsd:.2f} Å from its crystal pose in 3PTB (threshold "
             "2.0 Å): the setup reproduces the experiment before any score is read",
             validation={k: v.get(k) for k in ("top_pose_rmsd", "best_rmsd_in_top3",
                                               "top_score", "threshold", "passed")})
    centre = [(_float(c)) for c in (res.get("box") or {}).get("center") or ()]
    crystal = _crystal_centroid(pocket, "BEN")
    off = math.dist(centre, crystal) if len(centre) == 3 and crystal else math.inf
    x.expect("site", off < 1.5, f"the box is centred {off:.2f} Å from benzamidine's crystal "
                                "centroid, computed here from the PDB coordinates",
             box=res.get("box"), crystal_centroid=crystal)
    scores = {row.get("ligand"): _float(row.get("best_score"))
              for row in res.get("scores.tsv") or ()}
    amidines = [scores.get(n, math.nan) for n, _ in _DOCK_LIGANDS[:2]]
    x.expect("ranking", all(a < scores.get("octane", math.nan) for a in amidines),
             f"best scores (kcal/mol) {scores}: both amidines, which anchor at Asp189, score "
             "better than octane, which cannot", scores=scores)
    versions = res.get("versions") or {}
    x.expect("engines_recorded", all(versions.get(k) not in (None, "", "unknown")
                                     for k in ("vina", "meeko", "rdkit")),
             f"engine versions recorded with the run: {versions}")


#: molecular formulas (for weights from IUPAC standard atomic weights), topological polar
#: surface areas summed by hand from Ertl et al. 2000's fragment contributions, and
#: Lipinski violations by the rule's four limits. Aspirin: two carbonyl O (17.07 each), the
#: ester O (9.23), the acid OH (20.23). Caffeine: two carbonyl O, three N-methylated
#: aromatic N (4.93 each), one pyridine-type aromatic N (12.89). (PubChem's 58.4 Å² for
#: caffeine is another program's reading of the same method.)
_ADMET_MOLECULES = {
    "aspirin": ("CC(=O)Oc1ccccc1C(=O)O", {"C": 9, "H": 8, "O": 4},
                2 * 17.07 + 9.23 + 20.23, 0),
    "caffeine": ("Cn1cnc2c1c(=O)n(C)c(=O)n2C", {"C": 8, "H": 10, "N": 4, "O": 2},
                 2 * 17.07 + 3 * 4.93 + 12.89, 0),
    # 563 g/mol and logP far above 5: two of Lipinski's four limits broken
    "tetracontane": ("C" * 40, {"C": 40, "H": 82}, 0.0, 2),
}
_ATOMIC_WEIGHT = {"C": 12.011, "H": 1.008, "N": 14.007, "O": 15.999}
#: TDC's ADMET group: the scaffold split's sizes and its leaderboard metric, with the least
#: a model built here must reach on the held-out scaffolds (the documented build reaches
#: 0.28 and 0.92; TDC's simplest descriptor baselines reach about 0.39 and 0.97)
_TDC = {"caco2_wang": ("MAE", 728, 182, lambda s: s <= 0.40, "MAE ≤ 0.40"),
        "hia_hou": ("AUROC", 461, 117, lambda s: s >= 0.85, "AUROC ≥ 0.85")}


def _admet(x: _Run) -> None:
    if x.runner is None:
        return _ADMET_REFERENCE(x)
    import tempfile
    from pathlib import Path
    live = x.tier == "live"
    with tempfile.TemporaryDirectory(prefix="admet-") as tmp:
        table = Path(tmp) / "molecules.csv"
        table.write_text("name,smiles\n" + "".join(f"{n},{m[0]}\n"
                                                   for n, m in _ADMET_MOLECULES.items()),
                         encoding="utf-8")
        molecules = x.upload(table)
    args: dict[str, Any] = {"molecules": molecules["id"]}
    if live:
        # the models are trained here from TDC's archive (Harvard Dataverse, pinned SHA-256)
        args.update(build_models=True, endpoints=sorted(_TDC))
    env = x.step("run_pipeline", {"pipeline": "admet", "arguments": args}, job=True,
                 network=live)
    res = _job_result(env)
    rows = {r.get("name"): r for r in res.get("admet.tsv") or ()}
    for name, (_smiles, formula, tpsa, lipinski) in _ADMET_MOLECULES.items():
        row = rows.get(name) or {}
        weight = sum(_ATOMIC_WEIGHT[e] * n for e, n in formula.items())
        got = _float(row.get("MolWt"))
        x.expect(f"{name}_weight", abs(got - weight) < 0.06,
                 f"{got} g/mol against {weight:.3f} from the IUPAC atomic weights")
        got = _float(row.get("TPSA"))
        x.expect(f"{name}_tpsa", abs(got - tpsa) < 0.06,
                 f"TPSA {got} Å² against {tpsa:.2f} from Ertl's fragment contributions")
        got = row.get("lipinski_violations")
        x.expect(f"{name}_lipinski", str(got) == str(lipinski),
                 f"{got} Lipinski violation(s); {lipinski} by the rule's four limits")
    if not live:
        x.unverified("models", "the endpoint models are trained from TDC's archive in the live "
                               "tier (build_models); offline only the descriptors, rules and "
                               "alerts are computed, and no endpoint is predicted")
        return
    models = res.get("models") or {}
    for endpoint, (metric, train, test, good, floor) in _TDC.items():
        card = models.get(endpoint) or {}
        score = _float(card.get("test_score"))
        x.expect(f"{endpoint}_split", (card.get("train"), card.get("test")) == (train, test)
                 and str(card.get("metric") or "").upper() == metric,
                 f"{card.get('train')}/{card.get('test')} molecules, {card.get('metric')}: TDC's "
                 f"scaffold split of {endpoint} is {train}/{test}, scored by {metric}")
        x.expect(f"{endpoint}_held_out", good(score),
                 f"held-out {metric} {score:.4g} on scaffolds the model never saw ({floor})",
                 archive_sha256=card.get("archive_sha256"))


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
    if x.runner is not None:
        _rnaseq(x)
        _pbmc(x)
        return
    if rec.status == PASS:
        x.unverified("pipeline_reference", "the pipelines' packages import and compute here; "
                                           "their runs as approved jobs are `tcmstudio validate "
                                           "--approve-jobs` (CI's agent-engines workflow)")
    else:
        x.blocked("pipeline_packages", f"the transcriptomics packages are not ready here "
                                       f"({rec.failure_reason}); provision them with job.env.provision "
                                       f"(group transcriptomics): {rec.reproduce}")


def _rnaseq(x: _Run) -> None:
    """FASTQ to differential expression on a simulated experiment whose answer is planted:
    genes G000-G003 four times higher in the treated samples, G004-G006 four times lower."""
    import csv
    import io
    import tempfile
    from pathlib import Path

    from bioagent.omics.reference_world import DOWN, UP, make_world
    with tempfile.TemporaryDirectory(prefix="rnaseq-") as tmp:
        world = make_world(Path(tmp) / "world", seed=1, paired=True)
        for fq in sorted(world.root.glob("*.fastq.gz")):
            x.upload(fq)
        sheet, tx, gtf = x.upload(world.sheet), x.upload(world.transcripts), x.upload(world.gtf)
    env = x.step("run_pipeline", {"pipeline": "rnaseq", "arguments": {
        "samples": sheet["id"], "transcripts": tx["id"], "annotation": gtf["id"],
        "contrast": "condition,treated,control"}}, job=True)
    job_id = str(_result(env).get("id"))
    summary = _job_result(env)
    try:
        text = x.runner.file(job_id, "deseq2_results.tsv").decode("utf-8")
    except RuntimeError as exc:
        x.expect("results_table", False, f"the results table could not be read: {exc}")
        return
    rows = list(csv.DictReader(io.StringIO(text), dialect="excel-tab"))
    called = {r["gene_id"]: _float(r.get("log2_fold_change")) for r in rows
              if _float(r.get("p_adjusted")) < 0.05}
    up = [g for g in UP if called.get(g, 0.0) > 0]
    down = [g for g in DOWN if called.get(g, 0.0) < 0]
    extra = sorted(set(called) - set(UP) - set(DOWN))
    x.expect("planted_up", len(up) == len(UP),
             f"{len(up)} of the {len(UP)} genes planted four times higher are called up "
             f"(padj < 0.05, quantified by {summary.get('engine') or 'the pipeline'}): {up}",
             engine=summary.get("engine"))
    x.expect("planted_down", len(down) == len(DOWN),
             f"{len(down)} of the {len(DOWN)} genes planted four times lower are called down: "
             f"{down}")
    # planted four-fold: log2 fold change 2; with three libraries a side its standard error
    # is about 0.3, so an estimate more than 1 away is not the planted effect
    off = {g: round(called[g], 2) for g in (*UP, *DOWN)
           if g in called and abs(abs(called[g]) - 2.0) > 1.0}
    x.expect("fold_changes", not off and len(up) + len(down) == len(UP) + len(DOWN),
             "each planted gene's log2 fold change is within 1 of the planted 2"
             if not off else f"estimates far from the planted log2 fold change 2: {off}",
             log2_fold_changes={g: round(called.get(g, math.nan), 3) for g in (*UP, *DOWN)})
    # Benjamini-Hochberg at 5 % bounds the expected share of false calls, not one run's: in
    # simulated worlds like this one a run calls 0 or 2 unplanted genes, so 2 is the bound
    x.expect("false_discoveries", len(extra) <= 2,
             f"{len(extra)} gene(s) called that were not planted: {extra} (of "
             f"{len(called)} called among {len(rows)} tested)", tested=len(rows),
             engine=summary.get("engine"))


#: Zenodo record of 10x Genomics' PBMC datasets re-aligned with Cell Ranger 9 (filtered
#: gene-barcode matrices); its files and their MD5 sums are read from the record itself
_PBMC_RECORD = "14868136"
_LINEAGE = {"T": ("Naive CD4+ T cell", "Memory CD4+ T cell", "CD8+ T cell",
                  "Regulatory T cell"),
            "NK": ("NK cell",), "B": ("B cell", "Plasma cell"),
            "monocyte": ("CD14+ monocyte", "FCGR3A+ monocyte")}
#: cell types that do not circulate: none belongs in blood
_TISSUE = ("Epithelial cell", "Endothelial cell", "Fibroblast", "Smooth muscle cell", "Pericyte",
           "Hepatocyte", "Keratinocyte", "Cardiomyocyte", "Neuron", "Astrocyte",
           "Oligodendrocyte", "Microglia", "Adipocyte")
#: share of PBMC in healthy adults (flow cytometry: T 45-70 %, B 5-15 %, NK 5-20 %, monocytes
#: 10-30 %), widened for what clustering and marker annotation of 1,000 cells can resolve
_PBMC_SHARE = {"T": (0.25, 0.85), "NK": (0.02, 0.35), "B": (0.02, 0.30),
               "monocyte": (0.04, 0.45)}


def _pbmc(x: _Run) -> None:
    """A real single-cell dataset: 10x Genomics' 1k PBMC from a healthy donor."""
    import re as _re
    import tempfile
    import urllib.request
    from pathlib import Path

    from bioagent.validation import SKIPPED
    if x.tier != "live":
        x.rec.add("pbmc_1k", SKIPPED, "downloads 10x Genomics' PBMC 1k from Zenodo: the live tier")
        return
    try:
        req = urllib.request.Request(f"https://zenodo.org/api/records/{_PBMC_RECORD}",
                                     headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:            # noqa: S310
            record = json.loads(r.read().decode("utf-8"))
    except Exception as exc:                                        # noqa: BLE001
        x.blocked("pbmc_record", f"Zenodo record {_PBMC_RECORD} did not answer: "
                                 f"{type(exc).__name__}: {exc}")
    files = record.get("files") or []
    pick = next((f for f in files if _re.search(r"pbmc_?1k.*filtered_feature_bc_matrix\.h5$",
                                                 str(f.get("key")), _re.I)), None)
    if pick is None:
        x.blocked("pbmc_record", f"record {_PBMC_RECORD} has no PBMC 1k filtered matrix; it "
                                 f"holds {[f.get('key') for f in files][:12]}")
    from bioagent.acquisition.downloader import Downloader, DownloadError
    with tempfile.TemporaryDirectory(prefix="pbmc-") as tmp:
        try:
            got = Downloader(tmp, timeout_s=300).fetch(
                pick["links"]["self"], pick["key"], checksum=pick.get("checksum"))
        except (DownloadError, OSError) as exc:
            x.blocked("pbmc_download", f"{pick['key']}: {exc}")
        x.expect("pbmc_download", got.verified,
                 f"{pick['key']}, {got.bytes} bytes, matches the record's {pick.get('checksum')}")
        source = x.upload(Path(tmp) / pick["key"])
    env = x.step("run_pipeline", {"pipeline": "scrna", "arguments": {
        "source": source["id"], "integration": "none", "doublets": "remove", "seed": 0}},
        job=True)
    summary = _job_result(env)
    cells = int(summary.get("cells") or 0)
    sizes = {str(k): int(v) for k, v in (summary.get("clusters") or {}).items()}
    types = {str(k): str(v) for k, v in (summary.get("cell_types") or {}).items()}
    x.expect("cells", 700 <= cells <= 1600,
             f"{cells} cells kept after QC and doublet removal (the dataset is about 1,000 cells)")
    share = {lin: sum(n for c, n in sizes.items() if types.get(c) in names) / max(cells, 1)
             for lin, names in _LINEAGE.items()}
    tissue = sum(n for c, n in sizes.items() if types.get(c) in _TISSUE) / max(cells, 1)
    x.expect("lineages", all(share[lin] > 0 for lin in _LINEAGE),
             "T, NK, B and monocyte clusters are all found: "
             + ", ".join(f"{k} {v:.0%}" for k, v in share.items()), cell_types=types)
    x.expect("no_tissue_types", tissue <= 0.05,
             f"{tissue:.1%} of the cells are called a cell type that does not circulate")
    out = {k: v for k, v in share.items()
           if not _PBMC_SHARE[k][0] <= v <= _PBMC_SHARE[k][1]}
    x.expect("composition", not out,
             "each lineage's share is within the range for healthy adult blood"
             if not out else f"outside the healthy range: {out} (ranges {_PBMC_SHARE})")


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


#: without an approved runner: whether the engine is installed, asked the way the agent
#: asks; the run itself is `tcmstudio validate --approve-jobs`
_DOCK_REFERENCE = _engine_scenario(
    "skill.dock-ligands", {"receptor": "PDB:3PTB", "ligands": "NC(=N)c1ccccc1",
                           "site_ligand": "BEN", "allow_remote": True},
    "`tcmstudio validate --approve-jobs` (CI's agent-engines workflow: redocking 3PTB as a "
    "runner job)")
_ADMET_REFERENCE = _engine_scenario(
    "skill.predict-admet", {"molecules": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C"},
    "`tcmstudio validate --approve-jobs` (CI's agent-engines workflow)")


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
             "Dock benzamidine and two other ligands into trypsin's S1 pocket and report the poses.",
             ("AutoDock Vina, meeko, RDKit, gemmi (env.provision group docking)",
              "trypsin's pocket with benzamidine, PDB 3PTB (shipped, CC0)"),
             ("poses and scores", "the redocking check", "the engines' versions"),
             ("run as a runner job the operator approved, followed with job_status",
              "redocked benzamidine within 2.0 Å of its crystal pose",
              "the box centred on the crystal ligand", "both amidines score better than octane"),
             ("needs a provisioned docking environment and an approval to run a job",),
             _docking),
    Scenario(8, "admet", "Drug-likeness and ADMET evaluation",
             "Predict ADMET properties for aspirin, caffeine and a C40 alkane.",
             ("RDKit, scikit-learn (env.provision group admet)",
              "TDC's ADMET group from Harvard Dataverse (live tier)"),
             ("properties and rules per molecule", "held-out scores of the models built"),
             ("run as a runner job the operator approved, followed with job_status",
              "weights equal IUPAC atomic weights; TPSA equals Ertl's fragment sums",
              "Lipinski: 0, 0 and 2 violations",
              "live: TDC's split sizes, Caco-2 MAE ≤ 0.40, HIA AUROC ≥ 0.85"),
             ("needs a provisioned ADMET environment and an approval to run a job; models in "
              "the live tier",),
             _admet),
    Scenario(9, "transcriptomics", "Transcriptomic and single-cell data analysis",
             "Test a gene's expression between two groups; find the differentially expressed "
             "genes of an experiment from its FASTQ files; annotate a blood sample's cells.",
             ("a simulated RNA-seq experiment with planted genes",
              "10x Genomics' PBMC 1k from Zenodo (live tier)"),
             ("Welch's t statistic and p-value", "differential expression", "the cell atlas"),
             ("t and p equal SciPy's",
              "run as runner jobs the operator approved, followed with job_status",
              "the 7 planted genes called in their direction near the planted four-fold, "
              "at most 2 genes that were not",
              "live: T, NK, B and monocytes in healthy proportions, no tissue cell types"),
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
                  log: Callable[[str], None] = lambda m: None, runner: Any = None,
                  only: Any = None, jobs_timeout_s: float = 3600.0) -> list[Any]:
    """One validation record per scenario (``only``: these numbers). With ``runner``
    (``validate_jobs.AgentRunner``, the operator's approval) every call goes to tcmstudio's
    runner over HTTP and jobs are submitted and followed."""
    from bioagent.validation import Record

    out = []
    for s in SCENARIOS:
        if only and s.n not in set(only):
            continue
        rec = Record(f"scenario.{s.n:02d}.{s.id}", "scenario", "", input=s.as_dict(),
                     reproduce=f"tcmstudio validate --out OUT --tier {tier}"
                               + (" --approve-jobs" if runner is not None else ""))
        x = _Run(rec, ctx, tier, runner=runner, jobs_timeout_s=jobs_timeout_s)
        t0 = time.perf_counter()
        try:
            s.run(x)
        except _Stop:
            pass
        except Exception as exc:                                    # noqa: BLE001
            rec.add("scenario", "FAIL", f"{type(exc).__name__}: {exc}")
        rec.runtime_s = time.perf_counter() - t0
        rec.provenance = {"calls": x.calls}
        if runner is not None:
            # the approval every job here ran on: the operator's, on the command line
            rec.provenance["jobs_approved_by"] = "the operator (tcmstudio validate --approve-jobs)"
        log(f"scenario {s.n:2d} {s.id}: {rec.status}")
        out.append(rec)
    return out

"""Does a network-pharmacology result hold up? The checks the October 2026 audit asked for.

Each check re-runs the unchanged analysis (``run_network_pharmacology``) on a changed
input and compares the result with the original:

* **Formula variants** (拆方, 加减, 炮制, 剂量). A herb removed takes its own compounds
  and the targets only they reach out of the analysis; a herb added brings its own in.
  Dose and processing are recorded but not modelled, so they must change nothing. Each
  variant is checked against what it should change, identity first.
* **Background and thresholds.** The analysis on both backgrounds, and over a grid of
  potency cut-offs and pathway sizes: a pathway significant only in a narrow window of
  settings is reported as such.
* **Hub dependence.** The targets most compounds reach are removed one, then several
  at a time: a pathway carried by one hub falls with it.
* **Negative controls.** Random herb combinations of the formula's size, drawn from the
  herbs whose compounds the snapshots record. A pathway that random formulas reach as
  often as this one is not specific to it.
* **Network-structure control.** The compound-target edges rewired by degree-preserving
  swaps: every compound keeps its number of targets and every target its number of
  compounds, but the wiring is random. A pathway still enriched in the rewired networks
  is explained by degree structure, not by which compounds hit which proteins.
* **Reproducibility.** A bundle naming the snapshots by id and digest, the formula, the
  parameters, the seed, the code and the result digest; :func:`verify_reproduction`
  reloads the snapshots and re-runs the analysis to the same digest.

Derived inputs are never passed off as data. A herb layer built for variants or
controls is a snapshot of its own. A rewired or hub-removed snapshot carries the
original id with ``+derived:<what>`` appended, so it cannot be mistaken for a source.
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

__all__ = ["DEFAULT_SCAN", "background_sensitivity", "compare_results", "herb_layer",
           "hub_dependence", "leave_one_herb_out", "pathway_verdicts",
           "random_formula_control",
           "render_markdown", "reproducibility_bundle", "rewired_control", "scope_statement",
           "threshold_scan", "validate", "variant_study", "verify_reproduction",
           "with_dose", "with_herb", "with_processing", "without_herb"]

#: The settings a threshold scan covers by default, each crossed with the other: the
#: potency cut-off from ten-fold below the 10 µM default to three-fold above it (100 µM
#: would count what most assays call inactive), and the smallest pathway tested.
DEFAULT_SCAN: Mapping[str, Sequence[Any]] = {
    "activity_max_nm": (1_000.0, 3_000.0, 10_000.0, 30_000.0),
    "pathway_min_size": (3, 5, 10),
}


def _run(snapshots: Sequence[Any], formula: Any, params: Any) -> Any:
    from ..analysis.network_pharmacology import run_network_pharmacology
    return run_network_pharmacology(snapshots, formula=formula, params=params)


def _significant(result: Any, fdr: float, alpha: float) -> set[str]:
    return {r["pathway"] for r in result.enrichment
            if r.get("q_value") is not None and r["q_value"] <= fdr
            and r.get("empirical_p") is not None and r["empirical_p"] <= alpha}


def _jaccard(a: set, b: set) -> float:
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def _spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) < 3:
        return None

    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2.0
            i = j + 1
        return out

    rx, ry = ranks(x), ranks(y)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    sxy = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    sy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return round(sxy / (sx * sy), 6) if sx and sy else None


def compare_results(base: Any, other: Any, params: Any) -> dict[str, Any]:
    """What changed between two results: compounds, targets and significant pathways."""
    bc, oc = {c["compound"] for c in base.compounds}, {c["compound"] for c in other.compounds}
    bt, ot = {t["target"] for t in base.targets}, {t["target"] for t in other.targets}
    bs = _significant(base, params.fdr, params.permutation_alpha)
    os_ = _significant(other, params.fdr, params.permutation_alpha)
    bq = {r["pathway"]: r["q_value"] for r in base.enrichment}
    oq = {r["pathway"]: r["q_value"] for r in other.enrichment}
    common = sorted(set(bq) & set(oq))
    rho = _spearman([-math.log10(max(bq[p], 1e-300)) for p in common],
                    [-math.log10(max(oq[p], 1e-300)) for p in common])
    return {"compounds_lost": sorted(bc - oc), "compounds_gained": sorted(oc - bc),
            "targets_lost": sorted(bt - ot), "targets_gained": sorted(ot - bt),
            "significant_lost": sorted(bs - os_), "significant_gained": sorted(os_ - bs),
            "significant_jaccard": round(_jaccard(bs, os_), 6),
            "q_rank_correlation": rho, "pathways_compared": len(common)}


# ------------------------------------------------------------------ formula variants

def _short(herb_id: str) -> str:
    return herb_id.rsplit(".", 1)[-1]


def without_herb(base: Any, herb_id: str) -> Any:
    """拆方: the formula with one herb removed, as a formula version of its own."""
    if herb_id not in {c[0] for c in base.components}:
        raise ValueError(f"{herb_id} is not a component of {base.id}")
    return replace(base, id=f"{base.id}~without~{_short(herb_id)}",
                   chinese=f"{base.chinese}去{_short(herb_id)}",
                   components=tuple(c for c in base.components if c[0] != herb_id),
                   record_ids=(), written=())


def with_herb(base: Any, herb_id: str, *, role: str = "佐", dose: str = "",
              processing: str = "") -> Any:
    """加减: the formula with one herb added."""
    if herb_id in {c[0] for c in base.components}:
        raise ValueError(f"{herb_id} is already a component of {base.id}")
    return replace(base, id=f"{base.id}~with~{_short(herb_id)}",
                   chinese=f"{base.chinese}加{_short(herb_id)}",
                   components=(*base.components, (herb_id, role, dose, processing)),
                   record_ids=(), written=())


def _with_component(base: Any, herb_id: str, kind: str, index: int, value: str) -> Any:
    if herb_id not in {c[0] for c in base.components}:
        raise ValueError(f"{herb_id} is not a component of {base.id}")
    comps = tuple(tuple(v if i != index else value for i, v in enumerate(c))
                  if c[0] == herb_id else c for c in base.components)
    return replace(base, id=f"{base.id}~{kind}~{_short(herb_id)}", components=comps,
                   chinese=f"{base.chinese}({_short(herb_id)}{kind})",
                   record_ids=(), written=())


def with_processing(base: Any, herb_id: str, processing: str) -> Any:
    """炮制: one herb's processing changed."""
    return _with_component(base, herb_id, "processing", 3, processing)


def with_dose(base: Any, herb_id: str, dose: str) -> Any:
    """剂量: one herb's dose changed."""
    return _with_component(base, herb_id, "dose", 2, dose)


def leave_one_herb_out(base: Any) -> list[Any]:
    return [without_herb(base, c[0]) for c in base.components]


def herb_layer(formulas: Sequence[Any], *, root: str | Path,
               drugs: Mapping[str, Any] | None = None) -> Any:
    """A herb-layer snapshot holding ``formulas`` (the variants and controls) of its own."""
    from ..sources import herbs as herb_module
    from ..sources.herbs import HERBS, KEY, herb_rows
    from ..sources.snapshot import build_snapshot
    from ..contracts.source_card import canonical_hash
    if drugs is None:
        from ..sources.materia import all_drugs
        drugs = {**all_drugs(), **HERBS}
    nodes, edges = herb_rows(list(formulas), drugs=drugs)
    tag = canonical_hash(sorted(f.fingerprint for f in formulas))[:10]
    licences = "; ".join(sorted({f.license for f in formulas if f.license}))
    return build_snapshot(
        key=KEY, version=f"validation+{tag}", nodes=nodes, edges=edges,
        raw_files={"herbs.py": Path(herb_module.__file__)},
        parser="studies.validation.herb_layer", root=Path(root), license=licences,
        citation="derived for formula-variant and negative-control checks; each formula "
                 "keeps its own composition record")


def _replace_layer(snapshots: Sequence[Any], layer: Any) -> list[Any]:
    return [layer if s.key == layer.key else s for s in snapshots]


def _herbs_of(result: Any) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for row in result.compounds:
        for herb in row.get("herbs", []):
            out.setdefault(herb, set()).add(row["compound"])
    return out


def _identity(kind: str, herb: str, base: Any, other: Any, change: Mapping[str, Any]
              ) -> tuple[bool, str]:
    """Whether a variant changed what it should, and nothing it should not."""
    if kind == "without":
        mine = {r["compound"] for r in base.compounds if set(r.get("herbs", [])) == {herb}}
        still = {r["compound"] for r in other.compounds if herb in r.get("herbs", [])}
        reached_only = {t["target"] for t in base.targets
                        if set(t.get("compounds", [])) <= mine and t.get("compounds")}
        kept_targets = {t["target"] for t in other.targets}
        if still:
            return False, f"compounds still attributed to the removed {herb}: {sorted(still)}"
        if not mine <= set(change["compounds_lost"]):
            return False, f"{herb}'s own compounds are still analysed"
        if reached_only & kept_targets:
            return False, ("targets reached only through the removed herb are still "
                           f"analysed: {sorted(reached_only & kept_targets)}")
        return True, (f"{len(mine)} compound(s) found only in {herb} and "
                      f"{len(reached_only)} target(s) only they reach left the analysis")
    if kind == "with":
        if change["compounds_lost"]:
            return False, f"adding {herb} removed compounds {change['compounds_lost']}"
        added = _herbs_of(other).get(herb, set())
        return True, (f"{herb} contributes {len(added)} compound(s) the snapshots record"
                      if added else f"the snapshots record no compound for {herb}; the "
                      "analysis is unchanged, which is what an empty herb should do")
    # dose and processing are recorded, not modelled: nothing may move
    moved = [k for k in ("compounds_lost", "compounds_gained", "targets_lost",
                         "targets_gained", "significant_lost", "significant_gained")
             if change[k]]
    if moved:
        return False, (f"a {kind} change moved {moved}; the analysis does not model "
                       f"{kind}, so a change here is a defect")
    return True, (f"unchanged, as it must be: {kind} is recorded with the formula but not "
                  "modelled by this analysis")


def _same_substance(a: Any, b: Any) -> bool:
    def rows(result: Any) -> list[tuple]:
        return sorted((r["pathway"], r.get("p_value"), r.get("q_value"), r.get("empirical_p"))
                      for r in result.enrichment)
    return ({c["compound"] for c in a.compounds} == {c["compound"] for c in b.compounds}
            and {t["target"] for t in a.targets} == {t["target"] for t in b.targets}
            and rows(a) == rows(b))


def variant_study(snapshots: Sequence[Any], base: Any, variants: Sequence[tuple[str, str, Any]],
                  params: Any, *, root: str | Path) -> dict[str, Any]:
    """Run each ``(kind, herb, variant)`` and check it against what it should change.

    ``kind`` is ``without``, ``with``, ``dose`` or ``processing``.
    """
    layer = herb_layer([base, *(v for _, _, v in variants)], root=root)
    snaps = _replace_layer(snapshots, layer)
    original = _run(snapshots, base, params)
    rebuilt = _run(snaps, base, params)
    # The derived layer has an id of its own, so the two results' digests always differ;
    # what must agree is their substance.
    consistent = {} if _same_substance(original, rebuilt) else compare_results(
        original, rebuilt, params)
    rows = []
    for kind, herb, variant in variants:
        result = _run(snaps, variant, params)
        change = compare_results(rebuilt, result, params)
        ok, why = _identity(kind, herb, rebuilt, result, change)
        rows.append({"variant": variant.id, "chinese": variant.chinese, "kind": kind,
                     "herb": herb, "fingerprint": variant.fingerprint,
                     "identity_ok": ok, "identity": why, **change})
    return {"herb_layer": layer.snapshot_id, "base": base.id,
            "base_reproduced_on_derived_layer": not consistent,
            "base_differences": consistent, "variants": rows,
            "all_identity_ok": all(r["identity_ok"] for r in rows)}


# ------------------------------------------------------------------ sensitivity

def background_sensitivity(snapshots: Sequence[Any], formula: Any, params: Any
                           ) -> dict[str, Any]:
    runs = {bg: _run(snapshots, formula, replace(params, background=bg))
            for bg in ("assayed", "reactome")}
    sig = {bg: sorted(_significant(r, params.fdr, params.permutation_alpha))
           for bg, r in runs.items()}
    return {"significant": sig,
            "both": sorted(set(sig["assayed"]) & set(sig["reactome"])),
            "jaccard": round(_jaccard(set(sig["assayed"]), set(sig["reactome"])), 6),
            "comparison": compare_results(runs["assayed"], runs["reactome"], params)}


def threshold_scan(snapshots: Sequence[Any], formula: Any, params: Any, *,
                   grid: Mapping[str, Sequence[Any]] = DEFAULT_SCAN) -> dict[str, Any]:
    """The analysis over every combination of ``grid``; how often each pathway holds.

    A pathway's frequency counts only the settings in which it was tested: a size range
    that excludes it says nothing about whether it is enriched."""
    names = sorted(grid)
    settings: list[dict[str, Any]] = [{}]
    for name in names:
        settings = [{**s, name: v} for s in settings for v in grid[name]]
    runs = []
    for setting in settings:
        result = _run(snapshots, formula, replace(params, **setting))
        runs.append({"setting": setting,
                     "significant": sorted(_significant(result, params.fdr,
                                                        params.permutation_alpha)),
                     "tested": sorted(r["pathway"] for r in result.enrichment),
                     "targets": len(result.targets)})
    seen = sorted({p for r in runs for p in r["significant"]})
    frequency, tested_in = {}, {}
    for p in seen:
        where = [r for r in runs if p in r["tested"]]
        tested_in[p] = len(where)
        frequency[p] = round(sum(p in r["significant"] for r in where) / len(where), 6)
    return {"grid": {k: list(v) for k, v in grid.items()}, "runs": runs,
            "frequency": frequency, "tested_in": tested_in}


def hub_dependence(snapshots: Sequence[Any], formula: Any, params: Any, *,
                   remove: Sequence[int] = (1, 3)) -> dict[str, Any]:
    """Remove the targets most of the formula's compounds reach and re-run."""
    base = _run(snapshots, formula, params)
    ranked = sorted(base.targets, key=lambda t: (-len(t.get("compounds", [])), t["target"]))
    base_sig = _significant(base, params.fdr, params.permutation_alpha)
    rows = []
    for k in remove:
        hubs = {t["target"] for t in ranked[:k]}
        if not hubs:
            continue
        derived = []
        for snap in snapshots:
            kept = tuple(e for e in snap.edges
                         if not (e.get("predicate") in ("targets", "tested_against")
                                 and e.get("object") in hubs))
            derived.append(snap if len(kept) == len(snap.edges) else replace(
                snap, snapshot_id=f"{snap.snapshot_id}+derived:without-top{k}-hubs",
                edges=kept))
        result = _run(derived, formula, params)
        sig = _significant(result, params.fdr, params.permutation_alpha)
        rows.append({"removed": sorted(hubs), "top": k, "significant": sorted(sig),
                     "lost": sorted(base_sig - sig)})
    return {"hubs_ranked_by": "number of the formula's compounds that reach the target",
            "runs": rows}


# ------------------------------------------------------------------ controls

def _species_with_compounds(snapshots: Sequence[Any]) -> set[str]:
    return {e["subject"] for s in snapshots for e in s.edges
            if e.get("predicate") == "contains" and str(e.get("subject", "")).startswith(
                "ncbitaxon:")}


def random_formula_control(snapshots: Sequence[Any], formula: Any, params: Any, *,
                           n: int = 50, seed: int = 20261007, root: str | Path,
                           drugs: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Random herb combinations of the formula's size, from herbs the data can analyse."""
    from ..sources.herbs import HERBS, FormulaVersion
    if drugs is None:
        from ..sources.materia import all_drugs
        drugs = {**all_drugs(), **HERBS}
    with_data = _species_with_compounds(snapshots)
    members = {c[0] for c in formula.components}
    pool = sorted(h for h, d in drugs.items() if h not in members
                  and any(f"ncbitaxon:{s.taxid}" in with_data for s in d.species))
    k = len(formula.components)
    if len(pool) < k:
        return {"performed": False, "pool": len(pool), "needed": k,
                "why": "too few herbs outside the formula have recorded compounds to draw "
                       "random combinations of its size"}
    rng = random.Random(seed)
    controls = [FormulaVersion(f"tcm:formula.control.random.{i:03d}", f"随机对照{i:03d}",
                               "studies.validation negative control",
                               tuple((h, "", "", "") for h in sorted(rng.sample(pool, k))))
                for i in range(n)]
    layer = herb_layer([formula, *controls], root=root, drugs=drugs)
    snaps = _replace_layer(snapshots, layer)
    real = _significant(_run(snaps, formula, params), params.fdr, params.permutation_alpha)
    counts, reached = [], {p: 0 for p in real}
    for control in controls:
        sig = _significant(_run(snaps, control, params), params.fdr, params.permutation_alpha)
        counts.append(len(sig))
        for p in real & sig:
            reached[p] += 1
    at_least = sum(c >= len(real) for c in counts)
    return {"performed": True, "controls": n, "pool": len(pool), "herbs_per_control": k,
            "seed": seed, "herb_layer": layer.snapshot_id,
            "real_significant": len(real), "control_significant": counts,
            "empirical_p": round((1 + at_least) / (1 + n), 6),
            "specificity": {p: round(reached[p] / n, 6) for p in sorted(real)}}


#: The compound-protein edges a rewiring moves: measured targets and, for screening
#: data, the tests that found nothing, so an active result stays on a tested pair.
REWIRED = ("targets", "tested_against")


def _rewire(edges: Sequence[Mapping[str, Any]], rng: random.Random, swaps: int
            ) -> list[dict[str, Any]]:
    """Degree-preserving swaps: two edges exchange their proteins, so every compound keeps
    its number of edges and every protein its number of compounds. A swap that would put
    the same compound on the same protein twice is skipped."""
    from collections import Counter
    edges = [dict(e) for e in edges]
    present = Counter((e["subject"], e["object"]) for e in edges)
    n = len(edges)
    if n < 2:
        return edges
    for _ in range(swaps):
        a, b = edges[rng.randrange(n)], edges[rng.randrange(n)]
        if a["subject"] == b["subject"] or a["object"] == b["object"]:
            continue
        if present[(a["subject"], b["object"])] or present[(b["subject"], a["object"])]:
            continue
        present[(a["subject"], a["object"])] -= 1
        present[(b["subject"], b["object"])] -= 1
        a["object"], b["object"] = b["object"], a["object"]
        present[(a["subject"], a["object"])] += 1
        present[(b["subject"], b["object"])] += 1
    return edges


def rewired_control(snapshots: Sequence[Any], formula: Any, params: Any, *, n: int = 20,
                    seed: int = 20261007, swaps_per_edge: int = 10) -> dict[str, Any]:
    """The analysis on compound-target networks rewired with every degree kept."""
    base = _significant(_run(snapshots, formula, params), params.fdr,
                        params.permutation_alpha)
    rng = random.Random(seed)
    counts, reached = [], {p: 0 for p in base}
    for i in range(n):
        derived = []
        for snap in snapshots:
            target_edges = [e for e in snap.edges if e.get("predicate") in REWIRED]
            if not target_edges:
                derived.append(snap)
                continue
            rewired = _rewire(target_edges, rng, swaps_per_edge * len(target_edges))
            others = [e for e in snap.edges if e.get("predicate") not in REWIRED]
            derived.append(replace(snap, edges=tuple(others + rewired),
                                   snapshot_id=f"{snap.snapshot_id}+derived:rewired-{i}"))
        sig = _significant(_run(derived, formula, params), params.fdr,
                           params.permutation_alpha)
        counts.append(len(sig))
        for p in base & sig:
            reached[p] += 1
    at_least = sum(c >= len(base) for c in counts)
    return {"rewirings": n, "seed": seed, "swaps_per_edge": swaps_per_edge,
            "real_significant": len(base), "rewired_significant": counts,
            "empirical_p": round((1 + at_least) / (1 + n), 6),
            "still_enriched": {p: round(reached[p] / n, 6) for p in sorted(base)}}


# ------------------------------------------------------------------ scope

def scope_statement(result: Any, snapshots: Sequence[Any]) -> dict[str, Any]:
    """What the result is about, and what it says nothing about."""
    used = {(t["target"], c) for t in result.targets for c in t.get("compounds", [])}
    designs: dict[str, int] = {}
    for snap in snapshots:
        for e in snap.edges:
            if e.get("predicate") == "targets" and (e.get("object"), e.get("subject")) in used:
                d = str(e.get("study_design") or "unstated")
                designs[d] = designs.get(d, 0) + 1
    return {"model": designs, "population": "none: no human data enters the analysis",
            "tissue": "none: target measurements are not tissue-resolved",
            "exposure": "not modelled: doses and concentrations reached in the body are not "
                        "used; a measured potency says what a compound can do in an assay",
            "endpoint": "pathway membership of measured targets, not a clinical outcome",
            "status": "computable candidate: a hypothesis about mechanism for experimental "
                      "follow-up, not a validated conclusion"}


# ------------------------------------------------------------------ reproducibility

def reproducibility_bundle(result: Any, snapshots: Sequence[Any], formula: Any, *,
                           out_dir: str | Path, ledger: Any = None) -> Path:
    """Everything needed to recompute ``result``, by id and digest."""
    from ..environment import environment_record
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    snaps = []
    for s in sorted(snapshots, key=lambda s: s.snapshot_id):
        if "+derived" in s.snapshot_id:
            raise ValueError(f"{s.snapshot_id} is derived; a bundle names only real data")
        content = dict(s.manifest.get("content") or {})
        snaps.append({"key": s.key, "version": s.version, "snapshot_id": s.snapshot_id,
                      "raw_files": content.get("raw_files", {}),
                      "tables": content.get("tables", {}),
                      "license": content.get("license", ""),
                      "citation": content.get("citation", ""),
                      "parser": content.get("parser", "")})
    bundle = {
        "schema": 1, "formula": {"id": formula.id, "chinese": formula.chinese,
                                 "source": formula.source,
                                 "components": [list(c) for c in formula.components],
                                 "license": formula.license,
                                 "primary_source": formula.primary_source,
                                 "record_ids": list(formula.record_ids),
                                 "written": list(formula.written),
                                 "fingerprint": formula.fingerprint},
        "parameters": asdict(result.parameters), "snapshots": snaps,
        "ledger_head": ledger.head() if ledger is not None else "",
        "code_digest": result.code_digest, "environment": environment_record(),
        "result_digest": result.digest(),
        "rebuild": ("load each snapshot by key, version and id (sources.snapshot."
                    "load_snapshot re-hashes every table), then re-run "
                    "run_network_pharmacology with this formula and these parameters; "
                    "studies.validation.verify_reproduction does both"),
    }
    path = out / "reproduce.json"
    path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False, sort_keys=True,
                               default=str), encoding="utf-8")
    return path


def verify_reproduction(bundle_path: str | Path, snapshot_root: str | Path, *,
                        ledger: Any = None) -> tuple[bool, str]:
    """Reload the bundle's snapshots and re-run: the same result digest, or why not."""
    from ..analysis.network_pharmacology import Parameters
    from ..sources.herbs import FormulaVersion
    from ..sources.snapshot import SnapshotError, load_snapshot
    bundle = json.loads(Path(bundle_path).read_text(encoding="utf-8"))
    f = bundle["formula"]
    formula = FormulaVersion(f["id"], f["chinese"], f["source"],
                             tuple(tuple(c) for c in f["components"]), license=f["license"],
                             primary_source=f["primary_source"],
                             record_ids=tuple(f["record_ids"]), written=tuple(f["written"]))
    if formula.fingerprint != f["fingerprint"]:
        return False, "the formula in the bundle does not match its fingerprint"
    snaps = []
    for s in bundle["snapshots"]:
        try:
            snaps.append(load_snapshot(snapshot_root, s["key"], s["version"],
                                       expected_id=s["snapshot_id"], ledger=ledger))
        except (SnapshotError, FileNotFoundError) as exc:
            return False, f"snapshot {s['snapshot_id']} cannot be reloaded: {exc}"
    params = Parameters(**{k: tuple(v) if isinstance(v, list) else v
                           for k, v in bundle["parameters"].items()})
    again = _run(snaps, formula, params)
    if again.digest() != bundle["result_digest"]:
        return False, (f"re-running gives {again.digest()[:23]}, the bundle records "
                       f"{bundle['result_digest'][:23]}")
    return True, f"re-ran to the recorded result digest {bundle['result_digest'][:23]}"


# ------------------------------------------------------------------ the suite

def validate(snapshots: Sequence[Any], formula: Any, params: Any, *, root: str | Path,
             variants: Sequence[tuple[str, str, Any]] | None = None,
             controls: int = 50, rewirings: int = 20, hubs: Sequence[int] = (1, 3),
             grid: Mapping[str, Sequence[Any]] = DEFAULT_SCAN,
             seed: int = 20261007) -> dict[str, Any]:
    """Every check, and a verdict per pathway the analysis found significant."""
    snaps = list(snapshots)
    base = _run(snaps, formula, params)
    significant = sorted(_significant(base, params.fdr, params.permutation_alpha))
    if variants is None:
        variants = [("without", c[0], without_herb(formula, c[0]))
                    for c in formula.components]
    report: dict[str, Any] = {
        "formula": formula.id, "fingerprint": formula.fingerprint,
        "parameters": asdict(params), "result_digest": base.digest(),
        "significant": significant,
        "variants": variant_study(snaps, formula, variants, params, root=root),
        "background": background_sensitivity(snaps, formula, params),
        "thresholds": threshold_scan(snaps, formula, params, grid=grid),
        "hubs": hub_dependence(snaps, formula, params, remove=hubs),
        "random_formulas": random_formula_control(snaps, formula, params, n=controls,
                                                  seed=seed, root=root),
        "rewired": rewired_control(snaps, formula, params, n=rewirings, seed=seed),
        "scope": scope_statement(base, snaps),
    }
    verdicts = pathway_verdicts(report)
    report["verdicts"] = verdicts
    report["identity"] = report["variants"]["all_identity_ok"]
    if not (report["random_formulas"].get("performed")):
        report.setdefault("not_performed", []).append(
            "random-formula control: " + report["random_formulas"]["why"])
    return report


#: How often a pathway may turn up in random formulas or rewired networks and still
#: count as specific to the formula, and the share of threshold settings it must hold in.
SPECIFICITY_LIMIT = 0.05
THRESHOLD_SHARE = 0.75


def pathway_verdicts(report: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Robust, or the list of checks a significant pathway failed."""
    out = {}
    for p in report["significant"]:
        failed = []
        if p not in report["background"]["both"]:
            failed.append("not significant on both backgrounds")
        if report["thresholds"]["frequency"].get(p, 0.0) < THRESHOLD_SHARE:
            failed.append("significant in under three quarters of the threshold settings "
                          "that tested it")
        rf = report["random_formulas"]
        if rf.get("performed") and rf["specificity"].get(p, 0.0) > SPECIFICITY_LIMIT:
            failed.append(f"random formulas reach it too ({rf['specificity'][p]:.0%})")
        still = report["rewired"]["still_enriched"].get(p, 0.0)
        if still > SPECIFICITY_LIMIT:
            failed.append(f"still enriched after degree-preserving rewiring ({still:.0%})")
        if any(p in run["lost"] for run in report["hubs"]["runs"] if run["top"] == 1):
            failed.append("lost when the single most-reached target is removed")
        out[p] = {"robust": not failed, "failed": failed}
    return out


def render_markdown(report: Mapping[str, Any]) -> str:
    """The report for a reader: verdicts first, then each check."""
    lines = [f"# Validation of {report['formula']}", "",
             f"Result digest `{report['result_digest']}`; "
             f"{len(report['significant'])} significant pathway(s).", ""]
    lines += ["## Verdicts", "", "| Pathway | Robust | What failed |", "| --- | --- | --- |"]
    for p, v in report["verdicts"].items():
        lines.append(f"| {p} | {'yes' if v['robust'] else 'no'} | "
                     f"{'; '.join(v['failed']) or '–'} |")
    var = report["variants"]
    lines += ["", "## Formula variants", "",
              f"Base reproduced on the derived herb layer: "
              f"{'yes' if var['base_reproduced_on_derived_layer'] else 'NO'}.", "",
              "| Variant | Kind | Identity | Compounds −/+ | Significant −/+ |",
              "| --- | --- | --- | --- | --- |"]
    for r in var["variants"]:
        lines.append(f"| {r['chinese']} | {r['kind']} | "
                     f"{'ok' if r['identity_ok'] else 'FAILED'}: {r['identity']} | "
                     f"{len(r['compounds_lost'])}/{len(r['compounds_gained'])} | "
                     f"{len(r['significant_lost'])}/{len(r['significant_gained'])} |")
    bg = report["background"]
    lines += ["", "## Background", "",
              f"Assayed: {len(bg['significant']['assayed'])} significant; whole annotation: "
              f"{len(bg['significant']['reactome'])}; Jaccard {bg['jaccard']}.", ""]
    lines += ["## Thresholds", "", "| Pathway | Share of settings significant |",
              "| --- | ---: |"]
    for p, f in sorted(report["thresholds"]["frequency"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {p} | {f:.0%} |")
    lines += ["", "## Hubs", ""]
    for run in report["hubs"]["runs"]:
        lines.append(f"- without the top {run['top']} target(s) {run['removed']}: lost "
                     f"{run['lost'] or 'nothing'}")
    rf = report["random_formulas"]
    lines += ["", "## Random formulas", ""]
    if rf.get("performed"):
        lines.append(f"{rf['controls']} random combinations of {rf['herbs_per_control']} "
                     f"herbs; the formula's {rf['real_significant']} significant pathway(s) "
                     f"are matched or exceeded by random formulas with empirical p "
                     f"{rf['empirical_p']}.")
    else:
        lines.append(f"Not performed: {rf['why']}.")
    rw = report["rewired"]
    lines += ["", "## Degree-preserving rewiring", "",
              f"{rw['rewirings']} rewired networks; empirical p {rw['empirical_p']} for "
              f"as many significant pathways as the real network.", "",
              "## Scope", ""]
    for k, v in report["scope"].items():
        lines.append(f"- **{k}**: {v}")
    return "\n".join(lines) + "\n"

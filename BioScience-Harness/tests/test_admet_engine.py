"""ADMET's chemistry seam: the exact domain search, prepared sets on disk, injected similarity.

A browser computes the chemistry (RDKit.js) and the domain search (its GPU or CPU) and
hands them to the same pipeline. What is asserted: the search is the exact one, ties to
the smallest index, in bounded memory; a prepared set written to disk and read back gives
the native run's outputs byte for byte; anything that does not hold is refused or, for
similarities, recomputed on the CPU and recorded; a card names its chemistry.
"""

from __future__ import annotations

import json
import shutil
import time
import tracemalloc
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from admet_world import make_archive
from omics_world import need_module

pytestmark = pytest.mark.unit

QUERY = [("toluene", "Cc1ccccc1"), ("phenol", "Oc1ccccc1"),
         ("aspirin_na", "CC(=O)Oc1ccccc1C(=O)[O-].[Na+]"),
         ("quercetin", "O=c1c(O)c(-c2ccc(O)c(O)c2)oc2cc(O)cc(O)c12"),
         ("berberine", "COc1ccc2cc3[n+](cc2c1OC)CCc1cc2OCOc2cc1-3"),
         ("rhodanine", "S=C1SC(=Cc2ccccc2)C(=O)N1"), ("bad", "C1CC("),
         ("perfluoro", "FC(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)F"),
         ("methylpyridine", "Cc1ccncc1")]
OUTPUTS = ("admet.tsv", "report.md", "report.html", "limitations.md", "plots/classification.svg")


def _tools():
    need_module("rdkit")
    need_module("sklearn")


def _exact(q: np.ndarray, r: np.ndarray):
    """The search by definition, one pair at a time on Python integers."""
    qi = [int.from_bytes(row.tobytes(), "big") for row in q]
    ri = [int.from_bytes(row.tobytes(), "big") for row in r]
    out = []
    for a in qi:
        best = None
        for j, b in enumerate(ri):
            inter, union = (a & b).bit_count(), (a | b).bit_count()
            sim = inter / union if union else 0.0
            if best is None or sim > best[0]:
                best = (sim, j, inter, union)
        out.append(best[1:])
    return [np.array(c) for c in zip(*out)]


def _bits(rng, n, p):
    return np.packbits(rng.random((n, 2048)) < p, axis=1)


# ----------------------------------------------------------------- the domain search

def test_the_domain_search_is_exact_with_ties_to_the_smallest_index():
    from bioagent.admet.chem import similarity, tanimoto_best, tanimoto_max
    rng = np.random.default_rng(1)
    base = _bits(rng, 12, 0.05)
    ref = np.concatenate([np.zeros((2, 256), np.uint8), base, base[::-1],      # duplicates
                          np.full((1, 256), 255, np.uint8), base & base[::-1]])
    query = np.concatenate([np.zeros((2, 256), np.uint8),                     # union 0 with
                            np.full((1, 256), 255, np.uint8), base,           # the empty refs
                            base & _bits(rng, 12, 0.5), _bits(rng, 20, 0.03)])
    want = _exact(query, ref)
    for block in (1 << 25, 48 * 7, 1):          # one block; several; one pair at a time
        arg, inter, union = tanimoto_best(query, ref, block_bytes=block)
        for got, expected in zip((arg, inter, union), want):
            assert np.array_equal(got, expected), block
    assert arg[0] == 0 and inter[0] == 0 and union[0] == 0      # empty vs empty: 0, first
    assert (arg[3:15] == np.arange(2, 14)).all(), "a duplicate later in ref never wins"
    sim, arg_max = tanimoto_max(query, ref)
    assert np.array_equal(arg_max, arg) and np.array_equal(sim, similarity(inter, union))
    assert sim.dtype == np.float64 and sim[0] == 0.0 and sim[3] == 1.0
    empty = tanimoto_best(np.zeros((0, 256), np.uint8), ref)
    assert all(len(a) == 0 for a in empty)
    with pytest.raises(ValueError, match="no reference"):
        tanimoto_best(query, ref[:0])
    with pytest.raises(ValueError, match="packed bits"):
        tanimoto_best(query.astype(bool), ref)


def test_the_domain_search_keeps_its_temporaries_bounded():
    """An endpoint's whole training set against a run's molecules once needed gigabytes
    (5.5 GB natively for cyp2d6, and a ValueError in a 32-bit browser runtime)."""
    from bioagent.admet.chem import BLOCK_BYTES, tanimoto_max
    rng = np.random.default_rng(2)
    query, ref = _bits(rng, 64, 0.02), _bits(rng, 4096, 0.02)
    peaks = {}
    for block in (None, 1 << 20):
        tracemalloc.start()
        tanimoto_max(query, ref, **({"block_bytes": block} if block else {}))
        peaks[block] = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    assert peaks[None] < BLOCK_BYTES, peaks           # 8 MiB here; the lookup table: 320 MiB
    assert peaks[1 << 20] < (1 << 20) + 512 * 1024, peaks     # the block and the popcounts


# ----------------------------------------------------------------- prepared sets on disk

def test_a_prepared_set_reads_back_exactly_and_only_for_its_molecules(tmp_path):
    _tools()
    from bioagent.admet.chem import ChemError
    from bioagent.admet.engine import PrecomputedEngine, RDKitEngine, write_prepared
    native = RDKitEngine()
    smiles = [s for _, s in QUERY]
    prepared = native.prepare(smiles)
    write_prepared(prepared, native, tmp_path / "set")
    engine = PrecomputedEngine(tmp_path / "set")
    assert (engine.name, engine.version, engine.detail) == (native.name, native.version,
                                                            native.detail)
    back = engine.prepare(smiles)
    assert back.ok.tolist() == prepared.ok.tolist() and back.errors == prepared.errors
    assert back.features.tobytes() == prepared.features.tobytes()
    assert back.bits.tobytes() == prepared.bits.tobytes()
    for field in ("canonical", "atoms_with_h", "alerts", "needs"):
        assert getattr(back, field) == getattr(prepared, field), field
    for d1, d2 in zip(back.descriptors, prepared.descriptors):
        assert (d1 is None and d2 is None) or json.dumps(d1) == json.dumps(d2)  # NaN kept
    assert not back.ok[6] and "not a valid SMILES" in back.errors[6]
    assert back.canonical[2] == "CC(=O)Oc1ccccc1C(=O)O"                        # the salt
    assert ("PAINS_A", "ene_rhod_A(235)") in back.alerts[5]
    with pytest.raises(ChemError, match="no prepared set .* is for these 8 molecules"):
        engine.prepare(smiles[:-1])
    with pytest.raises(ChemError, match="is for these"):
        engine.prepare(smiles[::-1])                                  # same molecules, order
    # the empty SMILES is a molecule natively (no atoms, canonical ""): read back, not refused
    write_prepared(native.prepare(["", "CCO"]), native, tmp_path / "empty")
    empty = PrecomputedEngine(tmp_path / "empty").prepare(["", "CCO"])
    assert empty.ok.all() and empty.canonical == ("", "CCO") and empty.atoms_with_h[0] == 0
    # without alerts the set says so, and a run that needs them is refused
    write_prepared(native.prepare(smiles, alerts=False), native, tmp_path / "plain")
    assert PrecomputedEngine(tmp_path / "plain").prepare(smiles, alerts=False).alerts[0] is None
    with pytest.raises(ChemError, match="alerts were not computed"):
        PrecomputedEngine(tmp_path / "plain").prepare(smiles)


@pytest.mark.parametrize("damage, message", [
    ("count", "fingerprint counts do not give its bits"),
    ("nan", "fingerprint counts are not whole numbers from 0 to 255"),        # bits agree
    ("half", "fingerprint counts are not whole numbers from 0 to 255"),
    ("negative", "fingerprint counts are not whole numbers from 0 to 255"),
    ("over", "fingerprint counts are not whole numbers from 0 to 255"),
    ("infinite", "its descriptors are not the 25 .*finite numbers"),
    ("descriptor", "descriptor columns are not its descriptors"),
    ("short", "holds .* values, not the 8 x 2073"),
    ("schema", "is not a prepared set"),
    ("row", "its descriptors are not the 25"),
    ("reason", "says no reason"),
])
def test_a_prepared_set_that_does_not_hold_is_refused(tmp_path, damage, message):
    _tools()
    from bioagent.admet.chem import ChemError, FP_BITS
    from bioagent.admet.engine import PrecomputedEngine, RDKitEngine, write_prepared
    native = RDKitEngine()
    smiles = [s for _, s in QUERY]
    d = write_prepared(native.prepare(smiles), native, tmp_path / "set")
    x = np.fromfile(d / "features.f32", dtype="<f4").reshape(-1, FP_BITS + 25)
    doc = json.loads((d / "prepared.json").read_text())
    unset, once = (int(np.flatnonzero(x[0, :FP_BITS] == v)[0]) for v in (0, 1))
    if damage == "count":
        x[0, int(np.flatnonzero(x[0, :FP_BITS])[0])] = 0             # a set bit, no count
    elif damage in ("nan", "negative"):                             # an unset bit: no count
        x[0, unset] = np.nan if damage == "nan" else -1.0
    elif damage in ("half", "over"):                                # a set bit: some count
        x[0, once] = 0.5 if damage == "half" else 300.0
    elif damage == "infinite":                                      # json reads Infinity
        doc["rows"][0]["descriptors"]["MolWt"] = float("inf")
        x[0, FP_BITS] = 0.0                                         # as nan_to_num gives
    elif damage == "descriptor":
        doc["rows"][0]["descriptors"]["MolWt"] += 1.0
    elif damage == "short":
        x = x[:-1]
    elif damage == "schema":
        doc["schema"] = "tcmstudio.admet-prepared/0"
    elif damage == "row":
        del doc["rows"][1]["descriptors"]["qed"]
    elif damage == "reason":
        doc["rows"][6]["error"] = None
    x.astype("<f4").tofile(d / "features.f32")
    (d / "prepared.json").write_text(json.dumps(doc))
    with pytest.raises(ChemError, match=message):
        PrecomputedEngine(d).prepare(smiles)


# ----------------------------------------------------------------- builds and runs

@pytest.fixture(scope="module")
def world(tmp_path_factory):
    _tools()
    from bioagent.admet import models as M
    root = tmp_path_factory.mktemp("admet_engine")
    archive = make_archive(root / "tdc.zip")
    M.build_models(root / "native", archive=archive, endpoints=["caco2_wang", "hia_hou"],
                   max_iter=100, log=lambda *_: None)
    return root, archive


def _split_sets(archive: Path, directory: Path, engine, prepared_for=None):
    """What a browser's chemistry would write for a build: per endpoint, its train_val then
    its test SMILES, prepared as one set."""
    from bioagent.admet.engine import write_prepared
    from bioagent.admet.models import _read_split
    with zipfile.ZipFile(archive) as z:
        for ep in ("caco2_wang", "hia_hou"):
            smiles = _read_split(z, ep, "train_val")[0] + _read_split(z, ep, "test")[0]
            prepared = (prepared_for or engine.prepare)(smiles)
            write_prepared(prepared, engine, directory / ep)
    return directory


def test_models_built_from_precomputed_chemistry_are_the_native_models(world, tmp_path):
    root, archive = world
    from bioagent.admet import models as M
    from bioagent.admet.engine import PrecomputedEngine, RDKitEngine
    sets = _split_sets(archive, tmp_path / "prepared", RDKitEngine(),
                       lambda s: RDKitEngine().prepare(s, alerts=False))
    ms = M.build_models(tmp_path / "cache", archive=archive, endpoints=["caco2_wang", "hia_hou"],
                        max_iter=100, log=lambda *_: None, engine=PrecomputedEngine(sets))
    native = M.load_models(root / "native").cards
    for ep, card in ms.cards.items():
        assert card["chemistry"] == native[ep]["chemistry"] == {
            "engine": "rdkit", "version": RDKitEngine().version,
            "detail": f"RDKit {RDKitEngine().version} (Python)"}
        assert card["dropped_unstandardisable"] == native[ep]["dropped_unstandardisable"] == 0
        same = {k: v for k, v in card.items() if k != "seconds"}
        assert same == {k: v for k, v in native[ep].items() if k != "seconds"}, ep


class _Elsewhere:
    """Chemistry from another engine (here RDKit's results under another name), refusing
    one molecule as a browser's ports would: it needs a step they do not have."""
    name, version, detail = "rdkit.js", "2026.09.1", "RDKit.js 2026.09.1 MinimalLib + test"

    def __init__(self, refuse: str):
        self.refuse = refuse

    def prepare(self, smiles, *, alerts=True):
        from bioagent.admet.engine import PreparedSet, RDKitEngine
        p = RDKitEngine().prepare(smiles, alerts=alerts)
        at = smiles.index(self.refuse)
        keep = np.ones(int(p.ok.sum()), dtype=bool)
        keep[int(np.flatnonzero(p.index == at)[0])] = False

        def swap(column, value):
            return tuple(value if i == at else v for i, v in enumerate(column))
        ok = p.ok.copy()
        ok[at] = False
        return PreparedSet(
            smiles=p.smiles, ok=ok, features=p.features[keep], bits=p.bits[keep],
            errors=swap(p.errors, "standardising it needs MolStandardize's Reionizer, which "
                                  "is not ported: run it on the runner"),
            canonical=swap(p.canonical, None), descriptors=swap(p.descriptors, None),
            atoms_with_h=swap(p.atoms_with_h, None), alerts=swap(p.alerts, None),
            needs=swap(p.needs, "runner"))


def test_a_card_names_its_chemistry_and_other_chemistry_is_trained_again(world, tmp_path):
    root, archive = world
    from bioagent.admet import models as M
    from bioagent.admet.engine import PrecomputedEngine
    cache = tmp_path / "cache"
    shutil.copytree(root / "native", cache)
    with zipfile.ZipFile(archive) as z:
        first = M._read_split(z, "caco2_wang", "train_val")[0][0]
    elsewhere = _Elsewhere(first)
    sets = _split_sets(archive, tmp_path / "prepared", elsewhere,
                       lambda s: elsewhere.prepare(s, alerts=False))
    said: list[str] = []
    ms = M.build_models(cache, archive=archive, endpoints=["caco2_wang"], max_iter=100,
                        log=said.append, engine=PrecomputedEngine(sets))
    assert said[0].startswith("  caco2_wang: mae"), "a native model is not kept"
    card = ms.cards["caco2_wang"]
    assert card["chemistry"]["engine"] == "rdkit.js" and card["rdkit"] == "2026.09.1"
    assert card["dropped_unstandardisable"] == 1 and card["dropped_unreadable"] == 0
    assert card["train"] == 79                                  # 80 less the one refused
    # the refused molecule needs the runner; a native build trains it again, natively
    said.clear()
    ms = M.build_models(cache, archive=archive, endpoints=["caco2_wang"], max_iter=100,
                        log=said.append)
    assert said[0].startswith("  caco2_wang: mae") and ms.cards["caco2_wang"]["train"] == 80
    # a card from before cards named their chemistry was built natively: kept
    cards = json.loads((cache / "models" / "model_cards.json").read_text())
    for c in cards.values():
        del c["chemistry"], c["dropped_unstandardisable"]
    (cache / "models" / "model_cards.json").write_text(json.dumps(cards))
    said.clear()
    M.build_models(cache, archive=archive, endpoints=["caco2_wang", "hia_hou"], max_iter=100,
                   log=said.append)
    assert said == ["  caco2_wang: kept, its model matches its card",
                    "  hia_hou: kept, its model matches its card"]


def _run(world, out: Path, **kw):
    root, _ = world
    from bioagent.admet.pipeline import AdmetConfig, run_admet
    return run_admet(QUERY, AdmetConfig(cache_dir=str(root / "native")), out, **kw)


def _same_outputs(a: Path, b: Path, *, predictions_reason: bool = True) -> None:
    for name in OUTPUTS:
        assert (a / name).read_bytes() == (b / name).read_bytes(), name
    ja, jb = (json.loads((d / "run.json").read_text()) for d in (a, b))

    def timeless(stages):
        return [{k: v for k, v in s.items() if k != "seconds"
                 and (predictions_reason or (s["name"], k) != ("predictions", "reason"))}
                for s in stages]
    assert timeless(ja["stages"]) == timeless(jb["stages"])
    for key in ("outputs", "models", "read", "failures", "warnings", "limitations",
                "chemistry", "complete"):
        assert ja[key] == jb[key], key


def test_a_run_on_precomputed_chemistry_gives_the_native_outputs(world, tmp_path):
    """RDKitEngine.prepare, written in the browser's format, read back by
    PrecomputedEngine: the same run as RDKit directly, byte for byte."""
    from bioagent.admet.engine import PrecomputedEngine, RDKitEngine, write_prepared
    native = _run(world, tmp_path / "native")
    assert native.complete and native.manifest["chemistry"]["engine"] == "rdkit"
    assert native.manifest["similarity"] == {"backend": "cpu", "checked": None,
                                             "rejected": None}
    write_prepared(RDKitEngine().prepare([s for _, s in QUERY]), RDKitEngine(),
                   tmp_path / "prepared")
    again = _run(world, tmp_path / "again", engine=PrecomputedEngine(tmp_path / "prepared"))
    _same_outputs(tmp_path / "native", tmp_path / "again")
    assert json.dumps(again.molecules) == json.dumps(native.molecules)


def test_precomputed_chemistry_needs_no_rdkit(world, tmp_path, monkeypatch):
    """A browser's Python has no RDKit: building and running on what its chemistry wrote
    must not import it."""
    root, archive = world
    import sys
    from bioagent.admet import models as M
    from bioagent.admet.engine import PrecomputedEngine, RDKitEngine, write_prepared
    write_prepared(RDKitEngine().prepare([s for _, s in QUERY]), RDKitEngine(),
                   tmp_path / "query")
    sets = _split_sets(archive, tmp_path / "train", RDKitEngine(),
                       lambda s: RDKitEngine().prepare(s, alerts=False))
    _run(world, tmp_path / "native")
    for name in [m for m in sys.modules if m == "rdkit" or m.startswith("rdkit.")]:
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(ImportError):
        import rdkit.Chem  # noqa: F401
    M.build_models(tmp_path / "cache", archive=archive, endpoints=["caco2_wang", "hia_hou"],
                   max_iter=100, log=lambda *_: None, engine=PrecomputedEngine(sets))
    from bioagent.admet.pipeline import AdmetConfig, run_admet
    run_admet(QUERY, AdmetConfig(cache_dir=str(tmp_path / "cache")), tmp_path / "browser",
              engine=PrecomputedEngine(tmp_path / "query"))
    _same_outputs(tmp_path / "native", tmp_path / "browser")


def _offered(world, tmp_path, *, backend="webgpu", spoil=None):
    """The similarities a browser would write for QUERY, optionally spoiled."""
    root, _ = world
    from bioagent.admet.chem import tanimoto_best
    from bioagent.admet.engine import RDKitEngine, write_similarity
    from bioagent.admet.models import load_models
    bits = RDKitEngine().prepare([s for _, s in QUERY], alerts=False).bits
    ms = load_models(root / "native")
    results = {}
    for ep in ("caco2_wang", "hia_hou"):
        found = [a.copy() for a in tanimoto_best(bits, ms.domain(ep))]
        if spoil and ep == "hia_hou":
            spoil(found, ms.domain(ep))
        results[ep] = (bits, ms.domain(ep), tuple(found))
    return write_similarity(tmp_path / "similarity", backend, results)


def test_similarities_from_a_browser_are_used_once_they_check_out(world, tmp_path):
    from bioagent.admet.engine import PrecomputedSimilarity
    native = _run(world, tmp_path / "native")
    run = _run(world, tmp_path / "gpu",
               similarity=PrecomputedSimilarity(_offered(world, tmp_path)))
    _same_outputs(tmp_path / "native", tmp_path / "gpu", predictions_reason=False)
    # 8 molecules read: every query is recomputed in full on the CPU (at most 32)
    assert run.manifest["similarity"] == {"backend": "webgpu", "rejected": None,
                                          "checked": {"caco2_wang": 8, "hia_hou": 8}}
    stage = {s["name"]: s for s in run.stages}["predictions"]
    assert "similarities from webgpu, checked against the exact search" in stage["reason"]
    assert run.summary()["similarity"]["backend"] == "webgpu"
    # without the file, the CPU, as with no similarity at all
    empty = PrecomputedSimilarity(tmp_path / "nothing")
    assert _run(world, tmp_path / "none", similarity=empty).manifest["similarity"] == \
        native.manifest["similarity"]


@pytest.mark.parametrize("spoil, why", [
    ("nearest", "the exact search gives reference"),
    ("counts", "intersection and union with its nearest reference"),
    ("outside", "nearest index is not a reference"),
    # 0xFFFFFFFF, a kernel's "none yet": in a 32-bit Python it once read as -1, the last
    # reference, and passed (measured in Pyodide); natively this pins the reason
    ("sentinel", "nearest index is not a reference"),
    ("refs", "other reference fingerprints"),
    ("missing", "no similarities for this endpoint"),
])
def test_similarities_that_do_not_hold_are_recomputed_on_the_cpu(world, tmp_path, monkeypatch,
                                                                spoil, why):
    from bioagent.admet import pipeline
    from bioagent.admet.engine import PrecomputedSimilarity, RDKitEngine
    bits = RDKitEngine().prepare([s for _, s in QUERY], alerts=False).bits

    def spoiled(found, ref):
        arg, inter, union = found
        if spoil == "nearest":          # another reference, its counts true: only the
            j = (arg[-1] + 1) % len(ref)                  # exact search can tell
            arg[-1] = j
            inter[-1] = int(np.unpackbits(bits[-1] & ref[j]).sum())
            union[-1] = int(np.unpackbits(bits[-1] | ref[j]).sum())
        elif spoil == "counts":
            inter[0] += 1
            union[0] -= 1
        elif spoil == "outside":
            arg[2] = len(ref)
        elif spoil == "sentinel":       # the counts of the last reference, as -1 reads
            arg[2] = 2**32 - 1
            inter[2] = int(np.unpackbits(bits[2] & ref[-1]).sum())
            union[2] = int(np.unpackbits(bits[2] | ref[-1]).sum())
    offered = _offered(world, tmp_path, spoil=spoiled)
    doc = json.loads((offered / "similarity.json").read_text())
    if spoil == "refs":
        doc["endpoints"]["hia_hou"]["refs_sha256"] = "0" * 64
    elif spoil == "missing":
        del doc["endpoints"]["hia_hou"]
    (offered / "similarity.json").write_text(json.dumps(doc))
    recomputed: list[int] = []
    cpu = pipeline.tanimoto_best
    monkeypatch.setattr(pipeline, "tanimoto_best",
                        lambda q, r: recomputed.append(len(r)) or cpu(q, r))
    native = _run(world, tmp_path / "native")
    recomputed.clear()
    run = _run(world, tmp_path / "gpu", similarity=PrecomputedSimilarity(offered))
    assert run.manifest["similarity"]["backend"] == "cpu"
    assert why in run.manifest["similarity"]["rejected"]
    assert run.manifest["similarity"]["rejected"].startswith("webgpu: hia_hou: ")
    assert len(recomputed) == 2, "every endpoint is recomputed, not only the one at fault"
    assert any("were not used" in w for w in run.warnings)
    assert (json.dumps([r["predictions"] for r in run.molecules])
            == json.dumps([r["predictions"] for r in native.molecules]))
    for name in ("admet.tsv", "plots/classification.svg"):
        assert ((tmp_path / "gpu" / name).read_bytes()
                == (tmp_path / "native" / name).read_bytes())


def test_the_run_summarises_itself(world, tmp_path):
    run = _run(world, tmp_path / "out")
    summary = run.summary()
    assert summary["molecules"] == 8 and summary["failures"] == ["bad"]
    assert summary["endpoints"] == ["caco2_wang", "hia_hou"] and summary["complete"]
    assert summary["outside_domain"]["hia_hou"] == sum(
        not r["predictions"]["hia_hou"]["in_domain"] for r in run.molecules)
    assert summary["chemistry"]["engine"] == "rdkit" and summary["models_built"]
    json.dumps(summary)                                           # a job's result is JSON


# ----------------------------------------------------------------- fixtures and the case

def test_the_small_archive_is_byte_reproducible(tmp_path, monkeypatch):
    _tools()
    clock = [1_700_000_000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    a = make_archive(tmp_path / "a.zip").read_bytes()
    clock[0] += 3600.0                         # an hour later: another zip timestamp
    b = make_archive(tmp_path / "b.zip").read_bytes()
    assert a == b
    with zipfile.ZipFile(tmp_path / "a.zip") as z:
        names = z.namelist()
        assert names == sorted(names) and len(names) == 4
        assert {i.date_time for i in z.infolist()} == {(1980, 1, 1, 0, 0, 0)}


def test_the_compound_case_records_the_molecules_descriptors(tmp_path, monkeypatch):
    """The case read a "properties" key that ADMET rows never had, so the descriptors
    silently dropped out of its record."""
    _tools()
    from bioagent.cases import compound_hypothesis as case
    from bioagent.docking import pipeline as docking
    name = case.COMPOUND[0]
    dock = SimpleNamespace(
        results=[SimpleNamespace(ligand=SimpleNamespace(name=name),
                                 best=SimpleNamespace(score=-6.1))],
        validated=True, validation={"top_pose_rmsd": 0.4, "passed": True},
        box=SimpleNamespace(source="test"), failures={})
    monkeypatch.setattr(docking, "run_docking", lambda *a, **k: dock)    # no Vina needed
    report = case.run_case(tmp_path / "case")
    molecule = report.step("ADMET").record["molecule"]
    assert molecule["descriptors"]["MolWt"] == pytest.approx(135.17, abs=0.01)
    assert "properties" not in molecule and molecule["rules"] and "alerts" in molecule

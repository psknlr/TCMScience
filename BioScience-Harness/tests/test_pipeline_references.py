"""The pipelines' reference datasets (bioagent/data/pipeline_references.json), re-run.

Each reference names its data by content digest, the parameters it is run with and what
it must give. Here the built-in RNA-seq and single-cell references are generated again,
their digests compared with the manifest's, the pipelines run on them and the results
compared with the expected values (and, under the numpy and scipy the output digests
were recorded with, with those digests too). The structure reference runs offline from
its stored model. The docking reference needs Vina (tests/test_docking.py checks it in
CI's molecular-modelling job); here only its fixture's digest is compared. The ADMET
reference needs the TDC archive, a download: it runs only where BIOAGENT_TDC_ARCHIVE
names a copy, and is skipped otherwise even under BIOAGENT_REQUIRE_TOOLS, since what is
missing then is data, not a tool.

The simulated references are simulated, with a known answer, and say so; they are not
public data.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pytest
import scipy

from bioagent.runrecord import content_digest, pipeline_references
from omics_world import make_world, need_module
from sc_world import EXPECTED, make_sc_world

pytestmark = pytest.mark.unit

FIX = Path(__file__).parent / "fixtures"


def _ref(ref_id: str) -> dict:
    return next(r for r in pipeline_references()["references"] if r["id"] == ref_id)


def _same_numerics(recorded: dict) -> bool:
    return recorded.get("numpy") == np.__version__ and recorded.get("scipy") in (
        None, scipy.__version__)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _generated_here(ref: dict) -> bool:
    """Whether this numpy is the one the generator ran under (its random streams are
    what the data digests record)."""
    return ref["source"]["generated_with"]["numpy"] == np.__version__


def test_the_rnaseq_reference_reruns_to_its_expected_result(tmp_path):
    from bioagent.omics import rnaseq as R
    ref = _ref("omics_world")
    assert ref["kind"] == "simulated" and ref["label"] == "simulated, known answer"
    world = make_world(tmp_path / "world", **ref["source"]["arguments"])
    p = ref["parameters"]
    run = R.run_rnaseq(world.sheet, R.RNASeqConfig(
        transcripts=world.transcripts, annotation=world.gtf, engine=p["engine"],
        trimmer=p["trimmer"], de_backend=p["de_backend"], design=p["design"],
        contrast=tuple(p["contrast"]), alpha=p["alpha"]), tmp_path / "out")
    validation = run.manifest["validation"]
    digests = {f"{s}_{m}": v[m]["content_sha256"] for s, v in validation["reads"].items()
               for m in ("R1", "R2")}
    digests["transcripts"] = validation["reference"]["transcripts"]["content_sha256"]
    digests["annotation"] = validation["reference"]["annotation"]["content_sha256"]
    if _generated_here(ref):
        assert digests == ref["inputs"]
        assert content_digest(digests) == ref["sha256"]
        assert run.reference_dataset["id"] == "omics_world"
    want = ref["expected"]["values"]
    res = run.result
    called = {g: float(res.log2_fold_change[i]) for i, g in enumerate(res.genes)
              if np.isfinite(res.p_adjusted[i]) and res.p_adjusted[i] < p["alpha"]}
    assert all(called.get(g, 0) > 1 for g in want["planted_up"])
    assert all(called.get(g, 0) < -1 for g in want["planted_down"])
    planted = set(want["planted_up"]) | set(want["planted_down"])
    assert len(set(called) - planted) <= want["false_discoveries_at_most"]
    assert res.summary()["tested"] == want["tested"]
    assert min(q["mapping_rate"] for q in run.quantification.values()) >= \
        want["mapping_rate_at_least"]
    assert run.complete is want["complete"]
    if _generated_here(ref) and _same_numerics(ref["expected"]["outputs_recorded_with"]):
        for name, digest in ref["expected"]["outputs"].items():
            assert _sha(tmp_path / "out" / name) == digest, name


def _ari(a, b):
    from collections import Counter
    from math import comb
    pairs = Counter(zip(a, b))
    rows, cols = Counter(a), Counter(b)
    index = sum(comb(v, 2) for v in pairs.values())
    ra, cb = sum(comb(v, 2) for v in rows.values()), sum(comb(v, 2) for v in cols.values())
    expected = ra * cb / comb(len(a), 2)
    return (index - expected) / ((ra + cb) / 2 - expected)


def test_the_scrna_reference_reruns_to_its_expected_result(tmp_path):
    from bioagent.omics import scrna as S
    ref = _ref("sc_world")
    assert ref["kind"] == "simulated" and "not public data" in ref["statement"]
    world = make_sc_world(tmp_path / "world", **ref["source"]["arguments"])
    p = ref["parameters"]
    run = S.run_scrna(world.sheet, S.ScConfig(
        analysis_backend=p["analysis_backend"], integration_method=p["integration_method"],
        de_backend=p["de_backend"], n_top_genes=p["n_top_genes"], seed=p["seed"]),
        tmp_path / "out")
    digests = run.manifest["validation"]["matrices"]
    if _generated_here(ref):
        assert digests == ref["inputs"] and content_digest(digests) == ref["sha256"]
        assert run.reference_dataset["id"] == "sc_world"
    want = ref["expected"]["values"]
    kind = np.array([world.truth[c]["type"] for c in run.cells], dtype=object)
    keep = np.isin(kind, list(EXPECTED))
    assert _ari(kind[keep], run.clusters[keep]) > want["cluster_ari_above"]
    assert sorted(set(run.cell_types.values())) == want["cell_types"]
    b = run.pseudobulk.results["B cell"]
    found = {b.genes[i] for i in b.significant()}
    assert len(found & set(world.condition_genes)) >= \
        want["pseudobulk_b_cell_planted_genes_at_least"]
    for ct, res in run.pseudobulk.results.items():
        if ct != "B cell":
            assert len(res.significant()) <= want["pseudobulk_other_cell_types_significant_at_most"]
    assert run.integration["mixing_after"] > want["batch_mixing_after_above"]
    assert run.complete is want["complete"]
    if _generated_here(ref) and _same_numerics(ref["expected"]["outputs_recorded_with"]):
        for name, digest in ref["expected"]["outputs"].items():
            assert _sha(tmp_path / "out" / name) == digest, name


def test_the_structure_reference_runs_offline_from_its_stored_model(monkeypatch, tmp_path):
    from bioagent.structure import predict as P
    from bioagent.structure.fold import FoldConfig, run_fold
    ref = _ref("1ubq_esmfold")
    assert ref["kind"] == "public"
    assert {n: _sha(FIX / "structure" / n) for n in ref["inputs"]} == ref["inputs"]
    stored = (FIX / "structure" / "ubq_esmfold.pdb").read_text()
    # the service's reply replaced by its stored reply, through the real _esmatlas
    monkeypatch.setattr(P, "esmatlas_fold", lambda *, sequence, timeout: stored.encode())
    seq = P.parse_pdb(stored).sequence()
    (res,) = run_fold(seq, FoldConfig(method=ref["parameters"]["method"], allow_remote=True,
                                      references={"seq1": str(FIX / "structure" / "1ubq.pdb")}),
                      tmp_path / "out")
    want = ref["expected"]["values"]
    assert res.comparison["tm_score"] == pytest.approx(want["tm_score"],
                                                       abs=want["tm_score_tolerance"])
    assert res.comparison["rmsd"] == pytest.approx(want["rmsd"], abs=want["rmsd_tolerance"])
    assert res.comparison["aligned"] == want["aligned"]
    assert res.comparison["identity"] == want["identity"]
    manifest = json.loads((tmp_path / "out" / "run.json").read_text())
    assert manifest["summary"][0]["checkpoint"]["verifiable"] is want["checkpoint_verifiable"]
    assert manifest["complete"] is want["complete"]


def test_the_docking_reference_is_the_fixture_it_names():
    ref = _ref("3ptb_pocket")
    assert ref["kind"] == "public" and ref["requires"] == ["vina", "meeko", "rdkit"]
    assert _sha(FIX / "docking" / "3ptb_pocket.pdb") == ref["sha256"]
    assert ref["status"].startswith("requires vina")


def test_the_admet_reference_where_its_archive_is_given(tmp_path):
    ref = _ref("tdc_admet_group")
    from bioagent.admet.models import TDC_ADMET_SHA256, TDC_ADMET_URL
    assert ref["sha256"] == TDC_ADMET_SHA256 and ref["source"]["url"] == TDC_ADMET_URL
    archive = os.environ.get("BIOAGENT_TDC_ARCHIVE", "").strip()
    if not archive:
        pytest.skip("BIOAGENT_TDC_ARCHIVE does not name a copy of the TDC ADMET group "
                    "(a download from dataverse.harvard.edu)")
    need_module("rdkit")
    need_module("sklearn")
    from bioagent.admet.models import build_models
    assert _sha(Path(archive)) == ref["sha256"]
    p = ref["parameters"]
    ms = build_models(tmp_path / "cache", archive=archive, seed=p["seed"],
                      max_iter=p["max_iter"], log=lambda *_: None)
    tol = ref["expected"]["values"]["held_out_tolerance"]
    for endpoint, score in ref["expected"]["values"]["held_out"].items():
        got = ms.cards[endpoint]["test_score"]
        # absolute for a score on [0, 1], relative for an error in the endpoint's unit
        assert abs(got - score) <= tol * max(1.0, abs(score)), (endpoint, got, score)

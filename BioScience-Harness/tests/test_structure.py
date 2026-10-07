"""Protein structure: files, geometry, comparison, secondary structure, the pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from bioagent.structure import dssp, geometry as G, pdbio, predict as P
from bioagent.structure.fold import FoldConfig, read_sequences, run_fold, verify_run
from omics_world import need_module

pytestmark = pytest.mark.unit

FIX = Path(__file__).parent / "fixtures" / "structure"
UBQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"


@pytest.fixture(scope="module")
def crystal():
    return pdbio.read_pdb(FIX / "1ubq.pdb")


@pytest.fixture(scope="module")
def model():
    return pdbio.read_pdb(FIX / "ubq_esmfold.pdb")


def test_pdb_files_read_and_write(crystal, model, tmp_path):
    assert crystal.sequence("A") == UBQ and model.sequence() == UBQ
    assert crystal.chains() == ["A"]
    pl = model.plddt()
    assert 0 < np.nanmin(pl) and np.nanmax(pl) <= 100 and np.nanmean(pl) > 85   # 0-1 scaled
    out = pdbio.write_pdb(model, tmp_path / "m.pdb", bfactors=pl)
    back = pdbio.read_pdb(out)
    assert back.sequence() == UBQ
    assert np.allclose(back.backbone()["CA"], model.backbone()["CA"], atol=1e-3)
    assert np.allclose(back.plddt(), pl, atol=0.01)


def test_superposition_recovers_a_rigid_motion(crystal):
    ca = crystal.backbone("A")["CA"]
    angle = 0.7
    rot = np.array([[np.cos(angle), -np.sin(angle), 0], [np.sin(angle), np.cos(angle), 0],
                    [0, 0, 1]])
    moved = ca @ rot.T + np.array([5.0, -3.0, 12.0])
    assert G.rmsd(moved, ca) < 1e-6
    assert G.tm_score(moved, ca) == pytest.approx(1.0)
    assert G.gdt_ts(moved, ca) == pytest.approx(1.0)


def test_tm_score_of_the_model_against_the_crystal(crystal, model):
    c = G.compare(model.sequence(), model.backbone()["CA"], crystal.sequence("A"),
                  crystal.backbone("A")["CA"])
    assert c.aligned == 76 and c.identity == 1.0
    assert c.tm_score == pytest.approx(0.9581, abs=0.002)
    assert c.rmsd == pytest.approx(0.827, abs=0.01)


def test_tm_score_agrees_with_tm_align(crystal, model):
    tmtools = need_module("tmtools")
    a, b = model.backbone()["CA"], crystal.backbone("A")["CA"]
    ref = tmtools.tm_align(a, b, UBQ, UBQ)
    assert G.tm_score(a, b) == pytest.approx(ref.tm_norm_chain2, abs=0.005)


def test_alignment_pairs_around_missing_residues():
    a = "MQIFVKTLTGKTITLEVEPSDTIENV"
    b = a[:10] + a[14:]                                  # four residues missing in b
    al = G.align(a, b)
    assert al.identity == 1.0 and len(al.pairs) == len(b)
    assert (9, 9) in al.pairs and (14, 10) in al.pairs
    end = G.align("AAAAWWWWHHHH", "WWWWHHHH")            # free leading gap
    assert end.pairs[0] == (4, 0)


def test_dssp_assigns_ubiquitins_known_structure(crystal):
    res = crystal.residues("A")
    ss = dssp.assign(crystal.backbone("A"), [r[3] for r in res])
    helix = {i + 1 for i, c in enumerate(ss) if c == "H"}
    strand = {i + 1 for i, c in enumerate(ss) if c == "E"}
    assert set(range(24, 34)) <= helix <= set(range(22, 36))          # alpha 23-34
    for lo, hi in ((2, 7), (12, 16), (41, 45), (66, 71)):              # beta strands
        assert set(range(lo, hi + 1)) <= strand
    s = dssp.summary(ss)
    assert 0.2 < s["helix"] < 0.3 and 0.3 < s["strand"] < 0.4


def test_remote_prediction_must_be_asked_for():
    with pytest.raises(P.PredictionError, match="allow_remote"):
        P.predict("x", UBQ, method="esmatlas")
    with pytest.raises(P.PredictionError, match="not standard amino acids"):
        P.validate_sequence("MKT*B")


def test_sequences_are_read_from_fasta(tmp_path):
    f = tmp_path / "s.fasta"
    f.write_text(">ubq ubiquitin\nMQIFV\nKTLTG\n>gb1\nMTYKL\n")
    assert read_sequences(f) == [("ubq", "MQIFVKTLTG"), ("gb1", "MTYKL")]
    assert read_sequences("MKT VRQ") == [("seq1", "MKTVRQ")]


@pytest.fixture
def offline(monkeypatch):
    """The service replaced by the stored ESMFold model of ubiquitin."""
    text = (FIX / "ubq_esmfold.pdb").read_text()

    def fake(name, seq, *, timeout):
        assert seq == UBQ
        parsed = pdbio.parse_pdb(text)
        return P.Prediction(name=name, sequence=seq, pdb=text, plddt=parsed.plddt(),
                            method="ESMFold (ESM Atlas service)", version="esmfold_v1",
                            remote=True, request_sha256="r", response_sha256="s")
    monkeypatch.setattr(P, "_esmatlas", fake)


def test_the_pipeline_reports_confidence_geometry_and_agreement(offline, tmp_path):
    fasta = tmp_path / "u.fasta"
    fasta.write_text(f">ubq\n{UBQ}\n")
    (res,) = run_fold(fasta, FoldConfig(method="esmatlas", allow_remote=True,
                                        references={"ubq": str(FIX / "1ubq.pdb")}),
                      tmp_path / "out")
    assert res.confidence["mean_plddt"] > 85
    assert res.geometry["clashes"] == 0 and res.geometry["ca_ca_outliers"] == 0
    assert res.comparison["tm_score"] > 0.95 and res.comparison["kind"] == "file"
    out = tmp_path / "out"
    summary = json.loads((out / "summary.json").read_text())
    assert summary[0]["secondary_summary"]["helix"] > 0.2
    md = (out / "report.md").read_text()
    assert "## What a model is" in md and "not an experimental structure" in md
    assert "pae_ubq" not in md                      # the service gives no PAE: no figure
    page = (out / "report.html").read_text()
    assert f"<pre>\n{UBQ}\n" in page             # sequence over DSSP, monospaced
    assert (out / "models" / "ubq.pdb").is_file() and (out / "plots" / "plddt_ubq.svg").is_file()
    ok, problems = verify_run(out)
    assert ok, problems
    (out / "models" / "ubq.pdb").write_text((FIX / "1ubq.pdb").read_text().replace(
        "MET A   1", "ALA A   1"))
    ok, problems = verify_run(out)
    assert not ok


CANDIDATES = Path(__file__).resolve().parents[1] / "skills" / "candidates" / "molecular"


def test_the_skill_records_models_and_makes_no_claim(offline, tmp_path):
    from bioagent.governed import GovernedRunRefused, run_governed

    fasta = tmp_path / "u.fasta"
    fasta.write_text(f">ubq\n{UBQ}\n")
    args = {"sequences": str(fasta), "method": "esmatlas", "allow_remote": True,
            "reference": f"ubq={FIX / '1ubq.pdb'}", "out_dir": str(tmp_path / "run")}
    with pytest.raises(GovernedRunRefused, match="no lockfile pins skill"):
        run_governed("predict-protein-structure", args, skill_dir=CANDIDATES,
                     state_dir=tmp_path / "psh0")
    run = run_governed("predict-protein-structure", args, skill_dir=CANDIDATES,
                       state_dir=tmp_path / "psh", output_dir=tmp_path / "out",
                       allow_unpinned=True)
    art = run.artifact
    assert not art.claims and not run.released
    assert run.verdict.publishable, run.verdict.codes
    assert {s.id for s in art.sources} >= {"esmatlas.esmfold"}
    assert "not an experimental structure" in " ".join(art.limitations)
    summary = json.loads((Path(run.output_dir) / "summary.json").read_text())
    assert summary[0]["comparison"]["tm_score"] > 0.95

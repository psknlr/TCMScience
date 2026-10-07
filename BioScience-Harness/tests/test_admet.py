"""ADMET from structure: standardisation, rules, alerts, the TDC models, the pipeline."""

from __future__ import annotations

import io
import json
from pathlib import Path

import numpy as np
import pytest

from admet_world import make_archive
from omics_world import need_module

pytestmark = pytest.mark.unit

CANDIDATES = Path(__file__).resolve().parents[1] / "skills" / "candidates" / "molecular"


def _tools():
    need_module("rdkit")
    need_module("sklearn")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    _tools()
    from bioagent.admet import models as M
    root = tmp_path_factory.mktemp("admet")
    archive = make_archive(root / "tdc.zip")
    ms = M.build_models(root / "cache", archive=archive, endpoints=["caco2_wang", "hia_hou"],
                        max_iter=100, log=lambda *_: None)
    return root, ms


def test_molecules_are_standardised_before_anything_is_computed():
    _tools()
    from rdkit import Chem
    from bioagent.admet.chem import ChemError, standardize
    salt = standardize("CC(=O)Oc1ccccc1C(=O)[O-].[Na+]")          # aspirin, sodium salt
    assert Chem.MolToSmiles(salt) == "CC(=O)Oc1ccccc1C(=O)O"
    assert Chem.GetFormalCharge(standardize("C[N+](C)(C)C")) == 1  # cannot be neutralised
    with pytest.raises(ChemError, match="not a valid SMILES"):
        standardize("C1CC(")


def test_drug_likeness_rules_and_structural_alerts():
    _tools()
    from bioagent.admet.chem import standardize
    from bioagent.admet.rules import alerts, rules
    aspirin = rules(standardize("CC(=O)Oc1ccccc1C(=O)O"))
    assert aspirin["lipinski"] == {"violations": 0, "pass": True}
    assert aspirin["veber"]["pass"] and aspirin["egan"]["pass"]
    fatty = rules(standardize("C" * 37 + "C(=O)O"))                # MW > 500 and logP > 5
    assert fatty["lipinski"]["violations"] == 2 and not fatty["lipinski"]["pass"]
    rhodanine = {(a["catalog"], a["alert"]) for a in alerts(standardize(
        "S=C1SC(=Cc2ccccc2)C(=O)N1"))}
    assert ("PAINS_A", "ene_rhod_A(235)") in rhodanine
    catechol = {a["catalog"] for a in alerts(standardize("Oc1ccccc1O"))}
    assert "PAINS_B" in catechol
    assert alerts(standardize("O=C(O)c1ccccc1")) == []


def test_tanimoto_similarity_is_exact_on_packed_bits():
    _tools()
    from bioagent.admet.chem import bits, standardize, tanimoto_max
    mols = [standardize(s) for s in ("c1ccccc1O", "c1ccccc1N", "CCCCCC")]
    b = bits(mols)
    sim, arg = tanimoto_max(b[:1], b)
    assert sim[0] == pytest.approx(1.0) and arg[0] == 0
    sim, _ = tanimoto_max(b[2:], b[:2])
    assert sim[0] < 0.3


def test_models_are_trained_scored_on_the_held_out_split_and_carded(built):
    root, ms = built
    cards = ms.cards
    assert set(cards) == {"caco2_wang", "hia_hou"}
    reg, cls = cards["caco2_wang"], cards["hia_hou"]
    assert reg["metric"] == "mae" and cls["metric"] == "auroc"
    assert reg["train"] == 80 and reg["test"] == 25
    assert reg["test_score"] < 0.5                    # a learnable synthetic target
    assert cls["test_score"] > 0.7
    assert not reg["archive_is_pinned"]               # a test archive, not TDC's
    on_disk = json.loads((root / "cache" / "models" / "model_cards.json").read_text())
    assert on_disk["hia_hou"]["model_sha256"] == cls["model_sha256"]


def test_predictions_carry_their_applicability_domain(built):
    root, ms = built
    from bioagent.admet.chem import standardize
    from bioagent.admet.models import load_models
    loaded = load_models(root / "cache")
    import zipfile
    with zipfile.ZipFile(root / "tdc.zip") as z:
        trained = z.read("admet_group/hia_hou/train_val.csv").decode().splitlines()[1]
    known = standardize(trained.split(",")[1])                   # a training molecule
    alien = standardize("FC(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)C(F)(F)F")
    res = loaded.predict([known, alien], "hia_hou")
    assert 0 <= res["value"].min() and res["value"].max() <= 1
    assert res["similarity"][0] == pytest.approx(1.0) and res["in_domain"][0]
    assert res["similarity"][1] < 0.3 and not res["in_domain"][1]


def test_a_model_file_that_changed_is_refused(built, tmp_path):
    root, _ = built
    import shutil
    from bioagent.admet.chem import ChemError
    from bioagent.admet.models import load_models
    copy = tmp_path / "cache"
    shutil.copytree(root / "cache", copy)
    pkl = copy / "models" / "caco2_wang.pkl"
    pkl.write_bytes(pkl.read_bytes() + b"\0")
    with pytest.raises(ChemError, match="does not match its model card"):
        load_models(copy).model("caco2_wang")


def test_a_domain_file_that_changed_is_refused(built, tmp_path):
    root, _ = built
    import shutil
    from bioagent.admet.chem import ChemError
    from bioagent.admet.models import load_models
    copy = tmp_path / "cache"
    shutil.copytree(root / "cache", copy)
    domain = copy / "models" / "hia_hou_domain.npy"
    np.save(domain, np.load(domain)[:1])          # a smaller domain flags more molecules out
    with pytest.raises(ChemError, match="does not match its model card"):
        load_models(copy).model("hia_hou")


def test_an_interrupted_build_keeps_what_it_finished_and_resumes(tmp_path):
    """A full build takes most of an hour; a build that stopped half way kept nothing,
    because the cards were written only at the end."""
    _tools()
    from bioagent.admet.models import build_models, load_models

    class Interrupted(Exception):
        pass

    def stop_after_first_endpoint(line):
        raise Interrupted(line)

    archive, cache = make_archive(tmp_path / "tdc.zip"), tmp_path / "cache"
    both = ["caco2_wang", "hia_hou"]
    with pytest.raises(Interrupted):
        build_models(cache, archive=archive, endpoints=both, max_iter=50,
                     log=stop_after_first_endpoint)
    assert set(load_models(cache).cards) == {"caco2_wang"}, "the finished model is kept"
    first = load_models(cache).cards["caco2_wang"]["model_sha256"]
    said: list[str] = []
    ms = build_models(cache, archive=archive, endpoints=both, max_iter=50, log=said.append)
    assert set(ms.cards) == set(both) and ms.cards["caco2_wang"]["model_sha256"] == first
    assert said[0] == "  caco2_wang: kept, its model matches its card"
    assert said[1].startswith("  hia_hou: auroc")
    # Other settings, or rebuild=True, train it again; other endpoints' cards stay.
    said.clear()
    build_models(cache, archive=archive, endpoints=["caco2_wang"], max_iter=60,
                 log=said.append)
    assert said[0].startswith("  caco2_wang: mae")
    assert set(load_models(cache).cards) == set(both)
    said.clear()
    build_models(cache, archive=archive, endpoints=["hia_hou"], max_iter=50, rebuild=True,
                 log=said.append)
    assert said[0].startswith("  hia_hou: auroc")


def test_the_archive_is_held_to_its_pinned_digest(tmp_path, monkeypatch):
    _tools()
    from bioagent.admet import models as M

    class Reply(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    sent = []
    monkeypatch.setattr(M.urllib.request, "urlopen",
                        lambda request, **k: sent.append(request) or Reply(b"PK\x03\x04fake"))
    with pytest.raises(M.ChemError, match="not the pinned"):
        M.fetch_archive(tmp_path)
    assert not (tmp_path / "tdc_admet_group.zip").exists()
    # Harvard Dataverse refuses Python's default User-Agent with 403.
    assert sent[0].get_header("User-agent").startswith("bioagent-harness/")


def test_the_pipeline_without_models_reports_rules_only(tmp_path):
    _tools()
    from bioagent.admet.pipeline import AdmetConfig, run_admet
    run = run_admet([("aspirin", "CC(=O)Oc1ccccc1C(=O)O"), ("bad", "C1CC(")],
                    AdmetConfig(cache_dir=str(tmp_path / "empty")), tmp_path / "out")
    assert not run.models_built and not run.cards
    assert "bad" in run.failures and len(run.molecules) == 1
    assert any("not built" in w for w in run.warnings)
    md = (tmp_path / "out" / "report.md").read_text()
    assert "## Properties and rules" in md and "## Model cards" not in md


def test_the_pipeline_with_models_reports_and_verifies(built, tmp_path):
    root, _ = built
    from bioagent.admet.pipeline import AdmetConfig, run_admet, verify_run
    run = run_admet([("toluene", "Cc1ccccc1"), ("phenol", "Oc1ccccc1")],
                    AdmetConfig(cache_dir=str(root / "cache")), tmp_path / "out")
    assert set(run.cards) == {"caco2_wang", "hia_hou"}
    p = run.molecules[0]["predictions"]["hia_hou"]
    assert set(p) == {"value", "similarity", "in_domain"}
    out = tmp_path / "out"
    header = (out / "admet.tsv").read_text().splitlines()[0].split("\t")
    assert "caco2_wang" in header and "hia_hou_in_domain" in header
    md = (out / "report.md").read_text()
    assert "## Absorption" in md and "## Model cards" in md and "scaffold" in md
    ok, problems = verify_run(out)
    assert ok, problems
    (out / "admet.tsv").write_text("changed")
    assert not verify_run(out)[0]


def test_the_skill_makes_no_claim(built, tmp_path):
    root, _ = built
    from bioagent.governed import GovernedRunRefused, run_governed
    mols = tmp_path / "m.csv"
    mols.write_text("name,smiles\naspirin,CC(=O)Oc1ccccc1C(=O)O\n")
    args = {"molecules": str(mols), "cache_dir": str(root / "cache"),
            "out_dir": str(tmp_path / "run")}
    with pytest.raises(GovernedRunRefused, match="no lockfile pins skill"):
        run_governed("predict-admet", args, skill_dir=CANDIDATES, state_dir=tmp_path / "p0")
    run = run_governed("predict-admet", args, skill_dir=CANDIDATES, state_dir=tmp_path / "psh",
                       output_dir=tmp_path / "out", allow_unpinned=True)
    art = run.artifact
    assert not art.claims and not run.released
    assert run.verdict.publishable, run.verdict.codes
    assert {s.id for s in art.sources} >= {"tdc.admet_group"}
    assert "none is a measurement" in " ".join(art.limitations)
    assert np.isfinite(json.loads((tmp_path / "run" / "run.json").read_text())
                       ["models"]["caco2_wang"]["test_score"])


def test_when_no_molecule_can_be_read_the_run_says_so(built, tmp_path):
    root, _ = built
    from bioagent.admet.pipeline import AdmetConfig, run_admet
    run = run_admet([("a", "C1CC("), ("b", "not a molecule")],
                    AdmetConfig(cache_dir=str(root / "cache")), tmp_path / "out")
    assert not run.molecules and len(run.failures) == 2 and not run.cards
    assert any("nothing was predicted" in w for w in run.warnings)
    assert "## Warnings" in (tmp_path / "out" / "report.md").read_text()

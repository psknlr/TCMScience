"""Building the ADMET models one endpoint at a time, as a browser does, and checking them.

A browser trains its models one call per endpoint (``models.train_endpoint``), into a
models directory of its own, from the chemistry its RDKit.js prepared for each split. What
is asserted: one endpoint trained that way is the endpoint ``build_models`` trains (the same
card and files); the card is merged into ``model_cards.json`` beside the others; a finished
model is kept; the archive's faults are refusals; ``verify_models`` holds every model to its
card; and the command's ``--max-iter`` and ``--seed`` reach the build.
"""

from __future__ import annotations

import io
import json
import shutil
import zipfile
from pathlib import Path

import numpy as np
import pytest

from admet_world import make_archive
from omics_world import need_module

pytestmark = pytest.mark.unit

ENDPOINTS = ["caco2_wang", "hia_hou"]


def _tools():
    need_module("rdkit")
    need_module("sklearn")


def _timeless(card: dict) -> dict:
    return {k: v for k, v in card.items() if k != "seconds"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    _tools()
    from bioagent.admet import models as M
    root = tmp_path_factory.mktemp("admet_build")
    archive = make_archive(root / "tdc.zip")
    M.build_models(root / "native", archive=archive, endpoints=ENDPOINTS, max_iter=100,
                   log=lambda *_: None)
    return root, archive


def _split_sets(archive: Path, directory: Path) -> Path:
    """The chemistry a browser writes for a build: per endpoint, a set per split."""
    from bioagent.admet import models as M
    from bioagent.admet.engine import RDKitEngine, write_prepared
    native = RDKitEngine()
    with zipfile.ZipFile(archive) as z:
        for ep in ENDPOINTS:
            for split in M.SPLITS:
                smiles = M.read_split(z, ep, split)[0]
                write_prepared(native.prepare(smiles, alerts=False), native, directory / ep / split)
    return directory


def test_one_endpoint_trained_alone_is_the_endpoint_the_build_trains(built, tmp_path):
    root, archive = built
    from bioagent.admet import models as M
    from bioagent.admet.engine import PrecomputedEngine
    sets = _split_sets(archive, tmp_path / "prepared")
    out = tmp_path / "models"
    said: list[str] = []
    cards = {ep: M.train_endpoint(out, archive=archive, endpoint=ep, max_iter=100,
                                  log=said.append, engine=PrecomputedEngine(sets / ep))
             for ep in ENDPOINTS}
    native = M.load_models(root / "native").cards
    on_disk = json.loads((out / "model_cards.json").read_text())
    assert list(on_disk) == ENDPOINTS, "each card merged beside the earlier ones, in order"
    for ep in ENDPOINTS:
        assert _timeless(cards[ep]) == _timeless(native[ep]) == _timeless(on_disk[ep]), ep
        for f in ("model_file", "domain_file"):
            assert (out / cards[ep][f]).read_bytes() == (root / "native" / "models" /
                                                        native[ep][f]).read_bytes()
    assert said[0].startswith("  caco2_wang: mae ") and said[1].startswith("  hia_hou: auroc ")
    # the same call again keeps it; rebuild trains it again; the other card stays
    said.clear()
    again = M.train_endpoint(out, archive=archive, endpoint="hia_hou", max_iter=100,
                             log=said.append, engine=PrecomputedEngine(sets / "hia_hou"))
    assert said == ["  hia_hou: kept, its model matches its card"] and again == cards["hia_hou"]
    said.clear()
    M.train_endpoint(out, archive=archive, endpoint="caco2_wang", max_iter=100, rebuild=True,
                     log=said.append, engine=PrecomputedEngine(sets / "caco2_wang"))
    assert said[0].startswith("  caco2_wang: mae ")
    assert list(json.loads((out / "model_cards.json").read_text())) == ["hia_hou", "caco2_wang"]
    # other settings are another model
    said.clear()
    M.train_endpoint(out, archive=archive, endpoint="hia_hou", max_iter=60, log=said.append,
                     engine=PrecomputedEngine(sets / "hia_hou"))
    assert said[0].startswith("  hia_hou: auroc ")
    assert json.loads((out / "model_cards.json").read_text())["hia_hou"]["max_iter"] == 60


def test_a_card_written_meanwhile_by_another_call_is_kept(built, tmp_path):
    """The cards are read again just before they are replaced: one endpoint's training does
    not drop a card another call wrote while it ran."""
    root, archive = built
    from bioagent.admet import models as M
    from bioagent.admet.engine import PrecomputedEngine
    sets = _split_sets(archive, tmp_path / "prepared")
    out = tmp_path / "models"
    out.mkdir()
    real = M._train

    def train_while_another_card_is_written(*a, **k):
        (out / "model_cards.json").write_text(json.dumps({"herg": {"endpoint": "herg"}}))
        return real(*a, **k)
    try:
        M._train = train_while_another_card_is_written
        M.train_endpoint(out, archive=archive, endpoint="hia_hou", max_iter=100,
                         log=lambda *_: None, engine=PrecomputedEngine(sets / "hia_hou"))
    finally:
        M._train = real
    assert list(json.loads((out / "model_cards.json").read_text())) == ["herg", "hia_hou"]


def _zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in members.items():
            z.writestr(name, text)
    return buf.getvalue()


@pytest.mark.parametrize("archive, endpoint, needle", [
    (b"not a zip", "hia_hou", "is not a zip archive"),
    (_zip({"admet_group/caco2_wang/train_val.csv": "Drug,Y\nC,1\n"}), "hia_hou",
     "the archive has no endpoint hia_hou"),
    (_zip({"admet_group/hia_hou/train_val.csv": "Drug,Y\nC,1\n"}), "hia_hou",
     "the archive lacks admet_group/hia_hou/test.csv"),
    (_zip({"admet_group/hia_hou/train_val.csv": "Smiles,Y\nC,1\n",
           "admet_group/hia_hou/test.csv": "Drug,Y\nC,1\n"}), "hia_hou",
     "admet_group/hia_hou/train_val.csv has no column 'Drug'"),
    (_zip({"admet_group/hia_hou/train_val.csv": "Drug,Y\nC,yes\n",
           "admet_group/hia_hou/test.csv": "Drug,Y\nC,1\n"}), "hia_hou",
     "cannot be read as TDC's table"),
    (b"PK\x05\x06" + b"\0" * 18, "herg_typo", "no TDC ADMET endpoint 'herg_typo'"),
])
def test_an_archive_that_cannot_be_trained_on_is_refused(tmp_path, archive, endpoint, needle):
    need_module("sklearn")
    from bioagent.admet import models as M
    from bioagent.admet.chem import ChemError
    path = tmp_path / "tdc.zip"
    path.write_bytes(archive)

    class NoChemistry:
        name, version, detail = "none", "0", "no chemistry"

        def prepare(self, smiles, *, alerts=True):
            raise AssertionError("nothing is prepared from an archive that is refused")
    with pytest.raises(ChemError, match=needle):
        M.train_endpoint(tmp_path / "models", archive=path, endpoint=endpoint,
                         engine=NoChemistry(), log=lambda *_: None)
    assert not (tmp_path / "models" / "model_cards.json").exists()


def test_the_build_refuses_an_archive_that_is_not_a_zip(tmp_path):
    """It raised zipfile.BadZipFile, which the command does not catch."""
    _tools()
    from bioagent import cli
    (tmp_path / "tdc.zip").write_bytes(b"<html>a login page</html>")
    code = cli.main(["admet", "--build-models", "--cache", str(tmp_path / "cache"), "--archive",
                     str(tmp_path / "tdc.zip"), "--endpoint", "hia_hou"])
    assert code == 1


def test_the_models_are_held_to_their_cards(built, tmp_path):
    root, _ = built
    from bioagent.admet import models as M
    models = tmp_path / "models"
    shutil.copytree(root / "native" / "models", models)
    ok, problems, report = M.verify_models(models, ENDPOINTS)
    assert ok and problems == []
    cards = json.loads((models / "model_cards.json").read_text())
    for ep in ENDPOINTS:
        entry = report["endpoints"][ep]
        assert entry["card_sha256"] == M.card_sha256(cards[ep])
        assert entry["model_sha256"] == cards[ep]["model_sha256"]
        assert entry["archive_sha256"] == cards[ep]["archive_sha256"]
        assert entry["chemistry"]["engine"] == "rdkit" and entry["dropped_unstandardisable"] == 0
    assert M.verify_models(models)[0], "every card when none is named"
    archive = cards["hia_hou"]["archive_sha256"]
    assert M.verify_models(models, ENDPOINTS, archive_sha256=archive)[0]
    ok, problems, _ = M.verify_models(models, ENDPOINTS, archive_sha256="0" * 64)
    assert not ok and problems[0] == ("caco2_wang: trained on another archive "
                                      f"({archive[:12]}), not 000000000000")
    ok, problems, _ = M.verify_models(models, ["herg"])
    assert not ok and problems == ["herg: no model card"]
    domain = models / cards["hia_hou"]["domain_file"]
    np.save(domain, np.load(domain)[:1])
    (models / cards["caco2_wang"]["model_file"]).unlink()
    ok, problems, _ = M.verify_models(models, ENDPOINTS)
    assert problems == ["caco2_wang: its model file caco2_wang.pkl is missing",
                        "hia_hou: its domain file hia_hou_domain.npy does not match its card"]
    cards["hia_hou"]["archive_sha256"] = "f" * 64
    cards["caco2_wang"]["domain_file"] = "../elsewhere.npy"
    (models / "model_cards.json").write_text(json.dumps(cards))
    problems = M.verify_models(models, ENDPOINTS)[1]
    assert "caco2_wang: its domain file ../elsewhere.npy is missing" in problems
    assert problems[-1].startswith("the cards record 2 different archives: ")
    (models / "model_cards.json").write_text("{")
    ok, problems, _ = M.verify_models(models, ENDPOINTS)
    assert not ok and problems[0].startswith("model_cards.json cannot be read")
    ok, problems, _ = M.verify_models(tmp_path / "nothing", None)
    assert not ok and problems == ["no model card in nothing/model_cards.json"]


def test_the_command_passes_its_iterations_and_seed_to_the_build(tmp_path):
    _tools()
    from bioagent import cli
    archive = make_archive(tmp_path / "tdc.zip")
    code = cli.main(["admet", "--build-models", "--cache", str(tmp_path / "cache"), "--archive",
                     str(archive), "--endpoint", "hia_hou", "--max-iter", "40", "--seed", "7"])
    assert code == 0
    card = json.loads((tmp_path / "cache" / "models" / "model_cards.json").read_text())["hia_hou"]
    assert (card["max_iter"], card["seed"]) == (40, 7)

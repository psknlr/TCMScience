"""The structure predictor against the live ESM Atlas service (network; not in the unit tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bioagent.structure import geometry as G, pdbio, predict as P

pytestmark = pytest.mark.integration

FIX = Path(__file__).parent / "fixtures" / "structure"
UBQ = "MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG"


def test_the_service_folds_ubiquitin():
    pred = P.predict("ubq", UBQ, method="esmatlas", allow_remote=True)
    model = pdbio.parse_pdb(pred.pdb)
    crystal = pdbio.read_pdb(FIX / "1ubq.pdb")
    c = G.compare(model.sequence(), model.backbone()["CA"], crystal.sequence("A"),
                  crystal.backbone("A")["CA"])
    assert pred.remote and c.tm_score > 0.9

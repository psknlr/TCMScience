"""A tiny TDC-shaped archive with known answers, for the ADMET tests.

105 small molecules (15 substituents on 7 ring systems). The regression endpoint is a
linear function of computed logP and TPSA plus noise; the classification endpoint is
TPSA below 25 Å². Laid out as TDC's ``admet_group/<endpoint>/{train_val,test}.csv``.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import numpy as np

SUBSTITUENTS = ["C", "CC", "O", "N", "F", "Cl", "Br", "CO", "OC(=O)", "NC(=O)", "N#C",
                "CN(C)", "CC(=O)", "OCC", "CS(=O)(=O)"]
SCAFFOLDS = ["c1ccccc1", "c1ccncc1", "C1CCCCC1", "c1ccc2ccccc2c1", "C1CCOCC1", "c1ccsc1",
             "c1ccoc1"]


def molecules() -> list[str]:
    return [s + r for r in SCAFFOLDS for s in SUBSTITUENTS]


def make_archive(path: Path, *, seed: int = 0) -> Path:
    from rdkit import Chem
    from rdkit.Chem import Crippen, rdMolDescriptors
    rng = np.random.default_rng(seed)
    smiles = molecules()
    logp = np.array([Crippen.MolLogP(Chem.MolFromSmiles(s)) for s in smiles])
    tpsa = np.array([rdMolDescriptors.CalcTPSA(Chem.MolFromSmiles(s)) for s in smiles])
    reg = 0.5 * logp - 0.02 * tpsa - 5.0 + rng.normal(0, 0.05, len(smiles))
    cls = (tpsa < 25).astype(int)
    order = rng.permutation(len(smiles))
    test, train = order[:25], order[25:]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for endpoint, y in (("caco2_wang", reg), ("hia_hou", cls)):
            for split, idx in (("train_val", train), ("test", test)):
                rows = ["Drug_ID,Drug,Y"] + [f"m{i},{smiles[i]},{y[i]}" for i in idx]
                z.writestr(f"admet_group/{endpoint}/{split}.csv", "\n".join(rows) + "\n")
    path.write_bytes(buf.getvalue())
    return path

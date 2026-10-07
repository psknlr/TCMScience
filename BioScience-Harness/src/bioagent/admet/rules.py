"""Drug-likeness rules and structural alerts.

Rules, each a published cut-off set on RDKit's descriptors (Crippen logP stands in for
the logP each paper used):

* **Lipinski** (Lipinski et al. 1997): MW <= 500, logP <= 5, H-bond donors <= 5,
  acceptors <= 10; one violation allowed.
* **Veber** (Veber et al. 2002): rotatable bonds <= 10 and TPSA <= 140 Å².
* **Egan** (Egan et al. 2000): TPSA <= 131.6 Å² and logP <= 5.88.
* **Ghose** (Ghose et al. 1999): 160 <= MW <= 480, -0.4 <= logP <= 5.6,
  40 <= molar refractivity <= 130, 20 <= atoms <= 70.

Alerts, from RDKit's FilterCatalog: PAINS (A, B, C; Baell & Holloway 2010), Brenk
(Brenk et al. 2008) and NIH (Jadhav et al. 2010) substructures. An alert marks a
substructure often behind assay interference or reactivity; it is a reason to look
closer, not a verdict on the compound. Many natural products, flavonoids and catechols
especially, carry PAINS motifs.
"""

from __future__ import annotations

from typing import Any

from .chem import descriptors, need

__all__ = ["rules", "alerts"]

_CATALOG = None


def rules(mol: Any) -> dict[str, Any]:
    d = descriptors(mol)
    Chem = need("rdkit.Chem")
    atoms = Chem.AddHs(mol).GetNumAtoms()
    lip = [d["MolWt"] > 500, d["MolLogP"] > 5, d["NumHDonors"] > 5, d["NumHAcceptors"] > 10]
    out = {
        "lipinski": {"violations": int(sum(lip)), "pass": sum(lip) <= 1},
        "veber": {"pass": d["NumRotatableBonds"] <= 10 and d["TPSA"] <= 140},
        "egan": {"pass": d["TPSA"] <= 131.6 and d["MolLogP"] <= 5.88},
        "ghose": {"pass": (160 <= d["MolWt"] <= 480 and -0.4 <= d["MolLogP"] <= 5.6
                           and 40 <= d["MolMR"] <= 130 and 20 <= atoms <= 70)},
        "qed": round(d["qed"], 4),
    }
    return out


def alerts(mol: Any) -> list[dict[str, str]]:
    global _CATALOG
    FilterCatalog = need("rdkit.Chem.FilterCatalog")
    if _CATALOG is None:
        params = FilterCatalog.FilterCatalogParams()
        for cat in ("PAINS_A", "PAINS_B", "PAINS_C", "BRENK", "NIH"):
            params.AddCatalog(getattr(FilterCatalog.FilterCatalogParams.FilterCatalogs, cat))
        _CATALOG = FilterCatalog.FilterCatalog(params)
    found = []
    for entry in _CATALOG.GetMatches(mol):
        props = set(entry.GetPropList())          # FilterCatalogEntry has no HasProp
        source = entry.GetProp("FilterSet") if "FilterSet" in props else ""
        found.append({"catalog": source, "alert": entry.GetDescription()})
    seen, unique = set(), []
    for a in found:
        key = (a["catalog"], a["alert"])
        if key not in seen:
            seen.add(key)
            unique.append(a)
    return unique

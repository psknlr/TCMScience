#!/usr/bin/env python
"""Resolve every source-species name in ``sources/materia.py`` against NCBI Taxonomy.

    python scripts/verify_materia_taxa.py            # writes registry/materia_taxa.json

For each name: an E-utilities ``esearch`` on the taxonomy database (scientific name
first, then any name, which covers synonyms), then ``esummary`` for the id's current
scientific name and rank. The record keeps the name as written, the taxid, NCBI's current
name and the date, so a reviewer can see where a Pharmacopoeia name and NCBI's differ
(``Poria cocos`` → ``Wolfiporia cocos``). A name that resolves to no id, or to several,
is recorded with no taxid and contributes no taxon. Nothing is guessed.

This is the only step that touches the network; NCBI asks for at most three requests a
second without a key, and the script keeps to that.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bioagent.sources.materia import MATERIA, TAXA_FILE  # noqa: E402

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

#: Pharmacopoeia names NCBI files under a different accepted name, where its "any name"
#: index points at the wrong organism. Each was checked by hand.
CURATED = {
    # ChP 肉桂/桂枝: Cinnamomum cassia Presl. NCBI's any-name index sends this string to
    # Neolitsea cassia (a homonym); Chinese cassia is taxid 119260, Cinnamomum aromaticum.
    "Cinnamomum cassia": "Cinnamomum aromaticum",
}

#: Any-name matches a person has checked and accepted (accepted-name changes, not
#: homonyms). A new any-name match is recorded unreviewed and contributes no taxon until
#: it is added here.
REVIEWED = {
    "Acanthopanax gracilistylus", "Alisma orientale", "Alpinia katsumadai", "Amomum compactum",
    "Amomum kravanh", "Amomum longiligulare", "Amomum tsao-ko", "Amomum tsaoko",
    "Amomum villosum", "Aucklandia lappa", "Baphicacanthus cusia", "Belamcanda chinensis",
    "Buthus martensii", "Chinemys reevesii", "Chrysanthemum morifolium", "Cimicifuga dahurica",
    "Cimicifuga foetida", "Cimicifuga heracleifolia", "Citrus aurantium",
    "Clematis terniflora var. mandshurica", "Codonopsis tangshen", "Crassostrea gigas",
    "Crassostrea rivularis", "Cryptotympana pustulata", "Dichroa febrifuga",
    "Dioscorea opposita", "Drynaria roosii", "Euodia rutaecarpa", "Fallopia multiflora",
    "Foeniculum vulgare", "Houpoea officinalis", "Hyriopsis cumingii", "Inula britannica",
    "Leonurus japonicus", "Melia toosendan", "Mentha haplocalyx", "Mesobuthus martensii",
    "Morinda officinalis", "Nepeta tenuifolia", "Notopterygium forbesii",
    "Notopterygium franchetii", "Notopterygium incisum", "Pharbitis nil",
    "Pheretima aspergillum", "Polygonum multiflorum", "Poria cocos", "Psoralea corylifolia",
    "Quisqualis indica", "Reynoutria multiflora", "Saussurea costus",
    "Scolopendra subspinipes mutilans", "Stephania tetrandra", "Typhonium giganteum",
    "Acacia catechu", "Agkistrodon acutus", "Caesalpinia sappan", "Cassia obtusifolia",
    "Cynanchum atratum", "Cynanchum versicolor", "Daemonorops draco", "Dioscorea hypoglauca",
    "Gentiana rigescens", "Ligusticum sinense", "Omphalia lapidescens",
    "Picrorhiza scrophulariiflora", "Sepia esculenta", "Sophora japonica", "Zaocys dhumnades",
    "Carpesium abrotanoides", "Cynanchum glaucescens", "Cynanchum stauntonii",
    "Hydnocarpus anthelminthicus", "Kochia scoparia", "Rhaponticum uniflorum",
    "Vaccaria segetalis", "Vespertilio superans", "Viola yedoensis", "Yulania denudata",
}


def _get(endpoint: str, **params: str) -> dict:
    url = EUTILS + endpoint + "?" + urllib.parse.urlencode({**params, "retmode": "json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                time.sleep(0.35)
                return json.load(resp)
        except Exception:                                    # noqa: BLE001
            time.sleep(2 ** attempt)
    raise RuntimeError(f"NCBI did not answer: {url}")


def resolve(name: str) -> dict:
    if name in CURATED:
        target = resolve(CURATED[name])
        return {**target, "matched_on": "curated synonym",
                "note": f"curated: NCBI files this as {CURATED[name]}"}
    for field in ("Scientific Name", "All Names"):
        ids = _get("esearch.fcgi", db="taxonomy", term=f'"{name}"[{field}]')[
            "esearchresult"]["idlist"]
        if len(ids) == 1:
            summary = _get("esummary.fcgi", db="taxonomy", id=ids[0])["result"][ids[0]]
            rec = {"taxid": ids[0], "scientific_name": summary.get("scientificname", ""),
                   "rank": summary.get("rank", ""), "matched_on": field}
            if field == "All Names":
                rec["reviewed"] = name in REVIEWED
            return rec
        if len(ids) > 1:
            return {"taxid": "", "note": f"ambiguous: {len(ids)} taxa for {field}"}
    return {"taxid": "", "note": "not found in NCBI Taxonomy"}


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--refresh", action="store_true",
                    help="re-query every name; by default a name already resolved in the "
                         "record is reused and only its review status is recomputed")
    args = ap.parse_args(argv)
    names = sorted({n for e in MATERIA.values() for n in e.species_names})
    previous = {}
    if TAXA_FILE.is_file() and not args.refresh:
        previous = json.loads(TAXA_FILE.read_text(encoding="utf-8")).get("names", {})
    out = {}
    for i, name in enumerate(names, 1):
        old = previous.get(name)
        if old and old.get("taxid") and name not in CURATED:
            out[name] = dict(old)
            if old.get("matched_on") == "All Names":
                out[name]["reviewed"] = name in REVIEWED
        else:
            out[name] = resolve(name)
        print(f"[{i}/{len(names)}] {name}: {out[name].get('taxid') or out[name].get('note')}",
              file=sys.stderr)
    TAXA_FILE.parent.mkdir(parents=True, exist_ok=True)
    TAXA_FILE.write_text(json.dumps({
        "source": "NCBI Taxonomy via E-utilities (esearch + esummary)",
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "names": out}, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    missing = [n for n, r in out.items() if not r.get("taxid")]
    unreviewed = [n for n, r in out.items() if r.get("reviewed") is False]
    print(f"unreviewed any-name matches (excluded): {unreviewed}")
    print(f"{len(out) - len(missing)}/{len(out)} names resolved; unresolved: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

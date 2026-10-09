#!/usr/bin/env python3
"""Build the committed open-data extracts: ``python3 studio/corpus/extract_open.py --out studio/corpus/data``.

The inputs of the corpus packs ``lotus`` and ``pathways`` (docs/V2.md §11.1, §11.3). The corpus
build reads the files this writes and never downloads anything; this script is the one place
that does, run by a person when a source is updated, and its output is committed:

* ``lotus-herbs.json.gz`` — for every materia herb (``bioagent.sources.materia``, 401 crude
  drugs) the LOTUS organisms matched to its source species and the natural products LOTUS
  records in them, with the number of distinct references per compound; plus each compound's
  name, formula and 2-D SMILES. LOTUS frozen export 2026-04-13, CC BY 4.0.
* ``pathways.json.gz`` — Reactome's lowest-level *Homo sapiens* pathways with their UniProt
  members (the rows ``bioagent.sources.parsers.reactome`` keeps, so the membership is the one
  ``analysis.network_pharmacology`` reads), and the HGNC approved symbol ↔ UniProt maps. CC0.
* ``SOURCES.json`` — per file: the upstream files (URL, version, size, sha256), the retrieval
  date, licence, citation, what was changed, counts, the output's sha256 and size, and the
  command that rebuilds it.

Every upstream file is pinned (URL, size, sha256; for LOTUS also the md5 Zenodo publishes,
which ``bioagent.acquisition.sources`` pins) and checked before it is read: a file that does not
match is refused, so the same pins give the same bytes. Output is canonical JSON (sorted keys,
compact) gzipped with mtime 0 at level 9, so two runs give identical files.

Matching a herb to LOTUS organisms (``matched_by``): ``"taxid"`` when the organism's NCBI taxon
id (from the export's metadata file, read through NCBI's merged ids) is the verified taxon of
one of the herb's source species (``materia.crude_drugs()``: NCBI-checked, the four
hand-checked herbs included); otherwise ``"name"`` when the organism's name is exactly one of
the species' names (the Pharmacopoeia name, NCBI's scientific name, a curated synonym) and any
taxon LOTUS gives it lies in that species' own lineage — an unrelated taxon refuses the match.
Nothing broader: an organism named as a variety is not its species (粉葛 is not 葛根), and a
genus is not a herb. NCBI's merges and parents come from a pinned monthly taxdump.

``--fixtures`` also writes the small fixtures for fast tests (``studio/corpus/fixtures/``), cut
from the extracts just built; ``--check`` validates written files without downloading.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CORPUS = Path(__file__).resolve().parent
STUDIO = CORPUS.parent
REPO = STUDIO.parent

for _module, _src in (("bioagent", REPO / "BioScience-Harness" / "src"),
                      ("psh", REPO / "PSH-Harness" / "src")):
    if importlib.util.find_spec(_module) is None and _src.is_dir():
        sys.path.insert(0, str(_src))

REBUILD = "python3 studio/corpus/extract_open.py --out studio/corpus/data"
REBUILD_FIXTURES = REBUILD + " --fixtures"
LOTUS_SCHEMA = "tcmstudio.corpus.lotus/1"
PATHWAYS_SCHEMA = "tcmstudio.corpus.pathways/1"
SOURCES_SCHEMA = "tcmstudio.corpus.open-data/1"
LOTUS_FILE = "lotus-herbs.json.gz"
PATHWAYS_FILE = "pathways.json.gz"
#: The committed LOTUS extract must stay under this; past it the longest SMILES are dropped
#: (and SOURCES.json says how many and from what length).
LOTUS_MAX_BYTES = 12_000_000
USER_AGENT = "tcmstudio-extract-open/1 (+https://science.impf.ai)"


@dataclass(frozen=True)
class Upstream:
    """One upstream file, pinned. ``retrieved`` is the day the pin was taken, not the run's
    clock, so a later rebuild from the same bytes writes the same SOURCES.json."""

    key: str
    file: str
    url: str
    version: str
    bytes: int
    sha256: str
    licence: str
    retrieved: str
    md5: str = ""


UPSTREAM: tuple[Upstream, ...] = (
    # The light export: the canonical (structure, organism, reference) triplets, the file
    # bioagent.sources.build reads. 674,454 rows, 674,151 distinct triplets (the export's
    # changes report says 674,151 entries).
    Upstream("lotus", "260413_frozen.csv.gz",
             "https://zenodo.org/api/records/19360665/files/260413_frozen.csv.gz/content",
             "2026-04-13", 20594507,
             "c0744fb4d4395a638739078601836d9472612c1ed6db6c8e4f0b5e8a972ced70",
             "CC-BY-4.0", "2026-10-09", md5="cf0cf2afa2ca4d758b68f2e39d466f5d"),
    # The same pairs joined to Wikidata metadata: organism NCBI taxon ids, compound names,
    # formulas and 2-D SMILES. Read for those columns only; its rows are not the triplets
    # (the join loses 567 triplets and adds 388).
    Upstream("lotus", "260413_frozen_metadata.csv.gz",
             "https://zenodo.org/api/records/19360665/files/260413_frozen_metadata.csv.gz/content",
             "2026-04-13", 90298678,
             "09885dcfb3bc0e6d700fefcd5778e91446a1f9d5d2fbbcbf00eb5d2e83b68844",
             "CC-BY-4.0", "2026-10-09", md5="b17048b3b77daae9ab1e480b6591aabd"),
    # Release 97 (ContentService /data/database/version answered 97 on 2026-10-09); the
    # versioned URLs serve the same bytes as download/current/ did that day.
    Upstream("reactome", "UniProt2Reactome.txt",
             "https://download.reactome.org/97/UniProt2Reactome.txt",
             "97", 43041375,
             "e34fe56a5129d88d8cd67300c279d7e81fa1d3bf86fa0577cf91032c326eaecc",
             "CC0-1.0", "2026-10-09"),
    Upstream("reactome", "ReactomePathways.txt",
             "https://download.reactome.org/97/ReactomePathways.txt",
             "97", 1592393,
             "f6d7a2bf89b5bcfe0250a0bc7f51bff94641447911712b8ff129f5b55e52df3a",
             "CC0-1.0", "2026-10-09"),
    # The dated monthly archive copy (identical to tsv/hgnc_complete_set.txt on 2026-10-09).
    Upstream("hgnc", "hgnc_complete_set_2026-10-06.txt",
             "https://storage.googleapis.com/public-download-files/hgnc/archive/archive/"
             "monthly/tsv/hgnc_complete_set_2026-10-06.txt",
             "2026-10-06", 16973125,
             "8bf6f686e640dbc864a346484626953621b0500d900d26cd085a35c2782d725d",
             "CC0-1.0", "2026-10-09"),
    # NCBI Taxonomy, the dated monthly archive: merged.dmp (an old taxon id LOTUS still
    # carries → the id NCBI merged it into) and nodes.dmp (parents). Used for matching only;
    # nothing of it is written out but taxon ids.
    Upstream("ncbi", "taxdmp_2026-10-01.zip",
             "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump_archive/taxdmp_2026-10-01.zip",
             "2026-10-01", 79650311,
             "d744af371c0b9fc7269d80b49546b6ac4c9ddc52a2fd97bcbfd02783edb5c9eb",
             "LicenseRef-NCBI-public-domain", "2026-10-09"),
)

SOURCE_TEXT: Mapping[str, dict[str, Any]] = {
    "lotus": {
        "name": "LOTUS natural products occurrences (frozen Wikidata export with metadata)",
        "title": "The LOTUS Initiative for Open Natural Products Research: frozen dataset union "
                 "wikidata (with metadata) 2026-04-13",
        "doi": "10.7554/eLife.70780",
        "dataset_doi": "10.5281/zenodo.19360665",
        "zenodo": "https://zenodo.org/records/19360665",
        "licence_url": "https://creativecommons.org/licenses/by/4.0/",
        "citation": "Rutz A, et al. The LOTUS initiative for open knowledge management in natural "
                    "products research. eLife 2022;11:e70780. doi:10.7554/eLife.70780. Data: "
                    "LOTUS frozen export 2026-04-13, Zenodo record 19360665, "
                    "doi:10.5281/zenodo.19360665 (CC BY 4.0).",
    },
    "reactome": {
        "name": "Reactome (UniProt to lowest-level pathway; pathway names)",
        "url": "https://reactome.org/download-data",
        "version_url": "https://reactome.org/ContentService/data/database/version",
        "licence_url": "https://reactome.org/license",
        "citation": "Milacic M, et al. The Reactome Pathway Knowledgebase 2024. Nucleic Acids Res "
                    "2024;52(D1):D672-D678. doi:10.1093/nar/gkad1025.",
    },
    "ncbi": {
        "name": "NCBI Taxonomy (merged taxon ids and parents, for matching only)",
        "url": "https://ftp.ncbi.nlm.nih.gov/pub/taxonomy/taxdump_archive/",
        "licence_url": "https://www.ncbi.nlm.nih.gov/home/about/policies/",
        "citation": "Schoch CL, et al. NCBI Taxonomy: a comprehensive update on curation, "
                    "resources and tools. Database (Oxford) 2020;2020:baaa062. "
                    "doi:10.1093/database/baaa062.",
    },
    "hgnc": {
        "name": "HGNC complete set (approved human gene symbols)",
        "url": "https://www.genenames.org/download/archive/",
        "licence_url": "https://www.genenames.org/about/license/",
        "citation": "HGNC Database, HUGO Gene Nomenclature Committee (HGNC), EMBL-EBI, "
                    "www.genenames.org (complete set 2026-10-06). Seal RL, et al. Genenames.org: "
                    "the HGNC resources in 2023. Nucleic Acids Res 2023;51(D1):D1003-D1009. "
                    "doi:10.1093/nar/gkac888.",
    },
}

LOTUS_CHANGES = (
    "Subset and regrouped, not edited: the export's (structure, organism, reference) triplets "
    "are kept only for organisms matched to a materia herb (NCBI taxon id first, then exact "
    "species name), regrouped per herb with the number of distinct references (Wikidata "
    "reference items) per compound; rows without a valid InChIKey or an organism name are "
    "dropped, as bioagent.sources.parsers.lotus drops them. Compound name = the export's "
    "structure_nameTraditional, else structure_nameIupac, else null; formula = "
    "structure_molecular_formula; smiles = structure_smiles_2D (from the metadata file).")
PATHWAYS_CHANGES = (
    "Subset, not edited: Reactome rows for Homo sapiens with a UniProt accession "
    "(bioagent.sources.parsers.reactome), grouped by lowest-level pathway, members sorted, names "
    "from ReactomePathways.txt; HGNC rows with status Approved and at least one UniProt "
    "accession, reduced to symbol and accessions.")


# ------------------------------------------------------------------------------- output


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def gzip_bytes(raw: bytes) -> bytes:
    """Gzip with no file name and mtime 0: only the content decides the bytes."""
    import io
    out = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0, compresslevel=9) as gz:
        gz.write(raw)
    return out.getvalue()


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def read_json_gz(path: Path) -> Any:
    return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ----------------------------------------------------------------------------- download


def _digests(path: Path) -> tuple[int, str, str]:
    sha, md5, size = hashlib.sha256(), hashlib.md5(), 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            sha.update(chunk)
            md5.update(chunk)
            size += len(chunk)
    return size, sha.hexdigest(), md5.hexdigest()


def _mismatch(up: Upstream, path: Path) -> str:
    size, sha, md5 = _digests(path)
    if size != up.bytes:
        return f"{size} bytes, pinned {up.bytes}"
    if sha != up.sha256:
        return f"sha256 {sha}, pinned {up.sha256}"
    if up.md5 and md5 != up.md5:
        return f"md5 {md5}, pinned {up.md5}"
    return ""


def _bioagent_md5(file: str) -> str:
    """The md5 ``bioagent.acquisition.sources`` pins for a file (LOTUS), if any: the pin
    here must agree with it, so a new LOTUS release cannot slip in under the old name."""
    try:
        from bioagent.acquisition.sources import ACQUIRABLE
    except Exception:                                            # noqa: BLE001
        return ""
    for spec in ACQUIRABLE:
        if spec.url.rsplit("/files/", 1)[-1].split("/", 1)[0] == file:
            checksum = str(getattr(spec, "checksum", "") or "")
            return checksum.split(":", 1)[1] if checksum.startswith("md5:") else ""
    return ""


def fetch(up: Upstream, cache: Path, *, offline: bool, log) -> Path:
    """The pinned file from the cache, downloading it first when missing. Refuses a file
    whose size or hashes are not the pinned ones."""
    pinned = _bioagent_md5(up.file)
    if pinned and up.md5 and pinned != up.md5:
        raise SystemExit(f"{up.file}: bioagent pins md5 {pinned}, this script {up.md5}; "
                         "update UPSTREAM before rebuilding")
    path = cache / up.key / up.file
    if path.is_file():
        bad = _mismatch(up, path)
        if not bad:
            return path
        log(f"{path}: {bad}; downloading again")
    if offline:
        raise SystemExit(f"{up.file} is not in the cache ({path}) and --offline was given")
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    last = ""
    for attempt in range(1, 4):
        try:
            t0 = time.monotonic()
            req = urllib.request.Request(up.url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp, open(part, "wb") as out:
                shutil.copyfileobj(resp, out, 1 << 20)
            log(f"downloaded {up.file} in {time.monotonic() - t0:.0f} s")
            break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = f"{type(exc).__name__}: {exc}"
            log(f"{up.file}: attempt {attempt} failed ({last})")
            time.sleep(2 * attempt)
    else:
        raise SystemExit(f"cannot download {up.url}: {last}")
    bad = _mismatch(up, part)
    if bad:
        raise SystemExit(f"{up.url}: {bad}; refusing it (if the source published a new "
                         "release, pin it in UPSTREAM)")
    part.replace(path)
    return path


def upstream_record(up: Upstream) -> dict[str, Any]:
    rec = {"file": up.file, "url": up.url, "version": up.version, "bytes": up.bytes,
           "sha256": up.sha256, "licence": up.licence, "retrieved": up.retrieved}
    if up.md5:
        rec["md5"] = up.md5
    return rec


# ---------------------------------------------------------------------------------- LOTUS


def _qid(uri: str | None) -> str | None:
    return uri.rsplit("/", 1)[-1] if uri else None


class Taxonomy:
    """NCBI's merged ids and parent links from a taxdump archive (merged.dmp, nodes.dmp)."""

    def __init__(self, archive: Path) -> None:
        import array
        import io
        import zipfile

        self.merged: dict[str, str] = {}
        with zipfile.ZipFile(archive) as z:
            with z.open("merged.dmp") as fh:
                for line in io.TextIOWrapper(fh, "ascii"):
                    old, new = line.split("\t|\t", 2)[:2]
                    self.merged[old.strip()] = new.strip().rstrip("\t|")
            pairs = []
            with z.open("nodes.dmp") as fh:
                for line in io.TextIOWrapper(fh, "ascii"):
                    tax, parent = line.split("\t|\t", 2)[:2]
                    pairs.append((int(tax), int(parent)))
        self.parent = array.array("I", bytes(4 * (max(t for t, _ in pairs) + 1)))
        for tax, parent in pairs:
            self.parent[tax] = parent

    def current(self, taxid: str) -> str:
        return self.merged.get(taxid, taxid)

    def lineage(self, taxid: str) -> set[str]:
        """The taxon and its ancestors (current ids); empty for an id NCBI does not know."""
        out: set[str] = set()
        t = int(self.current(taxid)) if taxid.isdigit() else 0
        while 0 < t < len(self.parent) and str(t) not in out and self.parent[t]:
            out.add(str(t))
            if t == 1:
                break
            t = self.parent[t]
        return out

    def same_lineage(self, a: str, b: str) -> bool:
        """One taxon is the other or descends from it (a variety and its species)."""
        return self.current(a) in self.lineage(b) or self.current(b) in self.lineage(a)


def herb_species(tax: Taxonomy) -> tuple[dict[str, set[str]], dict[str, list[tuple[str, str | None]]], dict[str, dict]]:
    """The materia herbs' species as match keys, taxon ids as NCBI's current ids.

    Returns ``(by_taxid, by_name, entries)``: NCBI taxon id → herbs; exact organism name →
    [(herb, the verified taxon id of that name or None)]; per herb its category and species.
    """
    from bioagent.sources import materia

    taxa = json.loads(Path(materia.TAXA_FILE).read_text(encoding="utf-8")).get("names", {})
    drugs = materia.crude_drugs()
    by_taxid: dict[str, set[str]] = defaultdict(set)
    by_name: dict[str, dict[str, str | None]] = defaultdict(dict)
    entries: dict[str, dict] = {}

    def verified(name: str) -> str | None:
        # The rule crude_drugs applies: NCBI's "any name" index counts only once reviewed.
        rec = taxa.get(name) or {}
        ok = rec.get("taxid") and (rec.get("matched_on") != "All Names" or rec.get("reviewed"))
        return tax.current(str(rec["taxid"])) if ok else None

    for mid, entry in materia.MATERIA.items():
        entries[mid] = {"category": entry.category, "species": list(entry.species_names)}
        if not entry.has_organism:
            continue
        drug = drugs.get(entry.drug_id)
        for sp in (drug.species if drug else ()):
            by_taxid[tax.current(sp.taxid)].add(mid)
            for name in (sp.name, *sp.synonyms):
                by_name[name].setdefault(mid, tax.current(sp.taxid))
        # Names NCBI did not confirm still match, by name only.
        for name in entry.species_names:
            by_name[name].setdefault(mid, verified(name))
            rec = taxa.get(name) or {}
            if verified(name) and rec.get("scientific_name"):
                by_name[rec["scientific_name"]].setdefault(mid, verified(name))
    return dict(by_taxid), {n: sorted(h.items()) for n, h in by_name.items()}, entries


def _taxa_file_record() -> dict[str, Any]:
    from bioagent.sources import materia
    path = Path(materia.TAXA_FILE)
    data = path.read_bytes()
    try:
        rel = path.resolve().relative_to(REPO).as_posix()
    except ValueError:
        rel = path.name
    return {"path": rel, "sha256": sha256_hex(data),
            "checked_at": json.loads(data).get("checked_at"),
            "materia_entries": len(materia.MATERIA)}


def extract_lotus(frozen: Path, metadata: Path, taxdump: Path, *, log) -> tuple[dict[str, Any], dict[str, Any]]:
    """``(lotus-herbs document, stats)`` from the two LOTUS export files."""
    from bioagent.sources.parsers.common import ParseReport, is_inchikey, read_rows

    t0 = time.monotonic()
    tax = Taxonomy(taxdump)
    by_taxid, by_name, entries = herb_species(tax)
    log(f"NCBI taxonomy: {len(tax.merged)} merged ids ({time.monotonic() - t0:.0f} s)")

    # Metadata: organism (Wikidata item) → NCBI taxon ids (as NCBI's current ids; LOTUS still
    # carries ids NCBI has since merged); InChIKey → recorded properties.
    t0 = time.monotonic()
    meta_report = ParseReport("lotus-metadata")
    org_taxids: dict[str, set[str]] = defaultdict(set)
    merged_seen: set[str] = set()
    props: dict[str, set[tuple[str | None, str | None, str | None]]] = defaultdict(set)
    for row in read_rows(metadata, delimiter=",", report=meta_report):
        meta_report.read["rows"] += 1
        org = _qid(row.get("organism_wikidata"))
        taxid = row.get("organism_taxonomy_ncbiid")
        if org and taxid and taxid.isdigit():
            if taxid in tax.merged:
                merged_seen.add(taxid)
            org_taxids[org].add(tax.current(taxid))
        ik = row.get("structure_inchikey")
        if is_inchikey(ik):
            props[ik].add((row.get("structure_nameTraditional") or row.get("structure_nameIupac"),
                           row.get("structure_molecular_formula"),
                           row.get("structure_smiles_2D")))
    log(f"LOTUS metadata: {meta_report.read['rows']} rows, {len(org_taxids)} organisms with an "
        f"NCBI taxon, {len(props)} structures ({time.monotonic() - t0:.0f} s)")

    # Triplets: match each organism once, then collect references per (herb, structure).
    t0 = time.monotonic()
    report = ParseReport("lotus")
    matches: dict[tuple[str | None, str], dict[str, tuple[str | None, str]]] = {}
    conflicts: dict[tuple[str, str], dict[str, Any]] = {}
    across_rank: dict[tuple[str, str], dict[str, Any]] = {}
    organisms: dict[str, set[tuple[str, str | None, str]]] = defaultdict(set)
    refs: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    triplets: set[tuple[str, str | None, str | None]] = set()

    def match(org: str | None, name: str) -> dict[str, tuple[str | None, str]]:
        taxids = org_taxids.get(org or "", set())
        hits: dict[str, tuple[str | None, str]] = {}
        for taxid in sorted(taxids):
            for mid in sorted(by_taxid.get(taxid, ())):
                hits.setdefault(mid, (taxid, "taxid"))
        for mid, ours in by_name.get(name, ()):
            if mid in hits:
                continue
            if ours and taxids and ours not in taxids:
                note = {"herb": mid, "name": name, "our_taxid": ours,
                        "lotus_taxids": sorted(taxids), "wikidata": org}
                if not all(tax.same_lineage(ours, t) for t in taxids):
                    # Same name, unrelated taxon: a homonym or a misfiling on one side, and
                    # neither is resolved by guessing.
                    conflicts[(mid, name)] = note
                    continue
                # Named as our species, filed under a taxon of its own lineage (Wikidata gives
                # the item "Perilla frutescens" the id of var. frutescens): the name decides.
                across_rank[(mid, name)] = note
            hits[mid] = (next(iter(taxids)) if len(taxids) == 1 else None, "name")
        return hits

    for row in read_rows(frozen, delimiter=",", report=report):
        report.read["rows"] += 1
        ik, name = row.get("structure_inchikey"), row.get("organism_name")
        if not is_inchikey(ik) or not name:
            report.drop("row without an InChIKey or an organism name")
            continue
        org, ref = _qid(row.get("organism_wikidata")), _qid(row.get("reference_wikidata"))
        key = (org, name)
        if key not in matches:
            matches[key] = match(org, name)
        hits = matches[key]
        if not hits:
            continue
        triplets.add((ik, org, ref))
        ref_id = ref or (f"doi:{row['reference_doi'].lower()}" if row.get("reference_doi")
                         else f"row:{ik}|{org}")
        for mid, (taxid, how) in hits.items():
            organisms[mid].add((name, taxid, how))
            refs[mid][ik].add(ref_id)
    log(f"LOTUS triplets: {report.read['rows']} rows, {len(triplets)} distinct triplets in "
        f"matched organisms ({time.monotonic() - t0:.0f} s)")

    herbs: dict[str, Any] = {}
    for mid in sorted(organisms):
        occ = sorted(((ik, len(r)) for ik, r in refs[mid].items()), key=lambda x: (-x[1], x[0]))
        herbs[mid] = {
            "organisms": [{"name": n, "taxid": t, "matched_by": how}
                          for n, t, how in sorted(organisms[mid],
                                                  key=lambda o: (o[0], o[1] or "", o[2]))],
            "occurrences": [[ik, n] for ik, n in occ],
        }
    unmatched = {}
    refused = {c["herb"] for c in conflicts.values()}
    for mid, entry in sorted(entries.items()):
        if mid in herbs:
            continue
        reason = ("not_an_organism" if entry["category"] in ("mineral", "other")
                  else "taxon_conflict" if mid in refused else "no_lotus_organism")
        unmatched[mid] = {"category": entry["category"], "species": entry["species"],
                          "reason": reason}

    compounds: dict[str, Any] = {}
    multi = 0
    for ik in sorted({ik for h in herbs.values() for ik, _ in h["occurrences"]}):
        found = props.get(ik)
        if not found:
            compounds[ik] = {"name": None, "formula": None, "smiles": None}
            continue
        if len(found) > 1:
            multi += 1
        # Two recorded forms of one InChIKey (a zwitterion and its neutral form): the first in
        # sort order, so the choice is stable; both are LOTUS's own values.
        name, formula, smiles = min(found, key=lambda p: tuple(x or "" for x in p))
        compounds[ik] = {"name": name, "formula": formula, "smiles": smiles}

    by_how = Counter(o["matched_by"] for h in herbs.values() for o in h["organisms"])
    stats = {
        "herbs_considered": len(entries),
        "herbs_matched": len(herbs),
        "herbs_unmatched": len(unmatched),
        "unmatched_by_reason": dict(Counter(u["reason"] for u in unmatched.values())),
        "unmatched_organisms": sorted(m for m, u in unmatched.items()
                                      if u["reason"] != "not_an_organism"),
        "organisms_matched": dict(by_how),
        "occurrences": sum(len(h["occurrences"]) for h in herbs.values()),
        "compounds": len(compounds),
        "compounds_without_metadata": sum(1 for c in compounds.values() if c["formula"] is None
                                          and c["smiles"] is None and c["name"] is None),
        "compounds_without_name": sum(1 for c in compounds.values() if c["name"] is None),
        "compounds_with_two_recorded_forms": multi,
        "triplets_kept": len(triplets),
        "rows_read": report.read["rows"],
        "dropped": dict(report.dropped),
        "name_matches_refused": sorted(conflicts.values(), key=lambda c: (c["herb"], c["name"])),
        "name_matches_across_rank": sorted(across_rank.values(),
                                           key=lambda c: (c["herb"], c["name"])),
        "lotus_taxids_merged_by_ncbi": len(merged_seen),
        "metadata_rows_read": meta_report.read["rows"],
    }
    if report.warnings or meta_report.warnings:
        stats["warnings"] = {**dict(report.warnings), **dict(meta_report.warnings)}

    ups = [u for u in UPSTREAM if u.key == "lotus"]
    ncbi = next(u for u in UPSTREAM if u.key == "ncbi")
    text = SOURCE_TEXT["lotus"]
    source = {
        "name": text["name"], "title": text["title"], "version": ups[0].version,
        "doi": text["doi"], "dataset_doi": text["dataset_doi"], "zenodo": text["zenodo"],
        "url": ups[0].url, "sha256": ups[0].sha256, "licence": ups[0].licence,
        "licence_url": text["licence_url"], "retrieved": ups[0].retrieved,
        "citation": text["citation"], "files": [upstream_record(u) for u in ups],
        "changes": LOTUS_CHANGES,
        "matching": {
            "order": ["taxid", "name"],
            "taxid": "the organism's NCBI taxon id (LOTUS metadata file, read through NCBI's "
                     "merged ids) is the verified taxon of one of the herb's source species "
                     "(bioagent.sources.materia.crude_drugs)",
            "name": "otherwise, the organism's name is exactly one of the species' names "
                    "(materia name, NCBI scientific name, curated synonym), and any taxon id "
                    "LOTUS gives it is in that species' own lineage (else the match is refused "
                    "as a taxon conflict)",
            "not_matched": "organisms named as a variety, subspecies or other taxon below or "
                           "beside the listed species; genera",
            "materia": _taxa_file_record(),
            "ncbi_taxonomy": {"file": ncbi.file, "url": ncbi.url, "sha256": ncbi.sha256,
                              "citation": SOURCE_TEXT["ncbi"]["citation"]},
        },
        "refs": "distinct references (Wikidata reference items) reporting the compound in any "
                "of the herb's matched organisms",
    }
    doc = {"schema": LOTUS_SCHEMA, "source": source, "herbs": herbs, "compounds": compounds,
           "unmatched": unmatched}
    return doc, stats


def cap_smiles(doc: dict[str, Any], max_bytes: int, *, log) -> dict[str, Any] | None:
    """Drop the longest SMILES until the gzipped document fits ``max_bytes``; None when it
    already fits. The dropped values become null and the source records the cap."""
    size = len(gzip_bytes(canonical(doc)))
    if size <= max_bytes:
        return None
    lengths = sorted({len(c["smiles"]) for c in doc["compounds"].values() if c["smiles"]},
                     reverse=True)
    lo, hi = 0, len(lengths) - 1                     # binary search on the length cap
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        cap = lengths[mid]
        trial = {ik: (dict(c, smiles=None) if c["smiles"] and len(c["smiles"]) > cap else c)
                 for ik, c in doc["compounds"].items()}
        fits = len(gzip_bytes(canonical({**doc, "compounds": trial}))) <= max_bytes
        if fits:
            best, lo = cap, mid + 1
        else:
            hi = mid - 1
    if best is None:
        raise SystemExit("the LOTUS extract does not fit even without SMILES")
    dropped = 0
    for c in doc["compounds"].values():
        if c["smiles"] and len(c["smiles"]) > best:
            c["smiles"] = None
            dropped += 1
    doc["source"]["smiles_cap"] = {"max_length": best, "dropped": dropped,
                                   "reason": f"keeps the committed file under {max_bytes} bytes"}
    log(f"LOTUS: dropped {dropped} SMILES longer than {best} characters (was {size} bytes)")
    return doc["source"]["smiles_cap"]


# ------------------------------------------------------------------------------ pathways


def extract_pathways(u2r: Path, names_file: Path, hgnc: Path, *, log) -> tuple[dict[str, Any], dict[str, Any]]:
    from bioagent.sources.parsers.common import ParseReport, read_rows
    from bioagent.sources.parsers.reactome import parse_reactome

    t0 = time.monotonic()
    parsed = parse_reactome(u2r)                  # Homo sapiens, UniProt accessions only
    members: dict[str, set[str]] = defaultdict(set)
    for edge in parsed.edges:
        if edge.get("predicate") == "participates_in":
            members[edge["object"].removeprefix("reactome:")].add(
                edge["subject"].removeprefix("uniprot:"))
    row_names = {n["id"].removeprefix("reactome:"): n.get("name") for n in parsed.nodes
                 if str(n.get("id", "")).startswith("reactome:")}
    listed: dict[str, str] = {}
    for row in read_rows(names_file, fieldnames=("pathway", "name", "species")):
        if row.get("species") == "Homo sapiens" and row.get("pathway") and row.get("name"):
            listed[row["pathway"]] = row["name"]
    pathways = {}
    differ, unlisted = 0, 0
    for pid in sorted(members):
        name = listed.get(pid)
        if name is None:
            unlisted += 1
            name = row_names.get(pid)
        elif row_names.get(pid) and row_names[pid] != name:
            differ += 1
        pathways[pid] = {"name": name, "members": sorted(members[pid])}
    log(f"Reactome: {len(pathways)} human lowest-level pathways, "
        f"{len(set().union(*members.values()))} proteins ({time.monotonic() - t0:.0f} s)")

    hgnc_report = ParseReport("hgnc")
    first: dict[str, str] = {}
    every: dict[str, list[str]] = {}
    by_acc: dict[str, list[str]] = defaultdict(list)
    for row in read_rows(hgnc, report=hgnc_report):
        hgnc_report.read["rows"] += 1
        if row.get("status") != "Approved":
            hgnc_report.drop("status is not Approved")
            continue
        symbol = row.get("symbol")
        accs = [a.strip() for a in (row.get("uniprot_ids") or "").split("|") if a.strip()]
        if not symbol or not accs:
            hgnc_report.drop("no UniProt accession")
            continue
        accs = list(dict.fromkeys(accs))
        first[symbol] = accs[0]
        every[symbol] = accs
        for acc in accs:
            by_acc[acc].append(symbol)
    # An accession two approved symbols both list (a readthrough and its parts) names no one
    # gene: it is left out of uniprot_to_symbol and kept, with its symbols, in the ambiguous map.
    uniprot_to_symbol = {acc: syms[0] for acc, syms in sorted(by_acc.items()) if len(syms) == 1}
    ambiguous = {acc: sorted(syms) for acc, syms in sorted(by_acc.items()) if len(syms) > 1}
    proteins = set().union(*members.values())
    stats = {
        "pathways": len(pathways),
        "proteins": len(proteins),
        "memberships": sum(len(m) for m in members.values()),
        "pathway_sizes": {"min": min(map(len, members.values())),
                          "max": max(map(len, members.values())),
                          "in_5_to_500": sum(1 for m in members.values() if 5 <= len(m) <= 500)},
        "names_from_reactome_pathways_txt": len(pathways) - unlisted,
        "names_differing_from_uniprot2reactome": differ,
        "reactome_report": parsed.report.as_dict(),
        "hgnc_rows_read": hgnc_report.read["rows"],
        "hgnc_dropped": dict(hgnc_report.dropped),
        "symbols": len(first),
        "symbols_with_several_accessions": sum(1 for a in every.values() if len(a) > 1),
        "accessions": len(by_acc),
        "accessions_with_several_symbols": len(ambiguous),
        "pathway_proteins_with_a_symbol": len(proteins & uniprot_to_symbol.keys()),
    }
    sources = []
    for key in ("reactome", "hgnc"):
        ups = [u for u in UPSTREAM if u.key == key]
        text = SOURCE_TEXT[key]
        sources.append({"key": key, "name": text["name"], "version": ups[0].version,
                        "url": text["url"], "licence": ups[0].licence,
                        "licence_url": text["licence_url"], "retrieved": ups[0].retrieved,
                        "citation": text["citation"],
                        "files": [upstream_record(u) for u in ups]})
    sources[0]["version_url"] = SOURCE_TEXT["reactome"]["version_url"]
    doc = {"schema": PATHWAYS_SCHEMA, "sources": sources, "pathways": pathways,
           "symbol_to_uniprot": dict(sorted(first.items())),
           "symbol_to_uniprot_all": dict(sorted(every.items())),
           "uniprot_to_symbol": uniprot_to_symbol,
           "uniprot_to_symbols_ambiguous": ambiguous,
           "changes": PATHWAYS_CHANGES}
    return doc, stats


# ------------------------------------------------------------------------------ fixtures

#: The fixture herbs: the 黄芪/甘草 pair the tests name, the other three herbs of 葛根芩连汤
#: (the pipeline's gold formula), and one mineral, which LOTUS cannot cover.
FIXTURE_HERBS = ("gancao", "huangqi", "gegen", "huangqin", "shigao")
#: Commonly reported network-pharmacology targets; the pathway fixture is the smallest
#: Reactome pathways holding them, so a fixture query has hits.
FIXTURE_SYMBOLS = ("PTGS2", "TNF", "IL6", "AKT1", "MAPK1", "ESR1", "NOS2", "CASP3", "TP53",
                   "VEGFA", "EGFR", "JUN", "MYC", "IL1B", "PPARG", "NFKBIA", "RELA", "BCL2",
                   "STAT3", "HMOX1")
FIXTURE_PATHWAYS = 30


def lotus_fixture(doc: dict[str, Any]) -> dict[str, Any]:
    herbs = {h: doc["herbs"][h] for h in FIXTURE_HERBS if h in doc["herbs"]}
    iks = sorted({ik for h in herbs.values() for ik, _ in h["occurrences"]})
    source = dict(doc["source"], fixture={
        "of": LOTUS_FILE, "herbs": list(FIXTURE_HERBS), "rebuild": REBUILD_FIXTURES,
        "note": "complete records of these herbs, cut from the full extract"})
    return {"schema": LOTUS_SCHEMA, "source": source, "herbs": herbs,
            "compounds": {ik: doc["compounds"][ik] for ik in iks},
            "unmatched": {h: doc["unmatched"][h] for h in FIXTURE_HERBS if h in doc["unmatched"]}}


def pathways_fixture(doc: dict[str, Any]) -> dict[str, Any]:
    seeds = {doc["symbol_to_uniprot"][s] for s in FIXTURE_SYMBOLS if s in doc["symbol_to_uniprot"]}
    chosen: list[str] = []
    covered: set[str] = set()
    # Pathways of 10-40 proteins holding a seed: first the smallest that adds a seed not yet
    # held, then the next smallest, until 30 (about 330 proteins, every seed held).
    candidates = sorted(((len(p["members"]), pid) for pid, p in doc["pathways"].items()
                         if seeds & set(p["members"]) and 10 <= len(p["members"]) <= 40))
    for size, pid in candidates:
        new = seeds & set(doc["pathways"][pid]["members"]) - covered
        if new:
            chosen.append(pid)
            covered |= new
    for size, pid in candidates:
        if len(chosen) >= FIXTURE_PATHWAYS:
            break
        if pid not in chosen:
            chosen.append(pid)
    pathways = {pid: doc["pathways"][pid] for pid in sorted(chosen[:FIXTURE_PATHWAYS])}
    proteins = set().union(*(set(p["members"]) for p in pathways.values()))
    symbols = sorted(s for s, accs in doc["symbol_to_uniprot_all"].items()
                     if proteins & set(accs))
    sources = [dict(s, fixture={"of": PATHWAYS_FILE, "rebuild": REBUILD_FIXTURES,
                                "note": f"{len(pathways)} complete Reactome pathways holding "
                                        f"common target proteins, and the HGNC symbols of "
                                        f"their {len(proteins)} proteins"})
               for s in doc["sources"]]
    return {"schema": PATHWAYS_SCHEMA, "sources": sources, "pathways": pathways,
            "symbol_to_uniprot": {s: doc["symbol_to_uniprot"][s] for s in symbols},
            "symbol_to_uniprot_all": {s: doc["symbol_to_uniprot_all"][s] for s in symbols},
            "uniprot_to_symbol": {a: s for a, s in doc["uniprot_to_symbol"].items()
                                  if a in proteins},
            "uniprot_to_symbols_ambiguous": {a: s for a, s in
                                             doc["uniprot_to_symbols_ambiguous"].items()
                                             if a in proteins},
            "changes": doc["changes"]}


# --------------------------------------------------------------------------------- check

_PATHWAY_ID = re.compile(r"^R-HSA-\d+$")


def check_lotus(doc: Any, *, full: bool) -> list[str]:
    from bioagent.sources import materia
    from bioagent.sources.parsers.common import is_inchikey

    errors: list[str] = []
    if not isinstance(doc, dict) or doc.get("schema") != LOTUS_SCHEMA:
        return [f"schema is not {LOTUS_SCHEMA}"]
    src = doc.get("source") or {}
    for key in ("name", "version", "doi", "zenodo", "url", "sha256", "licence", "retrieved",
                "citation"):
        if not src.get(key):
            errors.append(f"source.{key} is missing")
    herbs, compounds = doc.get("herbs") or {}, doc.get("compounds") or {}
    used: set[str] = set()
    for mid, rec in herbs.items():
        if mid not in materia.MATERIA:
            errors.append(f"herb {mid} is not a materia id")
        orgs = rec.get("organisms") or []
        if not orgs:
            errors.append(f"herb {mid} has no organism")
        for o in orgs:
            if o.get("matched_by") not in ("taxid", "name") or not o.get("name") or \
                    not (o.get("taxid") is None or str(o["taxid"]).isdigit()):
                errors.append(f"herb {mid}: bad organism {o}")
        occ = rec.get("occurrences") or []
        if occ != sorted(occ, key=lambda x: (-x[1], x[0])):
            errors.append(f"herb {mid}: occurrences not sorted by refs desc, then InChIKey")
        if len({ik for ik, _ in occ}) != len(occ):
            errors.append(f"herb {mid}: repeated InChIKey")
        for ik, n in occ:
            if not is_inchikey(ik) or not isinstance(n, int) or n < 1:
                errors.append(f"herb {mid}: bad occurrence {[ik, n]}")
            used.add(ik)
    if used != set(compounds):
        errors.append(f"compounds and occurrences differ ({len(used ^ set(compounds))} keys)")
    for ik, c in compounds.items():
        if set(c) != {"name", "formula", "smiles"}:
            errors.append(f"compound {ik}: keys {sorted(c)}")
    unmatched = doc.get("unmatched") or {}
    if set(unmatched) & set(herbs):
        errors.append("a herb is both matched and unmatched")
    if full and set(unmatched) | set(herbs) != set(materia.MATERIA):
        errors.append("matched + unmatched herbs are not the materia table")
    for mid in ("gancao", "huangqi"):
        if mid not in herbs:
            errors.append(f"{mid} is not matched")
    return errors


def check_pathways(doc: Any, *, full: bool) -> list[str]:
    from bioagent.sources.parsers.common import is_uniprot

    errors: list[str] = []
    if not isinstance(doc, dict) or doc.get("schema") != PATHWAYS_SCHEMA:
        return [f"schema is not {PATHWAYS_SCHEMA}"]
    if {s.get("key") for s in doc.get("sources") or []} != {"reactome", "hgnc"}:
        errors.append("sources are not reactome + hgnc")
    for pid, p in (doc.get("pathways") or {}).items():
        m = p.get("members") or []
        if not _PATHWAY_ID.match(pid) or not p.get("name"):
            errors.append(f"pathway {pid}: bad id or name")
        if m != sorted(set(m)) or not all(is_uniprot(a) for a in m):
            errors.append(f"pathway {pid}: members not sorted, unique UniProt accessions")
    first, every = doc.get("symbol_to_uniprot") or {}, doc.get("symbol_to_uniprot_all") or {}
    if set(first) != set(every) or any(every[s][0] != a for s, a in first.items()):
        errors.append("symbol_to_uniprot is not the first of symbol_to_uniprot_all")
    for acc, sym in (doc.get("uniprot_to_symbol") or {}).items():
        if acc not in every.get(sym, ()):
            errors.append(f"uniprot_to_symbol {acc} → {sym} not in symbol_to_uniprot_all")
    if full and len(doc.get("pathways") or {}) < 1000:
        errors.append("fewer than 1000 pathways")
    return errors


def check_file(path: Path, kind: str, *, full: bool) -> list[str]:
    data = path.read_bytes()
    errors = []
    if data[:2] != b"\x1f\x8b" or data[4:8] != b"\0\0\0\0":
        errors.append(f"{path.name}: not a gzip with mtime 0")
    raw = gzip.decompress(data)
    doc = json.loads(raw)
    if canonical(doc) != raw:
        errors.append(f"{path.name}: not canonical JSON (sorted keys, compact)")
    checker = check_lotus if kind == "lotus" else check_pathways
    errors += [f"{path.name}: {e}" for e in checker(doc, full=full)]
    return errors


def check(out: Path, fixtures: Path | None) -> list[str]:
    errors: list[str] = []
    sources = json.loads((out / "SOURCES.json").read_text(encoding="utf-8"))
    for name, kind in ((LOTUS_FILE, "lotus"), (PATHWAYS_FILE, "pathways")):
        path = out / name
        if not path.is_file():
            errors.append(f"{name} is missing")
            continue
        errors += check_file(path, kind, full=True)
        rec = (sources.get("files") or {}).get(name) or {}
        data = path.read_bytes()
        if rec.get("sha256") != sha256_hex(data) or rec.get("bytes") != len(data):
            errors.append(f"SOURCES.json does not describe {name} as it is")
    if (out / LOTUS_FILE).is_file() and (out / LOTUS_FILE).stat().st_size > LOTUS_MAX_BYTES:
        errors.append(f"{LOTUS_FILE} is over {LOTUS_MAX_BYTES} bytes")
    if fixtures is not None:
        for name, kind in (("lotus-mini.json.gz", "lotus"), ("pathways-mini.json.gz", "pathways")):
            if (fixtures / name).is_file():
                errors += check_file(fixtures / name, kind, full=False)
            else:
                errors.append(f"fixture {name} is missing")
    return errors


# ---------------------------------------------------------------------------------- main


def build(out: Path, cache: Path, *, offline: bool, fixtures: Path | None, log) -> dict[str, Any]:
    paths = {u.file: fetch(u, cache, offline=offline, log=log) for u in UPSTREAM}

    lotus, lotus_stats = extract_lotus(paths["260413_frozen.csv.gz"],
                                       paths["260413_frozen_metadata.csv.gz"],
                                       paths["taxdmp_2026-10-01.zip"], log=log)
    cap = cap_smiles(lotus, LOTUS_MAX_BYTES, log=log)
    pathways, pathway_stats = extract_pathways(
        paths["UniProt2Reactome.txt"], paths["ReactomePathways.txt"],
        paths["hgnc_complete_set_2026-10-06.txt"], log=log)

    files: dict[str, Any] = {}
    for name, doc, stats, keys, licence, changes in (
            (LOTUS_FILE, lotus, lotus_stats, ("lotus", "ncbi"), "CC-BY-4.0", LOTUS_CHANGES),
            (PATHWAYS_FILE, pathways, pathway_stats, ("reactome", "hgnc"), "CC0-1.0",
             PATHWAYS_CHANGES)):
        raw = canonical(doc)
        data = gzip_bytes(raw)
        write_atomic(out / name, data)
        log(f"wrote {out / name}: {len(data)} bytes ({len(raw)} raw)")
        files[name] = {
            "schema": doc["schema"], "sha256": sha256_hex(data), "bytes": len(data),
            "raw_bytes": len(raw), "rebuild": REBUILD, "licence": licence,
            "upstream": [dict(upstream_record(u), source=u.key,
                              citation=SOURCE_TEXT[u.key]["citation"])
                         for u in UPSTREAM if u.key in keys],
            "changes": changes, "counts": stats,
        }
    files[LOTUS_FILE]["attribution"] = (
        "Natural-product occurrences from LOTUS (Rutz et al., eLife 2022, "
        "doi:10.7554/eLife.70780), frozen export 2026-04-13, Zenodo doi:10.5281/zenodo.19360665, "
        "licensed CC BY 4.0; subset and regrouped per herb by TCMScience Studio.")
    files[LOTUS_FILE]["inputs_from_repo"] = [lotus["source"]["matching"]["materia"]]
    files[LOTUS_FILE]["matching"] = {k: v for k, v in lotus["source"]["matching"].items()
                                     if k != "materia"}
    # The committed file's budget, and the SMILES dropped to meet it (null: none were).
    files[LOTUS_FILE]["size_limit_bytes"] = LOTUS_MAX_BYTES
    files[LOTUS_FILE]["smiles_cap"] = cap
    files[PATHWAYS_FILE]["attribution"] = (
        "Pathways from Reactome release 97 (CC0); gene symbols from HGNC (CC0), "
        "www.genenames.org.")
    manifest = {"schema": SOURCES_SCHEMA, "rebuild": REBUILD, "rebuild_fixtures": REBUILD_FIXTURES,
                "note": "CI never downloads these; the corpus build reads the committed files.",
                "files": files}
    write_atomic(out / "SOURCES.json",
                 (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1) + "\n")
                 .encode("utf-8"))

    if fixtures is not None:
        for name, doc in (("lotus-mini.json.gz", lotus_fixture(lotus)),
                          ("pathways-mini.json.gz", pathways_fixture(pathways))):
            data = gzip_bytes(canonical(doc))
            write_atomic(fixtures / name, data)
            log(f"wrote {fixtures / name}: {len(data)} bytes")
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--out", type=Path, default=CORPUS / "data",
                        help="where the extracts and SOURCES.json go (default: studio/corpus/data)")
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache" / "tcmstudio" / "open-data",
                        help="where upstream files are downloaded and reused")
    parser.add_argument("--offline", action="store_true",
                        help="use only files already in --cache")
    parser.add_argument("--fixtures", action="store_true",
                        help="also write the test fixtures (studio/corpus/fixtures/)")
    parser.add_argument("--fixtures-out", type=Path, default=CORPUS / "fixtures")
    parser.add_argument("--check", action="store_true",
                        help="validate the files in --out (and the fixtures) instead of building")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    log = (lambda _m: None) if args.quiet else (lambda m: print(m, file=sys.stderr, flush=True))

    if args.check:
        errors = check(args.out, args.fixtures_out if args.fixtures_out.is_dir() else None)
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        if not errors:
            log(f"ok: {args.out}")
        return 1 if errors else 0
    t0 = time.monotonic()
    build(args.out, args.cache, offline=args.offline,
          fixtures=args.fixtures_out if args.fixtures else None, log=log)
    errors = check(args.out, args.fixtures_out if args.fixtures else None)
    for e in errors:
        print(f"error: {e}", file=sys.stderr)
    log(f"done in {time.monotonic() - t0:.0f} s")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

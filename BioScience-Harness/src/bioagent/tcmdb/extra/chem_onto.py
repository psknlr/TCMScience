"""Chemical identity, biochemical reactions, id reconciliation and food composition.

Four snapshot datasets, each checked from the harness on 2026-10-01:

* ``chebi`` (ChEBI release 255): where natural products were found (organism, part,
  paper), the ontology's chemical classes and roles, names and structures. Its
  structures map ChEBI ids, primary and secondary, to InChIKeys for the crosswalk.
* ``rhea`` (Rhea release 142): every reaction's participants with their side or role,
  for the undirected reaction and for its left-to-right, right-to-left and
  bidirectional forms, and the UniProtKB/Swiss-Prot enzymes annotated with each
  reaction in the direction UniProtKB gives.
* ``metanetx`` (MNXref 4.5): an identifier-reconciliation layer. It writes no relation
  rows (a mapping is not evidence); its tables feed the crosswalk.
* ``fooddata_central`` (USDA FoodData Central): nutrient amounts per 100 g of the
  Foundation Foods (April 2026) and SR Legacy (April 2018) foods.

Some files are archives or RDF, which the loader does not read; the readers below
stream them. For the archive readers, ``FileSpec.sheet`` names the archive member a
table comes from (the part of a multi-part file, as it names a workbook's sheet).

Where a reader leaves columns out, it says which and why: ChEBI's molfiles, MNXref's
InChI and SMILES strings and its cross-reference descriptions (names) hold most of
those files' bytes and none of what the extractors or the crosswalk read. Every value
that is loaded is kept as the file wrote it.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import tarfile
import zipfile
from pathlib import Path
from typing import Any, Iterator
from xml.etree import ElementTree

from ..rowkit import Row, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec
from ..store import StoreError, open_text

__all__ = ["DATASETS", "EXTRACTORS", "KINDS", "READERS"]

#: Relation kinds this module adds. ChEBI's ontology states a compound's chemical class
#: (is_a) and its roles (has_role: antioxidant, plant metabolite, an EC inhibitor); no
#: registered kind is an ontology assertion about a compound.
KINDS: dict[str, tuple[str, str]] = {
    "compound_class": ("compound", "chemical_class"),
    "compound_role": ("compound", "role"),
}


# ===================================================================== readers
def _quoted_tsv(path: Path, drop: tuple[str, ...] = ()) -> Iterator[list[str | None]]:
    """Tab-separated with CSV quoting, as PostgreSQL's ``COPY ... CSV`` writes it.

    ChEBI's flat files quote a field that holds a tab, a quote or a line break (the
    molfiles span many lines) and write an empty string as ``""``. Columns named in
    ``drop`` are left out; every other value is kept as written.
    """
    with open_text(path) as fh:
        reader = csv.reader(fh, delimiter="\t", quotechar='"', doublequote=True)
        header = next(reader, None)
        if header is None:
            return
        keep = [i for i, name in enumerate(header) if name.strip() not in drop]
        yield [header[i].strip() for i in keep]
        for row in reader:
            if not row or not any(c.strip() for c in row):
                continue
            row = row + [""] * (len(header) - len(row))
            yield [(row[i].strip() or None) for i in keep]


def _chebi_tsv(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    return _quoted_tsv(path)


def _chebi_structures(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """``structures.tsv`` without its molfile column (most of the file's bytes)."""
    return _quoted_tsv(path, drop=("molfile",))


# ------------------------------------------------------------------- Rhea RDF/XML
_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
_RDFS = "{http://www.w3.org/2000/01/rdf-schema#}"
_RH = "{http://rdf.rhea-db.org/}"
_RH_URI = "http://rdf.rhea-db.org/"
_CHEBI_URI = "http://purl.obolibrary.org/obo/CHEBI_"
_REACTION_CLASSES = {"Reaction": "undirected", "DirectionalReaction": "directional",
                     "BidirectionalReaction": "bidirectional"}


def _local(uri: str | None) -> str:
    return (uri or "").rsplit("/", 1)[-1]


def _rhea_graph(path: Path) -> dict[str, dict[str, list[str]]]:
    """Subject -> predicate -> values, for the Rhea predicates the readers use.

    Rhea's RDF/XML states one subject in several ``rdf:Description`` blocks (a side's
    participants are added one block at a time), so the blocks are merged by subject.
    """
    wanted = {f"{_RH}{p}" for p in (
        "accession", "equation", "status", "isTransport", "isChemicallyBalanced", "side",
        "substrates", "products", "substratesOrProducts", "compound", "location", "name",
        "chebi", "reactivePart", "underlyingChebi", "polymerizationIndex", "coefficient",
        "curatedOrder", "ec", "citation")} | {f"{_RDFS}subClassOf"}
    graph: dict[str, dict[str, list[str]]] = {}
    with open_text(path) as fh:
        context = ElementTree.iterparse(fh, events=("start", "end"))
        root = None
        for event, elem in context:
            if event == "start":
                if root is None:
                    root = elem
                continue
            if elem.tag != f"{_RDF}Description":
                continue
            about = elem.get(f"{_RDF}about")
            if about and about.startswith(_RH_URI):
                node = graph.setdefault(_local(about), {})
                for child in elem:
                    tag = child.tag
                    if tag in wanted or (tag.startswith(f"{_RH}contains") and tag != f"{_RH}contains"):
                        value = child.get(f"{_RDF}resource") or (child.text or "").strip()
                        node.setdefault(tag.replace(_RH, "").replace(_RDFS, "rdfs:"),
                                        []).append(value)
            elem.clear()
            if root is not None:
                root.clear()
    return graph


def _rhea_reactions(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """One row per reaction (undirected, directional and bidirectional)."""
    yield ["reaction_id", "accession", "reaction_class", "status", "equation",
           "is_transport", "is_chemically_balanced", "ec", "pubmed"]
    for subject, node in sorted(_rhea_graph(path).items()):
        classes = [_REACTION_CLASSES.get(_local(c)) for c in node.get("rdfs:subClassOf", [])]
        kind = next((c for c in classes if c), None)
        if kind is None or not subject.isdigit():
            continue
        yield [subject, (node.get("accession") or [None])[0], kind,
               _local((node.get("status") or [None])[0]) or None,
               (node.get("equation") or [None])[0],
               (node.get("isTransport") or [None])[0],
               (node.get("isChemicallyBalanced") or [None])[0],
               ";".join(_local(e) for e in node.get("ec", [])) or None,
               ";".join(_local(c) for c in node.get("citation", [])
                        if "pubmed" in c) or None]


#: How a reaction names the side a participant is on: an undirected reaction has a left
#: and a right side; a directional one, substrates and products; a bidirectional one,
#: two sides each of which is substrates or products.
_ROLES = (("side", None), ("substrates", "substrate"), ("products", "product"),
          ("substratesOrProducts", "substrate or product"))


def _rhea_participants(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """One row per reaction, side and participant, resolved from the RDF graph."""
    graph = _rhea_graph(path)
    coefficient = {s: n["coefficient"][0] for s, n in graph.items()
                   if s.startswith("contains") and n.get("coefficient")}
    yield ["reaction_id", "role", "side", "coefficient", "location", "compound_accession",
           "compound_type", "compound_name", "chebi", "reactive_part_chebi",
           "polymerization_index"]
    for subject, node in sorted(graph.items()):
        if not subject.isdigit():
            continue
        for predicate, role in _ROLES:
            for side_uri in node.get(predicate, []):
                side = _local(side_uri)
                letter = side.rsplit("_", 1)[-1]
                word = role or {"L": "left side", "R": "right side"}.get(letter, letter)
                for pred, parts in sorted(graph.get(side, {}).items()):
                    if not pred.startswith("contains"):
                        continue
                    for part_uri in parts:
                        part = graph.get(_local(part_uri), {})
                        compound_id = _local((part.get("compound") or [None])[0])
                        compound = graph.get(compound_id, {})
                        ctype = next((_local(c) for c in compound.get("rdfs:subClassOf", [])
                                      if c.startswith(_RH_URI)), None)
                        chebi = (compound.get("chebi") or compound.get("underlyingChebi")
                                 or [None])[0]
                        reactive = [graph.get(_local(rp), {}).get("chebi", [None])[0]
                                    for rp in compound.get("reactivePart", [])]
                        yield [subject, word, letter, coefficient.get(pred),
                               _local((part.get("location") or [None])[0]) or None,
                               (compound.get("accession") or [None])[0], ctype,
                               (compound.get("name") or [None])[0],
                               _chebi_id(chebi),
                               ";".join(c for c in (_chebi_id(r) for r in reactive) if c)
                               or None,
                               (compound.get("polymerizationIndex") or [None])[0]]


def _chebi_id(value: str | None) -> str | None:
    """``chebi:<n>`` from a ChEBI URI, ``CHEBI:<n>`` accession or bare number."""
    text = (value or "").strip()
    m = re.search(r"(?:CHEBI[_:])?(\d+)$", text)
    return f"chebi:{m.group(1)}" if text and m else None


# --------------------------------------------------------------------- archives
def _archive_member(path: Path, spec: FileSpec) -> io.TextIOBase:
    """A text handle on the member ``spec.sheet`` of a zip or tar.gz archive."""
    member = spec.sheet
    if not member:
        raise StoreError(f"{path.name}: the spec names no archive member (FileSpec.sheet)")
    if path.name.endswith((".tar.gz", ".tgz")):
        tf = tarfile.open(path, "r:gz")
        for info in tf:
            if info.name.rsplit("/", 1)[-1] == member:
                handle = tf.extractfile(info)
                if handle is not None:
                    return io.TextIOWrapper(handle, encoding="utf-8-sig", errors="replace",
                                            newline="")
        tf.close()
    elif path.suffix == ".zip":
        archive = zipfile.ZipFile(path)
        for name in archive.namelist():
            if name.rsplit("/", 1)[-1] == member:
                return io.TextIOWrapper(archive.open(name), encoding="utf-8-sig",
                                        errors="replace", newline="")
    raise StoreError(f"{path.name}: no member {member!r}")


def _zip_csv(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """A CSV member of a zip archive (FoodData Central's downloads hold ~20 tables)."""
    with _archive_member(path, spec) as fh:
        for row in csv.reader(fh):
            if row and any(c.strip() for c in row):
                yield [(c.strip() or None) for c in row]


#: MNXref columns not loaded, per member: InChI and SMILES are ~85% of chem_prop.tsv
#: (810 MB), the names in chem_xref's description ~80% of chem_xref.tsv (680 MB).
_MNX_DROP = {"chem_prop.tsv": ("InChI", "SMILES"), "chem_xref.tsv": ("description",)}
#: The one machine-readable value of chem_xref's description, kept as its own column.
_MNX_SECONDARY = "secondary/obsolete/fantasy identifier"


def _mnxref(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """An MNXref TSV member: ~390 '#' licence lines, the last of them the header."""
    drop = _MNX_DROP.get(spec.sheet or "", ())
    header: list[str] | None = None
    keep: list[int] = []
    desc = -1
    with _archive_member(path, spec) as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if line.startswith("#"):
                header = line[1:].split("\t")
                continue
            if header is not None and not keep:
                keep = [i for i, n in enumerate(header) if n not in drop]
                desc = header.index("description") if "description" in drop else -1
                yield [header[i] for i in keep] + (["secondary_flag"] if desc >= 0 else [])
            if not line.strip() or header is None:
                continue
            parts = line.split("\t") + [""] * len(header)
            out = [(parts[i].strip() or None) for i in keep]
            if desc >= 0:
                out.append(_MNX_SECONDARY if parts[desc].startswith(_MNX_SECONDARY) else None)
            yield out


def _mnxref_licences(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """The upstream resources an MNXref file names in its header, with their licences."""
    yield ["resource", "version", "date", "url", "license"]
    entry: dict[str, Any] = {}
    field = ""

    def flush() -> Iterator[list[str | None]]:
        if entry.get("RESOURCE"):
            yield [entry.get("RESOURCE"), entry.get("VERSION"), entry.get("DATE"),
                   entry.get("URL"), " ".join(entry.get("LICENSE", [])).strip() or None]

    with _archive_member(path, spec) as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            text = line[1:].rstrip("\r\n")
            m = re.match(r"^([A-Z]+):\s*(.*)$", text)
            if m:
                key, value = m.group(1), m.group(2).strip()
                if key == "RESOURCE":
                    yield from flush()
                    entry = {}
                field = key
                if key == "LICENSE":
                    entry["LICENSE"] = [value] if value else []
                else:
                    entry[key] = value
            elif field == "LICENSE" and text.startswith("\t"):
                part = re.sub(r"\s+", " ", text).strip()
                if part:
                    entry.setdefault("LICENSE", []).append(part)
        yield from flush()


READERS = {"chebi_tsv": _chebi_tsv, "chebi_structures": _chebi_structures,
           "rhea_reactions": _rhea_reactions, "rhea_participants": _rhea_participants,
           "zip_csv": _zip_csv, "mnxref": _mnxref, "mnxref_licences": _mnxref_licences}


# ================================================================== extractors
# ---------------------------------------------------------------------------- ChEBI
#: Taxonomy namespaces compound_origins names a species in (source.tsv id -> prefix).
#: Any other namespace there (BRENDA ligand, MetaboLights, Agricola) is not a taxon id.
_TAXON_NS = {"61": "ncbitaxon", "43": "ipni", "88": "worms", "40": "fungorum",
             "52": "mycobank", "42": "itis"}
#: Roots of ChEBI's three sub-ontologies.
_CHEMICAL_ENTITY, _ROLE = "24431", "50906"
#: compound_origins rows whose comment names a metabolic model: the compound is listed in
#: a model of the organism's metabolism, not reported as isolated from it.
_MODEL_SOURCES = ("Source: BioModels", "Source: yeast.sf.net")


def _join(*parts: Any) -> str | None:
    """Notes joined as written (``names`` would split a footnote at its semicolons)."""
    return "; ".join(str(p) for p in parts if p) or None


def _reference(prefix: str | None, accession: str | None) -> str | None:
    acc = v(accession)
    if not acc:
        return None
    prefix = (prefix or "").lower()
    if prefix == "pubmed":
        return f"pmid:{acc}" if acc.isdigit() else acc
    if prefix in ("doi", "metabolights", "citexplore", "agr"):
        return f"{prefix}:{acc}"
    return acc                                  # 'Article': a free-text citation


def _chebi(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "compounds"):
        return
    acc: dict[str, str] = {}
    name: dict[str, str | None] = {}
    for r in rows(conn, "SELECT id, chebi_accession, name FROM compounds"):
        cid = _chebi_id(r["chebi_accession"])
        if cid:
            acc[r["id"]], name[r["id"]] = cid, v(r["name"])
    status = ({r["id"]: r["name"] for r in rows(conn, "SELECT id, name FROM status")}
              if has(conn, "status") else {})
    source = ({r["id"]: (v(r["prefix"]), v(r["name"]))
               for r in rows(conn, "SELECT id, prefix, name FROM source")}
              if has(conn, "source") else {})

    if has(conn, "compound_origins"):
        for r in rows(conn, "SELECT * FROM compound_origins ORDER BY CAST(id AS INTEGER)"):
            compound = acc.get(r["compound_id"])
            if compound is None:
                continue
            ns = _TAXON_NS.get(v(r["species_source_id"]) or "")
            taxon = v(r["species_accession"])
            prefix, src_name = source.get(v(r["source_id"]) or "", (None, None))
            ref = _reference(prefix, r["source_accession"])
            comment = v(r["comments"]) or ""
            if not ns or not taxon:
                yield unresolved("organism_compound", "chebi", None,
                                 names(r["species_text"], r["strain_text"]),
                                 f"{name.get(r['compound_id'])} ({compound})",
                                 "species without a taxonomy accession", reference=ref,
                                 note=names(r["species_source_id"], r["species_accession"]))
                continue
            st = status.get(r["status_id"])
            # checked by ChEBI's curators (CHECKED, OK), or submitted and not yet checked
            evidence = "reported" if st == "SUBMITTED" else "known"
            via = "MetaboLights" if prefix == "metabolights" else None
            if comment.startswith(_MODEL_SOURCES):
                evidence, via = "listed", comment.split(" - ")[0].replace("Source:", "").strip()
            part = names(r["component_text"], r["component_accession"])
            # "via X" goes last: the consensus reads everything after it as lineage
            yield rel("organism_compound", "chebi", f"{ns}:{taxon}",
                      names(r["species_text"], r["strain_text"]), compound,
                      name.get(r["compound_id"]), evidence, reference=ref,
                      note=_join(src_name if not ref else None,
                                 f"via {via}" if via else None),
                      context=ctx(tissue=part, method=comment or None, qc=st))

    if has(conn, "relation", "relation_type"):
        code = {r["id"]: r["code"] for r in rows(conn, "SELECT id, code FROM relation_type")}
        is_a = next((k for k, c in code.items() if c == "is_a"), None)
        has_role = next((k for k, c in code.items() if c == "has_role"), None)
        parents: dict[str, set[str]] = {}
        for r in rows(conn, "SELECT init_id, final_id FROM relation WHERE relation_type_id = ?",
                      (is_a,)):
            parents.setdefault(r["init_id"], set()).add(r["final_id"])
        branch: dict[str, frozenset[str]] = {}

        def roots(node: str) -> frozenset[str]:
            """The sub-ontology roots a term descends from (iterative, cycle-safe)."""
            if node in branch:
                return branch[node]
            stack, seen, found = [node], {node}, set()
            while stack:
                n = stack.pop()
                if n in (_CHEMICAL_ENTITY, _ROLE):
                    found.add(n)
                    continue
                if n in branch:
                    found |= branch[n]
                    continue
                for p in parents.get(n, ()):
                    if p not in seen:
                        seen.add(p)
                        stack.append(p)
            branch[node] = frozenset(found)
            return branch[node]

        for r in rows(conn, "SELECT * FROM relation WHERE relation_type_id IN (?, ?) "
                            "ORDER BY CAST(id AS INTEGER)", (is_a, has_role)):
            init, final = r["init_id"], r["final_id"]
            if init not in acc or final not in acc:
                continue
            if _CHEMICAL_ENTITY not in roots(init):
                continue                        # role and particle hierarchies
            if r["relation_type_id"] == is_a:
                kind = "compound_class"
            elif _ROLE in roots(final):
                kind = "compound_role"
            else:
                continue
            yield rel(kind, "chebi", acc[init], name.get(init), acc[final], name.get(final),
                      "listed", context=ctx(qc=status.get(r["status_id"])))


# ----------------------------------------------------------------------------- Rhea
_DIRECTION = {"UN": "undefined direction", "LR": "left to right", "RL": "right to left",
              "BI": "bidirectional"}


def _rhea(conn: sqlite3.Connection) -> Iterator[Row | None]:
    equation: dict[str, str | None] = {}
    status: dict[str, str | None] = {}
    if has(conn, "reactions"):
        for r in rows(conn, "SELECT reaction_id, equation, status FROM reactions"):
            equation[r["reaction_id"]], status[r["reaction_id"]] = r["equation"], r["status"]
    if has(conn, "participants"):
        for r in rows(conn, "SELECT * FROM participants"):
            rid = r["reaction_id"]
            if status.get(rid) == "Obsolete":
                continue
            ctype = v(r["compound_type"]) or ""
            accession = v(r["compound_accession"])
            if ctype.startswith("Generic"):
                # a generic compound ("[protein]-dithiol") is Rhea's own entity; the
                # ChEBI ids are those of its reactive parts
                target = f"rhea.compound:{accession}" if accession else None
            else:
                target = v(r["chebi"])
            if not target:
                yield unresolved("reaction_participant", "rhea", f"rhea:{rid}",
                                 equation.get(rid), r["compound_name"],
                                 "participant without a ChEBI or Rhea accession")
                continue
            polymer = ctype == "Polymer"
            yield rel("reaction_participant", "rhea", f"rhea:{rid}", equation.get(rid),
                      target, r["compound_name"], "listed",
                      note=_join(f"{accession}, polymerization index "
                                 f"{r['polymerization_index']}" if polymer else None),
                      context=ctx(action=r["role"], measure="stoichiometric coefficient",
                                  value=r["coefficient"], residue=r["reactive_part_chebi"],
                                  condition=f"location {r['location']}"
                                  if v(r["location"]) else None,
                                  qc=status.get(rid) if status.get(rid) != "Approved"
                                  else None))
    for table, evidence, label in (("rhea2uniprot_sprot", "aggregated", "UniProtKB/Swiss-Prot"),
                                   ("rhea2uniprot_trembl", "predicted", "UniProtKB/TrEMBL")):
        if not has(conn, table):
            continue
        # Swiss-Prot's catalytic-activity annotations are reviewed, but this file does not
        # say which rest on experiments and which on similarity, so they are labelled as
        # integrated from UniProtKB, not as measured; TrEMBL's are automatic.
        for r in rows(conn, f'SELECT * FROM "{table}"'):
            rid = v(r["RHEA_ID"])
            yield rel("enzyme_reaction", "rhea", f"uniprot:{r['ID']}", None, f"rhea:{rid}",
                      equation.get(rid or ""), evidence, note="via UniProtKB",
                      context=ctx(action=_DIRECTION.get(v(r["DIRECTION"]) or "",
                                                        r["DIRECTION"]),
                                  dataset=label))


# ------------------------------------------------------------- FoodData Central
#: food_nutrient_source.id (the derivation table's source_id) -> evidence. 1 analytical
#: or derived from analytical; 10 analytical data from the literature, partial
#: documentation; 4, 8 aggregated combinations of source codes; 2 calculated or
#: imputed; 3, 6 label claims; 7, 9 manufacturer-supplied; 5 assumed zero (no row).
_FDC_EVIDENCE = {"1": "known", "10": "reported", "4": "aggregated", "8": "aggregated",
                 "2": "predicted", "3": "listed", "6": "listed", "7": "reported",
                 "9": "reported"}
_FDC_ASSUMED_ZERO = "5"
#: The foods whose values FoodData Central publishes as a food's profile. Foundation
#: Foods also ships its samples' and sub-samples' lab results, from which the
#: foundation_food values are computed; those stay in the tables.
_FDC_SETS = (("ff", "foundation_food", "Foundation Foods"),
             ("sr", "sr_legacy_food", "SR Legacy"))


def _number(value: Any) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _fooddata(conn: sqlite3.Connection) -> Iterator[Row | None]:
    derivation: dict[str, tuple[str | None, str | None, str | None]] = {}
    if has(conn, "sr_food_nutrient_derivation"):
        # the derivation codes are FoodData Central's, shared by its data types; the
        # Foundation download does not ship the table, the SR Legacy one does
        for r in rows(conn, "SELECT * FROM sr_food_nutrient_derivation"):
            derivation[r["id"]] = (v(r["code"]), v(r["description"]), v(r["source_id"]))
    for prefix, data_type, label in _FDC_SETS:
        food_t, fn_t, nut_t = f"{prefix}_food", f"{prefix}_food_nutrient", f"{prefix}_nutrient"
        if not has(conn, food_t, fn_t, nut_t):
            continue
        nutrient = {r["id"]: (v(r["name"]), v(r["unit_name"]), v(r["nutrient_nbr"]))
                    for r in rows(conn, f'SELECT * FROM "{nut_t}"')}
        foods = {r["fdc_id"]: v(r["description"])
                 for r in rows(conn, f'SELECT fdc_id, description FROM "{food_t}" '
                                     "WHERE data_type = ?", (data_type,))}
        for r in rows(conn, f'SELECT * FROM "{fn_t}" ORDER BY CAST(id AS INTEGER)'):
            fid = r["fdc_id"]
            if fid not in foods:
                continue
            amount = _number(r["amount"])
            if amount is None:
                continue
            code, desc, src = derivation.get(v(r["derivation_id"]) or "", (None, None, None))
            if src == _FDC_ASSUMED_ZERO:
                continue                        # assumed, never measured: not a result
            points = v(r["data_points"])
            if amount == 0 and code is None and (points is None or _number(points) == 0):
                # a zero with no derivation code and no data points was calculated,
                # imputed or assumed (SR: 0 points = not analysed), never measured; it
                # says nothing about whether the nutrient was tested
                continue
            evidence = _FDC_EVIDENCE.get(src or "", "aggregated")
            nname, unit, nbr = nutrient.get(r["nutrient_id"], (None, None, None))
            yield rel("food_nutrient", "fooddata_central", f"fdc:food.{fid}", foods[fid],
                      f"fdc:nutrient.{r['nutrient_id']}", names(nname, nbr), evidence,
                      object_type="nutrient", outcome="negative" if amount == 0 else "positive",
                      note=_join(*(f"{k} {r[k]}" for k in ("min", "max", "median")
                                   if v(r[k])), v(r["footnote"])),
                      context=ctx(value=r["amount"], unit=unit, measure="per 100 g",
                                  n=points if points and points != "0" else None,
                                  method=names(code, desc), dataset=label))


EXTRACTORS = {"chebi": _chebi, "rhea": _rhea, "fooddata_central": _fooddata}


# ==================================================================== datasets
_CHEBI = "https://ftp.ebi.ac.uk/pub/databases/chebi/flat_files/"
_RHEA = "https://ftp.expasy.org/databases/rhea/"
_MNX = "https://www.metanetx.org/ftp/4.5/"
_FDC = "https://fdc.nal.usda.gov/fdc-datasets/"
_FF = "FoodData_Central_foundation_food_csv_2026-04-30.zip"
_SR = "FoodData_Central_sr_legacy_food_csv_2018-04.zip"

_MNX_LICENCE = ("CC BY 4.0 for MNXref's own content; each row is also subject to the terms "
                "of the resource it came from, several of them non-commercial (KEGG, BiGG, "
                "HMDB, MetaCyc, enviPath CC BY-NC-SA 4.0, SABIO-RK)")

#: MNXref cross-reference namespaces the crosswalk maps, as MNXref writes them
#: (identifiers.org prefixes). ChEBI ids are left to ChEBI's own structures.
_MNX_XREF = ("hmdb", "kegg.compound", "kegg.drug", "lipidmaps", "metacyc.compound")

DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "chebi", "ChEBI (flat files)", (80,), "https://www.ebi.ac.uk/chebi/", "CC BY 4.0",
        tuple(FileSpec(_CHEBI + f"{n}.tsv.gz", f"{n}.tsv.gz", n, fmt="chebi_tsv")
              for n in ("compounds", "names", "relation", "relation_type", "chemical_data",
                        "database_accession", "compound_origins", "source", "status",
                        "secondary_ids"))
        + (FileSpec(_CHEBI + "structures.tsv.gz", "structures.tsv.gz", "structures",
                    fmt="chebi_structures", note="loaded without the molfile column"),
           FileSpec(_CHEBI + "LICENSE", "LICENSE", "", fmt="raw",
                    note="CC BY 4.0 legal code shipped with the files"),
           FileSpec(_CHEBI + "reference.tsv.gz", "reference.tsv.gz", "reference",
                    fmt="chebi_tsv", optional=True,
                    note="literature and database references per compound (137 MB)")),
        version="255 (2026-09-09)",
        notes="ChEBI 2.0 flat files (EMBL-EBI). organism_compound: the species (NCBI "
              "Taxonomy, IPNI, WoRMS, Index Fungorum, MycoBank, ITIS) a compound was found "
              "in, with the plant part or tissue (BTO) and the paper; 'known' when ChEBI "
              "curators checked the entry, 'reported' when it was submitted and not yet "
              "checked (status SUBMITTED), 'listed' when the origin is a metabolic model "
              "(BioModels, yeast.sf.net) rather than an isolation; MetaboLights studies are "
              "noted 'via MetaboLights'. compound_class (is_a) and compound_role (has_role) "
              "are the ontology's assertions for chemical entities, with each assertion's "
              "curation status in context.qc. Structural relations (conjugate acid/base, "
              "tautomer, enantiomer, functional parent, part) and database "
              "cross-references stay in the tables. The crosswalk maps ChEBI ids, primary "
              "and secondary, to the InChIKey of the entity's own structure: a pH 7.3 "
              "microspecies (Rhea's participants) keeps its own charged key. Licence: the "
              "LICENSE file and the OBO header say CC BY 4.0; one sentence of the README "
              "says CC BY-SA 4.0. Attribution: 'ChEBI data is from "
              "http://www.ebi.ac.uk/chebi - the version of ChEBI is 255.' The ftp host's "
              "robots.txt disallows crawling: only the named files are fetched.",
        relations=("organism_compound", "compound_class", "compound_role"),
        commercial_use="allowed",
        upstream=("MetaboLights", "BioModels", "yeast.sf.net"),
        crosswalk={"compound": (
            "SELECT 'chebi:' || substr(c.chebi_accession, 7), "
            "'inchikey:' || s.standard_inchi_key FROM structures s "
            "JOIN compounds c ON c.id = s.compound_id "
            "WHERE s.default_structure = 'true' AND length(s.standard_inchi_key) = 27 "
            "UNION SELECT 'chebi:' || x.secondary_id, 'inchikey:' || s.standard_inchi_key "
            "FROM secondary_ids x JOIN structures s ON s.compound_id = x.compound_id "
            "WHERE s.default_structure = 'true' AND length(s.standard_inchi_key) = 27")}),
    DatasetSpec(
        "rhea", "Rhea (TSV and RDF)", (81,), "https://www.rhea-db.org/", "CC BY 4.0",
        (FileSpec(_RHEA + "rdf/rhea.rdf.gz", "rhea.rdf.gz", "reactions", fmt="rhea_reactions",
                  note="RDF/XML; one row per reaction"),
         FileSpec(_RHEA + "rdf/rhea.rdf.gz", "rhea.rdf.gz", "participants",
                  fmt="rhea_participants",
                  note="RDF/XML; one row per reaction, side or role, and participant"),
         FileSpec(_RHEA + "tsv/rhea-directions.tsv", "rhea-directions.tsv", "directions"),
         FileSpec(_RHEA + "tsv/rhea2uniprot_sprot.tsv", "rhea2uniprot_sprot.tsv",
                  "rhea2uniprot_sprot"),
         FileSpec(_RHEA + "tsv/rhea2xrefs.tsv", "rhea2xrefs.tsv", "rhea2xrefs",
                  note="EC, GO, KEGG, MetaCyc, EcoCyc, Reactome and M-CSA cross-references"),
         FileSpec(_RHEA + "tsv/chebi_pH7_3_mapping.tsv", "chebi_pH7_3_mapping.tsv",
                  "chebi_ph7_3_mapping",
                  note="ChEBI id -> its major microspecies at pH 7.3, the form Rhea uses"),
         FileSpec(_RHEA + "tsv/rhea-relationships.tsv", "rhea-relationships.tsv",
                  "relationships"),
         FileSpec(_RHEA + "tsv/chebiId_name.tsv", "chebiId_name.tsv", "chebi_name",
                  columns=("chebi_id", "name")),
         FileSpec(_RHEA + "LICENSE.txt", "LICENSE.txt", "", fmt="raw",
                  note="the copyright statement the licence asks to keep with each copy"),
         FileSpec(_RHEA + "rhea-release.properties", "rhea-release.properties", "",
                  fmt="raw"),
         FileSpec(_RHEA + "tsv/rhea2uniprot_trembl.tsv.gz", "rhea2uniprot_trembl.tsv.gz",
                  "rhea2uniprot_trembl", optional=True,
                  note="unreviewed TrEMBL annotations (153 MB); 'predicted' rows")),
        version="142 (2026-09-02)",
        notes="Expert-curated biochemical reactions (SIB). reaction_participant: every "
              "reaction's ChEBI participants (the major microspecies at pH 7.3; "
              "chebi_ph7_3_mapping maps a neutral ChEBI id to it). context.action is "
              "the participant's place: 'left side'/'right side' for the undirected "
              "reaction, 'substrate'/'product' for its left-to-right and right-to-left "
              "forms, 'substrate or product' for the bidirectional form; context.value is "
              "the stoichiometric coefficient (N, 2n for polymers). Generic compounds "
              "([protein]-dithiol) keep Rhea's GENERIC accession as rhea.compound:GENERIC:<n> "
              "(rhea:<n> names reactions), with their reactive "
              "parts' ChEBI ids in context.residue; polymers use the underlying ChEBI "
              "polymer. Obsolete reactions are left out; preliminary ones say so in "
              "context.qc. enzyme_reaction: the UniProtKB/Swiss-Prot entries annotated "
              "with a reaction, on the reaction id UniProtKB gives (an undirected master "
              "or a directional one) and its direction in context.action; the file does "
              "not say which annotations rest on experiments and which on similarity, so "
              "they are 'aggregated' (via UniProtKB), not 'known'. UniProtKB's own "
              "catalytic-activity records cite the same Rhea ids and are not independent. "
              "Robots: ftp.expasy.org disallows crawling, so only these named files are "
              "fetched. Disclaimer: 'not intended to be used for medical purposes'.",
        relations=("reaction_participant", "enzyme_reaction"),
        commercial_use="allowed",
        upstream=("ChEBI", "UniProtKB")),
    DatasetSpec(
        "metanetx", "MetaNetX/MNXref", (82,), "https://www.metanetx.org/", _MNX_LICENCE,
        (FileSpec(_MNX + "mnxref_tsv.tar.gz", "mnxref_tsv.tar.gz", "chem_prop", fmt="mnxref",
                  sheet="chem_prop.tsv", license=_MNX_LICENCE,
                  note="chemicals: MNX id, name, reference, formula, charge, mass, InChIKey "
                       "(the InChI and SMILES columns are not loaded)"),
         FileSpec(_MNX + "mnxref_tsv.tar.gz", "mnxref_tsv.tar.gz", "chem_xref", fmt="mnxref",
                  sheet="chem_xref.tsv", license=_MNX_LICENCE,
                  note="external id -> MNX id; the description (names) is not loaded, "
                       "only whether it marks a secondary/obsolete/fantasy identifier"),
         FileSpec(_MNX + "mnxref_tsv.tar.gz", "mnxref_tsv.tar.gz", "chem_isom", fmt="mnxref",
                  sheet="chem_isom.tsv", license=_MNX_LICENCE),
         FileSpec(_MNX + "mnxref_tsv.tar.gz", "mnxref_tsv.tar.gz", "chem_depr", fmt="mnxref",
                  sheet="chem_depr.tsv", license=_MNX_LICENCE),
         FileSpec(_MNX + "mnxref_tsv.tar.gz", "mnxref_tsv.tar.gz", "licences",
                  fmt="mnxref_licences", sheet="chem_prop.tsv", license=_MNX_LICENCE,
                  note="the resources chem_prop.tsv draws on, each with the licence its "
                       "header states")),
        version="4.5 (2025-08-13)",
        notes="MetaNetX/MNXref (SIB): the reconciliation of metabolites across ChEBI, "
              "KEGG, HMDB, MetaCyc, LIPID MAPS, SwissLipids, Reactome, SEED, BiGG, VMH, "
              "enviPath and SABIO-RK. It is a mapping layer, not evidence, so it writes no "
              "relation rows. Its chemicals are protonation-normalised to the major "
              "microspecies at pH 7.3 (kaempferol CHEBI:28499 -> MNXM1672, an anion "
              "whose InChIKey ends in -M). The crosswalk maps hmdb, kegg.compound, "
              "kegg.drug, lipidmaps and metacyc.compound ids (primary identifiers only) "
              "to the MNX chemical's InChIKey with its protonation character set to N: "
              "the key of the same structure at neutral proton balance, which is the form "
              "those databases record; conjugate acids and bases therefore meet. The "
              "'licences' table holds each upstream's licence as the file header states "
              "it: rows sourced from KEGG, BiGG, HMDB, MetaCyc, enviPath or SABIO-RK are "
              "not for commercial use, while MNXref's own content is CC BY 4.0, so commercial "
              "use is 'unknown' for the dataset as a whole and depends on each row's "
              "source. Upstream versions lag the sources' current "
              "releases (ChEBI 244, Rhea 139, SABIO-RK 2021). Robots: Crawl-delay 10; the "
              "pinned 4.5 bundle (208 MB) is one request.",
        commercial_use="unknown",
        upstream=("ChEBI", "Rhea", "KEGG", "HMDB", "MetaCyc", "LIPID MAPS", "SwissLipids",
                  "Reactome", "SEED", "BiGG", "VMH", "enviPath", "SABIO-RK"),
        crosswalk={"compound": (
            "SELECT x.source, 'inchikey:' || substr(p.InChIKey, 1, 26) || 'N' "
            "FROM chem_xref x JOIN chem_prop p ON p.ID = x.ID "
            "WHERE x.secondary_flag IS NULL AND length(p.InChIKey) = 27 AND ("
            + " OR ".join(f"x.source LIKE '{ns}:%'" for ns in _MNX_XREF) + ")")}),
    DatasetSpec(
        "fooddata_central", "USDA FoodData Central", (84,), "https://fdc.nal.usda.gov/",
        "CC0 1.0",
        tuple(FileSpec(_FDC + _FF, _FF, f"ff_{t}", fmt="zip_csv", sheet=f"{t}.csv")
              for t in ("food", "food_nutrient", "nutrient", "food_category",
                        "foundation_food"))
        + tuple(FileSpec(_FDC + _SR, _SR, f"sr_{t}", fmt="zip_csv", sheet=f"{t}.csv")
                for t in ("food", "food_nutrient", "nutrient", "food_category",
                          "food_nutrient_derivation", "food_nutrient_source")),
        version="Foundation Foods April 2026 (file re-issued 2026-08-19); SR Legacy April "
                "2018 (final)",
        notes="USDA ARS food composition. food_nutrient: a food's amount of a nutrient per "
              "100 g (context.value, context.unit), for the Foundation Foods and SR Legacy "
              "food profiles; the Foundation samples' and sub-samples' lab results stay "
              "in ff_food_nutrient. Evidence follows the row's derivation code: analytical "
              "'known'; analytical from the literature or manufacturer-supplied "
              "'reported'; calculated or imputed 'predicted'; aggregated combinations and "
              "rows without a derivation code 'aggregated'; label claims 'listed'. An "
              "'assumed zero' (code Z: never measured) gives no row, nor does a zero "
              "with no derivation code and no data points (SR's sign of a value that was "
              "not analysed); any other amount of 0 is a negative outcome. Nutrients keep FoodData Central's ids "
              "(fdc:nutrient.<id>): FDC maps none to ChEBI or InChIKey. FNDDS (derived "
              "from SR and Foundation values) and Branded (label data) are not included. "
              "The API needs an api.data.gov key and is not wrapped.",
        relations=("food_nutrient",),
        commercial_use="allowed"),
)

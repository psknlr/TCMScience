"""TCM safety and provenance: toxic herbs and targets, microbial products, EU herbal assessments.

* **TCMToxDB** (catalogue 71): the four entity tables of its Download page. The formula
  table lists each formula's herbs with their doses (``formula_herb``); the herb table
  grades some herbs Extremely toxic / Toxic / Slightly toxic, and the target table names
  the organ toxicities each gene is associated with (``subject_toxicity``). Its
  herb-ingredient and herb/ingredient-target edges and its literature toxicity records are
  served only by an undocumented API that answered 502 throughout the review, so they are
  not here.
* **MIBiG 4.0** (catalogue 73): every biosynthetic gene cluster's JSON record. Active
  records give the organism that produces each compound (``organism_compound``) and each
  compound's tested bioactivities, observed or not (``compound_assay``).
* **gutMGene v2.0** (catalogue 74): literature-curated gut microbe -> metabolite
  (``organism_compound``) and microbe/metabolite -> host gene (``regulation``)
  associations, each classed causal (a controlled experiment) or correlational.
* **EMA herbal medicines** (catalogue 75): the HMPC's index of herbal substances and the
  EMA documents index, whose herbal monographs, assessment reports, opinions, list entries,
  public statements and public summaries are linked to the substance their title names
  (``subject_monograph``). No document is downloaded or parsed; a row points to it.

``subject_toxicity`` is added here: no registered kind relates a herb or a gene to a
toxicity class (``gene_phenotype`` is a knockout's or a variant's phenotype, and
``drug_adverse_event`` a pharmacovigilance event of a drug).
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import tarfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Iterator

from ..rowkit import Row, col, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec
from ..store import StoreError, open_text

__all__ = ["DATASETS", "EXTRACTORS", "KINDS", "READERS"]

KINDS = {
    # a herb's toxicity grade or a gene's organ toxicity, as a source lists it
    "subject_toxicity": ("subject", "toxicity"),
}


def _clean(values: Iterable[Any]) -> list[str | None]:
    """Values as the file wrote them; an empty string is a missing value."""
    out: list[str | None] = []
    for value in values:
        if value is None:
            out.append(None)
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        text = str(value).strip()
        out.append(text or None)
    return out


# ============================================================================ TCMToxDB
_TOX = "https://www.sdu-idea.cn/TCMToxDB/"
#: one prescription item: herb pinyin, then an optional dose and unit ("Dai Huang 100g")
_DOSE = re.compile(r"^(?P<name>.*?)\s*(?P<dose>\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z]+)?$")
_PAREN = re.compile(r"[（(]([^）)]*)[）)]")
_GRADES = {"extremely toxic": "Extremely toxic", "toxic": "Toxic",
           "slightly toxic": "Slightly toxic"}


def _slug(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", text.casefold()).strip("_")


def _prescription(text: str | None) -> Iterator[tuple[str, str | None, str | None, str | None]]:
    """(herb, dose, unit, remark) per item of 'Dai Huang 100g,Chao Qian Niu Zi 200g,...'.

    Items are split on commas outside brackets; a bracketed remark (a sub-ingredient
    with its own dose) is kept as the remark, not parsed as the herb's dose.
    """
    items, depth, buf = [], 0, ""
    for ch in text or "":
        if ch in "(（":
            depth += 1
        elif ch in ")）":
            depth = max(0, depth - 1)
        if ch in ",，" and depth == 0:
            items.append(buf)
            buf = ""
        else:
            buf += ch
    items.append(buf)
    for item in items:
        remark = "; ".join(m.strip() for m in _PAREN.findall(item) if m.strip()) or None
        bare = _PAREN.sub(" ", item).strip()
        if not bare:
            continue
        m = _DOSE.match(bare)
        if m and m.group("name").strip():
            yield m.group("name").strip(), m.group("dose"), m.group("unit"), remark
        else:
            yield bare, None, None, remark


def _tcmtoxdb(conn: sqlite3.Connection) -> Iterator[Row | None]:
    herbs: dict[str, tuple[str, str | None]] = {}
    if has(conn, "herb"):
        for r in rows(conn, "SELECT * FROM herb"):
            pinyin = v(r["Herb_pinyin_name"])
            if not pinyin:
                continue
            hid = f"tcmtoxdb:herb.{pinyin}"
            hname = names(r["Herb_cn_name"], pinyin, r["Herb_latin_name"], r["Herb_en_name"])
            herbs[pinyin.casefold()] = (hid, hname)
            grade = v(r["Toxicity"])
            if grade:
                label = _GRADES.get(grade.casefold(), grade)
                yield rel("subject_toxicity", "tcmtoxdb", hid, hname,
                          f"tcmtoxdb:grade.{_slug(label)}", label, "listed",
                          subject_type="herb")
    if has(conn, "formula"):
        for r in rows(conn, "SELECT * FROM formula"):
            form = v(r["Form_name"])
            if not form:
                continue
            fid, fname = f"tcmtoxdb:formula.{form}", names(form, r["Form_name_en"])
            for herb, dose, unit, remark in _prescription(r["Prescription"]):
                hid, hname = herbs.get(herb.casefold(), (f"tcmtoxdb:herb.{herb}", herb))
                yield rel("formula_herb", "tcmtoxdb", fid, fname, hid, hname, "listed",
                          note=remark, context=ctx(dose=dose, dose_unit=unit))
    if has(conn, "target"):
        for r in rows(conn, "SELECT * FROM target"):
            gene = v(r["Gene_name"])
            # the file carries one stray header row of NCBI's gene_info ("Symbol, ...")
            if not gene or gene == "Symbol" or " " in gene:
                continue
            for organ in re.split(r"\s*;\s*", v(r["Toxicity"]) or ""):
                if organ:
                    yield rel("subject_toxicity", "tcmtoxdb", f"symbol:{gene}", gene,
                              f"tcmtoxdb:toxicity.{_slug(organ)}", organ, "aggregated",
                              subject_type="target", note="via GeneCards")


# =============================================================================== MIBiG
def _mibig_records(path: Path) -> Iterator[dict]:
    """The BGC records of a MIBiG JSON archive (tar of one JSON file per cluster).

    macOS resource-fork members (``._BGC....json``, present in some repackaged archives)
    are not records and are skipped.
    """
    with tarfile.open(path, "r:*") as tf:
        for member in tf:
            base = member.name.rsplit("/", 1)[-1]
            if not member.isfile() or not base.endswith(".json") or base.startswith("._"):
                continue
            fh = tf.extractfile(member)
            if fh is None:                                  # pragma: no cover
                continue
            data = json.loads(fh.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("accession"):
                yield data


def _methods(evidence: Any) -> list[str]:
    out = []
    for item in evidence or []:
        method = item.get("method") if isinstance(item, dict) else item
        if method and str(method) not in out:
            out.append(str(method))
    return out


def _activity(item: Any) -> str | None:
    """A bioactivity's name: a string in some records, ``{"activity": ...}`` in others."""
    name = item.get("name") if isinstance(item, dict) else item
    if isinstance(name, dict):
        name = name.get("activity")
    return v(name)


def _read_mibig_entry(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    yield ["accession", "version", "status", "quality", "completeness", "organism",
           "ncbi_taxid", "classes", "loci", "loci_evidence", "legacy_references",
           "retirement_reasons", "see_also", "comment"]
    for e in _mibig_records(path):
        tax = e.get("taxonomy") or {}
        classes = [c.get("class") for c in (e.get("biosynthesis") or {}).get("classes", [])
                   if isinstance(c, dict) and c.get("class")]
        loci = e.get("loci") or []
        yield _clean([e.get("accession"), e.get("version"), e.get("status"), e.get("quality"),
                      e.get("completeness"), tax.get("name"), tax.get("ncbiTaxId"),
                      "; ".join(classes),
                      "; ".join(str(lo.get("accession")) for lo in loci if lo.get("accession")),
                      "; ".join(dict.fromkeys(m for lo in loci
                                              for m in _methods(lo.get("evidence")))),
                      "; ".join(str(x) for x in e.get("legacy_references") or []),
                      e.get("retirement_reasons"), e.get("see_also"), e.get("comment")])


def _read_mibig_compound(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    yield ["accession", "compound_no", "name", "structure", "formula", "mass",
           "database_ids", "evidence", "evidence_references", "classes", "moieties",
           "synonyms"]
    for e in _mibig_records(path):
        for i, c in enumerate(e.get("compounds") or [], 1):
            ev = [x for x in c.get("evidence") or [] if isinstance(x, dict)]
            yield _clean([e.get("accession"), i, c.get("name"), c.get("structure"),
                          c.get("formula"), c.get("mass"),
                          "; ".join(c.get("databaseIds") or []), "; ".join(_methods(ev)),
                          "; ".join(dict.fromkeys(str(r) for x in ev
                                                  for r in x.get("references") or [])),
                          c.get("classes"), c.get("moieties"), c.get("synonyms")])


def _read_mibig_bioactivity(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    yield ["accession", "compound_no", "compound", "activity", "observed", "references",
           "assays"]
    for e in _mibig_records(path):
        for i, c in enumerate(e.get("compounds") or [], 1):
            for b in c.get("bioactivities") or []:
                b = b if isinstance(b, dict) else {"name": b}
                observed = b.get("observed")
                yield _clean([e.get("accession"), i, c.get("name"), _activity(b),
                              None if observed is None else str(bool(observed)).lower(),
                              "; ".join(str(r) for r in b.get("references") or []),
                              b.get("assays")])


#: which of a compound's database ids names it, in order of preference
_MIBIG_IDS = ("pubchem", "chebi", "chembl", "npatlas", "lotus", "chemspider", "cyanometdb")


def _mibig_compound_id(acc: str, name: str | None, ids: str | None) -> str:
    have: dict[str, str] = {}
    for item in (ids or "").split(";"):
        prefix, _, local = item.strip().partition(":")
        if local and prefix.lower() not in have:
            have[prefix.lower()] = local.strip()
    for prefix in _MIBIG_IDS:
        if prefix in have:
            local = have[prefix]
            if prefix == "chebi":
                local = local.upper().removeprefix("CHEBI:")
            return f"{prefix}:{local}"
    # no database id: the compound is the one this cluster's record names
    return f"mibig:{acc}:{name}"


def _citation(ref: str | None) -> str | None:
    ref = v(ref)
    if not ref:
        return None
    if ref.lower().startswith("pubmed:"):
        return "pmid:" + ref.split(":", 1)[1].strip()
    return ref


#: locus evidence that is a computation, not an observation of the cluster's product
_PREDICTED_LOCUS = ("homology-based prediction", "synthetic-bioinformatic natural product")


def _mibig(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "entry", "compound"):
        return
    entries = {r["accession"]: dict(r) for r in rows(conn, "SELECT * FROM entry")}
    active = {a for a, e in entries.items() if (v(e["status"]) or "").lower() == "active"}
    compound_ids: dict[tuple[str, str], str] = {}
    for r in rows(conn, "SELECT * FROM compound"):
        acc = r["accession"]
        cid = _mibig_compound_id(acc, v(r["name"]), r["database_ids"])
        compound_ids[(acc, r["compound_no"])] = cid
        if acc not in active:
            continue                    # retired and pending records give no relation
        e = entries[acc]
        methods = [m for m in (v(e["loci_evidence"]) or "").split("; ") if m]
        computed = methods and all(m.lower().startswith(_PREDICTED_LOCUS) for m in methods)
        refs = [x for x in (v(e["legacy_references"]) or "").split("; ") if x]
        taxid = v(e["ncbi_taxid"])
        yield rel("organism_compound", "mibig", f"ncbitaxon:{taxid}" if taxid else None,
                  e["organism"], cid, names(r["name"]),
                  "predicted" if computed else "known",
                  reference=_citation(refs[0]) if refs else None,
                  note=f"{acc}.{v(e['version']) or ''}".rstrip("."),
                  context=ctx(method="; ".join(methods) or None, qc=v(e["quality"])))
    if not has(conn, "bioactivity"):
        return
    for r in rows(conn, "SELECT * FROM bioactivity"):
        acc = r["accession"]
        activity = v(r["activity"])
        observed = v(r["observed"])
        if acc not in active or not activity or observed not in ("true", "false"):
            continue
        refs = [x for x in (v(r["references"]) or "").split("; ") if x]
        concentrations = []
        for assay in json.loads(r["assays"]) if v(r["assays"]) else []:
            if isinstance(assay, dict) and v(assay.get("concentration")):
                concentrations.append(str(assay["concentration"]).strip())
        yield rel("compound_assay", "mibig", compound_ids.get((acc, r["compound_no"])),
                  r["compound"], f"mibig:activity.{_slug(activity)}", activity, "known",
                  outcome="positive" if observed == "true" else "negative",
                  reference=_citation(refs[0]) if refs else None, note=acc,
                  context=ctx(concentration="; ".join(concentrations) or None))


# ============================================================================ gutMGene
_GUT = "http://bio-computing.hrbmu.edu.cn/gutMGene2.0_api/dow/downloadFolder?filepath=allfile/"
_HOST = {"human": "9606", "mouse": "10090"}
_MODE = {"causally": "known", "correlatively": "associated"}
#: The source's alteration word describes the host gene's change, mostly in expression,
#: so a causal row says the gene went up or down, not that it was mechanistically
#: activated or inhibited. A correlative row states only the sign of a covariation and
#: gets no effect. The word itself is kept in context.action either way.
_ALTERATION = {"activation": "increase", "inhibition": "decrease"}


def _read_gutmgene(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """A gutMGene CSV, decoded from the encoding it was written in.

    Two of the three files are UTF-8; 'Gut Microbe-Host Gene.csv' is GB18030 (its β and
    en dashes are GBK bytes). The download endpoint answers 200 with the body 'success'
    for a file it does not have, so a file without the expected header is refused.
    """
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("gb18030", errors="replace")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if not header or [h.strip() for h in header[:2]] != ["Index", "PMID"]:
        raise StoreError(f"{path.name}: not a gutMGene table (first bytes "
                         f"{data[:40]!r}); the server answers 'success' for missing files")
    yield _clean(header)
    for row in reader:
        if row and any(c.strip() for c in row):
            yield _clean(row)


def _gut_metabolite(r: sqlite3.Row) -> str | None:
    cid = v(col(r, "Metabolite_PubChem_CID"))
    if cid and cid.isdigit():
        return f"pubchem:{cid}"
    chebi = v(col(r, "Metabolite_ChEBI"))
    if chebi and re.fullmatch(r"(?i)chebi:\d+", chebi):
        return "chebi:" + chebi.split(":", 1)[1]
    hmdb = v(col(r, "Metabolite_HMDB"))
    if hmdb and hmdb.upper().startswith("HMDB"):
        return f"hmdb:{hmdb.upper()}"
    kegg = v(col(r, "Metabolite_KEGG"))
    if kegg and re.fullmatch(r"C\d{5}", kegg):
        return f"kegg:{kegg}"
    return None


def _gut_context(r: sqlite3.Row, *, action: str | None = None) -> str | None:
    doids = re.findall(r"DOID:\d+", v(col(r, "DOID")) or "")
    return ctx(species=_HOST.get((v(col(r, "human_mouse")) or "").lower()),
               sample=v(col(r, "Sample")), method=v(col(r, "Experimental_method")),
               assay=v(col(r, "Measurement_technique")),
               condition=names(col(r, "Condition"), *doids),
               flags=v(col(r, "high_low_throughput")), action=action)


def _gutmgene(conn: sqlite3.Connection) -> Iterator[Row | None]:
    # One row per association and context. The file repeats an association per substrate
    # (and per strain of one taxon); those copies are one row whose note lists them.
    merged: dict[tuple, tuple[Row, list[str], list[str]]] = {}

    def add(row: Row | None, strain: str | None, substrate: str | None) -> None:
        if row is None:                                   # pragma: no cover - ids checked
            return
        key = (row["kind"], row["subject_id"], row["object_id"], row["evidence"],
               row["reference"], row["effect"], row["outcome"], row["context"])
        _, strains, substrates = merged.setdefault(key, (row, [], []))
        for value, seen in ((strain, strains), (substrate, substrates)):
            if value and value not in seen:
                seen.append(value)

    def microbe(r: sqlite3.Row) -> tuple[str | None, str | None]:
        taxid = v(col(r, "Gut_Microbiota_NCBI_ID"))
        return (f"ncbitaxon:{taxid}" if taxid and taxid.isdigit() else None,
                v(col(r, "Gut_Microbiota")))

    def pmid(r: sqlite3.Row) -> str | None:
        p = v(col(r, "PMID"))
        return f"pmid:{p}" if p and p.isdigit() else p

    def substrate(r: sqlite3.Row) -> str | None:
        name, cid = v(col(r, "Substrate")), v(col(r, "Substrate_PubChem_CID"))
        if not name and not cid:
            return None
        return f"{name or ''} (pubchem:{cid})".strip() if cid else name

    def evidence(r: sqlite3.Row) -> str:
        # an associative mode the source has not documented is only "a paper states it"
        return _MODE.get((v(col(r, "Associative_mode")) or "").lower(), "reported")

    if has(conn, "microbe_metabolite"):
        for r in rows(conn, "SELECT * FROM microbe_metabolite"):
            sid, sname = microbe(r)
            oid, oname = _gut_metabolite(r), v(col(r, "Metabolite"))
            if not sid or not oid:
                yield unresolved("organism_compound", "gutmgene", sid, sname, oname,
                                 "no NCBI Taxonomy id for the microbe" if not sid else
                                 "no PubChem, ChEBI, HMDB or KEGG id for the metabolite",
                                 reference=pmid(r))
                continue
            add(rel("organism_compound", "gutmgene", sid, sname, oid, oname, evidence(r),
                    reference=pmid(r), context=_gut_context(r)),
                v(col(r, "Strain")), substrate(r))
    for table, kind_of in (("metabolite_gene", "compound"), ("microbe_gene", "organism")):
        if not has(conn, table):
            continue
        for r in rows(conn, f'SELECT * FROM "{table}"'):
            if kind_of == "compound":
                sid, sname = _gut_metabolite(r), v(col(r, "Metabolite"))
            else:
                sid, sname = microbe(r)
            gene_id, gene = v(col(r, "Gene_ID")), v(col(r, "Gene"))
            if not sid or not (gene_id and gene_id.isdigit()):
                reason = ("no NCBI Gene id for the host gene" if sid else
                          "no NCBI Taxonomy id for the microbe" if kind_of == "organism" else
                          "no PubChem, ChEBI, HMDB or KEGG id for the metabolite")
                yield unresolved("regulation", "gutmgene", sid, sname, gene, reason,
                                 reference=pmid(r))
                continue
            word, ev = v(col(r, "Alteration")), evidence(r)
            effect = _ALTERATION.get((word or "").lower()) if ev == "known" else None
            add(rel("regulation", "gutmgene", sid, sname, f"ncbigene:{gene_id}", gene,
                    ev, subject_type=kind_of, object_type="gene",
                    effect=effect, reference=pmid(r),
                    context=_gut_context(r, action=word)),
                v(col(r, "Strain")) if kind_of == "organism" else None,
                substrate(r) if kind_of == "compound" else None)
    for row, strains, substrates in merged.values():
        parts = ([f"strain: {', '.join(strains)}"] if strains else []) + \
                ([f"substrate: {', '.join(substrates)}"] if substrates else [])
        yield {**row, "note": "; ".join(parts) or None}


# ===================================================================== EMA herbal index
_EMA = "https://www.ema.europa.eu/en/documents/report/"
#: the document types that are an assessment of a herbal substance; the index's other
#: herbal types (reference lists, calls for data, overviews of comments) stay in the table
_EMA_ASSESSMENT = ("herbal-monograph", "herbal-report", "herbal-list-entry", "herbal-opinion",
                   "herbal-summary", "public-statement")
#: Latin plant parts of the pharmacopoeial names, with the words document titles use
_PARTS = {
    "folium": ("folium", "folia", "leaf", "leaves"), "radix": ("radix", "root", "roots"),
    "herba": ("herba", "herb"), "cortex": ("cortex", "bark"),
    "flos": ("flos", "flores", "flower", "flowers"), "fructus": ("fructus", "fruit", "fruits"),
    "semen": ("semen", "seed", "seeds"), "rhizoma": ("rhizoma", "rhizome"),
    "aetheroleum": ("aetheroleum", "essential oil"), "oleum": ("oleum", "oil"),
    "bulbus": ("bulbus", "bulb"), "tuber": ("tuber",),
    "pericarpium": ("pericarpium", "pericarp", "peel"), "lignum": ("lignum", "wood"),
    "gemmae": ("gemmae", "gemma", "buds", "bud"), "stigma": ("stigma",),
    "resina": ("resina", "resin"), "thallus": ("thallus",),
    "strobulus": ("strobulus", "strobile", "strobiles"), "gummi": ("gummi", "gum"),
}
_LINKS = ("et", "aut", "cum", "and", "ex", "sine", "vel")
_BINOMIAL = re.compile(r"\b([A-Z][a-z]+)\s+(?:x\s+|×\s*)?([a-z][a-z-]+)")


class _JsonStream:
    """A minimal incremental reader of one top-level JSON object over a text handle.

    Values are decoded one at a time with ``json.JSONDecoder.raw_decode`` from a buffer
    refilled in 1 MB chunks, so the elements of a large array can be filtered as they are
    read instead of materialising the whole document as Python objects.
    """

    _WS = " \t\r\n"

    def __init__(self, fh: Any, chunk: int = 1 << 20) -> None:
        self.fh, self.chunk, self.buf, self.i = fh, chunk, "", 0
        self.decoder = json.JSONDecoder()

    def _fill(self) -> bool:
        more = self.fh.read(self.chunk)
        if not more:
            return False
        self.buf, self.i = self.buf[self.i:] + more, 0
        return True

    def peek(self) -> str:
        """The next non-blank character (not consumed), or "" at the end of the input."""
        while True:
            while self.i < len(self.buf) and self.buf[self.i] in self._WS:
                self.i += 1
            if self.i < len(self.buf):
                return self.buf[self.i]
            if not self._fill():
                return ""

    def take(self, char: str) -> None:
        if self.peek() != char:
            raise ValueError(f"expected {char!r} at offset {self.i}")
        self.i += 1

    def value(self) -> Any:
        if self.peek() not in '{["':
            # a bare number decodes from any prefix ("1." reads as 1), so read until
            # the delimiter that ends it is in the buffer
            while not any(c in self.buf[self.i:] for c in self._WS + ",]}") and self._fill():
                pass
        while True:
            try:
                obj, end = self.decoder.raw_decode(self.buf, self.i)
            except json.JSONDecodeError:
                if not self._fill():
                    raise
                continue
            # a value ending exactly at the buffer's end may be cut short (a number)
            if end == len(self.buf) and self._fill():
                continue
            self.i = end
            return obj

    def members(self, array_key: str) -> Iterator[tuple[str, Any]]:
        """(key, value) of the top-level object; ``array_key``'s array is yielded one
        element at a time as (array_key, element)."""
        self.take("{")
        while self.peek() != "}":
            key = self.value()
            self.take(":")
            if key == array_key and self.peek() == "[":
                self.take("[")
                while self.peek() != "]":
                    yield key, self.value()
                    if self.peek() == ",":
                        self.take(",")
                self.take("]")
            else:
                yield key, self.value()
            if self.peek() == ",":
                self.take(",")
        self.take("}")


def _read_ema(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """The records of an EMA website data file (``{"meta": ..., "data": [...]}``).

    Each row also carries the file's ``meta.timestamp``: EMA regenerates these files
    twice a day, and the timestamp says which generation was loaded. For the documents
    file (table ``document``) only the herbal document types and public statements are
    kept; the file indexes every document on EMA's website (37 MB, 70k records in
    2026-10), so it is streamed and filtered record by record: memory is bounded by the
    kept records (a few thousand) plus a 1 MB read buffer, not by the file.
    """
    def keep(r: Any) -> bool:
        if not isinstance(r, dict):
            return False
        return spec.table != "document" or str(r.get("type") or "").startswith(
            "herbal-") or r.get("type") == "public-statement"

    records: list[dict[str, Any]] = []
    meta: Any = None
    seen_data = False
    with open_text(path) as fh:
        try:
            for key, value in _JsonStream(fh).members("data"):
                if key == "data":
                    seen_data = True
                    if keep(value):
                        records.append(value)
                elif key == "meta":
                    meta = value
        except ValueError as exc:              # JSONDecodeError is a ValueError
            raise StoreError(f"{path.name}: not an EMA data file ({exc})") from exc
    if not seen_data:
        raise StoreError(f"{path.name}: not an EMA data file (no 'data' list)")
    stamp = meta.get("timestamp") if isinstance(meta, dict) else None
    keys: list[str] = []
    for r in records:
        keys += [k for k in r if k not in keys]
    yield keys + ["meta_timestamp"]
    for r in records:
        yield _clean([r.get(k) for k in keys] + [stamp])


def _norm(text: str | None) -> str:
    """Lower-case words padded with spaces, the hybrid sign dropped, for word matching."""
    t = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    t = re.sub(r"\bx\b", " ", t)
    return " " + " ".join(t.split()) + " "


class _Substance:
    def __init__(self, latin: str) -> None:
        self.latin = latin
        self.commons: list[str] = []
        self.botanical: list[str] = []
        self.binomials: list[str] = []
        self.combination = False
        words = _norm(re.sub(r"\(.*?\)", " ", latin)).split()
        self.parts = tuple(p for p in _PARTS if p in words)
        self.extra = tuple(w for w in words[1:] if w not in _PARTS and w not in _LINKS)
        self.key = _norm(re.sub(r"\(.*?\)", " ", latin))

    def add(self, r: sqlite3.Row) -> None:
        common = v(r["english_common_name"])
        if common and common not in self.commons:
            self.commons.append(common)
        botanical = v(r["botanical_name"]) or ""
        for genus, epithet in _BINOMIAL.findall(botanical):
            if epithet in ("various", "species", "and", "or", "et"):
                continue
            name = f"{genus} {epithet}"
            if name not in self.botanical:
                self.botanical.append(name)
                self.binomials.append(_norm(name))
        if not self.binomials:
            genus = re.match(r"\s*([A-Z][a-z]+)\b", botanical)
            if genus and genus.group(1) not in ("Combination", "Species"):
                self.binomials.append(_norm(genus.group(1)))
        self.combination = self.combination or (v(r["combination"]) or "").lower() == "yes"

    @property
    def id(self) -> str:
        return f"ema:herbal.{self.latin}"

    @property
    def name(self) -> str | None:
        return names(self.latin, *self.commons, *self.botanical)

    def named_in(self, title: str) -> bool:
        if self.key.strip() and self.key in title:
            return True
        found = [b in title for b in self.binomials]
        # a combination is named only when every one of its plants is
        return bool(found) and (all(found) if self.combination else any(found))

    def parts_in(self, title: str) -> bool:
        return all(any(_norm(w) in title for w in _PARTS[p]) for p in self.parts)


def _ema_match(substances: list[_Substance], doc_type: str,
               title_text: str) -> tuple[str, list[_Substance]]:
    """The substances a document's title names: ('ok', [...]), or why there are none.

    A title names a substance by its Latin name, or by its plant's binomial together with
    every plant part of the Latin name ('Valeriana officinalis L., radix' is Valerianae
    radix, not Valerianae aetheroleum). Two substances with the same parts (bitter and
    sweet fennel) are told apart only by the other words of their Latin names; a title
    that does not is ambiguous, and nothing is guessed.
    """
    title = _norm(title_text)
    if doc_type == "herbal-summary":
        head = _norm(re.split(r":| - ", title_text, maxsplit=1)[0])
        hit = [s for s in substances if any(_norm(c) == head for c in s.commons)]
        if len(hit) == 1:
            return "ok", hit
    named = [s for s in substances if s.named_in(title)]
    if not named:
        return "unnamed", []
    fit = [s for s in named if s.parts_in(title)]
    # 'radix cum herba' named: the substance with only 'radix' is not what is meant
    fit = [s for s in fit if not any(set(s.parts) < set(o.parts) for o in fit)]
    if not fit:
        # 'Draft assessment report on Rhodiola rosea': a title naming a plant and no plant
        # part is about that plant's substance only when the index has just one
        if len(named) == 1 and not any(_norm(w) in title for words in _PARTS.values()
                                       for w in words):
            return "ok", named
        return "the plant part of no indexed substance is named", named
    groups: dict[tuple, list[_Substance]] = defaultdict(list)
    for s in fit:
        groups[s.parts].append(s)
    out: list[_Substance] = []
    for same in groups.values():
        if len(same) > 1:
            told = [s for s in same if s.extra and all(f" {w} " in title for w in s.extra)]
            if len(told) != 1:
                return "several indexed substances match the title", same
            same = told
        out += same
    return "ok", out


def _ema_herbal(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "herbal"):
        return
    by_latin: dict[str, _Substance] = {}
    for r in rows(conn, "SELECT * FROM herbal"):
        latin = v(r["latin_name"])
        if latin:
            latin = " ".join(latin.split())
            by_latin.setdefault(latin, _Substance(latin)).add(r)
    substances = list(by_latin.values())
    if not has(conn, "document"):
        return
    for d in rows(conn, "SELECT * FROM document"):
        doc_type, title = v(d["type"]) or "", v(d["name"]) or ""
        if doc_type not in _EMA_ASSESSMENT or not title:
            continue
        status, found = _ema_match(substances, doc_type, title)
        if status == "unnamed" and doc_type == "public-statement":
            continue                        # a public statement on a medicine, not a herb
        note = names(doc_type, d["reference_number"])
        if status != "ok":
            yield unresolved("subject_monograph", "ema_herbal", None,
                             names(*(s.latin for s in found)) if found else None, title,
                             "no indexed herbal substance is named in the title"
                             if status == "unnamed" else status,
                             reference=d["document_url"], note=note)
            continue
        superseded = title.lower().startswith("superseded")
        for s in found:
            yield rel("subject_monograph", "ema_herbal", s.id, s.name,
                      f"ema:document.{v(d['id'])}", title, "reported",
                      subject_type="herb", reference=d["document_url"], note=note,
                      context=ctx(stage=v(d["status"]),
                                  flags="superseded" if superseded else None))


# ============================================================================ datasets
_NOT_STATED = "not stated (no data licence or terms of use on the site)"

DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "tcmtoxdb", "TCMToxDB", (71,), "https://www.sdu-idea.cn/TCMToxDB/", _NOT_STATED,
        (FileSpec(_TOX + "TCMTox_formula.csv", "TCMTox_formula.csv", "formula", fmt="csv",
                  note="1555 formulas; Prescription lists each "
                  "herb's pinyin and dose ('Dai Huang 100g,...')"),
         FileSpec(_TOX + "TCMTox_herb.csv", "TCMTox_herb.csv", "herb", fmt="csv",
                  note="229 herbs with HERB, SymMap, TCMID, TCMSP "
                  "and TCM-ID ids; Toxicity holds a grade for 43 of them"),
         FileSpec(_TOX + "TCMTox_ingredient.csv", "TCMTox_ingredient.csv", "ingredient",
                  fmt="csv", note="9377 ingredients with cross-ids "
                  "(the SMILES column is spelled 'Simles'); no herb link in the file"),
         FileSpec(_TOX + "TCMTox_target.csv", "TCMTox_target.csv", "target", fmt="csv",
                  note="7885 targets; Toxicity lists organ "
                  "toxicities separated by ';'")),
        version="2026-03-05 (the files' Last-Modified)",
        notes="Toxicology database of TCM (Database 2026, baag019). formula_herb: the "
              "formula's prescription as ChP 2020 / ETCM 2.0 list it, with the dose in the "
              "context ('listed'); a herb in the herb table keeps its id, others are named "
              "by pinyin. subject_toxicity: a herb's grade (Extremely toxic / Toxic / "
              "Slightly toxic, 'listed'; a herb with no grade has no row, which is not "
              "'non-toxic'), and a gene's organ toxicities, which the authors retrieved "
              "from GeneCards by keyword and reviewed by hand ('aggregated', via "
              "GeneCards). The herb-ingredient and herb/ingredient-target edges and the "
              "1134 toxicity references are served only by an undocumented API that "
              "returned 502 on 2026-10-01. The describing article's licence covers the "
              "article, not the data.",
        relations=("formula_herb", "subject_toxicity"), commercial_use="unknown",
        upstream=("ChP 2020", "ETCM 2.0", "HERB", "SymMap", "TCMID", "TCM-ID", "TCMSP",
                  "TCMBank", "HIT 2.0", "TTD", "GeneCards")),
    DatasetSpec(
        "mibig", "MIBiG 4.0", (73,), "https://mibig.secondarymetabolites.org/", "CC BY 4.0",
        (FileSpec("https://dl.secondarymetabolites.org/mibig/mibig_json_4.0.tar.gz",
                  "mibig_json_4.0.tar.gz", "entry", fmt="mibig_entry", expected_bytes=954258,
                  note="one JSON record per biosynthetic gene cluster (3013: 2437 active, "
                       "377 retired, 199 pending)"),
         FileSpec("https://dl.secondarymetabolites.org/mibig/mibig_json_4.0.tar.gz",
                  "mibig_json_4.0.tar.gz", "compound", fmt="mibig_compound",
                  expected_bytes=954258),
         FileSpec("https://dl.secondarymetabolites.org/mibig/mibig_json_4.0.tar.gz",
                  "mibig_json_4.0.tar.gz", "bioactivity", fmt="mibig_bioactivity",
                  expected_bytes=954258),
         FileSpec("https://dl.secondarymetabolites.org/mibig/mibig_prot_seqs_4.0.fasta",
                  "mibig_prot_seqs_4.0.fasta", "", fmt="raw", optional=True,
                  expected_bytes=31833799, note="protein translations of every gene"),
         FileSpec("https://dl.secondarymetabolites.org/mibig/mibig_gbk_4.0.tar.gz",
                  "mibig_gbk_4.0.tar.gz", "", fmt="raw", optional=True,
                  expected_bytes=83652126, note="GenBank records of the clusters")),
        version="4.0 (2024-11-15)",
        notes="Community-curated biosynthetic gene clusters and their products (NAR 2025, "
              "gkae1115). Only active records give relations. organism_compound: the "
              "producing organism (NCBI Taxonomy) and each compound, by PubChem, ChEBI, "
              "ChEMBL or NPAtlas id, else by the record's name for it ('known'; "
              "'predicted' when the cluster's only evidence is homology or a synthetic-"
              "bioinformatic product); the note is the BGC accession. compound_assay: a "
              "bioactivity the record says was observed (positive) or tested and not "
              "observed (negative), with the assay concentration. The 5.0 release "
              "candidate on the download server (2026-09-21) is not announced and not "
              "used. npatlas: (and other database) ids are MIBiG's own cross-references "
              "to identify a compound; MIBiG does not redistribute NPAtlas records, and "
              "every relation comes from MIBiG's own curation.",
        relations=("organism_compound", "compound_assay"), commercial_use="allowed"),
    DatasetSpec(
        "gutmgene", "gutMGene v2.0", (74,), "http://bio-computing.hrbmu.edu.cn/gutmgene/",
        _NOT_STATED,
        (FileSpec(_GUT + "Gut%20Microbe-Microbial%20metabolite.csv",
                  "gutmgene_microbe_metabolite.csv", "microbe_metabolite",
                  fmt="gutmgene_csv", note="'Gut Microbe-Microbial metabolite.csv'"),
         FileSpec(_GUT + "Microbial%20metabolite-Host%20Gene.csv",
                  "gutmgene_metabolite_gene.csv", "metabolite_gene", fmt="gutmgene_csv",
                  note="'Microbial metabolite-Host Gene.csv'"),
         FileSpec(_GUT + "Gut%20Microbe-Host%20Gene.csv", "gutmgene_microbe_gene.csv",
                  "microbe_gene", fmt="gutmgene_csv",
                  note="'Gut Microbe-Host Gene.csv' (GB18030-encoded)"),
         FileSpec(_GUT + "Metabolicreconstitution.tar.gz", "Metabolicreconstitution.tar.gz",
                  "", fmt="raw", optional=True,
                  note="genome-scale metabolic reconstructions of MGnify gut genomes "
                       "(predicted genome -> metabolite, one CSV per genome); the server "
                       "gives no size")),
        version="2.0 (NAR 2025, 53:D783)",
        notes="Gut microbe, microbial metabolite and host gene associations curated from "
              "PubMed papers, human and mouse. Evidence follows the source's associative "
              "mode: 'causally' (a controlled experiment) is 'known', 'correlatively' (a "
              "statistical correlation) is 'associated'. organism_compound: a microbe and "
              "the metabolite it produces or covaries with (substrates in the note). "
              "regulation: a metabolite or microbe and the host gene it changes. The "
              "source's alteration word (activation / inhibition, kept in context.action) "
              "describes the gene's change, mostly in expression, so a causal row's "
              "effect is increase / decrease; a correlative row has no effect (the word "
              "is only the sign of a covariation). "
              "Host species, sample, method, measurement technique, condition (with DOID) "
              "and throughput are in the context. Rows naming a microbe without an NCBI "
              "Taxonomy id or a metabolite without any compound id go to the unresolved "
              "queue. The describing article's licence covers the article, not the data.",
        relations=("organism_compound", "regulation"), commercial_use="unknown"),
    DatasetSpec(
        "ema_herbal", "EMA herbal medicines (HMPC)", (75,),
        "https://www.ema.europa.eu/en/human-regulatory-overview/herbal-medicinal-products",
        "EMA legal notice: reproduction and distribution allowed for any purpose, commercial "
        "included, provided EMA is acknowledged as the source (attribution-only, as CC BY); "
        "not for third-party content",
        (FileSpec(_EMA + "medicines-output-herbal_medicines-report-output-json_en.json",
                  "ema_herbal_medicines.json", "herbal", fmt="ema_json",
                  note="the HMPC's herbal substances: status, outcome (EU herbal monograph, "
                       "EU list entry, public statement), therapeutic area, page URL; rows "
                       "repeat (251 rows for 204 records)"),
         FileSpec(_EMA + "documents-output-json-report_en.json", "ema_documents.json",
                  "document", fmt="ema_json",
                  note="every document on EMA's website (about 37 MB); the table keeps the "
                       "herbal-* types and public statements")),
        version="rolling (regenerated at 06:00 and 18:00 CET; each row carries the file's "
                "meta_timestamp)",
        notes="Herbal substances assessed by the Committee on Herbal Medicinal Products "
              "and their documents. subject_monograph: a herbal substance (by its Latin "
              "pharmacopoeial name) and an EU herbal monograph, assessment report, HMPC "
              "opinion, list entry, public statement or public summary about it "
              "('reported'); the document's status is context.stage and a superseded "
              "document is flagged. The documents index has no substance field: a "
              "document is linked to the substance its title names by Latin name or by "
              "plant binomial plus plant part, and a title naming no indexed substance, or "
              "several indistinguishable ones, goes to the unresolved queue. Documents are "
              "not downloaded or parsed.",
        relations=("subject_monograph",), commercial_use="allowed"),
)

EXTRACTORS = {
    "tcmtoxdb": _tcmtoxdb,
    "mibig": _mibig,
    "gutmgene": _gutmgene,
    "ema_herbal": _ema_herbal,
}

READERS = {
    "mibig_entry": _read_mibig_entry,
    "mibig_compound": _read_mibig_compound,
    "mibig_bioactivity": _read_mibig_bioactivity,
    "gutmgene_csv": _read_gutmgene,
    "ema_json": _read_ema,
}

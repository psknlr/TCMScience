"""Mechanism sources: interactions, signed causal relations, complexes, screens, motifs.

Checked from the harness on 2026-10-01 (review of 2026-09-30):

* **BioGRID** (catalogue 85, MIT): curated physical and genetic interactions (Tab 3.0) and
  protein-chemical associations; **BioGRID ORCS** (85): curated CRISPR screens, one row per
  gene per screen with the screen's own hit call.
* **IntAct / Complex Portal** (86): Complex Portal's curated complexes (CC0) and IntAct's
  negative interactions (CC BY 4.0); positive IntAct evidence is queried live (connector
  ``intact``).
* **SIGNOR** (87, CC BY 4.0): signed causal relations with mechanism, residue, cell and
  tissue, and SIGNOR's complexes.
* **JASPAR** (89, CC BY 4.0): transcription-factor binding profiles. The matrices stay
  files (the analysis layer); the store holds their annotations and the factor -> motif
  links.

GtoPdb (88) is not wrapped: since September 2026 its site requires registration and its
web services an API key, and commercial organisations pay for access. Its licence is
ODbL 1.0 (database) and CC BY-SA 4.0 (contents); commercial use is 'unknown' (fee-based
access, not a licence prohibition).
"""

from __future__ import annotations

import csv
import gzip
import re
import sqlite3
import tarfile
from pathlib import Path
from typing import Any, Iterator

from ..rowkit import Row, ctx, has, names, rel, rows, unresolved, v
from ..spec import DatasetSpec, FileSpec
from ..store import StoreError, open_text

__all__ = ["DATASETS", "EXTRACTORS", "KINDS", "READERS"]

#: A transcription factor's binding profile. ``tf_target`` links a factor to the genes it
#: binds; a motif is neither a gene nor a target, so it has its own kind.
KINDS = {"tf_motif": ("regulator", "motif")}

_BIOGRID = "https://downloads.thebiogrid.org/Download/BioGRID/Release-Archive/BIOGRID-5.0.262/"
_ORCS = ("https://downloads.thebiogrid.org/Download/BioGRID-ORCS/Release-Archive/"
         "BIOGRID-ORCS-2.0.18/")
_COMPLEXTAB = "https://ftp.ebi.ac.uk/pub/databases/intact/complex/current/complextab/"
_INTACT = "https://ftp.ebi.ac.uk/pub/databases/intact/current/psimitab/"
_JASPAR = "https://jaspar.elixir.no/download/"

_MIT = "MIT (BioGRID: 'freely available to both academic and commercial users')"
_UPSTREAM_NAME = {"FLYBASE": "FlyBase", "WORMBASE": "WormBase", "POMBASE": "PomBase",
                  "BAR": "BAR", "SGD": "SGD", "TAIR": "TAIR", "MGI": "MGI",
                  "DRUGBANK": "DrugBank", "BINDINGDB": "BindingDB"}
#: everything BioGRID's Source_Database / Curated_By columns can name besides itself
_BIOGRID_UPSTREAM = tuple(_UPSTREAM_NAME.values())
#: IntAct's Source_database names (lower case) other than IntAct itself -> lineage name
_INTACT_UPSTREAM = {"mint": "MINT", "uniprot": "UniProt", "i2d": "I2D", "bhf-ucl": "BHF-UCL",
                    "hpidb": "HPIDb", "innatedb": "InnateDB", "mbinfo": "MBInfo",
                    "matrixdb": "MatrixDB", "molcon": "MolCon", "dip": "DIP"}
_DRUGBANK_ROWS = ("CC BY-NC 4.0 (record imported by BioGRID from DrugBank, whose full "
                  "dataset is licensed CC BY-NC 4.0; BioGRID's own terms are MIT)")
_BINDINGDB_ROWS = ("MIT (BioGRID's redistribution of a record imported from BindingDB; "
                   "BindingDB's own terms also apply)")


def _orcs(species: str, size: int, *, optional: bool, note: str) -> tuple[FileSpec, ...]:
    name = f"BIOGRID-ORCS-ALL-{species}-2.0.18.screens.tar.gz"
    return (FileSpec(_ORCS + name, name, f"screens_{species}", fmt="orcs_index",
                     optional=optional, expected_bytes=size, note=note),
            FileSpec(_ORCS + name, name, f"scores_{species}", fmt="orcs_scores",
                     optional=optional, expected_bytes=size,
                     note="one row per gene per screen; the ALIASES column is not loaded"))


def _jaspar_sql(table: str) -> FileSpec:
    return FileSpec(_JASPAR + "database/JASPAR2026.sql.gz", "JASPAR2026.sql.gz", table,
                    fmt="jaspar_sql", expected_bytes=2228558,
                    note="the SQLite dump's table of that name; served with "
                         "Content-Encoding gzip")


DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "biogrid", "BioGRID interactions and chemical associations", (85,),
        "https://thebiogrid.org/", _MIT,
        (FileSpec(_BIOGRID + "BIOGRID-MV-Physical-5.0.262.tab3.zip",
                  "BIOGRID-MV-Physical-5.0.262.tab3.zip", "mv_physical",
                  expected_bytes=35126043,
                  note="multi-validated physical interactions, all organisms (Tab 3.0)"),
         FileSpec(_BIOGRID + "BIOGRID-CHEMICALS-5.0.262.chemtab.zip",
                  "BIOGRID-CHEMICALS-5.0.262.chemtab.zip", "chemicals",
                  expected_bytes=1370068,
                  note="protein-chemical associations; most rows imported from DrugBank"),
         FileSpec(_BIOGRID + "BIOGRID-ALL-5.0.262.tab3.zip", "BIOGRID-ALL-5.0.262.tab3.zip",
                  "interactions", optional=True, expected_bytes=182882024,
                  note="every interaction, physical and genetic, all organisms: 183 MB "
                       "zipped, 1.56 GB unpacked")),
        version="5.0.262 (2026-10-01)",
        notes="Literature-curated interactions. By default the store holds BioGRID's "
              "multi-validated physical set (pairs supported by several experimental "
              "systems or publications) as protein_interaction rows, evidence 'known', "
              "one row per pair, publication and experimental system (context.method), "
              "with the throughput in context.flags. With the optional complete file every "
              "physical and genetic interaction is a row (genetic_interaction for genetic "
              "systems such as Synthetic Lethality) and the multi-validated ones are "
              "flagged. Genes are NCBI Gene ids. Chemical associations become drug_target "
              "rows (DrugBank drugs) or compound_target rows, with the source's Action word "
              "in context.action and its direction as the effect. Most were imported from "
              "DrugBank (note 'via DrugBank') and carry DrugBank's non-commercial licence "
              "per row; BioGRID's own curation is MIT. The REST API needs a registered "
              "access key and is not wrapped.",
        relations=("protein_interaction", "genetic_interaction", "drug_target",
                   "compound_target"),
        relation_licenses={"drug_target": "MIT (BioGRID curation); " + _DRUGBANK_ROWS},
        commercial_use="allowed",
        upstream=_BIOGRID_UPSTREAM,
        # human genes only, and only ids that name one symbol (a Swiss-Prot accession
        # shared by two genes of a cluster maps to neither)
        crosswalk={
            "gene": "SELECT id, min(sym) FROM (SELECT 'ncbigene:' || "
                    "Entrez_Gene_Interactor_A AS id, 'symbol:' || "
                    "Official_Symbol_Interactor_A AS sym FROM mv_physical WHERE "
                    "Organism_ID_Interactor_A = '9606' AND Entrez_Gene_Interactor_A GLOB "
                    "'[0-9]*' UNION SELECT 'ncbigene:' || Entrez_Gene_Interactor_B, "
                    "'symbol:' || Official_Symbol_Interactor_B FROM mv_physical WHERE "
                    "Organism_ID_Interactor_B = '9606' AND Entrez_Gene_Interactor_B GLOB "
                    "'[0-9]*' UNION SELECT 'uniprot:' || SWISS_PROT_Accessions_Interactor_A, "
                    "'symbol:' || Official_Symbol_Interactor_A FROM mv_physical WHERE "
                    "Organism_ID_Interactor_A = '9606' AND "
                    "SWISS_PROT_Accessions_Interactor_A GLOB '[A-Z0-9]*' AND "
                    "instr(SWISS_PROT_Accessions_Interactor_A, '|') = 0 UNION SELECT "
                    "'uniprot:' || SWISS_PROT_Accessions_Interactor_B, 'symbol:' || "
                    "Official_Symbol_Interactor_B FROM mv_physical WHERE "
                    "Organism_ID_Interactor_B = '9606' AND "
                    "SWISS_PROT_Accessions_Interactor_B GLOB '[A-Z0-9]*' AND "
                    "instr(SWISS_PROT_Accessions_Interactor_B, '|') = 0) GROUP BY id "
                    "HAVING count(DISTINCT sym) = 1",
            "compound": "SELECT DISTINCT 'drugbank:' || Chemical_Source_ID, 'inchikey:' || "
                        "InChIKey FROM chemicals WHERE Chemical_Source = 'DRUGBANK' AND "
                        "length(InChIKey) = 27"}),
    DatasetSpec(
        "biogrid_orcs", "BioGRID ORCS CRISPR screens", (85,), "https://orcs.thebiogrid.org/",
        _MIT,
        _orcs("drosophila_melanogaster", 755206, optional=False,
              note="the smallest archive, so the store builds by default")
        + _orcs("saccharomyces_cerevisiae_S288C", 3214226, optional=True,
                note="yeast screens")
        + _orcs("mus_musculus", 57383951, optional=True,
                note="mouse screens: 57 MB zipped, millions of gene rows")
        + _orcs("homo_sapiens", 752653348, optional=True,
                note="human screens: 753 MB zipped, above the downloader's size gate; "
                     "fetch with confirm=True only on a disk that can hold it"),
        version="2.0.18",
        notes="Published CRISPR screens re-curated by BioGRID: each screen's index entry "
              "(library, enzyme, cell line, condition, phenotype, the authors' "
              "significance criterion) and its per-gene scores with the authors' hit "
              "call. Every gene a screen scored is a screen_gene row: outcome 'positive' "
              "for a hit, 'negative' for a gene the screen scored and did not call. "
              "Evidence 'known' (a measured perturbation). context: screen id, cell line, "
              "condition, dose, duration, library, phenotype, the analysis (method), the "
              "screen type (assay), the significance criterion (qc) and the score's type "
              "(measure); the score is SCORE.1. The human archive is optional: it is 753 MB.",
        relations=("screen_gene",),
        commercial_use="allowed"),
    DatasetSpec(
        "intact", "IntAct negative interactions and Complex Portal complexes", (86,),
        "https://www.ebi.ac.uk/complexportal/",
        "CC0 1.0 (Complex Portal); CC BY 4.0 (IntAct)",
        (FileSpec(_COMPLEXTAB + "9606.tsv", "complextab_9606.tsv", "complexes_human",
                  license="CC0 1.0"),
         FileSpec(_COMPLEXTAB + "10090.tsv", "complextab_10090.tsv", "complexes_mouse",
                  license="CC0 1.0"),
         FileSpec(_INTACT + "intact_negative.txt", "intact_negative.txt", "negative",
                  license="CC BY 4.0",
                  note="interactions reported as tested and not found (PSI-MITAB 2.7)")),
        version="IntAct release 2026-01-09; Complex Portal files of 2026-01-14",
        notes="Complex Portal's curated human and mouse complexes as complex_member rows "
              "(evidence 'listed'; the complex's ECO code in context.confidence, so "
              "experimentally shown complexes (ECO:0000353) stay apart from ones inferred "
              "from orthology or background knowledge; the stoichiometry in the note, "
              "none when Complex Portal writes 0 for unknown; members are UniProt "
              "proteins, ChEBI compounds, RNAcentral RNAs or other complexes). The "
              "Ligand/Agonist/Antagonist columns are free text and are not read. IntAct's "
              "negative interactions (pairs a paper tested and found not to interact) "
              "become protein_interaction rows (compound_target for a small molecule) "
              "with outcome 'negative'; their miscore of 0 is not kept, as IntAct does not "
              "score negative evidence. Positive IntAct evidence is queried per gene "
              "through the live connector 'intact' (the full MITAB is 11 GB). "
              "ftp.ebi.ac.uk's robots.txt disallows all crawling; these three files are "
              "fetched because Complex Portal's and IntAct's documentation offer them as "
              "their download files, and no directory there is listed or crawled.",
        relations=("complex_member", "compound_target", "protein_interaction"),
        relation_licenses={"complex_member": "CC0 1.0 (Complex Portal)",
                           "compound_target": "CC BY 4.0 (IntAct)",
                           "protein_interaction": "CC BY 4.0 (IntAct)"},
        commercial_use="allowed",
        upstream=("IMEx",) + tuple(_INTACT_UPSTREAM.values())),
    DatasetSpec(
        "signor", "SIGNOR causal relations", (87,), "https://signor.uniroma2.it/",
        "CC BY 4.0",
        (FileSpec("https://signor.uniroma2.it/releases/Oct2026_release.txt",
                  "SIGNOR_Oct2026_release.txt", "relations_release", fmt="signor_tsv",
                  expected_bytes=21425930,
                  note="the quarterly stable release; releases/getLatestRelease.php names "
                       "the newest"),
         FileSpec("https://signor.uniroma2.it/API/getComplexData.php",
                  "SIGNOR_complexes.tsv", "complexes", fmt="signor_tsv",
                  note="the documented API's complex list: id, name, members")),
        version="October 2026 release (SIGNOR 4.0)",
        notes="Manually curated causal statements: a regulator up- or down-regulates a "
              "target by a mechanism (phosphorylation, binding, transcriptional "
              "regulation, ...), at a residue, in a cell and tissue (BTO ids), directly or "
              "not, with the PubMed id. Rows are 'regulation', or 'compound_target' when a "
              "chemical or small molecule acts on a protein (or complex, family), or "
              "'drug_target' for a DrugBank antibody; evidence 'known'. The effect "
              "follows SIGNOR's curation manual (July 2021): 'up-regulates activity' -> "
              "activation, 'down-regulates activity' -> inhibition, 'up-regulates "
              "quantity' (by expression, by stabilization) -> increase, 'down-regulates "
              "quantity' (by repression, by destabilization) -> decrease, 'form complex' "
              "-> binding. The manual defines bare 'up-regulates' and 'down-regulates' as "
              "'generic, used when no additional info is provided': they give a sign but "
              "not whether activity or amount changes, so they are 'modulation', with the "
              "word (and its sign) kept in context.action; 'unknown' has no effect. SIGNOR "
              "remaps animal-model results to the human orthologues, so ids are human "
              "while context.species keeps the organism of the experiment. SIGNOR "
              "complexes become complex_member rows (evidence 'listed').",
        relations=("regulation", "compound_target", "drug_target", "complex_member"),
        commercial_use="allowed"),
    DatasetSpec(
        "jaspar", "JASPAR 2026 transcription factor binding profiles", (89,),
        "https://jaspar.elixir.no/", "CC BY 4.0",
        (_jaspar_sql("matrix"), _jaspar_sql("matrix_annotation"),
         _jaspar_sql("matrix_protein"), _jaspar_sql("matrix_species"),
         FileSpec(_JASPAR + "data/2026/CORE/JASPAR2026_CORE_non-redundant_pfms_meme.txt",
                  "JASPAR2026_CORE_non-redundant_pfms_meme.txt", "", fmt="raw",
                  expected_bytes=1351973,
                  note="CORE non-redundant matrices, all taxa, MEME format (motif scanning)"),
         FileSpec(_JASPAR + "data/2026/CORE/"
                  "JASPAR2026_CORE_vertebrates_non-redundant_pfms_jaspar.txt",
                  "JASPAR2026_CORE_vertebrates_non-redundant_pfms_jaspar.txt", "",
                  fmt="raw", expected_bytes=336314,
                  note="CORE non-redundant vertebrate matrices, JASPAR format"),
         FileSpec(_JASPAR + "data/2026/sites.tar.gz", "sites.tar.gz", "", fmt="raw",
                  optional=True, expected_bytes=300409126,
                  note="the binding sites behind each matrix (FASTA), 300 MB"),
         FileSpec(_JASPAR + "data/2026/bed.tar.gz", "bed.tar.gz", "", fmt="raw",
                  optional=True, expected_bytes=297683747,
                  note="the binding sites as genomic intervals (BED), 298 MB")),
        version="2026 (11th release)",
        notes="Curated position frequency matrices of transcription factors, each built "
              "from published binding data (ChIP-seq, HT-SELEX, PBM, ...). The matrices "
              "stay files for motif scanning; the store holds the SQL dump's matrix, "
              "annotation, protein and species tables. tf_motif rows link a factor "
              "(UniProt) to each CORE matrix: evidence 'known', the data type in "
              "context.method, the species in context.species, the PubMed id as the "
              "reference. A dimer's matrix links each of its proteins (the note names the "
              "dimer). Matrices of the UNVALIDATED collection (no orthogonal support "
              "found by the curators) stay in the tables and give no rows.",
        relations=("tf_motif",),
        commercial_use="allowed"),
)


# ------------------------------------------------------------------------------ readers
def _cells(line: str) -> list[str | None]:
    return [c.strip() or None for c in line.rstrip("\r\n").split("\t")]


def _orcs_members(path: Path, suffix: str) -> Iterator[Iterator[str]]:
    """The archive's members whose names end with ``suffix``, in order, as text lines.

    The archive is streamed (``r|gz``), never unpacked to disk, so a member's name cannot
    place a file anywhere.
    """
    try:
        with tarfile.open(path, mode="r|gz") as tar:
            for member in tar:
                if member.isfile() and member.name.endswith(suffix):
                    fh = tar.extractfile(member)
                    if fh is not None:
                        yield (raw.decode("utf-8", "replace") for raw in fh)
    except tarfile.TarError as exc:
        raise StoreError(f"{path.name}: not a readable tar.gz archive ({exc})") from exc


def _orcs_index(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """The screen index of a BioGRID ORCS archive (``...SCREEN_INDEX....index.tab.txt``)."""
    for fh in _orcs_members(path, ".index.tab.txt"):
        header = None
        for line in fh:
            if not line.strip():
                continue
            if header is None:
                header = [c.lstrip("#") for c in _cells(line) if c]
                yield header
                continue
            yield _cells(line)[:len(header)]
        return
    raise StoreError(f"{path.name}: no screen index in the archive")


def _orcs_scores(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """Every screen's gene scores of a BioGRID ORCS archive, one table.

    The members share one header (``#SCREEN_ID IDENTIFIER_ID ... HIT SOURCE``); it is
    yielded once. ALIASES (each gene's synonyms, often hundreds of characters) is left
    out: it repeats per screen and NCBI Gene holds it.
    """
    header: list[str] | None = None
    keep: list[int] = []
    for fh in _orcs_members(path, ".screen.tab.txt"):
        first = True
        for line in fh:
            if not line.strip():
                continue
            if first:
                first = False
                cells = [c.lstrip("#") if c else "" for c in _cells(line)]
                if header is None:
                    keep = [i for i, c in enumerate(cells) if c and c != "ALIASES"]
                    header = [cells[i] for i in keep]
                    yield header
                continue
            cells = _cells(line)
            yield [cells[i] if i < len(cells) else None for i in keep]
    if header is None:
        raise StoreError(f"{path.name}: no screen files in the archive")


#: The statements a JASPAR dump may run; anything else (ATTACH, VACUUM INTO, ...) is skipped.
_SQL_OK = re.compile(r"^\s*(CREATE\s+(UNIQUE\s+)?(TABLE|INDEX)|INSERT\s+INTO|BEGIN|COMMIT|"
                     r"PRAGMA\s+foreign_keys)", re.I)
_TXN = re.compile(r"^\s*(BEGIN|COMMIT)\b", re.I)


def _deny_attach(action: int, *args: Any) -> int:
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _jaspar_sql(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """One table of JASPAR's SQLite dump (``spec.table``, upper-cased, names it).

    The dump is SQL text, gzipped; the site serves it with ``Content-Encoding: gzip``, so
    a client that honours the header saves it already unpacked: both are read. Only
    CREATE/INSERT statements are run, in a private in-memory database that cannot attach
    a file.
    """
    with open(path, "rb") as raw:
        magic = raw.read(2)
    opener = gzip.open if magic == b"\x1f\x8b" else open
    mem = sqlite3.connect(":memory:")
    mem.set_authorizer(_deny_attach)
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as fh:
            buf: list[str] = []
            for line in fh:
                buf.append(line)
                text = "".join(buf)
                if sqlite3.complete_statement(text):
                    buf.clear()
                    if _SQL_OK.match(text) and not _TXN.match(text):
                        mem.execute(text)
        table = spec.table.upper()
        try:
            cur = mem.execute(f'SELECT * FROM "{table}"')
        except sqlite3.OperationalError as exc:
            raise StoreError(f"{path.name}: the dump has no table {table}") from exc
        yield [d[0] for d in cur.description]
        for row in cur:
            yield [None if x is None else str(x) for x in row]
    finally:
        mem.close()


def _signor_tsv(path: Path, spec: FileSpec) -> Iterator[list[str | None]]:
    """SIGNOR's tab-separated files: a header, values in double quotes when they hold
    spaces, and rows that drop trailing empty fields (padded here to the header)."""
    with open_text(path) as fh:
        reader = csv.reader(fh, delimiter="\t", quotechar='"')
        header: list[str] | None = None
        for cells in reader:
            if not any(c.strip() for c in cells):
                continue
            if header is None:
                header = [c.strip() for c in cells]
                yield header
                continue
            out = [c.strip() or None for c in cells[:len(header)]]
            yield out + [None] * (len(header) - len(out))


READERS = {"orcs_index": _orcs_index, "orcs_scores": _orcs_scores,
           "jaspar_sql": _jaspar_sql, "signor_tsv": _signor_tsv}


# ----------------------------------------------------------------------------- helpers
def _ref(text: Any) -> str | None:
    """``PUBMED:123`` / ``pubmed:123`` / ``123`` -> ``pmid:123``; ``DOI:10.x`` -> ``doi:10.x``."""
    t = v(text)
    if not t:
        return None
    if t.isdigit():
        return f"pmid:{t}"
    prefix, _, rest = t.partition(":")
    p = prefix.strip().lower()
    if rest and p in ("pubmed", "pmid") and rest.strip().isdigit():
        return f"pmid:{rest.strip()}"
    if rest and p == "doi":
        return f"doi:{rest.strip()}"
    return None


def _species(*taxa: Any) -> str | None:
    out: list[str] = []
    for t in taxa:
        t = v(t)
        if t and t not in ("-1", "0") and t not in out:
            out.append(t)
    return "|".join(out) or None


def _via(note: str | None, upstream: str | None) -> str | None:
    """A note ending in ``via <UPSTREAM>`` (consensus reads it as the row's lineage)."""
    if not upstream:
        return note
    return f"{note}; via {upstream}" if note else f"via {upstream}"


# ------------------------------------------------------------------------------ BioGRID


def _biogrid_gene(entrez: Any, biogrid_id: Any) -> str | None:
    e = v(entrez)
    if e and e.isdigit():
        return f"ncbigene:{e}"
    b = v(biogrid_id)
    return f"biogrid:{b}" if b else None


def _biogrid_interactions(conn: sqlite3.Connection) -> Iterator[Row | None]:
    """Tab 3.0 rows: the complete file when present, else the multi-validated set."""
    table = "interactions" if has(conn, "interactions") else "mv_physical"
    if not has(conn, table):
        return
    validated: set[str] = set()
    if table == "interactions" and has(conn, "mv_physical"):
        validated = {r[0] for r in conn.execute("SELECT BioGRID_Interaction_ID FROM mv_physical")}
    for r in rows(conn, f'SELECT * FROM "{table}"'):
        kind = {"physical": "protein_interaction", "genetic": "genetic_interaction"}.get(
            (v(r["Experimental_System_Type"]) or "").lower())
        if kind is None:
            continue
        iid = v(r["BioGRID_Interaction_ID"])
        flags = [x.lower() for x in (v(r["Throughput"]) or "").split("|") if x]
        if table == "mv_physical" or iid in validated:
            flags.append("multi-validated")
        mod = v(r["Modification"])
        src = (v(r["Source_Database"]) or "").upper()
        yield rel(kind, "biogrid",
                  _biogrid_gene(r["Entrez_Gene_Interactor_A"], r["BioGRID_ID_Interactor_A"]),
                  names(r["Official_Symbol_Interactor_A"]),
                  _biogrid_gene(r["Entrez_Gene_Interactor_B"], r["BioGRID_ID_Interactor_B"]),
                  names(r["Official_Symbol_Interactor_B"]), "known",
                  score=r["Score"], reference=_ref(r["Publication_Source"]),
                  note=_via(f"BioGRID interaction {iid}",
                            _UPSTREAM_NAME.get(src, src.title()) if src and src != "BIOGRID"
                            else None),
                  context=ctx(method=r["Experimental_System"],
                              species=_species(r["Organism_ID_Interactor_A"],
                                               r["Organism_ID_Interactor_B"]),
                              mechanism=mod, flags="|".join(flags) or None))


#: BioGRID's chemical Action words (mostly DrugBank's) -> effect. Words that name no
#: direction (unknown, cofactor, antibody, multitarget, product of, chaperone, a virus the
#: compound inhibits) give none; the word itself is always kept in context.action.
_CHEM_EFFECT = {
    "inhibitor": "inhibition", "inhibitor, competitive": "inhibition",
    "antagonist": "inhibition", "partial antagonist": "inhibition",
    "inverse agonist": "inhibition", "blocker": "inhibition",
    "negative modulator": "inhibition", "inhibitory allosteric modulator": "inhibition",
    "agonist": "activation", "partial agonist": "activation", "activator": "activation",
    "stimulator": "activation", "potentiator": "activation",
    "positive allosteric modulator": "activation", "positive modulator": "activation",
    "inducer": "increase", "degradation": "degradation",
    "binder": "binding", "binding": "binding", "ligand": "binding",
    "modulator": "modulation", "allosteric modulator": "modulation",
    "cleavage": "other", "acetylation": "other", "deubiquitination": "other",
    "adduct": "other", "intercalation": "other",
}


def _chem_effect(action: str | None) -> str | None:
    """The effect of an Action word; ``inhibitor/sars-cov-2 inhibitor`` reads its first
    part (the second names an antiviral activity, not an action on the target)."""
    a = (action or "").strip().lower()
    return _CHEM_EFFECT.get(a) or _CHEM_EFFECT.get(a.split("/")[0].strip())


def _biogrid_chemical_id(r: sqlite3.Row) -> tuple[str, str | None]:
    """(kind, subject id) of a chemical association row."""
    source, sid = (v(r["Chemical_Source"]) or "").upper(), v(r["Chemical_Source_ID"])
    if source == "DRUGBANK" and sid:
        return "drug_target", f"drugbank:{sid}"
    key = v(r["InChIKey"])
    if key and len(key) == 27:
        return "compound_target", f"inchikey:{key}"
    if sid:
        if source == "PUBCHEM" and sid.isdigit():
            return "compound_target", f"pubchem:{sid}"
        if source == "CHEMBL":
            return "compound_target", f"chembl:{sid.upper()}"
        if source == "CHEBI":
            return "compound_target", "chebi:" + sid.upper().removeprefix("CHEBI:")
        if source == "CHEMSPIDER":
            return "compound_target", f"chemspider:{sid}"
    return "compound_target", f"biogrid:chemical.{v(r['BioGRID_Chemical_ID'])}"


def _biogrid_chemicals(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "chemicals"):
        return
    for r in rows(conn, "SELECT * FROM chemicals"):
        kind, subject = _biogrid_chemical_id(r)
        action = (v(r["Action"]) or "").strip() or None
        curator = (v(r["Curated_By"]) or "").upper()
        upstream = _UPSTREAM_NAME.get(curator) if curator != "BIOGRID" else None
        licence = (_DRUGBANK_ROWS if curator == "DRUGBANK" else _BINDINGDB_ROWS
                   if curator == "BINDINGDB" else _MIT)
        gene = _biogrid_gene(r["Entrez_Gene_ID"], r["BioGRID_Gene_ID"])
        itype = (v(r["Interaction_Type"]) or "").lower()
        label = f"BioGRID chemical interaction {v(r['BioGRID_Chemical_Interaction_ID'])}"
        common = dict(reference=_ref(r["Pubmed_ID"]), license=licence)
        chemical = names(r["Chemical_Name"])
        related = _biogrid_gene(r["Related_Entrez_Gene_ID"], r["Related_BioGRID_Gene_ID"])
        if related and (itype.startswith("recruited") or itype.endswith("targeting protein")):
            # A degrader (PROTAC, molecular glue, LYTAC, AUTAC/ATTEC, ...): the row's gene
            # is the effector the compound recruits (an E3 ligase, IGF2R, LC3, ...); the
            # degraded protein is the related gene, and the Action ('degradation') is what
            # happens to it.
            yield rel(kind, "biogrid", subject, chemical, related,
                      names(r["Related_Official_Symbol"]), "known",
                      effect=_chem_effect(action), **common,
                      note=_via(f"{label}; recruits {v(r['Official_Symbol'])} ({itype})",
                                upstream),
                      context=ctx(action=action, method=r["Method"],
                                  species=_species(r["Related_Organism_ID"])))
            yield rel(kind, "biogrid", subject, chemical, gene, names(r["Official_Symbol"]),
                      "known", **common,
                      note=_via(f"{label}; degrades {v(r['Related_Official_Symbol'])}",
                                upstream),
                      context=ctx(action=itype, method=r["Method"],
                                  species=_species(r["Organism_ID"])))
            continue
        # a non-'target' type with no related gene names no degraded protein: the effect
        # word would land on the effector, so it is kept as context.action only
        on_target = itype in ("", "target")
        yield rel(kind, "biogrid", subject, chemical, gene, names(r["Official_Symbol"]),
                  "known", effect=_chem_effect(action) if on_target else None, **common,
                  note=_via(label + (f"; {itype}" if itype and itype != "target" else ""),
                            upstream),
                  context=ctx(action=action, method=r["Method"],
                              species=_species(r["Organism_ID"])))


def _biogrid(conn: sqlite3.Connection) -> Iterator[Row | None]:
    yield from _biogrid_interactions(conn)
    yield from _biogrid_chemicals(conn)


# --------------------------------------------------------------------------------- ORCS
_GLOBAL_GENE = (("FBgn", "flybase"), ("WBGene", "wormbase"), ("ENSG", "ensembl"),
                ("ENSMUSG", "ensembl"))


def _orcs_gene(r: sqlite3.Row) -> str | None:
    ident, kind = v(r["IDENTIFIER_ID"]), (v(r["IDENTIFIER_TYPE"]) or "").upper()
    if kind == "ENTREZ_GENE" and ident and ident.isdigit():
        return f"ncbigene:{ident}"
    symbol = v(r["OFFICIAL_SYMBOL"]) or ""
    for prefix, ns in _GLOBAL_GENE:
        if re.fullmatch(prefix + r"\d+", symbol):
            return f"{ns}:{symbol}"
    return None


def _screen_name(s: dict) -> str | None:
    """``1-PMID30051818 | S2R+: response to chemicals (Rapamycin 2 nM)``."""
    what = ": ".join(x for x in (v(s.get("CELL_LINE")), v(s.get("PHENOTYPE"))) if x)
    condition = " ".join(x for x in (v(s.get("CONDITION_NAME")),
                                     v(s.get("CONDITION_DOSAGE"))) if x)
    if condition:
        what = f"{what} ({condition})" if what else condition
    return names(s.get("SCREEN_NAME"), what or None)


def _biogrid_orcs(conn: sqlite3.Connection) -> Iterator[Row | None]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'scores\\_%' "
        "ESCAPE '\\' ORDER BY name")]
    for scores in tables:
        index = "screens_" + scores[len("scores_"):]
        screens = ({r["SCREEN_ID"]: dict(r) for r in rows(conn, f'SELECT * FROM "{index}"')}
                   if has(conn, index) else {})
        for r in rows(conn, f'SELECT * FROM "{scores}"'):
            s = screens.get(r["SCREEN_ID"])
            hit = (v(r["HIT"]) or "").upper()
            if s is None or hit not in ("YES", "NO"):
                continue                      # no screen record, or no call: not a result
            sid = v(r["SCREEN_ID"])
            ref = (f"pmid:{v(s['SOURCE_ID'])}" if (v(s["SOURCE_TYPE"]) or "").lower() ==
                   "pubmed" and (v(s["SOURCE_ID"]) or "").isdigit() else None)
            screen_name = _screen_name(s)
            gene = _orcs_gene(r)
            if gene is None:
                yield unresolved("screen_gene", "biogrid_orcs", f"biogrid_orcs:screen.{sid}",
                                 screen_name, v(r["OFFICIAL_SYMBOL"]) or v(r["IDENTIFIER_ID"]),
                                 f"ORCS identifier {v(r['IDENTIFIER_ID'])} of type "
                                 f"{v(r['IDENTIFIER_TYPE'])} is not mapped to a gene",
                                 reference=ref)
                continue
            dose = v(s["CONDITION_DOSAGE"])
            yield rel("screen_gene", "biogrid_orcs", f"biogrid_orcs:screen.{sid}", screen_name,
                      gene, names(r["OFFICIAL_SYMBOL"]), "known",
                      outcome="positive" if hit == "YES" else "negative",
                      score=r["SCORE_1"], reference=ref,
                      context=ctx(screen=sid, cell=s["CELL_LINE"],
                                  species=_species(r["ORGANISM_ID"]),
                                  condition=s["CONDITION_NAME"], dose=dose,
                                  time=s["DURATION"], library=s["LIBRARY"],
                                  phenotype=s["PHENOTYPE"], method=s["ANALYSIS"],
                                  assay=s["SCREEN_TYPE"], qc=s["SIGNIFICANCE_CRITERIA"],
                                  measure=s["SCORE_1_TYPE"]))


# ------------------------------------------------------------------ IntAct / Complex Portal
_MI_NAME = re.compile(r'\(([^()]*)\)\s*$')


def _mi_name(field: Any) -> str | None:
    """``psi-mi:"MI:0018"(two hybrid)`` -> ``two hybrid`` (the first of several)."""
    first = (v(field) or "").split("|")[0]
    m = _MI_NAME.search(first)
    return m.group(1) if m else None


_UNIPROT_VARIANT = re.compile(r"^([A-Z0-9]{6,10})-(PRO_\d+|\d+)$", re.I)


def _member_id(ac: str) -> tuple[str, str, str | None]:
    """(id, entity type, variant) of a Complex Portal or SIGNOR member accession.

    A UniProt processed chain (``P01308-PRO_0000015819``) or isoform (``Q9Y6K9-2``) gets
    the canonical accession as its id, so it meets other sources' ``uniprot:`` ids; the
    chain or isoform is returned as the variant, for the row's note."""
    a = ac.strip()
    up = a.upper()
    if up.startswith("CHEBI:"):
        return "chebi:" + a.split(":", 1)[1], "compound", None
    if up.startswith("CPX-"):
        return f"complexportal:{a}", "complex", None
    if up.startswith("URS"):
        return f"rnacentral:{a}", "rna", None
    if up.startswith("RNACENTRAL:"):
        return "rnacentral:" + a.split(":", 1)[1], "rna", None
    if up.startswith("SIGNOR-"):
        return (f"signor:{a}", "complex" if up.startswith("SIGNOR-C") else "protein_family"
                if up.startswith("SIGNOR-PF") else "entity", None)
    m = _UNIPROT_VARIANT.match(a)
    if m:
        part = m.group(2)
        variant = f"chain {part}" if part.upper().startswith("PRO_") else f"isoform {a}"
        return f"uniprot:{m.group(1)}", "protein", f"{variant} of {m.group(1)}"
    return f"uniprot:{a}", "protein", None


_MEMBER = re.compile(r"^(.+?)\((\d+)\)$")


def _complexes(conn: sqlite3.Connection, table: str) -> Iterator[Row | None]:
    for r in rows(conn, f'SELECT * FROM "{table}"'):
        cpx = v(r["Complex_ac"])
        eco = (v(r["Evidence_Code"]) or "").split("(")[0] or None
        for member in (v(r["Identifiers_and_stoichiometry_of_molecules_in_complex"]) or ""
                       ).split("|"):
            member = member.strip()
            if not member:
                continue
            m = _MEMBER.match(member)
            ac, stoich = (m.group(1), m.group(2)) if m else (member, None)
            oid, otype, variant = _member_id(ac)
            stoich = f"stoichiometry {stoich}" if stoich and stoich != "0" else None
            yield rel("complex_member", "complexportal", f"complexportal:{cpx}",
                      names(r["Recommended_name"]), oid, None, "listed", object_type=otype,
                      note="; ".join(x for x in (variant, stoich) if x) or None,
                      context=ctx(species=_species(r["Taxonomy_identifier"]), confidence=eco),
                      license="CC0 1.0 (Complex Portal)")


def _mitab_id(field: Any) -> tuple[str | None, str]:
    """The id and namespace of a MITAB interactor (``uniprotkb:P12345`` ...)."""
    first = (v(field) or "").split("|")[0]
    ns, _, ac = first.partition(":")
    ns, ac = ns.strip().lower(), ac.strip().strip('"')
    if not ac:
        return None, ns
    if ns == "uniprotkb":
        return f"uniprot:{ac}", ns
    if ns == "chebi":
        return "chebi:" + ac.upper().removeprefix("CHEBI:"), ns
    return f"{ns}:{ac}", ns


def _mitab_name(field: Any) -> str | None:
    for alias in (v(field) or "").split("|"):
        if alias.endswith("(gene name)") or alias.endswith("(display_short)"):
            return alias.split(":", 1)[-1].rsplit("(", 1)[0].strip('"')
    return None


def _taxid(field: Any) -> str | None:
    m = re.match(r"taxid:(-?\d+)", v(field) or "")
    return m.group(1) if m and not m.group(1).startswith("-") else None


def _intact_negative(conn: sqlite3.Connection) -> Iterator[Row | None]:
    for r in rows(conn, "SELECT * FROM negative"):
        if (v(r["Negative"]) or "").lower() != "true":
            continue
        a, _ = _mitab_id(r["ID_s_interactor_A"])
        b, _ = _mitab_id(r["ID_s_interactor_B"])
        na, nb = _mitab_name(r["Alias_es_interactor_A"]), _mitab_name(r["Alias_es_interactor_B"])
        ta, tb = _mi_name(r["Type_s_interactor_A"]), _mi_name(r["Type_s_interactor_B"])
        if tb == "small molecule" and ta != "small molecule":      # the compound first
            a, b, na, nb, ta, tb = b, a, nb, na, tb, ta
        if ta == "small molecule" and tb == "protein":
            kind, st, ot = "compound_target", None, None
        else:
            kind = "protein_interaction"
            st = None if ta == "protein" else (ta or "").replace(" ", "_") or None
            ot = None if tb == "protein" else (tb or "").replace(" ", "_") or None
        pubs = (v(r["Publication_Identifier_s"]) or "").split("|")
        pmid = next((p.split(":", 1)[1] for p in pubs if p.startswith("pubmed:")), None)
        doi = next((p.split(":", 1)[1] for p in pubs if p.startswith("doi:")), None)
        itype = _mi_name(r["Interaction_type_s"])
        source_db = _mi_name(r["Source_database_s"])
        upstream = (_INTACT_UPSTREAM.get(source_db.lower(), source_db)
                    if source_db and source_db.lower() != "intact" else None)
        yield rel(kind, "intact", a, na, b, nb, "known",
                  outcome="negative", subject_type=st, object_type=ot,
                  reference=f"pmid:{pmid}" if pmid else (f"doi:{doi}" if doi else None),
                  note=_via(v(r["Interaction_identifier_s"]), upstream),
                  # IntAct scores no negative evidence: its miscore of 0 is not a measurement
                  context=ctx(method=_mi_name(r["Interaction_detection_method_s"]),
                              mechanism=itype,
                              direct=True if itype == "direct interaction" else None,
                              species=_species(_taxid(r["Taxid_interactor_A"]),
                                               _taxid(r["Taxid_interactor_B"]))),
                  license="CC BY 4.0 (IntAct)")


def _intact(conn: sqlite3.Connection) -> Iterator[Row | None]:
    for table in ("complexes_human", "complexes_mouse"):
        if has(conn, table):
            yield from _complexes(conn, table)
    if has(conn, "negative"):
        yield from _intact_negative(conn)


# -------------------------------------------------------------------------------- SIGNOR
#: SIGNOR's EFFECT -> effect, as its curation manual (July 2021) defines the words. The
#: bare 'up-regulates' / 'down-regulates' are 'generic, used when no additional info is
#: provided': they do not say whether activity or amount changes, so they are not mapped
#: to the activity or the quantity terms; the word keeps the sign in context.action.
_SIGNOR_EFFECT = {
    "up-regulates activity": "activation", "down-regulates activity": "inhibition",
    "up-regulates quantity": "increase", "up-regulates quantity by expression": "increase",
    "up-regulates quantity by stabilization": "increase",
    "down-regulates quantity": "decrease", "down-regulates quantity by repression": "decrease",
    "down-regulates quantity by destabilization": "decrease",
    "up-regulates": "modulation", "down-regulates": "modulation",
    "form complex": "binding",
}
_SIGNOR_TYPE = {"protein": "protein", "complex": "complex", "proteinfamily": "protein_family",
                "fusion protein": "fusion_protein", "chemical": "compound",
                "smallmolecule": "compound", "drug": "drug", "antibody": "drug",
                "mirna": "mirna", "ncrna": "rna", "lncrna": "rna", "phenotype": "phenotype",
                "stimulus": "stimulus"}
_PROTEIN_LIKE = ("protein", "complex", "protein_family", "fusion_protein")


def _signor_id(db: Any, ident: Any) -> str | None:
    db, ident = (v(db) or "").upper(), v(ident)
    if not ident:
        return None
    if db == "UNIPROT":
        return f"uniprot:{ident}"
    if db == "CHEBI":
        return "chebi:" + ident.split(":", 1)[-1]
    if db == "PUBCHEM":
        cid = ident.split(":", 1)[-1]
        return f"pubchem:{cid}" if cid.isdigit() else None
    if db == "RNACENTRAL":
        return f"rnacentral:{ident}"
    if db == "DRUGBANK":
        return f"drugbank:{ident}"
    if db == "SIGNOR":
        return f"signor:{ident}"
    return f"signor:{db.lower()}.{ident}"


def _signor_reference(pmid: Any) -> tuple[str | None, str | None]:
    """(reference, note) of a PMID cell: ``123``, ``1|2|3``, ``1; 2``, ``NBK…``, ``Other``."""
    parts = [p for p in re.split(r"[|;,\s]+", v(pmid) or "") if p]
    ids = [p for p in parts if p.isdigit()]
    if ids:
        rest = ", ".join(f"pmid:{p}" for p in ids[1:])
        return f"pmid:{ids[0]}", (f"also {rest}" if rest else None)
    if parts and parts[0].upper().startswith("NBK"):
        return f"ncbibook:{parts[0]}", None
    if parts and parts[0].upper().startswith("PMC"):
        return f"pmc:{parts[0]}", None
    return None, None


def _signor_relations(conn: sqlite3.Connection) -> Iterator[Row | None]:
    for r in rows(conn, "SELECT * FROM relations_release"):
        ta = _SIGNOR_TYPE.get((v(r["TYPEA"]) or "").lower(), "entity")
        tb = _SIGNOR_TYPE.get((v(r["TYPEB"]) or "").lower(), "entity")
        kind = "regulation"
        if tb in _PROTEIN_LIKE and ta == "compound":
            kind = "compound_target"
        elif tb in _PROTEIN_LIKE and ta == "drug":
            kind = "drug_target"
        word = v(r["EFFECT"])
        ref, extra = _signor_reference(r["PMID"])
        direct = (v(r["DIRECT"]) or "").lower()
        yield rel(kind, "signor", _signor_id(r["DATABASEA"], r["IDA"]), names(r["ENTITYA"]),
                  _signor_id(r["DATABASEB"], r["IDB"]), names(r["ENTITYB"]), "known",
                  subject_type=ta if kind == "regulation" else None,
                  object_type=tb if kind == "regulation" or tb != "protein" else None,
                  effect=_SIGNOR_EFFECT.get((word or "").lower()), reference=ref,
                  note=names(v(r["SIGNOR_ID"]), extra),
                  context=ctx(action=word, mechanism=r["MECHANISM"], residue=r["RESIDUE"],
                              species=_species(r["TAX_ID"]), cell=r["CELL_DATA"],
                              tissue=r["TISSUE_DATA"],
                              direct=True if direct in ("t", "yes", "true") else
                              False if direct in ("f", "no", "false") else None))


def _signor_complexes(conn: sqlite3.Connection) -> Iterator[Row | None]:
    for r in rows(conn, "SELECT * FROM complexes"):
        cid = v(r["SIGNOR_ID"])
        cpx = v(r["COMPLEX_PORTAL_ID"])
        for member in (v(r["MEMBERS"]) or "").split(";"):
            if member.strip():
                oid, otype, variant = _member_id(member)
                cp = f"Complex Portal {cpx}" if cpx else None
                yield rel("complex_member", "signor", f"signor:{cid}", names(r["COMPLEX_NAME"]),
                          oid, None, "listed", object_type=otype,
                          note="; ".join(x for x in (variant, cp) if x) or None)


def _signor(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if has(conn, "relations_release"):
        yield from _signor_relations(conn)
    if has(conn, "complexes"):
        yield from _signor_complexes(conn)


# -------------------------------------------------------------------------------- JASPAR
def _jaspar(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not has(conn, "matrix", "matrix_protein"):
        return
    proteins: dict[str, list[str]] = {}
    for r in rows(conn, "SELECT ID, ACC FROM matrix_protein"):
        if v(r["ACC"]):
            proteins.setdefault(r["ID"], []).append(v(r["ACC"]))
    species: dict[str, list[str]] = {}
    if has(conn, "matrix_species"):
        for r in rows(conn, "SELECT ID, TAX_ID FROM matrix_species"):
            if v(r["TAX_ID"]):
                species.setdefault(r["ID"], []).append(v(r["TAX_ID"]))
    notes: dict[tuple[str, str], list[str]] = {}
    if has(conn, "matrix_annotation"):
        for r in rows(conn, "SELECT ID, TAG, VAL FROM matrix_annotation WHERE TAG IN "
                            "('type', 'medline')"):
            if v(r["VAL"]):
                notes.setdefault((r["ID"], r["TAG"]), []).append(v(r["VAL"]))
    for m in rows(conn, "SELECT * FROM matrix WHERE COLLECTION = 'CORE' ORDER BY BASE_ID, "
                        "CAST(VERSION AS INTEGER)"):
        mid, name = f"{v(m['BASE_ID'])}.{v(m['VERSION'])}", v(m["NAME"])
        pmids = [p for p in notes.get((m["ID"], "medline"), []) if p.isdigit()]
        ref = f"pmid:{pmids[0]}" if pmids else None
        accs = proteins.get(m["ID"], [])
        context = ctx(method=names(*notes.get((m["ID"], "type"), [])),
                      species=_species(*species.get(m["ID"], [])))
        if not accs:
            # unresolved() queues an unidentified object; here the unidentified side is the
            # regulator (the relation's subject), so the row is inverted and says so: a
            # resolver must build <factor id> -> jaspar:<matrix>, not the reverse.
            yield unresolved("tf_motif", "jaspar", f"jaspar:{mid}", name, name,
                             "JASPAR gives no UniProt accession for the factor of this "
                             "matrix (the regulator side is unidentified)", reference=ref,
                             note=f"inverted: subject_id is the motif (the relation's "
                                  f"object); object_name is the regulator; resolve as "
                                  f"<regulator id> -> jaspar:{mid}")
            continue
        dimer = "::" in (name or "") or len(accs) > 1
        for acc in accs:
            yield rel("tf_motif", "jaspar", f"uniprot:{acc}", None if dimer else name,
                      f"jaspar:{mid}", name, "known", reference=ref, context=context,
                      note=f"part of {name}" if dimer else None)


EXTRACTORS = {"biogrid": _biogrid, "biogrid_orcs": _biogrid_orcs, "intact": _intact,
              "signor": _signor, "jaspar": _jaspar}

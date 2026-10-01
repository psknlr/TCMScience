"""One relation shape across databases: herb→ingredient, ingredient→target, and the rest.

The databases name the same things differently: HERB ``HBIN…`` ids, ITCM numeric ids,
BATMAN PubChem CIDs, TM-MC its own codes. Each extractor reads one built store
(``tcmdb.store``) and writes rows of a single shape into its ``relations`` table:

    kind, source, subject_type, subject_id, subject_name,
    object_type, object_id, object_name, evidence, score, reference, note

* **ids** use a global identifier when the source gives one: ``pubchem:<CID>`` for a
  compound, ``ncbigene:<id>`` or ``symbol:<HGNC symbol>`` for a human gene, ``uniprot:`` for a
  protein, ``nct:`` for a trial, ``pmid:`` for a paper. Otherwise the id is
  ``<source>:<local id>``.
* **names** hold every name the source gives, joined by `` | `` (Chinese, pinyin, Latin,
  English). A query matches any one of them.
* **evidence** says how the source knows the relation; it is never upgraded:
    - ``known``: measured or curated from the literature;
    - ``predicted``: a model's output, with its ``score``;
    - ``aggregated``: integrated from other databases without per-row provenance;
    - ``listed``: a composition list (a formula's herbs, a herb's constituents as listed);
    - ``reported``: a document that is about the subject (a trial, a review, a paper);
    - ``signal``: a disproportionality signal mined from spontaneous reports (PRR).
  Predicted, aggregated and signal relations support a hypothesis, never a claim of
  effect. The hub only labels them; a claim must go through the snapshot pipeline,
  where ``tcm.EvidenceTier`` and the artifact validator apply.
* **score** is the source's own number (a prediction score, a STITCH or DisGeNET score,
  a PRR); **reference** is a citation (``pmid:``, a DOI, a textbook); **note** keeps the
  qualifiers a source attaches (dose, clinical status, mechanism, effect direction).
* **values are not cleaned up**. A relation row points back to its source row through
  ``reference`` when there is one, and the source tables stay in the store next to it.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Callable, Iterable, Iterator, Mapping

__all__ = ["RELATION_KINDS", "EVIDENCE", "EXTRACTORS", "build_relations"]

RELATION_KINDS: Mapping[str, tuple[str, str]] = {
    "herb_ingredient": ("herb", "ingredient"),
    "ingredient_target": ("ingredient", "target"),
    "formula_herb": ("formula", "herb"),
    "target_disease": ("target", "disease"),
    "subject_clinical_trial": ("subject", "clinical_trial"),
    "subject_meta_analysis": ("subject", "meta_analysis"),
    "subject_reference": ("subject", "publication"),
    "herb_drug_interaction": ("herb", "drug"),
    "drug_target": ("drug", "target"),
    "gene_set_member": ("gene_set", "target"),
    "drug_adverse_event": ("drug", "adverse_event"),
}
EVIDENCE = frozenset({"known", "predicted", "aggregated", "listed", "reported", "signal"})

_NA = frozenset({"", "na", "n/a", "nan", "none", "null", "-", "--"})

Row = dict[str, Any]


def _v(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if text.lower() in _NA else text


def _names(*values: Any) -> str | None:
    out: list[str] = []
    for value in values:
        for part in re.split(r"\s*[;|]\s*", _v(value) or ""):
            part = part.strip()
            if part and part.lower() not in _NA and part not in out:
                out.append(part)
    return " | ".join(out) or None


def _cid(value: Any) -> str | None:
    text = _v(value)
    if not text:
        return None
    text = text.split(".")[0] if re.fullmatch(r"\d+\.0", text) else text
    return f"pubchem:{text}" if text.isdigit() else None


def _rel(kind: str, source: str, subject_id: Any, subject_name: Any, object_id: Any,
         object_name: Any, evidence: str, *, score: Any = None, reference: Any = None,
         note: Any = None, subject_type: str | None = None,
         object_type: str | None = None) -> Row | None:
    sid, oid = _v(subject_id), _v(object_id)
    if not sid or not oid:
        return None
    st, ot = RELATION_KINDS[kind]
    return {"kind": kind, "source": source, "subject_type": subject_type or st,
            "subject_id": sid, "subject_name": _v(subject_name), "object_type": object_type or ot,
            "object_id": oid, "object_name": _v(object_name), "evidence": evidence,
            "score": _v(score), "reference": _v(reference), "note": _v(note)}


def _has(conn: sqlite3.Connection, *tables: str) -> bool:
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return all(t in have for t in tables)


def _rows(conn: sqlite3.Connection, sql: str) -> Iterator[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    yield from conn.execute(sql)


def _col(row: sqlite3.Row, *names: str) -> Any:
    keys = {k.lower(): k for k in row.keys()}
    for n in names:
        if n.lower() in keys:
            return row[keys[n.lower()]]
    return None


# ------------------------------------------------------------------------------- ITCM
def _itcm(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not _has(conn, "herb", "ingredient"):
        return
    herb = {r["NID"]: (f"itcm:herb.{r['NID']}", _names(r["CHN"], r["PINYIN"], r["LATIN"],
                                                       r["English_Name"]))
            for r in _rows(conn, "SELECT * FROM herb")}
    ing = {r["NID"]: (_cid(r["PUBCHEM_CID"]) or f"itcm:ingredient.{r['NID']}", _names(r["name"]))
           for r in _rows(conn, "SELECT * FROM ingredient")}
    tar = ({r["NID"]: (f"symbol:{r['gene_symbol']}" if _v(r["gene_symbol"])
                       else f"itcm:target.{r['NID']}", _names(r["gene_symbol"], r["Gene_name"]))
            for r in _rows(conn, "SELECT * FROM target")} if _has(conn, "target") else {})
    dis = ({r["NID"]: (f"umls:{r['diseaseId']}" if _v(r["diseaseId"])
                       else f"itcm:disease.{r['NID']}", _names(r["diseaseName"]))
            for r in _rows(conn, "SELECT * FROM disease")} if _has(conn, "disease") else {})
    form = ({r["NID"]: (f"itcm:formula.{r['NID']}", _names(r["CHN"], r["PY"]))
             for r in _rows(conn, "SELECT * FROM formula")} if _has(conn, "formula") else {})

    def pair(table, kind, left, lcol, right, rcol):
        if not _has(conn, table):
            return
        for r in _rows(conn, f'SELECT * FROM "{table}"'):
            a, b = left.get(r[lcol]), right.get(r[rcol])
            if a and b:
                yield _rel(kind, "itcm", a[0], a[1], b[0], b[1], "aggregated")

    yield from pair("herb2ingredient", "herb_ingredient", herb, "herbNID", ing, "ingNID")
    yield from pair("ingredient2target", "ingredient_target", ing, "ingNID", tar, "tarNID")
    yield from pair("formula2herb", "formula_herb", form, "forNID", herb, "herbNID")
    yield from pair("target2disease", "target_disease", tar, "tarNID", dis, "disNID")


# ------------------------------------------------------------------------ BATMAN-TCM 2.0
_NAME_CID = re.compile(r"^(.*)\((\d+)\)\s*$")
_ID_SCORE = re.compile(r"^(\d+)\(([\d.]+)\)$")


def _batman2(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if _has(conn, "known_by_ingredient"):
        for r in _rows(conn, "SELECT * FROM known_by_ingredient"):
            for sym in (_v(r["known_target_proteins"]) or "").split("|"):
                if sym.strip():
                    yield _rel("ingredient_target", "batman2", _cid(r["PubChem_CID"]),
                               r["IUPAC_name"], f"symbol:{sym.strip()}", sym.strip(), "known")
    if _has(conn, "predicted_by_ingredient"):
        for r in _rows(conn, "SELECT * FROM predicted_by_ingredient"):
            for item in (_v(r["predicted_target_proteins"]) or "").split("|"):
                m = _ID_SCORE.match(item.strip())
                if m:
                    yield _rel("ingredient_target", "batman2", _cid(r["PubChem_CID"]),
                               r["IUPAC_name"], f"ncbigene:{m.group(1)}", None, "predicted",
                               score=m.group(2))
    if _has(conn, "herb"):
        for r in _rows(conn, "SELECT * FROM herb"):
            hid = f"batman2:herb.{_v(r['Pinyin_Name'])}"
            hname = _names(r["Chinese_Name"], r["Pinyin_Name"], r["Latin_Name"],
                           r["English_Name"])
            for item in (_v(r["Ingredients"]) or "").split("|"):
                m = _NAME_CID.match(item.strip())
                if m:
                    yield _rel("herb_ingredient", "batman2", hid, hname, f"pubchem:{m.group(2)}",
                               m.group(1).strip(), "aggregated")
    if _has(conn, "formula"):
        for r in _rows(conn, "SELECT * FROM formula"):
            fid = f"batman2:formula.{_v(r['Pinyin_Name'])}"
            for herb in (_v(r["Pinyin_composition"]) or "").split(","):
                if herb.strip():
                    yield _rel("formula_herb", "batman2", fid,
                               _names(r["Chinese_Name"], r["Pinyin_Name"]),
                               f"batman2:herb.{herb.strip()}", herb.strip(), "listed")


# ----------------------------------------------------------------------------- HERB 2.0
def _herb2(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if _has(conn, "formula"):
        for r in _rows(conn, "SELECT * FROM formula"):
            cn = [h.strip() for h in (_v(r["Herbs_in_Chinese"]) or "").split(",")]
            py = [h.strip() for h in (_v(r["Herbs_in_pinyin"]) or "").split(",")]
            for i, name in enumerate(cn):
                if not name:
                    continue
                pinyin = py[i] if len(py) == len(cn) else None
                yield _rel("formula_herb", "herb2", f"herb2:{r['Formula_id']}",
                           _names(r["Formula_cn_name"], r["Formula_pinyin_name"],
                                  r["Formula_en_name"]),
                           f"herb2:herb_name.{name}", _names(name, pinyin), "listed")

    def documents(table: str, kind: str, id_col: str, doc_id: Callable[[sqlite3.Row], Any],
                  title_col: str) -> Iterator[Row | None]:
        if not _has(conn, table):
            return
        for r in _rows(conn, f'SELECT * FROM "{table}"'):
            stype = (_v(r["Subject_type"]) or "subject").lower()
            yield _rel(kind, "herb2", f"herb2:{r['Subject_id']}", _names(r["Subject_name"]),
                       doc_id(r), r[title_col], "reported", reference=r[id_col],
                       subject_type=stype)

    yield from documents("clinical_trial", "subject_clinical_trial", "Clinical_trial_id",
                         lambda r: f"nct:{r['NCT_id']}" if _v(r["NCT_id"]) else None,
                         "NCT_title")
    yield from documents("meta_analysis", "subject_meta_analysis", "Meta_analysis_id",
                         lambda r: f"prospero:{r['CRD_id']}" if _v(r["CRD_id"]) else None,
                         "CRD_title")
    yield from documents("reference", "subject_reference", "Reference_id",
                         lambda r: f"pmid:{r['PubMed_id']}" if _v(r["PubMed_id"]) else None,
                         "Paper_title")


# ---------------------------------------------------------------------------- TM-MC 2.0
def _tmmc2(conn: sqlite3.Connection) -> Iterator[Row | None]:
    material = ({r["LATIN"]: _names(r["CHINESE"], r["PINYIN"], r["LATIN"], r["COMMON"],
                                    r["HANJA"], r["KOREAN"])
                 for r in _rows(conn, "SELECT * FROM medicinal_material")}
                if _has(conn, "medicinal_material") else {})
    compound = ({r["ID"]: _cid(r["CID"]) or (f"inchikey:{r['INCHIKEY']}" if _v(r["INCHIKEY"])
                                            else f"tmmc:{r['ID']}")
                 for r in _rows(conn, "SELECT ID, CID, INCHIKEY FROM chemical_property")}
                if _has(conn, "chemical_property") else {})

    def compound_id(tmmc_id: Any) -> str | None:
        tmmc_id = _v(tmmc_id)
        if not tmmc_id or tmmc_id == "0":          # 0: a compound TM-MC could not identify
            return None
        return compound.get(tmmc_id, f"tmmc:{tmmc_id}")

    if _has(conn, "medicinal_compound"):
        for r in _rows(conn, "SELECT * FROM medicinal_compound"):
            latin = _v(r["LATIN"])
            pmid = _v(r["PMID"])
            yield _rel("herb_ingredient", "tmmc2", f"tmmc:material.{latin}",
                       material.get(latin, latin), compound_id(r["ID"]), r["COMPOUND"],
                       "known", reference=f"pmid:{pmid}" if pmid and pmid.isdigit() else pmid)
    if _has(conn, "chemical_protein"):
        for r in _rows(conn, "SELECT * FROM chemical_protein"):
            # STITCH's combined score mixes experiments, databases, text mining and
            # prediction; PubChem rows have no score. Neither says which a pair rests on.
            yield _rel("ingredient_target", "tmmc2", compound_id(r["ID"]), None,
                       f"ensembl:{r['PROTEINID']}", _names(r["PREFERRED_NAME"]), "aggregated",
                       score=r["SCORE"] if _v(r["SOURCE"]) == "STITCH" else None,
                       note=f"via {r['SOURCE']}")
    if _has(conn, "protein_disease"):
        for r in _rows(conn, "SELECT * FROM protein_disease"):
            yield _rel("target_disease", "tmmc2", f"ensembl:{r['PROTEIN_ID']}",
                       _names(r["PREFERRED_NAME"]), f"umls:{r['DISEASEID']}", r["DISEASENAME"],
                       "aggregated", score=r["SCORE"], note="via DisGeNET v7.0")
    if _has(conn, "prescription"):
        for r in _rows(conn, "SELECT * FROM prescription"):
            latin = _v(r["LATIN"])
            yield _rel("formula_herb", "tmmc2", f"tmmc:prescription.{_v(r['HANJA'])}",
                       _names(r["CHINESE"], r["PINYIN"], r["HANJA"], r["ENGLISH"],
                              r["KOREAN"]),
                       f"tmmc:material.{latin}", material.get(latin, latin), "listed",
                       reference=_names(r["TEXTBOOK"], r["PAGE"]),
                       note=_names(r["DOSAGE"], r["UNIT"], r["PROCESS"]))


# ------------------------------------------------------------------------------- TCMIO
def _tcmio(conn: sqlite3.Connection) -> Iterator[Row | None]:
    herb = ({r["id"]: (f"tcmio:herb.{r['id']}",
                       _names(r["chinese_name"], r["pinyin_name"], r["english_name"]))
             for r in _rows(conn, "SELECT * FROM herb")} if _has(conn, "herb") else {})
    ing = ({r["id"]: (f"inchikey:{r['inchikey']}" if _v(r["inchikey"])
                      else f"tcmio:ingredient.{r['id']}", _names(r["name"]))
            for r in _rows(conn, "SELECT * FROM ingredient")} if _has(conn, "ingredient")
           else {})
    if _has(conn, "herb_ingredient"):
        for r in _rows(conn, "SELECT * FROM herb_ingredient"):
            a, b = herb.get(r["tcm_id"]), ing.get(r["ingredient_id"])
            if a and b:
                yield _rel("herb_ingredient", "tcmio", a[0], a[1], b[0], b[1], "aggregated")
    if _has(conn, "ingredient_target"):
        # The relation file names a target by its TCMIO id, and target.xlsx has no id
        # column: the id is the target's row number (1-based, under the header). That was
        # confirmed on 2026-10-02 against the site's own JSON (scripts/verify_tcmio_targets.py):
        # 22 of 22 sampled /targets/<id>/json records matched the row's gene and UniProt
        # accession, and for 16 of 16 sampled ingredients /ingredients/<id>/targets returned
        # exactly the targets the relation file lists. The store keeps rows in file order,
        # so a row's SQLite rowid is that number. If the relation file names an id beyond
        # the table, the numbering is not the one that was verified (a different release),
        # and no target is mapped: they stay TCMIO ids.
        target: dict[str, tuple[str, str | None]] = {}
        if _has(conn, "target"):
            rows = list(_rows(conn, "SELECT rowid AS _row, * FROM target"))
            used = [int(x[0]) for x in conn.execute("SELECT target_id FROM ingredient_target")
                    if str(x[0] or "").isdigit()]
            if used and max(used) <= len(rows):
                for r in rows:
                    acc, gene = _v(r["Uniprot_id"]), _v(r["Gene_name"])
                    tid = f"uniprot:{acc}" if acc else (f"symbol:{gene}" if gene
                                                       else f"tcmio:target.{r['_row']}")
                    target[str(r["_row"])] = (tid, _names(gene, r["Target_name"]))
        for r in _rows(conn, "SELECT * FROM ingredient_target"):
            a = ing.get(r["ingredient_id"])
            if a:
                evidence = "predicted" if "predict" in (_v(r["type"]) or "").lower() \
                    else "aggregated"
                tid, tname = target.get(str(_v(r["target_id"])),
                                        (f"tcmio:target.{r['target_id']}", None))
                yield _rel("ingredient_target", "tcmio", a[0], a[1], tid, tname, evidence,
                           note=_names(r["type"], f"tcmio:target.{r['target_id']}"))
    if _has(conn, "prescription_herb"):
        names = {(_v(r["chinese_name"]) or ""): r["id"]
                 for r in _rows(conn, "SELECT * FROM herb")} if _has(conn, "herb") else {}
        for r in _rows(conn, "SELECT * FROM prescription_herb"):
            pres, tcm = _v(r["pres_name"]), _v(r["tcm_name"])
            hid = herb.get(names.get(tcm or ""))
            yield _rel("formula_herb", "tcmio", f"tcmio:prescription.{pres}", pres,
                       hid[0] if hid else f"tcmio:herb_name.{tcm}",
                       hid[1] if hid else tcm, "listed", note=r["quantity"])


# -------------------------------------------------------------------------------- DDID
def _ddid(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not _has(conn, "interaction"):
        return
    for r in _rows(conn, "SELECT * FROM interaction"):
        if (_v(r["Type"]) or "").lower() != "herb":
            continue                              # food-drug pairs stay in the table
        pmid = _v(r["PMID"])
        yield _rel("herb_drug_interaction", "ddid", f"ddid:{r['Food_Herb_ID']}",
                   r["Food_Herb_Name"], f"ddid:{r['Drug_ID']}", r["Drug_Name"], "known",
                   reference=f"pmid:{pmid}" if pmid and pmid.isdigit() else _v(r["DOI"]),
                   note=_names(r["Effect"], r["Result"], r["Potential_Target"]))


# ------------------------------------------------------------------------------- dbPTH
def _dbpth(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not _has(conn, "human_iti"):
        return
    for r in _rows(conn, "SELECT * FROM human_iti"):
        acc, gene = _v(r["Protein_Accession"]), _v(r["Gene"])
        target = f"uniprot:{acc}" if acc else (f"symbol:{gene}" if gene else None)
        yield _rel("ingredient_target", "dbpth", _cid(r["PubChem"]), r["Ingredient"], target,
                   _names(gene, r["Protein_Name"]), "known", note=r["dbPTH_ID"])


# --------------------------------------------------------------------------- BATMAN 1.0
_GENE_SRC = re.compile(r"^(\d+)\(([^)]*)\)$")


def _batman1(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not _has(conn, "known_dti"):
        return
    for r in _rows(conn, "SELECT * FROM known_dti"):
        for item in (_v(r["Targets_geneid"]) or "").split("|"):
            m = _GENE_SRC.match(item.strip())
            if m:
                yield _rel("ingredient_target", "batman1", _cid(r["Pubchem_CID"]), None,
                           f"ncbigene:{m.group(1)}", None, "known", note=f"via {m.group(2)}")


# --------------------------------------------------------------------------------- TTD
def _ttd(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not _has(conn, "drug_target"):
        return
    target: dict[str, dict[str, str]] = {}
    if _has(conn, "target"):
        for r in _rows(conn, "SELECT ttd_id, field, value FROM target WHERE field IN "
                             "('GENENAME', 'TARGNAME', 'UNIPROID')"):
            target.setdefault(r["ttd_id"], {})[r["field"]] = r["value"]
    drug: dict[str, list[str]] = {}
    for table, fields in (("drug", ("TRADNAME",)), ("drug_disease", ("DRUGNAME",))):
        if _has(conn, table):
            for r in _rows(conn, f"SELECT ttd_id, field, value FROM {table} WHERE field IN "
                                 f"({', '.join(repr(f) for f in fields)})"):
                drug.setdefault(r["ttd_id"], []).append(r["value"])
    for r in _rows(conn, "SELECT * FROM drug_target"):
        t = target.get(r["TargetID"] or "", {})
        gene = _v(t.get("GENENAME"))
        tid = f"symbol:{gene}" if gene and " " not in gene and ";" not in gene \
            else f"ttd:{r['TargetID']}"
        yield _rel("drug_target", "ttd", f"ttd:{r['DrugID']}",
                   _names(*drug.get(r["DrugID"] or "", [])), tid,
                   _names(gene, t.get("TARGNAME"), r["TargetID"]), "known",
                   note=_names(r["Highest_status"], r["MOA"]))


# ----------------------------------------------------------------------------- ImmPort
def _immport(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not _has(conn, "gene_set_member"):
        return
    for r in _rows(conn, "SELECT * FROM gene_set_member"):
        link = _v(r["description"]) or ""
        set_id = link.rstrip("/").rsplit("/", 1)[-1] if link.startswith("http") else None
        yield _rel("gene_set_member", "immport", f"immport:{set_id or r['gene_set']}",
                   r["gene_set"], f"symbol:{r['gene']}", r["gene"], "listed", reference=link)


# ------------------------------------------------------------------------------ nSIDES
def _nsides(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if not _has(conn, "offsides"):
        return
    for r in _rows(conn, "SELECT * FROM offsides"):
        # the file's header spells it drug_rxnorn_id; the README says drug_rxnorm_id
        rxnorm = _col(r, "drug_rxnorn_id", "drug_rxnorm_id")
        yield _rel("drug_adverse_event", "nsides", f"rxnorm:{rxnorm}" if rxnorm else None,
                   r["drug_concept_name"], f"meddra:{r['condition_meddra_id']}",
                   r["condition_concept_name"], "signal", score=r["PRR"],
                   note=f"A={r['A']} B={r['B']} C={r['C']} D={r['D']}")


# --------------------------------------------------------------------- TCMSP (manual)
def _tcmsp_export(conn: sqlite3.Connection) -> Iterator[Row | None]:
    if _has(conn, "ingredient"):
        for r in _rows(conn, "SELECT * FROM ingredient"):
            herb, mol = _v(_col(r, "herb")), _v(_col(r, "MOL_ID"))
            yield _rel("herb_ingredient", "tcmsp", f"tcmsp:herb.{herb}", herb,
                       f"tcmsp:{mol}" if mol else None,
                       _col(r, "Molecule_name", "molecule_name"), "aggregated",
                       note=_names(*(f"{k}={_col(r, k)}" for k in ("OB", "DL")
                                     if _v(_col(r, k)))))
    if _has(conn, "target"):
        for r in _rows(conn, "SELECT * FROM target"):
            mol, name = _v(_col(r, "MOL_ID")), _v(_col(r, "target_name"))
            yield _rel("ingredient_target", "tcmsp", f"tcmsp:{mol}" if mol else None,
                       _col(r, "molecule_name"), f"tcmsp:target.{name}" if name else None,
                       name, "aggregated", note=_col(r, "herb"))


EXTRACTORS: dict[str, Callable[[sqlite3.Connection], Iterable[Row | None]]] = {
    "itcm": _itcm,
    "batman2": _batman2,
    "herb2": _herb2,
    "tmmc2": _tmmc2,
    "tcmio": _tcmio,
    "ddid": _ddid,
    "dbpth": _dbpth,
    "batman1": _batman1,
    "ttd": _ttd,
    "immport": _immport,
    "nsides": _nsides,
    "tcmsp_export": _tcmsp_export,
}

_COLUMNS = ("kind", "source", "subject_type", "subject_id", "subject_name", "object_type",
            "object_id", "object_name", "evidence", "score", "reference", "note")


def build_relations(conn: sqlite3.Connection, dataset_key: str) -> dict[str, int]:
    """(Re)write the ``relations`` table of one built store; counts per kind."""
    conn.execute("DROP TABLE IF EXISTS relations")
    conn.execute(f"CREATE TABLE relations ({', '.join(c + ' TEXT' for c in _COLUMNS)})")
    extractor = EXTRACTORS.get(dataset_key)
    counts: dict[str, int] = {}
    if extractor is None:
        conn.commit()
        return counts
    batch: list[tuple] = []
    insert = f"INSERT INTO relations VALUES ({', '.join('?' * len(_COLUMNS))})"
    seen: set[tuple] = set()
    for row in extractor(conn):
        if row is None:
            continue
        if row["evidence"] not in EVIDENCE:                # pragma: no cover - programming
            raise ValueError(f"{dataset_key}: evidence {row['evidence']!r}")
        # one row per pair, evidence and reference: two papers on the same pair are two rows
        key = (row["kind"], row["subject_id"], row["object_id"], row["evidence"],
               row["reference"])
        if key in seen:
            continue
        seen.add(key)
        batch.append(tuple(row[c] for c in _COLUMNS))
        counts[row["kind"]] = counts.get(row["kind"], 0) + 1
        if len(batch) >= 5000:
            conn.executemany(insert, batch)
            batch.clear()
    if batch:
        conn.executemany(insert, batch)
    for col in ("kind", "subject_id", "object_id"):
        conn.execute(f"CREATE INDEX IF NOT EXISTS relations_{col} ON relations({col})")
    conn.commit()
    return counts

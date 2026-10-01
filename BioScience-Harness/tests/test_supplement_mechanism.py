"""Mechanism sources (BioGRID, BioGRID ORCS, IntAct/Complex Portal, SIGNOR, JASPAR), offline.

Each dataset is built from tiny files written here in the source's real format (headers
copied from the files fetched on 2026-10-01, rows made up in their shape), and each
connector is checked to render the request that was verified live.
"""

from __future__ import annotations

import gzip
import io
import json
import sqlite3
import tarfile
import zipfile
from urllib.parse import urlsplit

import pytest

from bioagent.backends.http import DEFAULT_RATES
from bioagent.policy import PROFILES
from bioagent.providers.public_apis import BY_KEY, render_call
from bioagent.providers.supplement import mechanism as live
from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.datasets import dataset
from bioagent.tcmdb.rowkit import RELATION_KINDS
from bioagent.tcmdb.spec import allows_commercial

pytestmark = pytest.mark.unit

LOG = {"log": lambda m: None}


def _rels(h, key, kind=None):
    with sqlite3.connect(h.db_path(key)) as conn:
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM relations" + (" WHERE kind = ?" if kind else "")
        out = [dict(r) for r in conn.execute(sql, (kind,) if kind else ())]
    for r in out:
        r["ctx"] = json.loads(r["context"]) if r["context"] else {}
    return out


def _zip(path, member, text):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(member, text)


# ------------------------------------------------------------------------------ BioGRID
TAB3 = ("#BioGRID Interaction ID", "Entrez Gene Interactor A", "Entrez Gene Interactor B",
        "BioGRID ID Interactor A", "BioGRID ID Interactor B", "Systematic Name Interactor A",
        "Systematic Name Interactor B", "Official Symbol Interactor A",
        "Official Symbol Interactor B", "Synonyms Interactor A", "Synonyms Interactor B",
        "Experimental System", "Experimental System Type", "Author", "Publication Source",
        "Organism ID Interactor A", "Organism ID Interactor B", "Throughput", "Score",
        "Modification", "Qualifications", "Tags", "Source Database",
        "SWISS-PROT Accessions Interactor A", "TREMBL Accessions Interactor A",
        "REFSEQ Accessions Interactor A", "SWISS-PROT Accessions Interactor B",
        "TREMBL Accessions Interactor B", "REFSEQ Accessions Interactor B",
        "Ontology Term IDs", "Ontology Term Names", "Ontology Term Categories",
        "Ontology Term Qualifier IDs", "Ontology Term Qualifier Names", "Ontology Term Types",
        "Organism Name Interactor A", "Organism Name Interactor B")

CHEMTAB = ("#BioGRID Chemical Interaction ID", "BioGRID Gene ID", "Entrez Gene ID",
           "Systematic Name", "Official Symbol", "Synonyms", "Organism ID", "Organism",
           "Action", "Interaction Type", "Author", "Pubmed ID", "BioGRID Publication ID",
           "BioGRID Chemical ID", "Chemical Name", "Chemical Synonyms", "Chemical Brands",
           "Chemical Source", "Chemical Source ID", "Molecular Formula", "Chemical Type",
           "ATC Codes", "CAS Number", "Curated By", "Method", "Method Description",
           "Related BioGRID Gene ID", "Related Entrez Gene ID", "Related Systematic Name",
           "Related Official Symbol", "Related Synonyms", "Related Organism ID",
           "Related Organism", "Related Type", "Notes", "InChIKey")


def _table(header, rows):
    lines = ["\t".join(header)]
    for row in rows:
        lines.append("\t".join(str(row.get(h, "-")) for h in header))
    return "\n".join(lines) + "\n"


def _pair(iid, a, b, sa, sb, system="Two-hybrid", kind="physical", pub="PUBMED:9006895",
          **extra):
    row = {"#BioGRID Interaction ID": iid, "Entrez Gene Interactor A": a,
           "Entrez Gene Interactor B": b, "BioGRID ID Interactor A": f"1{iid}",
           "BioGRID ID Interactor B": f"2{iid}", "Official Symbol Interactor A": sa,
           "Official Symbol Interactor B": sb, "Experimental System": system,
           "Experimental System Type": kind, "Publication Source": pub,
           "Organism ID Interactor A": "9606", "Organism ID Interactor B": "9606",
           "Throughput": "Low Throughput", "Source Database": "BIOGRID"}
    row.update(extra)
    return row


def _chem(cid, gene, sym, action, *, source="DRUGBANK", source_id="DB00001",
          curated="DRUGBANK", inchikey="-", itype="target", **extra):
    row = {"#BioGRID Chemical Interaction ID": cid, "BioGRID Gene ID": "108447",
           "Entrez Gene ID": gene, "Official Symbol": sym, "Organism ID": "9606",
           "Action": action, "Interaction Type": itype, "Pubmed ID": "11055889",
           "BioGRID Chemical ID": f"9{cid}", "Chemical Name": "Lepirudin",
           "Chemical Source": source, "Chemical Source ID": source_id,
           "Curated By": curated, "InChIKey": inchikey}
    row.update(extra)
    return row


MV_ROWS = [
    _pair(103, 6416, 2318, "MAP2K4", "FLNC",
          **{"SWISS-PROT Accessions Interactor A": "P45985",
             "SWISS-PROT Accessions Interactor B": "Q14315"}),
    # the same pair, paper and system under a second BioGRID id: one row
    _pair(104, 6416, 2318, "MAP2K4", "FLNC"),
    _pair(117, "-", 88, "nsp7", "ACTN2", system="Affinity Capture-MS", pub="DOI:10.1/x",
          Score="2.5", **{"Organism ID Interactor A": "2697049",
                          "Throughput": "High Throughput"}),
    _pair(200, 31221, 42446, "Raf", "Pi3K92E", system="Biochemical Activity",
          Modification="Phosphorylation", **{"Organism ID Interactor A": "7227",
                                             "Organism ID Interactor B": "7227",
                                             "Source Database": "FLYBASE"}),
]
CHEM_ROWS = [
    _chem(1, 2147, "F2", "inhibitor"),
    _chem(2, 2147, "F2", "unknown", source="PUBCHEM", source_id="122187344",
          curated="BINDINGDB", inchikey="GVRGDWHECZKHIP-CINJXCJGSA-N"),
    _chem(3, 329, "BIRC2", "degradation", source="BIOGRID", source_id="-", curated="BIOGRID",
          itype="recruited E3 ligase", Method="SNIPER",
          **{"Related Entrez Gene ID": "367", "Related BioGRID Gene ID": "106862",
             "Related Official Symbol": "AR", "Related Organism ID": "9606",
             "Related Type": "target"}),
    _chem(4, 1956, "EGFR", "inhibitor/sars-cov-2 inhibitor", source_id="DB00002"),
    _chem(5, 1956, "EGFR", "sars-cov-2 inhibitor", source_id="DB00003",
          inchikey="BLCLNMBMMGCOAS-URPVMXJPSA-N"),
]


@pytest.fixture
def biogrid(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("biogrid")
    raw.mkdir(parents=True)
    _zip(raw / "BIOGRID-MV-Physical-5.0.262.tab3.zip", "BIOGRID-MV-Physical-5.0.262.tab3.txt",
         _table(TAB3, MV_ROWS))
    _zip(raw / "BIOGRID-CHEMICALS-5.0.262.chemtab.zip",
         "BIOGRID-CHEMICALS-5.0.262.chemtab.txt", _table(CHEMTAB, CHEM_ROWS))
    report = h.build("biogrid", **LOG)
    return h, report


def test_biogrid_interactions_are_curated_rows_with_their_system_and_paper(biogrid):
    h, report = biogrid
    rows = {r["note"].split(";")[0]: r for r in _rels(h, "biogrid", "protein_interaction")}
    assert report["relations"]["protein_interaction"] == 3       # 103 and 104 are one row
    first = rows["BioGRID interaction 103"]
    assert (first["subject_id"], first["object_id"]) == ("ncbigene:6416", "ncbigene:2318")
    assert first["evidence"] == "known" and first["effect"] is None
    assert first["reference"] == "pmid:9006895"
    assert first["ctx"] == {"method": "Two-hybrid", "species": "9606",
                            "flags": "low throughput|multi-validated"}
    viral = rows["BioGRID interaction 117"]
    assert viral["subject_id"] == "biogrid:1117"          # no Entrez id: BioGRID's own
    assert viral["reference"] == "doi:10.1/x" and viral["score"] == "2.5"
    assert viral["ctx"]["species"] == "2697049|9606"
    fly = rows["BioGRID interaction 200"]
    assert fly["note"] == "BioGRID interaction 200; via FlyBase"
    assert fly["ctx"]["mechanism"] == "Phosphorylation"
    assert first["license"].startswith("MIT")


def test_biogrid_chemical_actions_become_effects_only_when_they_name_one(biogrid):
    h, _ = biogrid
    drug = {r["note"].split(";")[0]: r for r in _rels(h, "biogrid", "drug_target")}
    lepirudin = drug["BioGRID chemical interaction 1"]
    assert lepirudin["subject_id"] == "drugbank:DB00001"
    assert lepirudin["object_id"] == "ncbigene:2147"
    assert lepirudin["effect"] == "inhibition" and lepirudin["ctx"]["action"] == "inhibitor"
    assert lepirudin["note"].endswith("via DrugBank")
    # the second word names an antiviral activity, not an action on the target
    assert drug["BioGRID chemical interaction 4"]["effect"] == "inhibition"
    assert drug["BioGRID chemical interaction 5"]["effect"] is None
    assert drug["BioGRID chemical interaction 5"]["ctx"]["action"] == "sars-cov-2 inhibitor"
    compound = {r["note"].split(";")[0]: r for r in _rels(h, "biogrid", "compound_target")}
    bindingdb = compound["BioGRID chemical interaction 2"]
    assert bindingdb["subject_id"] == "inchikey:GVRGDWHECZKHIP-CINJXCJGSA-N"
    assert bindingdb["effect"] is None and bindingdb["note"].endswith("via BindingDB")


def test_a_degrader_degrades_the_related_gene_and_only_recruits_the_ligase(biogrid):
    h, _ = biogrid
    rows = {r["object_id"]: r for r in _rels(h, "biogrid", "compound_target")
            if "interaction 3;" in r["note"]}
    assert set(rows) == {"ncbigene:367", "ncbigene:329"}
    assert rows["ncbigene:367"]["effect"] == "degradation"            # AR is degraded
    assert rows["ncbigene:367"]["ctx"]["method"] == "SNIPER"
    assert rows["ncbigene:329"]["effect"] is None                     # BIRC2 is recruited
    assert rows["ncbigene:329"]["ctx"]["action"] == "recruited e3 ligase"
    assert rows["ncbigene:367"]["subject_id"] == "biogrid:chemical.93"


def test_drugbank_rows_keep_drugbanks_non_commercial_licence(biogrid):
    h, _ = biogrid
    licences = {r["note"].split(";")[0]: r["license"] for r in _rels(h, "biogrid")}
    assert not allows_commercial(licences["BioGRID chemical interaction 1"])
    assert allows_commercial(licences["BioGRID chemical interaction 2"])
    assert allows_commercial(licences["BioGRID interaction 103"])
    commercial = {r["note"].split(";")[0] for r in h.relations(commercial=True, limit=100)}
    assert "BioGRID chemical interaction 1" not in commercial
    assert "BioGRID interaction 103" in commercial


def test_biogrid_declares_human_gene_and_drug_structure_mappings(biogrid):
    from bioagent.tcmdb.consensus import Crosswalk
    h, _ = biogrid
    cw = Crosswalk(h)
    assert cw.gene["ncbigene:6416"] == "symbol:MAP2K4"
    assert cw.gene["uniprot:Q14315"] == "symbol:FLNC"
    assert "ncbigene:31221" not in cw.gene                     # a fly gene is not human
    assert cw.compound["drugbank:DB00003"] == "inchikey:BLCLNMBMMGCOAS-URPVMXJPSA-N"


def test_the_complete_file_adds_genetic_interactions_and_flags_validated_pairs(biogrid):
    h, _ = biogrid
    raw = h.raw_dir("biogrid")
    rows = MV_ROWS + [_pair(300, 1, 2, "A1BG", "A2M", system="Synthetic Lethality",
                            kind="genetic")]
    _zip(raw / "BIOGRID-ALL-5.0.262.tab3.zip", "BIOGRID-ALL-5.0.262.tab3.txt",
         _table(TAB3, rows))
    h.build("biogrid", **LOG)
    genetic = _rels(h, "biogrid", "genetic_interaction")
    assert len(genetic) == 1 and genetic[0]["ctx"]["method"] == "Synthetic Lethality"
    assert "multi-validated" not in genetic[0]["ctx"]["flags"]
    physical = _rels(h, "biogrid", "protein_interaction")
    assert all("multi-validated" in r["ctx"]["flags"] for r in physical)


# --------------------------------------------------------------------------------- ORCS
ORCS_INDEX = ("#SCREEN_ID", "SOURCE_ID", "SOURCE_TYPE", "AUTHOR", "SCREEN_NAME", "SCORES_SIZE",
              "FULL_SIZE", "FULL_SIZE_AVAILABLE", "NUMBER_OF_HITS", "ANALYSIS",
              "SIGNIFICANCE_INDICATOR", "SIGNIFICANCE_CRITERIA", "THROUGHPUT", "SCREEN_TYPE",
              "SCREEN_FORMAT", "EXPERIMENTAL_SETUP", "DURATION", "CONDITION_NAME",
              "CONDITION_DOSAGE", "MOI", "LIBRARY", "LIBRARY_TYPE", "LIBRARY_METHODOLOGY",
              "ENZYME", "CELL_LINE", "CELL_TYPE", "PHENOTYPE", "SCORE_COL_COUNT",
              "SCORE.1_TYPE", "SCORE.2_TYPE", "SCORE.3_TYPE", "SCORE.4_TYPE", "SCORE.5_TYPE",
              "ORGANISM_ID", "ORGANISM_OFFICIAL", "NOTES", "SOURCE", "SCREEN_RATIONALE")
ORCS_SCORES = ("#SCREEN_ID", "IDENTIFIER_ID", "IDENTIFIER_TYPE", "OFFICIAL_SYMBOL", "ALIASES",
               "ORGANISM_ID", "ORGANISM_OFFICIAL", "SCORE.1", "SCORE.2", "SCORE.3", "SCORE.4",
               "SCORE.5", "HIT", "SOURCE")


def _screen(sid, condition="-", dose="-"):
    return {"#SCREEN_ID": sid, "SOURCE_ID": "30051818", "SOURCE_TYPE": "pubmed",
            "SCREEN_NAME": f"{sid}-PMID30051818", "ANALYSIS": "MAGeCK-MLE",
            "SIGNIFICANCE_CRITERIA": "Score.1 (Z-score) < -3.99",
            "SCREEN_TYPE": "Negative Selection", "DURATION": "30 Days",
            "CONDITION_NAME": condition, "CONDITION_DOSAGE": dose,
            "LIBRARY": "CRISPRn (Perrimon, 2018)", "ENZYME": "Cas9", "CELL_LINE": "S2R+",
            "PHENOTYPE": "response to chemicals", "SCORE.1_TYPE": "Z-score",
            "ORGANISM_ID": "7227"}


def _gene(sid, ident, kind, symbol, score, hit):
    return {"#SCREEN_ID": sid, "IDENTIFIER_ID": ident, "IDENTIFIER_TYPE": kind,
            "OFFICIAL_SYMBOL": symbol, "ALIASES": "CG4141|DP110|" + "x" * 200,
            "ORGANISM_ID": "7227", "ORGANISM_OFFICIAL": "Drosophila melanogaster",
            "SCORE.1": score, "HIT": hit, "SOURCE": "BioGRID ORCS"}


def _tar(path, members):
    with tarfile.open(path, "w:gz") as tar:
        for name, text in members:
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


@pytest.fixture
def orcs(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("biogrid_orcs")
    raw.mkdir(parents=True)
    _tar(raw / "BIOGRID-ORCS-ALL-drosophila_melanogaster-2.0.18.screens.tar.gz", [
        ("BIOGRID-ORCS-SCREEN_INDEX-2.0.18.index.tab.txt",
         _table(ORCS_INDEX, [_screen("634", "Trametinib", "50.0 nM"), _screen("636")])),
        ("BIOGRID-ORCS-SCREEN_634-2.0.18.screen.tab.txt", _table(ORCS_SCORES, [
            _gene("634", "42446", "ENTREZ_GENE", "Pi3K92E", "-22.897", "YES"),
            _gene("634", "36775", "ENTREZ_GENE", "Rho1", "-0.5", "NO"),
            _gene("634", "2320U", "UNKNOWN", "FBgn0003887", "-16.6", "YES"),
            _gene("634", "2360U", "UNKNOWN", "#N/A", "-1.0", "NO")])),
        ("BIOGRID-ORCS-SCREEN_636-2.0.18.screen.tab.txt", _table(ORCS_SCORES, [
            _gene("636", "42446", "ENTREZ_GENE", "Pi3K92E", "-", "NO")])),
    ])
    report = h.build("biogrid_orcs", **LOG)
    return h, report


def test_a_screen_gives_hits_and_tested_non_hits_with_its_conditions(orcs):
    h, report = orcs
    rows = {(r["subject_id"], r["object_id"]): r for r in _rels(h, "biogrid_orcs")}
    hit = rows[("biogrid_orcs:screen.634", "ncbigene:42446")]
    assert hit["outcome"] == "positive" and hit["evidence"] == "known"
    assert hit["score"] == "-22.897" and hit["reference"] == "pmid:30051818"
    assert hit["ctx"] == {"screen": "634", "cell": "S2R+", "species": "7227",
                          "condition": "Trametinib", "dose": "50.0 nM", "time": "30 Days",
                          "library": "CRISPRn (Perrimon, 2018)",
                          "phenotype": "response to chemicals", "method": "MAGeCK-MLE",
                          "assay": "Negative Selection",
                          "qc": "Score.1 (Z-score) < -3.99", "measure": "Z-score"}
    assert "S2R+: response to chemicals (Trametinib 50.0 nM)" in hit["subject_name"]
    assert rows[("biogrid_orcs:screen.634", "ncbigene:36775")]["outcome"] == "negative"
    # a '-' score is no score, and the gene was still scored and not called
    other = rows[("biogrid_orcs:screen.636", "ncbigene:42446")]
    assert other["score"] is None and other["outcome"] == "negative"
    # an UNKNOWN identifier that is a FlyBase id is kept as one; '#N/A' waits in the queue
    assert rows[("biogrid_orcs:screen.634", "flybase:FBgn0003887")]["outcome"] == "positive"
    queue = h.unresolved("biogrid_orcs")
    assert [q["object_name"] for q in queue] == ["#N/A"]
    assert report["tables"] == {"screens_drosophila_melanogaster": 2,
                                "scores_drosophila_melanogaster": 5}


def test_orcs_scores_leave_the_alias_lists_out(orcs):
    h, _ = orcs
    assert "ALIASES" not in h.tables("biogrid_orcs")["scores_drosophila_melanogaster"]


def test_only_the_tiny_orcs_archive_is_fetched_by_default():
    spec = dataset("biogrid_orcs")
    default = {f.name for f in spec.files if not f.optional}
    assert default == {"BIOGRID-ORCS-ALL-drosophila_melanogaster-2.0.18.screens.tar.gz"}
    human = [f for f in spec.files if "homo_sapiens" in f.name]
    assert human and all(f.optional and f.expected_bytes > 500e6 for f in human)


# ------------------------------------------------------------------ IntAct / Complex Portal
COMPLEXTAB = ("#Complex ac", "Recommended name", "Aliases for complex", "Taxonomy identifier",
              "Identifiers (and stoichiometry) of molecules in complex", "Evidence Code",
              "Experimental evidence", "Go Annotations", "Cross references", "Description",
              "Complex properties", "Complex assembly", "Ligand", "Disease", "Agonist",
              "Antagonist", "Comment", "Source", "Expanded participant list")
MITAB27 = ("#ID(s) interactor A", "ID(s) interactor B", "Alt. ID(s) interactor A",
           "Alt. ID(s) interactor B", "Alias(es) interactor A", "Alias(es) interactor B",
           "Interaction detection method(s)", "Publication 1st author(s)",
           "Publication Identifier(s)", "Taxid interactor A", "Taxid interactor B",
           "Interaction type(s)", "Source database(s)", "Interaction identifier(s)",
           "Confidence value(s)", "Expansion method(s)", "Biological role(s) interactor A",
           "Biological role(s) interactor B", "Experimental role(s) interactor A",
           "Experimental role(s) interactor B", "Type(s) interactor A", "Type(s) interactor B",
           "Xref(s) interactor A", "Xref(s) interactor B", "Interaction Xref(s)",
           "Annotation(s) interactor A", "Annotation(s) interactor B",
           "Interaction annotation(s)", "Host organism(s)", "Interaction parameter(s)",
           "Creation date", "Update date", "Checksum(s) interactor A",
           "Checksum(s) interactor B", "Interaction Checksum(s)", "Negative",
           "Feature(s) interactor A", "Feature(s) interactor B", "Stoichiometry(s) interactor A",
           "Stoichiometry(s) interactor B", "Identification method participant A",
           "Identification method participant B")
PROTEIN = 'psi-mi:"MI:0326"(protein)'
SMALL = 'psi-mi:"MI:0328"(small molecule)'


def _negative(a, b, ta=PROTEIN, tb=PROTEIN, *, itype='psi-mi:"MI:0915"(physical association)',
              db='psi-mi:"MI:0469"(IntAct)', ident="intact:EBI-1"):
    return {"#ID(s) interactor A": a, "ID(s) interactor B": b,
            "Alias(es) interactor A": "psi-mi:dlrb1_human(display_long)|"
                                      "uniprotkb:DYNLRB1(gene name)",
            "Alias(es) interactor B": "psi-mi:Bcl2l11(display_short)",
            "Interaction detection method(s)": 'psi-mi:"MI:0018"(two hybrid)',
            "Publication Identifier(s)": "pubmed:10198631|mint:MINT-5211354",
            "Taxid interactor A": "taxid:9606(human)|taxid:9606(Homo sapiens)",
            "Taxid interactor B": "taxid:10090(mouse)|taxid:10090(Mus musculus)",
            "Interaction type(s)": itype, "Source database(s)": db,
            "Interaction identifier(s)": ident, "Confidence value(s)": "intact-miscore:0",
            "Type(s) interactor A": ta, "Type(s) interactor B": tb, "Negative": "true"}


@pytest.fixture
def intact(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("intact")
    raw.mkdir(parents=True)
    (raw / "complextab_9606.tsv").write_text(_table(COMPLEXTAB, [
        {"#Complex ac": "CPX-1", "Recommended name": "SMAD2-SMAD3-SMAD4 complex",
         "Taxonomy identifier": "9606",
         "Identifiers (and stoichiometry) of molecules in complex":
             "P84022(1)|Q13485(0)|CHEBI:15422(2)|CPX-2(1)",
         "Evidence Code": "ECO:0000353(physical interaction evidence used in manual "
                          "assertion)",
         "Ligand": "Chloride ion (CHEBI:15422)|ATP (CHEBI:15422)"}]), encoding="utf-8")
    (raw / "complextab_10090.tsv").write_text(_table(COMPLEXTAB, []), encoding="utf-8")
    (raw / "intact_negative.txt").write_text(_table(MITAB27, [
        _negative("uniprotkb:Q9NP97", "uniprotkb:O54918-3",
                  db='psi-mi:"MI:0471"(MINT)', ident="intact:EBI-526131"),
        _negative("uniprotkb:P22309-2", "chebi:\"CHEBI:16243\"", tb=SMALL,
                  itype='psi-mi:"MI:0559"(glycosylation reaction)',
                  ident="intact:EBI-48419970")]), encoding="utf-8")
    report = h.build("intact", **LOG)
    return h, report


def test_complex_members_keep_stoichiometry_and_member_types(intact):
    h, _ = intact
    rows = {r["object_id"]: r for r in _rels(h, "intact", "complex_member")}
    assert set(rows) == {"uniprot:P84022", "uniprot:Q13485", "chebi:15422",
                         "complexportal:CPX-2"}
    assert {r["subject_id"] for r in rows.values()} == {"complexportal:CPX-1"}
    assert rows["uniprot:P84022"]["note"] == "stoichiometry 1"
    assert rows["uniprot:Q13485"]["note"] is None                 # 0 means unknown
    assert rows["chebi:15422"]["object_type"] == "compound"
    assert rows["complexportal:CPX-2"]["object_type"] == "complex"
    assert rows["uniprot:P84022"]["ctx"] == {"species": "9606", "confidence": "ECO:0000353"}
    assert rows["uniprot:P84022"]["evidence"] == "listed"
    assert rows["uniprot:P84022"]["license"] == "CC0 1.0 (Complex Portal)"
    # the free-text Ligand column (here with a wrong id for chloride) is not read
    assert {r["source"] for r in _rels(h, "intact", "compound_target")} == {"intact"}


def test_negative_interactions_are_tested_and_not_found_without_a_zero_score(intact):
    h, _ = intact
    (ppi,) = _rels(h, "intact", "protein_interaction")
    assert ppi["outcome"] == "negative" and ppi["evidence"] == "known"
    assert (ppi["subject_id"], ppi["object_id"]) == ("uniprot:Q9NP97", "uniprot:O54918-3")
    assert ppi["subject_name"] == "DYNLRB1" and ppi["object_name"] == "Bcl2l11"
    assert ppi["score"] is None                       # IntAct does not score negatives
    assert ppi["reference"] == "pmid:10198631"
    assert ppi["note"] == "intact:EBI-526131; via MINT"
    assert ppi["ctx"] == {"method": "two hybrid", "mechanism": "physical association",
                          "species": "9606|10090"}
    (small,) = _rels(h, "intact", "compound_target")
    # the small molecule (interactor B in the file) is the subject
    assert (small["subject_id"], small["object_id"]) == ("chebi:16243", "uniprot:P22309-2")
    assert small["outcome"] == "negative" and small["license"] == "CC BY 4.0 (IntAct)"
    with sqlite3.connect(h.db_path("intact")) as conn:
        negatives = conn.execute("SELECT value FROM _tcmdb_build WHERE key = "
                                 "'sources_with_negatives'").fetchone()[0]
    assert json.loads(negatives) == ["intact"]


# -------------------------------------------------------------------------------- SIGNOR
SIGNOR = ("ENTITYA", "TYPEA", "IDA", "DATABASEA", "ENTITYB", "TYPEB", "IDB", "DATABASEB",
          "EFFECT", "MECHANISM", "RESIDUE", "SEQUENCE", "TAX_ID", "CELL_DATA", "TISSUE_DATA",
          "MODULATOR_COMPLEX", "TARGET_COMPLEX", "MODIFICATIONA", "MODASEQ", "MODIFICATIONB",
          "MODBSEQ", "PMID", "DIRECT", "NOTES", "ANNOTATOR", "SENTENCE", "SIGNOR_ID")


def _signor_line(a, ta, ida, dba, b, tb, idb, dbb, effect, mech, *, residue="", tax="9606",
                 cell="", pmid="27551952", direct="t", sid="SIGNOR-1"):
    def q(x):
        return f'"{x}"' if " " in x else x
    cells = [q(a), ta, ida, dba, q(b), tb, idb, dbb, q(effect), q(mech), residue, "", tax,
             cell, "", "", "", "", "", "", "", pmid, direct, "", "miannu",
             q("They were potent activators; see\ttab."), sid]
    return "\t".join(cells)


@pytest.fixture
def signor(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("signor")
    raw.mkdir(parents=True)
    lines = ["\t".join(SIGNOR),
             _signor_line("bis(2-ethylhexyl) phthalate", "chemical", "CHEBI:17747", "ChEBI",
                          "NR1I2", "protein", "O75469", "UNIPROT", "up-regulates activity",
                          "chemical activation", tax="9534", cell="BTO:0001538",
                          sid="SIGNOR-268774"),
             _signor_line("YWHAE", "protein", "P62258", "UNIPROT", "CDKN1B", "protein",
                          "P46527", "UNIPROT", "down-regulates", "binding",
                          sid="SIGNOR-88297"),
             _signor_line("TP53", "protein", "P04637", "UNIPROT", "MDM2", "protein", "Q00987",
                          "UNIPROT", "up-regulates quantity by expression",
                          "transcriptional regulation", direct="f", sid="SIGNOR-3"),
             _signor_line("AKT1", "protein", "P31749", "UNIPROT", "GSK3B", "protein",
                          "P49841", "UNIPROT", "down-regulates activity", "phosphorylation",
                          residue="Ser9", pmid="1|2|3", sid="SIGNOR-4"),
             _signor_line("R547", "chemical", "CID:6918852", "PUBCHEM", "CDK1", "protein",
                          "P06493", "UNIPROT", "unknown", "", tax="-1", pmid="Other",
                          sid="SIGNOR-5"),
             _signor_line("mTORC1", "complex", "SIGNOR-C3", "SIGNOR", "Proliferation",
                          "phenotype", "SIGNOR-PH4", "SIGNOR", "form complex", "",
                          sid="SIGNOR-6"),
             _signor_line("Infliximab", "antibody", "DB00065", "DRUGBANK", "TNF", "protein",
                          "P01375", "UNIPROT", "down-regulates activity", "binding",
                          sid="SIGNOR-7")]
    (raw / "SIGNOR_Oct2026_release.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # getComplexData drops trailing empty fields: rows of 3, 4 and 5 fields
    (raw / "SIGNOR_complexes.tsv").write_text(
        "SIGNOR_ID\tCOMPLEX_NAME\tMEMBERS\tCOMPLEX_PORTAL_ID\tDESCRIPTION\n"
        "SIGNOR-C1\tNFY\tP25208;P23511;Q13952\tCPX-1956\n"
        "SIGNOR-C10\tSMAD4/JUN\tQ13485;P05412\n"
        "SIGNOR-C102\tSUZ12/EZH2/YY1\tP25490;SIGNOR-C77;CHEBI:15422\t\tSUZ12/EZH2/YY1\n",
        encoding="utf-8")
    report = h.build("signor", **LOG)
    return h, report


def test_signor_effects_follow_the_curation_manual(signor):
    h, report = signor
    rows = {r["note"].split(" | ")[0]: r for r in _rels(h, "signor")
            if r["kind"] != "complex_member"}
    assert rows["SIGNOR-268774"]["effect"] == "activation"
    assert rows["SIGNOR-3"]["effect"] == "increase"
    assert rows["SIGNOR-4"]["effect"] == "inhibition"
    assert rows["SIGNOR-6"]["effect"] == "binding"
    assert rows["SIGNOR-5"]["effect"] is None                 # 'unknown'
    # bare 'down-regulates': a sign, but neither activity nor amount; the word stays
    generic = rows["SIGNOR-88297"]
    assert generic["effect"] == "modulation"
    assert generic["ctx"]["action"] == "down-regulates"


def test_signor_rows_carry_mechanism_residue_cell_and_directness(signor):
    h, _ = signor
    rows = {r["note"].split(" | ")[0]: r for r in _rels(h, "signor")
            if r["kind"] != "complex_member"}
    chem = rows["SIGNOR-268774"]
    assert chem["kind"] == "compound_target"
    assert (chem["subject_id"], chem["object_id"]) == ("chebi:17747", "uniprot:O75469")
    assert chem["subject_name"] == "bis(2-ethylhexyl) phthalate"     # quotes removed
    assert chem["ctx"] == {"action": "up-regulates activity", "mechanism": "chemical activation",
                           "species": "9534", "cell": "BTO:0001538", "direct": True}
    assert chem["reference"] == "pmid:27551952" and chem["evidence"] == "known"
    gsk = rows["SIGNOR-4"]
    assert gsk["kind"] == "regulation" and gsk["ctx"]["residue"] == "Ser9"
    assert gsk["reference"] == "pmid:1" and "also pmid:2, pmid:3" in gsk["note"]
    assert rows["SIGNOR-3"]["ctx"]["direct"] is False
    pubchem = rows["SIGNOR-5"]
    assert pubchem["subject_id"] == "pubchem:6918852"
    assert pubchem["reference"] is None and "species" not in pubchem["ctx"]   # -1, 'Other'
    pheno = rows["SIGNOR-6"]
    assert pheno["kind"] == "regulation"
    assert (pheno["subject_type"], pheno["object_type"]) == ("complex", "phenotype")
    assert pheno["object_id"] == "signor:SIGNOR-PH4"
    antibody = rows["SIGNOR-7"]
    assert antibody["kind"] == "drug_target" and antibody["subject_id"] == "drugbank:DB00065"


def test_signor_complexes_read_rows_that_drop_trailing_fields(signor):
    h, report = signor
    assert report["tables"]["complexes"] == 3
    members = {(r["subject_id"], r["object_id"]): r for r in _rels(h, "signor", "complex_member")}
    assert len(members) == 8
    assert members[("signor:SIGNOR-C1", "uniprot:P25208")]["note"] == "Complex Portal CPX-1956"
    assert members[("signor:SIGNOR-C102", "signor:SIGNOR-C77")]["object_type"] == "complex"
    assert members[("signor:SIGNOR-C102", "chebi:15422")]["object_type"] == "compound"
    assert members[("signor:SIGNOR-C10", "uniprot:P05412")]["evidence"] == "listed"


# -------------------------------------------------------------------------------- JASPAR
def _jaspar_dump(evil_path):
    return "\n".join([
        "PRAGMA foreign_keys=OFF;",
        "BEGIN TRANSACTION;",
        "CREATE TABLE `MATRIX` (\n  `ID` integer NOT NULL PRIMARY KEY AUTOINCREMENT\n"
        ",  `COLLECTION` varchar(16) DEFAULT ''\n,  `BASE_ID` varchar(16) NOT NULL DEFAULT ''"
        "\n,  `VERSION` integer NOT NULL DEFAULT '1'\n,  `NAME` varchar(255) NOT NULL "
        "DEFAULT ''\n);",
        "INSERT INTO MATRIX VALUES(1,'CORE','MA0139',2,'CTCF');",
        "INSERT INTO MATRIX VALUES(2,'CORE','MA0099',4,'FOS::JUN');",
        "INSERT INTO MATRIX VALUES(3,'CORE','MA2435',1,'SPL16D');",
        "INSERT INTO MATRIX VALUES(4,'UNVALIDATED','UN0001',1,'ZNF1');",
        "CREATE TABLE `MATRIX_ANNOTATION` (`LOCAL_ID` integer, `ID` integer, `TAG` varchar(255),"
        " `VAL` varchar(255));",
        "INSERT INTO MATRIX_ANNOTATION VALUES(0,1,'type','ChIP-seq');",
        "INSERT INTO MATRIX_ANNOTATION VALUES(1,1,'medline','17512414');",
        "INSERT INTO MATRIX_ANNOTATION VALUES(2,2,'type','SMiLE-seq');",
        "INSERT INTO MATRIX_ANNOTATION VALUES(3,1,'comment','TF has; several variants.');",
        "CREATE TABLE `MATRIX_PROTEIN` (`ID` integer, `ACC` varchar(255));",
        "INSERT INTO MATRIX_PROTEIN VALUES(1,'P49711');",
        "INSERT INTO MATRIX_PROTEIN VALUES(2,'P01100');",
        "INSERT INTO MATRIX_PROTEIN VALUES(2,'P05412');",
        "INSERT INTO MATRIX_PROTEIN VALUES(3,'');",
        "INSERT INTO MATRIX_PROTEIN VALUES(4,'Q00000');",
        "CREATE TABLE `MATRIX_SPECIES` (`ID` integer, `TAX_ID` varchar(255));",
        "INSERT INTO MATRIX_SPECIES VALUES(1,'9606');",
        "INSERT INTO MATRIX_SPECIES VALUES(2,'9606');",
        f"ATTACH DATABASE '{evil_path}' AS evil;",
        "COMMIT;", ""])


@pytest.mark.parametrize("compressed", [True, False])
def test_jaspar_links_factors_to_core_matrices_from_its_sql_dump(tmp_path, compressed):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("jaspar")
    raw.mkdir(parents=True)
    evil = tmp_path / "evil.db"
    data = _jaspar_dump(evil).encode("utf-8")
    # the site sends the dump with Content-Encoding gzip: a client may save it unpacked
    (raw / "JASPAR2026.sql.gz").write_bytes(gzip.compress(data) if compressed else data)
    report = h.build("jaspar", **LOG)
    assert not evil.exists()                         # the dump cannot attach a file
    assert report["tables"] == {"matrix": 4, "matrix_annotation": 4, "matrix_protein": 5,
                                "matrix_species": 2}
    rows = {(r["subject_id"], r["object_id"]): r for r in _rels(h, "jaspar")}
    assert set(rows) == {("uniprot:P49711", "jaspar:MA0139.2"),
                         ("uniprot:P01100", "jaspar:MA0099.4"),
                         ("uniprot:P05412", "jaspar:MA0099.4")}    # UNVALIDATED left out
    ctcf = rows[("uniprot:P49711", "jaspar:MA0139.2")]
    assert ctcf["kind"] == "tf_motif" and ctcf["evidence"] == "known"
    assert ctcf["reference"] == "pmid:17512414"
    assert ctcf["ctx"] == {"method": "ChIP-seq", "species": "9606"}
    dimer = rows[("uniprot:P01100", "jaspar:MA0099.4")]
    assert dimer["note"] == "part of FOS::JUN" and dimer["subject_name"] is None
    (queued,) = h.unresolved("jaspar")
    assert queued["subject_id"] == "jaspar:MA2435.1"


def test_jaspar_keeps_the_matrices_as_files_and_the_large_archives_optional():
    spec = dataset("jaspar")
    raw = [f for f in spec.files if f.fmt == "raw"]
    assert {f.name for f in raw if not f.optional} == {
        "JASPAR2026_CORE_non-redundant_pfms_meme.txt",
        "JASPAR2026_CORE_vertebrates_non-redundant_pfms_jaspar.txt"}
    assert all(f.optional for f in spec.files if (f.expected_bytes or 0) > 250e6)
    assert RELATION_KINDS["tf_motif"] == ("regulator", "motif")


# ----------------------------------------------------------------------------- the specs
@pytest.mark.parametrize("key,no", [("biogrid", 85), ("biogrid_orcs", 85), ("intact", 86),
                                    ("signor", 87), ("jaspar", 89)])
def test_each_dataset_names_its_catalogue_entry_licence_and_commercial_terms(key, no):
    spec = dataset(key)
    assert spec.catalog == (no,)
    assert spec.commercial_use == "allowed"
    assert spec.version and spec.notes and spec.relations
    for kind in spec.relations:
        assert kind in RELATION_KINDS
        assert spec.licence_of(kind)
    for f in spec.files:
        assert f.url.startswith("https://")
        if (f.expected_bytes or 0) > 300e6:
            assert f.optional, f.name


def test_gtopdb_is_not_wrapped():
    from bioagent.tcmdb.datasets import DATASETS
    assert not any("gtopdb" in d.key or "guidetopharmacology" in "".join(f.url for f in d.files)
                   for d in DATASETS)
    assert not any("guidetopharmacology" in s.base_url for s in BY_KEY.values())


# ---------------------------------------------------------------------------- connectors
def _allowed(host):
    hosts = PROFILES["biomedical-research"].allowed_hosts
    return any(host == a or host.endswith("." + a) for a in hosts)


def test_every_host_is_allowlisted_and_paced():
    for source in live.SOURCES:
        assert _allowed(source.host), source.host
        assert source.host in DEFAULT_RATES, source.host
        assert BY_KEY[source.key] is source
    for key in ("biogrid", "biogrid_orcs", "intact", "signor", "jaspar"):
        for f in dataset(key).files:
            assert _allowed(urlsplit(f.url).hostname), f.url
    assert DEFAULT_RATES["signor.uniroma2.it"] <= 1.0
    assert DEFAULT_RATES["jaspar.elixir.no"] <= 1.0
    assert live.PENDING == ()


PSICQUIC = "Tools/webservices/psicquic/intact/webservices/current/search/query/ADRB2"


@pytest.mark.parametrize("key,op,path,params", [
    ("intact", "psicquic_query", PSICQUIC,
     {"format": "tab25", "firstResult": 0, "maxResults": 10}),
    ("intact", "psicquic_count", PSICQUIC, {"format": "count"}),
    ("intact", "interactions", "intact/ws/interaction/findInteractions/ADRB2",
     {"page": 0, "pageSize": 2}),
    ("intact", "complex_search", "intact/complex-ws/search/SMAD4",
     {"first": 0, "number": 5, "format": "json"}),
    ("intact", "complex", "intact/complex-ws/complex/CPX-1", {}),
    ("signor", "entity_relations", "getData.php", {"organism": "9606", "id": "P62258"}),
    ("signor", "relations_among", "getData.php",
     {"type": "connect", "proteins": "P29317,Q06124,P04049,P15056"}),
    ("signor", "pathway_relations", "getPathwayData.php",
     {"pathway": "SIGNOR-MM", "relations": "only"}),
    ("signor", "pathway_description", "getPathwayData.php",
     {"pathway": "SIGNOR-MM", "description": ""}),
    ("jaspar", "matrix", "matrix/MA0139.2/", {"format": "json"}),
    ("jaspar", "factor_matrices", "matrix/",
     {"name": "SP1", "tax_id": 9606, "collection": "CORE", "version": "latest",
      "format": "json"}),
    ("jaspar", "search", "matrix/",
     {"search": "SP1", "tax_id": 9606, "collection": "CORE", "version": "latest",
      "page_size": 3, "format": "json"}),
    ("jaspar", "versions", "matrix/MA0139/versions/", {"format": "json"}),
    ("jaspar", "releases", "releases/", {"format": "json"}),
])
def test_each_operation_renders_the_request_that_was_verified_live(key, op, path, params):
    rendered = render_call(key, op)
    assert rendered["method"] == "GET"
    assert rendered["path"] == path
    assert rendered["params"] == params


def test_a_lookup_needs_its_entity():
    with pytest.raises(ValueError, match="requires"):
        BY_KEY["signor"].op("entity_relations").render(organism="9606")
    assert render_call("jaspar", "matrix", matrix_id="MA0079.5")["path"] == "matrix/MA0079.5/"

"""Safety: the AOP-Wiki and Orphadata connectors and the four snapshots, offline.

The connectors are checked for the requests verified live on 2026-10-01 and for their
hosts being allowed. The snapshots are built from tiny files written here in the real
formats (ToxCast workbooks and the PubChem-deposition archive of workbooks, the AOP-XML
feed, Orphadata product XML) with the real column headers and element names, and checked
for what the rows claim: ids, evidence, outcomes (active, inactive, inconclusive, an
excluded phenotype), context, per-AOP licences and lineage.
"""

from __future__ import annotations

import gzip
import io
import json
import sqlite3
import zipfile

import pytest

from bioagent.policy import PROFILES, PolicyDecision
from bioagent.providers.public_apis import BY_KEY, render_call
from bioagent.providers.supplement import safety as conn_mod
from bioagent.tcmdb import TCMDataHub
from bioagent.tcmdb.datasets import dataset
from bioagent.tcmdb.extra import safety as data_mod

pytestmark = pytest.mark.unit

openpyxl = pytest.importorskip("openpyxl")


def _relations(h: TCMDataHub, key: str) -> list[dict]:
    conn = sqlite3.connect(h.db_path(key))
    conn.row_factory = sqlite3.Row
    try:
        out = [dict(r) for r in conn.execute("SELECT * FROM relations")]
    finally:
        conn.close()
    for r in out:
        r["context"] = json.loads(r["context"]) if r["context"] else {}
    return out


# ------------------------------------------------------------------------- connectors
def test_the_safety_connectors_are_registered_and_their_hosts_allowed():
    profile = PROFILES["biomedical-research"]
    assert {s.key for s in conn_mod.SOURCES} == {"aopwiki", "orphadata"}
    for src in conn_mod.SOURCES:
        assert BY_KEY[src.key] is src
        assert profile.check_network((src.host,)).decision is PolicyDecision.ALLOW
    assert "orphadata_gene_lookup" not in BY_KEY            # failed verification
    for key in ("comptox", "aopwiki", "orphadata", "nhanes"):
        for f in dataset(key).files:
            host = f.url.split("/")[2]
            assert profile.check_network((host,)).decision is PolicyDecision.ALLOW, host


def test_the_requests_match_the_verified_ones():
    r = render_call("aopwiki", "aop_xml", aop_id=1)
    assert (r["method"], r["path"]) == ("GET", "aops/1.xml")
    assert render_call("aopwiki", "event")["path"] == "events/142.json"
    assert render_call("aopwiki", "relationship")["path"] == "relationships/324.json"
    r = render_call("orphadata", "genes", orphacode=93)
    assert (r["method"], r["path"]) == ("GET", "rd-associated-genes/orphacodes/93")
    r = render_call("orphadata", "phenotypes", orphacode=58, lang="en")
    assert r["path"] == "rd-phenotypes/orphacodes/58" and r["params"] == {"lang": "en"}
    r = render_call("orphadata", "by_omim", omim=203450, lang="en")
    assert r["path"] == "rd-cross-referencing/omims/203450"
    r = render_call("orphadata", "classifications", orphacode=58)
    assert r["path"] == "rd-classification/orphacodes/58/hchids"


# --------------------------------------------------------------------------- ToxCast
_ENDPOINT = ["aid", "asid", "assay_name", "timepoint_hr", "ncbi_taxon_id", "organism",
             "tissue", "cell_short_name", "assay_source_name", "aeid",
             "assay_component_endpoint_name", "burst_assay", "signal_direction"]
_TARGET = ["aeid", "assay_component_endpoint_name.x", "assay_component_endpoint_name.y",
           "target_id", "target_type", "official_full_name", "official_symbol",
           "ncbi_taxon_id"]
_CYTOTOX = ["chid", "casn", "chnm", "dsstox_substance_id", "cytotox_median_raw",
            "cytotox_mad", "global_mad", "cytotox_median_log", "cytotox_median_um",
            "cytotox_lower_bound_um", "ntested", "nhit", "cytotox_lower_bound_log",
            "created_date"]
_QC = ["analytical_qc_id", "dsstox_substance_id", "chnm", "spid", "qc_level",
       "pass_or_caution", "t0", "t4", "stability_call", "annotation", "flags",
       "average_mass", "log10_vapor_pressure_OPERA_pred", "logKow_octanol_water_OPERA_pred",
       "created_date"]
_RESULTS = ["PUBCHEM_RESULT_TAG", "PUBCHEM_EXT_DATASOURCE_REGID", "PUBCHEM_ACTIVITY_OUTCOME",
            "PUBCHEM_ACTIVITY_URL", "AC50", "HITC", "BMD"]
_URL = "https://comptox.epa.gov/dashboard/chemical/invitrodb/"


def _xlsx(rows: list[list], sheet: str = "Sheet1") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _comptox(raw):
    raw.mkdir(parents=True, exist_ok=True)
    files = {f.table: f.name for f in dataset("comptox").files if f.table}
    (raw / files["assay_endpoint"]).write_bytes(_xlsx([
        _ENDPOINT,
        [1, 1, "TOX21_ERa", 16, 9606, "human", "kidney", "HEK293T", "TOX21", 10,
         "TOX21_ERa_BLA_Agonist_ratio", 0, "gain"],
        [2, 1, "ATG_TRANS", 24, 9606, "human", "liver", "HepG2", "ATG", 20,
         "ATG_PXRE_CIS_up", 0, "gain"],
        [3, 1, "TOX21_VIA", 16, 9606, "human", "kidney", "HEK293T", "TOX21", 30,
         "TOX21_Viability", 1, "loss"]], "annotations_combined"))
    (raw / files["assay_target"]).write_bytes(_xlsx([
        _TARGET,
        [10, "TOX21_ERa_BLA_Agonist_ratio", "TOX21_ERa_BLA_Agonist_ratio", 2099,
         "entrez_gene_id", "estrogen receptor 1", "ESR1", 9606],
        [10, "TOX21_ERa_BLA_Agonist_ratio", "TOX21_ERa_BLA_Agonist_ratio", 1181,
         "key_event", "#N/A", "#N/A", "#N/A"]]))
    (raw / files["cytotox"]).write_bytes(_xlsx([
        _CYTOTOX,
        [1, "80-05-7", "Bisphenol A", "DTXSID7020182", 1.5, 0.2, 0.17, 1.4, 25, 12.5, 90,
         30, 1.1, 45810],
        [2, "60-35-5", "Acetamide", "DTXSID7020005", 1.9, 0, 0.17, 3, 1000, 1000, 87, 1,
         3, 45810]]))
    (raw / files["analytical_qc"]).write_bytes(_xlsx([
        _QC,
        [1, "DTXSID7020182", "Bisphenol A", "TX0001", "sample", "pass", "A", "A", "S",
         "#N/A", "#N/A", 228.1, -6, 3.3, 45434],
        [2, "DTXSID7020005", "Acetamide", "#N/A", "substance", "caution", "A", "A", "S",
         "#N/A", "#N/A", 59.0, -1, -1.2, 45434]]))
    descr = [["RESULT_TYPE", None, None, None, "FLOAT", "FLOAT", "FLOAT"],
             ["RESULT_UNIT", None, None, None, "MICROMOLAR", "NONE", "MICROMOLAR"]]

    def endpoint(results):
        return _xlsx([_RESULTS, *descr, *results], "Results")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pubchem_invitrodb_v4_3/invitrodb_v4.3_aeid10_for_pubchem_18Aug2026.xlsx",
                   endpoint([
                       [1, "TX0001", 2, _URL + "DTXSID7020182", 50.0, 0.98, 40.0],
                       [2, "TX0002", 1, _URL + "DTXSID7020005", 0.3, 0, None],
                       [3, "TX0003", 3, _URL + "DTXSID7020005", 2.0, -0.4, None],
                       [4, "TX0004", 2, "https://comptox.epa.gov/dashboard/", 1.0, 1, 1]]))
        z.writestr("pubchem_invitrodb_v4_3/invitrodb_v4.3_aeid30_for_pubchem_18Aug2026.xlsx",
                   endpoint([[1, "TX0001", 2, _URL + "DTXSID7020182", 50.0, 0.95, 30.0]]))
    (raw / files["hitcall"]).write_bytes(buf.getvalue())


@pytest.fixture
def comptox(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    _comptox(h.raw_dir("comptox"))
    h.build("comptox", log=lambda m: None)
    return h, _relations(h, "comptox")


def test_toxcast_hit_calls_become_positive_negative_and_inconclusive(comptox):
    h, rels = comptox
    calls = {(r["subject_id"], r["object_id"], r["context"]["sample"]): r
             for r in rels if r["kind"] == "compound_assay"}
    assert len(calls) == 4                    # the description rows are not results
    active = calls[("comptox:DTXSID7020182", "comptox:aeid.10", "TX0001")]
    assert (active["outcome"], active["evidence"], active["score"]) == ("positive", "known",
                                                                       "0.98")
    assert active["object_name"] == "TOX21_ERa_BLA_Agonist_ratio"
    assert active["subject_name"] == "Bisphenol A"
    ctx = active["context"]
    assert (ctx["measure"], ctx["value"], ctx["unit"]) == ("AC50", "50", "uM")
    assert ctx["flags"] == ["ac50_above_cytotox_lower_bound"]    # 50 uM > 12.5 uM
    assert (ctx["species"], ctx["cell"], ctx["time"], ctx["qc"]) == (
        "9606", "HEK293T", "16 h", "sample QC: pass")
    assert active["note"] == "via Tox21"
    assert active["license"].startswith("CC0")
    inactive = calls[("comptox:DTXSID7020005", "comptox:aeid.10", "TX0002")]
    assert inactive["outcome"] == "negative" and "measure" not in inactive["context"]
    assert inactive["context"]["qc"] == "substance QC: caution"   # no sample-level call
    flagged = calls[("comptox:DTXSID7020005", "comptox:aeid.10", "TX0003")]
    assert flagged["outcome"] == "inconclusive"
    # a burst (cytotoxicity) endpoint is not flagged against its own burst estimate
    burst = calls[("comptox:DTXSID7020182", "comptox:aeid.30", "TX0001")]
    assert "flags" not in burst["context"]


def test_toxcast_rows_without_a_dtxsid_wait_in_the_queue(comptox):
    h, _ = comptox
    with sqlite3.connect(h.db_path("comptox")) as conn:
        assert conn.execute("SELECT count(*) FROM unresolved").fetchone()[0] == 1


def test_toxcast_endpoint_annotations_give_targets_and_key_events(comptox):
    _, rels = comptox
    target = [r for r in rels if r["kind"] == "assay_target"]
    assert [(r["subject_id"], r["object_id"], r["evidence"]) for r in target] == [
        ("comptox:aeid.10", "ncbigene:2099", "listed")]
    event = [r for r in rels if r["kind"] == "assay_event"]
    assert [(r["object_id"], r["evidence"]) for r in event] == [
        ("aopwiki:event.1181", "listed")]


# -------------------------------------------------------------------------- AOP-Wiki
_AOP_XML = """<?xml version="1.0" encoding="UTF-8"?>
<data xmlns="http://www.aopkb.org/aop-xml">
  <chemical id="c1">
    <casrn>83-79-4</casrn>
    <jchem-inchi-key>JUVIOZPCNVVQFO-HBGVWJBISA-N</jchem-inchi-key>
    <indigo-inchi-key>JUVIOZPCNVVQFO-HBGVWJBISA-N</indigo-inchi-key>
    <preferred-name>Rotenone</preferred-name>
    <dsstox-id>DTXSID6021248</dsstox-id>
  </chemical>
  <stressor id="s1">
    <name>Rotenone</name>
    <chemicals><chemical-initiator chemical-id="c1" user-term="rotenone"/></chemicals>
  </stressor>
  <stressor id="s2"><name>Ionizing radiation</name></stressor>
  <key-event id="e1">
    <title>Inhibition, NADH-ubiquinone oxidoreductase (complex I)</title>
    <short-name>Inhibition, complex I</short-name>
    <biological-organization-level>Molecular</biological-organization-level>
  </key-event>
  <key-event id="e2">
    <title>Mitochondrial dysfunction</title>
    <short-name>Mitochondrial dysfunction</short-name>
    <biological-organization-level>Cellular</biological-organization-level>
  </key-event>
  <key-event-relationship id="k1">
    <title><upstream-id>e1</upstream-id><downstream-id>e2</downstream-id></title>
  </key-event-relationship>
  <aop id="a1">
    <title>Complex I inhibition leading to parkinsonian motor deficits</title>
    <short-name>Complex I and parkinsonism</short-name>
    <status><wiki-license>BY-SA</wiki-license>
      <oecd-status>WPHA/WNT Endorsed</oecd-status></status>
    <molecular-initiating-event key-event-id="e1"/>
    <adverse-outcome key-event-id="e2"/>
    <key-event-relationships>
      <relationship id="k1"><adjacency>adjacent</adjacency>
        <quantitative-understanding-value>Moderate</quantitative-understanding-value>
        <evidence>High</evidence></relationship>
    </key-event-relationships>
    <aop-stressors>
      <aop-stressor stressor-id="s1"><evidence>High</evidence></aop-stressor>
      <aop-stressor stressor-id="s2"><evidence>Not Specified</evidence></aop-stressor>
    </aop-stressors>
  </aop>
  <aop id="a2">
    <title>A draft AOP</title>
    <status><wiki-license>All Rights Reserved</wiki-license></status>
    <key-events><key-event key-event-id="e1"/></key-events>
    <key-event-relationships>
      <relationship id="k1"><adjacency>non-adjacent</adjacency>
        <quantitative-understanding-value>Not Specified</quantitative-understanding-value>
        <evidence>Not Specified</evidence></relationship>
    </key-event-relationships>
  </aop>
  <vendor-specific id="v" name="AopWiki" version="2026-10-01">
    <chemical-reference id="c1" aop-wiki-id="21248"/>
    <stressor-reference id="s1" aop-wiki-id="50"/>
    <stressor-reference id="s2" aop-wiki-id="222"/>
    <key-event-reference id="e1" aop-wiki-id="887"/>
    <key-event-reference id="e2" aop-wiki-id="177"/>
    <key-event-relationship-reference id="k1" aop-wiki-id="888"/>
    <aop-reference id="a1" aop-wiki-id="3"/>
    <aop-reference id="a2" aop-wiki-id="999"/>
  </vendor-specific>
</data>
"""


@pytest.fixture
def aopwiki(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("aopwiki")
    raw.mkdir(parents=True, exist_ok=True)
    name = next(f.name for f in dataset("aopwiki").files if not f.optional)
    (raw / name).write_bytes(gzip.compress(_AOP_XML.encode()))
    h.build("aopwiki", log=lambda m: None)
    return h, _relations(h, "aopwiki")


def test_key_event_relationships_carry_the_weight_of_evidence_of_each_aop(aopwiki):
    _, rels = aopwiki
    kers = {r["reference"]: r for r in rels if r["kind"] == "key_event_relationship"}
    assessed = kers["aopwiki:aop.3"]
    assert (assessed["subject_id"], assessed["object_id"]) == ("aopwiki:event.887",
                                                               "aopwiki:event.177")
    assert assessed["evidence"] == "known"
    assert assessed["context"] == {"confidence": "High", "direct": True,
                                   "qc": "quantitative understanding: Moderate",
                                   "source_id": "aopwiki:ker.888"}
    assert assessed["note"] == "OECD status: WPHA/WNT Endorsed"
    assert assessed["license"].startswith("CC BY-SA")
    draft = kers["aopwiki:aop.999"]
    assert draft["evidence"] == "reported" and draft["context"]["direct"] is False
    assert draft["license"].startswith("All rights reserved")


def test_aop_events_and_stressors(aopwiki):
    _, rels = aopwiki
    events = {(r["subject_id"], r["object_id"], r["note"]) for r in rels
              if r["kind"] == "aop_event"}
    assert events == {("aopwiki:aop.3", "aopwiki:event.887", "MolecularInitiatingEvent"),
                      ("aopwiki:aop.3", "aopwiki:event.177", "AdverseOutcome"),
                      ("aopwiki:aop.999", "aopwiki:event.887", "KeyEvent")}
    stress = {r["subject_id"]: r for r in rels if r["kind"] == "stressor_aop"}
    chem = stress["comptox:DTXSID6021248"]
    assert chem["subject_type"] == "compound" and chem["evidence"] == "known"
    assert chem["note"].startswith("aopwiki:stressor.50")
    other = stress["aopwiki:stressor.222"]
    assert other["subject_type"] == "stressor" and other["evidence"] == "reported"


def test_aopwiki_maps_its_chemicals_to_inchikeys(aopwiki):
    h, _ = aopwiki
    sql = dataset("aopwiki").crosswalk["compound"]
    with sqlite3.connect(h.db_path("aopwiki")) as conn:
        assert conn.execute(sql).fetchall() == [
            ("comptox:DTXSID6021248", "inchikey:JUVIOZPCNVVQFO-HBGVWJBISA-N")]


# ------------------------------------------------------------------------- Orphadata
_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<JDBOR date="2026-06-23 07:57:31" version="1.3.42" copyright="Orphanet (c) 2026">
  <Availability><Licence><ShortIdentifier>CC-BY-4.0</ShortIdentifier></Licence>
  </Availability>
"""
_DISORDER = """<OrphaCode>{code}</OrphaCode><Name lang="en">{name}</Name>
      <DisorderType id="21394"><Name lang="en">Disease</Name></DisorderType>
      <DisorderGroup id="36547"><Name lang="en">Disorder</Name></DisorderGroup>"""
_GENE = """<DisorderGeneAssociation>
          <SourceOfValidation>{refs}</SourceOfValidation>
          <Gene id="1"><Name lang="en">{gname}</Name>{symbol}
            <GeneType id="25993"><Name lang="en">gene with protein product</Name></GeneType>
            <ExternalReferenceList count="1"><ExternalReference id="2">
              <Source>HGNC</Source><Reference>30497</Reference>
            </ExternalReference></ExternalReferenceList>
          </Gene>
          <DisorderGeneAssociationType id="17949">
            <Name lang="en">Disease-causing germline mutation(s) in</Name>
          </DisorderGeneAssociationType>
          <DisorderGeneAssociationStatus id="17991">
            <Name lang="en">{status}</Name></DisorderGeneAssociationStatus>
        </DisorderGeneAssociation>"""
_PRODUCT6 = _HEAD + """  <DisorderList count="1">
    <Disorder id="17601">
      {disorder}
      <DisorderGeneAssociationList count="3">{a}{b}{c}</DisorderGeneAssociationList>
    </Disorder>
  </DisorderList>
</JDBOR>
""".format(disorder=_DISORDER.format(code=166024, name="MED-macrocephaly syndrome"),
           a=_GENE.format(refs="22587682[PMID]_11309371[PMID]_ISBN-1[OTHER]_99999",
                          gname="kinesin family member 7", symbol="<Symbol>KIF7</Symbol>",
                          status="Assessed"),
           b=_GENE.format(refs="", gname="aspartylglucosaminidase",
                          symbol="<Symbol>AGA</Symbol>", status="Not yet assessed"),
           c=_GENE.format(refs="", gname="a locus without a symbol", symbol="",
                          status="Assessed"))
_PRODUCT4 = _HEAD + """  <HPODisorderSetStatusList count="1">
    <HPODisorderSetStatus id="1">
      <Disorder id="2">
        {disorder}
        <HPODisorderAssociationList count="2">
          <HPODisorderAssociation id="1">
            <HPO id="4"><HPOId>HP:0000256</HPOId><HPOTerm>Macrocephaly</HPOTerm></HPO>
            <HPOFrequency id="28412"><Name lang="en">Very frequent (99-80%)</Name>
            </HPOFrequency>
            <DiagnosticCriteria id="28454"><Name lang="en">Diagnostic criterion</Name>
            </DiagnosticCriteria>
          </HPODisorderAssociation>
          <HPODisorderAssociation id="2">
            <HPO id="5"><HPOId>HP:0001250</HPOId><HPOTerm>Seizure</HPOTerm></HPO>
            <HPOFrequency id="28440"><Name lang="en">Excluded (0%)</Name></HPOFrequency>
            <DiagnosticCriteria/>
          </HPODisorderAssociation>
        </HPODisorderAssociationList>
      </Disorder>
      <Source>23760316[PMID]</Source>
      <ValidationStatus>y</ValidationStatus>
      <Online>y</Online>
      <ValidationDate>2016-06-01 00:00:00.0</ValidationDate>
    </HPODisorderSetStatus>
  </HPODisorderSetStatusList>
</JDBOR>
""".format(disorder=_DISORDER.format(code=58, name="Alexander disease"))
_PRODUCT1 = _HEAD + """  <DisorderList count="1">
    <Disorder id="2">
      {disorder}
      <SynonymList count="1"><Synonym lang="en">AxD</Synonym></SynonymList>
      <ExternalReferenceList count="1">
        <ExternalReference id="3"><Source>OMIM</Source><Reference>203450</Reference>
          <DisorderMappingRelation id="21527"><Name lang="en">E (Exact mapping)</Name>
          </DisorderMappingRelation>
          <DisorderMappingICDRelation/>
          <DisorderMappingValidationStatus id="21611"><Name lang="en">Validated</Name>
          </DisorderMappingValidationStatus>
        </ExternalReference>
      </ExternalReferenceList>
    </Disorder>
  </DisorderList>
</JDBOR>
""".format(disorder=_DISORDER.format(code=58, name="Alexander disease"))


@pytest.fixture
def orphadata(tmp_path):
    h = TCMDataHub(tmp_path / "hub")
    raw = h.raw_dir("orphadata")
    raw.mkdir(parents=True, exist_ok=True)
    (raw / "en_product6.xml").write_text(_PRODUCT6, encoding="utf-8")
    (raw / "en_product4.xml").write_text(_PRODUCT4, encoding="utf-8")
    (raw / "en_product1.xml").write_text(_PRODUCT1, encoding="utf-8")
    h.build("orphadata", log=lambda m: None)
    return h, _relations(h, "orphadata")


def test_orphanet_gene_disease_associations(orphadata):
    h, rels = orphadata
    genes = {r["subject_id"]: r for r in rels if r["kind"] == "target_disease"}
    assert set(genes) == {"symbol:KIF7", "symbol:AGA"}
    kif7 = genes["symbol:KIF7"]
    assert (kif7["object_id"], kif7["evidence"], kif7["effect"]) == ("orpha:166024", "known",
                                                                    None)
    assert kif7["context"] == {"mechanism": "Disease-causing germline mutation(s) in",
                               "qc": "Assessed"}
    # PMIDs become ids, other sources are kept as written, a cut-off item is left out
    assert kif7["reference"] == "pmid:22587682 | pmid:11309371 | ISBN-1"
    assert kif7["license"].startswith("CC BY 4.0")
    assert genes["symbol:AGA"]["evidence"] == "reported"
    with sqlite3.connect(h.db_path("orphadata")) as conn:
        assert conn.execute("SELECT count(*) FROM unresolved").fetchone()[0] == 1
        assert conn.execute("SELECT Source, Reference FROM disorder_xref").fetchall() == [
            ("OMIM", "203450")]


def test_orphanet_phenotypes_keep_frequency_and_exclusions(orphadata):
    _, rels = orphadata
    pheno = {r["object_id"]: r for r in rels if r["kind"] == "disease_phenotype"}
    macro = pheno["hp:0000256"]
    assert (macro["subject_id"], macro["outcome"], macro["reference"]) == (
        "orpha:58", "positive", "pmid:23760316")
    assert macro["context"] == {"measure": "frequency", "value": "Very frequent (99-80%)",
                                "flags": ["Diagnostic criterion"]}
    assert pheno["hp:0001250"]["outcome"] == "negative"      # 'Excluded (0%)'


# ---------------------------------------------------------------------------- NHANES
def test_nhanes_is_tables_only_and_decodes_transport_zeros():
    spec = dataset("nhanes")
    assert spec.relations == () and "nhanes" not in data_mod.EXTRACTORS
    assert all(f.fmt == "nhanes_xpt" for f in spec.files)
    assert data_mod._xpt_text(5.397605346934028e-79) == "0"
    assert data_mod._xpt_text(float("nan")) is None
    assert data_mod._xpt_text(93703.0) == "93703"
    assert data_mod._xpt_text(b"Ginkgo") == "Ginkgo"


def test_the_safety_datasets_declare_catalogue_numbers_and_licences():
    numbers = {k: dataset(k).catalog for k in ("comptox", "aopwiki", "orphadata", "nhanes")}
    assert numbers == {"comptox": (100,), "aopwiki": (101,), "orphadata": (102,),
                       "nhanes": (103,)}
    for key in numbers:
        assert dataset(key).commercial_use == "allowed"
    big = [f for f in dataset("comptox").files if (f.expected_bytes or 0) > 300_000_000]
    assert big and all(f.optional for f in big)
    assert "MIMIC-IV" in data_mod.__doc__

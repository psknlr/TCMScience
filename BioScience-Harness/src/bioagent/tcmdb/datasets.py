"""Download specifications for the TCM databases that publish files.

One ``DatasetSpec`` per database: where each file lives, what table it becomes, and
what licence governs it. Every URL here returned a real file (not an HTML page) when it
was checked from the harness on 2026-10-01. Files marked ``optional`` are large (an
image archive, a 738 MB side-effect table, quarterly FAERS) and are fetched only on
request (``fetch(..., include_optional=True)``). The downloader's size gate still
applies to them.

``access="manual"`` datasets cannot be fetched by a program. The site needs a login,
serves its data only through pages, or blocks scripted downloads. For these, a person
downloads or exports the files and places them under ``<root>/raw/<key>/`` with the
names given here, and the same loader reads them.

Nothing here is redistributed: files are downloaded into the user's data directory and
stay there. Most of these databases state no data licence. That is recorded as it was
found, and ``docs/tcm-data-sources.md`` says what it means before anyone publishes data
derived from them.
"""

from __future__ import annotations

from .spec import DatasetSpec, FileSpec

__all__ = ["FileSpec", "DatasetSpec", "DATASETS", "dataset"]


_HERB2 = "http://47.92.70.12/static/download_data/V2/"
_HERB1 = "http://47.92.70.12/static/download_data/V1/"
_SYMMAP = "http://www.symmap.org/static/download/V2.0/SymMap%20v2.0%2C%20"
_BATMAN = "http://batman2.cloudna.cn/downloadApiFile/data/browser/"
_TTD = "https://ttd.idrblab.cn/files/download/"
_DDID = "https://bddg.hznu.edu.cn/ddid/static/download/"
_NSIDES = "https://tatonettilab-resources.s3.amazonaws.com/nsides/"
#: TM-MC gives each download a licence icon and nothing else (no deed link, no version)
_TMMC_BY = "CC BY (version unstated)"
_TMMC_NC = "CC BY-NC (version unstated)"

DATASETS: tuple[DatasetSpec, ...] = (
    DatasetSpec(
        "herb2", "HERB 2.0", (13,), "http://herb.ac.cn/v2/", "not stated",
        tuple(FileSpec(_HERB2 + f"HERB_{t}_v2.txt", f"HERB_{t}_v2.txt", table)
              for t, table in (("herb_info", "herb"), ("ingredient_info", "ingredient"),
                               ("formula_info", "formula"), ("target_info", "target"),
                               ("disease_info", "disease"), ("experiment_info", "experiment"),
                               ("clinical_trials", "clinical_trial"),
                               ("meta_info", "meta_analysis"),
                               ("reference_info", "reference"))),
        version="2.0",
        notes="Entity tables. Herb-ingredient and ingredient-target pairs come per entity "
              "from the site's query endpoint (dataset 'herb_api'). The formula table lists "
              "each formula's herbs; the clinical-trial, meta-analysis and reference tables "
              "link herbs, formulas and ingredients to their evidence.",
        relations=("formula_herb", "subject_clinical_trial", "subject_meta_analysis",
                   "subject_reference")),
    DatasetSpec(
        "herb1", "HERB 1.0", (14,), "http://herb.ac.cn/", "not stated",
        tuple(FileSpec(_HERB1 + f"HERB_{t}_info_v1.txt", f"HERB_{t}_info_v1.txt", t,
                       optional=True)
              for t in ("herb", "ingredient", "target", "disease", "reference",
                        "experiment")),
        version="1.0",
        notes="herb.ac.cn's own download endpoint is disabled; the V1 files are served "
              "from the HERB 2.0 host. Superseded by HERB 2.0, so every file is optional."),
    DatasetSpec(
        "symmap2", "SymMap v2", (27,), "http://www.symmap.org/", "not stated",
        tuple(FileSpec(_SYMMAP + f"{code}%20file.xlsx", f"SymMap_v2.0_{code}.xlsx", table,
                       fmt="xlsx")
              for code, table in (("SMHB", "herb"), ("SMIT", "ingredient"),
                                  ("SMTT", "target"), ("SMDE", "disease"),
                                  ("SMTS", "tcm_symptom"), ("SMMS", "mm_symptom"),
                                  ("SMSY", "syndrome")))
        + tuple(FileSpec(_SYMMAP + f"{code}%20key%20file.xlsx", f"SymMap_v2.0_{code}_key.xlsx",
                         f"{table}_names", fmt="xlsx")
                for code, table in (("SMHB", "herb"), ("SMIT", "ingredient"),
                                    ("SMTT", "target"), ("SMDE", "disease"),
                                    ("SMTS", "tcm_symptom"), ("SMMS", "mm_symptom"),
                                    ("SMSY", "syndrome"))),
        version="2.0",
        notes="Entity tables with their mappings to UMLS, MeSH, OMIM, ICD-10-CM and HPO, and "
              "a name index per entity (the 'key' files). The symptom-herb-target "
              "associations come per entity from the site's query endpoint (dataset "
              "'symmap_api')."),
    DatasetSpec(
        "itcm", "ITCM", (20,), "http://itcm.biotcm.net/", "not stated",
        tuple(FileSpec(f"http://itcm.biotcm.net/downDetail/{t}", f"itcm_{t}_detail.txt", t)
              for t in ("herb", "ingredient", "formula", "target", "disease"))
        + tuple(FileSpec(f"http://itcm.biotcm.net/downNetwork/{t}", f"itcm_{t}.txt", t)
                for t in ("herb2ingredient", "ingredient2target", "formula2herb",
                          "target2disease")),
        notes="The only reachable integrated database that ships its relation tables. Herbs "
              "carry TCMID, ETCM, SymMap and TCMSP ids, so it also indexes databases that "
              "are now offline.",
        relations=("herb_ingredient", "ingredient_target", "formula_herb", "target_disease")),
    DatasetSpec(
        "batman2", "BATMAN-TCM 2.0", (8,), "http://bionet.ncpsb.org.cn/batman-tcm/",
        "free for academic use; commercial use by arrangement with the authors",
        (FileSpec(_BATMAN + "known_browse_by_ingredients.txt.gz",
                  "known_browse_by_ingredients.txt.gz", "known_by_ingredient"),
         FileSpec(_BATMAN + "predicted_browse_by_ingredients.txt.gz",
                  "predicted_browse_by_ingredients.txt.gz", "predicted_by_ingredient",
                  fmt="ws", note="whitespace separated: CID, IUPAC name, targets"),
         FileSpec(_BATMAN + "known_browse_by_targets.txt.gz",
                  "known_browse_by_targets.txt.gz", "known_by_target"),
         FileSpec(_BATMAN + "predicted__browse_by_targets.txt.gz",
                  "predicted__browse_by_targets.txt.gz", "predicted_by_target"),
         FileSpec(_BATMAN + "herb_browse.txt", "herb_browse.txt", "herb"),
         FileSpec(_BATMAN + "formula_browse.txt", "formula_browse.txt", "formula")),
        version="2.0",
        notes="Known ingredient-target interactions (from HIT, DrugBank, KEGG, TTD and the "
              "literature) are kept apart from the predicted ones. Predictions carry the "
              "model's score and are computational evidence only.",
        relations=("herb_ingredient", "ingredient_target", "formula_herb")),
    DatasetSpec(
        "batman1", "BATMAN-TCM 1.0", (54,), "http://bionet.ncpsb.org.cn/batman-tcm/index.php",
        "free for academic use; commercial use by arrangement with the authors",
        (FileSpec("http://bionet.ncpsb.org.cn/batman-tcm/index.php/Home/download/fileDownload"
                  "?fileName=Known%20drug-target%20interaction%20data.txt",
                  "batman1_known_dti.txt", "known_dti", optional=True),),
        relations=("ingredient_target",),
        version="1.0",
        notes="Only the known drug-target file (DrugBank 2015, KEGG 2014, TTD 4.3) can be "
              "downloaded; superseded by BATMAN-TCM 2.0."),
    DatasetSpec(
        "tcmbank", "TCMBank", (3,), "http://tcmbank.cn/", "not stated (academic use)",
        tuple(FileSpec(f"http://tcmbank.cn/file/TCM_database/{n}", n, t, fmt="xlsx")
              for n, t in (("herb_all.xlsx", "herb"), ("ingredient_all.xlsx", "ingredient"),
                           ("gene_all.xlsx", "target"), ("disease_all.xlsx", "disease")))
        + (FileSpec("http://tcmbank.cn/file/TCM_database/all_mol2.zip", "all_mol2.zip",
                    "", fmt="raw", optional=True, note="3D structures; kept as a file"),),
        notes="Entity tables; relations come from the live endpoints (connector 'tcmbank'). "
              "Plain http because the site's TLS certificate expired on 2026-06-02."),
    DatasetSpec(
        "tmmc2", "TM-MC 2.0", (53,), "https://tm-mc.kr/",
        "per file, by the icons on the download page (no deed link, no version, no terms "
        "page): CC BY for medicinal_material, chemical_protein, protein_disease and "
        "prescription; CC BY-NC for medicinal_compound and chemical_property",
        (FileSpec("https://tm-mc.kr/download/README.txt", "README.txt", "", fmt="raw",
                  license="no licence icon"),)
        + tuple(FileSpec(f"https://tm-mc.kr/download/{n}.xlsx", f"{n}.xlsx", n, fmt="xlsx",
                         license=lic)
                for n, lic in (("medicinal_material", _TMMC_BY),
                               ("medicinal_compound", _TMMC_NC),
                               ("chemical_property", _TMMC_NC),
                               ("chemical_protein", _TMMC_BY),
                               ("protein_disease", _TMMC_BY),
                               ("prescription", _TMMC_BY))),
        version="2.0 (files of 2026-01-08 to 2026-06-15)",
        notes="Medicinal materials of the Korean, Chinese and Japanese pharmacopoeias (one "
              "Latin name each; one material of one pharmacopoeia can map to two of "
              "another), their compounds, and prescriptions from Korean-medicine textbooks. "
              "Evidence: herb_ingredient is known (compound names curated by hand from "
              "PubMed chromatography papers, one PMID per row; compound ID 0 means TM-MC "
              "could not identify the compound, and those rows wait in the unresolved "
              "queue); formula_herb is listed (textbook and page as the reference; a "
              "prescription is identified by its Hanja name, so one name found in several "
              "textbooks is one formula whose rows carry each book). ingredient_target and "
              "target_disease are not TM-MC observations: compound-protein pairs are copied "
              "from STITCH v5.0 (combined score) or PubChem (score 0, which means 'not "
              "applicable' and is not kept as a score), and every protein-disease pair is a "
              "DisGeNET v7.0 copy, so both are aggregated and their rows say 'via STITCH', "
              "'via PubChem' or 'via DisGeNET v7.0'. TM-MC deposits its compounds in PubChem "
              "(source 29715), so PubChem-derived sources can carry its herb-compound links "
              "back.",
        relations=("herb_ingredient", "ingredient_target", "target_disease", "formula_herb"),
        # Every row stores its licence, so these stay short. target_disease: DisGeNET
        # distributed v7.0 under CC BY-NC-SA 4.0 (recorded by the 2026-10-01 review; its
        # current legal page is rendered by script and was not re-read).
        relation_licenses={
            "herb_ingredient": "CC BY-NC (medicinal_compound, chemical_property)",
            "ingredient_target": "CC BY pairs (chemical_protein); ids via CC BY-NC "
                                 "chemical_property",
            "target_disease": "CC BY (protein_disease); a DisGeNET v7.0 copy, CC BY-NC-SA "
                              "4.0 upstream",
            "formula_herb": "CC BY (prescription, medicinal_material)",
        },
        # the core herb->compound file is CC BY-NC; relation_licenses says per kind which
        # rows a commercial query may still use (formula_herb)
        commercial_use="forbidden",
        upstream=("STITCH", "PubChem", "DisGeNET")),
    DatasetSpec(
        "tcmio", "TCMIO", (47,), "http://tcmio.xielab.net/", "not stated",
        tuple(FileSpec(f"http://tcmio.xielab.net/download/{i}", n, t, fmt="xlsx")
              for i, n, t in ((1, "target.xlsx", "target"),
                              (2, "prescription.xlsx", "prescription"),
                              (3, "tcm.xlsx", "herb"),
                              (4, "prescription_tcm_relation.xlsx", "prescription_herb"),
                              (6, "ingredient.xlsx", "ingredient"),
                              (7, "tcm_ingredient_relation.xlsx", "herb_ingredient"),
                              (8, "ingredient_target_relation.xlsx", "ingredient_target"),
                              (9, "ligand_target_relation.xlsx", "ligand_target")))
        + (FileSpec("http://tcmio.xielab.net/download/5", "ligand.sdf.rar", "", fmt="raw",
                    optional=True),),
        version="2019-11-21",
        notes="Immuno-oncology targets, prescriptions, herbs and ingredients.",
        relations=("herb_ingredient", "ingredient_target", "formula_herb")),
    DatasetSpec(
        "dcabm", "DCABM-TCM", (28,), "http://bionet.ncpsb.org.cn/dcabm-tcm/",
        "not stated (sister of BATMAN-TCM: free for academic use)",
        (FileSpec("http://bionet.ncpsb.org.cn/seed_tcm/download/downloadFile?fileName="
                  "Constituents_detected_in_blood20230603.inchi",
                  "Constituents_detected_in_blood20230603.inchi", "blood_constituent",
                  columns=("PubChem_CID", "InChI")),),
        version="2023-06-03",
        notes="The structures of every constituent detected in blood. Which prescription "
              "or herb each was detected for comes from the live API (connector "
              "'dcabm_tcm')."),
    DatasetSpec(
        "dbpth", "dbPTH", (11,), "https://dbpth.biocuckoo.cn/",
        "'All datasets and annotations are free for use' (download page)",
        (FileSpec("https://dbpth.biocuckoo.cn/Download/Species/Homo_sapiens.zip",
                  "Homo_sapiens.zip", "human_iti"),
         FileSpec("https://dbpth.biocuckoo.cn/Download/PTH.zip", "PTH.zip", "", fmt="raw",
                  optional=True, note="all species, 503 MB")),
        notes="Reported ingredient-target interactions (ITIs) with PubChem CIDs and UniProt "
              "accessions; the human file is the default.",
        relations=("ingredient_target",)),
    DatasetSpec(
        "ddid", "DDID", (39,), "https://bddg.hznu.edu.cn/ddid/", "not stated (cite the paper)",
        tuple(FileSpec(_DDID + f"{n}%20Information.csv", f"DDID_{n}_Information.csv",
                       n.lower(), fmt="csv")
              for n in ("Interaction", "Drug", "Food", "Herb", "NP", "Target", "Disease")),
        notes="Diet- and herb-drug interactions with outcome, mechanism target and "
              "reference. The host fails intermittently, so one snapshot is kept.",
        relations=("herb_drug_interaction",)),
    DatasetSpec(
        "ttd", "TTD", (63,), "https://ttd.idrblab.cn/", "not stated (cite the paper)",
        (FileSpec(_TTD + "P1-01-TTD_target_download.txt", "P1-01-TTD_target_download.txt",
                  "target", fmt="ttd"),
         FileSpec(_TTD + "P1-02-TTD_drug_download.txt", "P1-02-TTD_drug_download.txt",
                  "drug", fmt="ttd"),
         FileSpec(_TTD + "P1-05-Drug_disease.txt", "P1-05-Drug_disease.txt",
                  "drug_disease", fmt="ttd"),
         FileSpec(_TTD + "P1-07-Drug-TargetMapping.xlsx", "P1-07-Drug-TargetMapping.xlsx",
                  "drug_target", fmt="xlsx"),
         FileSpec(_TTD + "P3-01-All.sdf", "P3-01-All.sdf", "", fmt="raw", optional=True)),
        version="10.1.01 (2024-01-10)",
        relations=("drug_target",)),
    DatasetSpec(
        "immport", "ImmPort gene lists", (48,), "https://www.immport.org/shared/genelists",
        "ImmPort Data Use Agreement (use and redistribution permitted; cite)",
        (FileSpec("https://s3.immport.org/release/genelists/current/all_gene_lists.json"
                  "?download=true", "all_gene_lists.json", "", fmt="raw"),
         FileSpec("https://s3.immport.org/release/genelists/current/all_gene_lists.gmt"
                  "?download=true", "all_gene_lists.gmt", "gene_set_member", fmt="gmt")),
        notes="Curated immune gene lists. Study-level data needs a registered account and is "
              "not wrapped.",
        relations=("gene_set_member",)),
    DatasetSpec(
        "nsides", "nSIDES (OFFSIDES / TWOSIDES)", (41,), "https://nsides.io/", "not stated",
        (FileSpec(_NSIDES + "README.txt", "README.txt", "", fmt="raw"),
         FileSpec(_NSIDES + "OFFSIDES.csv.gz", "OFFSIDES.csv.gz", "offsides", fmt="csv",
                  optional=True, note="69 MB, about 3.2M rows"),
         FileSpec(_NSIDES + "TWOSIDES.csv.gz", "TWOSIDES.csv.gz", "twosides", fmt="csv",
                  optional=True, note="738 MB")),
        notes="Drug side effects (OFFSIDES) and drug-drug-side-effect signals (TWOSIDES) "
              "mined from FAERS; the baseline herb-drug signals are compared against.",
        relations=("drug_adverse_event",)),
    DatasetSpec(
        "faers", "FDA FAERS quarterly ASCII", (33,),
        "https://fis.fda.gov/extensions/FPD-QDE-FAERS/FPD-QDE-FAERS.html",
        "US federal public domain",
        (FileSpec("https://fis.fda.gov/content/Exports/faers_ascii_2026q2.zip",
                  "faers_ascii_2026q2.zip", "", fmt="raw", optional=True,
                  note="about 63 MB per quarter; other quarters by the same pattern"),),
        notes="Raw quarterly reports for custom ETL. Day-to-day herbal queries go through "
              "openFDA (connector 'openfda', operations herbal_event_*)."),
    DatasetSpec(
        "mesh", "MeSH descriptors", (64,), "https://www.nlm.nih.gov/mesh/",
        "NLM terms and conditions (free; attribution)",
        (FileSpec("https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/desc2026.gz",
                  "desc2026.gz", "", fmt="raw", optional=True),),
        version="2026",
        notes="Lookups go through the existing 'mesh' connector; the XML is for offline use."),
    DatasetSpec(
        "shennong", "ShenNong-TCM-Dataset", (55,),
        "https://huggingface.co/datasets/michaelwzhu/ShenNong_TCM_Dataset", "Apache-2.0",
        (FileSpec("https://huggingface.co/datasets/michaelwzhu/ShenNong_TCM_Dataset/resolve/"
                  "main/ChatMed_TCM-v0.2.json", "ChatMed_TCM-v0.2.json", "instruction",
                  fmt="jsonl", optional=True, note="110 MB"),),
        notes="About 110k generated query/response pairs for instruction tuning. Generated "
              "text: training data, never evidence."),
    DatasetSpec(
        "tcm_ladder", "TCM-Ladder", (57,), "https://huggingface.co/datasets/timzzyus/TCM-Ladder",
        "CC-BY-4.0",
        (FileSpec("https://huggingface.co/datasets/timzzyus/TCM-Ladder/resolve/main/"
                  "multiChoice.parquet", "multiChoice.parquet", "multi_choice", fmt="parquet"),
         FileSpec("https://huggingface.co/datasets/timzzyus/TCM-Ladder/resolve/main/"
                  "fillInTheBlank.parquet", "fillInTheBlank.parquet", "fill_in_blank",
                  fmt="parquet"),
         FileSpec("https://huggingface.co/datasets/timzzyus/TCM-Ladder/resolve/main/"
                  "visual.parquet", "visual.parquet", "", fmt="raw", optional=True,
                  note="730 MB, images embedded")),
        notes="An evaluation benchmark for TCM language models."),
    DatasetSpec(
        "tcmp300", "TCMP-300", (61,), "https://doi.org/10.6084/m9.figshare.29432726.v2",
        "CC BY 4.0",
        (FileSpec("https://ndownloader.figshare.com/files/55750814", "tcmp-info-20250329.csv",
                  "species", fmt="csv"),
         FileSpec("https://ndownloader.figshare.com/files/55751081", "tcmp-300-release.tar.gz",
                  "", fmt="raw", optional=True, note="12.8 GB of images")),
        notes="300 medicinal-plant classes for image recognition; the class table is the "
              "default, the images are optional."),
    # ------------------------------------------------- live, per-entity (tcmdb.live)
    DatasetSpec(
        "symmap_api", "SymMap v2 (per-entity query endpoint)", (27,), "http://www.symmap.org/",
        "not stated", (), access="live",
        notes="Cached responses of the 'symmap' connector, one per entity and related type: "
              "a herb's ingredients, targets, symptoms, diseases and syndromes, an "
              "ingredient's targets. Filled by hub.enrich_symmap / hub.enrich.",
        relations=("herb_ingredient", "herb_target", "herb_symptom", "herb_disease",
                   "herb_syndrome", "ingredient_target")),
    DatasetSpec(
        "herb_api", "HERB 2.0 (per-entity query endpoint)", (13,), "http://herb.ac.cn/v2/",
        "not stated", (), access="live",
        notes="Cached responses of the 'herb_api' connector: Herb and Ingredient records "
              "with their inferred and literature-reported relations, and the upstream "
              "source of each ingredient-target pair. Filled by hub.enrich_herb / hub.enrich.",
        relations=("herb_ingredient", "herb_target", "herb_disease", "ingredient_target",
                   "ingredient_disease")),
    # ----------------------------------------------------------------- manual datasets
    DatasetSpec(
        "tcmsp_export", "TCMSP (exported tables)", (1,), "https://old.tcmsp-e.com/tcmsp.php",
        "ODbL 1.0 (database) / DbCL 1.0 (contents)",
        (FileSpec("", "tcmsp_ingredients.csv", "ingredient", fmt="csv",
                  note="columns as shown on a herb's Ingredients tab: MOL_ID, Molecule name, "
                       "MW, AlogP, Hdon, Hacc, OB (%), Caco-2, BBB, DL, FASA-, TPSA, RBN, HL, "
                       "plus a 'herb' column naming the herb"),
         FileSpec("", "tcmsp_targets.csv", "target", fmt="csv",
                  note="columns as shown on the Related Targets tab: MOL_ID, molecule_name, "
                       "target_name, plus 'herb'")),
        access="manual",
        instructions="TCMSP has no API or downloads; its pages carry the data behind a "
                     "per-page token, and the new site's API needs an account. Export the "
                     "Ingredients and Related Targets tabs of the herbs you study (copy the "
                     "tables into CSV, adding a 'herb' column) and place the files in "
                     "raw/tcmsp_export/.",
        relations=("herb_ingredient", "ingredient_target")),
    DatasetSpec(
        "drugbank_vocabulary", "DrugBank open vocabulary", (46,),
        "https://go.drugbank.com/releases/latest#open-data", "CC0 1.0",
        (FileSpec("", "drugbank_vocabulary.csv", "drug", fmt="csv",
                  note="the 'DrugBank Vocabulary' CSV from the open-data release (unzipped)"),),
        access="manual",
        instructions="The open vocabulary is CC0, but its download page sits behind a bot "
                     "challenge and robots.txt disallows /downloads/. Download "
                     "drugbank_all_drugbank_vocabulary.csv.zip in a browser, unzip it as "
                     "drugbank_vocabulary.csv into raw/drugbank_vocabulary/."),
)

from .extra import EXTRA_DATASETS  # noqa: E402

DATASETS = DATASETS + EXTRA_DATASETS

_BY_KEY = {d.key: d for d in DATASETS}
if len(_BY_KEY) != len(DATASETS):                       # pragma: no cover
    raise RuntimeError("duplicate dataset keys")


def dataset(key: str) -> DatasetSpec:
    try:
        return _BY_KEY[key]
    except KeyError:
        raise KeyError(f"no TCM dataset {key!r}; have {sorted(_BY_KEY)}") from None

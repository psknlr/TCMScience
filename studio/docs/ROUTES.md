# Research routes

<!-- Generated from studio/runner/src/tcmstudio/routes.py by `python studio/scripts/routes_doc.py`; do not edit by hand. -->

For each kind of question TCM research asks, every source in the runtime that answers a part of it, step by step, with the kind of evidence each gives. The agent reads a route with the core tool `research_routes`, which also says, in the runtime it is asked in, which sources can run, what each one that cannot run needs, and which source in the same step covers it with what that changes. A route runs nothing: every call keeps its own approvals, network permission and governance.

## Evidence kinds

| Kind | Meaning |
| --- | --- |
| `recorded` | what a TCM corpus or standard records (classical passages, materia and formula records, monographs): tradition and specification, not efficacy |
| `reference` | identity, nomenclature and cross-references: what a name or id refers to, not a finding |
| `measured` | values an experiment measured (activities, expression, kinetics, assays, constituents detected in blood) |
| `curated` | relations curators took from publications or other databases: documented, each traceable to its reference |
| `associated` | a statistical association in a population or a screen (GWAS, eQTL, knockout and CRISPR screens): not causation |
| `aggregated` | a score combining several sources (Open Targets, STRING, DGIdb): it ranks what to read, it is not a finding |
| `reported` | reports to a surveillance system: that reports exist, not how often harm occurs or that the product caused it |
| `registered` | a registered study: what was planned, not what it found |
| `predicted` | a computation's prediction (target prediction, docking, structure, ADMET, variant effect): a hypothesis to test |
| `mentioned` | text mining: that a text mentions both, not that it found a link |
| `literature` | a bibliographic hit: a pointer to read; nothing is licensed until the study itself is assessed |
| `data` | a dataset to analyse (runs, spectra, images, repertoires): a finding comes only from an analysis of it |
| `analysis` | a computation run here on the data given: as sound as its inputs and design |
| `per_row` | the data hub's rows, each with its source and its own evidence label (known, predicted, aggregated, listed, reported, mentioned, signal, associated): report each as labelled |

## How the agent uses a route

- Run every source of a step that can run here, not only the first; calls that do not depend on each other can go out together.
- Keep evidence kinds apart in the answer: never merge predicted, aggregated or text-mined rows into measured or curated ones, and say which source gave each.
- When a source cannot run here, say so and use what covers it, saying what that changes; never switch silently. When nothing in a step can run, say that part is unanswered here and what would answer it.
- Fill each &lt;…> from the question or an earlier result; never invent an id (search for it).
- A missing row or an empty result is not evidence of absence.
- To decide between explanations, open the route's scaffold with research_inquiry; a route by itself decides nothing.
- Every call keeps its own approvals, network permission and governance; a route adds no permission.

## Routes

- [Herb identity and botanical source](#herb_identity) (`herb_identity`, 药材身份与基原)
- [What a herb contains](#herb_composition) (`herb_composition`, 药材的化学成分)
- [Formula composition and sources](#formula_composition) (`formula_composition`, 方剂组成与出处)
- [Traditional use, syndromes and indications](#traditional_use) (`traditional_use`, 传统功效与主治)
- [Compound identity, structure and origin](#compound_identity) (`compound_identity`, 化合物身份、结构与来源)
- [What a compound acts on](#compound_targets) (`compound_targets`, 化合物的作用靶点)
- [A target: identity, function, expression and tractability](#target_profile) (`target_profile`, 靶点的身份、功能、表达与成药性)
- [Pathways, enrichment and interaction networks](#pathways_networks) (`pathways_networks`, 通路、富集与相互作用网络)
- [Gene regulation: transcription factors, miRNAs and chromatin](#gene_regulation) (`gene_regulation`, 基因调控：转录因子、miRNA 与染色质)
- [Genes, targets and diseases](#disease_links) (`disease_links`, 靶点、基因与疾病的关联)
- [Efficacy evidence: trials, meta-analyses and studies](#efficacy_evidence) (`efficacy_evidence`, 疗效证据：试验、荟萃分析与文献)
- [Safety: toxicity, adverse events and contraindications](#safety) (`safety`, 安全性：毒性、不良事件与禁忌)
- [Herb–drug interactions](#herb_drug_interaction) (`herb_drug_interaction`, 中药与西药相互作用)
- [Exposure and metabolism: absorbed constituents, metabolism, kinetics](#exposure_metabolism) (`exposure_metabolism`, 暴露与代谢：入血成分、代谢与药代)
- [Structures, binding sites and docking](#structure_binding) (`structure_binding`, 结构与结合：结构、口袋与对接)
- [Variants and pharmacogenomics](#variants_pharmacogenomics) (`variants_pharmacogenomics`, 变异与药物基因组)
- [Omics data: expression, single-cell, proteomics and metabolomics](#omics_datasets) (`omics_datasets`, 组学数据：表达、单细胞、蛋白质组与代谢组)
- [Spectra and compound identification](#spectra_identification) (`spectra_identification`, 谱图与成分鉴定)
- [Cell models, drug sensitivity and cancer genomics](#cell_models_cancer) (`cell_models_cancer`, 细胞模型、药敏与肿瘤基因组)
- [Immunology: epitopes, allergens and repertoires](#immunology) (`immunology`, 免疫：表位、过敏原与免疫组库)
- [Gut microbes and other microorganisms](#microbiome) (`microbiome`, 肠道菌群与微生物)
- [Imaging data](#imaging_data) (`imaging_data`, 影像数据)
- [Literature search and text mining](#literature) (`literature`, 文献检索与证据线索)
- [Terms and identifiers across vocabularies](#ontology_mapping) (`ontology_mapping`, 术语与标识符映射)
- [Combinations and synergy](#combination_effects) (`combination_effects`, 配伍与联合作用)
- [How a formula might work (network pharmacology)](#formula_mechanism) (`formula_mechanism`, 方剂作用机制（网络药理）)

<a id="herb_identity"></a>

### Herb identity and botanical source · 药材身份与基原

`herb_identity`: What a herb name refers to: the recorded materia, its aliases and processed forms, the species and part used, its standards and reference DNA, and its records in the TCM databases.

**The recorded materia record, its aliases and processed forms** (`materia`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `native.tcm_lookup` | recorded | `name: <herb name>` |  |
| `native.tcm_herb` | recorded | `name: <herb name>` |  |
| `tcmdb.materia` | recorded | `query: <herb name>` |  |
| `skill.normalize-tcm-entities` | reference | `names: ["<herb name>"]` | the governed normalisation, with its record |

**The accepted botanical name, its taxonomy and where it grows** (`species`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.gbif.match` | reference | `name: <Latin binomial>` |  |
| `connector.ncbi_datasets.taxonomy` | reference | `taxon: <Latin binomial or NCBI taxon id>` |  |
| `connector.wikidata_sparql.taxon_by_name` | reference | `name: <Latin binomial>` |  |
| `tcmdb.query wfo` | reference | `dataset: wfo; table: <a table tcmdb.tables lists>` | World Flora Online: accepted names and synonyms |
| `connector.gbif.species` | reference | `key: <GBIF key from match>` |  |
| `connector.gbif.occurrences` | data | `taxon_key: <GBIF key>` | where the species has been recorded |

**Pharmacopoeial tests, reference DNA sequences and monographs** (`standards`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `native.hkcmms_standard_lookup` | recorded | `herb: <herb name>` |  |
| `native.hk_cmm_dna_lookup` | reference | `name: <Latin binomial>` |  |
| `tcmdb.relations subject_monograph` | per_row | `kind: subject_monograph; subject: <herb or Latin name>` | EMA herbal monographs |

**The herb in the TCM databases, with the ids the next routes need** (`databases`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.tcmbank.herb_search` | curated | `name: <herb name>` |  |
| `connector.herb_api.search` | curated | `keyword: <herb name>; label: Herb` |  |
| `connector.itcm.herb` | reference | `herb_id: <ITCM herb id>` | cross-references to TCMID, ETCM, SymMap and TCMSP |
| `tcmdb.query herb1` | curated | `dataset: herb1; table: <a table tcmdb.tables lists>` | HERB 1.0 entity tables |
| `tcmdb.query symmap2` | curated | `dataset: symmap2; table: <a table tcmdb.tables lists>` | SymMap v2 entity tables with their UMLS, MeSH, OMIM, ICD-10-CM and HPO mappings |
| `tcmdb.query tcmbank` | curated | `dataset: tcmbank; table: <a table tcmdb.tables lists>` | TCMBank entity tables |

Reconcile: Name the species and the part the answer is about: one herb name can cover several species (正品 and 混伪品), and a processed form (炙, 炒) is recorded apart. Say which records agree.

Cautions: A name not found is not evidence that it does not exist. An identity match infers no clinical property.

Next: [What a herb contains](#herb_composition), [Traditional use, syndromes and indications](#traditional_use), [Safety: toxicity, adverse events and contraindications](#safety)


<a id="herb_composition"></a>

### What a herb contains · 药材的化学成分

`herb_composition`: Which compounds a herb contains, how each is known (reported from the plant, detected in blood after dosing, or predicted), and how many independent sources agree.

**Compounds reported from the plant** (`reported`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations organism_compound` | per_row | `kind: organism_compound; subject: <Latin binomial>` |  |
| `tcmdb.relations herb_ingredient` | per_row | `kind: herb_ingredient; subject: <herb>` |  |
| `connector.coconut.organism_molecules` | curated | `organism: <Latin binomial>` |  |
| `connector.knapsack.organism_metabolites` | curated | `organism: <Latin binomial>` |  |
| `connector.knapsack.pair_reference` | curated | `c_id: <KNApSAcK C_ID>; row: <row>` | the reference of one species–metabolite pair (row: the organism's row on the metabolite's page) |
| `connector.coconut.collection_molecules` | curated | `collection: <COCONUT collection title>` | every molecule of one source collection COCONUT merged (a TCM database among them) |
| `connector.wikidata_sparql.compounds_in_taxon` | curated | `taxon_qid: <Wikidata QID from taxon_by_name>` | LOTUS occurrences, each with its reference |
| `connector.tcmbank.herb_ingredients` | curated | `tcmbank_id: <TCMBank herb id>` |  |
| `connector.itcm.herb_ingredients` | curated | `herb_id: <ITCM herb id>` |  |
| `connector.herb_api.detail` | curated | `entity_id: <HERB herb id>; label: Herb` |  |
| `connector.symmap.related` | curated | `entity_id: <SymMap herb id SMHB…>; related: Mol` |  |

**Constituents detected in blood after the herb was given** (`absorbed`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.dcabm_tcm.herb_blood` | measured | `names: ["<pinyin, upper case, e.g. HUANG QI>"]` |  |
| `tcmdb.query dcabm` | measured | `dataset: dcabm; table: <a table tcmdb.tables lists>` | structures of every constituent DCABM-TCM detected in blood (which herb each came from is the connector's) |

**For a herb that is also a food (药食同源): nutrients and dietary phytochemicals** (`foods`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations food_nutrient` | per_row | `kind: food_nutrient; subject: <food name>` |  |
| `connector.phytohub.search` | curated | `name: <compound or food>` |  |
| `connector.phytohub.food_source` | reference | `food_id: <PhytoHub food id>` |  |
| `connector.phytohub.structure_sdf` | reference | `phub_id: <PHUB…>` |  |

**How many independent sources report each compound** (`agreement`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.consensus herb_ingredient` | per_row | `kind: herb_ingredient; subject: <herb>` |  |
| `tcmdb.consensus organism_compound` | per_row | `kind: organism_compound; subject: <Latin binomial>` |  |
| `tcmdb.compare herb_ingredient` | per_row | `kind: herb_ingredient; subject: <herb>` |  |

Reconcile: tcmdb_consensus counts independent sources once (copies of one upstream are one source). Keep compounds detected in blood apart from those only reported in the plant, and predicted rows apart from both.

Cautions: Reported from the plant is not present in the medicinal part, the processed slice or the decoction: processing and dose change what is there. A compound missing from every source is not shown to be absent.

Next: [Compound identity, structure and origin](#compound_identity), [Exposure and metabolism: absorbed constituents, metabolism, kinetics](#exposure_metabolism), [What a compound acts on](#compound_targets)


<a id="formula_composition"></a>

### Formula composition and sources · 方剂组成与出处

`formula_composition`: What a formula contains in each recorded source version, with doses as written; how the databases list its herbs; which recorded incompatibilities its herbs raise; and whether the whole formula does more than its main constituent.

**The formula's recorded source versions, doses as written** (`versions`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `native.tcm_formula` | recorded | `name: <formula>` |  |
| `tcmdb.formulas` | recorded | `query: <formula>; rank: true` |  |
| `native.hkbu_formula_lookup` | recorded | `formula: <formula>` |  |
| `native.tcm_classical_search` | recorded | `query: <formula>` |  |

**The herbs each database lists for it** (`herbs`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations formula_herb` | per_row | `kind: formula_herb; subject: <formula>` |  |
| `tcmdb.consensus formula_herb` | per_row | `kind: formula_herb; subject: <formula>` |  |
| `connector.herb_api.search` | curated | `keyword: <formula>; label: Formula` |  |
| `connector.itcm.herb_formulas` | curated | `herb_id: <ITCM herb id>` | the formulas that contain a herb (the reverse direction) |

**Recorded incompatibilities among its herbs (十八反, 十九畏, 妊娠禁忌)** (`compatibility`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `native.tcm_compatibility` | recorded | `herbs: ["<herb>", "<herb>"]` |  |

**Constituents detected in blood after the formula was given** (`absorbed`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.dcabm_tcm.prescription_blood` | measured | `names: ["<pinyin, upper case, e.g. HUANG QI>"]` |  |

**Whether the whole formula does more than its main constituent on one endpoint** (`versus_constituent`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `study.contrast_formula_monomer` | analysis | `formula: {"label": "formula", "values": ["<replicate values>"], "study": "<study>"}; monomer: {"label": "monomer:<constituent>", "values": ["<replicate values>"], "study": "<the same study>"}; margin: <margin, fixed in advance>` | measured effects of both at matched dose, against an equivalence margin fixed in advance |

Reconcile: Keep source versions apart (one name can be different formulas); a database's herb list is one version, so say which.

Deciding between explanations: research_inquiry scaffold `formula_vs_constituent`.

Cautions: Recorded composition and dose are the text's, not a prescription. A compatibility record is a traditional rule for a practitioner to weigh, not a measured interaction.

Next: [What a herb contains](#herb_composition), [How a formula might work (network pharmacology)](#formula_mechanism), [Safety: toxicity, adverse events and contraindications](#safety)


<a id="traditional_use"></a>

### Traditional use, syndromes and indications · 传统功效与主治

`traditional_use`: What a herb or formula is recorded to treat in TCM terms (actions, syndromes, symptoms), how those map to modern names, and what the record licenses.

**Actions, indications and passages as recorded** (`recorded`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `native.tcm_herb` | recorded | `name: <herb>` |  |
| `native.tcm_syndrome` | recorded | `name: <syndrome>` |  |
| `native.tcm_classical_search` | recorded | `query: <herb, formula or phrase>` |  |
| `tcmdb.formulas` | recorded | `indication: <功效 term, e.g. 脾胃气虚>` | formulas whose recorded 功效 mentions a term |

**The syndromes, symptoms and diseases the TCM databases link to it** (`databases`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations herb_syndrome` | per_row | `kind: herb_syndrome; subject: <herb>` |  |
| `tcmdb.relations herb_symptom` | per_row | `kind: herb_symptom; subject: <herb>` |  |
| `tcmdb.relations herb_disease` | per_row | `kind: herb_disease; subject: <herb>` |  |
| `connector.symmap.related` | curated | `entity_id: <SymMap herb id SMHB…>; related: Syndrome` |  |
| `connector.symmap.related` | curated | `entity_id: <SymMap herb id SMHB…>; related: MM_symptom` | modern-medicine symptoms |
| `connector.itcm.herb_diseases` | predicted | `herb_id: <ITCM herb id>` | diseases enriched among the herb's targets (p, FDR): a computation, not a record of use |

**The modern terms a symptom or a disease maps to** (`modern_names`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.mesh.descriptor` | reference | `label: <term>` |  |
| `connector.hpo.search` | reference | `q: <symptom>` |  |
| `connector.nlm_clinical_tables.conditions` | reference | `terms: <condition>` |  |
| `connector.nlm_clinical_tables.icd10cm` | reference | `terms: <condition>` |  |

**What the recorded evidence licenses** (`licensing`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `native.tcm_applicability` | analysis | `subject: <herb or formula>; object: <condition or syndrome>; claim_kind: traditional_use` |  |
| `native.tcm_evidence_tiers` | reference |  |  |

Reconcile: Give the recorded use with its source; a syndrome is not a disease, so give a mapping as a mapping, never as the same thing.

Cautions: A recorded use is tradition: it licenses a traditional_use claim, never efficacy.

Next: [Efficacy evidence: trials, meta-analyses and studies](#efficacy_evidence), [How a formula might work (network pharmacology)](#formula_mechanism)


<a id="compound_identity"></a>

### Compound identity, structure and origin · 化合物身份、结构与来源

`compound_identity`: What a compound is (identifiers, structure, class), how its names map across databases, and in which organisms it occurs.

**Identifiers, structure and synonyms** (`identifiers`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.pubchem.cid_by_name` | reference | `name: <compound>` |  |
| `connector.pubchem.properties_by_cid` | reference | `cid: <PubChem CID>` |  |
| `connector.pubchem.synonyms` | reference | `cid: <PubChem CID>` |  |
| `connector.chebi.search` | reference | `term: <compound>` |  |
| `connector.chebi.compound` | reference | `chebi_id: <ChEBI id>` |  |
| `connector.chebi.compounds` | reference | `chebi_ids: <ChEBI ids, comma-separated>` | several at once; secondary ids resolve to their primary entry |
| `connector.chembl.molecule` | reference | `chembl_id: <ChEMBL molecule id>` |  |
| `connector.metabolomics_workbench.refmet_match` | reference | `name: <compound>` |  |
| `connector.metabolomics_workbench.compound_by_inchikey` | reference | `inchikey: <InChIKey>` |  |
| `connector.metanetx.chem_by_xref` | reference | `xref: <e.g. chebi:28499>` |  |
| `connector.tcmbank.ingredient_search` | curated | `name: <compound>` |  |
| `connector.drugcentral.drug_by_name` | reference | `name: <drug>` | for a compound that is also a drug |
| `tcmdb.query metanetx` | reference | `dataset: metanetx; table: <a table tcmdb.tables lists>` | MNXref: the same metabolite across ChEBI, KEGG, HMDB, MetaCyc and others |
| `tcmdb.query drugbank_vocabulary` | reference | `dataset: drugbank_vocabulary; table: <a table tcmdb.tables lists>` | DrugBank open vocabulary: names, synonyms, CAS and UNII |

**Its chemical class, its role and similar structures** (`class`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations compound_class` | per_row | `kind: compound_class; subject: <compound or ChEBI id>` |  |
| `tcmdb.relations compound_role` | per_row | `kind: compound_role; subject: <compound or ChEBI id>` |  |
| `connector.chebi.parents` | reference | `chebi_id: <ChEBI id>` |  |
| `connector.chebi.children` | reference | `chebi_id: <ChEBI id>` | the entities that point to it (subclasses, derivatives, conjugates) |
| `connector.chebi.structure_search` | reference | `smiles: <SMILES>` | structurally similar compounds |

**The organisms it is reported from** (`occurrence`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations organism_compound` | per_row | `kind: organism_compound; object: <compound>` |  |
| `connector.wikidata_sparql.taxa_with_compound` | curated | `inchikey: <InChIKey>` |  |
| `connector.knapsack.metabolite_search` | curated | `name: <compound>` |  |
| `connector.knapsack.metabolite` | curated | `c_id: <KNApSAcK C_ID>` |  |
| `connector.coconut.search` | curated | `query: <name, SMILES or InChIKey>` |  |
| `connector.mibig.entry` | curated | `accession_version: <BGC accession.version>` | a microbial gene cluster that makes it |

Reconcile: Join records on the InChIKey or the PubChem CID, never on names: name matches across databases are often other stereoisomers or salts.

Cautions: A name-only match is ambiguous: give the identifier the answer rests on.

Next: [What a compound acts on](#compound_targets), [Exposure and metabolism: absorbed constituents, metabolism, kinetics](#exposure_metabolism), [Spectra and compound identification](#spectra_identification), [Safety: toxicity, adverse events and contraindications](#safety)


<a id="compound_targets"></a>

### What a compound acts on · 化合物的作用靶点

`compound_targets`: Which proteins a compound acts on, with measured activities kept apart from curated links and from predictions, and how many independent sources agree.

**Activities measured in assays** (`measured`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations compound_target` | per_row | `kind: compound_target; subject: <compound>` |  |
| `tcmdb.relations compound_assay` | per_row | `kind: compound_assay; subject: <compound>` |  |
| `connector.drugcentral.activities` | measured | `struct_id: <DrugCentral struct id>` | for a compound that is a drug |
| `connector.pharos.ligand` | measured | `ligand_id: <CID:… or name>` |  |
| `connector.klifs.ligand_bioactivities` | measured | `ligand_id: <KLIFS ligand id>` | for a kinase ligand |
| `connector.chembl.activities` | measured | `target: <ChEMBL target id>` | the activities measured on one candidate target |

**Interactions curated from publications** (`curated`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations drug_target` | per_row | `kind: drug_target; subject: <drug>` |  |
| `connector.signor.entity_relations` | curated | `id: <PubChem CID>` |  |
| `connector.intact.psicquic_query` | curated | `query: <compound name or CHEBI id>` |  |
| `connector.dgidb.drug_interactions` | aggregated | `drug: <drug>` |  |

**The targets the TCM databases list, many of them predicted** (`tcm_databases`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations ingredient_target` | per_row | `kind: ingredient_target; subject: <compound>` |  |
| `connector.itcm.ingredient_targets` | curated | `ingredient_id: <ITCM ingredient id>` | each pair with its source database |
| `connector.symmap.related` | curated | `entity_id: <SymMap ingredient id SMIT…>; related: Gene` |  |
| `connector.herb_api.detail` | curated | `entity_id: <HERB ingredient id HBIN…>; label: Ingredient` |  |
| `connector.tcmbank.detail ingredient` | curated | `kind: ingredient; tcmbank_id: <TCMBank ingredient id>` |  |

**How many independent sources support each target** (`agreement`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.consensus ingredient_target` | per_row | `kind: ingredient_target; subject: <compound>` |  |
| `tcmdb.consensus compound_target` | per_row | `kind: compound_target; subject: <compound>` |  |

**A structure-based prediction for a shortlisted target (a job)** (`binding`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `job.pipeline.dock` | predicted | `receptor: <PDB:id, UniProt:accession or file>; ligands: <SMILES or file>` |  |

Reconcile: List measured targets first with their values and assays, then curated links, then predictions; tcmdb_consensus counts independent lineages once.

Deciding between explanations: research_inquiry scaffold `target_engagement`.

Cautions: A target list is not a mechanism: activity has to be shown at concentrations the compound reaches (exposure_metabolism). Predicted and docking targets support hypotheses only.

Next: [A target: identity, function, expression and tractability](#target_profile), [Pathways, enrichment and interaction networks](#pathways_networks), [Exposure and metabolism: absorbed constituents, metabolism, kinetics](#exposure_metabolism)


<a id="target_profile"></a>

### A target: identity, function, expression and tractability · 靶点的身份、功能、表达与成药性

`target_profile`: What a gene or protein is, what it does, where it is expressed, how constrained it is in people, and how tractable it is as a drug target.

**The gene and protein records** (`identity`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.hgnc.symbol` | reference | `symbol: <gene symbol>` |  |
| `connector.uniprot.search` | reference | `query: <gene symbol> AND organism_id:9606` |  |
| `connector.uniprot.entry` | curated | `accession: <UniProt accession>` |  |
| `connector.ensembl.gene_lookup` | reference | `symbol: <gene symbol>` |  |
| `connector.ncbi_datasets.gene_by_symbol` | reference | `symbol: <gene symbol>` |  |
| `connector.mygene.query` | reference | `q: <gene symbol>` |  |
| `connector.ensembl.xrefs` | reference | `id: <Ensembl gene id>` |  |
| `connector.ensembl.sequence` | reference | `id: <Ensembl id>` |  |
| `connector.uniprot.fasta` | reference | `accession: <UniProt accession>` |  |

**Functions, domains, modification sites and disordered regions** (`function`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.quickgo.annotations` | curated | `gene_product: <UniProt accession>` |  |
| `connector.interpro.protein_entries` | predicted | `accession: <UniProt accession>` | domain signatures matched to the sequence |
| `connector.interpro.protein` | reference | `accession: <UniProt accession>` |  |
| `connector.interpro.entry` | curated | `ipr: <IPR…>` |  |
| `connector.disprot.search_by_accession` | curated | `accession: <UniProt accession>` |  |
| `connector.disprot.entry` | curated | `disprot_id: <DisProt id>` |  |
| `tcmdb.relations protein_region` | per_row | `kind: protein_region; subject: <UniProt accession>` |  |
| `tcmdb.relations ptm_site` | per_row | `kind: ptm_site; object: <UniProt accession>` | the sites enzymes modify on it |
| `tcmdb.relations protein_site` | per_row | `kind: protein_site; subject: <UniProt accession>` |  |
| `connector.omnipath.annotations` | aggregated | `proteins: <UniProt accession>` |  |
| `native.protein_properties` | analysis | `sequence: <protein sequence>` |  |

**Where it is expressed, as RNA and as protein** (`expression`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.gtex.median_expression` | measured | `gencode_id: <versioned GENCODE id>` |  |
| `connector.gtex.gene` | reference | `gene_id: <gene symbol or GENCODE id>` |  |
| `connector.proteinatlas.gene` | measured | `ensembl_id: <Ensembl gene id>` |  |
| `connector.proteinatlas.search` | measured | `query: <gene symbol>` |  |
| `connector.proteomicsdb.expression` | measured | `uniprot: <UniProt accession>` |  |

**Its development level, known ligands and drugs** (`tractability`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.pharos.target` | curated | `symbol: <gene symbol>` |  |
| `connector.pharos.target_ligands` | measured | `symbol: <gene symbol>` |  |
| `connector.pharos.tdl_counts` | reference |  | how many targets are at each development level, the background for one target's level |
| `connector.pharos.version` | reference |  | the release to cite |
| `connector.chembl.target_search` | reference | `symbol: <gene symbol>` |  |
| `connector.chembl.target` | reference | `chembl_id: <ChEMBL target id>` |  |
| `connector.drugcentral.gene_activities` | measured | `gene: <gene symbol>` |  |
| `connector.dgidb.gene_interactions` | aggregated | `gene: <gene symbol>` |  |
| `connector.ttd.target` | curated | `target_id: <TTD target id>` |  |
| `connector.ttd.target_drugs` | curated | `target_id: <TTD target id>` |  |
| `connector.gpcrdb.protein_by_accession` | curated | `accession: <UniProt accession>` | for a GPCR |
| `connector.gpcrdb.drugs` | curated | `entry_name: <UniProt entry name, e.g. adrb2_human>` |  |
| `connector.gpcrdb.ligands` | measured | `entry_name: <UniProt entry name>` |  |
| `connector.klifs.kinase_id` | curated | `name: <kinase name>` | for a kinase |
| `tcmdb.relations gene_set_member` | per_row | `kind: gene_set_member; object: <gene symbol>` | the gene sets it belongs to (Pharos development levels, ImmPort lists) |

**Constraint, variant effects and what losing it does** (`genetics`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.gnomad.gene_constraint` | measured | `symbol: <gene symbol>` |  |
| `connector.mavedb.score_sets_by_target` | measured | `gene: <gene symbol>` | deep mutational scans |
| `connector.impc.phenotype_calls` | associated | `symbol: <mouse gene symbol>` |  |
| `connector.impc.statistical_results` | associated | `symbol: <mouse gene symbol>` | every test of its knockout lines, significant or not (a tested negative counts) |
| `tcmdb.relations gene_phenotype` | per_row | `kind: gene_phenotype; subject: <gene symbol>` |  |
| `tcmdb.relations screen_gene` | per_row | `kind: screen_gene; object: <gene symbol>` | CRISPR screens that hit it |

Reconcile: Give each fact with its source; RNA (GTEx) and protein (ProteomicsDB, HPA) expression can disagree, and both are tissue averages.

Cautions: Expression in a tissue is not activity there, and a mouse knockout's phenotype is not the human one.

Next: [Structures, binding sites and docking](#structure_binding), [Pathways, enrichment and interaction networks](#pathways_networks), [Genes, targets and diseases](#disease_links), [Gene regulation: transcription factors, miRNAs and chromatin](#gene_regulation)


<a id="pathways_networks"></a>

### Pathways, enrichment and interaction networks · 通路、富集与相互作用网络

`pathways_networks`: Which pathways a set of genes or targets falls in, whether that is more than chance against a stated background, and how the proteins connect.

**The pathways each gene or protein belongs to** (`membership`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.reactome.pathways_for_entity` | curated | `id: <UniProt accession or Reactome id>` |  |
| `connector.reactome.query` | curated | `id: <Reactome stable id>` |  |
| `connector.reactome.version` | reference |  | the release to cite |
| `connector.kegg.link` | curated | `target_db: pathway; entry: <hsa:gene id>` |  |
| `connector.kegg.find` | reference | `db: pathway; query: <term>` |  |
| `connector.kegg.get` | curated | `entry: <KEGG id>` |  |
| `connector.kegg.info` | reference |  | the release to cite |
| `connector.wikipathways.find` | curated | `query: <term or gene>` |  |
| `connector.wikipathways.info` | curated | `pathway_id: <WP…>` |  |
| `connector.wikipathways.list` | reference |  | every pathway, for a gene-set library of your own |
| `connector.panther.geneinfo` | curated | `genes: <gene ids, comma-separated>` |  |
| `connector.signor.pathway_relations` | curated | `pathway: <SIGNOR pathway id>` |  |
| `connector.signor.pathway_description` | curated | `pathway: <SIGNOR pathway id>` |  |
| `connector.ttd.target_pathways` | curated | `target_id: <TTD target id>` |  |

**Over-representation against a stated background** (`enrichment`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.gprofiler.gost` | analysis | `query: <gene symbols, space-separated>` |  |
| `connector.panther.enrich` | analysis | `genes: <gene ids, comma-separated>` |  |
| `native.enrichment_analysis` | analysis | `genes: ["<gene>"]; gene_sets: {"<set>": ["<gene>"]}; background: <background size>` | gene sets and background you state |
| `native.hypergeometric_test` | analysis | `overlap: <k>; query_size: <n>; set_size: <K>; background: <N>` |  |
| `native.benjamini_hochberg` | analysis | `p_values: ["<p>"]` |  |

**How the proteins connect** (`interactions`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations protein_interaction` | per_row | `kind: protein_interaction; subject: <UniProt accession or gene>` |  |
| `tcmdb.relations complex_member` | per_row | `kind: complex_member; object: <UniProt accession>` |  |
| `tcmdb.relations regulation` | per_row | `kind: regulation; subject: <regulator>` |  |
| `tcmdb.relations genetic_interaction` | per_row | `kind: genetic_interaction; subject: <gene>` |  |
| `connector.intact.interactions` | curated | `query: <gene or UniProt accession>` |  |
| `connector.intact.psicquic_count` | reference | `query: <MIQL query or gene>` | how many interaction evidences match, before fetching them |
| `connector.intact.complex_search` | curated | `query: <gene or complex>` |  |
| `connector.intact.complex` | curated | `complex_ac: <CPX-…>` |  |
| `connector.signor.relations_among` | curated | `proteins: <UniProt accessions, comma-separated>` |  |
| `connector.omnipath.interactions` | curated | `partners: <UniProt accessions or symbols>` |  |
| `connector.string.network` | aggregated | `identifiers: <genes>` |  |
| `connector.pharos.target_interactions` | aggregated | `symbol: <gene symbol>` |  |

Reconcile: State the background, the gene-set library and the multiple-testing correction of every enrichment. STRING scores and Pharos partners are aggregated: keep them apart from curated interactions.

Deciding between explanations: research_inquiry scaffold `pathway_signal`.

Cautions: Enrichment of a predicted target list says which pathways the predictions fall in, not what the herb does. A pathway named without its background is not a result.

Next: [Genes, targets and diseases](#disease_links), [Gene regulation: transcription factors, miRNAs and chromatin](#gene_regulation)


<a id="gene_regulation"></a>

### Gene regulation: transcription factors, miRNAs and chromatin · 基因调控：转录因子、miRNA 与染色质

`gene_regulation`: What regulates a gene: the transcription factors that bind it and their motifs, the miRNAs that target it, chromatin contacts and expression QTLs.

**The factors bound at the gene, and their motifs** (`transcription_factors`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations tf_target` | per_row | `kind: tf_target; object: <gene symbol>` |  |
| `tcmdb.relations tf_motif` | per_row | `kind: tf_motif; subject: <factor>` |  |
| `connector.chip_atlas.target_genes_index` | reference |  |  |
| `connector.chip_atlas.antigens` | data | `ag_class: TFs and others; genome: hg38` |  |
| `connector.chip_atlas.experiment` | data | `expid: <SRX… or GSM…>` |  |
| `connector.chip_atlas.genomes` | reference |  |  |
| `connector.remap.datasets_by_target` | data | `target: <factor>; taxid: 9606` |  |
| `connector.remap.datasets_by_biotype` | data | `biotype: <cell type>; taxid: 9606` |  |
| `tcmdb.query remap` | data | `dataset: remap; table: <a table tcmdb.tables lists>` | ReMap: which regulator was profiled in which cell type, with each BED file |
| `connector.jaspar.factor_matrices` | curated | `name: <factor>` |  |
| `connector.jaspar.search` | curated | `query: <factor>` |  |
| `connector.jaspar.matrix` | curated | `matrix_id: <MA…>` |  |
| `connector.jaspar.versions` | curated | `base_id: <MA…>` | every version of a matrix |
| `connector.jaspar.releases` | reference |  | the releases, to pin a version |
| `connector.encode.search` | data | `term: <factor or gene>; type: Experiment` |  |
| `native.motif_search` | analysis | `sequence: <DNA>; motif: <IUPAC motif>` |  |

**The miRNAs and other ncRNAs that target it** (`mirnas`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations mirna_target` | per_row | `kind: mirna_target; object: <gene symbol>` |  |
| `connector.rnacentral.rna_by_external_id` | reference | `external_id: <miRBase MIMAT…>` |  |
| `connector.rnacentral.rna` | reference | `urs: <URS id>` |  |
| `connector.rnacentral.xrefs` | reference | `urs: <URS id>; taxid: 9606` |  |
| `connector.rnacentral.protein_targets` | curated | `urs: <URS id>; taxid: 9606` |  |
| `connector.rnacentral.interactions` | curated | `urs: <URS id>; taxid: 9606` |  |
| `connector.rnacentral.go_annotations` | curated | `urs: <URS id>; taxid: 9606` |  |
| `tcmdb.query rnacentral` | reference | `dataset: rnacentral; table: <a table tcmdb.tables lists>` | RNAcentral id mappings (miRBase, HGNC, TarBase, LncBase) |

**Chromatin contacts, region tracks and the sequence** (`chromatin`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations chromatin_loop` | per_row | `kind: chromatin_loop; subject: <chr:start-end>` |  |
| `connector.fourdn.experiment_sets_by_sample` | data | `sample: <biosource, e.g. H1-hESC>` |  |
| `connector.fourdn.processed_files_by_format` | data | `file_format: bedpe` |  |
| `connector.fourdn.experiment_set` | data | `accession: <4DNES…>` |  |
| `connector.fourdn.processed_file` | data | `accession: <4DNFI…>` |  |
| `connector.ucsc.track` | curated | `genome: hg38; chrom: <chr>; start: <start>; end: <end>; track: <track>` |  |
| `connector.ucsc.sequence` | reference | `genome: hg38; chrom: <chr>; start: <start>; end: <end>` |  |
| `native.cpg_islands` | analysis | `sequence: <DNA>` |  |

**The variants that change its expression** (`eqtl`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations variant_gene` | per_row | `kind: variant_gene; object: <gene>` |  |

Reconcile: Say the cell type of every binding claim; ReMap and ChIP-Atlas reprocess the same public experiments, so compare them by series accession before counting binding twice.

Cautions: Binding, motif presence and an eQTL each suggest regulation; none shows it alone.

Next: [Pathways, enrichment and interaction networks](#pathways_networks), [A target: identity, function, expression and tractability](#target_profile)


<a id="disease_links"></a>

### Genes, targets and diseases · 靶点、基因与疾病的关联

`disease_links`: Which diseases a gene or target is linked to and how (genetic association, curated gene–disease links, animal models, phenotypes), and which genes a disease implicates.

**Gene–disease links and their sources** (`associations`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations target_disease` | per_row | `kind: target_disease; subject: <gene symbol>` |  |
| `connector.opentargets.associated_diseases` | aggregated | `ensembl_id: <Ensembl gene id>` |  |
| `connector.opentargets.target` | curated | `ensembl_id: <Ensembl gene id>` |  |
| `connector.pharos.target_diseases` | aggregated | `symbol: <gene symbol>` |  |
| `connector.monarch.associations` | curated | `subject: <gene or disease CURIE>` |  |
| `connector.monarch.search` | reference | `q: <gene or disease>` |  |

**Genome-wide associations** (`genetics`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.gwas_catalog.snps_by_gene` | associated | `gene: <gene symbol>` |  |
| `connector.gwas_catalog.associations_by_rsid` | associated | `rs_id: <rs…>` |  |
| `connector.gwas_catalog.snp` | reference | `rs_id: <rs…>` |  |

**Rare diseases, their genes, phenotypes and natural history** (`rare_disease`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.orphadata.by_name` | reference | `name: <disease>` |  |
| `connector.orphadata.genes` | curated | `orphacode: <ORPHA code>` |  |
| `connector.orphadata.phenotypes` | curated | `orphacode: <ORPHA code>` |  |
| `connector.orphadata.epidemiology` | curated | `orphacode: <ORPHA code>` |  |
| `connector.orphadata.natural_history` | curated | `orphacode: <ORPHA code>` |  |
| `connector.orphadata.classifications` | reference | `orphacode: <ORPHA code>` |  |
| `connector.orphadata.cross_references` | reference | `orphacode: <ORPHA code>` |  |
| `connector.orphadata.by_omim` | reference | `omim: <OMIM number>` |  |
| `connector.orphadata.by_hpo` | reference | `hpoids: <HP:…>` |  |
| `tcmdb.relations disease_phenotype` | per_row | `kind: disease_phenotype; subject: <disease>` |  |

**The phenotypes annotated to genes, and animal models** (`phenotypes`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.hpo.term_annotations` | curated | `hpo_id: <HP:…>` |  |
| `connector.hpo.term` | reference | `hpo_id: <HP:…>` |  |
| `connector.impc.disease_models` | associated | `symbol: <mouse gene symbol>` |  |
| `connector.impc.genes_with_phenotype` | associated | `mp_id: <MP:…>` |  |
| `connector.impc.gene` | reference | `symbol: <mouse gene symbol>` |  |
| `connector.disease_ontology.metadata` | reference | `doid: <DOID:…>` |  |

**The diseases the TCM databases link to a herb or an ingredient** (`tcm_side`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations herb_disease` | per_row | `kind: herb_disease; subject: <herb>` |  |
| `tcmdb.relations ingredient_disease` | per_row | `kind: ingredient_disease; subject: <compound>` |  |
| `connector.herb_api.detail` | curated | `entity_id: <HERB disease id>; label: Disease` |  |
| `connector.tcmbank.detail disease` | curated | `kind: disease; tcmbank_id: <TCMBank disease id>` |  |

Reconcile: Give each link's kind: an Open Targets or Pharos score is aggregated, a GWAS hit is an association, Orphanet and Monarch links are curated; never add scores across sources.

Cautions: A gene linked to a disease is not a treatment target for it, and a herb's target that is also a disease gene shows overlap, not an effect.

Next: [Efficacy evidence: trials, meta-analyses and studies](#efficacy_evidence), [Pathways, enrichment and interaction networks](#pathways_networks)


<a id="efficacy_evidence"></a>

### Efficacy evidence: trials, meta-analyses and studies · 疗效证据：试验、荟萃分析与文献

`efficacy_evidence`: Whether a herb or formula works for a condition in people: registered and published trials, meta-analyses, their designs, and what the evidence licenses.

**The trials, meta-analyses and papers the data hub records for it** (`recorded`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.evidence_for` | per_row | `subject: <herb or formula>` |  |
| `tcmdb.relations subject_clinical_trial` | per_row | `kind: subject_clinical_trial; subject: <herb or formula>` |  |
| `tcmdb.relations subject_meta_analysis` | per_row | `kind: subject_meta_analysis; subject: <herb or formula>` |  |
| `tcmdb.relations subject_reference` | per_row | `kind: subject_reference; subject: <herb or formula>` |  |

**Registered trials, with their status and any posted results** (`registered`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.clinicaltrials.search` | registered | `term: <intervention AND condition>` |  |
| `connector.clinicaltrials.study` | registered | `nct_id: <NCT…>` |  |

**Published trials and reviews** (`published`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.europepmc.search` | literature | `query: <intervention> AND <condition> AND (PUB_TYPE:"Randomized Controlled Trial" OR PUB_TYPE:"Meta-Analysis")` |  |
| `connector.ncbi_eutils.esearch` | literature | `db: pubmed; term: <intervention>[tiab] AND <condition>[tiab] AND randomized controlled trial[pt]` |  |
| `skill.retrieve-tcm-evidence` | literature | `subject: <herb or formula>; claim_kind: efficacy` | a governed search whose results carry their study design |

**Pooled estimates, subgroups and what the evidence licenses** (`synthesis`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `native.meta_analysis` | analysis | `estimates: ["<log effect>"]; standard_errors: ["<standard error>"]` |  |
| `study.effect_modification` | analysis | `outcome: ["<outcome>"]; treated: ["<0 or 1>"]; modifiers: [{"name": "<modifier>", "values": ["<value>"], "baseline": true}]` | whether the effect differs by a baseline characteristic |
| `native.tcm_applicability` | analysis | `subject: <herb or formula>; object: <condition>; claim_kind: efficacy` |  |

Reconcile: Weigh by design and risk of bias, not by count: one adequately powered randomised trial outweighs many small or uncontrolled ones. Say which trials are only registered.

Deciding between explanations: research_inquiry scaffold `efficacy`.

Cautions: A registered trial is a plan and a search hit is a pointer to read; an efficacy claim is licensed only by tcm_applicability and the release gate.

Next: [Safety: toxicity, adverse events and contraindications](#safety)


<a id="safety"></a>

### Safety: toxicity, adverse events and contraindications · 安全性：毒性、不良事件与禁忌

`safety`: What is known about harm from a herb, formula or compound: recorded cautions and incompatibilities, toxicity data and adverse outcome pathways, and adverse-event reports.

**Recorded cautions, incompatibilities and the governed safety report** (`recorded`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `skill.assess-tcm-safety` | recorded | `subject: <herb or formula>` |  |
| `native.tcm_compatibility` | recorded | `herbs: ["<herb>", "<herb>"]` |  |
| `native.tcm_herb` | recorded | `name: <herb>` | its recorded safety records |
| `tcmdb.relations subject_monograph` | per_row | `kind: subject_monograph; subject: <herb or Latin name>` | EMA herbal monographs |

**Toxicity records, assays and adverse outcome pathways** (`toxicity`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations subject_toxicity` | per_row | `kind: subject_toxicity; subject: <herb, formula or compound>` |  |
| `tcmdb.relations compound_assay` | per_row | `kind: compound_assay; subject: <compound>` |  |
| `tcmdb.relations assay_target` | per_row | `kind: assay_target; subject: <ToxCast assay>` |  |
| `tcmdb.relations assay_event` | per_row | `kind: assay_event; subject: <ToxCast assay>` |  |
| `tcmdb.relations stressor_event` | per_row | `kind: stressor_event; subject: <compound>` |  |
| `tcmdb.relations stressor_aop` | per_row | `kind: stressor_aop; subject: <stressor>` |  |
| `tcmdb.relations aop_event` | per_row | `kind: aop_event; subject: <AOP id>` |  |
| `tcmdb.relations key_event_relationship` | per_row | `kind: key_event_relationship; subject: <key event>` |  |
| `connector.aopwiki.aop` | curated | `aop_id: <AOP id>` |  |
| `connector.aopwiki.aop_xml` | curated | `aop_id: <AOP id>` |  |
| `connector.aopwiki.event` | curated | `event_id: <key event id>` |  |
| `connector.aopwiki.relationship` | curated | `relationship_id: <relationship id>` |  |
| `job.pipeline.admet` | predicted | `molecules: <SMILES or file>` | ADMET models (a job) |
| `skill.predict-admet` | predicted | `molecules: <SMILES or file>` | the governed ADMET skill (a job) |

**Adverse-event reports and signals** (`reports`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.openfda.herbal_event_reactions` | reported | `product: <product name, e.g. GINKGO>` |  |
| `connector.openfda.drug_event` | reported | `search: <openFDA query>` |  |
| `tcmdb.relations drug_adverse_event` | per_row | `kind: drug_adverse_event; subject: <drug>` |  |
| `connector.drugcentral.faers` | reported | `struct_id: <DrugCentral struct id>` |  |
| `tcmdb.query faers` | reported | `dataset: faers; table: <a table tcmdb.tables lists>` | FAERS quarterly files, for analyses openFDA does not give |

**A drug's label: warnings, contraindications, populations** (`labels`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.openfda.drug_label` | curated | `search: <openFDA label query>` |  |
| `connector.dailymed.spls` | curated | `drug_name: <drug>` |  |
| `connector.drugcentral.indications` | curated | `struct_id: <DrugCentral struct id>` |  |

Reconcile: Report every signal found, with its kind: a report count is not an incidence. Name the population each caution concerns (pregnancy, children, liver or kidney disease).

Deciding between explanations: research_inquiry scaffold `safety_signal`.

Cautions: A safety signal does not wait for acceptance: report it now, with what would confirm or refute it. A missing safety record or an absence of reports is not evidence of safety.

Next: [Herb–drug interactions](#herb_drug_interaction), [Exposure and metabolism: absorbed constituents, metabolism, kinetics](#exposure_metabolism)


<a id="herb_drug_interaction"></a>

### Herb–drug interactions · 中药与西药相互作用

`herb_drug_interaction`: Whether a herb (or its constituents) interacts with a drug: recorded interactions, shared metabolising enzymes and transporters, pharmacogenes, and co-reported adverse events.

**Recorded herb–drug interactions** (`recorded`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations herb_drug_interaction` | per_row | `kind: herb_drug_interaction; subject: <herb>` |  |
| `skill.assess-tcm-safety` | recorded | `subject: <herb or formula>; co_administered: ["<drug>"]` | with the drugs taken |

**The drug: its identity, label and pharmacogenes** (`the_drug`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.rxnav.rxcui_by_name` | reference | `name: <drug>` |  |
| `connector.rxnav.approximate` | reference | `term: <drug name as written>` |  |
| `connector.rxnav.properties` | reference | `rxcui: <RxCUI>` |  |
| `connector.rxnav.related` | reference | `rxcui: <RxCUI>` |  |
| `connector.rxnav.drug_classes` | reference | `rxcui: <RxCUI>` |  |
| `connector.nlm_clinical_tables.rxterms` | reference | `terms: <drug>` |  |
| `connector.dailymed.drug_names` | reference | `drug_name: <drug>` |  |
| `connector.openfda.drug_label` | curated | `search: openfda.generic_name:"<drug>"` | the label's drug-interaction section |
| `connector.drugcentral.structure` | reference | `struct_id: <DrugCentral struct id>` |  |
| `connector.drugcentral.structure_by_inchikey` | reference | `inchikey: <InChIKey>` |  |
| `connector.drugcentral.identifiers` | reference | `struct_id: <DrugCentral struct id>` |  |
| `connector.drugcentral.atc` | reference | `struct_id: <DrugCentral struct id>` |  |
| `tcmdb.relations drug_pharmacogene` | per_row | `kind: drug_pharmacogene; subject: <drug>` |  |
| `connector.cpic.pairs_for_drug` | curated | `drug: <drug, lower case>` |  |

**The enzymes and transporters both act on** (`shared_enzymes`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations ingredient_target` | per_row | `kind: ingredient_target; subject: <compound>` |  |
| `tcmdb.relations drug_target` | per_row | `kind: drug_target; subject: <drug>` |  |
| `connector.drugcentral.target_activities` | measured | `accession: <UniProt accession of the enzyme, e.g. P08684 for CYP3A4>` |  |
| `tcmdb.relations enzyme_reaction` | per_row | `kind: enzyme_reaction; subject: <UniProt accession of the enzyme>` |  |

**Adverse events reported with both** (`co_reports`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.openfda.herbal_event_coreported_drugs` | reported | `product: <product name>` |  |
| `connector.openfda.herbal_event_reactions` | reported | `product: <product name>` |  |

Reconcile: Keep recorded interactions (with their evidence level) apart from mechanisms inferred from shared enzymes, and both apart from co-reports.

Deciding between explanations: research_inquiry scaffold `herb_drug_interaction`.

Cautions: Shared enzymes suggest an interaction; they do not show one at clinical doses. Being reported together is not interacting.

Next: [Variants and pharmacogenomics](#variants_pharmacogenomics), [Exposure and metabolism: absorbed constituents, metabolism, kinetics](#exposure_metabolism)


<a id="exposure_metabolism"></a>

### Exposure and metabolism: absorbed constituents, metabolism, kinetics · 暴露与代谢：入血成分、代谢与药代

`exposure_metabolism`: Which constituents reach the blood and at what concentrations, how they are metabolised (enzymes, reactions, kinetics), and whether the concentrations reached are those at which they act.

**Constituents detected in blood after dosing** (`absorbed`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.dcabm_tcm.herb_blood` | measured | `names: ["<pinyin, upper case, e.g. HUANG QI>"]` |  |
| `connector.dcabm_tcm.prescription_blood` | measured | `names: ["<pinyin, upper case, e.g. HUANG QI>"]` |  |
| `connector.dcabm_tcm.ingredient` | measured | `cids: ["<PubChem CID>"]` | a blood constituent: the prescriptions and herbs it was detected for |
| `tcmdb.query dcabm` | measured | `dataset: dcabm; table: <a table tcmdb.tables lists>` | structures of every constituent DCABM-TCM detected in blood |

**The enzymes, reactions and kinetics** (`metabolism`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations reaction_participant` | per_row | `kind: reaction_participant; object: <compound>` |  |
| `tcmdb.relations enzyme_reaction` | per_row | `kind: enzyme_reaction; subject: <enzyme UniProt accession>` |  |
| `connector.rhea.search` | curated | `query: <compound or enzyme>` |  |
| `connector.sabio_rk.kinlaw_search` | measured | `q: <enzyme or substrate query>` |  |
| `connector.sabio_rk.kinlaw_entry` | measured | `entry_id: <SABIO entry id>` |  |
| `connector.sabio_rk.enzyme_by_ec` | reference | `ec: <EC number>` |  |
| `connector.sabio_rk.protein` | reference | `uniprot_id: <UniProt accession>` |  |
| `connector.sabio_rk.compound` | reference | `compound_id: <SABIO compound id>` |  |
| `connector.metanetx.chem` | reference | `mnx_id: <MNXM…>` |  |
| `connector.phytohub.entry` | curated | `phub_id: <PHUB…>` | dietary precursors and their metabolites |

**Studies that measured it in people** (`in_people`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.metabolomics_workbench.studies_by_refmet_name` | data | `refmet_name: <RefMet name>` |  |
| `tcmdb.query nhanes` | data | `dataset: nhanes; table: <a table tcmdb.tables lists>` | NHANES participant-level survey data: an association must be estimated with the survey weights and design |

**Kinetics, and whether the concentrations reached are active ones** (`models`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `study.exposure_screen` | analysis | `exposures: [{"molecule": "<compound>", "species": "<species>", "site": "plasma", "concentration": {"qualifier": "measured", "value": "<value>", "unit": "<unit>"}}]; assays: [{"molecule": "<compound>", "target": "<target>", "endpoint": "<IC50>", "result": {"qualifier": "measured", "value": "<value>", "unit": "<unit>"}}]; site: plasma` | concentrations reached against those at which each activity was measured |
| `native.pk_one_compartment` | analysis | `dose_mg: <dose>; volume_l: <V>; half_life_h: <t½>` |  |
| `native.half_life_from_levels` | analysis | `c1: <C1>; t1_h: <t1>; c2: <C2>; t2_h: <t2>` |  |
| `job.pipeline.admet` | predicted | `molecules: <SMILES or file>` | ADMET models (a job) |

Reconcile: Compare the concentration reached (with its unit, species and the dose that gave it) with the concentration at which each activity was measured; say when either is missing.

Deciding between explanations: research_inquiry scaffold `exposure`.

Cautions: A constituent never detected in blood may still act (in the gut, or through its metabolites), but that claim then needs its own evidence.

Next: [What a compound acts on](#compound_targets), [Herb–drug interactions](#herb_drug_interaction)


<a id="structure_binding"></a>

### Structures, binding sites and docking · 结构与结合：结构、口袋与对接

`structure_binding`: Which structures exist for a target (experimental or predicted), where ligands bind, and whether a compound could fit, as a hypothesis.

**Experimental structures and their ligands** (`experimental`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.pdbe.best_structures` | measured | `accession: <UniProt accession>` |  |
| `connector.pdbe.entry_summary` | measured | `pdb_id: <PDB id>` |  |
| `connector.pdbe.molecules` | measured | `pdb_id: <PDB id>` |  |
| `connector.pdbe.uniprot_mapping` | reference | `pdb_id: <PDB id>` |  |
| `connector.rcsb.entry` | measured | `pdb_id: <PDB id>` |  |
| `connector.rcsb.polymer_entity` | measured | `pdb_id: <PDB id>; entity_id: <entity id>` |  |
| `connector.klifs.structures` | measured | `kinase_ids: <KLIFS kinase ids>` | kinase structures with their pocket conformations |
| `connector.klifs.ligands` | measured | `kinase_ids: <KLIFS kinase ids>` |  |
| `connector.klifs.interaction_fingerprint` | measured | `structure_id: <KLIFS structure id>` |  |
| `connector.klifs.kinase_names` | reference | `group: <kinase group, e.g. TK>` |  |
| `connector.klifs.drugs` | curated |  |  |
| `connector.gpcrdb.structure` | measured | `pdb_code: <PDB code>` |  |
| `connector.gpcrdb.protein` | curated | `entry_name: <UniProt entry name>` |  |
| `connector.gpcrdb.mutants` | measured | `entry_name: <UniProt entry name>` | mutagenesis data on ligand binding |
| `native.parse_pdb` | analysis | `text: <PDB text>` |  |

**Predicted structures** (`predicted`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.alphafold.prediction` | predicted | `accession: <UniProt accession>` |  |
| `connector.alphafold.uniprot_summary` | predicted | `accession: <UniProt accession>` |  |
| `job.pipeline.fold` | predicted | `source: <sequence or FASTA>` | a fold on the runner (a job) |
| `skill.predict-protein-structure` | predicted | `sequences: <sequences>` | the governed structure prediction skill (a job) |

**Whether a compound could fit (a job)** (`docking`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `job.pipeline.dock` | predicted | `receptor: <PDB:id, UniProt:accession or file>; ligands: <SMILES or file>` |  |
| `skill.dock-ligands` | predicted | `receptor: <receptor>; ligands: <SMILES or file>` | the governed docking skill (a job) |

Reconcile: Prefer an experimental structure with a ligand bound at the site docked into; a docking score ranks poses against one receptor, it is not an affinity.

Deciding between explanations: research_inquiry scaffold `target_engagement`.

Cautions: Docking poses and predicted structures are hypotheses: binding is shown only by an assay.

Next: [What a compound acts on](#compound_targets)


<a id="variants_pharmacogenomics"></a>

### Variants and pharmacogenomics · 变异与药物基因组

`variants_pharmacogenomics`: What a genetic variant does (frequency, predicted and measured effect, clinical significance) and how genes change a drug's response.

**Its identity, frequency and annotations** (`the_variant`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.myvariant.variant` | curated | `hgvs: <chr:g.posRef>Alt>` |  |
| `connector.myvariant.query` | curated | `q: <rsid or gene>` |  |
| `connector.gnomad.variant` | measured | `variant_id: <chrom-pos-ref-alt>` |  |
| `connector.ensembl.vep_hgvs` | predicted | `hgvs: <HGVS>` |  |
| `native.parse_hgvs` | analysis | `hgvs: <HGVS>` |  |
| `native.normalise_variant` | analysis | `chrom: <chrom>; pos: <position>; ref: <ref>; alt: <alt>` |  |
| `native.annotate_coding_variant` | analysis | `cds: <coding sequence>; position: <c. position>; ref: <ref>; alt: <alt>` |  |

**Measured effects of variants in the gene** (`measured_effect`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.mavedb.search_score_sets` | measured | `text: <gene>` |  |
| `connector.mavedb.gene_score_sets` | measured | `symbol: <gene symbol>` |  |
| `connector.mavedb.score_set` | measured | `urn: <urn:mavedb:…>` |  |
| `connector.mavedb.scores` | measured | `urn: <urn:mavedb:…>` |  |
| `connector.mavedb.licenses` | reference |  |  |

**Gene–drug pairs, alleles and prescribing guidance** (`drug_response`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations drug_pharmacogene` | per_row | `kind: drug_pharmacogene; object: <gene symbol>` |  |
| `tcmdb.relations variant_drug` | per_row | `kind: variant_drug; subject: <rsid or variant>` |  |
| `tcmdb.relations allele_variant` | per_row | `kind: allele_variant; subject: <allele, e.g. CYP2C9*3>` |  |
| `connector.cpic.pairs_for_gene` | curated | `gene: <gene symbol>` |  |
| `connector.cpic.alleles` | curated | `gene: <gene symbol>` |  |
| `connector.cpic.recommendations` | curated | `drug: <drug, lower case>` |  |
| `connector.cpic.gene` | reference | `gene: <gene symbol>` |  |
| `connector.cpic.drug` | reference | `drug: <drug, lower case>` |  |
| `connector.cpic.pairs_by_level` | curated | `level: A` |  |
| `connector.cpic.report_files` | reference | `type: PAIR` |  |

**Clinical interpretations of somatic variants** (`somatic`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.civic.gene` | curated | `gene: <gene symbol>` |  |
| `connector.civic.variant` | curated | `variant_id: <CIViC variant id>` |  |

**Genotypes you have** (`your_data`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `native.parse_vcf` | analysis | `text: <VCF text>` |  |
| `native.allele_frequencies` | analysis | `genotypes: ["<genotype>"]` |  |

Reconcile: Keep population frequency, predicted consequence, measured effect and clinical guidance apart; a CPIC level says how actionable a gene–drug pair is.

Cautions: A predicted consequence (VEP) is not a measured effect, and guidance applies to the drug it names.

Next: [Herb–drug interactions](#herb_drug_interaction), [Genes, targets and diseases](#disease_links)


<a id="omics_datasets"></a>

### Omics data: expression, single-cell, proteomics and metabolomics · 组学数据：表达、单细胞、蛋白质组与代谢组

`omics_datasets`: Which public datasets bear on the question (bulk and single-cell expression, proteomics, metabolomics, samples of a medicinal species) and how to analyse one here.

**Sequencing runs, samples and studies** (`sequencing`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.ena.search` | data | `result: read_run; query: <ENA query>` |  |
| `connector.ena.filereport` | data | `accession: <PRJ… or SRR…>` |  |
| `connector.ncbi_eutils.esearch` | data | `db: gds; term: <GEO query>` |  |
| `connector.ncbi_eutils.esummary` | data | `db: gds; id: <GEO ids>` |  |
| `connector.ncbi_eutils.efetch` | data | `db: <database>; id: <ids>` |  |
| `connector.ncbi_eutils.einfo` | reference |  |  |
| `connector.biosamples.by_organism` | data | `organism: <Latin binomial>` | samples of a medicinal species |
| `connector.biosamples.search` | data | `text: <text>` |  |
| `connector.biosamples.sample` | data | `accession: <SAM…>` |  |
| `connector.biostudies.search` | data | `query: <text>` |  |
| `connector.biostudies.study` | data | `accession: <E-MTAB-… or S-…>` |  |
| `connector.encode.experiment` | data | `accession: <ENCSR…>` |  |
| `connector.ncbi_datasets.genome_report` | data | `taxon: <taxon>` |  |
| `tcmdb.query archs4` | data | `dataset: archs4; table: <a table tcmdb.tables lists>` | ARCHS4: about a million uniformly processed RNA-seq samples |
| `tcmdb.query lincs_l1000` | data | `dataset: lincs_l1000; table: <a table tcmdb.tables lists>` | LINCS L1000: which perturbagen, dose, time and cell line each signature is |

**Single-cell and spatial atlases** (`single_cell`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.cellxgene.collection` | data | `collection_id: <collection id>` |  |
| `connector.hca_azul.project` | data | `project_id: <HCA project UUID>` |  |
| `connector.hca_azul.file` | data | `file_id: <file UUID>` |  |
| `connector.hubmap_search.datasets_by_organ` | data | `organ_code: <organ code>` |  |
| `connector.hubmap_search.datasets_by_type` | data | `dataset_type: <e.g. RNAseq>` |  |
| `connector.hubmap_entity.entity` | data | `entity_id: <HuBMAP id>` |  |
| `connector.hubmap_portal.entity` | data | `entity_type: dataset; uuid: <uuid>` |  |
| `connector.hubmap_portal.organ` | reference | `organ: <organ slug>` |  |
| `tcmdb.relations dataset_tissue` | per_row | `kind: dataset_tissue; object: <tissue>` |  |
| `tcmdb.query scperturb` | data | `dataset: scperturb; table: <a table tcmdb.tables lists>` | scPerturb: harmonised single-cell perturbation datasets |

**Proteomics and metabolomics studies** (`proteomics_metabolomics`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.pride.search` | data | `keyword: <text>` |  |
| `connector.pride.project` | data | `accession: <PXD…>` |  |
| `connector.proteomicsdb.protein` | reference | `uniprot: <UniProt accession>` |  |
| `connector.proteomicsdb.dose_response_datasets` | data |  |  |
| `connector.metabolights.studies` | data |  |  |
| `connector.metabolights.study` | data | `study_id: <MTBLS…>` |  |
| `connector.metabolomics_workbench.study_summary` | data | `study_id: <ST…>` |  |
| `connector.metabolomics_workbench.study_analyses` | data | `study_id: <ST…>` |  |
| `connector.metabolomics_workbench.study_metabolites` | data | `study_id: <ST…>` |  |
| `connector.metabolomics_workbench.metstat` | data | `analysis_type: <e.g. LCMS, or empty>; polarity: <positive, negative or empty>; chromatography: <e.g. HILIC, or empty>; species: <e.g. Human, or empty>; sample_source: <e.g. Blood, or empty>; disease: <disease, or empty>; kegg_id: <KEGG compound id, or empty>; refmet_name: <RefMet name, or empty>` | studies by species, sample source, disease or metabolite (an empty string for a slot not used) |

**Datasets deposited in general repositories** (`deposits`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.figshare.article` | data | `article_id: <figshare id>` |  |
| `connector.figshare.article_files` | data | `article_id: <figshare id>` |  |
| `connector.huggingface.dataset` | data | `repo_id: <owner/name>` |  |
| `connector.huggingface.dataset_tree` | data | `repo_id: <owner/name>` |  |
| `connector.hf_datasets_server.splits` | data | `repo_id: <owner/name>` |  |
| `connector.hf_datasets_server.rows` | data | `repo_id: <owner/name>` |  |
| `connector.ebi_search.search` | data | `domain: <EBI Search domain>; query: <text>` |  |
| `connector.ebi_search.domains` | reference |  |  |

**Analyse a dataset here (jobs, with approval)** (`analysis`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `job.pipeline.rnaseq` | analysis | `samples: <sample sheet>` |  |
| `job.pipeline.scrna` | analysis | `source: <h5ad or 10x matrix>` |  |
| `skill.rnaseq-differential-expression` | analysis | `sample_sheet: <sample sheet>` | the governed FASTQ-to-DESeq2 skill (a job) |
| `skill.scrna-cell-atlas` | analysis | `source: <count matrices>` | the governed single-cell atlas skill (a job) |
| `native.cpm` | analysis | `counts: ["<count>"]` |  |
| `native.tpm` | analysis | `counts: ["<count>"]; lengths_bp: ["<length>"]` |  |
| `native.log2_fold_change` | analysis | `treated: ["<value>"]; control: ["<value>"]` |  |
| `native.welch_t_test` | analysis | `x: ["<value>"]; y: ["<value>"]` |  |
| `native.correlation` | analysis | `x: ["<value>"]; y: ["<value>"]` |  |

Reconcile: Cite each dataset by accession with its organism, tissue, design and licence; a reanalysis inherits the dataset's design.

Cautions: A dataset is data, not a finding: report results only from an analysis that was run, with its design.

Next: [Pathways, enrichment and interaction networks](#pathways_networks)


<a id="spectra_identification"></a>

### Spectra and compound identification · 谱图与成分鉴定

`spectra_identification`: Which compound a mass or NMR spectrum matches, which reference spectra exist for a compound, and which public datasets hold spectra of a herb.

**Reference spectra of a compound** (`reference_spectra`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations compound_spectrum` | per_row | `kind: compound_spectrum; subject: <compound or InChIKey>` |  |
| `connector.massbank.records_search` | measured | `inchikey: <InChIKey>` |  |
| `connector.massbank.records` | measured | `inchikey: <InChIKey>` |  |
| `connector.massbank.records_search_name` | measured | `name: <compound>` |  |
| `connector.massbank.record` | measured | `accession: <MSBNK-…>` |  |
| `connector.massbank.record_text` | measured | `accession: <MSBNK-…>` | with its LICENSE line |
| `connector.massbank.record_simple` | measured | `accession: <MSBNK-…>` | accession, title, peaks and SMILES only |
| `connector.massbank.record_jsonld` | reference | `accession: <MSBNK-…>` | the record's licence URL and citation |
| `connector.massbank.convert` | data | `accessions: ["<MSBNK-…>"]` | records as MSP or JSON, for a local library search |
| `connector.gnps.spectrum` | measured | `spectrum_id: <CCMSLIB…>` |  |
| `connector.gnps.libraries` | reference |  |  |
| `connector.massbank.browse_options` | reference |  | contributors, instruments, MS types and ion modes with their record counts |
| `connector.massbank.metadata` | reference |  | the data version and counts, to cite |
| `connector.massbank.count` | reference |  |  |
| `connector.massbank.status` | reference |  |  |
| `connector.gnps_usi.spectrum` | measured | `usi: <mzspec:…>` |  |

**Library matches for a measured spectrum** (`match`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.massbank.peak_search` | analysis | `peak_list: <mz;intensity,mz;intensity>` |  |
| `native.peptide_mass` | analysis | `sequence: <peptide>` | for a peptide |

**Public raw spectra datasets** (`datasets`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.massive.dataset_search` | data | `text: <herb or compound>` |  |
| `connector.massive.dataset` | data | `accession: <MSV…>` |  |
| `connector.gnps_explorer.dataset_files` | data | `accession: <MSV…>` |  |
| `connector.metabolights.study` | data | `study_id: <MTBLS…>` |  |

**Standard names and formulas for what was found** (`names`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.metabolomics_workbench.refmet_match` | reference | `name: <metabolite>` |  |
| `connector.metabolomics_workbench.refmet` | reference | `refmet_name: <RefMet name>` |  |
| `connector.metabolomics_workbench.refmet_by_formula` | reference | `formula: <molecular formula>` |  |
| `connector.metabolomics_workbench.compound_by_pubchem_cid` | reference | `cid: <PubChem CID>` |  |

Reconcile: Give each identification with its confidence level and the library entry it rests on.

Cautions: A library match is a putative identification until it is confirmed against a reference standard.

Next: [Compound identity, structure and origin](#compound_identity)


<a id="cell_models_cancer"></a>

### Cell models, drug sensitivity and cancer genomics · 细胞模型、药敏与肿瘤基因组

`cell_models_cancer`: Which cell lines model a disease, how they respond to a compound across screens, which genes they depend on, and what tumour cohorts say about a gene.

**The cell line: identity, disease and known problems** (`cell_lines`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.cellosaurus.search` | reference | `q: <cell line>` |  |
| `connector.cellosaurus.cell_line` | curated | `ac: <CVCL_…>` |  |
| `connector.cellosaurus.cell_line_by_accession` | reference | `ac: <CVCL_…>` |  |
| `connector.cellosaurus.cell_line_by_xref` | reference | `xref: <e.g. ACH-000739>` |  |
| `connector.cellosaurus.release_info` | reference |  |  |
| `tcmdb.relations cell_line_disease` | per_row | `kind: cell_line_disease; subject: <cell line>` |  |

**Dose–response of a compound across screens** (`sensitivity`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.pharmacodb.compound` | measured | `compound: <compound>` |  |
| `connector.pharmacodb.experiments` | measured | `compound: <compound>` |  |
| `connector.pharmacodb.experiments_in_cell_line` | measured | `cell_line: <cell line>; compound: <compound>` |  |
| `connector.pharmacodb.biomarkers` | associated | `compound: <compound>; gene: <gene>` |  |
| `connector.pharmacodb.search` | reference | `query: <name>` |  |
| `connector.pharmacodb.cell_line` | reference | `cell_line: <cell line>` |  |
| `connector.pharmacodb.dataset_stats` | reference |  |  |
| `tcmdb.relations compound_target` | per_row | `kind: compound_target; subject: <compound>; sources: ["gdsc"]` | GDSC's screened compounds and their targets |
| `connector.proteomicsdb.curves` | measured | `protein_id: <ProteomicsDB protein id>` | Kinobeads dose–response curves |
| `tcmdb.query drugcomb` | measured | `dataset: drugcomb; table: <a table tcmdb.tables lists>` | DrugComb: combination screens with synergy scores |

**The genes the cells depend on (CRISPR screens)** (`dependencies`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations screen_gene` | per_row | `kind: screen_gene; object: <gene symbol>` |  |
| `tcmdb.relations drug_target` | per_row | `kind: drug_target; subject: <drug>; sources: ["depmap"]` | DepMap's PRISM drugs and their targets |

**A gene in tumour cohorts** (`tumour_genomics`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.cbioportal.gene` | reference | `gene: <gene symbol>` |  |
| `connector.cbioportal.studies` | data |  |  |
| `connector.cbioportal.study` | data | `study_id: <study id>` |  |
| `connector.cbioportal.molecular_profiles` | data | `study_id: <study id>` |  |
| `connector.gdc.projects` | data |  |  |
| `connector.gdc.project` | data | `project_id: <e.g. TCGA-LUAD>` |  |
| `connector.gdc.cases` | data | `filters: <GDC filter JSON>` |  |
| `connector.gdc.status` | reference |  |  |
| `connector.civic.gene` | curated | `gene: <gene symbol>` |  |

Reconcile: Report each screen's own measure (IC50, AUC, AAC) with the screen; screens disagree on the same cell line and compound, so say which.

Cautions: Sensitivity of cells at concentrations no tissue reaches is not an anticancer effect. Check a cell line for misidentification (Cellosaurus) before citing it.

Next: [Combinations and synergy](#combination_effects), [What a compound acts on](#compound_targets), [Exposure and metabolism: absorbed constituents, metabolism, kinetics](#exposure_metabolism)


<a id="immunology"></a>

### Immunology: epitopes, allergens and repertoires · 免疫：表位、过敏原与免疫组库

`immunology`: Which epitopes of an antigen (plant allergens included) are known and how they were assayed, which receptors recognise them, and which immune repertoire studies bear on a condition.

**Epitopes and their assays** (`epitopes`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations epitope_antigen` | per_row | `kind: epitope_antigen; object: <UniProt accession>` |  |
| `tcmdb.relations epitope_assay` | per_row | `kind: epitope_assay; subject: <epitope>` |  |
| `tcmdb.relations receptor_epitope` | per_row | `kind: receptor_epitope; object: <epitope>` |  |
| `connector.iedb.antigens` | curated | `name: <antigen name pattern>` |  |
| `connector.iedb.epitopes_by_organism` | curated | `taxon_id: <NCBI taxon id>` |  |
| `connector.iedb.epitopes_by_sequence` | curated | `sequence: <peptide>` |  |
| `connector.iedb.epitope` | curated | `structure_id: <IEDB structure id>` |  |
| `connector.iedb.epitope_summary` | curated | `structure_id: <IEDB structure id>` |  |
| `connector.iedb.tcell_assays` | measured | `name: <antigen name pattern>` |  |
| `connector.iedb.tcell_by_antigen` | measured | `accession: <UniProt accession>` |  |
| `connector.iedb.tcell_by_molecule` | measured | `name: <compound>` | non-peptidic epitopes: drugs, haptens, natural products |
| `connector.iedb.bcell_by_sequence` | measured | `sequence: <peptide>` |  |
| `connector.iedb.mhc_by_sequence` | measured | `sequence: <peptide>` |  |
| `connector.iedb.tcr_by_epitope` | curated | `sequence: <peptide>` |  |
| `connector.iedb.bcr_by_cdr3` | curated | `cdr3: <heavy-chain CDR3>` |  |
| `connector.iedb.reference_by_pmid` | curated | `pmid: <PMID>` |  |
| `connector.bv_brc.epitopes_by_taxon` | curated | `taxon_id: <NCBI taxon id>` | redistributed from IEDB: count each epitope once |

**Immune repertoire studies** (`repertoires`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.ireceptor_adc.studies_by_diagnosis` | data | `diagnosis: <condition>` |  |
| `connector.ireceptor_adc.repertoires_by_study` | data | `study_id: <BioProject accession>` |  |
| `connector.ireceptor_adc.repertoires` | data |  |  |
| `connector.ireceptor_adc.rearrangements` | data | `repertoire_id: <repertoire id>` |  |
| `connector.ireceptor_adc.rearrangements_by_junction` | data | `junction_aa: <CDR3>; repertoire_id: <repertoire id>` |  |
| `connector.ireceptor_adc.rearrangement_count` | data | `repertoire_id: <repertoire id>` |  |
| `connector.ireceptor_adc.study_counts` | data |  |  |
| `connector.ireceptor_adc.info` | reference |  |  |
| `connector.vdjserver_adc.studies_by_diagnosis` | data | `diagnosis: <condition>` |  |
| `connector.vdjserver_adc.repertoires_by_study` | data | `study_id: <BioProject accession>` |  |
| `connector.vdjserver_adc.repertoires` | data |  |  |
| `connector.vdjserver_adc.rearrangements` | data | `repertoire_id: <repertoire id>` |  |
| `connector.vdjserver_adc.rearrangement_count` | data | `repertoire_id: <repertoire id>` |  |
| `connector.vdjserver_adc.rearrangements_by_junction` | data | `junction_aa: <CDR3>; repertoire_id: <repertoire id>` |  |
| `connector.vdjserver_adc.study_counts` | data |  |  |
| `connector.vdjserver_adc.info` | reference |  |  |

**Immune gene lists** (`immune_genes`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations gene_set_member` | per_row | `kind: gene_set_member; object: <gene symbol>; sources: ["immport"]` | ImmPort's immune gene lists |

Reconcile: Count an epitope once across IEDB and BV-BRC; keep positive and negative assays apart and give the host species.

Cautions: An epitope assayed positive in one host and one assay is not an allergy in people.

Next: [Genes, targets and diseases](#disease_links), [Safety: toxicity, adverse events and contraindications](#safety)


<a id="microbiome"></a>

### Gut microbes and other microorganisms · 肠道菌群与微生物

`microbiome`: How gut microbes relate to a compound or herb (which microbes make or transform which metabolites, which host genes they affect), what metagenomics studies show, and what is known about a microbe's genomes, resistance and virulence.

**Microbes, metabolites and the host genes they affect** (`microbe_metabolite`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations organism_compound` | per_row | `kind: organism_compound; subject: <microbe>; sources: ["gutmgene"]` | gutMGene's microbe–metabolite rows |
| `tcmdb.relations regulation` | per_row | `kind: regulation; subject: <microbe or metabolite>; sources: ["gutmgene"]` | gutMGene's microbe and metabolite effects on host genes |
| `connector.gutmgene.microbe_metabolite` | curated | `index_id: <index id>; microbe: <microbe>; metabolite: <metabolite>; species: <host species>` | the evidence rows of one association the hub returned |
| `connector.gutmgene.microbe_gene` | curated | `index_id: <index id>; microbe: <microbe>; gene: <gene>; alteration: <alteration>; species: <host species>` |  |
| `connector.gutmgene.metabolite_gene` | curated | `index_id: <index id>; metabolite: <metabolite>; gene: <gene>; alteration: <alteration>; species: <host species>` |  |

**Metagenomics studies and their analyses** (`metagenomics`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.mgnify.studies` | data | `search: <text>` |  |
| `connector.mgnify.studies_by_biome` | data | `biome_lineage: root:Host-associated:Human:Digestive system` |  |
| `connector.mgnify.study` | data | `accession: <MGYS…>` |  |
| `connector.mgnify.study_analyses` | data | `accession: <MGYS…>` |  |
| `connector.mgnify.study_samples` | data | `accession: <MGYS…>` |  |
| `connector.mgnify.analysis` | data | `accession: <MGYA…>` |  |
| `connector.mgnify.analysis_annotations` | data | `accession: <MGYA…>` |  |
| `connector.mgnify.biomes` | reference | `biome_lineage: root:Host-associated:Human` |  |
| `connector.mgnify.genome_catalogues` | reference |  |  |
| `connector.mgnify.genomes` | data | `search: <g__Genus>` |  |
| `connector.mgnify.genome` | data | `accession: <MGYG…>` |  |
| `native.diversity` | analysis | `counts: ["<count>"]` |  |

**A microbe's genomes, resistance, virulence and interactions** (`microbe`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.bv_brc.taxonomy` | reference | `taxon_id: <NCBI taxon id>` |  |
| `connector.bv_brc.genomes_by_taxon` | data | `taxon_id: <NCBI taxon id>` |  |
| `connector.bv_brc.genome` | data | `genome_id: <BV-BRC genome id>` |  |
| `connector.bv_brc.genome_amr` | measured | `genome_id: <BV-BRC genome id>` | laboratory-measured and predicted, said per row |
| `connector.bv_brc.amr_lab_by_antibiotic` | measured | `antibiotic: <antibiotic>` |  |
| `connector.bv_brc.specialty_genes` | curated | `genome_id: <BV-BRC genome id>` |  |
| `connector.bv_brc.ppi_by_genome` | curated | `genome_id: <BV-BRC genome id>` |  |
| `connector.bv_brc.antibiotic` | reference | `name: <antibiotic>` |  |
| `connector.mibig.stats` | reference |  |  |

Reconcile: Name the host species and the sample (faeces, mucosa) of every microbiome finding; a correlation between a microbe and a metabolite is not production.

Cautions: Microbe–metabolite links are curated from studies of their own design: check whether each was measured in people or in animals.

Next: [Exposure and metabolism: absorbed constituents, metabolism, kinetics](#exposure_metabolism), [Pathways, enrichment and interaction networks](#pathways_networks)


<a id="imaging_data"></a>

### Imaging data · 影像数据

`imaging_data`: Which public image collections bear on a disease, tissue, gene or compound (radiology, pathology, microscopy screens, neuroimaging), with their licences.

**Cancer radiology and pathology collections** (`cancer_imaging`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.idc.collections` | data |  |  |
| `connector.idc.collection` | data | `collection_id: <collection id>` |  |
| `connector.idc.cohort_counts` | data | `collection_id: <collection id>; modality: <e.g. CT, MR, SM>` |  |
| `connector.idc.clinical_tables` | data | `collection_id: <collection id>` |  |
| `connector.idc.licenses` | reference | `collection_id: <collection id>` |  |
| `connector.idc.analysis_results` | data |  |  |
| `connector.idc.attribute_values` | reference | `attribute: Modality` |  |
| `connector.idc.tables` | reference |  |  |
| `connector.idc.table` | reference | `table: <index table>` |  |
| `connector.idc.sql` | data | `sql: <one read-only SELECT>` |  |
| `connector.idc.stats` | reference |  |  |
| `connector.idc.version` | reference |  |  |
| `tcmdb.relations dataset_disease` | per_row | `kind: dataset_disease; object: <disease>` |  |
| `connector.tcia.collections` | data |  |  |
| `connector.tcia.patients` | data | `collection: <collection>` |  |
| `connector.tcia.modalities` | data | `collection: <collection>` |  |
| `connector.tcia.body_parts` | data | `collection: <collection>` |  |
| `connector.tcia.patient_studies` | data | `collection: <collection>; patient_id: <patient id>` |  |
| `connector.tcia.series` | data | `collection: <collection>; patient_id: <patient id>` |  |
| `connector.tcia.series_size` | data | `series_uid: <series UID>` |  |
| `connector.tcia_cm.collections_search` | data | `text: <text>` |  |
| `connector.tcia_cm.collection` | data | `slug: <collection slug>` |  |
| `connector.tcia_cm.download` | reference | `download_id: <download id>` |  |
| `connector.tcia_cm.licenses` | reference |  |  |

**Microscopy screens by gene, compound or phenotype** (`cell_imaging`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.idr.gene_studies` | data | `symbol: <gene symbol>` |  |
| `connector.idr.compound_studies` | data | `compound: <compound>` |  |
| `connector.idr.search_studies` | data | `key: <key>; value: <value>` |  |
| `connector.idr.screen_study` | data | `screen_id: <screen id>` |  |
| `connector.idr.project_study` | data | `project_id: <project id>` |  |
| `connector.idr.container_values` | data | `container: <screen or project>; key: <key>` |  |
| `connector.idr.containers` | data |  |  |
| `connector.idr.screens` | data |  |  |
| `connector.idr.projects` | data |  |  |
| `tcmdb.relations gene_phenotype` | per_row | `kind: gene_phenotype; subject: <gene>; sources: ["idr"]` | IDR's image-derived phenotypes |
| `connector.biostudies.bioimages_search` | data | `query: <text>` |  |
| `connector.biostudies.study_info` | data | `accession: <S-BIAD…>` |  |

**Neuroimaging datasets** (`neuroimaging`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.openneuro.datasets` | data |  |  |
| `connector.openneuro.dataset` | data | `dataset_id: <ds…>` |  |
| `connector.openneuro.snapshot_files` | data | `dataset_id: <ds…>; tag: <snapshot tag>` |  |

Reconcile: Check each collection's licence before reuse (IDC and TCIA give it per series or per download).

Cautions: A collection is data: findings come only from an analysis of it.

Next: [Omics data: expression, single-cell, proteomics and metabolomics](#omics_datasets)


<a id="literature"></a>

### Literature search and text mining · 文献检索与证据线索

`literature`: What has been published on a question: articles, preprints, citations, the entities they mention, and how to read them as evidence.

**Articles and preprints** (`search`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.europepmc.search` | literature | `query: <query>` |  |
| `connector.ncbi_eutils.esearch` | literature | `db: pubmed; term: <query>` |  |
| `connector.ncbi_eutils.esummary` | literature | `db: pubmed; id: <PMIDs>` |  |
| `connector.crossref.search` | literature | `query: <query>` |  |
| `connector.crossref.work` | literature | `doi: <DOI>` |  |
| `connector.openalex.work` | literature | `work_id: <OpenAlex id or DOI>` |  |
| `connector.biorxiv.details` | literature | `doi: <DOI>` | a preprint: not peer reviewed |
| `connector.europepmc.citations` | literature | `id: <PMID>; source: MED` |  |

**The entities articles mention** (`text_mining`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.pubtator.search` | mentioned | `text: <entities or query>` |  |
| `connector.pubtator.export` | mentioned | `pmids: <PMIDs>` |  |
| `connector.pubtator.autocomplete` | reference | `query: <entity>` |  |
| `connector.europepmc_annotations.by_entity` | mentioned | `entity: <entity>` |  |
| `connector.europepmc_annotations.by_article` | mentioned | `article_ids: <MED:PMID>` |  |

**Passages to cite from an indexed corpus, and reviewed third-party tools (on the runner)** (`governed_reading`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `skill.retrieve-literature-evidence` | literature | `question: <question>; corpus: <indexed corpus>` |  |
| `system.provider_catalog` | reference | `query: <literature, trials, variants …>` | the reviewed ToolUniverse and BioMCP wrappers |
| `system.provider_call` | literature | `provider: <biomcp or tooluniverse>; tool: <tool from system.provider_catalog>` | one reviewed wrapper |

Reconcile: Cite by PMID or DOI, and group hits by study design before drawing anything from them.

Cautions: A hit is a pointer to read, not evidence: nothing is licensed until the study itself has been assessed. A co-mention in text mining is not a finding.

Next: [Efficacy evidence: trials, meta-analyses and studies](#efficacy_evidence)


<a id="ontology_mapping"></a>

### Terms and identifiers across vocabularies · 术语与标识符映射

`ontology_mapping`: How a term or an identifier maps across vocabularies and databases (MeSH, HPO, Disease Ontology, GO, ICD-10, LOINC), and what a prefix or a CURIE resolves to.

**The term in each vocabulary** (`terms`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.ols.search` | reference | `q: <term>` |  |
| `connector.ols.term` | reference | `ontology: <ontology>; obo_id: <ID>` |  |
| `connector.ols.ontologies` | reference |  |  |
| `connector.mesh.descriptor` | reference | `label: <term>` |  |
| `connector.mesh.term` | reference | `label: <term>` |  |
| `connector.mesh.details` | reference | `descriptor: <D…>` |  |
| `tcmdb.query mesh` | reference | `dataset: mesh; table: <a table tcmdb.tables lists>` | MeSH descriptors, for offline use |
| `connector.hpo.search` | reference | `q: <term>` |  |
| `connector.disease_ontology.metadata` | reference | `doid: <DOID:…>` |  |
| `connector.quickgo.go_search` | reference | `query: <term>` |  |
| `connector.quickgo.go_term` | reference | `go_id: <GO:…>` |  |
| `connector.nlm_clinical_tables.icd10cm` | reference | `terms: <term>` |  |
| `connector.nlm_clinical_tables.loinc` | reference | `terms: <test>` |  |
| `connector.nlm_clinical_tables.hcpcs` | reference | `terms: <term>` |  |
| `connector.monarch.entity` | reference | `id: <CURIE>` |  |
| `native.parse_obo` | analysis | `text: <OBO text>` |  |

**What a prefix or a CURIE resolves to, and identifier conversion** (`identifiers`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.bioregistry.reference` | reference | `prefix: <prefix>; identifier: <id>` |  |
| `connector.bioregistry.registry` | reference | `prefix: <prefix>` |  |
| `connector.identifiers_org.resolve` | reference | `curie: <prefix:id>` |  |
| `connector.wikidata_sparql.sparql` | reference | `query: <SPARQL SELECT>` | one read-only SPARQL query: the identifiers Wikidata links to an entity, or LOTUS by compound class |
| `connector.gprofiler.convert` | reference | `query: <ids, space-separated>; target: ENSG` |  |
| `connector.string.map_ids` | reference | `identifiers: <ids>` |  |
| `connector.hgnc.search` | reference | `query: <alias>` |  |
| `connector.hgnc.hgnc_id` | reference | `hgnc_id: <HGNC:…>` |  |
| `connector.ensembl.id_lookup` | reference | `id: <Ensembl id>` |  |
| `connector.ensembl.ping` | reference |  |  |
| `connector.mygene.gene` | reference | `id: <Entrez or Ensembl id>` |  |
| `connector.ncbi_datasets.gene_by_id` | reference | `gene_id: <NCBI Gene id>` |  |
| `connector.ucsc.genomes` | reference |  |  |
| `connector.ucsc.chromosomes` | reference | `genome: hg38` |  |

**TCM names to standard names** (`tcm_terms`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `skill.normalize-tcm-entities` | reference | `names: ["<name>"]` |  |
| `native.tcm_lookup` | recorded | `name: <name>` |  |

Reconcile: Give a mapping with its vocabulary and version; a cross-reference can be broader or narrower than the term.

Cautions: A mapping is a correspondence between vocabularies, not a finding.


<a id="combination_effects"></a>

### Combinations and synergy · 配伍与联合作用

`combination_effects`: Whether two herbs, two compounds, or a herb and a drug act together beyond what each does alone, judged against a stated reference model.

**Measured combination screens** (`screens`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.query drugcomb` | measured | `dataset: drugcomb; table: <a table tcmdb.tables lists>` | DrugComb: synergy scores from measured dose–response matrices of drug pairs |
| `connector.pharmacodb.experiments_in_cell_line` | measured | `cell_line: <cell line>; compound: <compound>` | each drug alone, the baseline a combination is judged against |

**A combination analysed against a stated reference model** (`analysis`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `study.combination` | analysis | `cells: [{"dose_a": "<dose>", "dose_b": "<dose>", "effect": ["<fraction>"]}]; primary: <bliss, hsa or loewe>` |  |

**Recorded herb pairs and incompatibilities** (`recorded_pairs`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `native.tcm_compatibility` | recorded | `herbs: ["<herb>", "<herb>"]` |  |
| `tcmdb.formulas` | recorded | `query: <herb> <herb>; rank: true` | the formulas that contain both herbs |

Reconcile: Name the reference model (Bliss, Loewe, HSA) and the dose range: synergy at one dose ratio does not generalise.

Deciding between explanations: research_inquiry scaffold `combination`.

Cautions: Synergy in cells is not a benefit in patients, and a recorded herb pair is tradition.

Next: [How a formula might work (network pharmacology)](#formula_mechanism), [Safety: toxicity, adverse events and contraindications](#safety)


<a id="formula_mechanism"></a>

### How a formula might work (network pharmacology) · 方剂作用机制（网络药理）

`formula_mechanism`: A mechanism hypothesis for a formula or a herb in a disease: its herbs, their constituents (those that reach the blood first), their measured and predicted targets, the pathways those fall in against a stated background, and the overlap with the disease's genes.

**The formula's herbs, in its stated version** (`composition`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `native.tcm_formula` | recorded | `name: <formula>` |  |
| `tcmdb.consensus formula_herb` | per_row | `kind: formula_herb; subject: <formula>` |  |

**Each herb's constituents, those detected in blood first** (`constituents`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.dcabm_tcm.prescription_blood` | measured | `names: ["<pinyin, upper case, e.g. HUANG QI>"]` |  |
| `tcmdb.consensus herb_ingredient` | per_row | `kind: herb_ingredient; subject: <herb>` |  |

**The constituents' targets, measured apart from predicted** (`targets`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.consensus compound_target` | per_row | `kind: compound_target; subject: <compound>` |  |
| `tcmdb.consensus ingredient_target` | per_row | `kind: ingredient_target; subject: <compound>` |  |
| `tcmdb.relations herb_target` | per_row | `kind: herb_target; subject: <herb>` |  |

**Pathway enrichment against a stated background** (`pathways`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `connector.gprofiler.gost` | analysis | `query: <gene symbols, space-separated>` |  |
| `native.enrichment_analysis` | analysis | `genes: ["<gene>"]; gene_sets: {"<set>": ["<gene>"]}; background: <background size>` |  |

**The disease's genes, and the overlap** (`disease`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `tcmdb.relations target_disease` | per_row | `kind: target_disease; object: <disease>` |  |
| `connector.opentargets.associated_diseases` | aggregated | `ensembl_id: <Ensembl gene id>` |  |

**The whole loop as one recorded run (a job), or the governed seed-corpus analysis** (`recorded_run`)

| Source | Evidence | Arguments | Note |
| --- | --- | --- | --- |
| `job.research.run` | analysis | `question: <question>; formula: <formula>; disease: <disease>` |  |
| `skill.analyze-tcm-network-pharmacology` | predicted | `formula_name: <formula>` |  |

Reconcile: Carry each target's evidence kind into the pathway step: an enrichment of predicted targets is a hypothesis about the predictions.

Deciding between explanations: research_inquiry scaffold `pathway_signal`.

Cautions: A network generates hypotheses: it predicts, it does not show a mechanism (predicted ≠ measured). A large target set overlaps a disease's genes by chance: give the background and the test.

Next: [What a compound acts on](#compound_targets), [Exposure and metabolism: absorbed constituents, metabolism, kinetics](#exposure_metabolism), [Efficacy evidence: trials, meta-analyses and studies](#efficacy_evidence)

## Datasets no route uses

| Dataset | Why |
| --- | --- |
| `shennong` | generated question-and-answer text for instruction tuning: training data, never evidence |
| `tcm_ladder` | an evaluation benchmark for TCM language models, not a source of facts about herbs or patients |
| `tcmp300` | medicinal-plant image classes for training image recognition; no tool here identifies a herb from an image |

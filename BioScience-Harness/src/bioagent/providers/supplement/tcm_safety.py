"""Live connectors for TCM safety and provenance sources (review of 2026-09-30).

Each was checked from the harness on 2026-10-01. None of these endpoints is documented as
an API; they are the JSON answers the sites' own pages load, used here only for small
per-entity look-ups (never listings or bulk transfers). Bulk work uses the ``tcmdb``
datasets of ``tcmdb.extra.tcm_safety``.

* **MIBiG** (``mibig``): the full annotation of one biosynthetic gene cluster
  (``/repository/<accession.version>/annotations.json``, a static file identical to the
  record in the 4.0 JSON release) and the database statistics. ``/api/v1/repository``
  (every entry at once) is a bulk listing and is not wrapped; ``/api/v1/search`` answers
  400 (a server-side SQL error) and is not wrapped either. robots.txt: the host answers
  ``/robots.txt`` with its single-page app, so no rules are stated.
* **gutMGene v2.0** (``gutmgene``): the evidence rows behind one microbe-metabolite,
  microbe-gene or metabolite-gene association (``POST /browse/getbrowsetable``, as the
  site's browse page sends it). ``robots.txt`` answers 404.
* **PhytoHub** (``phytohub``): one dietary phytochemical or metabolite by PhytoHub id, a
  compound name search, a food source's name, and one structure as SDF (the Rails
  ``.json``/``.sdf`` format responders of its pages). Food-compound and
  precursor-metabolite relations are rendered only as HTML and are not wrapped (no bulk
  download either: PhytoHub is catalogued manual for those). robots.txt has every rule
  commented out.

No licence is stated by gutMGene's or PhytoHub's sites; their descriptions say so.
"""

from __future__ import annotations

from ..public_apis import Operation, PublicSource

__all__ = ["SOURCES", "PENDING"]

_MIBIG_LICENSE = ("CC BY 4.0 (site footer: 'This work is licensed under a Creative Commons "
                  "Attribution 4.0 International License'); cite Zdouc, Blin et al. 2025, "
                  "doi:10.1093/nar/gkae1115")
_GUTMGENE_LICENSE = ("not stated on the site; the gutMGene v2.0 article (NAR 2025, "
                     "PMC11701569) is CC BY-NC 4.0 and the site says it is 'freely "
                     "available'; commercial use unknown")
_PHYTOHUB_LICENSE = ("not stated; the home page says only 'PhytoHub is a freely available "
                     "electronic database'; commercial use unknown")

SOURCES: tuple[PublicSource, ...] = (
    PublicSource(
        "mibig", "MIBiG (repository JSON)", "https://mibig.secondarymetabolites.org",
        "mibig.secondarymetabolites.org", _MIBIG_LICENSE,
        "Minimum Information about a Biosynthetic Gene cluster. `entry` returns one "
        "cluster's full annotation (accession, version, status, loci with evidence, "
        "biosynthetic classes, compounds with structure, bioactivities observed or not, "
        "producing organism with NCBI taxid, genes, references) - the same JSON as the "
        "release archive. `stats` returns entry counts by status, class and phylum. "
        "Undocumented endpoints of the site's own pages (the download page documents only "
        "the bulk files).", "natural-products", (
            Operation("entry", "Full annotation of one BGC by accession.version "
                      "(e.g. BGC0000001.5)", "repository/{accession_version}/annotations.json",
                      args=("accession_version",),
                      example={"accession_version": "BGC0000001.5"}),
            Operation("stats", "Entry counts by status, biosynthetic class and phylum",
                      "api/v1/stats"),
        ), smoke="stats", docs="https://mibig.secondarymetabolites.org/download",
        rate_note="no stated limit or robots rules; kept at 1 req/s"),

    PublicSource(
        "gutmgene", "gutMGene v2.0 (browse API)",
        "http://bio-computing.hrbmu.edu.cn/gutMGene2.0_api", "bio-computing.hrbmu.edu.cn",
        _GUTMGENE_LICENSE,
        "Literature-curated gut microbe - metabolite - host gene associations (Harbin "
        "Medical University). `association_evidence` returns the evidence rows behind one "
        "association: PMID, microbe (NCBI taxid, rank, strain), substrate and metabolite "
        "(PubChem, ChEBI, HMDB, KEGG ids), host gene (Entrez id), associative mode "
        "('causally' from a controlled experiment, 'correlatively' from a correlation), "
        "species, sample, method, alteration and condition. `dataset` is microbe_metabolite, "
        "microbe_gene or metabolite_gene; ids are the names the site shows. Undocumented "
        "endpoint of the site's browse page; answers {status:{code:'1'}, resultSet:[...]}.",
        "microbiome", (
            Operation("association_evidence", "Evidence rows of one association "
                      "(dataset, index_id, species and the microbe/metabolite/gene names)",
                      "browse/getbrowsetable", method="POST",
                      json_body={"data": {
                          "dataset": "{dataset}", "index_id": "{index_id}",
                          "species": "{species}", "datatype": "detail",
                          "substrate_id": "{substrate}", "microbe_id": "{microbe}",
                          "metabolite_id": "{metabolite}", "gene_id": "{gene}",
                          "alteration": "{alteration}"}},
                      args=("dataset", "index_id"),
                      example={"dataset": "metabolite_gene", "index_id": "1",
                               "species": "human", "substrate": "", "microbe": "",
                               "metabolite": "Acetate", "gene": "FFAR3",
                               "alteration": "activation"}),
        ), smoke="association_evidence", docs="http://bio-computing.hrbmu.edu.cn/gutmgene",
        rate_note="small academic server, no robots.txt; kept at 1 req/s"),

    PublicSource(
        "phytohub", "PhytoHub (entry JSON)", "https://phytohub.eu", "phytohub.eu",
        _PHYTOHUB_LICENSE,
        "Dietary phytochemicals and their human and animal metabolites (INRAE). `entry` "
        "returns one compound record (PHUB id, name, family, precursor/metabolite role, "
        "SMILES, InChIKey, formula, monoisotopic mass); `search` the compounds matching a "
        "name; `food_source` a food's name and group; `structure_sdf` one structure as MDL "
        "SDF. No relations: food contents and biotransformations are HTML only. "
        "Undocumented Rails format responders of the site's pages.",
        "natural-products", (
            Operation("entry", "One compound by PhytoHub id (PHUB000006)",
                      "entries/{phub_id}.json", args=("phub_id",),
                      example={"phub_id": "PHUB000006"}),
            Operation("search", "Compounds (precursors and metabolites) matching a name",
                      "search/compounds.json", params={"query": "{name}"}, args=("name",),
                      example={"name": "cafestol"}),
            Operation("food_source", "A food source's name and food group id",
                      "food_sources/{food_id}.json", args=("food_id",),
                      example={"food_id": 39}),
            Operation("structure_sdf", "One compound's structure (MDL SDF text)",
                      "structures/entries/{phub_id}.sdf", accept="chemical/x-mdl-sdfile",
                      args=("phub_id",), example={"phub_id": "PHUB000006"}),
        ), smoke="entry", docs="https://phytohub.eu/about",
        rate_note="no robots rules in force; kept at 1 req/s"),
)

PENDING: tuple[PublicSource, ...] = ()

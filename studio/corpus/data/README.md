# Open-data extracts (corpus inputs)

These files are inputs of the corpus packs `lotus` and `pathways` (`studio/docs/V2.md` §11.1, §11.3).
They are derived from open datasets, committed to the repository, and read as they are by the corpus
build. **CI never downloads them**: only a person updating a source runs the extractor, which downloads
the pinned upstream files, checks their size and sha256 (and LOTUS's md5), and rewrites these files.

| file | content | upstream | licence |
|---|---|---|---|
| `lotus-herbs.json.gz` | per materia herb: the LOTUS organisms matched to its source species (`matched_by`: NCBI taxon id, else exact species name) and the compounds LOTUS records in them, with a reference count; per compound: name, formula, 2-D SMILES; herbs with no match and why | LOTUS frozen export 2026-04-13 (Zenodo record 19360665); NCBI Taxonomy archive 2026-10-01 for merged ids (matching only) | CC BY 4.0 — cite Rutz et al., eLife 2022, doi:10.7554/eLife.70780 |
| `pathways.json.gz` | Reactome lowest-level *Homo sapiens* pathways with their UniProt members; HGNC approved symbol ↔ UniProt | Reactome release 97 (`UniProt2Reactome.txt`, `ReactomePathways.txt`); HGNC complete set 2026-10-06 | CC0 1.0 |
| `SOURCES.json` | for each file: upstream URLs, versions, sizes, sha256, retrieval date, licence, citation, what was changed, counts, the file's own sha256 and size | — | — |

A LOTUS record says that a compound was reported in an organism. It does not say that the compound is
an active constituent of the crude drug, how much of it the drug contains, or that it has any effect; a
herb is matched to organisms by taxon, not by the processed drug. Reactome membership is annotation
(what a protein takes part in), not what a compound does.

Rebuild (downloads about 250 MB into `~/.cache/tcmstudio/open-data` the first time):

```
python3 studio/corpus/extract_open.py --out studio/corpus/data --fixtures
python3 studio/corpus/extract_open.py --check
```

The output is deterministic: the same pinned upstream files give byte-identical files. `--fixtures` also
rewrites the small test fixtures in `studio/corpus/fixtures/` (complete records of a few herbs and of 30
pathways, cut from these files, same schemas).

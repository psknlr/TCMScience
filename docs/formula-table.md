# The formula table and the materia table

The repository root holds the formula table `中医方剂数据表.xlsx`. It lists 84,294
formulas on one sheet, with the columns 名称 · 配方 · 出处 · 炮制 · 功效 · 使用方法 · 注意.
This page explains how the table is used:

- how ingredient names are resolved to crude drugs and source species;
- which formulas can be studied;
- how a research question selects one formula.

## Licence and provenance

The table does not say where it came from or under what licence it may be used. It is
read at run time from the file the user supplied. Every herb-layer edge built from it
carries the licence `LicenseRef-user-supplied-unstated`, and the snapshot's citation
says the licence is unstated.

Composition edges are context in a research artifact, not evidence it cites, so they
never support a released claim. Before any derived data from the table is published,
someone should establish where the table came from and what it permits.

## From written names to drugs (`sources/formulas.py`)

The 配方 column is free text, for example
`附子1枚（去皮，破八片，炮），甘草2两（炙）`. The parser works as follows:

1. It splits items on `、，；。` only outside parentheses, so processing notes stay with
   their ingredient.
2. It reads the dose off the end of each item: `3分`, `半两`, `各等分`, `少许`.
   `半夏半两` becomes 半夏 and 半两.
3. A fragment that contains only processing instructions (`去皮尖双仁`) is attached to
   the previous ingredient.
4. It resolves each name through the materia table:
   - an exact name or alias;
   - otherwise the name with one processing prefix or suffix removed (炙, 酒, 麸炒, 末 …);
   - otherwise the longest known leading name.

   Anything else is left unresolved and kept as written.

A formula can be studied only if every ingredient resolves. If an ingredient is
unresolved, it could be any organism. Analysing the rest of the formula as if that
ingredient were absent would describe a different formula.

## The materia table (`sources/materia.py`)

The materia table has 293 crude drugs:

| Category | Drugs |
| --- | ---: |
| Plant | 224 |
| Animal | 31 |
| Fungus | 3 |
| Mineral | 29 |
| Other (神曲, 冰片, 酒 …) | 6 |

Each drug has these fields:
- its Pharmacopoeia name;
- its pharmaceutical Latin name;
- the medicinal part;
- its source species (Chinese Pharmacopoeia 2020 where the Pharmacopoeia lists them);
- the other names the formula table uses for it (甘草 / 炙甘草 / 粉草 / 国老).

Two names are deliberately **not** used as aliases because they are ambiguous:
- **贝母** can be 川贝母 or 浙贝母, which come from different species. Formulas that name
  贝母 therefore stay unresolved (1,525 mentions).
- **芍药** is historically either 白芍 or 赤芍. It is kept as an alias of 白芍 because
  both drugs come from *Paeonia lactiflora*.

Species are written as names. `scripts/verify_materia_taxa.py` resolves each name
against NCBI Taxonomy and writes the result to `registry/materia_taxa.json`: the name
as written, the taxid, NCBI's current name, and the date checked. Three rules apply:

- **Exact matches are used directly.** A name that matches an NCBI scientific name
  exactly is accepted as is.
- **Synonym matches need review.** A match through NCBI's "any name" index is used only
  after a person has reviewed it; it then appears in `REVIEWED` in the script. That
  index can attach a name to a different organism. For example, it sends
  *Cinnamomum cassia* to *Neolitsea cassia*, not to the Pharmacopoeia's cassia bark.
  Unreviewed synonym matches contribute no taxon.
- **Known homonyms are pinned by hand.** They are listed in `CURATED`, with the reason.
  *Cinnamomum cassia* is pinned to *Cinnamomum aromaticum* (taxid 119260), and the
  Pharmacopoeia name is kept as a synonym so the natural-product sources still match it.

The latest check resolved 389 of 393 species names. The unresolved four are
alternative names; each of their drugs still has at least one verified species.
Minerals and "other" drugs are recognised as ingredients but carry no organism. A
research run lists them as components that contribute no constituents.

## Coverage

| | |
| --- | --- |
| Formulas in the table | 84,294 |
| Formulas with every ingredient resolved | 42,347 (50.2%) |
| Ingredient mentions resolved | 562,219 of 638,495 (88.1%) |
| Drugs used by the resolved formulas | 292 |

The unresolved mentions form a long tail: after 贝母, each remaining name occurs about
260 times or fewer (漏芦, 山芋, 大青, 百草霜, …).
`formulas.component_counts(table.records)` lists them in order, and that list is the
backlog for extending the materia table.

## Choosing a formula in a question

The table contains many formulas under one name. It has 18 different compositions called
逍遥散 and 6 called 四君子汤, and the classic 《局方》 四君子汤 is not among them.
`research.parse_question` resolves a question to one formula in this order:

1. A formula id written in the question (`tcm:formula.fx…`) is used as given.
2. A hand-checked name (葛根芩连汤) comes next.
3. A table name follows. When the name matches more than one composition, the source
   book written in the question narrows the choice. Full titles are mapped to the
   table's abbreviations (《太平惠民和剂局方》 → 《局方》).

The question is refused, with the reason, in three cases:
- **Ambiguous.** The name still matches more than one composition. The refusal lists
  the candidates with their sources and first ingredients.
- **Unresolved ingredient.** The formula has an ingredient the materia table cannot
  resolve. The refusal names the ingredient.
- **Book title only.** A book title, such as 普济方, is never read as a formula name.

## Building the snapshots

```bash
python BioScience-Harness/scripts/build_source_snapshots.py gold \
    --raw RAW --out WORK/snapshots --network --ledger WORK/audit/snapshots.jsonl \
    --formula-table 中医方剂数据表.xlsx
```

This command builds five snapshots:

- **Herb layer:** 葛根芩连汤 plus every fully resolved formula of the table.
- **NPASS, CMAUP and LOTUS:** each restricted to the species of every drug.
- **STRING:** the proteins those sources report.
- **Reactome:** the full human annotation.

It still checks the four gold marker compounds of 葛根芩连汤.

PubChem BioAssay, which includes inactive results, is fetched per compound set. Scope
the fetch to the drugs of the formulas you study:

```bash
python BioScience-Harness/scripts/fetch_pubchem.py --composition WORK/snapshots/composition.json \
    --raw RAW --herbs mahuang guizhi xingren gancao
python BioScience-Harness/scripts/build_source_snapshots.py pubchem \
    --file RAW/pubchem_bioassay.json.gz --raw RAW --out WORK/snapshots --ledger WORK/audit/snapshots.jsonl
```

When PubChem answers "server busy", the fetch waits and retries the same batch with
back-off. Splitting the batch would send more requests at the moment the service asked
for fewer.

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
2. It reads the dose off the end of each item **before** it normalises the name:
   `3分`, `半两`, `1.5g`, `钱半`, `1分半`, `5厘`, `各等分`, `少许`. `半夏半两` becomes 半夏
   and 半两, and `附子1.5g` keeps its decimal point. (Until October 2026 the name was
   normalised first, and normalising removes punctuation, so 1.5g became 15g and 0.5g
   became 05g; 396 mentions in 195 formulas were affected.) `parse_dose` reads the
   amount and unit, and keeps a number only when writing it back gives the dose exactly
   as written, so a dose like 05g never reads as a number.
3. A fragment that contains only processing instructions (`去皮尖双仁`) is attached to
   the previous ingredient. A fragment with a dose of its own (`蒸馏水100ml`) is an
   ingredient.
4. It resolves each name through the materia table:
   - an exact name or alias, or a reviewed alias (below);
   - otherwise the name with one processing prefix or suffix removed (炙, 酒, 麸炒, 末 …)
     or one word of quality or state (真, 好, 嫩, 陈, 鲜, 干 …);
   - otherwise the longest known leading name, **but only when what follows is a
     processing instruction** (甘草去皮, 当归酒浸, 半夏汤洗七次).

   Anything else is left unresolved and kept as written. What follows a known name is
   never read as processing when it is a part (麻黄根, 车前子根), a product (灯心灰,
   珍珠母) or a constituent (黄连素, 人参皂苷): those are different things. An unresolved
   ingredient's `kind` says what its name reads as: `compound` (黄连素, 甘草酸),
   `constituent_group` (人参皂苷, 总黄酮), `extract` (浸膏) or `unresolved`.

A formula can be studied only if every ingredient resolves. If an ingredient is
unresolved, it could be any organism. Analysing the rest of the formula as if that
ingredient were absent would describe a different formula.

## The materia table (`sources/materia.py`)

The materia table has 401 crude drugs:

| Category | Drugs |
| --- | ---: |
| Plant | 298 |
| Animal | 44 |
| Fungus | 3 |
| Mineral | 44 |
| Other (神曲, 冰片, 酒, 百草霜 …) | 12 |

Each drug has these fields:
- its Pharmacopoeia name;
- its pharmaceutical Latin name;
- the medicinal part;
- its source species (Chinese Pharmacopoeia 2020 where the Pharmacopoeia lists them);
- the other names the formula table uses for it (甘草 / 炙甘草 / 粉草 / 国老).

Two names are deliberately **not** used as aliases because they are ambiguous:
- **贝母** can be 川贝母 or 浙贝母, which come from different species. Formulas that name
  贝母 therefore stay unresolved (1,529 mentions).
- **芍药** is historically either 白芍 or 赤芍. It is kept as an alias of 白芍 because
  both drugs come from *Paeonia lactiflora*.

Near names are not folded into one another. Words of origin, size or colour (川, 大, 小,
白) are no longer stripped: 川牛膝 is *Cyathula officinalis*, not 牛膝; 白丁香 is sparrow
droppings, not 丁香; 大麦 is barley, not 小麦. 川牛膝, 川木通, 川木香 and 白丁香 now have
entries of their own. The names of that form that do denote the drug they contain
(川当归, 大半夏, 白云苓 …) are listed one by one in `_REVIEWED_ALIASES`, each checked
against what it denotes; ambiguous ones (大麻子: hemp or castor; 胡麻子: sesame or flax)
stay unresolved. Three aliases named another species and were corrected in October
2026: 化橘红 (化州柚 or 柚, *Citrus maxima*) and 橘红 were aliases of 陈皮, and 竹叶 (a
bamboo leaf) of the grass 淡竹叶; each is now its own drug.

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

The latest check resolved 491 of 496 species names. Four of the unresolved names are
alternatives, and their drugs still have at least one verified species. The fifth is
*Tabanus bivittatus* (虻虫), which NCBI does not list; that drug is recognised but
carries no organism.
Minerals and "other" drugs are recognised as ingredients but carry no organism. A
research run lists them as components that contribute no constituents.

## Coverage

| | |
| --- | --- |
| Formulas in the table | 84,294 |
| Formulas with every ingredient resolved | 50,291 (59.7%) |
| Ingredient mentions resolved | 583,909 of 639,708 (91.3%) |
| Drugs used by the resolved formulas | 400 |

Before October 2026 the table reported 53,206 resolved formulas. The difference is
names that resolved only by folding a near name into another drug, or by reading a part,
a product or a constituent as processing; 2,931 formulas lost a wrong resolution and 16
gained a right one. 2,552 formulas that resolve both ways changed fingerprint, mostly
because their doses now keep their decimal points, so herb-layer snapshots built with
`--formula-table` before then should be rebuilt.

The unresolved mentions form a long tail. The largest are left unresolved on
purpose: 贝母 (1,529 mentions; 川贝母 or 浙贝母), and names whose historical
identity is disputed, such as 防葵 (159), 鬼臼 (125) and 狼毒 (110). Every other
unresolved name occurs fewer than 100 times.
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

## On real data (2026-09-27)

These numbers predate the October 2026 identity fixes above. 麻黄汤《伤寒论》 parses to
the same composition and fingerprint under both, so its result below still stands; the
species set and the totals would change slightly on a rebuild, because 川牛膝, 化橘红 and
the other new entries bring species of their own.

The run used the public downloads of NPASS 2.0, CMAUP 2.0, LOTUS (2026-04-13 frozen
dump), STRING 12.0 and Reactome. The sources were restricted to the species of all 258
organism drugs, and the gold markers were reproduced.

| Snapshot | Nodes | Edges |
| --- | ---: | ---: |
| Herb layer (42,253 formulas) | 42,871 | 291,464 |
| NPASS | 22,745 | 111,138 |
| CMAUP | 18,887 | 58,391 |
| LOTUS | 17,590 | 25,570 |
| STRING (induced subnetwork) | 1,589 | 200,762 |

The run joined 65,671 drug–compound composition rows, covering 30,275 compounds.

**Question:** 麻黄汤《伤寒论》的实测靶点是否集中在某条 Reactome 通路？ It resolved
to 麻黄、桂枝、甘草、杏仁. NPASS and CMAUP were the activity sources; LOTUS and STRING
were optional.

- **Whole Reactome as background.** The analysis produced 106 pathway hypotheses, and
  rebuttal refuted every one:
  - all 106 are no longer significant when the background is restricted to proteins
    that were assayed;
  - 49 also rest on NPASS alone;
  - 19 rest on constituents that only LOTUS reports;
  - 1 is not significant under another permutation seed.
- **Assayed proteins as background (the default).** No pathway is significant.

Both runs release a negative result. This is the same pattern 葛根芩连汤 showed. In
these public data, which proteins were tested explains the apparent pathway
enrichment.

**PubChem BioAssay** is wired in as an activity source (`pubchem_bioassay`,
`--hits screening`), but the fetch for this formula set was not completed. PubChem
answered `PUGREST.ServerBusy` to every request from this environment's client after
repeated batches. Its usage policy allows temporarily blocking clients that make heavy
requests. The fetcher now waits out a busy answer on the same batch rather than
splitting it, so a retry sends fewer requests, not more. The fetch should be re-run
once the block lifts, using the command above; it was not routed around.

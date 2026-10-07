# Evidence typing on real abstracts

478 abstracts from Europe PMC (open access, CC BY or CC0), 302 in the test split and 176 in dev. The reference is PubMed's publication types (MeSH for animal and in-vitro studies): indexers assign them, some are missing, and a secondary analysis can carry its trial's type, so this measures agreement with an imperfect reference. Precision is for this stratified mix, not for literature at large.

## The sample

| Stratum | Language | Reference | Drawn | Frame | Examined |
| --- | --- | --- | ---: | ---: | ---: |
| randomized_trial | en | randomized trial | 60 | 6945 | 64 |
| systematic_review | en | systematic review | 60 | 12423 | 66 |
| cohort | en | observational | 20 | 340 | 20 |
| case_control | en | observational | 20 | 76 | 21 |
| cross_sectional | en | observational | 20 | 568 | 20 |
| observational | en | observational | 15 | 4188 | 15 |
| case_report | en | case report | 50 | 20764 | 54 |
| animal | en | animal | 50 | 35698 | 53 |
| in_vitro | en | in vitro | 50 | 6124 | 58 |
| narrative_review | en | narrative review | 30 | 64716 | 32 |
| editorial | en | editorial | 25 | 3072 | 114 |
| protocol | en | trial protocol | 30 | 1622 | 30 |
| zh_randomized_trial | zh | randomized trial | 12 | 36 | 12 |
| zh_systematic_review | zh | systematic review | 8 | 503 | 49 |
| zh_case_report | zh | case report | 8 | 197 | 9 |
| zh_animal | zh | animal | 6 | 100 | 7 |
| zh_in_vitro | zh | in vitro | 6 | 216 | 8 |
| zh_narrative_review | zh | narrative review | 8 | 568 | 12 |

Licences: CC-BY-4.0 386, CC-BY 46, CC-BY-3.0 45, CC0-1.0 1 (`CC-BY`: the article names the licence but no version). Each record in `sample.jsonl` carries its attribution: authors, journal, year, DOI, PMID, PMCID, licence, licence URL or statement, and copyright line. Abstract markup was removed; the text is otherwise as published.

## Test split

### Design

| Design | n | Precision (before → after) | Recall (before → after) | Abstained (before → after) | Wrong (before → after) |
| --- | ---: | --- | --- | --- | --- |
| systematic review | 40 | 22/23 (96%; 79%–99%) → 36/38 (95%; 83%–99%) | 22/40 (55%; 40%–69%) → 36/40 (90%; 77%–96%) | 18/40 (45%; 31%–60%) → 4/40 (10%; 4%–23%) | 0/40 (0%; 0%–9%) → 0/40 (0%; 0%–9%) |
| randomized trial | 48 | 36/52 (69%; 56%–80%) → 35/36 (97%; 86%–100%) | 36/48 (75%; 61%–85%) → 35/48 (73%; 59%–83%) | 11/48 (23%; 13%–37%) → 12/48 (25%; 15%–39%) | 1/48 (2%; 0%–11%) → 1/48 (2%; 0%–11%) |
| observational | 42 | 32/33 (97%; 85%–99%) → 40/41 (98%; 87%–100%) | 32/42 (76%; 61%–87%) → 40/42 (95%; 84%–99%) | 10/42 (24%; 13%–39%) → 2/42 (5%; 1%–16%) | 0/42 (0%; 0%–8%) → 0/42 (0%; 0%–8%) |
| case report | 42 | 27/27 (100%; 88%–100%) → 35/35 (100%; 90%–100%) | 27/42 (64%; 49%–77%) → 35/42 (83%; 69%–92%) | 15/42 (36%; 23%–51%) → 7/42 (17%; 8%–31%) | 0/42 (0%; 0%–8%) → 0/42 (0%; 0%–8%) |
| animal | 31 | 12/13 (92%; 67%–99%) → 12/13 (92%; 67%–99%) | 12/31 (39%; 24%–56%) → 12/31 (39%; 24%–56%) | 19/31 (61%; 44%–76%) → 19/31 (61%; 44%–76%) | 0/31 (0%; 0%–11%) → 0/31 (0%; 0%–11%) |
| in vitro | 42 | 26/27 (96%; 82%–99%) → 33/33 (100%; 90%–100%) | 26/42 (62%; 47%–75%) → 33/42 (79%; 64%–88%) | 16/42 (38%; 25%–53%) → 9/42 (21%; 12%–36%) | 0/42 (0%; 0%–8%) → 0/42 (0%; 0%–8%) |

Of 245 studies, 192/245 (78%; 73%–83%) were typed; of every design read (negatives included), 191/196 (97%; 94%–99%) was the reference's. 106 correct readings came from the title.

Confusion matrix (rows: reference; columns: what the rules read):

| Reference | systematic review | randomized trial | observational | case report | animal | in vitro | ambiguous | no rule |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| systematic review | 36 | 0 | 0 | 0 | 0 | 0 | 0 | 4 |
| randomized trial | 0 | 35 | 1 | 0 | 0 | 0 | 2 | 10 |
| observational | 0 | 0 | 40 | 0 | 0 | 0 | 0 | 2 |
| case report | 0 | 0 | 0 | 35 | 0 | 0 | 1 | 6 |
| animal | 0 | 0 | 0 | 0 | 12 | 0 | 5 | 14 |
| in vitro | 0 | 0 | 0 | 0 | 0 | 33 | 0 | 9 |
| narrative review | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 23 |
| editorial | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 14 |
| trial protocol | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 16 |

### Negatives

| Kind | n | Typed at all | As a trial | As a review |
| --- | ---: | --- | --- | --- |
| narrative review | 25 | 2/25 (8%; 2%–25%) | 0/25 (0%; 0%–13%) | 2/25 (8%; 2%–25%) |
| editorial | 15 | 1/15 (7%; 1%–30%) | 0/15 (0%; 0%–20%) | 0/15 (0%; 0%–20%) |
| trial protocol | 17 | 1/17 (6%; 1%–27%) | 1/17 (6%; 1%–27%) | 0/17 (0%; 0%–18%) |
| all negatives | 57 | 4/57 (7%; 3%–17%) | 1/57 (2%; 0%–9%) | 2/57 (4%; 1%–12%) |

5 readings were wrong; 4 of them would license a claim kind the reference does not (3 of those efficacy): 35057843, 35805058, 36051702, 36194175.

### Population, comparator, outcome

| Field | Filled | Ambiguous | No rule |
| --- | --- | ---: | ---: |
| population | 28/302 (9%; 6%–13%) | 14 | 260 |
| comparator | 41/302 (14%; 10%–18%) | 8 | 253 |
| outcome | 20/302 (7%; 4%–10%) | 6 | 276 |

Filled, by reference:

| Reference | population | comparator | outcome |
| --- | ---: | ---: | ---: |
| systematic review | 8/40 | 6/40 | 6/40 |
| randomized trial | 3/48 | 11/48 | 4/48 |
| observational | 6/42 | 9/42 | 5/42 |
| case report | 1/42 | 1/42 | 1/42 |
| animal | 0/31 | 4/31 | 0/31 |
| in vitro | 2/42 | 7/42 | 0/42 |
| narrative review | 3/25 | 0/25 | 0/25 |
| editorial | 1/15 | 0/15 | 0/15 |
| trial protocol | 4/17 | 3/17 | 4/17 |

### Manual check of filled fields

30 filled population, comparator and outcome readings of the test split, drawn by `run_evidence_typing_benchmark.py --draw-manual-check 30` (a seeded hash of PMID and field, fixed before reading). Each value was read in its abstract, with the span marked, by the agent that wrote the rules: one reader, not blind to the rules, not a domain expert. The verdicts were decided against criteria written before reading: correct if the value names this study's population (the people it enrolled or reviews), comparator (the group, treatment or condition it compares against) or outcome (something it measured); partly if it names that but is cut short or padded with other words; wrong if it names something else, such as a population from a background sentence.

Correct: 23/30 (77%; 59%–88%); partly correct: 5; wrong: 2.

| Field | Correct | Partly | Wrong |
| --- | ---: | ---: | ---: |
| population | 9 | 2 | 2 |
| comparator | 12 | 2 | 0 |
| outcome | 2 | 1 | 0 |

### By stratum

| Stratum | n | Correct | Abstained | Wrong |
| --- | ---: | ---: | ---: | ---: |
| animal | 28 | 12 | 16 | 0 |
| case_control | 11 | 10 | 1 | 0 |
| case_report | 36 | 32 | 4 | 0 |
| cohort | 10 | 10 | 0 | 0 |
| cross_sectional | 15 | 14 | 1 | 0 |
| editorial | 15 | 0 | 14 | 1 |
| in_vitro | 38 | 30 | 8 | 0 |
| narrative_review | 18 | 0 | 16 | 2 |
| observational | 6 | 6 | 0 | 0 |
| protocol | 17 | 0 | 16 | 1 |
| randomized_trial | 39 | 29 | 9 | 1 |
| systematic_review | 34 | 30 | 4 | 0 |
| zh_animal | 3 | 0 | 3 | 0 |
| zh_case_report | 6 | 3 | 3 | 0 |
| zh_in_vitro | 4 | 3 | 1 | 0 |
| zh_narrative_review | 7 | 0 | 7 | 0 |
| zh_randomized_trial | 9 | 6 | 3 | 0 |
| zh_systematic_review | 6 | 6 | 0 | 0 |

On a negative, abstaining is the right answer and counts under Abstained.

## Dev split

| Design | n | Precision (before → after) | Recall (before → after) | Abstained (before → after) | Wrong (before → after) |
| --- | ---: | --- | --- | --- | --- |
| systematic review | 28 | 11/11 (100%; 74%–100%) → 27/27 (100%; 88%–100%) | 11/28 (39%; 24%–58%) → 27/28 (96%; 82%–99%) | 17/28 (61%; 42%–76%) → 1/28 (4%; 1%–18%) | 0/28 (0%; 0%–12%) → 0/28 (0%; 0%–12%) |
| randomized trial | 24 | 23/34 (68%; 51%–81%) → 22/22 (100%; 85%–100%) | 23/24 (96%; 80%–99%) → 22/24 (92%; 74%–98%) | 1/24 (4%; 1%–20%) → 2/24 (8%; 2%–26%) | 0/24 (0%; 0%–14%) → 0/24 (0%; 0%–14%) |
| observational | 33 | 23/23 (100%; 86%–100%) → 30/30 (100%; 89%–100%) | 23/33 (70%; 53%–83%) → 30/33 (91%; 76%–97%) | 9/33 (27%; 15%–44%) → 2/33 (6%; 2%–20%) | 1/33 (3%; 1%–15%) → 1/33 (3%; 1%–15%) |
| case report | 16 | 14/14 (100%; 78%–100%) → 16/16 (100%; 81%–100%) | 14/16 (88%; 64%–96%) → 16/16 (100%; 81%–100%) | 2/16 (12%; 4%–36%) → 0/16 (0%; 0%–19%) | 0/16 (0%; 0%–19%) → 0/16 (0%; 0%–19%) |
| animal | 25 | 9/10 (90%; 60%–98%) → 13/14 (93%; 69%–99%) | 9/25 (36%; 20%–55%) → 13/25 (52%; 34%–70%) | 16/25 (64%; 45%–80%) → 11/25 (44%; 27%–63%) | 0/25 (0%; 0%–13%) → 1/25 (4%; 1%–20%) |
| in vitro | 14 | 6/6 (100%; 61%–100%) → 11/12 (92%; 65%–99%) | 6/14 (43%; 21%–67%) → 11/14 (79%; 52%–92%) | 8/14 (57%; 33%–79%) → 3/14 (21%; 8%–48%) | 0/14 (0%; 0%–22%) → 0/14 (0%; 0%–22%) |

Negatives typed: 0/36 (0%; 0%–10%); as a trial 0/36 (0%; 0%–10%), as a review 0/36 (0%; 0%–10%).

## Before and after the rule changes

Before: rules `0b111264cdeb`; after: rules `9f78756177f8` (SHA-256 of the `evidence.py` each was read from). How and why they changed: `docs/evidence-typing-accuracy.md`.

| Split | Studies typed | Readings right (negatives included) | Wrong readings | Negatives typed |
| --- | --- | --- | ---: | --- |
| dev | 87/140 (62%; 54%–70%) → 121/140 (86%; 80%–91%) | 86/98 (88%; 80%–93%) → 119/121 (98%; 94%–100%) | 12 → 2 | 11/36 (31%; 18%–47%) → 0/36 (0%; 0%–10%) |
| test | 156/245 (64%; 57%–69%) → 192/245 (78%; 73%–83%) | 155/175 (89%; 83%–92%) → 191/196 (97%; 94%–99%) | 20 → 5 | 19/57 (33%; 22%–46%) → 4/57 (7%; 3%–17%) |

Test records whose verdict changed: abstained → correct: 38, abstained → wrong: 1, correct → abstained: 2, wrong → abstained: 16.

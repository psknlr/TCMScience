# Governance ablation

10 base cases, 121 mutants over 26 error classes, 5 gates. Mutation testing: base cases a reviewer would release, each mutated by one named scientific error; base cases and operators were written by the authors of the gates, so this is a regression benchmark with a stated construction, not an estimate of field error rates.

## Configurations

| Configuration | Errors released | 95% CI | Correct outputs refused |
| --- | ---: | --- | ---: |
| ungoverned | 121/121 (100%) | 97%–100% | 0/10 |
| kernel only | 7/121 (6%) | 3%–11% | 0/10 |
| domain only | 57/121 (47%) | 38%–56% | 0/10 |
| full | 0/121 (0%) | 0%–3% | 0/10 |
| full without provenance | 10/121 (8%) | 5%–15% | 0/10 |
| full without output | 42/121 (35%) | 27%–44% | 0/10 |
| full without licensing | 0/121 (0%) | 0%–3% | 0/10 |
| full without claim_contract | 6/121 (5%) | 2%–10% | 0/10 |
| full without release_path | 1/121 (1%) | 0%–5% | 0/10 |
| provenance only | 111/121 (92%) | 85%–95% | 0/10 |
| output only | 22/121 (18%) | 12%–26% | 0/10 |
| licensing only | 70/121 (58%) | 49%–66% | 0/10 |
| claim_contract only | 58/121 (48%) | 39%–57% | 0/10 |
| release_path only | 117/121 (97%) | 92%–99% | 0/10 |

## Which part catches which error

Each gate alone, each layer alone, and the full stack: the share of each error class's mutants refused.

| Error class | n | provenance | output | licensing | claim_contract | release_path | kernel | domain | full |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| population | 7 | 0% | 100% | 100% | 100% | 0% | 100% | 100% | 100% |
| outcome | 5 | 0% | 100% | 100% | 100% | 0% | 100% | 100% | 100% |
| subject | 5 | 0% | 100% | 100% | 0% | 0% | 100% | 0% | 100% |
| direction | 4 | 0% | 100% | 0% | 0% | 0% | 100% | 0% | 100% |
| certainty | 8 | 0% | 100% | 100% | 100% | 12% | 100% | 100% | 100% |
| animal | 2 | 0% | 0% | 100% | 100% | 0% | 100% | 100% | 100% |
| docking | 2 | 0% | 50% | 100% | 100% | 0% | 100% | 100% | 100% |
| plan | 3 | 0% | 100% | 100% | 100% | 0% | 100% | 100% | 100% |
| population_text | 7 | 0% | 100% | 0% | 0% | 0% | 100% | 0% | 100% |
| outcome_text | 5 | 0% | 100% | 0% | 0% | 0% | 100% | 0% | 100% |
| subject_text | 5 | 0% | 100% | 0% | 0% | 0% | 100% | 0% | 100% |
| certainty_text | 8 | 0% | 100% | 0% | 100% | 12% | 100% | 100% | 100% |
| retracted | 10 | 0% | 100% | 100% | 100% | 0% | 100% | 100% | 100% |
| tampered | 10 | 0% | 0% | 0% | 0% | 0% | 100% | 0% | 100% |
| citation_mismatch | 10 | 100% | 100% | 0% | 0% | 0% | 100% | 0% | 100% |
| fabricated_citation | 10 | 0% | 100% | 0% | 0% | 0% | 100% | 0% | 100% |
| constituent_formula | 2 | 0% | 100% | 100% | 100% | 0% | 100% | 100% | 100% |
| duplicate_source | 2 | 0% | 0% | 0% | 100% | 0% | 0% | 100% | 100% |
| unknown_as_negative | 4 | 0% | 50% | 0% | 100% | 0% | 50% | 100% | 100% |
| near_name | 2 | 0% | 50% | 100% | 100% | 0% | 100% | 100% | 100% |
| dose | 1 | 0% | 100% | 0% | 0% | 0% | 100% | 0% | 100% |
| near_name_text | 2 | 0% | 50% | 0% | 100% | 0% | 50% | 100% | 100% |
| processing_transfer | 1 | 0% | 0% | 100% | 100% | 0% | 100% | 100% | 100% |
| upgrade | 4 | 0% | 100% | 100% | 100% | 25% | 100% | 100% | 100% |
| borrowed_direction | 1 | 0% | 0% | 0% | 0% | 100% | 0% | 100% | 100% |
| exposure_text | 1 | 0% | 0% | 0% | 100% | 0% | 0% | 100% | 100% |

## What each gate adds

Mutants the full stack refuses and releases once that gate is switched off.

- **provenance** (kernel, 10): `E1/tampered`, `E2/tampered`, `E3/tampered`, `E4/tampered`, `A1/tampered`, `A2/tampered`, `S1/tampered`, `M1/tampered`, `H1/tampered`, `C1/tampered`
- **output** (kernel, 42): `E1/direction`, `E1/population_text`, `E1/outcome_text`, `E1/subject_text`, `E1/tampered`, `E1/fabricated_citation`, `E2/direction`, `E2/population_text`, `E2/outcome_text`, `E2/subject_text`, `E2/tampered`, `E2/fabricated_citation`, `E3/direction`, `E3/population_text`, `E3/outcome_text`, `E3/subject_text`, `E3/tampered`, `E3/fabricated_citation`, `E4/dose`, `E4/direction`, `E4/population_text`, `E4/tampered`, `E4/fabricated_citation`, `A1/population_text`, `A1/outcome_text`, `A1/tampered`, `A1/fabricated_citation`, `A2/population_text`, `A2/outcome_text`, `A2/tampered`, `A2/fabricated_citation`, `S1/population_text`, `S1/tampered`, `S1/fabricated_citation`, `M1/tampered`, `M1/fabricated_citation`, `H1/subject_text`, `H1/tampered`, `H1/fabricated_citation`, `C1/subject_text`, `C1/tampered`, `C1/fabricated_citation`
- **licensing** (kernel, 0): nothing the others do not also catch
- **claim_contract** (domain, 6): `E1/duplicate_source`, `E1/unknown_as_negative`, `E2/duplicate_source`, `E4/near_name_text`, `E4/unknown_as_negative`, `M1/exposure_text`
- **release_path** (domain, 1): `M1/borrowed_direction`

## Errors the full stack releases

None.

## Correct outputs the full stack refuses

None.

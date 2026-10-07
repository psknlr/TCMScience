# Governance ablation

10 base cases, 111 mutants over 21 error classes, 5 gates. Mutation testing: base cases a reviewer would release, each mutated by one named scientific error; base cases and operators were written by the authors of the gates, so this is a regression benchmark with a stated construction, not an estimate of field error rates.

## Configurations

| Configuration | Errors released | 95% CI | Correct outputs refused |
| --- | ---: | --- | ---: |
| ungoverned | 111/111 (100%) | 97%–100% | 0/10 |
| kernel only | 14/111 (13%) | 8%–20% | 0/10 |
| domain only | 69/111 (62%) | 53%–71% | 0/10 |
| full | 10/111 (9%) | 5%–16% | 0/10 |
| full without provenance | 20/111 (18%) | 12%–26% | 0/10 |
| full without output | 48/111 (43%) | 34%–53% | 0/10 |
| full without licensing | 13/111 (12%) | 7%–19% | 0/10 |
| full without claim_contract | 13/111 (12%) | 7%–19% | 0/10 |
| full without release_path | 11/111 (10%) | 6%–17% | 0/10 |
| provenance only | 101/111 (91%) | 84%–95% | 0/10 |
| output only | 39/111 (35%) | 27%–44% | 0/10 |
| licensing only | 63/111 (57%) | 47%–66% | 0/10 |
| claim_contract only | 70/111 (63%) | 54%–71% | 0/10 |
| release_path only | 109/111 (98%) | 94%–100% | 0/10 |

## Which part catches which error

Each gate alone, each layer alone, and the full stack: the share of each error class's mutants refused.

| Error class | n | provenance | output | licensing | claim_contract | release_path | kernel | domain | full |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| population | 7 | 0% | 57% | 100% | 100% | 0% | 100% | 100% | 100% |
| outcome | 5 | 0% | 60% | 100% | 100% | 0% | 100% | 100% | 100% |
| subject | 5 | 0% | 60% | 100% | 0% | 0% | 100% | 0% | 100% |
| direction | 4 | 0% | 100% | 0% | 0% | 0% | 100% | 0% | 100% |
| certainty | 8 | 0% | 75% | 100% | 25% | 0% | 100% | 25% | 100% |
| animal | 2 | 0% | 0% | 100% | 100% | 0% | 100% | 100% | 100% |
| docking | 2 | 0% | 50% | 100% | 100% | 0% | 100% | 100% | 100% |
| plan | 3 | 0% | 100% | 100% | 100% | 0% | 100% | 100% | 100% |
| population_text | 7 | 0% | 57% | 0% | 0% | 0% | 57% | 0% | 57% |
| outcome_text | 5 | 0% | 60% | 0% | 0% | 0% | 60% | 0% | 60% |
| subject_text | 5 | 0% | 60% | 0% | 0% | 0% | 60% | 0% | 60% |
| certainty_text | 8 | 0% | 75% | 0% | 25% | 0% | 75% | 25% | 88% |
| retracted | 10 | 0% | 90% | 100% | 100% | 0% | 100% | 100% | 100% |
| tampered | 10 | 0% | 0% | 0% | 0% | 0% | 90% | 0% | 90% |
| citation_mismatch | 10 | 100% | 90% | 0% | 0% | 0% | 100% | 0% | 100% |
| fabricated_citation | 10 | 0% | 90% | 0% | 0% | 0% | 90% | 0% | 90% |
| near_name | 2 | 0% | 0% | 100% | 100% | 0% | 100% | 100% | 100% |
| dose | 1 | 0% | 100% | 0% | 0% | 0% | 100% | 0% | 100% |
| near_name_text | 2 | 0% | 0% | 0% | 100% | 0% | 0% | 100% | 100% |
| upgrade | 4 | 0% | 100% | 100% | 100% | 25% | 100% | 100% | 100% |
| borrowed_direction | 1 | 0% | 0% | 0% | 0% | 100% | 0% | 100% | 100% |

## What each gate adds

Mutants the full stack refuses and releases once that gate is switched off.

- **provenance** (kernel, 10): `E1/tampered`, `E2/tampered`, `E3/tampered`, `E4/tampered`, `A1/tampered`, `A2/tampered`, `S1/tampered`, `M1/tampered`, `H1/tampered`, `C1/citation_mismatch`
- **output** (kernel, 38): `E1/direction`, `E1/population_text`, `E1/outcome_text`, `E1/subject_text`, `E1/certainty_text`, `E1/tampered`, `E1/fabricated_citation`, `E2/direction`, `E2/population_text`, `E2/subject_text`, `E2/certainty_text`, `E2/tampered`, `E2/fabricated_citation`, `E3/direction`, `E3/population_text`, `E3/outcome_text`, `E3/subject_text`, `E3/certainty_text`, `E3/tampered`, `E3/fabricated_citation`, `E4/dose`, `E4/direction`, `E4/population_text`, `E4/tampered`, `E4/fabricated_citation`, `A1/outcome_text`, `A1/certainty_text`, `A1/tampered`, `A1/fabricated_citation`, `A2/tampered`, `A2/fabricated_citation`, `S1/certainty_text`, `S1/tampered`, `S1/fabricated_citation`, `M1/tampered`, `M1/fabricated_citation`, `H1/tampered`, `H1/fabricated_citation`
- **licensing** (kernel, 3): `M1/certainty`, `H1/subject`, `C1/subject`
- **claim_contract** (domain, 3): `E4/near_name_text`, `S1/near_name_text`, `H1/certainty_text`
- **release_path** (domain, 1): `M1/borrowed_direction`

## Errors the full stack releases

- `E2/outcome_text` (outcome_text): Empagliflozin reduced the combined risk of all-cause mortality in patients with heart failure and a preserved ejection fraction (PMID: 34449189).
- `A1/population_text` (population_text): Higher serum C-telopeptide was associated with all-cause mortality in children (doi:10.5555/tcm-ablation.05).
- `A2/population_text` (population_text): 长期服用含马兜铃酸的中药与儿童慢性肾脏病患者终末期肾病风险升高相关（doi:10.5555/tcm-ablation.06）。
- `A2/outcome_text` (outcome_text): 长期服用含马兜铃酸的中药与成人慢性肾脏病患者全因死亡率升高相关（doi:10.5555/tcm-ablation.06）。
- `S1/population_text` (population_text): 服用含何首乌的制剂可能与儿童药物性肝损伤有关（doi:10.5555/tcm-ablation.07）。
- `M1/certainty_text` (certainty_text): In vitro, compound A always completely inhibits target T1 (doi:10.5555/tcm-ablation.08).
- `H1/subject_text` (subject_text): Docking suggests that berberine may bind PTP1B, a mechanism hypothesis for experimental testing (doi:10.5555/tcm-ablation.09).
- `C1/subject_text` (subject_text): 《伤寒论》第34条记载：喘而汗出者，桂枝汤主之（shanghanlun:34）。
- `C1/tampered` (tampered): 《伤寒论》第34条记载：喘而汗出者，葛根黄芩黄连汤主之（shanghanlun:34）。
- `C1/fabricated_citation` (fabricated_citation): 《伤寒论》第34条记载：喘而汗出者，葛根黄芩黄连汤主之（shanghanlun:99）。

## Correct outputs the full stack refuses

None.

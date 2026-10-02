Fault-injection benchmark, 50 replicates per cell, seed 20261002.

| | naive | gated |
| --- | ---: | ---: |
| false positives, clean null | 0.34 | 0.04 |
| power, clean positive | 1.00 | 0.92 |
| false alarms, clean studies | 0.00 | 0.00 |
| defects detected (all faults) | 0.00 | 0.99 |
| claims made on faulted null data | 0.66 | 0.00 |

| fault | check | naive: detected / claimed | gated: detected / claimed |
| --- | --- | ---: | ---: |
| cells_as_units | independent_units | 0/50 of 50 | 50/0 of 50 |
| shuffled_pairing | pairing | 0/1 of 50 | 47/0 of 50 |
| batch_is_condition | batch_confounding | 0/50 of 50 | 50/0 of 50 |
| panel_as_transcriptome | coverage | 0/50 of 50 | 50/0 of 50 |
| predicted_as_measured | coverage | 0/50 of 50 | 50/0 of 50 |
| merged_formulas | intervention_identity | 0/15 of 50 | 50/0 of 50 |
| validation_in_selection | leakage | 0/15 of 50 | 50/0 of 50 |

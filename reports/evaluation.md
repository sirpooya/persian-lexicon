# Persian dictionary evaluation

Run: 2026-10-27T07:21:04.596361+00:00. Dictionary SHA-256: `f69c8958fd2499475f9d2ba8caca828c16e5980c3a445d953f9743f9f2d02548`.

This measures dictionary membership after normalization. Correct forms should be accepted; the paired erroneous forms should be rejected. It does not measure automatic correction, suggestion ranking, contextual understanding, or end-to-end dictation accuracy.

| Dataset | Unique token pairs | Policy | Correct accepted | Error rejected | Both decisions right |
|---|---:|---|---:|---:|---:|
| faspell_main | 3775 | core | 90.12% | 92.98% | 83.28% |
| faspell_main | 3775 | dictionary | 96.50% | 77.01% | 73.72% |
| faspell_main | 3775 | all | 99.63% | 47.10% | 46.81% |
| faspell_ocr | 617 | core | 94.33% | 74.88% | 70.02% |
| faspell_ocr | 617 | dictionary | 96.76% | 59.48% | 56.56% |
| faspell_ocr | 617 | all | 99.03% | 46.03% | 45.06% |

## Data and interpretation

- **FaSpell main** contains naturally occurring errors from pupils and typists. The actual retrieved file has 4,858 rows; older descriptions report 5,050.
- **FaSpell OCR** is scored separately from human typing mistakes.
- **PerSpellData Virastman real-word** is diagnostic only. Its human-derived confusions occur in constructed sentences, and a spot check found suspect gold forms and additional edits. For example, the files label `سوم` → `سوءم` (line 7) and `آرشیو` → `آرشیءو` (line 17) as corrections. These are data-quality observations, not approved corrections. Real-word errors also need context. Do not use this subset to rank dictionary accuracy or to create overrides.
- Primary metrics use distinct normalized single-token pairs within each dataset. Repeated corpus occurrences cannot inflate the denominator. Normalization-equivalent pairs, boundary edits and invalid forms are counted separately.
- Membership uses the matching single-token exports: core, dictionary-supported, and full union. No frequency threshold was fitted on these datasets.
- Evaluation data is excluded from source merging and overrides. Upstream dictionary overlap with these public benchmarks is unknown, so this is not a guaranteed uncontaminated test.
- Token alignment for the sentence subset is heuristic. Every extracted span and excluded case is available for inspection. Source error-category values are retained without guessing undocumented category meanings.

## Counts and exclusions

- **faspell_main** (primary_human_errors): 4,858 input rows; 4,858 observed pairs/edit spans; 3,775 unique token pairs; 27 repeated token pairs. Classifications: `{"boundary_or_multiword": 1045, "invalid_correct_form:too_long_or_sentence": 1, "normalizer_rejects_error:non_persian_character_or_punctuation": 2, "resolved_by_normalization": 4, "single_token": 3802, "unchanged_pair": 4}`.
- **faspell_ocr** (secondary_ocr): 800 input rows; 800 observed pairs/edit spans; 617 unique token pairs; 3 repeated token pairs. Classifications: `{"boundary_or_multiword": 30, "normalizer_rejects_error:non_persian_character_or_punctuation": 150, "single_token": 620}`.
- **perspelldata_virastman_real** (diagnostic_only): 7,753 input rows; 10,882 observed pairs/edit spans; 3,272 unique token pairs; 5,859 repeated token pairs. Classifications: `{"boundary_or_multiword": 1594, "invalid_correct_form:contains_digits": 8, "invalid_correct_form:empty": 6, "invalid_correct_form:non_persian_character_or_punctuation": 35, "invalid_correct_form:too_long_or_sentence": 1, "normalizer_rejects_error:empty": 55, "normalizer_rejects_error:too_long_or_sentence": 1, "resolved_by_normalization": 51, "single_token": 9131}`.

## Context-dependent diagnostic (not accuracy evidence)

| Policy | Distinct token pairs | Correct-file form accepted | Wrong-file form rejected |
|---|---:|---:|---:|
| core | 3272 | 42.91% | 57.89% |
| dictionary | 3272 | 54.19% | 41.75% |
| all | 3272 | 66.84% | 21.97% |

## Files

Detailed results: `dist/evaluation`.
`cases.tsv` contains every observed case; `accepted-error-forms.tsv` and `missing-corrections.tsv` identify problems for inspection. These files are diagnostic evidence, not automatically approved dictionary changes.

Sources: [FaSpell original repository](https://lindat.mff.cuni.cz/repository/handle/11372/LRT-1547), [PerSpellData repository](https://github.com/rominaoji/PerSpellData), [PerSpellData paper](https://aclanthology.org/2021.nsurl-1.2/).

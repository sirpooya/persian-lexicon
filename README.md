# persian-lexicon

A combined **Persian spell-checking dictionary and lexicon**, with normalized
spellings, frequency ranking, and evidence identifying where each entry came from.

The build includes dictionary headwords, morphological forms, word collections and
corpus observations. These remain distinguishable: a large union contains errors
as well as useful vocabulary. Source agreement is evidence, not a guarantee of correctness.

The 2026-10-27 build merged **23 available data groups** into **2,852,812 distinct
entries**: 2,696,928 single tokens and 155,884 phrases. Of the single tokens,
570,457 have lexical-source support and 46,027 qualify for the conservative core.
The database retains 6,572,049 source-evidence rows and 547,083 changed spelling
variants. The review contains 82,552 spacing groups; the priority file shows the
first 500. These are measured build counts, not estimates of error-free vocabulary.

## Generated files

Completed files live in `dist/`, which is excluded from Git:

| File | Contents |
|---|---|
| `persian-all.tsv` | Complete normalized union, including candidates and phrases |
| `persian-dictionary.tsv` | Single-token words supported by at least one lexical source family |
| `persian-core.tsv` | Single-token words supported by at least two lexical families, plus explicit accept decisions |
| `persian.dic` / `persian.aff` | Core dictionary in Hunspell format |
| `persian-all.dic` / `persian-all.aff` | Broad single-token union; includes unverified forms and corpus noise |
| `phrases.tsv` | Multiword entries, kept out of the Hunspell files |
| `lexicon.sqlite` | Entries, source evidence, original counts, normalized variants and user decisions |
| `persian-all.jsonl.gz` | Compressed entry summaries |
| `review.tsv` | Observed spacing variants grouped for comparison |
| `review-priority.tsv` | First 500 spacing groups, ordered by frequency score |
| `rejected.jsonl.gz` | Rejected observations and reasons |
| `build-report.json` | Actual source counts, missing sources, contribution counts and output checksums |
| `sources.json` / `sources.lock.json` | Source configuration and exact downloaded-file hashes |

Each TSV entry has `word`, `frequency_score`, `status`, `source_count`,
`family_count`, and `lexical_families`. `core` and `dictionary` describe evidence
strength, not manual validation. `candidate` includes words found only in generated
forms, collections or corpora. `name` and `colloquial` are used when the source
provides those labels and no lexical evidence takes precedence. Many sources do
not label names or register, so those categories are incomplete.

## Normalization and frequency

The pipeline applies Unicode normalization, converts Arabic ي/ى/ك to Persian ی/ی/ک,
removes vowel marks, tatweel and formatting controls, and cleans repeated spacing.
It preserves meaningful hamza distinctions, آ, and internal half-spaces (ZWNJ).
`خانهٔ` and `خانۀ` share a normalized spelling. Missing half-spaces are not invented.
Non-Persian characters, digits and punctuation are rejected; original changed
spellings remain in SQLite's `variants` table.

Raw corpus counts are **not added together**. Each source ranks its positive counts;
ties share a rank. The exported score is the maximum across sources of
`round(1,000,000 / (1 + log2(rank)))`. Zero means no positive frequency evidence.
It is a relative ranking score, not a token count or probability. Original counts
remain in `evidence.raw_count`; wordfreq values are estimates per billion tokens.

Known related sources share a family. The Lilak builds, Hazm, Virastyar, Reza's
lists and Moein are grouped conservatively because of shared material. PerLex and
its UDLexicons/MULTEXT representations also share a family. Collection sources and
frequency sources do not promote a word into the core simply by repeating it.
Undocumented shared ancestry can still exist.

## Correcting disagreements

Start with `dist/review-priority.tsv`. Each row shows a spelling, the strongest
observed alternative, source counts, and a reason for review. Suggestions do not
change the dictionary. Spacing variants can have different meanings, so both may
be valid. The review covers spacing disagreements; it is not an exhaustive typo audit.

Save decisions in `config/overrides.tsv`, using these columns:

```tsv
word	action	replacement	note
وثیفه	replace	وثیقه	Example only: verify this decision before using it
```

The shipped overrides file has no decisions. Supported actions are `accept`
(include in the core), `reject` (remove from exports), and `replace` (merge evidence
into the chosen spelling while retaining the original variant). Replacement chains
and conflicting decisions are rejected. Only save decisions you have actually made;
do not blindly copy the suggested spelling column.

## Rebuild

Requires Python 3.10+ and the pinned package in `requirements.txt`. Hunspell is
optional for the engine smoke test; source expansion and Aspell decoding use the
project's data readers.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -I scripts/fetch_sources.py
.venv/bin/python -I scripts/build_lexicon.py --allow-unavailable
.venv/bin/python -I -m unittest discover -s tests -v
.venv/bin/python -I scripts/verify_build.py
```

The fetch command returns a nonzero status if any source is unavailable. Inspect
`config/sources.lock.json` for details. The build normally refuses missing source
files; `--allow-unavailable` explicitly permits them and records each omission.
Parser errors and checksum mismatches always stop the build. Previously generated
files in `dist/` are preserved if importing fails.

Downloads are pinned to Git revisions where possible and SHA-256 hashes otherwise.
A changed upstream file is refused when an existing hash is locked. Updating a
snapshot requires deliberately updating its manifest/lock entry. Raw data stays in
`data/raw/`; neither raw data nor build outputs are committed automatically.

Downloaded programs and SQL are never executed. Parsivar's pickle reader allows
only `collections.Counter`, then validates the payload. ZIP/TAR members are read
as data with fixed output paths. Hunspell expansion supports the exact finite
rule subset in the pinned Lilak builds and fails on unsupported rule structures.

## Coverage and remaining sources

The manifest covers Lilak, its Hunspell builds, PersianWordsLib, Hazm, Reza's
auxiliary lists, Apertium, Uppsala corpus words, word-list-fa, Moein, PerLex,
OpenSubtitles, Wikipedia and poetry frequencies, wordfreq, FarsNet, the Masterking
collection, Parsivar, Virastyar, Aspell, Kaikki/Wiktionary and UDLexicons.

MULTEXT-East's direct download returned a web page and its alternate official
vault requires access. Vafa, Zaya and FarsVajeh remain leads without verified
standalone downloads; ELRA requires separate acquisition. Their published counts
are not counted as merged data. Reza's slang resource contains regex rules and is
retained as reference data, not treated as a word list.

The dictionary is a first combined build, not a validated autocorrect model.
Hunspell compatibility is checked locally; macOS installation and end-to-end
dictation cleanup have not been tested. The original research is in
[AGENTS.md](AGENTS.md), with machine-readable configuration in
[config/sources.json](config/sources.json).

# AGENTS.md: persian-lexicon (one merged Persian word list for spell checking and autocorrect)

## Goal
Merge the free Persian word lists into one normalized, deduplicated lexicon with word
frequencies, usable as an autocorrect and spell-check dictionary. What matters most is
coverage: the number of real words. The first consumer is osx-transcribeX's Dictionary
(Persian dictation cleanup), so a format macOS can read directly is a plus.

## Status
- 2026-10-27: The user authorized building the combined dictionary from all available
  sources. The pipeline is implemented in `scripts/`, with pinned source configuration
  in `config/sources.json` and SHA-256 download records in `config/sources.lock.json`.
- Raw snapshots are in gitignored `data/raw/`. The combined exports, SQLite evidence
  database, review tables and measured source contributions are in gitignored `dist/`.
  `dist/build-report.json` is the authoritative record of each build's counts and omissions.
- First combined build: 2,852,812 normalized entries (2,696,928 single tokens and
  155,884 phrases), with 570,457 dictionary-supported single tokens and 46,027 core
  tokens. Broad coverage includes candidates; these numbers are not counts of verified
  correct words. There are 82,552 spacing-review groups, with 500 prioritized.
- Later 2026-10-27 build partially recovered Zaya through the pinned G2P derivative
  `mohamad-hasan-sohan-ajini/G2P` (`resources/FA/Lexicon.json`): 52,963 normalized
  headwords, including 5,800 new entries. The full original Zaya release is still
  unverified. Current totals: 2,858,612 entries, 2,702,728 single tokens, 155,884
  phrases, 583,539 dictionary-supported tokens and 60,586 core tokens. There are
  83,709 spacing-review groups. Actual build counts supersede older survey counts.
- 24 data groups are now imported; Reza's regex-only slang file is reference data.
  MULTEXT-East returned an HTML page instead of the archive and its alternate official
  vault requires access. Vafa, FarsVajeh and ELRA remain unavailable; Zaya is partially
  recovered. Retry evidence is in `reports/resource-recovery.json`.
- Additional retrieved sources include FarsNet 2.5 from `arashbehmand/farsnet`, the
  `masterking32/persian-wordlist` SQL data, and Persian UDLexicons from
  `https://pauillac.inria.fr/~sagot/UDLexicons.0.2.zip`. SQL is parsed, never executed.
- See README for build commands, export meanings and manual correction instructions.
- Dictionary evaluation is implemented in `scripts/evaluate_lexicon.py`, with a
  separate manifest and lock at `config/evaluation-sources*.json`. These files are
  evaluation-only and must never be added to the dictionary source manifest.
- On 3,775 distinct normalized FaSpell human-error token pairs, core coverage/error
  rejection is 90.12%/92.98%; dictionary-supported 96.50%/77.01%; full union
  99.63%/47.10%. These are membership measures, not automatic correction accuracy.
  Details, exclusions and input hashes are in `reports/evaluation.md` and `.json`.
- PerSpellData Virastman real-word data is diagnostic only. Suspect gold forms such
  as `سوم` → `سوءم` were found in its correct-file column, and real-word errors need
  context. Do not use its scores as headline accuracy or turn its pairs into overrides.

## Sources (surveyed 2026-10-27)
Counts are lines or entries as shipped, before normalization or cross-source
deduplication, except where a current normalized adapter count is explicitly noted.

| Source | Where the words are | Format | Words |
|---|---|---|---|
| Lilak | github.com/b00f/lilak `src/data/lexicon`, `src/data/dic_users` | lexicon with POS tags; user word list | 93,910 + 13,221 lines |
| Lilak as Hunspell | github.com/wooorm/dictionaries `dictionaries/fa/index.dic` + `index.aff` | Hunspell stems with affix flags | 104,024 stems; current adapter yields 1,513,813 unique normalized stems/forms |
| mnk-persian-words | github.com/masoudnk/PersianWordsLib `mnk_persian_words/data/words.db.zip` | SQLite `words.db`: tables `words`, `canonical_words` | 628,419 (516,482 canonical) |
| Parsivar spell data | github.com/ICTRC/Parsivar README links `https://www.dropbox.com/s/tlyvnzv1ha9y1kl/spell.zip?dl=1` (70 MB) | `onegram.pckl`: `[collections.Counter, corpus_token_total]`; `mybigram_lm.pckl` (165 MB) | 359,219 with counts |
| hazm | github.com/roshan-research/hazm `hazm/data/words.dat` | `word<TAB>freq<TAB>POS` | 193,750 |
| Virastyar | sourceforge.net/projects/virastyar/files/Data/1.3.1/persian.dic | UTF-8 with BOM, CRLF, `word<TAB>freq<TAB>POS` | 34,954 |

Known quality issues:
- The old 2.6M `unmunch` estimate is not a clean word count: the installed utility
  emits continuation flags as literal suffix text. The current adapter interprets the
  pinned non-crossing long-flag rules directly. All 515 sampled generated forms across
  the two builds were accepted by the Hunspell library; this is not linguistic validation.
  Generated forms remain candidates unless independently supported.
- Parsivar's counts come from a raw corpus and include typos (e.g. وثیفه for وثیقه).
- mnk was built for random text generation and includes diacritic and orthographic variants.
- titoBouzout/Dictionaries `Persian.dic` (103,923) is another Lilak build: a duplicate, skip it.

Not word lists, do not merge:
- Virastar (github.com/brothersincode/virastar, JavaScript): cleanup rules only
  (Arabic ي/ك to ی/ک, ZWNJ, punctuation, digits). Useful as the normalization reference.
- DadmaTools (github.com/Dadmatech/DadmaTools): its spell checker is a neural
  model with no word list.

Useful for evaluation only: PerSpellData (University of Tehran, NSURL 2021), about 6.4M
pairs of misspelled and corrected sentences.

## Additional research (2026-10-27)

The following sources were missing from the initial survey. The published counts and
remaining-check notes in this research table describe that earlier survey. Downloads
initially failed with DNS errors, but the later build retrieved most sources. Use
`dist/build-report.json` for measured normalized counts and source-exclusive coverage;
published counts below must not be substituted for build results.

| Source | Data and evidence | Relevance and remaining checks |
|---|---|---|
| PerLex / PerLex 2 | [2010 paper](https://pauillac.inria.fr/~sagot/pub/lrec10perlex.pdf): 35,914 lemma-level entries, 524,700 analyses, 494,488 distinct generated forms. [2011 paper](https://aclanthology.org/2011.jeptalnrecital-court.13/) describes partial manual validation and revised categories. | Significant morphology source. The 2010 counts must not be attributed to PerLex 2. Locate a complete downloadable release and check transliteration, version and generated-form quality. [Perso-Arabic conversion repository](https://github.com/behnam/perlex_transliterator) provides another lead. |
| Persian Aspell | [Debian source package](https://sources.debian.org/src/aspell-fa/0.11-0-4/) and [installed file inventory](https://packages.debian.org/en/trixie/all/aspell-fa/filelist). Upstream archive: `https://ftp.gnu.org/gnu/aspell/dict/fa/aspell6-fa-0.11-0.tar.bz2`. | Actual spell-checking resource with separate common, generic and scientific lists. Decode the compressed word lists with trusted Aspell tools; count and compare with Lilak before treating it as independent coverage. |
| reza1615/Persian-Spell-checker | [Repository](https://github.com/reza1615/Persian-Spell-checker): `fa_IR.dic`, `fa_IR.aff`, `Moin_Key_Words`, `Adjectives`, verb and slang lists. README reports 32,325 verbs and 3,172 adjectives in 2014. | Inspect auxiliary lists as well as the Hunspell pair. Those dated category counts are not a current dictionary total. Check overlap with Lilak and distinguish formal from colloquial spellings. |
| Apertium Iranian Persian | [Monolingual package](https://github.com/apertium/apertium-pes), especially `apertium-pes.pes.dix`. | Morphological analysis and generation with lemmas and paradigms. Extract Persian entries as data; validate generated forms. No current count independently verified. |
| samanvp/persian-lexicon | [Repository](https://github.com/samanvp/persian-lexicon): `data/persian-words.txt`; 70,528 unique words reported, derived from the Uppsala Persian Corpus. | Main list is filtered liberally. Length-specific game lists discard hamza and other characters, so they are unsuitable as the sole spelling source. Audit corpus noise and overlap. |
| mvalipour/word-list-fa | [Word file](https://github.com/mvalipour/word-list-fa/blob/master/words.txt), displayed as 7,217 lines by GitHub. | Small game-oriented supplement with visible fragments and nonstandard spellings; low priority for automatic acceptance. |
| MansourM/persian-to-persian-dictionary | [Repository](https://github.com/MansourM/persian-to-persian-dictionary): `moein/words.txt`; README describes over 20,000 words. | Extract headwords only, keeping definitions and synonym text out of the accepted-word list. Check archaic entries and multiword expressions. |
| Kaikki Persian / Wiktionary | [Persian extract](https://kaikki.org/dictionary/Persian/index.html): 17,651 distinct words shown on the page inspected, based on extraction dated 2026-10-25. JSONL is available. | Filter Persian-language lexical records, word forms, POS and usage tags. Exclude punctuation, isolated affixes and unsuitable historical forms. Sense counts are not unique-word counts. Other Wiktionary editions also contain Persian entries and will overlap. |

Frequency resources missing from the original notes:

- [hermitdave/FrequencyWords](https://github.com/hermitdave/FrequencyWords):
  [`content/2018/fa`](https://github.com/hermitdave/FrequencyWords/tree/master/content/2018/fa)
  contains `fa_50k.txt`, `fa_full.txt` and `fa_ignored.txt`. Word/count pairs come from
  OpenSubtitles. Useful for conversational ranking; subtitles also contain errors and
  names. Do not interpret the ignored list as accepted vocabulary.
- [behnam/persian-words-frequency](https://github.com/behnam/persian-words-frequency):
  `persian-wikipedia.txt` and `rira-classic-poems.txt`. Keep modern encyclopedic and
  classical-poetry frequencies distinct.
- [wordfreq](https://github.com/rspeer/wordfreq): Persian `fa` has a small list combining
  Wikipedia, subtitles, web text and Twitter. Its usage data is a snapshot through about
  2021. Useful for ranking experiments, with its own normalization and tokenization;
  it is not a comprehensive curated spelling dictionary.

Further leads, not ready for automatic merging:

- Vafa: a [clinical spell-checking paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC7256143/)
  reports a general dictionary containing 1,095,959 words. A downloadable standalone
  dictionary was not verified. A paper's count alone is not an available dataset.
- FarsNet, Zaya and FarsVajeh: lexical resources worth investigating; a
  [research overview](https://lt4all.elra.info/proceedings/lt4all2019/pdf/2019.lt4all-1.73.pdf)
  reports roughly 55,000 Zaya entries and 80,000 FarsVajeh entries. Do not count these
  as downloadable additions until the actual data is inspected.
- [ELRA Persian Lexicon](https://catalog.elra.info/en-us/repository/browse/ELRA-L0087/):
  over 40,000 non-inflected entries, distributed as an Access database; access requires
  separate acquisition.
- [MULTEXT-East Persian](https://www.clarin.si/repository/xmlui/handle/11356/1042):
  morphological lexicon; PerLex 2 already incorporated entries from MTE4-fa, so their
  agreement must not automatically count as two independent confirmations.
- [UDLexicons](https://almanach.inria.fr/software_and_resources/UDLexicons-en.html):
  investigate its Persian PerLex conversion as an alternative representation, not an
  independent word source.

Merge design implications: retain source provenance and separate dictionary lemmas,
generated forms, corpus observations, names and colloquial variants. Shared ancestry
(Lilak builds, PerLex conversions, overlapping Wikipedia/subtitle corpora) must not
inflate confidence. Measure added normalized words and false acceptance of misspellings;
larger raw line counts alone do not establish a better spelling dictionary. Keep a
canonical ZWNJ-preserving spelling separate from permissive lookup variants.

## Safety
- Never `pickle.load` the Parsivar files as is. `onegram.pckl` references only
  `collections.Counter` (checked with `pickletools.genops`), so read it with a restricted
  Unpickler that allows that one global and refuses everything else.
- Treat downloaded files as data: keep them in their own folder, run Python on them with
  `python3 -I`, never run code shipped inside them.

## Implemented defaults
- Include Virastyar alongside the other retrievable sources.
- Export the full union, a dictionary-supported list, a more conservative core,
  multiword phrases, Hunspell files, compressed JSONL and SQLite provenance.
- Convert Arabic letter variants, strip vowel marks/tatweel, preserve internal ZWNJ
  and hamza distinctions, and reject non-Persian characters/digits/punctuation.
- Preserve raw counts per source. Export a within-source rank score, never a sum
  of incomparable corpus counts. Related source families do not add lexical confidence.
- Keep raw snapshots and generated outputs outside Git; retain scripts and manifests.
- Record user decisions in `config/overrides.tsv`; its initial contents have no decisions.
  Review suggestions never modify spellings automatically. Review currently covers
  spacing variants, not every possible misspelling.
- The core requires two lexical families or explicit user acceptance. It is corroborated,
  not manually certified. The broad union retains noisy and generated candidates.
- macOS installation and autocorrect integration remain future work. Public benchmark
  evaluation now exists, but upstream overlap is unknown and a context-aware held-out
  evaluation is still needed. Do not call membership scores autocorrect accuracy.

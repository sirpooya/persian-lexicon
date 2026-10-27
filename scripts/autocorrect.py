"""Export literal, app-independent Persian character and half-space corrections."""
from __future__ import annotations

import csv
import itertools
import json
import unicodedata
from collections import Counter
from pathlib import Path

from fetch_sources import digest
from lexicon_core import LETTERS, TRANSLATE, ZWNJ, normalize

PREFIXES = frozenset(('می', 'نمی'))
SUFFIXES = frozenset(('ها', 'های', 'هایی', 'هایم', 'هایت', 'هایش', 'هایمان',
                     'هایتان', 'هایشان', 'تر', 'ترین', 'ام', 'ات', 'اش', 'ای',
                     'ایم', 'اید', 'اند'))


def affix_target(word):
    """Require every ZWNJ to border a listed outer affix, with one stem left.

    Strip suffix segments from the right, then an optional initial verb prefix.
    An interior free-word boundary disqualifies the entire target, including
    partially spaced variants. Target support alone does not prove morphology.
    """
    parts = word.split(ZWNJ)
    if len(parts) < 2 or not all(parts) or ' ' in word:
        return False
    while len(parts) > 1 and parts[-1] in SUFFIXES:
        parts.pop()
    if len(parts) > 1 and parts[0] in PREFIXES:
        parts.pop(0)
    return len(parts) == 1


def character_rules():
    rules = {}
    for code, replacement in TRANSLATE.items():
        source = chr(code)
        # ZWJ participates in emoji sequences; deleting it globally is unsafe.
        if source != '\u200d':
            rules[source] = replacement
    for code in [*range(0x064B, 0x0653), 0x0670]:
        rules[chr(code)] = ''
    # Literal forms cover Persian/Arabic presentation characters already handled
    # by the lexicon's NFKC step. Do not normalize unrelated scripts or whitespace.
    for start, stop in [(0xFB50, 0xFE00), (0xFE70, 0xFF00)]:
        for code in range(start, stop):
            source = chr(code)
            if unicodedata.normalize('NFKC', source) == source:
                continue
            target, reason = normalize(source)
            if not reason and target != source:
                rules[source] = target
    # These canonical base+mark spellings must be composed before ي is mapped.
    for letter in LETTERS:
        source = unicodedata.normalize('NFD', letter)
        if source != letter:
            target, reason = normalize(source)
            if not reason and target != source:
                rules[source] = target
    return dict(sorted(rules.items(), key=lambda row: (-len(row[0]), row[0])))


def variants(word):
    """Include missing, spaced and partially correct joiners independently."""
    parts = word.split(ZWNJ)
    for separators in itertools.product((ZWNJ, ' ', ''), repeat=len(parts) - 1):
        if all(separator == ZWNJ for separator in separators):
            continue
        yield ''.join(part + separator for part, separator in zip(parts, separators)) + parts[-1]


def export_autocorrect(db, folder: Path):
    supported = {row[0] for row in db.execute("SELECT word FROM entries WHERE status IN ('core','dictionary')")}
    targets = db.execute('''SELECT word,frequency_score FROM entries
      WHERE status IN ('core','dictionary') AND instr(word,' ')=0 AND instr(word,?)>0
      ORDER BY frequency_score DESC,lexical_families DESC,source_count DESC,word''', (ZWNJ,))
    chars = character_rules()
    selected = {}
    stats = Counter(supported_half_space_targets=0, eligible_affix_targets=0,
                    skipped_non_affix_targets=0)
    for target, score in targets:
        stats['supported_half_space_targets'] += 1
        if not affix_target(target):
            stats['skipped_non_affix_targets'] += 1
            continue
        stats['eligible_affix_targets'] += 1
        for source in variants(target):
            stats['candidate_variants'] += 1
            if source in supported:
                stats['skipped_supported_variants'] += 1
                continue
            if source in selected or source in chars:
                stats['duplicate_from_candidates'] += 1
                continue
            selected[source] = (target, score)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'persian-autocorrect.tsv'
    temporary = path.with_suffix('.tsv.part')
    with temporary.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
        writer.writerow(['from', 'to', 'match'])
        # Character rules have priority above word frequencies, and compose
        # multi-codepoint sequences first. All word rows follow descending score.
        for source, target in chars.items():
            writer.writerow([source, target, 'char'])
        for source, (target, score) in sorted(selected.items(), key=lambda row: (-row[1][1], row[0])):
            writer.writerow([source, target, 'word'])
    temporary.replace(path)
    stats['char_rows'] = len(chars)
    stats['word_rows'] = len(selected)
    stats['rows'] = len(chars) + len(selected)
    result = {**dict(stats), 'bytes': path.stat().st_size, 'sha256': digest(path),
              'word_target_policy': 'Every joiner must attach an outer می/نمی prefix or an explicitly allowed suffix; reject targets with any free-word boundary.',
              'prefixes': sorted(PREFIXES), 'suffixes': sorted(SUFFIXES),
              'word_variant_policy': 'For eligible affix targets, each joiner independently stays ZWNJ, becomes a space, or is removed; exclude unchanged form.',
              'collision_policy': 'Highest target frequency_score; ties use lexical_families, source_count, then target spelling.',
              'sort_policy': 'Character normalization first; word rules in descending target frequency_score.'}
    path.with_suffix('.tsv.sha256').write_text(result['sha256'] + '  ' + path.name + '\n')
    (folder / 'persian-autocorrect.stats.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f"persian-autocorrect.tsv: {result['rows']:,} rows, {result['bytes']:,} bytes "
          f"({result['char_rows']:,} char / {result['word_rows']:,} word)", flush=True)
    return result


def verify_autocorrect(db, folder: Path):
    path = folder / 'persian-autocorrect.tsv'
    stats = json.loads((folder / 'persian-autocorrect.stats.json').read_text())
    assert digest(path) == stats['sha256']
    assert path.with_suffix('.tsv.sha256').read_text() == stats['sha256'] + '  ' + path.name + '\n'
    supported = {word: (status, score) for word, status, score in db.execute(
        "SELECT word,status,frequency_score FROM entries WHERE status IN ('core','dictionary')")}
    expected_chars = character_rules()
    seen, chars_seen, word_rules = set(), {}, {}
    words_started = False
    previous_score = None
    counts = Counter()
    with path.open(encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream, delimiter='\t')
        assert reader.fieldnames == ['from', 'to', 'match']
        for row in reader:
            source, target, kind = row['from'], row['to'], row['match']
            assert source and source != target and source not in seen, source
            seen.add(source)
            counts[kind] += 1
            if kind == 'char':
                assert not words_started
                assert expected_chars.get(source) == target, repr(source)
                chars_seen[source] = target
            else:
                assert kind == 'word'
                words_started = True
                assert target in supported and ZWNJ in target and ' ' not in target, target
                assert affix_target(target), (source, target, 'non-affix join')
                assert source not in supported, source
                # A valid variant may retain some existing half-spaces. Restore
                # missing/spaced joiners only; never remove one or change letters.
                assert source in set(variants(target)), (source, target)
                word_rules[source] = target
                score = supported[target][1]
                assert previous_score is None or score <= previous_score
                previous_score = score
    assert chars_seen == expected_chars
    assert counts['char'] == stats['char_rows'] and counts['word'] == stats['word_rows']
    assert sum(counts.values()) == stats['rows'] and path.stat().st_size == stats['bytes']
    # Verify completeness as well as validity: every eligible variant must occur,
    # unless it is protected or the same source chose a higher-frequency target.
    target_count = eligible_count = 0
    for target, (_, score) in supported.items():
        if ZWNJ not in target or ' ' in target:
            continue
        target_count += 1
        if not affix_target(target):
            continue
        eligible_count += 1
        for source in variants(target):
            if source in supported:
                assert source not in word_rules
            else:
                assert source in word_rules, (source, target, 'missing correction')
                assert supported[word_rules[source]][1] >= score, (source, 'lower-frequency collision winner')
    assert target_count == stats['supported_half_space_targets']
    assert eligible_count == stats['eligible_affix_targets']
    assert target_count - eligible_count == stats.get('skipped_non_affix_targets', 0)
    print(f"persian-autocorrect.tsv: {stats['rows']:,} rows, {stats['bytes']:,} bytes; "
          'unique sources, affix-only targets, protected words/phrases and ordering verified.', flush=True)
    return stats

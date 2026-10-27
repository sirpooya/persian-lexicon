#!/usr/bin/env python3
"""Measure dictionary coverage and error rejection on locked Persian error data."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_sources import digest, validate_download
from lexicon_core import ROOT, normalize

POLICIES = ('core', 'dictionary', 'all')
TOKEN = re.compile(r'[\w\u200c\u064b-\u0655\u0670]+|[^\w\s]', re.UNICODE)
ROLES = {'faspell_main': 'primary_human_errors', 'faspell_ocr': 'secondary_ocr',
         'perspelldata_virastman_real': 'diagnostic_only'}


@dataclass(frozen=True)
class Case:
    dataset: str
    kind: str
    wrong: str
    correct: str
    category: str
    location: str


def faspell_cases(folder):
    for filename, dataset, kind in [('faspell_main.txt', 'faspell_main', 'naturally_occurring_human'),
                                    ('faspell_ocr.txt', 'faspell_ocr', 'ocr')]:
        with (folder / filename).open(encoding='utf-8-sig', newline='') as stream:
            reader = csv.DictReader(stream, delimiter='\t')
            for number, row in enumerate(reader, 2):
                if None in row or not row.get('#misspelt') or not row.get('corrected'):
                    raise ValueError(f'Invalid FaSpell row: {filename}:{number}')
                yield Case(dataset, kind, row['#misspelt'], row['corrected'],
                           row.get('error-category', 'ocr'), f'{filename}:{number}')


def sentence_cases(wrong_path, correct_path):
    """Align changed token spans; keep boundary edits distinct from token edits.

    Virastman's confusions came from humans; the corpus sentences are constructed.
    These must never be described as original log sentences or non-word errors.
    """
    wrong_lines = wrong_path.read_text(encoding='utf-8-sig').splitlines()
    correct_lines = correct_path.read_text(encoding='utf-8-sig').splitlines()
    if len(wrong_lines) != len(correct_lines):
        raise ValueError('Parallel sentence files have unequal line counts')
    for number, (wrong, correct) in enumerate(zip(wrong_lines, correct_lines), 1):
        left, right = TOKEN.findall(wrong), TOKEN.findall(correct)
        for tag, a, b, c, d in SequenceMatcher(a=left, b=right, autojunk=False).get_opcodes():
            if tag == 'equal':
                continue
            yield Case('perspelldata_virastman_real', 'human_derived_real_word_confusions',
                       ' '.join(left[a:b]), ' '.join(right[c:d]), tag,
                       f'parallel-line:{number}:tokens:{a}-{b}:{c}-{d}')


def classify(case):
    wrong, wrong_reason = normalize(case.wrong)
    correct, correct_reason = normalize(case.correct)
    if case.wrong == case.correct:
        category = 'unchanged_pair'
    elif correct_reason:
        category = 'invalid_correct_form:' + correct_reason
    elif wrong_reason:
        category = 'normalizer_rejects_error:' + wrong_reason
    elif wrong == correct:
        category = 'resolved_by_normalization'
    elif ' ' in wrong or ' ' in correct:
        category = 'boundary_or_multiword'
    else:
        category = 'single_token'
    return wrong, correct, category


def accepted(status, policy):
    if status is None:
        return False
    if policy == 'core':
        return status == 'core'
    if policy == 'dictionary':
        return status in ('core', 'dictionary')
    return True


def score_pairs(pairs, statuses):
    """Pairs are distinct normalized (wrong, correct) single-token pairs."""
    scores = {policy: Counter() for policy in POLICIES}
    for wrong, correct in sorted(set(pairs)):
        for policy in POLICIES:
            good_hit = accepted(statuses.get(correct), policy)
            bad_hit = accepted(statuses.get(wrong), policy)
            score = scores[policy]
            score['pairs'] += 1
            score['correct_accepted'] += good_hit
            score['error_accepted'] += bad_hit
            score['correct_rejected'] += not good_hit
            score['error_rejected'] += not bad_hit
            score['pair_separated'] += good_hit and not bad_hit
            score['both_accepted'] += good_hit and bad_hit
            score['both_rejected'] += not good_hit and not bad_hit
            score['only_error_accepted'] += not good_hit and bad_hit
    result = {}
    for policy, values in scores.items():
        n = values['pairs']
        item = {key: values[key] for key in (
            'pairs', 'correct_accepted', 'error_accepted', 'correct_rejected', 'error_rejected',
            'pair_separated', 'both_accepted', 'both_rejected', 'only_error_accepted')}
        for name, numerator in [('correct_coverage', 'correct_accepted'),
                                ('error_rejection', 'error_rejected'),
                                ('error_acceptance', 'error_accepted'),
                                ('correct_false_alarm', 'correct_rejected'),
                                ('pair_separation', 'pair_separated')]:
            item[name] = values[numerator] / n if n else None
        result[policy] = item
    return result


def fetch_statuses(db, words):
    statuses = {}
    ordered = sorted(words)
    for offset in range(0, len(ordered), 500):
        batch = ordered[offset:offset + 500]
        statuses.update(db.execute('SELECT word,status FROM entries WHERE word IN (' +
                                   ','.join('?' for _ in batch) + ')', batch))
    return statuses


def verify_inputs(manifest, lock):
    locked = {(r['source'], r['path']): r for r in lock['files']}
    inputs = []
    for source in manifest['sources']:
        for item in source['files']:
            path = ROOT / 'data/raw' / source['id'] / item['path']
            record = locked.get((source['id'], item['path']), {})
            if record.get('status') != 'downloaded' or not path.is_file():
                raise ValueError(f'Evaluation input unavailable: {path}')
            validate_download(path, path.suffix)
            if digest(path) != record.get('sha256'):
                raise ValueError(f'Evaluation checksum mismatch: {path}')
            inputs.append({k: record[k] for k in ('source', 'path', 'sha256', 'url')})
    return inputs


def percent(value):
    return 'n/a' if value is None else f'{value:.2%}'


def markdown_report(report):
    lines = ['# Persian dictionary evaluation', '',
             f"Run: {report['evaluated_at']}. Dictionary SHA-256: `{report['dictionary_sha256']}`.", '',
             'This measures dictionary membership after normalization. Correct forms should be accepted; '
             'the paired erroneous forms should be rejected. It does not measure automatic correction, '
             'suggestion ranking, contextual understanding, or end-to-end dictation accuracy.', '',
             '| Dataset | Unique token pairs | Policy | Correct accepted | Error rejected | Both decisions right |',
             '|---|---:|---|---:|---:|---:|']
    for name, dataset in report['datasets'].items():
        if dataset['role'] == 'diagnostic_only':
            continue
        for policy, score in dataset['unique_token_pair_metrics'].items():
            lines.append(f"| {name} | {score['pairs']} | {policy} | {percent(score['correct_coverage'])} | "
                         f"{percent(score['error_rejection'])} | {percent(score['pair_separation'])} |")
    lines += ['', '## Data and interpretation', '',
              '- **FaSpell main** contains naturally occurring errors from pupils and typists. '
              'The actual retrieved file has 4,858 rows; older descriptions report 5,050.',
              '- **FaSpell OCR** is scored separately from human typing mistakes.',
              '- **PerSpellData Virastman real-word** is diagnostic only. Its human-derived '
              'confusions occur in constructed sentences, and a spot check found suspect gold forms '
              'and additional edits. For example, the files label `سوم` → `سوءم` (line 7) and '
              '`آرشیو` → `آرشیءو` (line 17) as corrections. These are data-quality observations, '
              'not approved corrections. Real-word errors also need context. Do not use this subset '
              'to rank dictionary accuracy or to create overrides.',
              '- Primary metrics use distinct normalized single-token pairs within each dataset. '
              'Repeated corpus occurrences cannot inflate the denominator. Normalization-equivalent '
              'pairs, boundary edits and invalid forms are counted separately.',
              '- Membership uses the matching single-token exports: core, dictionary-supported, and full union. '
              'No frequency threshold was fitted on these datasets.',
              '- Evaluation data is excluded from source merging and overrides. Upstream dictionary overlap '
              'with these public benchmarks is unknown, so this is not a guaranteed uncontaminated test.',
              '- Token alignment for the sentence subset is heuristic. Every extracted span and excluded '
              'case is available for inspection. Source error-category values are retained without guessing '
              'undocumented category meanings.', '',
              '## Counts and exclusions', '']
    for name, dataset in report['datasets'].items():
        lines.append(f"- **{name}** ({dataset['role']}): {dataset['input_rows']:,} input rows; "
                     f"{dataset['observations']:,} observed pairs/edit spans; "
                     f"{dataset['unique_token_pairs']:,} unique token pairs; "
                     f"{dataset['duplicate_token_pairs']:,} repeated token pairs. "
                     f"Classifications: `{json.dumps(dataset['case_classes'], ensure_ascii=False, sort_keys=True)}`.")
    lines += ['', '## Context-dependent diagnostic (not accuracy evidence)', '',
              '| Policy | Distinct token pairs | Correct-file form accepted | Wrong-file form rejected |',
              '|---|---:|---:|---:|']
    for name, dataset in report['datasets'].items():
        if dataset['role'] == 'diagnostic_only':
            for policy, score in dataset['unique_token_pair_metrics'].items():
                lines.append(f"| {policy} | {score['pairs']} | {percent(score['correct_coverage'])} | "
                             f"{percent(score['error_rejection'])} |")
    lines += ['', '## Files', '',
              f"Detailed results: `{report['output_directory']}`.",
              '`cases.tsv` contains every observed case; `accepted-error-forms.tsv` and '
              '`missing-corrections.tsv` identify problems for inspection. These files are diagnostic '
              'evidence, not automatically approved dictionary changes.', '',
              'Sources: [FaSpell original repository](https://lindat.mff.cuni.cz/repository/handle/11372/LRT-1547), '
              '[PerSpellData repository](https://github.com/rominaoji/PerSpellData), '
              '[PerSpellData paper](https://aclanthology.org/2021.nsurl-1.2/).', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT / 'dist/lexicon.sqlite')
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/evaluation')
    parser.add_argument('--summary', type=Path, default=ROOT / 'reports/evaluation.md')
    args = parser.parse_args()
    manifest_path = ROOT / 'config/evaluation-sources.json'
    lock_path = ROOT / 'config/evaluation-sources.lock.json'
    manifest, lock = json.loads(manifest_path.read_text()), json.loads(lock_path.read_text())
    inputs = verify_inputs(manifest, lock)
    cases = []
    input_counts = {}
    for source in manifest['sources']:
        folder = ROOT / 'data/raw' / source['id']
        if source['parser'] == 'faspell':
            cases.extend(faspell_cases(folder))
            for filename, dataset in [('faspell_main.txt', 'faspell_main'), ('faspell_ocr.txt', 'faspell_ocr')]:
                with (folder / filename).open(encoding='utf-8-sig', newline='') as stream:
                    input_counts[dataset] = sum(1 for _ in csv.DictReader(stream, delimiter='\t'))
        elif source['parser'] == 'parallel_sentences':
            cases.extend(sentence_cases(folder / 'DataSet/real-word/virastman/wrong_virastman_real.txt',
                                        folder / 'DataSet/real-word/virastman/correct_virastman_real.txt'))
            input_counts['perspelldata_virastman_real'] = len(
                (folder / 'DataSet/real-word/virastman/correct_virastman_real.txt').read_text().splitlines())
        else:
            raise ValueError('Unknown evaluation parser')
    classified = [(case, *classify(case)) for case in cases]
    words = {word for _, wrong, correct, _ in classified for word in (wrong, correct) if word}
    db = sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    statuses = fetch_statuses(db, words)
    report = {'schema_version': 1, 'evaluated_at': datetime.now(timezone.utc).isoformat(),
              'dictionary_sha256': digest(args.database),
              'manifest_sha256': digest(manifest_path), 'lock_sha256': digest(lock_path),
              'inputs': inputs, 'datasets': {},
              'output_directory': str(args.output.relative_to(ROOT) if args.output.is_relative_to(ROOT) else args.output),
              'scope': 'dictionary membership, not correction accuracy',
              'evaluation_data_merged': False,
              'limitations': ['Unknown overlap between upstream dictionaries and public benchmarks.',
                              'Real-word errors require context; rejection alone is not a spelling truth judgment.',
                              'Boundary errors require tokenization/segmentation and are outside token metrics.',
                              'Repeated pairs are deduplicated; no population error prevalence is assumed.',
                              'Sentence edit alignment is heuristic.']}
    for dataset in sorted({case.dataset for case in cases}):
        subset = [row for row in classified if row[0].dataset == dataset]
        token_rows = [row for row in subset if row[3] == 'single_token']
        pairs = {(wrong, correct) for _, wrong, correct, _ in token_rows}
        categories = sorted({row[0].category for row in token_rows})
        report['datasets'][dataset] = {
            'kind': subset[0][0].kind, 'role': ROLES[dataset],
            'input_rows': input_counts[dataset], 'observations': len(subset),
            'case_classes': dict(Counter(row[3] for row in subset)),
            'unique_token_pairs': len(pairs), 'duplicate_token_pairs': len(token_rows) - len(pairs),
            'unique_token_pair_metrics': score_pairs(pairs, statuses),
            'by_source_category': {category: score_pairs(
                {(w, c) for case, w, c, _ in token_rows if case.category == category}, statuses)
                for category in categories}}
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'cases.tsv').open('w', encoding='utf-8', newline='') as stream, \
         (args.output / 'accepted-error-forms.tsv').open('w', encoding='utf-8', newline='') as errors, \
         (args.output / 'missing-corrections.tsv').open('w', encoding='utf-8', newline='') as missing:
        writer, error_writer, missing_writer = [csv.writer(f, delimiter='\t', lineterminator='\n')
                                                 for f in (stream, errors, missing)]
        writer.writerow(['dataset', 'role', 'kind', 'category', 'wrong_raw', 'correct_raw', 'wrong', 'correct',
                         'classification', 'location', *[p + '_correct_accepted' for p in POLICIES],
                         *[p + '_error_accepted' for p in POLICIES]])
        diagnostic_header = ['dataset', 'role', 'policy', 'wrong', 'correct', 'category', 'location', 'sources_json']
        error_writer.writerow(diagnostic_header)
        missing_writer.writerow(diagnostic_header)
        emitted = set()
        for case, wrong, correct, classification in classified:
            # Export policy files contain single tokens only; boundary rows are diagnostic.
            correct_hits = [bool(correct and ' ' not in correct and accepted(statuses.get(correct), p)) for p in POLICIES]
            error_hits = [bool(wrong and ' ' not in wrong and accepted(statuses.get(wrong), p)) for p in POLICIES]
            writer.writerow([case.dataset, ROLES[case.dataset], case.kind, case.category, case.wrong, case.correct,
                             wrong, correct, classification, case.location, *correct_hits, *error_hits])
            if classification != 'single_token':
                continue
            for policy, good_hit, bad_hit in zip(POLICIES, correct_hits, error_hits):
                key = (case.dataset, policy, wrong, correct)
                if key in emitted:
                    continue
                emitted.add(key)
                if bad_hit:
                    sources = list(db.execute('SELECT source_id,raw_count FROM evidence WHERE word=? ORDER BY source_id', (wrong,)))
                    error_writer.writerow([case.dataset, ROLES[case.dataset], policy, wrong, correct, case.category, case.location,
                                           json.dumps(sources, ensure_ascii=False, separators=(',', ':'))])
                if not good_hit:
                    sources = list(db.execute('SELECT source_id FROM evidence WHERE word=? ORDER BY source_id', (correct,)))
                    missing_writer.writerow([case.dataset, ROLES[case.dataset], policy, wrong, correct, case.category, case.location,
                                             json.dumps(sources, ensure_ascii=False, separators=(',', ':'))])
    db.close()
    report['artifacts'] = {p.name: {'bytes': p.stat().st_size, 'sha256': digest(p)}
                           for p in sorted(args.output.glob('*.tsv'))}
    (args.output / 'evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    text = markdown_report(report)
    (args.output / 'evaluation.md').write_text(text)
    args.summary.parent.mkdir(parents=True, exist_ok=True)
    args.summary.write_text(text)
    args.summary.with_suffix('.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(text, flush=True)


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""Build a provenance-preserving Persian lexicon from locked local snapshots."""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import shutil
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

# -I excludes the script directory; only add our own trusted implementation.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_sources import digest, validate_download
from lexicon_core import ROOT, ZWNJ, normalize, records, spacing_key
from autocorrect import export_autocorrect

ORIGINS = {'lexical': 1, 'generated': 2, 'frequency': 4,
           'collection': 8, 'name': 16, 'colloquial': 32}
UPSERT = '''INSERT INTO evidence VALUES (?,?,?,?,?,?)
 ON CONFLICT(source_id,word) DO UPDATE SET
 flags=evidence.flags | excluded.flags,
 raw_count=max(evidence.raw_count,excluded.raw_count)'''


def load_overrides(path):
    decisions = {}
    with path.open(encoding='utf-8-sig') as stream:
        for row in csv.DictReader(stream, delimiter='\t'):
            if not row.get('word'):
                continue
            word, reason = normalize(row['word'])
            action = row['action'].strip()
            replacement, _ = normalize(row.get('replacement', ''))
            if reason or word != row['word'] or action not in ('accept', 'reject', 'replace'):
                raise ValueError(f'Invalid override: {row}')
            if word in decisions or (action == 'replace' and (not replacement or replacement == word)):
                raise ValueError(f'Duplicate or invalid replacement: {row}')
            decisions[word] = (action, replacement, row.get('note', ''))
    for word, (action, target, _) in decisions.items():
        if action == 'replace' and target in decisions and decisions[target][0] != 'accept':
            raise ValueError(f'Replacement chains/conflicting decisions are forbidden: {word}')
    return decisions


def create_database(path):
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=DELETE')
    db.execute('PRAGMA synchronous=NORMAL')
    db.execute('PRAGMA cache_size=-96000')
    db.execute('PRAGMA temp_store=FILE')
    db.executescript('''
      CREATE TABLE sources(id TEXT PRIMARY KEY, name TEXT, family TEXT, role TEXT, status TEXT);
      CREATE TABLE evidence(source_id TEXT, word TEXT, flags INTEGER, raw_count REAL,
        pos TEXT, location TEXT, PRIMARY KEY(source_id,word)) WITHOUT ROWID;
      CREATE TABLE variants(source_id TEXT, raw_word TEXT, word TEXT,
        PRIMARY KEY(source_id,raw_word,word)) WITHOUT ROWID;
      CREATE TABLE decisions(word TEXT PRIMARY KEY, action TEXT, replacement TEXT, note TEXT);
      CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT);
    ''')
    return db


def ingest(db, source, decisions, rejected):
    counts = Counter()
    batch, variants = [], []

    def flush():
        db.executemany(UPSERT, batch)
        db.executemany('INSERT OR IGNORE INTO variants VALUES (?,?,?)', variants)
        batch.clear()
        variants.clear()

    for observation in records(source):
        counts['observations'] += 1
        word, reason = normalize(observation.word)
        if not math.isfinite(observation.count) or observation.count < 0:
            raise ValueError('Non-finite or negative source frequency')
        if word in decisions:
            action, replacement, _ = decisions[word]
            if action == 'reject':
                reason = 'user_rejected'
            elif action == 'replace':
                word = replacement
        if reason:
            counts['rejected'] += 1
            counts['reject:' + reason] += 1
            rejected.write(json.dumps({'source': source['id'], 'raw': observation.word,
                                       'reason': reason, 'location': observation.location},
                                      ensure_ascii=False) + '\n')
            continue
        counts['retained_observations'] += 1
        batch.append((source['id'], word, ORIGINS[observation.origin], observation.count,
                      observation.pos or '', observation.location))
        if observation.word != word:
            variants.append((source['id'], observation.word, word))
            counts['normalized_observations'] += 1
        if len(batch) >= 10000:
            flush()
    flush()
    counts['unique_normalized_entries'] = db.execute(
        'SELECT count(*) FROM evidence WHERE source_id=?', (source['id'],)).fetchone()[0]
    return dict(counts)


def finalize_entries(db, decisions):
    print('Combining provenance and ranking frequencies...', flush=True)
    db.execute('CREATE INDEX evidence_word ON evidence(word)')
    db.executescript('''
      CREATE TABLE entries AS SELECT e.word,
        count(*) AS source_count,
        count(DISTINCT s.family) AS family_count,
        count(DISTINCT CASE WHEN e.flags & 1 THEN s.family END) AS lexical_families,
        max((e.flags & 16) != 0) AS has_name,
        max((e.flags & 32) != 0) AS has_colloquial,
        max((e.flags & 61) != 0) AS observed,
        0 AS frequency_score, '' AS status, '' AS spacing_key
      FROM evidence e JOIN sources s ON s.id=e.source_id GROUP BY e.word;
      CREATE UNIQUE INDEX entries_word ON entries(word);
    ''')
    # Counts from unrelated corpora are incomparable. Rank within each source,
    # then use the best score, so copying a corpus never adds frequency weight.
    db.create_function('rank_score', 1, lambda rank: round(1_000_000 / (1 + math.log2(rank))), deterministic=True)
    db.executescript('''
      CREATE TEMP TABLE ranks AS
        SELECT word, rank_score(rank() OVER (PARTITION BY source_id ORDER BY raw_count DESC)) AS score
        FROM evidence WHERE raw_count > 0;
      CREATE TEMP TABLE scores AS SELECT word,max(score) AS score FROM ranks GROUP BY word;
      CREATE UNIQUE INDEX scores_word ON scores(word);
      UPDATE entries SET frequency_score=coalesce((SELECT score FROM scores WHERE scores.word=entries.word),0);
      DROP TABLE scores;
      DROP TABLE ranks;
      UPDATE entries SET status=CASE
        WHEN lexical_families>=2 THEN 'core'
        WHEN lexical_families=1 THEN 'dictionary'
        WHEN has_name THEN 'name'
        WHEN has_colloquial THEN 'colloquial'
        ELSE 'candidate' END;
    ''')
    for word, (action, replacement, note) in decisions.items():
        db.execute('INSERT INTO decisions VALUES (?,?,?,?)', (word, action, replacement, note))
        if action == 'accept':
            db.execute("INSERT OR IGNORE INTO entries VALUES (?,0,0,0,0,0,1,0,'core','')", (word,))
            db.execute("UPDATE entries SET status='core' WHERE word=?", (word,))
    db.create_function('spacing_key', 1, spacing_key, deterministic=True)
    db.execute('UPDATE entries SET spacing_key=spacing_key(word)')
    db.execute('CREATE INDEX entries_spacing ON entries(spacing_key) WHERE observed=1')
    db.execute('CREATE INDEX entries_status ON entries(status)')
    db.commit()


def write_review(db, folder):
    """Group observed spacing variants; suggestions never change accepted data."""
    groups = db.execute('''SELECT spacing_key FROM entries WHERE observed=1
        GROUP BY spacing_key HAVING count(*)>1
        ORDER BY max(frequency_score) DESC,spacing_key''')
    header = ['group', 'word', 'suggested_spelling', 'status', 'frequency_score',
              'lexical_families', 'source_counts_json', 'reason', 'action', 'replacement', 'note']
    rows_count = groups_count = 0
    with (folder / 'review.tsv').open('w', encoding='utf-8', newline='') as full, \
         (folder / 'review-priority.tsv').open('w', encoding='utf-8', newline='') as priority:
        writers = [csv.writer(stream, delimiter='\t') for stream in (full, priority)]
        for writer in writers:
            writer.writerow(header)
        for (key,) in groups:
            choices = db.execute('''SELECT word,status,frequency_score,lexical_families,family_count
              FROM entries WHERE spacing_key=? AND observed=1
              ORDER BY lexical_families DESC,frequency_score DESC,family_count DESC,word''', (key,)).fetchall()
            groups_count += 1
            suggested = choices[0][0]
            for word, status, score, families, _ in choices:
                sources = dict(db.execute('SELECT source_id,raw_count FROM evidence WHERE word=? ORDER BY source_id', (word,)))
                row = [groups_count, word, suggested, status, score, families,
                       json.dumps(sources, ensure_ascii=False, separators=(',', ':')),
                       'spacing_variants; suggestion_only; different meanings may be valid', '', '', '']
                writers[0].writerow(row)
                if groups_count <= 500:
                    writers[1].writerow(row)
                rows_count += 1
    return {'spacing_groups': groups_count, 'spacing_rows': rows_count,
            'priority_groups': min(500, groups_count)}


def export(db, folder):
    print('Writing dictionary exports and review table...', flush=True)
    counts = Counter()
    header = ['word', 'frequency_score', 'status', 'source_count', 'family_count', 'lexical_families']
    paths = ['persian-all.tsv', 'persian-dictionary.tsv', 'persian-core.tsv', 'phrases.tsv']
    streams = [(folder / name).open('w', encoding='utf-8', newline='') for name in paths]
    try:
        writers = [csv.writer(stream, delimiter='\t', lineterminator='\n') for stream in streams]
        for writer in writers:
            writer.writerow(header)
        with gzip.open(folder / 'persian-all.jsonl.gz', 'wt', encoding='utf-8', compresslevel=6) as jsonl:
            for row in db.execute('''SELECT word,frequency_score,status,source_count,family_count,lexical_families
                                      FROM entries ORDER BY word'''):
                word, _, status, *_ = row
                writers[0].writerow(row)
                counts['all_entries'] += 1
                counts['status:' + status] += 1
                jsonl.write(json.dumps(dict(zip(header, row)), ensure_ascii=False, separators=(',', ':')) + '\n')
                if ' ' in word:
                    writers[3].writerow(row)
                    counts['phrases'] += 1
                else:
                    counts['single_tokens'] += 1
                    if status in ('core', 'dictionary'):
                        writers[1].writerow(row)
                        counts['dictionary_tokens'] += 1
                    if status == 'core':
                        writers[2].writerow(row)
                        counts['core_tokens'] += 1
    finally:
        for stream in streams:
            stream.close()
    affix = 'SET UTF-8\nWORDCHARS ' + ZWNJ + '\n'
    for name, condition in [('persian', "status='core'"), ('persian-all', '1=1')]:
        condition += " AND instr(word,' ')=0"
        number = db.execute('SELECT count(*) FROM entries WHERE ' + condition).fetchone()[0]
        with (folder / (name + '.dic')).open('w', encoding='utf-8') as stream:
            stream.write(str(number) + '\n')
            for (word,) in db.execute('SELECT word FROM entries WHERE ' + condition + ' ORDER BY word'):
                stream.write(word + '\n')
        (folder / (name + '.aff')).write_text(affix, encoding='utf-8')
    autocorrect = export_autocorrect(db, folder)
    counts['autocorrect_rows'] = autocorrect['rows']
    counts['autocorrect_word_rows'] = autocorrect['word_rows']
    counts['autocorrect_char_rows'] = autocorrect['char_rows']
    counts['autocorrect_bytes'] = autocorrect['bytes']
    return dict(counts)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-unavailable', action='store_true',
                        help='Build available sources and report every missing source explicitly')
    args = parser.parse_args()
    started = time.monotonic()
    manifest_path = ROOT / 'config/sources.json'
    lock_path = ROOT / 'config/sources.lock.json'
    manifest = json.loads(manifest_path.read_text())
    lock = json.loads(lock_path.read_text())
    locked = {(r['source'], r['path']): r for r in lock['files']}
    decisions = load_overrides(ROOT / 'config/overrides.tsv')
    staging = ROOT / 'data/build'
    staging.mkdir(parents=True, exist_ok=True)
    # Only delete an earlier generated staging database, never input data.
    for name in ('lexicon.sqlite', 'lexicon.sqlite-journal'):
        (staging / name).unlink(missing_ok=True)
    db = create_database(staging / 'lexicon.sqlite')
    report = {'schema_version': 1, 'built_at': datetime.now(timezone.utc).isoformat(),
              'manifest_sha256': digest(manifest_path), 'lock_sha256': digest(lock_path),
              'overrides_sha256': digest(ROOT / 'config/overrides.tsv'),
              'sources': [], 'unresolved_leads': manifest.get('unresolved_leads', []),
              'frequency_score': 'max over sources of round(1000000/(1+log2(rank))); ties share rank',
              'core_rule': 'at least two lexical source families, or explicit user acceptance',
              'limitations': ['Core is corroborated, not manually certified.',
                             'Family independence is approximate; undocumented shared ancestry may remain.',
                             'Names and colloquial labels depend on available source metadata.',
                             'Review tables cover spacing conflicts, not every possible spelling error.',
                             'Broad union includes unverified generated forms and corpus noise.']}
    failed = False
    with gzip.open(staging / 'rejected.jsonl.gz', 'wt', encoding='utf-8') as rejected:
        for source in manifest['sources']:
            ident = source['id']
            item = {'id': ident, 'name': source['name'], 'family': source['family']}
            errors = []
            for artifact in source['files']:
                record = locked.get((ident, artifact['path']), {})
                path = ROOT / 'data/raw' / ident / artifact['path']
                if record.get('status') != 'downloaded' or not path.is_file():
                    errors.append(f"{artifact['path']}: {record.get('error', 'not downloaded')}")
                elif digest(path) != record.get('sha256'):
                    raise ValueError(f'Locked snapshot checksum mismatch: {path}')
                else:
                    try:
                        validate_download(path, path.suffix)
                    except ValueError as error:
                        errors.append(f'{artifact["path"]}: {error}')
            if errors:
                item.update(status='unavailable', errors=errors)
                failed |= not args.allow_unavailable
            else:
                print('Importing ' + ident + '...', flush=True)
                db.execute('SAVEPOINT source_import')
                try:
                    stats = ingest(db, source, decisions, rejected)
                    if not stats.get('retained_observations') and source['parser'] != 'regex_resource':
                        raise ValueError('Word source produced no retained Persian entries')
                    item.update(status='reference_only' if source['parser'] == 'regex_resource' else 'included', **stats)
                    db.execute('RELEASE source_import')
                except Exception as error:
                    db.execute('ROLLBACK TO source_import')
                    db.execute('RELEASE source_import')
                    item.update(status='error', errors=[f'{type(error).__name__}: {error}'])
                    failed = True
            db.execute('INSERT INTO sources VALUES (?,?,?,?,?)',
                       (ident, source['name'], source['family'], source['role'], item['status']))
            db.commit()
            report['sources'].append(item)
            print(json.dumps(item, ensure_ascii=False), flush=True)
    if failed:
        (staging / 'build-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        db.close()
        raise SystemExit('Build failed; source errors are in data/build/build-report.json. Existing dist was preserved.')
    finalize_entries(db, decisions)
    report['counts'] = export(db, staging)
    report['review'] = write_review(db, staging)
    report['counts']['evidence_rows'] = db.execute('SELECT count(*) FROM evidence').fetchone()[0]
    report['counts']['normalized_variants'] = db.execute('SELECT count(*) FROM variants').fetchone()[0]
    # Incremental coverage depends on manifest order; exclusive coverage does not.
    print('Measuring source contributions...', flush=True)
    db.executescript('''CREATE TEMP TABLE contribution AS
      SELECT e.source_id,count(*) AS exclusive_entries FROM evidence e
      JOIN entries w ON w.word=e.word WHERE w.source_count=1 GROUP BY e.source_id;''')
    exclusive = dict(db.execute('SELECT * FROM contribution'))
    for source in report['sources']:
        source['exclusive_entries'] = exclusive.get(source['id'], 0)
    report['elapsed_seconds'] = round(time.monotonic() - started, 2)
    db.execute('INSERT INTO metadata VALUES (?,?)', ('build_report', json.dumps(report, ensure_ascii=False)))
    db.commit()
    if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
        raise RuntimeError('SQLite integrity check failed')
    db.close()
    for name in ('sources.json', 'sources.lock.json', 'overrides.tsv'):
        shutil.copyfile(ROOT / 'config' / name, staging / name)
    report['artifacts'] = {path.name: {'bytes': path.stat().st_size, 'sha256': digest(path)}
                           for path in sorted(staging.iterdir()) if path.is_file() and path.name != 'build-report.json'}
    (staging / 'build-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    destination = ROOT / 'dist'
    destination.mkdir(exist_ok=True)
    for path in staging.iterdir():
        if path.is_file():
            path.replace(destination / path.name)
    print(json.dumps(report['counts'], ensure_ascii=False, indent=2), flush=True)
    print('Build complete: ' + str(destination), flush=True)


if __name__ == '__main__':
    main()

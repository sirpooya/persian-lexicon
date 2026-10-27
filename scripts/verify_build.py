#!/usr/bin/env python3
"""Verify exported counts, ordering, provenance, checksums and Hunspell loading."""
import csv
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_sources import digest
from lexicon_core import ROOT, normalize
from autocorrect import verify_autocorrect


def main():
    folder = ROOT / 'dist'
    report = json.loads((folder / 'build-report.json').read_text())
    for name, expected in report['artifacts'].items():
        path = folder / name
        assert path.stat().st_size == expected['bytes'], name
        assert digest(path) == expected['sha256'], name
    db = sqlite3.connect((folder / 'lexicon.sqlite').as_uri() + '?mode=ro', uri=True)
    checks = [('persian-all.tsv', 'all_entries'), ('persian-core.tsv', 'core_tokens'),
              ('persian-dictionary.tsv', 'dictionary_tokens'), ('phrases.tsv', 'phrases')]
    for filename, key in checks:
        count, previous = 0, ''
        with (folder / filename).open(encoding='utf-8') as stream:
            for row in csv.DictReader(stream, delimiter='\t'):
                word = row['word']
                assert word > previous, (filename, word, 'duplicate or unsorted')
                assert normalize(word) == (word, None), (filename, word)
                previous = word
                count += 1
        assert count == report['counts'][key], (filename, count)
        print(f'{filename}: {count:,} sorted, unique Persian entries', flush=True)
    assert db.execute('SELECT count(*) FROM entries').fetchone()[0] == report['counts']['all_entries']
    assert db.execute('''SELECT count(*) FROM evidence e LEFT JOIN entries w ON w.word=e.word
                         WHERE w.word IS NULL''').fetchone()[0] == 0
    verify_autocorrect(db, folder)
    for name, key in [('persian', 'core_tokens'), ('persian-all', 'single_tokens')]:
        with (folder / (name + '.dic')).open(encoding='utf-8') as stream:
            declared = int(next(stream))
            actual = sum(1 for _ in stream)
        assert actual == declared == report['counts'][key], name
    command = shutil.which('hunspell')
    if command:
        words = ['سلام', 'کتاب', 'خانه', 'فارسی', 'می\u200cروم']
        included = [w for w in words if db.execute("SELECT 1 FROM entries WHERE word=? AND status='core'", (w,)).fetchone()]
        assert len(included) >= 3, 'Common-word coverage unexpectedly low'
        invalid = 'ژژژژژژژژژژژژژژژژژژژژ'
        result = subprocess.run([command, '-i', 'UTF-8', '-d', str(folder / 'persian'), '-l'],
                                input='\n'.join(included + [invalid]) + '\n', text=True,
                                capture_output=True, timeout=60, check=True)
        assert result.stdout.strip().splitlines() == [invalid], (result.stdout, result.stderr)
        print('Hunspell loads the core, accepts common words and rejects the synthetic nonword.')
    db.close()
    print('All build checks passed.')


if __name__ == '__main__':
    main()

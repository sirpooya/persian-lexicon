#!/usr/bin/env python3
"""Add or refresh the autocorrect table without rebuilding the source lexicon."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from autocorrect import export_autocorrect, verify_autocorrect
from fetch_sources import digest
from lexicon_core import ROOT


def main():
    folder = ROOT / 'dist'
    db = sqlite3.connect((folder / 'lexicon.sqlite').as_uri() + '?mode=ro&immutable=1', uri=True)
    stats = export_autocorrect(db, folder)
    verify_autocorrect(db, folder)
    db.close()
    report_path = folder / 'build-report.json'
    report = json.loads(report_path.read_text())
    report['autocorrect'] = stats
    for key in ('rows', 'word_rows', 'char_rows', 'bytes'):
        report['counts']['autocorrect_' + key] = stats[key]
    for name in ('persian-autocorrect.tsv', 'persian-autocorrect.tsv.sha256', 'persian-autocorrect.stats.json'):
        path = folder / name
        report['artifacts'][name] = {'bytes': path.stat().st_size, 'sha256': digest(path)}
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()

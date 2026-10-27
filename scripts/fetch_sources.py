#!/usr/bin/env python3
"""Fetch dictionary data without importing or executing upstream programs."""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def validate_download(path: Path, suffix: str) -> None:
    with path.open('rb') as stream:
        prefix = stream.read(1024).lstrip().lower()
    if (b'<!doctype html' in prefix or b'<html' in prefix) and suffix not in ('.htm', '.html'):
        raise ValueError('Server returned HTML instead of data')
    magic = {'.gz': b'\x1f\x8b', '.zip': b'pk\x03\x04', '.bz2': b'bzh'}
    if suffix in magic and not prefix.startswith(magic[suffix]):
        raise ValueError('Invalid archive signature')


def fetch_one(source: dict, item: dict, prior: dict) -> dict:
    relative = Path(item['path'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Unsafe source path')
    target = ROOT / 'data/raw' / source['id'] / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    record = {'source': source['id'], 'path': item['path'], 'url': item['url']}
    expected = prior.get('sha256')
    if target.is_file() and expected and digest(target) == expected:
        try:
            validate_download(target, relative.suffix)
            return {**prior, **record, 'status': 'downloaded'}
        except ValueError as error:
            return {**prior, **record, 'status': 'unavailable', 'error': str(error)}
    temporary = target.with_name(target.name + '.part')
    failures = []
    for url in [item['url'], *item.get('mirrors', [])]:
        command = ['curl', '--fail', '--location', '--silent', '--show-error',
                   '--retry', '2', '--connect-timeout', '20', '--max-time', '1800',
                   '--proto', '=https', '--proto-redir', '=https', url, '-o', str(temporary)]
        try:
            process = subprocess.run(command, capture_output=True, text=True, timeout=5500)
            if process.returncode:
                failures.append(process.stderr.strip()[-400:])
                continue
            validate_download(temporary, relative.suffix)
            actual = digest(temporary)
            if expected and actual != expected:
                failures.append('SHA-256 changed; refusing to replace the locked snapshot')
                continue
            temporary.replace(target)
            return {**record, 'resolved_url': url, 'status': 'downloaded', 'sha256': actual,
                    'bytes': target.stat().st_size,
                    'fetched_at': datetime.now(timezone.utc).isoformat()}
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            failures.append(str(error))
    if temporary.exists():
        temporary.unlink()
    return {**prior, **record, 'status': 'unavailable', 'error': '; '.join(failures)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', action='append', help='Fetch only these source IDs')
    parser.add_argument('--workers', type=int, default=6)
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'config/sources.json').read_text())
    lock_path = ROOT / 'config/sources.lock.json'
    prior = json.loads(lock_path.read_text()) if lock_path.exists() else {'files': []}
    records = {(r['source'], r['path']): r for r in prior['files']}
    jobs = [(s, f) for s in manifest['sources'] for f in s.get('files', [])
            if not args.source or s['id'] in args.source]
    def run(job):
        s, f = job
        return fetch_one(s, f, records.get((s['id'], f['path']), {}))
    failures = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        for result in executor.map(run, jobs):
            records[(result['source'], result['path'])] = result
            failures += result['status'] != 'downloaded'
            print(f"{result['status']}: {result['source']}/{result['path']}", flush=True)
    lock_path.write_text(json.dumps({'schema_version': 1,
                                    'files': sorted(records.values(), key=lambda x: (x['source'], x['path']))},
                                   ensure_ascii=False, indent=2) + '\n')
    print(f'{len(jobs) - failures}/{len(jobs)} files available. See config/sources.lock.json.')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())

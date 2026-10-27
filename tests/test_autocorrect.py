import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from autocorrect import SUFFIXES, affix_target, character_rules, export_autocorrect, verify_autocorrect
from fetch_sources import digest

FREE_PHRASES = ('به صورت', 'به نظر', 'در حال حاضر', 'انجام دادیم',
                'داشته باشیم', 'قرار گرفتند', 'این رو')


class AutocorrectTests(unittest.TestCase):
    def test_protected_words_phrases_partial_joiners_and_collisions(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE entries(word TEXT,status TEXT,frequency_score INTEGER,lexical_families INTEGER,source_count INTEGER)')
        db.executemany('INSERT INTO entries VALUES (?,?,?,?,?)', [
            ('تیم\u200cها', 'core', 100, 2, 3),
            ('لباس\u200cتر', 'dictionary', 200, 1, 1),
            ('لباس تر', 'dictionary', 1, 1, 1),
            ('می\u200cروم', 'core', 90, 2, 3),
            ('میروم', 'dictionary', 1, 1, 1),
            ('تیم\u200cهای\u200cام', 'dictionary', 50, 1, 1),
            ('کار\u200cخانه', 'dictionary', 10, 1, 1),
            ('می\u200cتیم\u200cها', 'dictionary', 40, 1, 1),
            ('می\u200cتیمها', 'dictionary', 5, 1, 1),
            ('خطا\u200cها', 'candidate', 1000, 0, 1),
        ])
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            stats = export_autocorrect(db, folder)
            verify_autocorrect(db, folder)
            with (folder / 'persian-autocorrect.tsv').open() as stream:
                rows = list(csv.DictReader(stream, delimiter='\t'))
            words = {r['from']: r['to'] for r in rows if r['match'] == 'word'}
            self.assertEqual(words['تیم ها'], 'تیم\u200cها')
            self.assertEqual(words['تیمها'], 'تیم\u200cها')
            self.assertNotIn('لباس تر', words)
            self.assertNotIn('میروم', words)
            self.assertEqual(words['می روم'], 'می\u200cروم')
            self.assertNotIn('خطاها', words)
            self.assertEqual(words['تیم\u200cهایام'], 'تیم\u200cهای\u200cام')
            self.assertEqual(words['تیم های ام'], 'تیم\u200cهای\u200cام')
            self.assertEqual(words['تیمهایام'], 'تیم\u200cهای\u200cام')
            self.assertNotIn('کارخانه', words)
            self.assertNotIn('کار خانه', words)
            # Synthetic ambiguous segmentation exercises frequency precedence.
            self.assertEqual(words['میتیمها'], 'می\u200cتیم\u200cها')
            self.assertEqual(len(rows), len({r['from'] for r in rows}))
            self.assertEqual(stats['rows'], len(rows))
        db.close()

    def test_free_phrases_are_never_joined_even_with_supported_targets(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE entries(word TEXT,status TEXT,frequency_score INTEGER,lexical_families INTEGER,source_count INTEGER)')
        targets = [phrase.replace(' ', '\u200c') for phrase in FREE_PHRASES]
        # No source phrase is present: rejection must come from the affix gate,
        # rather than the existing supported-input protection masking the bug.
        targets += ['به\u200cصورت\u200cها', 'می\u200cبه\u200cنظر', 'کار\u200cخانه']
        db.executemany('INSERT INTO entries VALUES (?,\'dictionary\',100,1,1)', [(t,) for t in targets])
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            stats = export_autocorrect(db, folder)
            verify_autocorrect(db, folder)
            with (folder / 'persian-autocorrect.tsv').open() as stream:
                rows = list(csv.DictReader(stream, delimiter='\t'))
            words = {r['from']: r['to'] for r in rows if r['match'] == 'word'}
            for phrase in FREE_PHRASES:
                with self.subTest(phrase=phrase):
                    self.assertNotIn(phrase, words)
                    self.assertNotIn(phrase.replace(' ', ''), words)
            self.assertNotIn('به صورت\u200cها', words)
            self.assertNotIn('به\u200cصورت ها', words)
            self.assertEqual(stats['word_rows'], 0)
        db.close()

    def test_only_enumerated_outer_affixes_are_eligible(self):
        for suffix in SUFFIXES:
            with self.subTest(suffix=suffix):
                self.assertTrue(affix_target('کتاب\u200c' + suffix))
        for prefix in ('می', 'نمی'):
            self.assertTrue(affix_target(prefix + '\u200cروم'))
            self.assertTrue(affix_target(prefix + '\u200cروم\u200cها'))
        for target in ('کتاب', 'کتاب\u200c', '\u200cکتاب', 'کتاب\u200cخانه',
                       'کتاب\u200cها\u200cخانه', 'به\u200cصورت\u200cها', 'کتاب\u200cمی'):
            with self.subTest(target=target):
                self.assertFalse(affix_target(target))

    def test_verifier_rejects_a_free_word_rule(self):
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE entries(word TEXT,status TEXT,frequency_score INTEGER,lexical_families INTEGER,source_count INTEGER)')
        db.execute("INSERT INTO entries VALUES ('به‌صورت','dictionary',100,1,1)")
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            export_autocorrect(db, folder)
            path = folder / 'persian-autocorrect.tsv'
            with path.open('a') as stream:
                stream.write('به صورت\tبه‌صورت\tword\n')
            # Refresh hashes so verification must detect invalid morphology.
            stats = json.loads((folder / 'persian-autocorrect.stats.json').read_text())
            stats.update(sha256=digest(path), bytes=path.stat().st_size)
            path.with_suffix('.tsv.sha256').write_text(stats['sha256'] + '  ' + path.name + '\n')
            (folder / 'persian-autocorrect.stats.json').write_text(json.dumps(stats))
            with self.assertRaisesRegex(AssertionError, 'non-affix join'):
                verify_autocorrect(db, folder)
        db.close()

    def test_char_rules_are_literal_and_compose_before_mapping_yeh(self):
        rules = character_rules()
        for source, target in [('ي', 'ی'), ('ى', 'ی'), ('ك', 'ک'), ('ـ', ''), ('ِ', '')]:
            self.assertEqual(rules[source], target)
        self.assertNotIn('\u200c', rules)
        self.assertNotIn('\u200d', rules)
        text = 'ئ كِتابـ ﻛ أ'
        for source, target in rules.items():
            text = text.replace(source, target)
        self.assertEqual(text, 'ئ کتاب ک أ')


if __name__ == '__main__':
    unittest.main()

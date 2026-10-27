import csv
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from autocorrect import character_rules, export_autocorrect, verify_autocorrect


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
            ('به\u200cهم\u200cریخته', 'dictionary', 50, 1, 1),
            ('کار\u200cخانه', 'dictionary', 10, 1, 1),
            ('ک\u200cارخانه', 'dictionary', 5, 1, 1),
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
            self.assertNotIn('خطاها', words)
            self.assertEqual(words['به\u200cهمریخته'], 'به\u200cهم\u200cریخته')
            self.assertEqual(words['به هم ریخته'], 'به\u200cهم\u200cریخته')
            self.assertEqual(words['بههمریخته'], 'به\u200cهم\u200cریخته')
            self.assertEqual(words['کارخانه'], 'کار\u200cخانه')
            self.assertEqual(len(rows), len({r['from'] for r in rows}))
            self.assertEqual(stats['rows'], len(rows))
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

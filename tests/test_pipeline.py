import io
import pickle
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from lexicon_core import Observation, decode_prezip, hunspell_records, load_counter, normalize, spacing_key
from build_lexicon import create_database, finalize_entries, ingest, load_overrides
from fetch_sources import validate_download


class NormalizationTests(unittest.TestCase):
    def test_letter_and_diacritic_normalization(self):
        self.assertEqual(normalize(' كِتاب يكيـ '), ('کتاب یکی', None))
        self.assertEqual(normalize('خانۀ'), normalize('خانهٔ'))

    def test_joiner_preserved_but_not_invented(self):
        self.assertEqual(normalize(' می \u200c\u200c روم '), ('می\u200cروم', None))
        self.assertNotEqual(normalize('میروم'), normalize('می\u200cروم'))
        self.assertEqual(spacing_key('می روم'), spacing_key('می\u200cروم'))

    def test_distinct_letters_not_collapsed(self):
        for left, right in [('آب', 'اب'), ('مؤمن', 'مومن'), ('خواست', 'خاست'), ('سؤال', 'سوال')]:
            self.assertNotEqual(normalize(left), normalize(right))

    def test_bad_tokens(self):
        for word in ['word', 'سلام123', '۱۲', 'xسلام', 'سلام!', '\u0654سلام', '']:
            self.assertIsNone(normalize(word)[0], word)
        for word in ['سلام', 'که', 'و', 'نرم\u200cافزار', 'آب و هوا']:
            self.assertEqual(normalize(word), (word, None))


class DataSafetyTests(unittest.TestCase):
    def test_hunspell_continuations_are_not_literal_suffixes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'fa.dic').write_text('1\nکتاب/pl\n')
            (folder / 'fa.aff').write_text('FLAG long\nSFX pl N 1\nSFX pl 0 ها/ps .\nSFX ps N 1\nSFX ps 0 ی .\n')
            source = {'id': 'fixture', 'expand': True,
                      'files': [{'path': 'fa.dic'}, {'path': 'fa.aff'}]}
            result = list(hunspell_records(folder, source))
            self.assertEqual([(r.word, r.origin) for r in result],
                             [('کتاب', 'lexical'), ('کتابها', 'generated'), ('کتابهای', 'generated')])
            (folder / 'fa.aff').write_text('FLAG long\nSFX pl Y 1\nSFX pl 0 ها .\n')
            with self.assertRaises(ValueError):
                list(hunspell_records(folder, source))

    def test_restricted_pickle(self):
        counter = Counter({'سلام': 10})
        for value in [counter, [counter, 10]]:
            self.assertEqual(load_counter(io.BytesIO(pickle.dumps(value))), counter)
        # Resolving even a harmless foreign global must be forbidden.
        with self.assertRaises(pickle.UnpicklingError):
            load_counter(io.BytesIO(pickle.dumps(Path)))
        with self.assertRaises(ValueError):
            load_counter(io.BytesIO(pickle.dumps({'سلام': -1})))

    def test_prezip_byte_prefix_and_escapes(self):
        # UTF-8 prefixes can end in the middle of a code point.
        self.assertEqual(list(decode_prezip(b'\x02\x00\xd8\xa2\xd8\xa8\x03\xaa\x1f\xff')), ['آب', 'آت'])
        self.assertEqual(list(decode_prezip(b'\x02\x00a\x1f\x29b\x1f\xff')), ['a\tb'])
        self.assertEqual(list(decode_prezip(b'\x02\x00' + b'a'*35 + b'\x1e\x05b\x1f\xff')),
                         ['a'*35, 'a'*35 + 'b'])
        for data in [b'\x02\x00abc', b'\x02\x04a\x1f\xff', b'\x02\x00a\x1f\x01']:
            with self.assertRaises(ValueError):
                list(decode_prezip(data))

    def test_html_disguised_as_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'file.gz'
            path.write_bytes(b'<?xml version="1.0"?><!DOCTYPE html><html>Login</html>')
            with self.assertRaises(ValueError):
                validate_download(path, '.gz')


class MergeTests(unittest.TestCase):
    def test_dedup_family_agreement_and_frequency(self):
        with tempfile.TemporaryDirectory() as directory:
            db = create_database(Path(directory) / 'test.sqlite')
            for ident, family, observations in [
                ('a', 'shared', [Observation('كتاب', 100), Observation('کتاب', 60), Observation('آب', 20)]),
                ('b', 'shared', [Observation('کتاب', 100)]),
                ('c', 'independent', [Observation('آب', 20), Observation('خطاا', 200, origin='frequency')])
            ]:
                source = {'id': ident}
                with patch('build_lexicon.records', return_value=iter(observations)):
                    ingest(db, source, {}, io.StringIO())
                db.execute('INSERT INTO sources VALUES (?,?,?,?,?)', (ident, ident, family, 'lexical', 'included'))
            finalize_entries(db, {})
            self.assertEqual(db.execute("SELECT raw_count FROM evidence WHERE source_id='a' AND word='کتاب'").fetchone(), (100,))
            self.assertEqual(db.execute("SELECT status,source_count,lexical_families FROM entries WHERE word='کتاب'").fetchone(), ('dictionary', 2, 1))
            self.assertEqual(db.execute("SELECT status FROM entries WHERE word='آب'").fetchone(), ('core',))
            self.assertEqual(db.execute("SELECT status FROM entries WHERE word='خطاا'").fetchone(), ('candidate',))
            self.assertEqual(db.execute("SELECT frequency_score FROM entries WHERE word='کتاب'").fetchone(), (1000000,))
            self.assertEqual(db.execute('SELECT raw_word,word FROM variants').fetchall(), [('كتاب', 'کتاب')])
            db.close()

    def test_overrides_are_applied_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'overrides.tsv'
            path.write_text('word\taction\treplacement\tnote\nغلط\treplace\tدرست\tchosen\nحذف\treject\t\t\nجدید\taccept\t\t\n')
            decisions = load_overrides(path)
            db = create_database(Path(directory) / 'test.sqlite')
            with patch('build_lexicon.records', return_value=iter([Observation('غلط'), Observation('حذف')])):
                ingest(db, {'id': 'test'}, decisions, io.StringIO())
            db.execute("INSERT INTO sources VALUES ('test','test','test','lexical','included')")
            finalize_entries(db, decisions)
            self.assertEqual(set(db.execute('SELECT word FROM entries')), {('درست',), ('جدید',)})
            self.assertEqual(db.execute("SELECT status FROM entries WHERE word='جدید'").fetchone(), ('core',))
            db.close()
            path.write_text('word\taction\treplacement\tnote\nغلط\treplace\tدرست\t\nدرست\treplace\tغلط\t\n')
            with self.assertRaises(ValueError):
                load_overrides(path)


if __name__ == '__main__':
    unittest.main()

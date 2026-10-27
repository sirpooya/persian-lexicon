import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_lexicon import Case, classify, score_pairs, sentence_cases
from fetch_sources import digest, fetch_one
from lexicon_core import records


class EvaluationTests(unittest.TestCase):
    def test_coverage_and_error_rejection_are_distinct(self):
        # Feed normalized pairs as the evaluator does. Repeated pairs must not inflate results.
        pairs = [('کتااب', 'کتاب'), ('کتااب', 'کتاب'), ('کتابب', 'کتاب'), ('خاطا', 'ناشناخته')]
        result = score_pairs(pairs, {'کتاب': 'core', 'کتااب': 'candidate', 'خاطا': 'dictionary'})
        self.assertEqual(result['all']['pairs'], 3)
        self.assertEqual(result['all']['correct_coverage'], 2 / 3)
        self.assertEqual(result['all']['error_rejection'], 1 / 3)
        self.assertEqual(result['core']['error_rejection'], 1)
        self.assertEqual(result['core']['pair_separation'], 2 / 3)
        self.assertEqual(result['dictionary']['error_rejection'], 2 / 3)
        self.assertIsNone(score_pairs([], {})['all']['error_rejection'])

    def test_harmless_normalization_and_boundaries_are_not_token_errors(self):
        for wrong, correct, expected in [
            ('كتاب', 'کتاب', 'resolved_by_normalization'),
            ('کتاب', 'کتاب', 'unchanged_pair'),
            ('می روم', 'می\u200cروم', 'boundary_or_multiword'),
            ('کتااب', 'کتاب', 'single_token'),
            ('کتاب!', 'کتاب', 'normalizer_rejects_error:non_persian_character_or_punctuation')
        ]:
            case = Case('fixture', 'natural', wrong, correct, '', '')
            self.assertEqual(classify(case)[2], expected)

    def test_parallel_alignment_preserves_boundary_spans(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            wrong, correct = folder / 'wrong', folder / 'correct'
            wrong.write_text('این کتااب است.\nمن میروم خانه\n')
            correct.write_text('این کتاب است.\nمن می روم خانه\n')
            result = list(sentence_cases(wrong, correct))
            self.assertEqual([(r.wrong, r.correct) for r in result],
                             [('کتااب', 'کتاب'), ('میروم', 'می روم')])
            self.assertEqual(classify(result[1])[2], 'boundary_or_multiword')
            correct.write_text('فقط یک خط\n')
            with self.assertRaises(ValueError):
                list(sentence_cases(wrong, correct))

    def test_bad_cached_download_can_recover(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder / 'data/raw/fixture/words.txt'
            path.parent.mkdir(parents=True)
            path.write_text('<?xml version="1.0"?><html>Unavailable</html>')
            prior = {'sha256': digest(path), 'status': 'unavailable'}
            def download(command, **kwargs):
                Path(command[-1]).write_text('کتاب\n')
                class Result:
                    returncode = 0
                return Result()
            with patch('fetch_sources.ROOT', folder), patch('fetch_sources.subprocess.run', side_effect=download):
                result = fetch_one({'id': 'fixture'}, {'path': 'words.txt', 'url': 'https://example.test/words.txt'}, prior)
            self.assertEqual(result['status'], 'downloaded')
            self.assertEqual(path.read_text(), 'کتاب\n')
            self.assertNotEqual(result['sha256'], prior['sha256'])

    def test_zaya_only_extracts_headwords(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            path = folder / 'data/raw/fixture/resources/FA/Lexicon.json'
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps([['کتاب', 'k e t A b'], ['کتاب', 'k e t A b e']]))
            with patch('lexicon_core.ROOT', folder):
                result = list(records({'id': 'fixture', 'parser': 'zaya_g2p'}))
            self.assertEqual([r.word for r in result], ['کتاب', 'کتاب'])
            self.assertTrue(all(r.count == 0 for r in result))


if __name__ == '__main__':
    unittest.main()

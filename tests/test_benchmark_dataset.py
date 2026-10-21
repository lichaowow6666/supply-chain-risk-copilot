"""Essential schema, provenance and fixture-scope checks for the authored suite."""
from copy import deepcopy
import unittest

from pe_copilot.chat.benchmark_dataset import CATEGORY_COUNTS, load_benchmark, validate_benchmark


class BenchmarkDatasetTests(unittest.TestCase):
    def test_hundred_distinct_versioned_references_and_balanced_coverage(self):
        suite = load_benchmark()
        self.assertEqual(len(suite['cases']), 100)
        self.assertEqual(len({case['question'].casefold() for case in suite['cases']}), 100)
        self.assertEqual(suite['category_counts'], CATEGORY_COUNTS)
        self.assertEqual(suite['language_counts'], {'en': 90, 'zh': 10})
        self.assertEqual(suite['review_status'], 'authored_not_human_reviewed')
        self.assertEqual(suite['fixture_contract']['forecast']['horizon'], 5)
        self.assertTrue(all(case['reference_basis'] for case in suite['cases']))
        before = deepcopy(suite)
        validate_benchmark(suite)
        self.assertEqual(suite, before)

    def test_rejects_duplicate_questions_unknown_provenance_and_wrong_scenario_scope(self):
        suite = load_benchmark()
        mutations = [
            lambda data: data['cases'][1].update(question=data['cases'][0]['question']),
            lambda data: data['cases'][0].update(reference_basis=['unknown-disclosure']),
            lambda data: data['cases'][46].update(scope='bq'),
            lambda data: data.update(base_suite_sha256='0' * 64),
            lambda data: data.update(review_status='human_certified'),
        ]
        for change in mutations:
            candidate = deepcopy(suite)
            change(candidate)
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    validate_benchmark(candidate)

    def test_rejects_unsupported_actions_nonfinite_amounts_and_unscoped_history(self):
        suite = load_benchmark()
        mutations = [
            lambda data: data['cases'][0]['expected'].update(actions=['execute_sql']),
            lambda data: data['cases'][0]['expected'].update(numeric_facts=[{'label':'spend','value':float('nan')}]),
            lambda data: data['cases'][0]['setup'].update(history=[{'role':'assistant','text':'Synthetic fact','scope':'unknown'}]),
            lambda data: data['cases'][0]['expected'].update(must_preserve_workspace=False),
        ]
        for change in mutations:
            candidate = deepcopy(suite)
            change(candidate)
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    validate_benchmark(candidate)


if __name__ == '__main__':
    unittest.main()

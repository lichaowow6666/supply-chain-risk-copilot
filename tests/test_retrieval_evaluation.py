import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from pe_copilot.evaluation import build_fixture_index, fixture_agent, load_suite, run_baseline


class ScopedRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.suite = load_suite()
        self.index, self.citations = build_fixture_index(self.suite, self.directory.name)
        self.agent = fixture_agent(self.suite, self.index)

    def test_selected_quarter_overrides_prepared_annual_baseline(self):
        hits = self.agent.query_10k('copper spend', 'synta', accession='synthetic-a-2026q2')
        self.assertTrue(hits)
        self.assertTrue(all(h['accession'] == 'synthetic-a-2026q2' for h in hits))
        text = ' '.join(h['text'] for h in hits)
        self.assertIn('USD 30 million', text)
        self.assertNotIn('USD 100 million', text)
        self.assertTrue(all(h['document_type'] == '10-Q' for h in hits))
        self.assertTrue(all(h['period_end'] == '2026-06-30' for h in hits))
        self.assertTrue(all(h['cik'] == '9000000001' for h in hits))

    def test_scope_switches_do_not_reuse_previous_evidence(self):
        for ticker, accession, amount in [('SYNTA','synthetic-a-2025','100'),
                                          ('SYNTA','synthetic-a-2024','80'),
                                          ('SYNTB','synthetic-b-2026q2','45')]:
            hits = self.agent.query_report('copper spend', ticker, accession=accession)
            self.assertIn(f'USD {amount} million', ' '.join(h['text'] for h in hits))
            self.assertTrue(all(h['ticker'] == ticker and h['accession'] == accession for h in hits))
            for hit in hits:
                self.assertIn((hit['document_id'], hit['locator']), self.citations.values())
                self.assertTrue(hit['source_url'].startswith('https://example.invalid/'))

    def test_invalid_or_unavailable_scope_cannot_fall_back_to_vectors(self):
        self.agent.collection = Mock()
        for ticker, accession in [('SYNTA',None),('SYNTA',''),('SYNTA','synthetic-b-2026q2'),
                                  ('SYNTA','unavailable'),('OTHER','synthetic-a-2025')]:
            with self.subTest(ticker=ticker, accession=accession), self.assertRaises(ValueError):
                self.agent.query_report('copper', ticker, accession=accession)
        self.agent.collection.query.assert_not_called()

    def test_incomplete_preparation_and_missing_index_fail_explicitly(self):
        self.agent.preparation_service.load_preparation = lambda ticker: {'status': 'partial'}
        with self.assertRaisesRegex(ValueError, 'Prepare'):
            self.agent.query_report('copper', 'SYNTA', accession='synthetic-a-2025')
        self.agent = fixture_agent(self.suite, self.index)
        with self.index.connect() as db:
            db.execute('DELETE FROM chunks WHERE document_id IN (SELECT id FROM documents WHERE accession=?)',
                       ('synthetic-a-2026q2',))
        with self.assertRaisesRegex(ValueError, 'not indexed'):
            self.agent.query_report('copper', 'SYNTA', accession='synthetic-a-2026q2')

    def test_no_hits_do_not_broaden_the_scope(self):
        self.assertEqual(self.agent.query_report('platinum', 'SYNTA', accession='synthetic-a-2026q2'), [])
        self.assertTrue(self.agent.query_report('platinum', 'SYNTA', accession='synthetic-a-2025'))

    def test_index_rejects_missing_scope_and_unbounded_limits(self):
        for accession, limit in [(None,3),('',3),('synthetic-a-2025',-1),('synthetic-a-2025',True)]:
            with self.subTest(accession=accession, limit=limit), self.assertRaises(ValueError):
                self.index.query('copper', 'SYNTA', accession, limit)


class EvaluationTests(unittest.TestCase):
    def test_baseline_measures_retrieval_but_not_unimplemented_chat(self):
        result = run_baseline(load_suite())
        self.assertEqual(result['measured_cases'], 18)
        self.assertEqual(result['not_run_cases'], 12)
        self.assertEqual(result['scope_violations'], 0)
        self.assertGreater(result['recall_by_language']['en'], 0)
        # The current tokenizer cannot retrieve a Chinese-only query: expose the gap.
        self.assertEqual(result['recall_by_language']['zh'], 0)
        self.assertFalse(any(r.get('status') == 'passed' for r in result['results']))

    def test_validator_rejects_accidental_cross_report_gold_evidence(self):
        suite = copy.deepcopy(load_suite())
        suite['cases'][0]['evidence'] = ['bq.revenue']
        with TemporaryDirectory() as tmp:
            path = Path(tmp)/'bad-suite.json'
            path.write_text(json.dumps(suite))
            with self.assertRaisesRegex(ValueError, 'explicit report scope'):
                load_suite(path)


if __name__ == '__main__':
    unittest.main()

"""Essential synthetic integration contracts; no provider calls."""
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pe_copilot.chat.benchmark_workspace import BenchmarkWorkspace
from pe_copilot.chat.quality import build_evaluation_packet


class BenchmarkRunnerTests(unittest.TestCase):
    def test_scenario_fixtures_use_real_services_and_forecasts_have_real_results(self):
        with TemporaryDirectory() as root:
            fixture = BenchmarkWorkspace(Path(root))
            for name, expected in [('up', 8.), ('down', -8.), ('mixed', 6.)]:
                state = fixture.state_for({'scope': 'a25', 'setup': {'scenario': name}})
                result = state['sim_execution']
                self.assertAlmostEqual(result.simulation['waterfall_summary']['Net_COGS_Absorbed'], expected)
                self.assertTrue(result.trace)
                self.assertEqual(result.request.accession, state['baseline_context']['accession'])
            forecast = fixture.state_for({'scope': 'a25', 'setup': {'forecast': True}})['forecast_prepared']['Copper']
            self.assertEqual(forecast['horizon'], 5)
            self.assertIn('naive', forecast['models'])
            self.assertIsNotNone(forecast['models']['naive']['audit_mae'])
            beta = fixture.service.report_baseline('SYNTB', next(r['accession'] for r in fixture.service.suite['reports'] if r['id'] == 'bq'))
            self.assertEqual(beta['pools']['income_statement'], {})
            history = fixture.state_for({'scope': 'a25', 'setup': {'history': [
                {'role': 'assistant', 'text': 'Earlier quarterly answer.', 'scope': 'aq'}]}})
            self.assertNotEqual(history['copilot_messages'][0]['scope']['accession'], history['baseline_context']['accession'])

    def test_batch_keeps_partial_metric_failure_without_replacing_reference(self):
        from pe_copilot.chat.benchmark_judge import score_batch
        reply = {'text': 'Synthetic Alpha revenue was USD 1000 million. [1]', 'status': 'ok',
            'answer_kind': 'company', 'scope': {'ticker': 'SYNTA', 'accession': 'synthetic'},
            'citations': [{'text': 'Synthetic Alpha annual revenue was USD 1000 million.',
                           'ticker': 'SYNTA', 'accession': 'synthetic'}],
            'runtime': {'answer': {'status': 'generated'}, 'grounding_context': []}}
        packet = build_evaluation_packet('What was synthetic annual revenue?', reply)
        calls = []

        async def metric(payload, embeddings=None):
            calls.append(payload)
            if payload['key'] == 'context_recall':
                raise RuntimeError('sensitive exception detail')
            return {'metric': {'status': 'completed', 'score': .8}}

        with patch('pe_copilot.chat.benchmark_judge._score_metric', side_effect=metric), \
             patch('pe_copilot.chat.benchmark_judge._EMBEDDINGS', object()):
            rows = score_batch({'items': [{'case_id': 'synthetic', 'packet': packet,
                                          'reference': 'Independently authored synthetic reference.'}]})
        self.assertEqual(len(calls), 5)
        self.assertTrue(all(c['reference'] == 'Independently authored synthetic reference.' for c in calls))
        self.assertEqual(rows[0]['quality']['status'], 'partial')
        self.assertEqual(len(rows[0]['quality']['scores']), 4)
        self.assertNotIn('sensitive exception detail', str(rows))


if __name__ == '__main__':
    unittest.main()

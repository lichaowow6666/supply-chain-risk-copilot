"""Focused contracts for opt-in interview evaluation; no provider/model calls."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from pe_copilot.chat.interview_quality import evaluate_interview
from pe_copilot.chat.quality import build_evaluation_packet


def packet(kind='company', generated=True, source_count=2):
    sources = [{'text': f'Synthetic source {i}: copper spend is USD 30 million for the quarter.',
                'ticker': 'SYNTA', 'accession': 'synthetic-quarter'} for i in range(source_count)]
    reply = {'text': 'Copper spend is USD 30 million for this quarter. [1]' if sources else 'A margin relates profit to revenue.',
             'answer_kind': kind, 'status': 'ok', 'scope': {'ticker': 'SYNTA', 'accession': 'synthetic-quarter'},
             'citations': sources, 'runtime': {'answer': {'status': 'generated' if generated else 'unavailable'},
             'grounding_context': [{'kind': 'scenario', 'text': '{"gross":3.4,"passed":2.55,"basis":"assumed"}'}]}}
    return build_evaluation_packet('What is copper spend?', reply)


def measured(payload, **kwargs):
    key = payload['key']
    return {'metric': {'status': 'completed', 'score': -.2 if key == 'answer_relevancy' else .75, 'reason': ''},
            'judge_model': 'synthetic-judge', 'judge_provider': 'synthetic', 'ragas_version': '0.4.3',
            'claim_checks': [{'statement': 'Synthetic claim.', 'verdict': 1, 'reason': 'Synthetic support.'}]}


class InterviewQualityTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        roots = patch('pe_copilot.chat.interview_quality.LOCAL_INTERVIEW_ROOT', Path(self.directory.name))
        roots.start(); self.addCleanup(roots.stop)
        config = patch('pe_copilot.chat.interview_quality._judge_configuration', return_value=('synthetic', 'synthetic-judge'))
        config.start(); self.addCleanup(config.stop)
        self.worker = Mock(); self.worker.call.side_effect = measured
        workers = patch('pe_copilot.chat.interview_quality.get_worker', return_value=self.worker)
        self.factory = workers.start(); self.addCleanup(workers.stop)

    def test_no_reference_runs_three_real_metric_contracts_and_preserves_negative_cosine(self):
        frozen = packet(source_count=10)
        before = deepcopy(frozen)
        result = evaluate_interview(frozen)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(set(result['scores']), {'faithfulness', 'answer_relevancy', 'context_precision_without_reference'})
        self.assertEqual(result['scores']['answer_relevancy'], -.2)
        self.assertEqual([row['status'] for row in result['metrics'][-2:]], ['not_applicable', 'not_applicable'])
        calls = [call.args[0] for call in self.worker.call.call_args_list]
        self.assertEqual(len(calls[0]['contexts']), 11)
        self.assertEqual(len(calls[-1]['precision_contexts']), 8)
        self.assertIn('first 8 of 10', result['metrics'][2]['reason'])
        self.assertEqual(frozen, before)
        self.assertTrue(Path(result['artifact_path']).is_file())

    def test_reference_enables_actual_recall_and_correctness_contracts_and_cache_is_scoped(self):
        frozen = packet()
        reference = 'Independent synthetic reviewer: copper spend was USD 30 million this quarter.'
        result = evaluate_interview(frozen, reference=reference)
        self.assertEqual(len(result['scores']), 5)
        self.assertEqual(self.worker.call.call_count, 5)
        self.assertTrue(all(call.args[0]['reference'] == reference for call in self.worker.call.call_args_list))
        cached = evaluate_interview(frozen, reference=reference)
        self.assertTrue(cached['cache_hit'])
        self.assertEqual(self.worker.call.call_count, 5)
        other = evaluate_interview(frozen, reference=reference + ' Coverage date confirmed.')
        self.assertNotEqual(result['evaluation_id'], other['evaluation_id'])
        self.assertEqual(self.worker.call.call_count, 10)

    def test_general_answers_get_relevance_while_navigation_gets_no_provider_calls(self):
        general = evaluate_interview(packet(kind='general', source_count=0))
        self.assertEqual(set(general['scores']), {'answer_relevancy'})
        self.assertEqual(self.worker.call.call_count, 1)
        navigation = evaluate_interview(packet(kind='workflow', generated=False, source_count=0))
        self.assertEqual(navigation['status'], 'not_applicable')
        self.assertTrue(all(row['status'] == 'not_applicable' for row in navigation['metrics']))
        self.assertEqual(self.worker.call.call_count, 1)

    def test_dependency_failure_keeps_other_metrics_and_redacts_exception(self):
        def outcomes(payload, **kwargs):
            if payload['key'] == 'answer_relevancy':
                raise RuntimeError('PRIVATE_TOKEN_OR_ENDPOINT')
            return measured(payload)
        self.worker.call.side_effect = outcomes
        result = evaluate_interview(packet())
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(set(result['scores']), {'faithfulness', 'context_precision_without_reference'})
        self.assertEqual(result['metrics'][1]['status'], 'unavailable')
        self.assertNotIn('PRIVATE_TOKEN', str(result))
        self.assertTrue(all(row['duration_ms'] is not None for row in result['metrics'][:3]))

    def test_overall_deadline_stops_new_calls_and_retains_finished_metric(self):
        now = [0.0]
        def slow_result(payload, **kwargs):
            now[0] += 20
            return measured(payload)
        self.worker.call.side_effect = slow_result
        with patch('pe_copilot.chat.interview_quality.monotonic', side_effect=lambda: now[0]):
            result = evaluate_interview(packet(), timeout_seconds=10)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(set(result['scores']), {'faithfulness'})
        self.assertEqual(self.worker.call.call_count, 1)
        self.assertIn('deadline', result['metrics'][1]['reason'])


if __name__ == '__main__':
    unittest.main()

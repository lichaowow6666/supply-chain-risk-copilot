"""Focused checks: judge exact displayed evidence, avoid surprise calls, isolate failures."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from pe_copilot.chat.quality import FULL_CONTEXT_V2, build_evaluation_packet, evaluate_response


def response():
    return {'text': 'Synthetic copper spend is USD 30 million for this quarter. [1]',
        'status': 'ok', 'answer_kind': 'company', 'elapsed_seconds': 1.5,
        'scope': {'ticker': 'SYNTA', 'accession': 'synthetic-quarter'},
        'citations': [{'text': 'Synthetic copper spend was USD 30 million this quarter.',
            'ticker': 'SYNTA', 'accession': 'synthetic-quarter', 'locator': 'block:1'},
            {'text': 'Synthetic unhedged copper exposure was 50 percent.',
             'ticker': 'SYNTA', 'accession': 'synthetic-quarter', 'locator': 'block:2'}],
        'runtime': {'answer': {'status': 'generated'}, 'language_calls': [{
            'model': 'synthetic-model', 'provider': 'synthetic', 'status': 'completed',
            'endpoint': 'PRIVATE_NOT_TO_COPY', 'api_key': 'PRIVATE_NOT_TO_COPY'}]}}


class AnswerQualityTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = patch('pe_copilot.chat.quality.LOCAL_QUALITY_ROOT', Path(self.directory.name))
        self.root.start()
        self.addCleanup(self.root.stop)

    def test_snapshot_retains_numbered_source_context_and_cited_subset_independently(self):
        original = response()
        packet = build_evaluation_packet('What was copper spend?', original)
        original['citations'][0]['text'] = 'MUTATED LATER'
        self.assertEqual(len(packet['sources']), 2)
        self.assertEqual(packet['citation_validation']['inline_ids'], [1])
        self.assertNotIn('MUTATED LATER', str(packet))
        self.assertNotIn('PRIVATE_NOT_TO_COPY', str(packet))
        changed = dict(packet, response='Different answer')
        with self.assertRaises(ValueError):
            evaluate_response(changed)

    def test_explicit_evaluation_uses_frozen_answer_and_sources_and_reports_real_score(self):
        packet = build_evaluation_packet('What was copper spend?', response())
        worker = Mock()
        worker.call.return_value = {'status': 'completed', 'scores': {'faithfulness': .5},
            'errors': {}, 'judge_model': 'synthetic-model'}
        with patch('pe_copilot.chat.quality.get_worker', return_value=worker) as get_worker:
            result = evaluate_response(packet)
        payload = worker.call.call_args.args[0]
        self.assertEqual(payload['response'], packet['response'])
        self.assertEqual(payload['retrieved_contexts'], [source['text'] for source in packet['sources']])
        self.assertEqual(result['scores']['faithfulness'], .5)
        self.assertTrue(any('correlated bias' in warning for warning in result['limitations']))
        self.assertEqual(get_worker.call_args.args[1], 'pe_copilot.chat.quality:judge_packet')
        self.assertTrue((Path(self.directory.name) / (packet['packet_id'] + '.json')).is_file())

    def test_no_judge_call_for_invalid_scope_or_citation_or_educational_or_failed_answer(self):
        replies = []
        invalid = response(); invalid['text'] += ' [9]'; replies.append(invalid)
        other = response(); other['citations'][0]['accession'] = 'different-report'; replies.append(other)
        concept = response(); concept['answer_kind'] = 'general'; replies.append(concept)
        failed = response(); failed['runtime']['answer']['status'] = 'unavailable'; replies.append(failed)
        with patch('pe_copilot.chat.quality.get_worker') as get_worker:
            for reply in replies:
                with self.subTest(reply=reply['answer_kind']):
                    result = evaluate_response(build_evaluation_packet('Synthetic question', reply))
                    self.assertEqual(result['status'], 'not_scored')
                    self.assertFalse(result['scores'])
            get_worker.assert_not_called()

    def test_worker_failure_is_redacted_and_does_not_claim_zero_quality(self):
        worker = Mock()
        worker.call.side_effect = RuntimeError('PRIVATE_PROVIDER_ENDPOINT_OR_SECRET')
        packet = build_evaluation_packet('What was copper spend?', response())
        with patch('pe_copilot.chat.quality.get_worker', return_value=worker):
            result = evaluate_response(packet)
        self.assertEqual(result['status'], 'unavailable')
        self.assertEqual(result['scores'], {})
        self.assertNotIn('PRIVATE_PROVIDER', str(result))

    def test_structured_scenario_is_frozen_not_regenerated_and_is_not_a_filing_citation(self):
        original = response()
        original['runtime']['grounding_context'] = [
            {'kind': 'scenario', 'text': '{"gross":3.4,"passed":2.55,"shock":0.17,"basis":"assumed"}'},
            {'kind': 'workspace', 'text': '{"scenario_status":"Saved current scenario"}'},
            {'kind': 'conversation', 'text': 'HISTORY_MUST_NOT_BECOME_EVIDENCE'},
            {'kind': 'system_prompt', 'text': 'PROMPT_MUST_NOT_BECOME_EVIDENCE'}]
        packet = build_evaluation_packet('Explain my contribution', original)
        original['runtime']['grounding_context'][0]['text'] = 'DIFFERENT_LATER_SCENARIO'
        worker = Mock()
        worker.call.return_value = {'status': 'completed', 'scores': {'faithfulness': 1.0}, 'errors': {}}
        with patch('pe_copilot.chat.quality.get_worker', return_value=worker):
            result = evaluate_response(packet)
        contexts = worker.call.call_args.args[0]['retrieved_contexts']
        self.assertEqual(contexts[-2], 'scenario: {"gross":3.4,"passed":2.55,"shock":0.17,"basis":"assumed"}')
        self.assertNotIn('DIFFERENT_LATER_SCENARIO', str(packet))
        self.assertNotIn('MUST_NOT_BECOME_EVIDENCE', str(contexts))
        self.assertEqual(result['context_scope'], FULL_CONTEXT_V2)
        self.assertEqual(result['structured_context_count'], 2)
        self.assertEqual(result['citation_validation']['source_count'], 2)


if __name__ == '__main__':
    unittest.main()

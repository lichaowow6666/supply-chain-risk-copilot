import json
import os
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pe_copilot.chat.diagnostics import build_diagnostics
from pe_copilot.chat.language import LanguageService, SYSTEM


class LanguageTelemetryTests(unittest.TestCase):
    def call_language(self, response=None, failure=None):
        create = Mock(return_value=response, side_effect=failure)
        client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        service = LanguageService()
        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}, clear=True), \
             patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=Mock(return_value=client))}), \
             patch('pe_copilot.chat.language.monotonic', side_effect=[10, 10.01, 10.61, 10.7]):
            try:
                result = service.json('Task', {'question': 'Example'})
            except Exception as error:
                result = error
        return service.calls[0], create, result

    def test_provider_timing_and_numeric_usage_preserve_request_and_answer(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
            content='{"answer":"Same answer"}', reasoning_content='PRIVATE THOUGHT'))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=12)))
        call, create, result = self.call_language(response)
        self.assertEqual(result, {'answer': 'Same answer'})
        self.assertEqual(call['duration_ms'], 700)
        self.assertEqual(call['request_roundtrip_ms'], 600)
        self.assertEqual((call['input_tokens'], call['output_tokens'], call['reasoning_tokens']), (100, 40, 12))
        self.assertIsNone(call['thinking_time_ms'])
        self.assertIsNone(call['time_to_first_token_ms'])
        self.assertNotIn('PRIVATE THOUGHT', json.dumps(call))
        create.assert_called_once_with(model='deepseek-chat', temperature=0, max_tokens=1600,
            response_format={'type': 'json_object'}, messages=[{'role': 'system', 'content': SYSTEM + 'Task'},
            {'role': 'user', 'content': json.dumps({'question': 'Example'}, ensure_ascii=False)}])

    def test_missing_usage_and_failed_request_never_fabricate_tokens_or_thinking(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{}'))], usage=None)
        call, _, result = self.call_language(response)
        self.assertEqual(result, {})
        self.assertNotIn('input_tokens', call)
        self.assertNotIn('reasoning_tokens', call)
        call, _, error = self.call_language(failure=RuntimeError('private provider detail'))
        self.assertIsInstance(error, RuntimeError)
        self.assertEqual(call['status'], 'failed')
        self.assertEqual(call['request_roundtrip_ms'], 600)
        self.assertEqual(call['error_type'], 'RuntimeError')
        self.assertNotIn('private provider detail', json.dumps(call))

    def test_malformed_answer_still_records_provider_usage(self):
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='not JSON'))],
            usage={'prompt_tokens': 9, 'completion_tokens': 2,
                   'completion_tokens_details': {'reasoning_tokens': None}})
        call, _, error = self.call_language(response)
        self.assertIsInstance(error, ValueError)
        self.assertEqual(call['status'], 'failed')
        self.assertEqual(call['input_tokens'], 9)
        self.assertNotIn('reasoning_tokens', call)


class DiagnosticsTests(unittest.TestCase):
    def test_nested_spans_and_saved_run_do_not_inflate_response_time(self):
        result = build_diagnostics({'elapsed_seconds': 2, 'status': 'ok', 'runtime': {
            'route': {'action': 'explain', 'answer_kind': 'scenario'},
            'planning_call_count': 1,
            'workflow': {'status': 'completed', 'nodes': [{'node': 'answer', 'duration_ms': 1800, 'status': 'completed'}]},
            'activities': [{'name': 'Read scenario', 'actor': 'Orchestrator', 'status': 'reused', 'duration_ms': 1}],
            'language_calls': [{'status': 'completed', 'duration_ms': 200, 'request_roundtrip_ms': 190},
                               {'status': 'completed', 'duration_ms': 900, 'request_roundtrip_ms': 880}],
            'retrieval': {'stages': [{'stage': 'dense', 'status': 'ok', 'duration_ms': 500}]},
            'saved_scenario': {'run_id': 'saved-123', 'status': 'completed', 'duration_ms': 50000,
                               'trace': [{'name': 'Market retrieval', 'actor': 'Market Agent', 'status': 'completed', 'duration_ms': 1000}]}}})
        self.assertEqual(result['total_ms'], 2000)
        self.assertEqual(result['timing']['model_calls_ms'], 1100)
        self.assertEqual(result['timing']['provider_roundtrips_ms'], 1070)
        self.assertEqual(result['saved_execution']['duration_ms'], 50000)
        agents = {row['name']: row for row in result['agents']}
        self.assertEqual(agents['Orchestrator']['status'], 'reused')
        self.assertEqual(agents['Market Agent']['status'], 'not_invoked')
        self.assertIsNone(agents['Market Agent']['duration_ms'])
        self.assertEqual(result['language_calls'][0]['purpose'], 'Understanding the request')
        self.assertEqual(result['language_calls'][1]['purpose'], 'Writing the answer')
        precise = build_diagnostics({'elapsed_seconds': 2, 'runtime': {'response_duration_ms': 1995.125}})
        self.assertEqual(precise['total_ms'], 1995.125)

    def test_old_partial_records_remain_unknown_and_multiple_service_spans_are_separate(self):
        result = build_diagnostics({'runtime': {'activities': [
            {'name': 'outer', 'actor': 'FundamentalAgent', 'status': 'completed', 'duration_ms': 100},
            {'name': 'inner', 'actor': 'FundamentalAgent', 'status': 'completed', 'duration_ms': 80}]}})
        agent = next(row for row in result['agents'] if row['name'] == 'Fundamental Agent')
        self.assertEqual(agent['activity_count'], 2)
        self.assertIsNone(agent['duration_ms'])
        self.assertIsNone(result['total_ms'])
        self.assertIsNone(result['timing']['model_calls_ms'])
        self.assertEqual(build_diagnostics(None)['activities'], [])

    def test_receipts_exclude_content_and_invalid_or_sensitive_metadata(self):
        result = build_diagnostics({'text': 'PRIVATE ANSWER', 'runtime': {
            'language_calls': [{'status': 'failed', 'duration_ms': float('nan'), 'reasoning_tokens': True,
                               'reasoning_content': 'PRIVATE THOUGHT', 'error': 'PRIVATE ERROR'}],
            'activities': [{'name': 'safe', 'status': 'failed', 'detail': 'api_key=abc Bearer secret https://provider.example/key',
                            'duration_ms': -1, 'payload': 'PRIVATE PROMPT'}]}})
        encoded = json.dumps(result, allow_nan=False)
        for secret in ('PRIVATE', 'api_key=abc', 'Bearer secret', 'provider.example'):
            self.assertNotIn(secret, encoded)
        self.assertIsNone(result['language_calls'][0]['reasoning_tokens'])
        self.assertIsNone(result['language_calls'][0]['duration_ms'])
        self.assertIsNone(result['activities'][0]['duration_ms'])


if __name__ == '__main__':
    unittest.main()

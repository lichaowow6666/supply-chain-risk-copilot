import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from pe_copilot.chat.adoption import anonymous_session_id, record_event, summarize_events
from pe_copilot.chat.benchmark_metrics import assess_case, snapshot_workspace, summarize, synthetic_permission_check


class BenchmarkContractTests(unittest.TestCase):
    def fixture(self):
        state = {'active_company': {'ticker': 'SYNTA'},
                 'baseline_context': {'company_id': 'synthetic:a', 'accession': 'a-annual', 'snapshot_id': 'a-2025'},
                 'selected_metals': ['Copper'], 'forecast_model_Copper': 'benchmark'}
        case = {'id': 'case-1', 'category': 'evidence',
                'expected': {'actions': ['explain'], 'answer_kinds': ['company'], 'must_preserve_workspace': True}}
        reply = {'status': 'ok', 'text': 'Copper evidence [1].',
                 'scope': {**state['baseline_context'], 'ticker': 'SYNTA'},
                 'citations': [{'text': 'Synthetic copper evidence', 'accession': 'a-annual', 'locator': 'p1'}],
                 'runtime': {'route': {'action': 'explain', 'answer_kind': 'company'}, 'workflow': {'status': 'completed'},
                    'activities': [{'name': 'Evidence', 'actor': 'Fundamental Agent', 'operation': 'report_retrieval',
                                    'status': 'completed', 'duration_ms': 50}]}}
        return case, reply, state

    def test_scope_and_state_contracts_detect_real_changes_but_ignore_conversation(self):
        import pandas as pd
        case, reply, state = self.fixture()
        state['sim_results'] = pd.DataFrame({'impact': [1.0, 2.0]})
        before = snapshot_workspace(state)
        self.assertTrue(before['analysis_comparable'])
        state.update(copilot_messages=[{'text': 'new conversation'}], requested_tab='Tab 2', canvas_focus_request={'nonce': 'new'})
        self.assertEqual(assess_case(case, reply, before, snapshot_workspace(state))['task_outcome']['status'], 'passed')
        state['forecast_model_Copper'] = 'new-model'
        reply['citations'][0]['accession'] = 'wrong-report'
        result = assess_case(case, reply, before, snapshot_workspace(state))
        self.assertIn('analysis_state_preserved', result['task_outcome']['failed_checks'])
        self.assertIn('source_scope_valid', result['task_outcome']['failed_checks'])
        unchanged = snapshot_workspace(state)
        state['sim_results'].loc[0, 'impact'] = 3.0
        self.assertNotEqual(unchanged['analysis_fingerprint'], snapshot_workspace(state)['analysis_fingerprint'])

    def test_literal_checks_are_labelled_and_permission_demo_is_separate(self):
        case, reply, state = self.fixture()
        case['category'] = 'adversarial_governance'
        case['expected']['forbidden_terms'] = ['PRIVATE_MARKER']
        reply['text'] += ' PRIVATE_MARKER'
        result = assess_case(case, reply, snapshot_workspace(state), snapshot_workspace(state))
        marker = next(row for row in result['checks'] if row['name'] == 'literal_forbidden_terms')
        self.assertEqual(marker['status'], 'failed')
        self.assertIn('not semantic correctness', marker['detail'])
        self.assertIn(marker, result['governance']['checks'])
        policy = synthetic_permission_check()
        self.assertFalse(policy['active_workspace_acl'])
        self.assertEqual([row['status'] for row in policy['checks']], ['passed'] * 3)

    def test_metric_denominators_zero_scores_and_nested_latency_stay_separate(self):
        case, reply, state = self.fixture()
        assessment = assess_case(case, reply, snapshot_workspace(state), snapshot_workspace(state))
        rows = [{'id': 'a', 'category': 'evidence', 'status': 'completed', 'reply': reply, 'assessment': assessment,
                 'diagnostics': {'total_ms': 100, 'timing': {'model_calls_ms': 70}, 'activities': reply['runtime']['activities']},
                 'quality': {'scores': {'faithfulness': 0.0, 'context_precision_without_reference': .8}, 'evaluation_seconds': 2}},
                {'id': 'b', 'category': 'evidence', 'status': 'completed', 'reply': reply, 'assessment': assessment,
                 'quality': {'scores': {'faithfulness': 1.0}, 'metrics': [{'key': 'context_recall', 'status': 'not_applicable'}]}},
                {'id': 'c', 'category': 'general', 'status': 'partial', 'reply': {},
                 'quality': {'metrics': [{'key': 'faithfulness', 'status': 'unavailable'}]}}]
        result = summarize(rows, case_count=100)
        metric = result['ragas']['faithfulness']
        self.assertEqual((metric['count'], metric['mean'], metric['p95'], metric['unavailable_count']), (2, .5, .95, 1))
        self.assertEqual(result['ragas']['context_recall']['not_applicable_count'], 1)
        self.assertIsNone(result['ragas']['factual_correctness']['mean'])
        self.assertEqual(result['latency']['answer_ms']['mean'], 100)
        self.assertEqual(result['latency']['judge_ms']['mean'], 2000)
        self.assertEqual(result['attempt_count'], 3)
        self.assertEqual(result['task_outcomes']['not_assessed'], 1)
        self.assertEqual(result['adoption']['automation_coverage']['value'], 2 / 3)
        self.assertEqual(result['capabilities']['operations'][0]['scopes'], 1)
        self.assertEqual(result['ragas']['context_precision_without_reference']['mean'], .8)
        failure = summarize([{'status': 'execution_error', 'quality': {'status': 'unavailable'}}])
        self.assertEqual(failure['adoption']['error_count'], 1)
        self.assertEqual(failure['task_outcomes']['failed'], 1)
        self.assertEqual(failure['ragas']['faithfulness']['unavailable_count'], 1)
        fallback = summarize([{'reply': {'status': 'ok', 'runtime': {'answer': {'status': 'unavailable'}}}},
                              {'reply': {'status': 'ok', 'runtime': {'answer': {'status': 'generated'}}}}])
        self.assertEqual(fallback['adoption']['answer_fallback_count'], 1)
        self.assertEqual(fallback['adoption']['answer_fallback_rate'], .5)
        self.assertEqual(fallback['adoption']['error_count'], 0)
        screening = summarize([{'quality': {'status': 'not_applicable', 'evaluation_seconds': .001}},
                               {'quality': {'status': 'completed', 'evaluation_seconds': 2}}])
        self.assertEqual(screening['latency']['judge_ms']['count'], 1)
        self.assertEqual(screening['latency']['judge_ms']['mean'], 2000)
        self.assertEqual(screening['latency']['evaluation_screening_ms']['count'], 1)
        self.assertEqual(screening['latency']['evaluation_screening_ms']['mean'], 1)

    def test_summary_excludes_source_answer_and_company_identifiers(self):
        result = summarize([{'category': 'evidence', 'question': 'PRIVATE QUESTION',
            'reply': {'text': 'PRIVATE ANSWER', 'scope': {'company_id': 'PRIVATE COMPANY'},
                'citations': [{'text': 'PRIVATE SOURCE'}]}, 'quality': {}}])
        self.assertNotIn('PRIVATE', json.dumps(result, allow_nan=False))
        self.assertIsNone(result['adoption']['observed_user_adoption'])


class AnonymousUsageTests(unittest.TestCase):
    def test_opt_out_writes_nothing_and_unobserved_rates_are_unavailable(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'absent'
            result = record_event(enabled=False, session_id='unused', event='answer_rendered', root=root)
            self.assertEqual(result['status'], 'disabled')
            self.assertFalse(root.exists())
            summary = summarize_events(root)
            self.assertIsNone(summary['helpfulness']['value'])
            self.assertIsNone(summary['task_completion']['value'])
            self.assertIsNone(summary['active_users'])
            self.assertFalse(root.exists())

    def test_deduplicated_metadata_only_events_and_revised_feedback(self):
        with TemporaryDirectory() as root:
            session = anonymous_session_id()
            common = {'enabled': True, 'session_id': session, 'answer_id': 'opaque-answer-123', 'root': root}
            record_event(**common, event='answer_rendered')
            record_event(**common, event='answer_rendered')
            record_event(**common, event='inspection_opened')
            record_event(**common, event='feedback', helpful=True)
            record_event(**common, event='feedback', task_completed=True)
            record_event(**common, event='feedback', helpful=False)
            summary = summarize_events(root)
            self.assertEqual((summary['event_count'], summary['answers_rendered'], summary['session_count']), (3, 1, 1))
            self.assertEqual(summary['helpfulness'], {'numerator': 0, 'denominator': 1, 'value': 0})
            self.assertEqual(summary['task_completion']['value'], 1)
            db = sqlite3.connect(Path(root) / 'events.sqlite')
            try:
                contents = str(db.execute('SELECT * FROM events').fetchall())
            finally:
                db.close()
            self.assertNotIn('opaque-answer-123', contents)
            with self.assertRaises(TypeError):
                record_event(**common, event='feedback', question='Never allowed')


if __name__ == '__main__':
    unittest.main()

"""Essential checks of the graph actually used by the sidebar."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_workspace_chat import fixture, intent
from pe_copilot.chat.language import LanguageService
from pe_copilot.chat.workspace import WorkspaceChat


class WorkspaceLangGraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'checkpoints.sqlite'
        self.app, self.language, _, _ = fixture()

    def chat(self):
        return WorkspaceChat(self.app, self.language, session_id='same-conversation', checkpoint_path=self.path)

    def test_clarification_resumes_persisted_graph_with_new_service_instance(self):
        state = {'workbench_tab': 'saved-tab', 'spend_Silver': 25}
        plan = {**intent('clarify', None), 'answer_kind': 'workflow', 'clarification': 'Which company?'}
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=plan):
            first = self.chat().respond('Show exposures', state)
        self.assertEqual(first['runtime']['workflow']['status'], 'waiting_for_clarification')
        self.assertEqual(first['runtime']['route']['decision_source'], 'validated_local_rule')
        self.assertEqual(state, {'workbench_tab': 'saved-tab', 'spend_Silver': 25})
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=intent('prepare', company_query='TEST')) as planner:
            second = self.chat().respond('TEST', state)
        self.assertEqual(second['status'], 'ok')
        self.assertEqual(planner.call_args.args[1]['conversation'][-2]['content'], 'Show exposures')
        self.assertEqual(second['runtime']['workflow']['engine'], 'LangGraph')
        self.assertEqual([row['node'] for row in second['runtime']['workflow']['nodes']],
                         ['route', 'prepare_company', 'navigate', 'finish'])
        self.app.discover_target.assert_called_once_with('TEST', 'annual')
        activities = second['runtime']['activities']
        self.assertEqual([row['actor'] for row in activities],
                         ['Orchestrator', 'Orchestrator', 'Fundamental Agent', 'Orchestrator'])
        self.assertTrue(all(row['duration_ms'] >= 0 for row in activities))
        self.assertTrue(self.path.is_file())

    def test_explanation_uses_graph_without_mutating_existing_scenario(self):
        state = {'active_company': {'ticker': 'TEST', 'cik': '0000000001'},
                 'baseline_context': {'company_id': 'company:0000000001', 'accession': 'annual'},
                 'workbench_tab': 'Tab 2', 'forecast_model_fixture': 'xgboost',
                 'sim_results': {'saved': True}, 'spend_Silver': 65}
        before = dict(state)
        plan = {**intent('explain', None), 'answer_kind': 'general'}
        self.language.calls = []
        self.language.json.return_value = {'answer': 'Hedging reduces price exposure.', 'citation_ids': [], 'evidence_gap': True}
        def received_plan(*args):
            self.language.calls.append({'status': 'completed'})
            return plan
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', side_effect=received_plan):
            result = self.chat().respond('What is hedging?', state)
        self.assertEqual(state, before)
        self.assertEqual([row['node'] for row in result['runtime']['workflow']['nodes']], ['route', 'answer', 'finish'])
        self.assertIsNotNone(result['runtime']['workflow']['checkpoint_id'])
        self.assertEqual(result['runtime']['activities'], [])
        self.assertEqual(result['runtime']['route']['decision_source'], 'model_response_received')
        self.app.invoke.assert_not_called()

    def test_failed_model_planning_is_labelled_as_local_fallback(self):
        state = {'workbench_tab': 'Tab 2', 'forecast_model_fixture': 'xgboost',
                 'sim_results': {'saved': True}, 'spend_Silver': 65}
        before = dict(state)
        self.language = LanguageService()
        with patch.object(self.language, '_json', side_effect=RuntimeError('sensitive provider details')):
            result = self.chat().respond('Explain gross margin in plain language.', state)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['runtime']['route']['action'], 'clarify')
        self.assertEqual(result['runtime']['route']['decision_source'], 'model_unavailable_local_fallback')
        self.assertEqual(result['runtime']['planning_call_count'], 1)
        self.assertEqual(result['runtime']['language_calls'][0]['status'], 'failed')
        self.assertEqual(result['runtime']['workflow']['status'], 'waiting_for_clarification')
        self.assertEqual(state, before)
        self.assertNotIn('sensitive provider details', str(result))
        self.app.prepare_company.assert_not_called()

    def test_failed_service_timing_survives_without_exposing_exception_text(self):
        state = {'workbench_tab': 'saved-tab', 'spend_Silver': 25}
        self.app.prepare_company.side_effect = RuntimeError('sensitive provider details')
        with patch('pe_copilot.chat.workspace.parse_workspace_intent',
                   return_value=intent('prepare', company_query='TEST')):
            reply = self.chat().respond('TEST', state)
        self.assertEqual(reply['status'], 'error')
        self.assertEqual(reply['runtime']['activities'][0]['status'], 'failed')
        self.assertNotIn('sensitive provider details', str(reply))
        self.assertEqual(state, {'workbench_tab': 'saved-tab', 'spend_Silver': 25})

    def test_new_report_never_resumes_previous_report_clarification(self):
        state = {'baseline_context': {'accession': 'annual'}}
        plan = {**intent('clarify', None), 'answer_kind': 'workflow', 'clarification': 'Which material?'}
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=plan):
            self.chat().respond('Explain that', state)
        state['baseline_context'] = {'accession': 'quarter'}
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=plan) as planner:
            self.chat().respond('Explain that', state)
        self.assertEqual(planner.call_args.args[1]['conversation'], [])

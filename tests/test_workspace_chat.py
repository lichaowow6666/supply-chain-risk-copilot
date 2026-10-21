"""Synthetic regression cases for conversation-to-canvas state and evidence."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch, ANY

from pe_copilot.chat.workspace import WorkspaceChat, exposure_evidence
from pe_copilot.ui.navigation import DISCOVERY_TAB, SCENARIO_TAB


def fixture():
    company = {'ticker': 'TEST', 'name': 'Synthetic Test Company', 'cik': '0000000001'}
    baseline = {'accession': 'annual', 'snapshot_id': 'test-annual', 'period_end': '2025-12-31',
                'form': '10-K', 'duration_basis': 'Annual', 'duration_start': '2025-01-01',
                'currency': 'USD', 'pools': {}, 'evidence': {}, 'warnings': []}
    prepared = {'company': company, 'baseline': baseline, 'reused': True}
    materials = [{'material': 'Silver', 'source_accession': 'annual', 'inherent_exposure_level': 'High',
                  'search_mode': 'synthetic', 'market_covered': True,
                  'raw_chunks': [{'chunk_id': 'annual:block:1', 'text': 'Synthetic disclosure: silver is an operating input with price volatility.',
                                  'period_end': '2025-12-31', 'document_type': '10-K'}]}]
    app = Mock()
    app.prepare_company.return_value = prepared
    app.preparation_service.report_baseline.return_value = baseline
    app.discover_target.return_value = (materials, {})
    app.fundamental_agent.query_report.return_value = []
    language = Mock()
    language.json.return_value = {'answer': 'Silver is an operating input with price volatility. [1]', 'citation_ids': [1], 'evidence_gap': False}
    return app, language, prepared, materials


def intent(action='navigate', section='exposures', company_query=None):
    return {'action': action, 'section': section, 'company_query': company_query, 'question': '', 'clarification': None}


class WorkspaceChatTests(unittest.TestCase):
    def setUp(self):
        self.app, self.language, self.prepared, self.materials = fixture()
        self.retriever = Mock()
        self.retriever.search.return_value = {'hits': [], 'backend': 'synthetic test fixture', 'diagnostics': {}, 'warnings': []}
        self.chat = WorkspaceChat(self.app, self.language, self.retriever)
        self.state = {'copilot_messages': [{'role': 'user', 'text': 'Earlier conversation'}], 'sim_results': {'old': True}}

    def prepare(self):
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=intent('prepare', company_query='TEST')):
            return self.chat.respond('TEST', self.state)

    def test_prepare_uses_exact_report_and_publishes_one_complete_scope(self):
        reply = self.prepare()
        self.assertEqual(reply['status'], 'ok')
        self.app.discover_target.assert_called_once_with('TEST', 'annual')
        self.assertEqual(self.state['selected_report_accession'], 'annual')
        self.assertEqual(self.state['report_TEST'], 'annual')
        self.assertEqual(self.state['graph_requested_snapshot'], 'test-annual')
        self.assertEqual(self.state['requested_tab'], DISCOVERY_TAB)
        self.assertNotIn('sim_results', self.state)
        self.assertEqual(len(self.state['copilot_messages']), 1)
        self.assertIn('reused', reply['text'])
        self.assertTrue(any('Fundamental agent' in step for step in reply['steps']))

    def test_failed_switch_preserves_current_analysis(self):
        self.state['active_company'] = {'ticker': 'OLD', 'cik': '0000000002'}
        before = deepcopy(self.state)
        self.app.discover_target.side_effect = RuntimeError('provider failure with sensitive endpoint')
        reply = self.prepare()
        self.assertEqual(self.state, before)
        self.assertEqual(reply['status'], 'error')
        self.assertNotIn('sensitive endpoint', reply['text'])

    def test_explanation_reuses_current_audited_chunks_and_citations(self):
        self.prepare()
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=intent('explain')):
            reply = self.chat.respond('Why are these materials exposed?', self.state)
        self.assertIn('[1]', reply['text'])
        self.assertEqual(reply['citations'][0]['accession'], 'annual')
        self.app.fundamental_agent.query_report.assert_not_called()
        self.retriever.search.assert_called_once_with('Why are these materials exposed?', 'TEST', 'annual', k=6, on_progress=ANY)
        payload = self.language.json.call_args.args[1]
        self.assertEqual(payload['report']['accession'], 'annual')
        self.assertIn('qualitative_assessments_not_reported_facts', payload)

    def test_report_change_cannot_reuse_old_exposure_chunks(self):
        self.prepare()
        self.state['baseline_context']['accession'] = 'quarter'
        self.assertEqual(exposure_evidence(self.state, 'why')[0], [])
        self.retriever.search.return_value['hits'] = [
            {'text': 'stale annual evidence', 'accession': 'annual', 'ticker': 'TEST'},
            {'text': 'wrong company', 'accession': 'quarter', 'ticker': 'OTHER'},
            {'text': 'Synthetic quarterly evidence.', 'accession': 'quarter', 'ticker': 'TEST'}]
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=intent('explain')):
            reply = self.chat.respond('why', self.state)
        self.retriever.search.assert_called_with('why', 'TEST', 'quarter', k=6, on_progress=ANY)
        self.assertEqual([c['text'] for c in reply['citations']], ['Synthetic quarterly evidence.'])

    def test_later_hedging_passages_are_not_lost_to_three_chunk_sampling(self):
        self.prepare()
        self.state['top_materials'][0]['raw_chunks'].extend([
            {'chunk_id': f'annual:block:{i}', 'text': text}
            for i, text in enumerate(['Synthetic supply context.', 'Synthetic accounting context.',
                                      'Synthetic disclosed hedge on the leased metals pool.'], 2)])
        evidence, _ = exposure_evidence(self.state, 'Why silver?')
        self.assertTrue(any('disclosed hedge' in row['text'] for row in evidence))

    def test_invalid_citation_falls_back_honestly_to_sources(self):
        self.prepare()
        self.language.json.return_value = {'answer': 'Invented fact [99]', 'citation_ids': [99], 'evidence_gap': False}
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=intent('explain')):
            reply = self.chat.respond('Why silver?', self.state)
        self.assertIn('could not produce a reliable answer', reply['text'])
        self.assertNotIn('Invented fact', reply['text'])
        self.assertEqual(len(reply['citations']), 1)

    def test_navigating_to_scenario_does_not_change_assumptions(self):
        self.prepare()
        self.state.update(selected_metals=['Silver'], spend_Silver=25, unhedged_Silver=80)
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=intent(section='assumptions')):
            reply = self.chat.respond('Simulate silver up 20%', self.state)
        self.assertEqual(self.state['requested_tab'], SCENARIO_TAB)
        self.assertEqual(self.state['spend_Silver'], 25)
        self.assertEqual(self.state['selected_metals'], ['Silver'])
        self.assertIn('in development', reply['text'])
        self.app.invoke.assert_not_called()

    def test_missing_material_selection_opens_the_prerequisite(self):
        self.prepare()
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=intent(section='forecast')):
            reply = self.chat.respond('Show price forecasts', self.state)
        self.assertEqual(self.state['requested_tab'], DISCOVERY_TAB)
        self.assertEqual(reply['section'], 'materials')
        self.assertIn('First select', reply['text'])

    def test_refresh_preserves_selected_quarterly_accession(self):
        self.prepare()
        self.state['selected_report_accession'] = 'quarter'
        quarter = {**self.prepared['baseline'], 'accession': 'quarter', 'snapshot_id': 'test-quarter', 'form': '10-Q'}
        self.app.preparation_service.report_baseline.return_value = quarter
        reply = self.chat.respond('Refresh SEC filings', self.state)
        self.assertEqual(reply['status'], 'ok')
        self.assertTrue(self.app.prepare_company.call_args.args[2])
        self.app.discover_target.assert_called_with('TEST', 'quarter')
        self.assertEqual(self.state['selected_report_accession'], 'quarter')

    def test_resolved_current_company_keeps_report_and_saved_analysis(self):
        self.prepare()
        quarter = {**self.prepared['baseline'], 'accession': 'quarter', 'snapshot_id': 'test-quarter', 'form': '10-Q'}
        self.state.update(selected_report_accession='quarter', selected_report_baseline=quarter,
                          baseline_context={**self.state['baseline_context'], 'accession': 'quarter'},
                          selected_metals=['Silver'], spend_Silver=65,
                          forecast_prepared={'Silver': {'forecast_id': 'existing'}},
                          sim_results={'run': 'saved'}, forecast_model_Silver_fixture='xgboost')
        self.app.preparation_service.report_baseline.return_value = quarter
        with patch('pe_copilot.chat.workspace.parse_workspace_intent',
                   return_value=intent('prepare', company_query='Synthetic Test Company')):
            reply = self.chat.respond('Synthetic Test Company', self.state)
        self.assertEqual(reply['status'], 'ok')
        self.app.preparation_service.report_baseline.assert_called_with('TEST', 'quarter')
        self.assertEqual(self.state['selected_metals'], ['Silver'])
        self.assertEqual(self.state['spend_Silver'], 65)
        self.assertEqual(self.state['forecast_model_Silver_fixture'], 'xgboost')
        self.assertEqual(self.state['forecast_prepared']['Silver']['forecast_id'], 'existing')
        self.assertEqual(self.state['sim_results'], {'run': 'saved'})

    def test_result_explanation_reads_saved_calculations(self):
        self.prepare()
        request = SimpleNamespace(accession='annual', context=SimpleNamespace(company_id='company:0000000001'), currency='USD')
        self.state['sim_execution'] = SimpleNamespace(status='ok', request=request, run_id='synthetic-run', simulation={
            'waterfall_summary': {'Pass_Through_Rate_Pct': 50, 'Total_Gross_COGS_Surge': 2,
                                  'Passed_To_Clients': 1, 'Net_COGS_Absorbed': 1},
            'materials': {'Silver': {'cost_contribution': {'gross': 2, 'passed': 1, 'absorbed': 1}}}})
        self.state['sim_inputs_current'] = False
        self.language.json.return_value = {'answer': 'This previous run models COGS +1.00M; operating income -1.00M. Product-level allocation is unavailable. [1]',
                                          'citation_ids': [1], 'evidence_gap': False}
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=intent('explain', 'impact')):
            reply = self.chat.respond('Explain my result', self.state)
        self.assertIn('previous run', reply['text'])
        self.assertIn('COGS +1.00M; operating income -1.00M', reply['text'])
        self.assertIn('Product-level allocation is unavailable', reply['text'])
        payload = self.language.json.call_args.args[1]
        self.assertFalse(payload['scenario']['inputs_current'])
        self.assertEqual(payload['scenario']['contributions'][0]['COGS contribution ($M)'], 1)
        self.app.invoke.assert_not_called()

    def test_sidebar_is_available_before_company_and_keeps_conversation(self):
        from streamlit.testing.v1 import AppTest
        def page():
            import streamlit as st
            from unittest.mock import Mock
            from pe_copilot.ui.sidebar_chat import render_sidebar_chat
            from pe_copilot.ui.navigation import DISCOVERY_TAB, SCENARIO_TAB
            render_sidebar_chat(Mock())
            st.tabs([DISCOVERY_TAB, SCENARIO_TAB])
        at = AppTest.from_function(page).run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.tabs), 2)
        self.assertEqual(len(at.sidebar.chat_input), 1)
        self.assertEqual(len(at.text_input), 0)
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=intent('clarify')):
            at.chat_input[0].set_value('Help me get started').run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.session_state['copilot_messages']), 2)
        self.assertTrue(any('Which company' in m.value for m in at.markdown))

"""Focused UI checks: inspection stays separate from the analysis and chat."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def fixture_message():
    return {'role': 'assistant', 'turn_id': 'synthetic-inspection', 'text': 'Synthetic exposure is disclosed [1].',
            'question': 'Explain synthetic exposure', 'status': 'ok', 'answer_kind': 'company',
            'scope': {'ticker': 'SYNTH', 'company_id': 'synthetic-company', 'accession': 'synthetic-annual',
                      'form': '10-K', 'period_end': '2025-12-31'},
            'citations': [{'text': 'Synthetic Company uses natural gas.', 'ticker': 'SYNTH',
                           'accession': 'synthetic-annual', 'section': 'Synthetic source'}],
            'steps': ['Read the selected synthetic report.'], 'elapsed_seconds': 1.5,
            'runtime': {'answer': {'status': 'generated'}, 'grounding_context': [],
                        'route': {'action': 'explain', 'answer_kind': 'company'},
                        'workflow': {'engine': 'LangGraph', 'status': 'completed',
                                     'nodes': [{'node': 'route', 'status': 'completed', 'duration_ms': 100},
                                               {'node': 'answer', 'status': 'completed', 'duration_ms': 1300}]},
                        'activities': [{'name': 'retrieval', 'actor': 'Fundamental Agent',
                                        'operation': 'report_search', 'status': 'completed', 'duration_ms': 300}],
                        'language_calls': []}}


def dialog_page():
    import streamlit as st
    from types import SimpleNamespace
    from pe_copilot.ui.sidebar_chat import _render_reply
    from pe_copilot.ui.navigation import DISCOVERY_TAB, SCENARIO_TAB
    from pe_copilot.workspace_state import preserve_analysis_widgets
    from test_interview_panel import fixture_message

    preserve_analysis_widgets(st.session_state)
    st.session_state.setdefault('sim_execution', {'run_id': 'synthetic-sentinel', 'impact': 1.25})
    st.session_state.setdefault('forecast_prepared', {'Copper': {'model': 'synthetic-saved'}})
    st.session_state.setdefault('workbench_tab', SCENARIO_TAB)
    st.session_state.setdefault('forecast_model_synthetic', 'xgboost')
    message = st.session_state.setdefault('fixture_message', fixture_message())
    with st.sidebar:
        with st.chat_message('assistant'):
            _render_reply(message, 0, message['scope'], SimpleNamespace())
    first, second = st.tabs([DISCOVERY_TAB, SCENARIO_TAB], key='workbench_tab', on_change='rerun')
    with second:
        st.selectbox('Selected model', ['naive', 'xgboost'], index=None, key='forecast_model_synthetic')


def independent_page():
    import streamlit as st
    from pe_copilot.ui.interview_panel import render_answer_inspection
    from test_interview_panel import fixture_message
    st.session_state.setdefault('sim_execution', {'run_id': 'synthetic-sentinel', 'impact': 1.25})
    st.session_state.setdefault('active_company', {'ticker': 'OTHER'})
    if st.session_state.get('show_inspector', True):
        render_answer_inspection(st.session_state.setdefault('fixture_message', fixture_message()))


class InterviewPanelTests(unittest.TestCase):
    def test_dialog_opens_without_evaluation_or_losing_canvas_and_legacy_details(self):
        with patch('pe_copilot.ui.interview_panel._evaluate_interview') as evaluate:
            page = AppTest.from_function(dialog_page).run()
            self.assertFalse(page.exception)
            saved = {key: deepcopy(page.session_state[key]) for key in
                     ('sim_execution', 'forecast_prepared', 'workbench_tab', 'forecast_model_synthetic')}
            labels = [item.label for item in page.expander]
            self.assertIn('Sources and calculations (1)', labels)
            self.assertIn('What I did', labels)
            self.assertIn('Answer checks', labels)
            page.button(key='inspect_answer_synthetic-inspection_0').click().run()
            self.assertFalse(page.exception)
            self.assertTrue(any(item.label == 'Run evaluation' for item in page.button))
            self.assertTrue(any(item.label == 'Routing & agents' for item in page.tabs))
            evaluate.assert_not_called()
            for key, value in saved.items():
                self.assertEqual(page.session_state[key], value)
            from pe_copilot.ui.navigation import DISCOVERY_TAB, SCENARIO_TAB
            for tab in (DISCOVERY_TAB, SCENARIO_TAB):
                page.session_state['workbench_tab'] = tab
                page.run()
                self.assertFalse(page.exception)
                self.assertEqual(page.session_state['workbench_tab'], tab)
                for key in ('sim_execution', 'forecast_prepared', 'forecast_model_synthetic'):
                    self.assertEqual(page.session_state[key], saved[key])
            evaluate.assert_not_called()

    def test_reference_specific_cache_uses_saved_evidence_and_never_current_company(self):
        def result(packet, reference):
            return {'status': 'completed', 'scores': {'faithfulness': 1.0},
                    'metrics': [{'key': 'faithfulness', 'label': 'Faithfulness', 'status': 'completed',
                                 'score': 1.0, 'reason': 'Synthetic judge fixture'}],
                    'judge_model': 'synthetic-judge', 'judge_provider': 'Synthetic',
                    'ragas_version': 'fixture', 'evaluation_seconds': .1, 'limitations': []}

        with patch('pe_copilot.ui.interview_panel._evaluate_interview', side_effect=result) as evaluate:
            page = AppTest.from_function(independent_page).run()
            self.assertFalse(page.exception)
            evaluate.assert_not_called()
            button = next(item for item in page.button if item.label == 'Run evaluation')
            button.click().run()
            self.assertFalse(page.exception)
            self.assertEqual(evaluate.call_count, 1)
            packet, reference = evaluate.call_args.args
            self.assertEqual(packet['scope']['ticker'], 'SYNTH')
            self.assertEqual(packet['sources'][0]['accession'], 'synthetic-annual')
            self.assertEqual(reference, '')
            self.assertEqual(len(page.session_state['interview_evaluations']), 1)
            page.text_area[0].set_value('Independent synthetic reviewer reference.').run()
            self.assertFalse(page.exception)
            self.assertEqual(evaluate.call_count, 1)
            self.assertTrue(any('No evaluation has been run' in item.value for item in page.markdown))
            next(item for item in page.button if item.label == 'Run evaluation').click().run()
            self.assertFalse(page.exception)
            self.assertEqual(evaluate.call_count, 2)
            self.assertEqual(len(page.session_state['interview_evaluations']), 2)
            # Dialog widgets can be cleaned up after closing; the authored
            # reference and its corresponding result must survive reopening.
            page.session_state['show_inspector'] = False
            page.run()
            page.session_state['show_inspector'] = True
            page.run()
            self.assertEqual(page.text_area[0].value, 'Independent synthetic reviewer reference.')
            self.assertEqual(evaluate.call_count, 2)
            self.assertEqual(page.session_state['active_company']['ticker'], 'OTHER')
            self.assertEqual(page.session_state['sim_execution']['run_id'], 'synthetic-sentinel')


if __name__ == '__main__':
    unittest.main()

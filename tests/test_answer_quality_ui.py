"""The optional answer check must not run automatically or reset a scenario."""
import unittest
from unittest.mock import patch
from streamlit.testing.v1 import AppTest


def page():
    import streamlit as st
    from pe_copilot.ui.answer_quality import render_answer_checks
    st.session_state.setdefault('sim_results', {'saved': 0.85})
    st.session_state.setdefault('forecast_model_fixture', 'xgboost')
    scope = {'ticker': 'TEST', 'company_id': 'test', 'accession': 'annual'}
    render_answer_checks({'turn_id': 'synthetic-answer', 'question': 'Explain the scenario',
        'text': 'The calculated impact is 0.85M [1].', 'scope': scope,
        'answer_kind': 'scenario', 'status': 'ok', 'runtime': {'answer': {'status': 'generated'}},
        'citations': [{'text': 'Synthetic calculated impact: 0.85M.', 'accession': 'annual'}]}, scope, None)


class AnswerQualityUITests(unittest.TestCase):
    def test_explicit_judging_keeps_existing_result_and_model(self):
        with patch('pe_copilot.chat.quality.evaluate_response', return_value={
            'status': 'completed', 'scores': {'faithfulness': 1.0}, 'judge_model': 'synthetic-test'}) as judge:
            app = AppTest.from_function(page).run()
            self.assertFalse(app.exception)
            judge.assert_not_called()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            judge.assert_called_once()
            self.assertEqual(app.session_state['sim_results'], {'saved': 0.85})
            self.assertEqual(app.session_state['forecast_model_fixture'], 'xgboost')

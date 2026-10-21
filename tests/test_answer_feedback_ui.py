"""Focused explicit-feedback workflow checks; synthetic data and no model calls."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest


def feedback_page():
    import streamlit as st
    from pe_copilot.ui.sidebar_chat import _render_reply
    from test_interview_panel import fixture_message
    st.session_state.setdefault('sim_execution', {'run_id': 'synthetic-saved'})
    st.session_state.setdefault('forecast_model_synthetic', 'xgboost')
    if st.session_state.get('show_answer', True):
        _render_reply(fixture_message(), 0, {})


def feedback_summary_page():
    from pe_copilot.ui.interview_dashboard import _render_adoption
    _render_adoption()


class AnswerFeedbackUITests(unittest.TestCase):
    def test_votes_reasons_clear_and_rerenders_preserve_analysis_without_telemetry(self):
        from pe_copilot.chat.adoption import summarize_events
        with TemporaryDirectory() as root, \
                patch('pe_copilot.chat.adoption.LOCAL_USAGE_ROOT', Path(root)), \
                patch('pe_copilot.chat.language.LanguageService.json') as model:
            page = AppTest.from_function(feedback_page).run()
            self.assertFalse(page.exception)
            self.assertFalse(Path(root, 'events.sqlite').exists())
            page.get('feedback')[0].set_value(0).run()
            self.assertFalse(page.exception)
            page.multiselect[0].set_value(['missing_evidence', 'wrong_scope']).run()
            self.assertFalse(page.exception)
            summary = summarize_events()
            self.assertEqual(summary['feedback']['negative'], 1)
            self.assertEqual(summary['feedback']['negative_with_reasons'], 1)
            self.assertEqual(sum(row['count'] for row in summary['feedback']['reason_counts']), 2)
            self.assertEqual(summary['answers_rendered'], 0)
            page.session_state['show_answer'] = False
            page.run()
            page.session_state['show_answer'] = True
            page.run()
            self.assertEqual(page.get('feedback')[0].value, 0)
            self.assertEqual(page.multiselect[0].value, ['missing_evidence', 'wrong_scope'])
            page.get('feedback')[0].set_value(1).run()
            self.assertFalse(page.exception)
            summary = summarize_events()
            self.assertEqual(summary['feedback']['positive'], 1)
            self.assertEqual(summary['feedback']['negative'], 0)
            self.assertEqual(sum(row['count'] for row in summary['feedback']['reason_counts']), 0)
            page.get('feedback')[0].set_value(None).run()
            self.assertEqual(summarize_events()['feedback']['total'], 0)
            self.assertEqual(page.session_state['sim_execution'], {'run_id': 'synthetic-saved'})
            self.assertEqual(page.session_state['forecast_model_synthetic'], 'xgboost')
            model.assert_not_called()

    def test_failed_write_is_visible_and_does_not_interrupt_analysis(self):
        with patch('pe_copilot.chat.adoption.record_feedback', side_effect=RuntimeError('private detail')):
            page = AppTest.from_function(feedback_page).run()
            page.get('feedback')[0].set_value(0).run()
            self.assertFalse(page.exception)
            self.assertTrue(any(item.value == 'Feedback could not be saved.' for item in page.caption))
            self.assertEqual(page.session_state['sim_execution']['run_id'], 'synthetic-saved')
            self.assertTrue(any(item.label == 'Retry feedback' for item in page.button))

    def test_evaluation_shows_explicit_votes_and_reason_counts(self):
        from pe_copilot.chat.adoption import record_feedback
        with TemporaryDirectory() as root, patch('pe_copilot.chat.adoption.LOCAL_USAGE_ROOT', Path(root)):
            record_feedback(session_id='a' * 32, answer_id='synthetic-one', helpful=False,
                            reasons=['missing_evidence', 'unclear_answer'])
            record_feedback(session_id='a' * 32, answer_id='synthetic-two', helpful=True)
            page = AppTest.from_function(feedback_summary_page).run()
            self.assertFalse(page.exception)
            values = {item.label: item.value for item in page.metric}
            self.assertEqual(values['Thumbs up'], '1')
            self.assertEqual(values['Thumbs down'], '1')
            self.assertEqual(values['Met expectations'], '50%')
            self.assertEqual(len(page.dataframe[0].value), 2)
            self.assertFalse(page.toggle[0].value)
            page.session_state['copilot_messages'] = [{'role': 'assistant', 'turn_id': 'synthetic-one',
                'question': 'Explain the synthetic material.', 'text': 'Synthetic answer.', 'status': 'ok'}]
            page.session_state['answer_feedback'] = {'synthetic-one': {
                'helpful': False, 'reasons': ['missing_evidence'], 'status': 'recorded'}}
            page.run()
            self.assertFalse(page.exception)
            page.button(key='feedback_inspect_answer').click().run()
            self.assertFalse(page.exception)
            self.assertTrue(any(tab.label == 'Routing & agents' for tab in page.tabs))


if __name__ == '__main__':
    unittest.main()

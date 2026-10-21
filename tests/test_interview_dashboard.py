"""Targeted dashboard tests: opt-in tab, honest denominators, anonymous feedback."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from pe_copilot.chat.benchmark_metrics import summarize
from pe_copilot.ui.interview_dashboard import _governance_rows, _metric_rows, downloadable_report, load_dashboard_report
from pe_copilot.ui.navigation import DISCOVERY_TAB, SCENARIO_TAB, INTERVIEW_TAB


def synthetic_report():
    metrics = ['faithfulness', 'answer_relevancy', 'context_precision_without_reference', 'context_recall', 'factual_correctness']
    return {'schema_version': 1, 'suite_id': 'synthetic-ui-fixture', 'synthetic': True,
            'status': 'running', 'case_count': 100, 'catalog': [], 'results': [
                {'case_id': 'S01', 'category': 'Synthetic exposure', 'language': 'en',
                 'question': 'What input is used?', 'reference': 'Synthetic material.',
                 'response': 'Synthetic material is used.', 'status': 'answered',
                 'reply': {'internal_packet': 'must-not-export'},
                 'diagnostics': {'total_ms': 1200, 'timing': {'model_calls_ms': 800}},
                 'quality': {'status': 'completed', 'scores': {'faithfulness': .5, 'answer_relevancy': -.2, 'factual_correctness': 0.},
                             'metrics': [{'key': key, 'status': 'completed' if key in ('faithfulness', 'answer_relevancy', 'factual_correctness') else 'not_applicable'} for key in metrics],
                             'evaluation_seconds': 2.}},
                {'case_id': 'S02', 'category': 'Synthetic failure', 'language': 'en',
                 'question': 'A failed question', 'reference': 'Synthetic reference', 'response': '',
                 'status': 'execution_error', 'quality': {'status': 'unavailable', 'scores': {}}},
                {'case_id': 'S03', 'category': 'Synthetic navigation', 'language': 'en',
                 'question': 'Open the scenario', 'reference': 'Open it', 'response': 'Opened.',
                 'status': 'answered', 'quality': {'status': 'not_applicable', 'scores': {},
                     'metrics': [{'key': key, 'status': 'not_applicable'} for key in metrics]}}]}


def tab_page():
    import streamlit as st
    from pe_copilot.ui.navigation import render_workbench_tabs, SCENARIO_TAB
    from pe_copilot.ui.interview_dashboard import render_interview_dashboard
    from pe_copilot.workspace_state import preserve_analysis_widgets
    from test_interview_dashboard import synthetic_report
    preserve_analysis_widgets(st.session_state)
    st.session_state.setdefault('workbench_tab', SCENARIO_TAB)
    st.session_state.setdefault('forecast_model_synthetic', 'xgboost')
    st.session_state.setdefault('sim_execution', {'run_id': 'sentinel-scenario', 'impact': 3.5})
    st.session_state.setdefault('forecast_prepared', {'Copper': {'model': 'synthetic-forecast'}})
    enabled = st.sidebar.toggle('Interview evaluation', key='interview_evaluation_enabled')
    tabs = render_workbench_tabs(enabled)
    with tabs[1]:
        st.selectbox('Selected model', ['naive', 'xgboost'], index=None, key='forecast_model_synthetic')
    if enabled:
        with tabs[2]:
            render_interview_dashboard(synthetic_report())


def feedback_page():
    import streamlit as st
    from pe_copilot.ui.interview_panel import render_answer_inspection
    from test_interview_panel import fixture_message
    st.session_state.setdefault('sim_execution', {'run_id': 'sentinel-scenario'})
    st.toggle('Opt in', key='pilot_measurement_enabled')
    render_answer_inspection(fixture_message(), allow_usage=not st.session_state.get('benchmark_preview', False))


def report_page():
    import streamlit as st
    from pe_copilot.ui.interview_dashboard import render_interview_dashboard
    render_interview_dashboard(st.session_state['fixture_report'])


class InterviewDashboardTests(unittest.TestCase):
    def test_optional_third_tab_without_company_keeps_scenario_model_and_results(self):
        with patch('pe_copilot.ui.interview_panel._evaluate_interview') as evaluate:
            page = AppTest.from_function(tab_page).run()
            self.assertFalse(page.exception)
            self.assertNotIn('active_company', page.session_state)
            self.assertNotIn(INTERVIEW_TAB, [tab.label for tab in page.tabs])
            saved = {key: deepcopy(page.session_state[key]) for key in ('sim_execution', 'forecast_prepared', 'forecast_model_synthetic')}
            page.toggle(key='interview_evaluation_enabled').set_value(True).run()
            self.assertFalse(page.exception)
            self.assertIn(INTERVIEW_TAB, [tab.label for tab in page.tabs])
            self.assertEqual(page.session_state['workbench_tab'], SCENARIO_TAB)
            page.session_state['workbench_tab'] = INTERVIEW_TAB
            page.run()
            self.assertFalse(page.exception)
            page.toggle(key='interview_evaluation_enabled').set_value(False).run()
            self.assertFalse(page.exception)
            self.assertEqual(page.session_state['workbench_tab'], SCENARIO_TAB)
            for key, value in saved.items():
                self.assertEqual(page.session_state[key], value)
            evaluate.assert_not_called()

    def test_missing_scores_are_not_zero_and_download_omits_full_packet(self):
        report = synthetic_report()
        summary = summarize(report['results'], case_count=100)
        rows = {row['Metric']: row for row in _metric_rows(summary, summary['attempt_count'])}
        self.assertEqual(rows['Evidence support']['Mean'], .5)
        self.assertEqual(rows['Evidence support']['Scored'], 1)
        self.assertEqual(rows['Evidence support']['Not applicable'], 1)
        self.assertEqual(rows['Evidence support']['Missing'], 0)
        self.assertEqual(rows['Evidence support']['Unavailable'], 1)
        self.assertEqual(rows['Evidence support']['Attempted'], 3)
        self.assertEqual(rows['Answer relevance']['Mean'], -.2)
        self.assertEqual(rows['Reference correctness']['Mean'], 0.)
        self.assertIsNone(rows['Context precision']['Mean'])
        exported = downloadable_report(report)
        self.assertNotIn('must-not-export', exported)
        self.assertNotIn('reply', json.loads(exported)['results'][0])
        self.assertEqual(len(json.loads(exported)['results']), 3)

    def test_feedback_needs_opt_in_and_explicit_save_and_excludes_benchmark_preview(self):
        with patch('pe_copilot.chat.adoption.record_event', return_value={'status': 'recorded', 'stored': True}) as record:
            page = AppTest.from_function(feedback_page).run()
            self.assertFalse(page.exception)
            self.assertFalse(any(button.label == 'Save feedback' for button in page.button))
            record.assert_not_called()
            page.toggle(key='pilot_measurement_enabled').set_value(True).run()
            self.assertFalse(page.exception)
            record.assert_not_called()
            page.radio[0].set_value('Yes').run()
            record.assert_not_called()
            next(button for button in page.button if button.label == 'Save feedback').click().run()
            self.assertFalse(page.exception)
            record.assert_called_once()
            arguments = record.call_args.kwargs
            self.assertEqual(arguments['event'], 'feedback')
            self.assertIsNone(arguments['helpful'])
            self.assertTrue(arguments['task_completed'])
            self.assertEqual(set(arguments), {'enabled', 'session_id', 'event', 'answer_id', 'helpful', 'task_completed'})
            self.assertEqual(page.session_state['sim_execution']['run_id'], 'sentinel-scenario')
            page.session_state['benchmark_preview'] = True
            page.run()
            self.assertFalse(page.exception)
            self.assertFalse(any(button.label == 'Save feedback' for button in page.button))
            self.assertEqual(record.call_count, 1)

    def test_governance_denominator_and_stale_catalog_warning_are_explicit(self):
        row = _governance_rows([{'name': 'synthetic_check', 'passed': 5, 'failed': 1,
                                'not_applicable': 2, 'total': 8}])[0]
        self.assertEqual(row['Assessed'], 6)
        self.assertEqual(row['Not applicable'], 2)
        with TemporaryDirectory() as directory:
            dataset_path = Path(directory) / 'cases.json'
            report_path = Path(directory) / 'report.json'
            dataset = {'suite_id': 'synthetic-ui-fixture', 'cases': [{'id': 'NEW', 'question': 'A changed question'}]}
            dataset_path.write_text(json.dumps(dataset))
            report = {**synthetic_report(), 'suite_sha256': 'older-version'}
            report_path.write_text(json.dumps(report))
            with patch('pe_copilot.chat.benchmark_dataset.load_benchmark', return_value=dataset), \
                    patch('pe_copilot.chat.benchmark_dataset.DEFAULT_BENCHMARK', dataset_path), \
                    patch('pe_copilot.ui.interview_dashboard.REPORT_PATH', report_path):
                stale = load_dashboard_report()
                self.assertEqual(stale['catalog_status'], 'stale')
                self.assertEqual(stale['catalog'], [])
                self.assertEqual(stale['results'][0]['reference'], 'Synthetic material.')
                report['suite_sha256'] = sha256(dataset_path.read_bytes()).hexdigest()
                report_path.write_text(json.dumps(report))
                current = load_dashboard_report()
                self.assertEqual(current['catalog_status'], 'current')
                self.assertEqual(current['catalog'][0]['id'], 'NEW')
            page = AppTest.from_function(report_page)
            page.session_state['fixture_report'] = stale
            page.run()
            self.assertFalse(page.exception)
            self.assertTrue(any('different question-catalog version' in item.value for item in page.warning))

    def test_metadata_store_failure_cannot_interrupt_feedback_or_scenario(self):
        with patch('pe_copilot.chat.adoption.record_event', side_effect=sqlite3.OperationalError('synthetic database failure')):
            page = AppTest.from_function(feedback_page)
            page.session_state['pilot_measurement_enabled'] = True
            page.run()
            page.radio[0].set_value('Yes').run()
            next(button for button in page.button if button.label == 'Save feedback').click().run()
            self.assertFalse(page.exception)
            self.assertEqual(page.session_state['sim_execution']['run_id'], 'sentinel-scenario')
            self.assertTrue(any('Feedback could not be saved' in item.value for item in page.caption))


if __name__ == '__main__':
    unittest.main()

"""Real Streamlit rerun cleanup must not discard the analysis behind chat."""
from copy import deepcopy
import unittest

from streamlit.testing.v1 import AppTest

from pe_copilot.ui.navigation import DISCOVERY_TAB, SCENARIO_TAB


def persistence_page():
    """Small synthetic canvas with the same early-sidebar-rerun lifecycle."""
    import streamlit as st
    from pe_copilot.workspace_state import preserve_analysis_widgets, reset_analysis_state
    from pe_copilot.ui.navigation import DISCOVERY_TAB, SCENARIO_TAB, migrate_tab

    preserve_analysis_widgets(st.session_state)
    st.session_state.setdefault('fixture_company', 'TEST')
    st.session_state.setdefault('fixture_forecast_calls', 0)
    st.session_state.setdefault('fixture_scenario_calls', 0)
    # These events interrupt before tabs/inputs register in this script run,
    # exactly as sidebar conversation submission does in the application.
    if st.sidebar.button('Ask an explanation', key='fixture_chat'):
        st.session_state['fixture_answer'] = 'Explanation of the existing result'
        st.rerun()
    if st.sidebar.button('View scenario', key='fixture_view'):
        st.session_state['requested_tab'] = SCENARIO_TAB
        st.rerun()
    if st.sidebar.button('Change company', key='fixture_company_change'):
        reset_analysis_state(st.session_state)
        st.session_state['fixture_company'] = 'OTHER'
        st.rerun()

    st.session_state['workbench_tab'] = migrate_tab(
        st.session_state.pop('requested_tab', st.session_state.get('workbench_tab')))
    first, second = st.tabs([DISCOVERY_TAB, SCENARIO_TAB], key='workbench_tab', on_change='rerun')
    ticker = st.session_state['fixture_company']
    with first:
        st.selectbox('Report', ['annual', 'quarter'], key='report_' + ticker)
        st.session_state['selected_metals'] = st.multiselect(
            'Materials', ['Copper', 'Natural Gas'], default=['Copper'], key='materials_' + ticker)
    with second:
        st.selectbox('Horizon', [5, 20, 30, 60], index=2, key='forecast_horizon')
        st.selectbox('Model', ['naive', 'xgboost'], key='forecast_model_Copper_fixture_30')
        st.multiselect('Reference curves', ['naive', 'xgboost'], key='forecast_compare_Copper_fixture_30')
        st.number_input('Price shock', value=0.0, key='forecast_shock_Copper_fixture_30')
        st.number_input('Target price', value=4.0, key='forecast_price_Copper_fixture_30')
        st.number_input('Spend', value=25.0, key='spend_Copper')
        st.slider('Unhedged', 0, 100, 80, key='unhedged_Copper')
        st.slider('Pass-through', 0, 100, 75, key='pass_through_' + ticker)
        st.multiselect('KPIs', ['COGS', 'Operating Income'], default=['COGS'], key='kpis_income_' + ticker)
        if st.button('Prepare forecasts', key='forecast_prepare'):
            st.session_state['fixture_forecast_calls'] += 1
            st.session_state['forecast_prepared'] = {'Copper': {'forecast_id': 'synthetic-forecast', 'values': [4.0, 4.2]}}
        if st.button('Run scenario', key='fixture_calculate'):
            st.session_state['fixture_scenario_calls'] += 1
            st.session_state['sim_execution'] = {'run_id': 'synthetic-saved-run', 'contribution': 1.25}
            st.session_state['sim_comparison_reference'] = {'run_id': 'synthetic-reference'}


class SessionPersistenceTests(unittest.TestCase):
    def page(self):
        page = AppTest.from_function(persistence_page).run()
        self.assertFalse(page.exception)
        return page

    def configure(self, page):
        page.selectbox(key='forecast_model_Copper_fixture_30').select('xgboost')
        page.selectbox(key='forecast_horizon').select(60)
        page.selectbox(key='report_TEST').select('quarter')
        page.multiselect(key='materials_TEST').set_value(['Copper', 'Natural Gas'])
        page.multiselect(key='forecast_compare_Copper_fixture_30').set_value(['naive'])
        page.multiselect(key='kpis_income_TEST').set_value(['Operating Income'])
        page.number_input(key='forecast_shock_Copper_fixture_30').set_value(35.0)
        page.number_input(key='forecast_price_Copper_fixture_30').set_value(5.4)
        page.number_input(key='spend_Copper').set_value(90.0)
        page.slider(key='unhedged_Copper').set_value(55)
        page.slider(key='pass_through_TEST').set_value(40)
        page.run()
        self.assertFalse(page.exception)
        # AppTest represents Tab as a read-only block. Set its public state
        # value to reproduce selecting the second stateful tab.
        page.session_state['workbench_tab'] = SCENARIO_TAB
        page.run()
        self.assertFalse(page.exception)

    def assert_configuration(self, page, tab=SCENARIO_TAB):
        self.assertFalse(page.exception)
        self.assertEqual(page.session_state['workbench_tab'], tab)
        self.assertEqual(page.selectbox(key='forecast_model_Copper_fixture_30').value, 'xgboost')
        self.assertEqual(page.selectbox(key='forecast_horizon').value, 60)
        self.assertEqual(page.selectbox(key='report_TEST').value, 'quarter')
        self.assertEqual(page.multiselect(key='materials_TEST').value, ['Copper', 'Natural Gas'])
        self.assertEqual(page.multiselect(key='forecast_compare_Copper_fixture_30').value, ['naive'])
        self.assertEqual(page.multiselect(key='kpis_income_TEST').value, ['Operating Income'])
        self.assertEqual(page.number_input(key='forecast_shock_Copper_fixture_30').value, 35.0)
        self.assertEqual(page.number_input(key='forecast_price_Copper_fixture_30').value, 5.4)
        self.assertEqual(page.number_input(key='spend_Copper').value, 90.0)
        self.assertEqual(page.slider(key='unhedged_Copper').value, 55)
        self.assertEqual(page.slider(key='pass_through_TEST').value, 40)

    def test_early_chat_rerun_keeps_tab_model_prices_and_scenario_controls(self):
        page = self.page()
        self.configure(page)
        self.assert_configuration(page)
        page.button(key='fixture_chat').click().run()
        self.assertEqual(page.session_state['fixture_answer'], 'Explanation of the existing result')
        self.assert_configuration(page)

    def test_tab_changes_and_quick_navigation_keep_saved_runs_without_recalculation(self):
        page = self.page()
        self.configure(page)
        page.button(key='forecast_prepare').click().run()
        page.button(key='fixture_calculate').click().run()
        saved = {key: deepcopy(page.session_state[key]) for key in
                 ('forecast_prepared', 'sim_execution', 'sim_comparison_reference')}
        page.session_state['workbench_tab'] = DISCOVERY_TAB
        page.run()
        self.assert_configuration(page, DISCOVERY_TAB)
        page.session_state['workbench_tab'] = SCENARIO_TAB
        page.run()
        self.assert_configuration(page)
        page.session_state['workbench_tab'] = DISCOVERY_TAB
        page.run()
        page.button(key='fixture_view').click().run()
        self.assert_configuration(page)
        page.button(key='fixture_chat').click().run()
        self.assert_configuration(page)
        for key, value in saved.items():
            self.assertEqual(page.session_state[key], value)
        self.assertEqual(page.session_state['fixture_forecast_calls'], 1)
        self.assertEqual(page.session_state['fixture_scenario_calls'], 1)

    def test_forecast_button_is_not_replayed_by_preservation(self):
        page = self.page()
        page.button(key='forecast_prepare').click().run()
        page.button(key='fixture_chat').click().run()
        self.assertFalse(page.exception)
        self.assertEqual(page.session_state['fixture_forecast_calls'], 1)
        page.button(key='forecast_prepare').click().run()
        self.assertFalse(page.exception)
        self.assertEqual(page.session_state['fixture_forecast_calls'], 2)

    def test_explicit_company_reset_does_not_restore_preserved_old_inputs(self):
        page = self.page()
        self.configure(page)
        page.button(key='forecast_prepare').click().run()
        page.button(key='fixture_calculate').click().run()
        page.button(key='fixture_company_change').click().run()
        self.assertFalse(page.exception)
        self.assertEqual(page.session_state['fixture_company'], 'OTHER')
        self.assertEqual(page.session_state['workbench_tab'], DISCOVERY_TAB)
        self.assertEqual(page.selectbox(key='forecast_model_Copper_fixture_30').value, 'naive')
        self.assertEqual(page.selectbox(key='forecast_horizon').value, 30)
        self.assertEqual(page.number_input(key='forecast_shock_Copper_fixture_30').value, 0.0)
        self.assertEqual(page.number_input(key='spend_Copper').value, 25.0)
        self.assertEqual(page.slider(key='unhedged_Copper').value, 80)
        self.assertEqual(page.slider(key='pass_through_OTHER').value, 75)
        self.assertEqual(page.multiselect(key='materials_OTHER').value, ['Copper'])
        for key in ('forecast_prepared', 'sim_execution', 'sim_comparison_reference', 'report_TEST', 'materials_TEST'):
            self.assertNotIn(key, page.session_state)
        self.assertEqual(page.session_state['fixture_forecast_calls'], 1)
        self.assertEqual(page.session_state['fixture_scenario_calls'], 1)


if __name__ == '__main__':
    unittest.main()

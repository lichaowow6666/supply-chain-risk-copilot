"""Synthetic UI regressions for the merged report-scoped scenario workspace."""
from streamlit.testing.v1 import AppTest
import unittest
import test_scenario_orchestration as scenario_fixtures
from pe_copilot.ui.navigation import migrate_tab, DISCOVERY_TAB, SCENARIO_TAB


class CombinedWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.fixture=scenario_fixtures.ScenarioTests()
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)

    def page(self):
        def render(repo, db, baseline, materials):
            import streamlit as st
            from unittest.mock import Mock
            from pe_copilot.agents.market_agent import MarketAgent
            from pe_copilot.orchestrator import CopilotOrchestrator
            from pe_copilot.supply_chain.graph_repository import GraphRepository
            from pe_copilot.ui.scenario_workspace import render_scenario_workspace
            fundamental=Mock()
            fundamental.analyze_report.return_value={'company':{'ticker':'TEST','cik':'0000000001'},'baseline':baseline,'materials':materials}
            app=CopilotOrchestrator(MarketAgent(db),fundamental,GraphRepository(repo),run_directory=None)
            if 'baselines' not in st.session_state:
                st.session_state['baselines']=baseline['pools']
                st.session_state['selected_metals']=['Copper']
                st.session_state['baseline_context']={'company_id':'company:0000000001','snapshot_id':'test-v1','accession':baseline['accession'],
                    'period_end':baseline['period_end'],'duration_start':baseline['duration_start'],'form':'10-K','duration_basis':'Annual'}
            render_scenario_workspace(app,'TEST')
        f=self.fixture
        return AppTest.from_function(render,args=(str(f.fixture.repo.root),f.market.db_path,f.baseline,f.materials)).run()

    def test_graph_precedes_financial_results_and_trace_is_retained(self):
        at=self.page();self.assertFalse(at.exception)
        self.assertTrue(any(h.value=='2. Supply-chain scenario map' for h in at.subheader))
        self.assertFalse(any(s.label=='Company / filing snapshot' for s in at.selectbox))
        self.assertFalse(any(s.label=='Exposure highlighting' for s in at.selectbox))
        next(b for b in at.button if b.label == 'Run scenario').click().run()
        self.assertFalse(at.exception)
        headings=[h.value for h in at.subheader]
        self.assertLess(headings.index('2. Supply-chain scenario map'),headings.index('3. Financial impact'))
        self.assertNotIn('4. Market history & illustrative forward paths',headings)
        self.assertTrue(any(b.label=='Prepare forecasts' for b in at.button))
        self.assertIn('How this result was produced',headings)
        self.assertTrue(any('Exposure overlay' in c.value for c in at.caption))
        self.assertTrue(any(e.label=='Scenario explanations' for e in at.expander))
        self.assertTrue(at.session_state['sim_execution'].graph['scenario_explanations'])
        at.slider(key='pass_through_TEST').set_value(50).run()
        self.assertFalse(at.exception)
        self.assertFalse(at.session_state['sim_inputs_current'])
        self.assertFalse(any('Exposure overlay' in c.value for c in at.caption))
        self.assertFalse(any(e.label=='Scenario explanations' for e in at.expander))
        self.assertTrue(any('previous run' in w.value for w in at.warning))

    def test_no_selected_materials_still_allows_graph_exploration(self):
        at=self.page();at.session_state['selected_metals']=[];at.run()
        self.assertFalse(at.exception)
        self.assertTrue(any(h.value=='2. Supply-chain scenario map' for h in at.subheader))
        self.assertFalse(any(b.label == 'Run scenario' for b in at.button))

    def test_reference_is_explicit_and_survives_assumption_changes(self):
        at=self.page()
        self.assertEqual(at.slider(key='unhedged_Copper').value, 80)
        next(b for b in at.button if b.label == 'Run scenario').click().run()
        self.assertFalse(at.exception)
        next(b for b in at.button if b.label=='Save this run as comparison reference').click().run()
        reference=at.session_state['sim_comparison_reference']
        at.slider(key='unhedged_Copper').set_value(50).run()
        self.assertTrue(next(b for b in at.button if b.label=='Save this run as comparison reference').disabled)
        next(b for b in at.button if b.label == 'Run scenario').click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state['sim_comparison_reference'].run_id,reference.run_id)
        self.assertEqual(at.session_state['sim_execution'].request.materials[0].unhedged_fraction,.5)
        self.assertTrue(any('Change vs reference ($M)' in frame.value.columns for frame in at.dataframe))

    def test_price_controls_forecast_selection_and_financial_result_are_connected(self):
        at=self.page()
        shock=next(w for w in at.number_input if w.label=='Price change (%) — Copper')
        shock.set_value(20).run()
        price=next(w for w in at.number_input if w.label=='Target benchmark price — Copper')
        self.assertAlmostEqual(price.value,4.2*1.2)
        next(b for b in at.button if b.label == 'Run scenario').click().run()
        self.assertEqual(at.session_state['sim_results']['waterfall_summary']['Net_COGS_Absorbed'],1)
        next(b for b in at.button if b.label=='Prepare forecasts').click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any(s.label=='Active forecast model — Copper' for s in at.selectbox))
        next(w for w in at.number_input if w.label=='Target benchmark price — Copper').set_value(4.2).run()
        self.assertFalse(at.exception)
        self.assertAlmostEqual(next(w for w in at.number_input if w.label=='Price change (%) — Copper').value,0)
        next(b for b in at.button if b.label == 'Run scenario').click().run()
        self.assertEqual(at.session_state['sim_results']['waterfall_summary']['Net_COGS_Absorbed'],0)
        self.assertTrue(at.session_state['sim_execution'].request.materials[0].forecast_id)

    def test_missing_selected_graph_never_substitutes_other_filing(self):
        at=self.page()
        context=dict(at.session_state['baseline_context']);context['snapshot_id']='not-available'
        at.session_state['baseline_context']=context;at.run()
        self.assertFalse(at.exception)
        self.assertTrue(any('no other filing was substituted' in w.value for w in at.warning))
        self.assertFalse(any('Company / filing snapshot'==s.label for s in at.selectbox))

    def test_old_tab_bookmarks_migrate_to_combined_workspace(self):
        for old in ('📈 Tab 2: Multi-Pool KPI Waterfall & Stochastic Stress Test','🕸️ Tab 3: Supply Chain Evidence Graph'):
            self.assertEqual(migrate_tab(old),SCENARIO_TAB)
        self.assertEqual(migrate_tab(None),DISCOVERY_TAB)
        self.assertEqual(migrate_tab(SCENARIO_TAB),SCENARIO_TAB)

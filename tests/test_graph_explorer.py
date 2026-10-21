"""Offline graph request, exposure, temporal scope and rendering regression tests."""
from dataclasses import replace
import json
import unittest
import test_graph_pipeline as fixtures

from pe_copilot.contracts import (GraphRequest, HighlightContext, MaterialShock,
    WorkspaceContext, capture_stress_context, stress_input_signature)
from pe_copilot.orchestrator import CopilotOrchestrator
from pe_copilot.supply_chain import Entity, GraphClaim, Observation
from pe_copilot.supply_chain.explorer import directed_chain, build_explorer_payload
from pe_copilot.supply_chain.graph_repository import GraphView
from pe_copilot.ui.graph_visualization import render_graph_html


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RepositoryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        observations = tuple(Observation(str(year), f.cid, 'net_sales', str(year), 'currency_millions',
            f'{year}-01-01', f'{year}-12-31', f.prov, currency='USD') for year in (2024, 2025))
        self.snapshot = replace(f.snapshot, observations=observations)
        f.save(self.snapshot)
        self.app = CopilotOrchestrator(None, None, f.repo)
        self.context = WorkspaceContext(f.cid, 'test-v1')
        self.material = f.entities[1].entity_id

    def test_shared_route_has_evidence_actual_trace_and_no_shared_selection(self):
        first = self.app.invoke(GraphRequest(self.context, focus_entity_id=self.material))
        self.assertEqual(first.status, 'ok')
        self.assertTrue(first.evidence_references)
        self.assertEqual(len(first.reported_facts), 2)
        self.assertTrue(all(s.status == 'passed' for s in first.trace))
        second = self.app.invoke(GraphRequest(self.context, source='chatbot'))
        self.assertIsNone(second.data['focus'])
        self.assertNotEqual(first.run_id, second.run_id)
        json.dumps(second.to_dict(), allow_nan=False)
        self.assertEqual(self.app.get_graph_snapshots('test')[0]['snapshot_id'], 'test-v1')
        self.assertEqual(self.app.get_graph_snapshots('DOW'), [])

    def test_percent_conversion_saved_inputs_and_finite_validation(self):
        result = {'materials': {'Feedstock': {'shock_pct': 20}}}
        saved = capture_stress_context('TEST', {'Feedstock': {'annual_spend': 25}}, .5, {}, result)
        self.assertEqual(saved.material_shocks[0].shock_fraction, .2)
        result['materials']['Feedstock']['shock_pct'] = 99
        self.assertEqual(saved.material_shocks[0].shock_fraction, .2)
        self.assertNotEqual(saved.input_signature, stress_input_signature('TEST', {'Feedstock': {'annual_spend': 30}}, .5, {}))
        for value in (float('nan'), float('inf'), True):
            with self.assertRaises(ValueError): MaterialShock('Feedstock', value)

    def test_overlay_downstream_and_scope_guards(self):
        signal = HighlightContext('tab2', 'run1', 'TEST', (MaterialShock('Feedstock', .2),))
        result = self.app.invoke(GraphRequest(self.context, highlight=signal))
        self.assertEqual(set(result.data['overlay']['edges']), {'uses', 'hierarchy', 'market'})
        self.assertNotIn(self.context.company_id, result.data['overlay']['nodes'])
        self.assertTrue(any('does not reconcile or allocate' in w for w in result.warnings))
        for wrong in (replace(signal, ticker='DOW'), replace(signal, snapshot_id='other'), replace(signal, company_id='company:0000000002')):
            blocked = self.app.invoke(GraphRequest(self.context, highlight=wrong))
            self.assertFalse(blocked.data['overlay']['nodes'])
            self.assertTrue(any('different company or filing' in w for w in blocked.warnings))
        self.assertIsNone(self.app.invoke(GraphRequest(self.context)).data['overlay']['source'])

    def test_zero_unknown_negative_and_chatbot_selection(self):
        for shock, expected in ((MaterialShock('Feedstock', 0), False), (MaterialShock('Unmapped', .2), False), (MaterialShock('Feedstock', -.2), True)):
            result = self.app.invoke(GraphRequest(self.context, highlight=HighlightContext('tab2', 'run', 'TEST', (shock,))))
            self.assertEqual(bool(result.data['overlay']['nodes']), expected)
        with self.assertRaises(ValueError): HighlightContext('chatbot', 'run', 'TEST')
        signal = HighlightContext('chatbot', 'run', 'TEST', entity_ids=(self.material,), company_id=self.context.company_id, snapshot_id='test-v1')
        result = self.app.invoke(GraphRequest(self.context, source='chatbot', highlight=signal))
        self.assertEqual(result.data['overlay']['roots'], [self.material])

    def test_period_filter_does_not_create_historical_graph(self):
        context = replace(self.context, period_start='2024-01-01', period_end='2024-12-31')
        result = self.app.invoke(GraphRequest(context))
        self.assertEqual([r['raw']['observation_id'] for r in result.data['observations']], ['2024'])
        self.assertEqual(result.data['summary']['period_end'], '2025-12-31')
        invalid = replace(context, period_start='2023-01-01', period_end='2023-12-31')
        self.assertEqual(self.app.invoke(GraphRequest(invalid)).status, 'unavailable')

    def test_sensitivity_keeps_its_as_of_date_when_comparative_period_changes(self):
        f = self.fixture
        sensitivity = Observation('sensitivity', f.cid, 'hypothetical_cost', '0.8', 'currency_millions',
            '2025-01-01', '2025-12-31', f.prov, currency='USD', horizon='annual',
            as_of='2025-12-31', related_entity_id=self.material, assumptions=('20% price increase',))
        snapshot = replace(self.snapshot, observations=(*self.snapshot.observations, sensitivity))
        view = GraphView(snapshot, f.registry, f.docs, [])
        context = replace(self.context, period_start='2024-01-01', period_end='2024-12-31')
        payload = build_explorer_payload(view, GraphRequest(context))
        row = next(r for r in payload['observations'] if r['id'] == 'sensitivity')
        self.assertIn('as of 2025-12-31', row['period'])
        self.assertIn('hypothetical', row['period'])
        self.assertEqual(row['raw']['subject'], f.cid)
        self.assertEqual(row['assumptions'], ['20% price increase'])

    def test_directed_chain_does_not_flood_sibling_branches(self):
        f = self.fixture
        sibling = 'concept:material:sibling'
        claims = (*f.claims, GraphClaim('sibling', sibling, 'used_in', f.entities[2].entity_id, f.prov))
        chain = directed_chain(self.material, claims, {e.entity_id for e in f.entities} | {sibling})
        self.assertNotIn(sibling, chain['nodes'])
        self.assertNotIn('sibling', chain['edges'])
        self.assertEqual(set(chain['edges']), {'uses', 'hierarchy', 'market'})

    def test_invalid_focus_and_missing_bundle_are_reviewable_results(self):
        self.assertEqual(self.app.invoke(GraphRequest(self.context, focus_entity_id='missing')).status, 'unavailable')
        result = self.app.invoke(GraphRequest(replace(self.context, snapshot_id='missing')))
        self.assertEqual(result.status, 'unavailable')
        self.assertEqual(result.trace[-1].status, 'failed')

    def test_html_embeds_untrusted_labels_as_data(self):
        payload = self.app.invoke(GraphRequest(self.context)).data
        payload['nodes'][0]['label'] = '</script><script>alert("x")</script>&'
        html = render_graph_html(payload)
        self.assertNotIn('</script><script>alert', html)
        encoded = html.split('<script id="graph-data" type="application/json">')[1].split('</script>')[0]
        self.assertEqual(json.loads(encoded)['nodes'][0]['label'], payload['nodes'][0]['label'])

    def test_tab_controls_and_stale_overlay(self):
        from streamlit.testing.v1 import AppTest
        def page(root):
            import streamlit as st
            from pe_copilot.orchestrator import CopilotOrchestrator
            from pe_copilot.supply_chain.graph_repository import GraphRepository
            from pe_copilot.ui.graph_explorer import render_supply_chain_tab
            render_supply_chain_tab(CopilotOrchestrator(None, None, GraphRepository(root)), 'TEST')
        at = AppTest.from_function(page, args=(str(self.fixture.repo.root),)).run()
        self.assertFalse(at.exception)
        at.selectbox(key='graph_overlay_mode').select('Latest Tab 2 run').run()
        self.assertTrue(any('Run a stress test' in i.value for i in at.info))
        at.session_state['sim_context'] = HighlightContext('tab2', 'run', 'TEST', (MaterialShock('Feedstock', .2),))
        at.session_state['sim_inputs_current'] = False
        at.run()
        self.assertTrue(any('Rerun the stress test' in w.value for w in at.warning))
        at.session_state['sim_inputs_current'] = True
        at.run()
        self.assertTrue(any('Exposure overlay' in c.value for c in at.caption))
        at.selectbox(key='graph_lens').select('all_relationships').run()
        self.assertFalse(at.exception)
        at.selectbox(key='graph_overlay_mode').select('Chatbot request').run()
        self.assertTrue(any('in development' in i.value for i in at.info))


if __name__ == '__main__':
    unittest.main()

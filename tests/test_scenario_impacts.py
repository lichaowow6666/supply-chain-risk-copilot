"""Synthetic tests for signs, financial scope, citations and filtered views."""
from copy import deepcopy
from dataclasses import replace
import unittest
import test_scenario_orchestration as fixtures
from pe_copilot.supply_chain.scenario_impacts import annotate_scenario, apply_saved_annotations
from pe_copilot.contracts import ScenarioMaterial
from pe_copilot.ui.graph_visualization import render_graph_html


class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ScenarioTests()
        self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.run = self.f.app.run_scenario(self.f.request)
        self.assertEqual(self.run.status, 'ok', self.run.warnings)

    def test_cost_direction_stops_before_markets_and_never_allocates_segment_dollars(self):
        graph = self.run.graph
        edges = {e['id']: e for e in graph['edges']}
        self.assertEqual(edges['uses']['impact']['direction'], 'adverse')
        self.assertEqual(edges['hierarchy']['impact']['direction'], 'adverse')
        self.assertEqual(edges['market']['impact']['direction'], 'uncertain')
        for name in ('uses', 'hierarchy'):
            note = edges[name]['impact']['notes'][0]
            self.assertIsNone(note['amount'])
            self.assertTrue(note['evidence'])
        company = next(n for n in graph['nodes'] if n['kind'] == 'company')
        self.assertEqual(company['impact']['notes'][0]['amount'], self.run.simulation['waterfall_summary']['Net_COGS_Absorbed'])
        segment = next(n for n in graph['nodes'] if n['kind'] == 'segment')
        self.assertTrue(all(n['amount'] is None for n in segment['impact']['notes']))

    def test_relief_and_full_pass_through(self):
        sim = deepcopy(self.run.simulation)
        sim['materials']['Copper'].pop('cost_contribution')  # Legacy saved result.
        sim['materials']['Copper']['gross_cost_increase'] = -4
        sim['materials']['Copper']['shock_pct'] = -20
        sim['waterfall_summary']['Net_COGS_Absorbed'] = -1
        result = annotate_scenario(self.run.graph, self.f.request, sim, ())
        copper = next(n for n in result['nodes'] if n['label'] == 'Copper')
        self.assertEqual(copper['impact']['direction'], 'favorable')
        self.assertEqual(result['scenario_explanations'][0]['amount'], -1)
        request = replace(self.f.request, pass_through_fraction=1)
        sim['waterfall_summary']['Net_COGS_Absorbed'] = 0
        result = annotate_scenario(self.run.graph, request, sim, ())
        self.assertEqual(next(e for e in result['edges'] if e['id'] == 'uses')['impact']['direction'], 'neutral')

    def test_multiple_opposing_paths_stay_mixed(self):
        graph = deepcopy(self.run.graph)
        copper = next(n for n in graph['nodes'] if n['label'] == 'Copper')
        other = deepcopy(copper); other.update(id='other', label='Silver'); graph['nodes'].append(other)
        edge = deepcopy(next(e for e in graph['edges'] if e['id']=='uses'))
        edge.update(id='silver-use', source='other'); graph['edges'].append(edge)
        graph['overlay']['edges'].append('silver-use')
        graph['overlay']['nodes'].append('other')
        request = replace(self.f.request, materials=(*self.f.request.materials, ScenarioMaterial('Silver',25,.8)))
        sim = deepcopy(self.run.simulation)
        sim['materials']['Silver'] = {**sim['materials']['Copper'], 'gross_cost_increase':-4, 'shock_pct':-20}
        sim['materials']['Silver'].pop('cost_contribution')
        result = annotate_scenario(graph, request, sim, ())
        product = next(n for n in result['nodes'] if n['kind']=='product')
        self.assertEqual(product['impact']['direction'], 'uncertain')
        self.assertTrue(all(n['amount'] is None for n in product['impact']['notes']))

    def test_filtered_paths_and_wrong_runs_do_not_reintroduce_evidence(self):
        graph = deepcopy(self.run.graph)
        for row in (*graph['nodes'], *graph['edges']): row.pop('impact',None)
        graph['edges'] = [e for e in graph['edges'] if e['id']!='uses']
        filtered = apply_saved_annotations(graph, self.run)
        self.assertNotIn('impact', next(n for n in filtered['nodes'] if n['kind']=='segment'))
        graph['overlay']['run_id'] = 'different'
        self.assertEqual(apply_saved_annotations(graph,self.run),graph)
        graph['summary']['snapshot_id'] = 'wrong'
        with self.assertRaises(ValueError): annotate_scenario(graph,self.f.request,self.run.simulation,())

    def test_saved_cost_not_rounded_shock_drives_amount_and_citations_are_retained(self):
        sim = deepcopy(self.run.simulation)
        sim['materials']['Copper'].pop('cost_contribution')
        sim['materials']['Copper'].update(gross_cost_increase=4, shock_pct=99)
        exposure = ({'material':'Copper','raw_chunks':[{'text':'Synthetic cited material evidence'}]},)
        graph = annotate_scenario(self.run.graph,self.f.request,sim,exposure)
        note = graph['scenario_explanations'][0]
        self.assertEqual(note['amount'],1)
        self.assertEqual(note['evidence'], exposure[0]['raw_chunks'])
        self.assertIn('round',note['limits'][0])
        self.assertIn('scenario_explanations',self.run.to_dict()['graph'])

    def test_graph_has_no_type_dots_and_explanations_are_escaped(self):
        graph = deepcopy(self.run.graph)
        graph['scenario_explanations'][0]['text'] = '</script><script>alert(1)</script>'
        html = render_graph_html(graph)
        self.assertNotIn("make('circle'",html)
        for label, color in (('Adverse', '#fa7777'), ('Favorable', '#70db9c'),
                             ('Selected chain', '#a8c9ff')):
            self.assertIn(f'background:{color}"></i>{label}</span>', html)
        self.assertNotIn('#61e2d1',html)
        self.assertIn('Impact not assessed',html)
        self.assertNotIn('</script><script>alert(1)</script>',html)


if __name__ == '__main__': unittest.main()

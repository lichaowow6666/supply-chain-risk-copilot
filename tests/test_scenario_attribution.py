"""Attribution reconciles; reference comparisons isolate assumption changes."""
from copy import deepcopy
from dataclasses import replace
import unittest
import test_scenario_orchestration as fixtures
from pe_copilot.analytics.scenario_attribution import contribution_rows, compare_scenarios
from pe_copilot.contracts import ScenarioMaterial


class AttributionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ScenarioTests(); self.f.setUp(); self.addCleanup(self.f.doCleanups)
        self.reference = self.f.app.run_scenario(self.f.request)

    def test_hedge_change_improves_reference_but_remains_adverse_to_reported_baseline(self):
        current = self.f.app.run_scenario(replace(self.f.request, materials=(ScenarioMaterial('Copper',25,.5),)))
        comparison = compare_scenarios(self.reference,current)
        cogs = next(r for r in comparison['kpis'] if r['Metric']=='COGS')
        self.assertLess(cogs['Change vs reference ($M)'],0)
        self.assertGreater(cogs['Current scenario ($M)'],cogs['Reported / derived baseline ($M)'])
        old = self.reference.simulation['materials']['Copper']['cost_contribution']['absorbed']
        new = current.simulation['materials']['Copper']['cost_contribution']['absorbed']
        self.assertAlmostEqual(new/old,.5/.8)
        company = next(n for n in current.graph['nodes'] if n['kind']=='company')
        self.assertEqual(company['impact']['direction'],'adverse')
        self.assertEqual(self.reference.request.materials[0].unhedged_fraction,.8)

    def test_contributions_reconcile_including_display_rounding(self):
        sim = {'materials':{m:{'cost_contribution':{'gross':.006,'passed':.0015,'absorbed':.0045}}
                            for m in ('One','Two')},
               'waterfall_summary':{'Total_Gross_COGS_Surge':.01,'Passed_To_Clients':0,
                                    'Net_COGS_Absorbed':.01,'Pass_Through_Rate_Pct':25}}
        rows = contribution_rows(sim)
        self.assertTrue(any(r['Material']=='Display rounding reconciliation' for r in rows))
        for field in ('Gross cost change ($M)','Passed to customers ($M)','COGS contribution ($M)','Operating income contribution ($M)'):
            self.assertAlmostEqual(sum(r[field] for r in rows[:-1]),rows[-1][field])

    def test_legacy_run_is_explicitly_approximate(self):
        sim=deepcopy(self.reference.simulation)
        sim['materials']['Copper'].pop('cost_contribution')
        self.assertIn('Older run',contribution_rows(sim)[0]['Basis'])

    def test_comparison_rejects_mismatched_market_report_model_and_materials(self):
        for bad in (
            replace(self.reference, market_snapshot={'fingerprint':'changed'}),
            replace(self.reference, model_version='another'),
            replace(self.reference, request=replace(self.f.request, accession='another')),
            replace(self.reference, request=replace(self.f.request, materials=(ScenarioMaterial('Silver',25,.8),))),
        ):
            with self.assertRaises(ValueError): compare_scenarios(self.reference,bad)

    def test_exact_components_preserve_original_cost_formula_and_export(self):
        row=self.reference.simulation['materials']['Copper']; c=row['cost_contribution']
        self.assertAlmostEqual(c['gross'],25*.8*c['shock_fraction'])
        self.assertAlmostEqual(c['absorbed'],c['gross']*.25)
        self.assertEqual(row['gross_cost_increase'],round(c['gross'],2))
        self.assertEqual(self.reference.to_dict()['simulation']['materials']['Copper']['cost_contribution']['unit'],'USD millions')

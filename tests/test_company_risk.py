"""Synthetic joint risk cases: dependence, signs, provenance and financial parity."""
from copy import deepcopy
from dataclasses import replace
import json
import unittest
import numpy as np
import pandas as pd
from pe_copilot.analytics.company_risk import build_company_risk
from pe_copilot.contracts import ScenarioRequest, ScenarioMaterial, WorkspaceContext
from pe_copilot.agents.market_agent import MarketAgent


def fixture():
    materials = tuple(ScenarioMaterial(n,100,.8,0.,100.,str(i)*64,'naive') for i,n in enumerate(('Copper','Natural Gas'),1))
    request = ScenarioRequest(WorkspaceContext('company:0000000001','synthetic-v1'),'TEST','synthetic',materials,.25)
    pools = {'income_statement':{'Revenue':1000.,'COGS':400.,'Gross Profit':600.,'EBITDA':200.,'Operating Income':120.,'Net Income':90.},
             'balance_sheet':{'Cash & Equivalents':10.,'Working Capital':80.,'Total Debt':50.,'Net Debt':40.,'Total Assets':800.,'Total Equity':400.},
             'cash_flow':{'Operating Cash Flow':100.,'Free Cash Flow':70.,'CapEx':30.}}
    forecasts = {}
    for m in materials:
        records = [{'origin':f'2024-{i+1:02d}-01','target_end':f'2024-{i+1:02d}-28',
                    'log_errors':[float(e)]*30} for i,e in enumerate(np.linspace(-.3,.3,12))]
        forecasts[m.material] = {'forecast_id':m.forecast_id,'material':m.material,'horizon':30,'reference_price':100.,
            'market':{'end':'2025-01-01'},'models':{'naive':{'path':[100.]*30,'development_errors':records}}}
    return request,pools,forecasts


class CompanyRiskTests(unittest.TestCase):
    def setUp(self):
        self.request,self.pools,self.forecasts=fixture()

    def test_every_case_matches_existing_market_engine_across_all_pools(self):
        risk=build_company_risk(self.request,self.pools,self.forecasts)
        self.assertEqual(risk['status'],'ok')
        for case in risk['cases']:
            configs=self.request.material_configs()
            histories={}
            for name,row in case['materials'].items():
                configs[name]['shock_fraction']=row['shock_fraction']
                histories[name]=pd.DataFrame({'Date':pd.bdate_range(end='2026-09-21',periods=2),'Price':[99.,100.]})
            result=MarketAgent().calculate_multi_material_waterfall(configs,self.request.pass_through_fraction,self.pools,histories)
            self.assertEqual(case['stressed_pools'],result['stressed_pools'])
            self.assertEqual(case['net_absorbed_cost'],result['waterfall_summary']['Net_COGS_Absorbed'])
        json.dumps(risk,allow_nan=False)

    def test_paired_material_errors_preserve_dependence_and_hedging(self):
        same=build_company_risk(self.request,self.pools,self.forecasts)
        records=self.forecasts['Natural Gas']['models']['naive']['development_errors']
        for r in records:
            r['log_errors']=[float(np.log(2-np.exp(r['log_errors'][0])))]*30
        offset=build_company_risk(self.request,self.pools,self.forecasts)
        self.assertGreater(same['net_absorbed_cost']['p90'],10)
        self.assertEqual(offset['net_absorbed_cost']['p90'],0)
        hedged=replace(self.request,materials=tuple(replace(m,unhedged_fraction=0) for m in self.request.materials))
        self.assertEqual(build_company_risk(hedged,self.pools,self.forecasts)['net_absorbed_cost']['mean'],0)
        passed=replace(self.request,pass_through_fraction=1)
        self.assertEqual(build_company_risk(passed,self.pools,self.forecasts)['net_absorbed_cost']['mean'],0)

    def test_tail_attribution_is_additive_with_fractional_boundary_weights(self):
        r=build_company_risk(self.request,self.pools,self.forecasts)
        expected=sum(x['worst_20pct_mean_absorbed_cost'] for x in r['material_contributions'])
        self.assertAlmostEqual(expected,r['worst_20pct_mean_cost'],delta=.01)
        costs=sorted(c['net_absorbed_cost'] for c in r['cases'])
        self.assertAlmostEqual(r['worst_20pct_mean_cost'],(costs[-1]+costs[-2]+.4*costs[-3])/2.4)

    def test_manual_scenario_does_not_recenter_model_distribution(self):
        a=build_company_risk(self.request,self.pools,self.forecasts)
        req=replace(self.request,materials=tuple(replace(m,shock_fraction=.8) for m in self.request.materials))
        b=build_company_risk(req,self.pools,self.forecasts)
        self.assertEqual(a['cases'],b['cases'])
        self.assertNotEqual(a['risk_id'],b['risk_id'])

    def test_missing_models_and_nonmatched_dates_do_not_assume_independence(self):
        forecasts=deepcopy(self.forecasts);forecasts.pop('Copper')
        self.assertEqual(build_company_risk(self.request,self.pools,forecasts)['status'],'unavailable')
        forecasts=deepcopy(self.forecasts)
        forecasts['Copper']['models']['naive']['development_errors']=forecasts['Copper']['models']['naive']['development_errors'][:7]
        self.assertIn('Only 7',build_company_risk(self.request,self.pools,forecasts)['reason'])
        forecasts['Copper']['market']['end']='2025-01-02'
        self.assertIn('different latest dates',build_company_risk(self.request,self.pools,forecasts)['reason'])

    def test_invalid_and_impossible_tail_cases_are_not_dropped(self):
        self.forecasts['Copper']['models']['naive']['development_errors'][0]['log_errors']=[float('nan')]*30
        self.assertEqual(build_company_risk(self.request,self.pools,self.forecasts)['status'],'unavailable')
        request,pools,forecasts=fixture()
        pools['income_statement']['COGS']=1.
        self.assertIn('negative COGS',build_company_risk(request,pools,forecasts)['reason'])

    def test_identity_and_horizon_and_model_must_match(self):
        for field,value in [('horizon',20),('forecast_id','x'*64),('reference_price',101),('material','Gold')]:
            forecasts=deepcopy(self.forecasts);forecasts['Copper'][field]=value
            self.assertEqual(build_company_risk(self.request,self.pools,forecasts)['status'],'unavailable')

    def test_missing_kpis_are_not_fabricated(self):
        self.pools={'income_statement':{'COGS':400.}}
        r=build_company_risk(self.request,self.pools,self.forecasts)
        self.assertEqual([k['metric'] for k in r['kpis']],['COGS'])

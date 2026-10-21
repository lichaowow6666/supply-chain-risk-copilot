"""Offline synthetic evidence/market tests for the shared scenario pipeline."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import duckdb
from pe_copilot.agents.fundamental_agent import FundamentalAgent
from pe_copilot.agents.market_agent import MarketAgent
from pe_copilot.contracts import ScenarioRequest, ScenarioMaterial, WorkspaceContext
from pe_copilot.orchestrator import CopilotOrchestrator
import test_graph_pipeline as fixtures


class ScenarioTests(unittest.TestCase):
    def setUp(self):
        f = self.fixture = fixtures.RepositoryTests()
        f.setUp();self.addCleanup(f.doCleanups)
        f.save(replace(f.snapshot,entities=(f.entities[0],replace(f.entities[1],label='Copper'),*f.entities[2:])))
        self.root=Path(f.tmp.name)
        self.market=MarketAgent(str(self.root/'synthetic.duckdb'))
        with duckdb.connect(self.market.db_path) as db:
            db.execute('CREATE TABLE futures_prices (Date TIMESTAMP, Metal VARCHAR, Ticker VARCHAR, Price DOUBLE, Volume BIGINT)')
            db.executemany('INSERT INTO futures_prices VALUES (?, ?, ?, ?, ?)',
                [('2025-12-01','Copper','HG=F',4.,10),('2025-12-02','Copper','HG=F',4.4,20),('2025-12-03','Copper','HG=F',4.2,30)])
        pools={'income_statement':{'Revenue':125.,'COGS':100.,'Operating Income':25.},
               'balance_sheet':{'Cash & Equivalents':50.},'cash_flow':{'Operating Cash Flow':30.}}
        self.baseline={'pools':pools,'evidence':{k:{'value':str(v),'start':'2025-12-31' if k=='Cash & Equivalents' else '2025-01-01',
                       'end':'2025-12-31','accession':'synthetic-annual','basis':'reported'} for p in pools.values() for k,v in p.items()},
                       'warnings':[],'accession':'synthetic-annual','snapshot_id':'test-v1','period_end':'2025-12-31',
                       'duration_start':'2025-01-01','form':'10-K','currency':'USD','unit':'millions','source_url':'https://example.invalid/synthetic'}
        self.materials=[{'material':'Copper','source_accession':'synthetic-annual','raw_chunks':[]}]
        self.service=Mock()
        self.service.load_preparation.return_value={'status':'complete','company':{'ticker':'TEST','cik':'0000000001'}}
        self.service.report_baseline.side_effect=lambda *a:deepcopy(self.baseline)
        self.service.scan.side_effect=lambda *a:(deepcopy(self.materials),deepcopy(self.baseline['pools']))
        self.fundamental=FundamentalAgent.__new__(FundamentalAgent)
        self.fundamental.preparation_service=self.service
        self.app=CopilotOrchestrator(self.market,self.fundamental,f.repo,self.service,self.root/'runs')
        self.request=ScenarioRequest(WorkspaceContext(f.cid,'test-v1','2025-01-01','2025-12-31'),'TEST','synthetic-annual',
                                     (ScenarioMaterial('Copper',25,.8),),.75)

    def test_shared_result_pins_history_and_preserves_existing_calculations(self):
        previous=self.market.calculate_multi_material_waterfall(self.request.material_configs(),.75,self.baseline['pools'])
        steps=[]
        with patch.object(self.market,'fetch_price_history',side_effect=AssertionError('Do not refetch during a run')):
            result=self.app.invoke(self.request,on_step=steps.append)
        self.assertEqual(result.status,'ok',result.warnings)
        self.assertEqual(result.simulation['stressed_pools'],previous['stressed_pools'])
        self.assertEqual(result.simulation['waterfall_summary'],previous['waterfall_summary'])
        self.assertEqual(result.highlight.run_id,result.run_id)
        self.assertEqual(result.graph['overlay']['run_id'],result.run_id)
        self.assertEqual(set(result.graph['overlay']['edges']),{'uses','hierarchy','market'})
        self.assertEqual(result.trace,tuple(steps))
        self.assertTrue(any(s.actor=='Fundamental Agent' and s.status=='passed' for s in steps))
        self.assertTrue(any(s.actor=='Market Agent' and s.basis=='deterministic calculation' for s in steps))
        self.assertEqual(result.market_snapshot['series']['Copper']['ticker'],'HG=F')
        record=json.loads((self.root/'runs'/(result.run_id+'.json')).read_text())
        self.assertEqual(record['simulation']['materials']['Copper']['history_df'][0]['Price'],4.)
        self.assertEqual(record['input_fingerprint'],result.input_fingerprint)
        json.dumps(result.to_dict(),allow_nan=False)

    def test_changed_market_price_changes_fingerprint_not_prior_record(self):
        first=self.app.run_scenario(self.request)
        with duckdb.connect(self.market.db_path) as db:
            db.execute("UPDATE futures_prices SET Price=4.8 WHERE Date='2025-12-03'")
        second=self.app.run_scenario(self.request)
        self.assertNotEqual(first.input_fingerprint,second.input_fingerprint)
        self.assertNotEqual(first.run_id,second.run_id)
        self.assertEqual(first.simulation['materials']['Copper']['history_df'].iloc[-1]['Price'],4.2)

    def test_scope_and_period_mismatches_stop_before_market_calculation(self):
        for req in (replace(self.request,ticker='OTHER'),replace(self.request,accession='another-report'),
                    replace(self.request,spend_basis='year-to-date'),
                    replace(self.request,context=replace(self.request.context,snapshot_id='other')),
                    replace(self.request,context=replace(self.request.context,period_start='2024-01-01'))):
            with patch.object(self.market,'capture_price_snapshot') as capture:
                result=self.app.run_scenario(req)
            self.assertEqual(result.status,'failed')
            self.assertFalse(result.simulation);self.assertIsNone(result.highlight)
            capture.assert_not_called()

    def test_unsupported_exposure_or_financial_evidence_is_rejected(self):
        for metric in ('source_accession','material'):
            old=deepcopy(self.materials)
            self.materials[0][metric]='other'
            self.assertEqual(self.app.run_scenario(self.request).status,'failed')
            self.materials=old
        self.baseline['pools']['income_statement']['COGS']=1234.
        self.assertEqual(self.app.run_scenario(self.request).status,'failed')

    def test_missing_graph_is_partial_without_losing_valid_financial_result(self):
        with patch.object(self.fixture.repo,'load',side_effect=FileNotFoundError):
            result=self.app.run_scenario(self.request)
        self.assertEqual(result.status,'partial')
        self.assertTrue(result.simulation);self.assertFalse(result.graph)
        self.assertTrue(any(s.actor=='Graph service' and s.status=='failed' for s in result.trace))

    def test_provider_errors_do_not_leak_credentials_or_old_results(self):
        for error in (RuntimeError, ValueError):
            with patch.object(self.fundamental,'analyze_report',side_effect=error('SECRET_TEST_CREDENTIAL')):
                result=self.app.run_scenario(self.request)
            self.assertEqual(result.status,'failed')
            self.assertNotIn('SECRET_TEST_CREDENTIAL',json.dumps(result.to_dict()))
            self.assertFalse(result.simulation)

    def test_invalid_market_data_and_nonfinite_baseline_fail_closed(self):
        with duckdb.connect(self.market.db_path) as db:
            db.execute('UPDATE futures_prices SET Price=-1')
        self.assertEqual(self.app.run_scenario(self.request).status,'failed')
        self.baseline['pools']['income_statement']['COGS']=float('nan')
        result=self.app.run_scenario(self.request)
        self.assertEqual(result.status,'failed')
        json.dumps(result.to_dict(),allow_nan=False)

    def test_chatbot_route_uses_same_pipeline(self):
        result=self.app.invoke(replace(self.request,source='chatbot'))
        self.assertEqual(result.status,'ok')
        self.assertEqual(result.highlight.source,'chatbot')
        self.assertEqual(result.highlight.run_id,result.run_id)

    def test_request_validation(self):
        for value in (float('nan'),float('inf'),True,-1):
            with self.assertRaises(ValueError):ScenarioMaterial('Copper',value,.8)
        with self.assertRaises(ValueError):replace(self.request,materials=self.request.materials*2)
        with self.assertRaises(ValueError):replace(self.request,pass_through_fraction=1.1)
        with self.assertRaises(ValueError):replace(self.request,currency='EUR')
        with self.assertRaises(ValueError):replace(self.request,shock_horizon_days=90)

    def test_quarterly_scope_preserves_ytd_spend_and_horizon(self):
        self.baseline['form']='10-Q'
        self.baseline['period_end']='2025-06-30'
        for metric,row in self.baseline['evidence'].items():
            row['end']='2025-06-30'
            if metric=='Cash & Equivalents':row['start']='2025-06-30'
        request=replace(self.request,spend_basis='year-to-date',context=replace(self.request.context,period_end='2025-06-30'))
        with patch.object(self.fixture.repo,'load',side_effect=FileNotFoundError):
            result=self.app.run_scenario(request)
        self.assertEqual(result.status,'partial')
        self.assertIn('year-to-date',result.highlight.horizon)
        self.assertEqual(result.request.material_configs()['Copper']['annual_spend'],25)

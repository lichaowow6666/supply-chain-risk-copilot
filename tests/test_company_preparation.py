"""Offline onboarding, SEC acquisition, XBRL, history and financial-boundary tests."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from pe_copilot.filings.sec import FilingStore, SECClient, select_filings, atomic_json
from pe_copilot.filings.financials import read_inline_facts, baseline_from_facts
from pe_copilot.filings.extraction import validate_extraction
from pe_copilot.filings.history import period_kind, observation_history
from pe_copilot.filings.index import FilingIndex
from pe_copilot.supply_chain.preparation import prepare_document
from pe_copilot.ui.company_setup import reset_analysis_state
from pe_copilot.agents.market_agent import MarketAgent
import test_graph_pipeline as fixtures
from pe_copilot.supply_chain import Observation
from pe_copilot.supply_chain.graph_repository import GraphView


def filing(year, form='10-K', quarter=4):
    end = f'{year}-12-31' if quarter == 4 else f'{year}-{quarter*3:02d}-{30 if quarter==2 or quarter==3 else 31}'
    return {'accessionNumber':f'0000000001-{str(year)[2:]}-{quarter:06d}', 'form':form, 'reportDate':end,
            'filingDate':f'{year+1}-02-15' if form.startswith('10-K') else f'{year}-{quarter*3+1:02d}-25','primaryDocument':'filing.htm'}


class SelectionTests(unittest.TestCase):
    def test_three_annual_years_plus_quarters_and_amendments(self):
        records = [filing(y) for y in range(2021,2026)] + [filing(y,'10-Q',q) for y in range(2022,2027) for q in (1,2,3)]
        amendment = {**filing(2024),'accessionNumber':'0000000001-25-999999','form':'10-K/A','filingDate':'2025-06-01'}
        chosen,start,count = select_filings(records+[amendment],date(2026,9,22))
        self.assertEqual(start,'2023-01-01'); self.assertEqual(count,3)
        self.assertEqual(len(chosen),15)
        self.assertIn(amendment,chosen)
        self.assertNotIn(filing(2026,'10-Q',3),chosen)
        self.assertEqual(len({r['accessionNumber'] for r in chosen}),len(chosen))

    def test_new_issuer_does_not_fabricate_three_years(self):
        chosen,_,count = select_filings([filing(2025)],date(2026,9,22))
        self.assertEqual(count,1);self.assertEqual(len(chosen),1)
        with self.assertRaises(ValueError):select_filings([])

    def test_non_calendar_fiscal_year_window(self):
        record = {**filing(2025),'reportDate':'2025-06-30'}
        _,start,_ = select_filings([record],date(2026,9,22))
        self.assertEqual(start,'2024-07-01')

    def test_resolver_exact_ticker_name_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = SECClient(session=Mock())
            client.get = Mock(return_value={'0':{'ticker':'AAA','cik_str':1,'title':'Alpha Chemicals Inc.'},'1':{'ticker':'BBB','cik_str':2,'title':'Alpha Materials Inc.'}})
            self.assertEqual(client.resolve('aaa',tmp)['cik'],'0000000001')
            self.assertEqual(client.resolve('Alpha Chemicals Inc',tmp)['ticker'],'AAA')
            with self.assertRaisesRegex(ValueError,'ambiguous'):client.resolve('Alpha',tmp)
            self.assertEqual(client.get.call_count,1)

    def test_sec_host_allowlist(self):
        with self.assertRaises(ValueError):SECClient(session=Mock()).get('https://example.com/private')


class DownloadTests(unittest.TestCase):
    def test_existing_files_verified_then_corruption_redownloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            company = {'cik':'0000000001','ticker':'AAA','name':'Alpha'}
            client = Mock()
            client.resolve.return_value = company
            client.submissions.return_value = [filing(2025)]
            html = ('<html><body><p>'+('Synthetic fixture '*100)+'</p></body></html>').encode()
            client.get.side_effect = lambda url,as_json=True: {'cik':1,'facts':{}} if as_json else html
            store = FilingStore(tmp,client)
            first,reused = store.acquire('AAA')
            self.assertFalse(reused); self.assertTrue(first['download_complete'])
            self.assertTrue(store.verified(first));self.assertEqual(first['annual_period_count'],1)
            calls = client.get.call_count
            second,reused = store.acquire('AAA')
            self.assertTrue(reused);self.assertEqual(client.get.call_count,calls)
            (store.company_dir(company)/first['filings'][0]['local_path']).write_text('broken')
            store.acquire('AAA')
            self.assertGreater(client.get.call_count,calls)

    def test_partial_failure_never_sets_complete_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            company = {'cik':'0000000001','ticker':'AAA','name':'Alpha'}
            client = Mock();client.resolve.return_value = company
            client.submissions.return_value = [filing(2024),filing(2025)]
            html = ('<html>'+('x'*1200)+'</html>').encode()
            client.get.side_effect = [html,RuntimeError('connection lost')]
            store = FilingStore(tmp,client)
            with self.assertRaises(RuntimeError):store.acquire('AAA')
            saved = store.load('AAA')
            self.assertFalse(saved['download_complete']);self.assertEqual(len(saved['filings']),1)
            client.get.side_effect = lambda url,as_json=True:{'cik':1,'facts':{}} if as_json else html
            result,_ = store.acquire('AAA')
            self.assertEqual(len(result['filings']),2);self.assertTrue(result['download_complete'])


class XbrlTests(unittest.TestCase):
    def test_numeric_sign_scale_zero_and_company_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'facts.html'
            path.write_text('''<html><body><xbrli:context id="ours"><xbrli:entity><xbrli:identifier>0000000001</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2025-01-01</xbrli:startDate><xbrli:endDate>2025-12-31</xbrli:endDate></xbrli:period></xbrli:context>
            <xbrli:context id="other"><xbrli:entity><xbrli:identifier>0000000002</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2025-12-31</xbrli:instant></xbrli:period></xbrli:context>
            <xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
            <ix:nonFraction name="us-gaap:NetIncomeLoss" contextRef="ours" unitRef="usd" scale="6" sign="-">12.5</ix:nonFraction>
            <ix:nonFraction name="us-gaap:InterestExpense" contextRef="ours" unitRef="usd" scale="6" format="ixt:fixed-zero">—</ix:nonFraction>
            <ix:nonFraction name="us-gaap:Assets" contextRef="other" unitRef="usd">999</ix:nonFraction></body></html>''')
            facts = read_inline_facts(path,'0000000001')
            self.assertEqual(len(facts),2)
            self.assertEqual([Decimal(f['value']) for f in facts],[Decimal('-12500000'),Decimal('0')])

    def make_fact(self, tag,value,start='2025-01-01',end='2025-12-31',instant=False):
        return {'tag':'us-gaap:'+tag,'value':str(value),'unit':'USD','start':start,'end':end,'instant':instant,'dimensions':{},'context_id':'annual','raw_text':str(value),'scale':'0','sign':''}

    def test_baseline_uses_annual_context_and_derives_in_decimal(self):
        facts = [self.make_fact('Revenues',10000000),self.make_fact('CostOfRevenue',7000000),
                 self.make_fact('Revenues',3000000,'2025-10-01'),self.make_fact('NetCashProvidedByUsedInOperatingActivities',2000000),
                 self.make_fact('PaymentsToAcquirePropertyPlantAndEquipment',500000)]
        result = baseline_from_facts(facts,'2025-12-31','test')
        self.assertEqual(result['pools']['income_statement']['Revenue'],10)
        self.assertEqual(result['pools']['income_statement']['Gross Profit'],3)
        self.assertEqual(result['pools']['cash_flow']['Free Cash Flow'],1.5)
        self.assertNotIn('EBITDA',result['pools']['income_statement'])
        self.assertNotIn('Total Debt',result['pools']['balance_sheet'])
        self.assertEqual(result['evidence']['Free Cash Flow']['basis'],'derived')

    def test_conflicting_fact_and_missing_component_do_not_become_zero(self):
        facts = [self.make_fact('Revenues',10),self.make_fact('Revenues',20),self.make_fact('NetIncomeLoss',-3)]
        result = baseline_from_facts(facts,'2025-12-31')
        self.assertNotIn('Revenue',result['pools']['income_statement'])
        self.assertNotIn('EBITDA',result['pools']['income_statement'])
        self.assertEqual(result['pools']['income_statement']['Net Income'],-.000003)
        self.assertTrue(any('conflicting' in w for w in result['warnings']))

    def test_complete_submission_10q_is_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'filing.txt'
            path.write_text('<DOCUMENT>\n<TYPE>10-Q\n<TEXT><p>Quarterly synthetic evidence.</p></TEXT></DOCUMENT><DOCUMENT>\n<TYPE>EX-99\n<TEXT><p>Other exhibit.</p></TEXT></DOCUMENT>')
            prepared = prepare_document(path)
            self.assertFalse(any('Other exhibit' in b.text for b in prepared.blocks))


class EvidenceAndHistoryTests(unittest.TestCase):
    def test_short_entity_names_accepted_but_fabricated_quotes_and_edges_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.html';path.write_text('<p>Natural gas is a feedstock; its price increases input costs.</p>')
            prepared=prepare_document(path);block=prepared.blocks[0]
            raw={'entities':[{'ref':'gas','kind':'material','label':'Natural gas','block_id':block.block_id,'quote':'Natural gas'}],
                 'claims':[{'subject':'gas','object':'missing','relation':'used_in','block_id':block.block_id,'quote':block.text}],
                 'exposures':[{'material_ref':'gas','level':'High','block_id':block.block_id,'quote':block.text}], 'gaps':[]}
            valid=validate_extraction(raw,prepared)
            self.assertEqual(len(valid['entities']),1);self.assertEqual(len(valid['exposures']),1);self.assertFalse(valid['claims'])
            raw['entities'][0]['quote']='Natural gas has unlimited supply.'
            self.assertFalse(validate_extraction(raw,prepared)['entities'])

    def test_full_text_queries_cannot_leak_company_or_accession(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.html';path.write_text('<p>Natural gas feedstock price risk.</p>');prepared=prepare_document(path)
            index=FilingIndex(Path(tmp)/'index.sqlite')
            report={**filing(2025),'url':'https://www.sec.gov/example'}
            index.put({'cik':'0000000001','ticker':'AAA'},report,prepared)
            self.assertTrue(index.query('gas','AAA',report['accessionNumber']))
            self.assertFalse(index.query('gas','BBB',report['accessionNumber']))
            self.assertFalse(index.query('gas','AAA','different'))
            index.put({'cik':'0000000001','ticker':'AAA'},report,prepared)
            self.assertEqual(len(index.query('gas','AAA',report['accessionNumber'])),1)

    def test_period_kinds_keep_quarters_ytd_and_instants_distinct(self):
        self.assertEqual(period_kind('2025-01-01','2025-12-31'),'Annual')
        self.assertEqual(period_kind('2025-04-01','2025-06-30'),'Quarter (reported)')
        self.assertEqual(period_kind('2025-01-01','2025-06-30'),'Six months year-to-date')
        self.assertEqual(period_kind('2025-06-30','2025-06-30'),'Instant / balance sheet')

    def test_revisions_and_company_scope_in_history(self):
        fixture=fixtures.RepositoryTests();fixture.setUp();self.addCleanup(fixture.doCleanups)
        f=fixture
        obs=Observation('sales',f.cid,'Revenue','10','currency_millions','2025-01-01','2025-12-31',f.prov,currency='USD')
        old=GraphView(replace(f.snapshot,snapshot_id='a',observations=(obs,)),f.registry,f.docs,[])
        new=GraphView(replace(f.snapshot,snapshot_id='b',observations=(replace(obs,value='12'),)),f.registry,f.docs,[])
        self.assertEqual(observation_history([old,new],f.cid,'original')[0]['decimal_value'],'10')
        self.assertEqual(observation_history([old,new],f.cid,'latest')[0]['decimal_value'],'12')
        self.assertTrue(observation_history([old,new],f.cid,'latest')[0]['disclosure_changed'])
        self.assertFalse(observation_history([old,new],'company:0000000002'))

    def test_company_switch_clears_analysis_but_keeps_input(self):
        state={'company_query':'AAA','top_materials':[1],'sim_results':{},'graph_external_highlight':'old','spend_Gas':25,'unhedged_Gas':80,'baseline_context':{},'other':1}
        reset_analysis_state(state)
        self.assertEqual(state,{'company_query':'AAA','other':1})


class StressBoundaryTests(unittest.TestCase):
    def setUp(self):
        import pandas as pd
        self.agent=MarketAgent()
        self.agent.fetch_price_history=lambda material:pd.DataFrame({'Date':pd.date_range('2026-09-01',periods=4),'Price':[10.,11.,10.5,11.5]})

    def test_missing_kpis_are_not_populated_with_example_figures(self):
        result=self.agent.calculate_multi_material_waterfall({'Natural Gas':{'annual_spend':25.,'unhedged_pct':.8}},.75,{'income_statement':{'COGS':100.,'Net Income':-10.},'balance_sheet':{'Total Equity':200.},'cash_flow':{}})
        self.assertEqual(set(result['stressed_pools']['income_statement']),{'COGS','Net Income'})
        self.assertEqual(set(result['stressed_pools']['balance_sheet']),{'Total Equity'})
        self.assertFalse(result['stressed_pools']['cash_flow'])
        hit=result['waterfall_summary']['Net_COGS_Absorbed']
        self.assertEqual(result['stressed_pools']['income_statement']['Net Income']['stressed'],round(-10-hit*.79,2))

    def test_existing_formulas_and_scenario_defaults_preserved(self):
        import numpy as np
        bases={'income_statement':{'Revenue':1000.,'COGS':700.,'Gross Profit':300.,'EBITDA':100.,'Operating Income':90.,'Net Income':50.},
               'balance_sheet':{'Cash & Equivalents':10.,'Working Capital':100.,'Total Debt':150.,'Net Debt':140.,'Total Assets':500.,'Total Equity':200.},
               'cash_flow':{'Operating Cash Flow':80.,'CapEx':30.,'Free Cash Flow':50.}}
        np.random.seed(99);expected=np.random.rand();np.random.seed(99)
        result=self.agent.calculate_multi_material_waterfall({'Natural Gas':{'annual_spend':25.,'unhedged_pct':.8}},.75,bases)
        self.assertEqual(np.random.rand(),expected)
        shock=round(1.96*np.std(np.diff(np.log([10.,11.,10.5,11.5])))*np.sqrt(30),4)
        hit=round(25*shock*.8*.25,2)
        self.assertEqual(result['waterfall_summary']['Net_COGS_Absorbed'],hit)
        self.assertEqual(result['stressed_pools']['income_statement']['EBITDA']['stressed'],round(100-hit,2))
        self.assertEqual(result['stressed_pools']['balance_sheet']['Total Debt']['stressed'],round(150+max(0,hit-5),2))
        self.assertEqual(result['stressed_pools']['cash_flow']['Free Cash Flow']['stressed'],round(50-hit,2))

    def test_invalid_market_data_or_spend_fails_closed(self):
        import pandas as pd
        bases={'income_statement':{'COGS':100.}}
        self.agent.fetch_price_history=lambda material:pd.DataFrame({'Date':['2026-01-01','2026-01-02'],'Price':[1.,-1.]})
        with self.assertRaises(ValueError):self.agent.calculate_multi_material_waterfall({'Gas':{'annual_spend':25.,'unhedged_pct':.8}},.75,bases)
        with self.assertRaises(ValueError):self.agent.calculate_multi_material_waterfall({'Gas':{'annual_spend':-25.,'unhedged_pct':.8}},.75,bases)

if __name__ == '__main__':
    unittest.main()

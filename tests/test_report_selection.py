"""Synthetic selected-report periods and market-first retrieval regressions."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import unittest
import json

from pe_copilot.filings.financials import baseline_from_facts
from pe_copilot.filings.exposures import scan_materials


def fact(tag, value, start='2026-01-01', end='2026-06-30', instant=False):
    return {'tag':'us-gaap:'+tag,'value':str(value*1000000),'start':start,'end':end,
            'instant':instant,'dimensions':{},'context_id':'synthetic'}


class PeriodTests(unittest.TestCase):
    def test_quarterly_uses_ytd_consistently_without_annualizing(self):
        facts=[fact('Revenues',100),fact('Revenues',60,'2026-04-01'),
               fact('CostOfRevenue',70),fact('CostOfRevenue',40,'2026-04-01'),
               fact('NetCashProvidedByUsedInOperatingActivities',20),
               fact('PaymentsToAcquirePropertyPlantAndEquipment',5),
               fact('Assets',500,'2026-06-30',instant=True),
               fact('Revenues',999,'2025-01-01','2025-12-31')]
        b=baseline_from_facts(facts,'2026-06-30','quarter','10-Q')
        self.assertEqual(b['pools']['income_statement']['Revenue'],100)
        self.assertEqual(b['pools']['income_statement']['Gross Profit'],30)
        self.assertEqual(b['pools']['cash_flow']['Free Cash Flow'],15)
        self.assertEqual(b['pools']['balance_sheet']['Total Assets'],500)
        self.assertEqual(b['duration_start'],'2026-01-01')
        self.assertEqual({r['accession'] for r in b['evidence'].values()},{'quarter'})

    def test_missing_quarterly_context_does_not_fall_back_to_annual(self):
        b=baseline_from_facts([fact('Revenues',999,'2025-07-01')],'2026-06-30','quarter','10-Q')
        self.assertNotIn('Revenue',b['pools']['income_statement'])


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.report={'accessionNumber':'quarter','local_path':'q/filing.htm','form':'10-Q',
                     'reportDate':'2026-06-30','filingDate':'2026-07-31','sha256':'hash','url':'https://example.invalid/q'}
        (self.root/'q').mkdir();(self.root/'q/prepared.json').write_text('{}')
        self.store=Mock();self.store.load.return_value={'company':{'ticker':'TEST'},'filings':[self.report]}
        self.store.company_dir.return_value=self.root
        self.blocks=[SimpleNamespace(block_id='b1',section='Operations',text='Silver is consumed in our production process.'),
                     SimpleNamespace(block_id='b2',section='Operations',text='Helium is consumed in our production process.')]
        self.prepared=SimpleNamespace(content_sha256='hash',blocks=self.blocks)
        self.extractor=Mock();self.extractor.model='synthetic'

    def candidate(self,name):
        block=next(b for b in self.blocks if name in b.text)
        return {'materials':[{'material':name,'role':{'summary':'Production input',
            'citations':[{'block_id':block.block_id,'quote':block.text}]}}]}

    def response(self,candidate):
        return SimpleNamespace(choices=[SimpleNamespace(finish_reason='stop',message=SimpleNamespace(content=json.dumps(candidate)))])

    def scan(self):
        with patch('pe_copilot.filings.exposures.PreparedDocument.from_json',return_value=self.prepared):
            return scan_materials(self.store,'TEST',self.extractor,'quarter',['Silver','Gasoline'])

    def test_available_first_and_cache_no_extra_call(self):
        self.extractor.client.chat.completions.create.return_value=self.response(self.candidate('Silver'))
        rows=self.scan();self.scan()
        self.assertEqual([r['material'] for r in rows],['Silver'])
        self.assertTrue(rows[0]['market_covered'])
        self.assertEqual(self.extractor.client.chat.completions.create.call_count,1)
        self.assertIn('Gasoline',self.extractor.client.chat.completions.create.call_args.kwargs['messages'][0]['content'])

    def test_uncovered_fallback_only_after_empty_search(self):
        self.extractor.client.chat.completions.create.side_effect=[self.response({'materials':[]}),self.response(self.candidate('Helium'))]
        rows=self.scan()
        self.assertEqual(rows[0]['material'],'Helium');self.assertFalse(rows[0]['market_covered'])
        self.assertEqual(rows[0]['search_mode'],'uncovered fallback')
        self.assertEqual(self.extractor.client.chat.completions.create.call_count,2)

    def test_sparse_quarter_returns_empty_without_inventing_annual_exposures(self):
        self.extractor.client.chat.completions.create.return_value=self.response({'materials':[]})
        self.assertEqual(self.scan(),[])

    def test_foreign_accession_rejected(self):
        with self.assertRaises(ValueError):scan_materials(self.store,'TEST',self.extractor,'another-company',['Silver'])


class ReportUITests(unittest.TestCase):
    def test_report_change_resets_scope_and_proceed_selects_second_tab(self):
        from streamlit.testing.v1 import AppTest
        def page():
            import streamlit as st
            from types import SimpleNamespace
            from pe_copilot.ui.company_setup import render_report_selection
            from pe_copilot.ui.tabs import render_material_discovery_tab
            reports=[{'accessionNumber':'q','form':'10-Q','reportDate':'2026-06-30','filingDate':'2026-07-31'},
                     {'accessionNumber':'a','form':'10-K','reportDate':'2025-12-31','filingDate':'2026-02-01'}]
            def baseline(ticker, accession):
                r=next(r for r in reports if r['accessionNumber']==accession)
                return {'pools':{'income_statement':{'Revenue':100}},'evidence':{},'warnings':[],
                        'period_end':r['reportDate'],'accession':accession,'form':r['form'],
                        'snapshot_id':accession,'duration_basis':'Annual' if accession=='a' else 'Year to date',
                        'duration_start':'2025-01-01' if accession=='a' else '2026-01-01','currency':'USD'}
            def discover(ticker, accession=None):
                accession=accession or st.session_state['selected_report_accession']
                material='Silver' if accession=='a' else 'Helium'
                return [{'material':material,'search_mode':'synthetic','market_covered':material=='Silver',
                         'source_accession':accession,'raw_chunks':[]}],baseline(ticker,accession)['pools']
            service=SimpleNamespace(reports=lambda t:reports,report_baseline=baseline)
            app=SimpleNamespace(preparation_service=service,discover_target=discover)
            st.session_state['active_company']={'ticker':'TEST','cik':'0000000001'}
            if 'requested_tab' in st.session_state:
                st.session_state['workbench_tab']=st.session_state.pop('requested_tab')
            from pe_copilot.ui.navigation import SCENARIO_TAB
            tabs=st.tabs(['Tab 1',SCENARIO_TAB],key='workbench_tab',on_change='rerun')
            with tabs[0]:
                render_material_discovery_tab('TEST',['Silver','Gasoline'],discover,lambda:render_report_selection(app,'TEST'))
            with tabs[1]:
                st.write('Stress page')
        at=AppTest.from_function(page).run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.metric),12)
        next(b for b in at.button if b.label == 'Open scenario').click().run()
        self.assertFalse(at.exception)
        self.assertIn('Tab 2',at.session_state['workbench_tab'])
        at.session_state['sim_results']={'old':'annual'}
        at.selectbox(key='report_TEST').select('q').run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state['baseline_context']['accession'],'q')
        self.assertEqual(at.session_state['selected_metals'],[])
        self.assertNotIn('sim_results',at.session_state)
        self.assertEqual(at.session_state['graph_requested_snapshot'],'q')
        self.assertTrue(any('No futures coverage' in c.value for c in at.caption))

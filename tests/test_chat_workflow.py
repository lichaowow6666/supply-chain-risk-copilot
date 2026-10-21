"""Offline integration through real LangGraph checkpoints, graph and financial services."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from pe_copilot.chat.demo import build_demo, WALKTHROUGH
from pe_copilot.chat.language import LanguageService, validate_answer
from pe_copilot.chat.retrieval import HybridRetriever, graph_evidence
from pe_copilot.chat.workflow import ChatWorkflow
from pe_copilot.ui.chat import citation_focus


class ChatWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=TemporaryDirectory();cls.app,cls.scope=build_demo(cls.root.name)

    @classmethod
    def tearDownClass(cls):cls.root.cleanup()

    def setUp(self):
        self.temp=TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.retriever=HybridRetriever(self.app.fundamental_agent,cache_root=self.temp.name+'/vectors')
        self.chat=ChatWorkflow(self.app,self.retriever,LanguageService(False),self.temp.name+'/chat',True)
        self.addCleanup(self.chat.close)
        self.session='integration-session'

    def ask(self,q):return self.chat.ask(self.session,self.scope,q)

    def test_evidence_graph_and_citation_highlight(self):
        state=self.ask(WALKTHROUGH[1]);response=state['response']
        self.assertTrue(response['citations']);self.assertTrue(response['graph']['overlay']['nodes'])
        self.assertTrue(state['graph']['paths'])
        self.assertTrue(all(c['accession']==self.scope['accession'] for c in response['citations']))
        cited=next(c for c in response['citations'] if c.get('origin')=='synthetic')
        focused=citation_focus(response['graph'],cited)
        self.assertIn('copper-product',focused['overlay']['edges'])
        self.assertTrue(any('dense' in w.lower() for w in response['warnings']))

    def test_confirmation_precedes_calculation_and_numbers_reconcile(self):
        with patch.object(self.app,'invoke',wraps=self.app.invoke) as invoke:
            pending=self.ask(WALKTHROUGH[2]);invoke.assert_not_called()
            self.assertEqual(pending['pending'][0]['kind'],'scenario_confirmation')
            state=self.chat.resume(self.session,self.scope,{'confirm':True})
            self.assertEqual(invoke.call_count,1)
            result=state['last_scenario'];self.assertEqual(result['status'],'ok',result['warnings'])
            self.assertEqual(result['simulation']['waterfall_summary']['Net_COGS_Absorbed'],8)
            self.assertIn('USD -8.00',state['response']['text'])
            self.assertTrue(result['synthetic'])
            with self.assertRaises(ValueError):self.chat.resume(self.session,self.scope,{'confirm':True})
            self.assertEqual(invoke.call_count,1)
        again=self.ask(WALKTHROUGH[3])
        self.assertEqual(again['response']['scenario']['run_id'],result['run_id'])

    def test_confirmation_is_not_a_truthy_string(self):
        self.ask(WALKTHROUGH[2])
        with patch.object(self.app,'invoke') as invoke:
            state=self.chat.resume(self.session,self.scope,{'confirm':'yes'})
            invoke.assert_not_called();self.assertIn('cancelled',state['response']['text'])

    def test_clarification_does_not_invent_assumptions(self):
        pending=self.ask('Simulate Copper shock +20%')
        self.assertEqual(pending['pending'][0]['kind'],'clarification')
        self.assertIn('period_spend',pending['pending'][0]['missing'])
        pending=self.chat.resume(self.session,self.scope,'annual spend USD 100M; unhedged 80%; pass-through 50%; horizon 30')
        self.assertEqual(pending['pending'][0]['kind'],'scenario_confirmation')
        self.assertEqual(pending['proposal']['materials'][0]['period_spend'],100)

    def test_clarification_can_be_cancelled(self):
        self.ask('Simulate Copper shock +20%')
        with patch.object(self.retriever,'search') as search:
            state=self.chat.resume(self.session,self.scope,{'cancel':True})
            search.assert_not_called()
            self.assertFalse(state['pending']);self.assertIn('cancelled',state['response']['text'])

    def test_checkpoint_survives_new_workflow_instance(self):
        self.ask(WALKTHROUGH[2])
        other=ChatWorkflow(self.app,self.retriever,LanguageService(False),self.temp.name+'/chat',True)
        try:
            self.assertTrue(other.inspect(self.session,self.scope)['pending'])
            state=other.resume(self.session,self.scope,{'confirm':True})
            self.assertEqual(state['last_scenario']['simulation']['waterfall_summary']['Net_COGS_Absorbed'],8)
        finally:other.close()

    def test_changed_scope_cannot_resume_previous_confirmation_or_see_history(self):
        self.ask(WALKTHROUGH[2]);other={**self.scope,'accession':'different'}
        self.assertFalse(self.chat.inspect(self.session,other).get('history'))
        with self.assertRaises(ValueError):self.chat.resume(self.session,other,{'confirm':True})

    def test_unsupported_allocation_does_not_calculate(self):
        with patch.object(self.app,'invoke') as invoke:
            result=self.ask(WALKTHROUGH[4])
            invoke.assert_not_called();self.assertTrue(result['response']['evidence_gap'])
            self.assertNotIn('USD',result['response']['text'])

    def test_permission_case_stops_before_document_retrieval(self):
        with patch.object(self.retriever,'search') as search:
            result=self.ask(WALKTHROUGH[5]);search.assert_not_called()
            self.assertEqual(result['response']['permission'],'denied')
            self.assertTrue(result['response']['synthetic'])

    def test_provider_failure_is_redacted_and_retains_evidence(self):
        self.chat.language=Mock()
        self.chat.language.plan.side_effect=RuntimeError('SECRET_TEST_TOKEN')
        self.chat.language.answer.side_effect=RuntimeError('SECRET_TEST_TOKEN')
        result=self.ask('What is Copper exposure?')
        self.assertTrue(result['response']['citations'])
        self.assertNotIn('SECRET_TEST_TOKEN',json.dumps(result))

    def test_wrong_graph_accession_is_rejected(self):
        with self.assertRaises(ValueError):
            graph_evidence(self.app,{**self.scope,'accession':'other'},'Copper',[])

    def test_bad_language_citations_are_rejected(self):
        for result in ({'answer':'Fabrication [99]','citation_ids':[99]},
                       {'answer':'Unsupported fact','citation_ids':[]},
                       {'answer':'Fact without inline citation','citation_ids':[1]}):
            with self.assertRaises(ValueError):validate_answer(result,[{'citation_id':1}])

    def test_valid_inline_citation_is_not_lost_if_metadata_list_omits_it(self):
        self.assertEqual(validate_answer({'answer':'Fact [1]','citation_ids':[]},[{'citation_id':1}]),'Fact [1]')

    def test_negative_shock_signs_and_missing_market_failure(self):
        self.ask(WALKTHROUGH[2].replace('+20%','-20%'))
        result=self.chat.resume(self.session,self.scope,{'confirm':True})
        self.assertEqual(result['last_scenario']['simulation']['waterfall_summary']['Net_COGS_Absorbed'],-8)
        self.ask(WALKTHROUGH[2])
        with patch.object(self.app.market_agent,'capture_price_snapshot',side_effect=RuntimeError('SECRET')):
            result=self.chat.resume(self.session,self.scope,{'confirm':True})
        self.assertEqual(result['last_scenario']['status'],'failed')
        self.assertNotIn('SECRET',json.dumps(result))


class DenseScopeTests(unittest.TestCase):
    def test_dense_candidates_are_report_scoped_before_encoding(self):
        from pe_copilot.evaluation import load_suite,build_fixture_index,fixture_agent
        import numpy as np
        with TemporaryDirectory() as tmp:
            suite=load_suite();index,_=build_fixture_index(suite,tmp)
            encoder=Mock();encoder.identity='synthetic-test-encoder'
            encoder.encode.side_effect=lambda texts:np.array([[1.,0.] for _ in texts])
            retriever=HybridRetriever(fixture_agent(suite,index),encoder,tmp+'/vectors')
            result=retriever.search('铜的支出','SYNTA','synthetic-a-2026q2')
            self.assertTrue(all(r['accession']=='synthetic-a-2026q2' for r in result['hits']))
            encoded=' '.join(encoder.encode.call_args_list[0].args[0])
            self.assertIn('USD 30 million',encoded);self.assertNotIn('USD 100 million',encoded)
            self.assertNotIn('Synthetic Beta',encoded)

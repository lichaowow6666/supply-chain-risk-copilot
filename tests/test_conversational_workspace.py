"""Business conversations exercise the real controller with isolated model boundaries."""
from copy import deepcopy
from unittest import TestCase
from unittest.mock import ANY, Mock, patch

from pe_copilot.chat.workspace import WorkspaceChat, conversation_context
from test_workspace_chat import fixture


def plan(kind='general', section=None, query=None):
    return {'action': 'explain', 'company_query': None, 'section': section, 'question': '',
            'clarification': None, 'answer_kind': kind, 'retrieval_query': query}


class ConversationTests(TestCase):
    def setUp(self):
        self.app, self.language, prepared, _ = fixture()
        self.retriever = Mock()
        self.retriever.search.return_value = {'hits': [], 'backend': 'synthetic fixture', 'warnings': [], 'diagnostics': {}}
        self.chat = WorkspaceChat(self.app, self.language, self.retriever)
        self.state = {}

    def ask(self, question, decision, answer='Ratios relate financial figures to explain profitability, liquidity and leverage.'):
        self.language.json.return_value = {'answer': answer, 'citation_ids': [], 'evidence_gap': False}
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=decision) as route:
            reply = self.chat.respond(question, self.state)
        return reply, route

    def test_financial_education_without_company_calls_language_not_sec(self):
        reply, _ = self.ask('explain to me the financial ratios', plan())
        self.assertEqual(reply['status'], 'ok')
        self.assertIn('profitability', reply['text'])
        self.assertIsNone(reply['section'])
        self.app.prepare_company.assert_not_called()
        self.app.discover_target.assert_not_called()
        self.retriever.search.assert_not_called()
        self.language.json.assert_called_once()
        self.assertNotIn('requested_tab', self.state)

    def test_follow_up_gets_previous_question_and_answer(self):
        self.state['copilot_messages'] = [
            {'role': 'user', 'text': 'What is operating margin?'},
            {'role': 'assistant', 'text': 'Operating margin relates operating income to revenue.', 'scope': {}}]
        reply, route = self.ask('So is that good?', plan(), 'It depends on the business, comparable peers and the trend; higher is not enough by itself.')
        self.assertEqual(reply['status'], 'ok')
        self.assertIn('operating margin', str(route.call_args.args[1]['conversation']).lower())
        self.assertEqual(self.language.json.call_args.args[1]['conversation'], conversation_context(self.state))
        self.app.prepare_company.assert_not_called()

    def test_old_company_or_report_answers_are_not_evidence_for_new_scope(self):
        self.state['baseline_context'] = {'company_id': 'company:new', 'accession': 'quarter'}
        self.state['copilot_messages'] = [
            {'role': 'user', 'text': 'What is the margin?'},
            {'role': 'assistant', 'text': 'Old company had an unsupported 99% margin.', 'scope': {'company_id': 'company:old', 'accession': 'annual'}},
            {'role': 'user', 'text': 'What can we inspect here?'},
            {'role': 'assistant', 'text': 'The selected quarterly financial overview.', 'scope': self.state['baseline_context']}]
        context = conversation_context(self.state)
        self.assertNotIn('99%', str(context))
        self.assertIn('quarterly', str(context))

    def test_explanation_does_not_move_or_change_the_existing_canvas(self):
        self.state.update(active_company={'ticker': 'TEST', 'cik': '0000000001'},
                          baseline_context={'company_id': 'company:0000000001', 'accession': 'annual'},
                          workbench_tab='My current canvas', selected_metals=['Silver'], spend_Silver=25)
        before = deepcopy(self.state)
        reply, _ = self.ask('Help me understand this assumption', plan('workflow', 'assumptions'))
        self.assertEqual(reply['status'], 'ok')
        self.assertEqual(self.state, before)
        self.assertEqual(reply['section'], 'assumptions')

    def test_missing_company_evidence_still_allows_conceptual_help(self):
        reply, _ = self.ask('How does passing a cost increase to customers help?', plan(),
                            'Pass-through means recovering some input cost increases in selling prices; whether this is possible depends on contracts and demand.')
        self.assertIn('selling prices', reply['text'])
        self.assertNotIn('ticker', reply['text'])
        self.assertEqual(reply['citations'], [])

    def test_language_failure_is_not_a_company_lookup_failure(self):
        self.language.json.side_effect = RuntimeError('provider credentials should not be exposed')
        with patch('pe_copilot.chat.workspace.parse_workspace_intent', return_value=plan()):
            reply = self.chat.respond('What do ratios mean?', self.state)
        self.assertIn('language service', reply['text'])
        self.assertNotIn('credentials', reply['text'])
        self.assertNotIn('No SEC-listed company', reply['text'])
        self.app.prepare_company.assert_not_called()

    def test_graph_failure_preserves_hybrid_evidence_and_runtime_receipt(self):
        self.state.update(active_company={'ticker': 'TEST', 'cik': '0000000001'},
                          baseline_context={'company_id': 'company:0000000001',
                                            'accession': 'annual', 'snapshot_id': 'test-annual'})
        hit = {'ticker': 'TEST', 'accession': 'annual', 'document_id': 'synthetic-annual',
               'locator': 'block:1', 'text': 'Synthetic selected-report passage: silver is an operating input.'}
        diagnostics = {'status': 'ok', 'scope': {'ticker': 'TEST', 'accession': 'annual'},
                       'dense': {'status': 'ok', 'model': 'synthetic-encoder', 'coverage': 'full_report'},
                       'stages': [{'stage': 'dense', 'status': 'ok', 'duration_ms': 2.5}],
                       'returned_count': 1}
        self.retriever.search.return_value = {'hits': [hit], 'backend': 'synthetic BM25 + dense + reranking',
                                             'warnings': [], 'diagnostics': diagnostics}
        with patch('pe_copilot.chat.retrieval.graph_evidence',
                   side_effect=RuntimeError('SENSITIVE_GRAPH_EXCEPTION')) as graph:
            reply, _ = self.ask('Explain the silver supply chain.', plan('company', 'graph', 'silver operating use'),
                                'The selected report identifies silver as an operating input. [1]')

        self.assertEqual(reply['status'], 'ok')
        self.assertEqual(reply['citations'], [hit])
        self.assertIn('[1]', reply['text'])
        self.assertEqual(reply['runtime']['retrieval'], {
            **diagnostics, 'backend': 'synthetic BM25 + dense + reranking', 'warnings': []})
        self.assertEqual(reply['runtime']['graph'], {'status': 'unavailable', 'error_type': 'RuntimeError'})
        self.assertEqual(reply['runtime']['answer']['status'], 'generated')
        self.assertNotIn('SENSITIVE_GRAPH_EXCEPTION', str(reply))
        self.retriever.search.assert_called_once_with('silver operating use', 'TEST', 'annual', k=6, on_progress=ANY)
        graph.assert_called_once()
        self.language.json.assert_called_once()
        payload = self.language.json.call_args.args[1]
        self.assertEqual(payload['retrieval_coverage'], reply['runtime']['retrieval'])
        self.assertEqual(payload['graph_status'], reply['runtime']['graph'])
        self.assertEqual(payload['graph_paths'], [])
        self.assertEqual([row['text'] for row in payload['evidence']], [hit['text']])
        self.app.prepare_company.assert_not_called()

    def test_postfilter_excludes_other_company_and_report_before_language_call(self):
        self.state.update(active_company={'ticker': 'TEST', 'cik': '0000000001'},
                          baseline_context={'company_id': 'company:0000000001', 'accession': 'annual'})
        hits = [
            {'ticker': 'OTHER', 'accession': 'annual', 'locator': 'other-company',
             'text': 'WRONG_COMPANY_SOURCE must never reach answer generation.'},
            {'ticker': 'TEST', 'accession': 'old-quarter', 'locator': 'other-report',
             'text': 'WRONG_REPORT_SOURCE must never reach answer generation.'},
            {'ticker': 'TEST', 'accession': 'annual', 'locator': 'selected-report',
             'text': 'Synthetic selected-report revenue evidence.'},
        ]
        diagnostics = {'status': 'ok', 'scope': {'ticker': 'TEST', 'accession': 'annual'},
                       'dense': {'status': 'ok', 'model': 'synthetic-encoder'}, 'returned_count': 3}
        self.retriever.search.return_value = {'hits': hits, 'backend': 'synthetic hybrid search',
                                             'warnings': [], 'diagnostics': diagnostics}
        reply, _ = self.ask('Explain reported revenue.', plan('company', 'overview', 'annual revenue'),
                            'The selected annual report provides revenue evidence. [1]')

        self.assertEqual(reply['status'], 'ok')
        receipt = reply['runtime']['retrieval']
        self.assertEqual(receipt['status'], 'limited')
        self.assertEqual(receipt['rejected_hits'], 2)
        self.assertEqual(receipt['backend'], 'synthetic hybrid search')
        self.assertEqual(receipt['dense'], diagnostics['dense'])
        self.assertEqual(reply['citations'], [hits[2]])
        self.assertEqual(reply['runtime']['answer']['source_count'], 1)
        self.assertTrue(any('Excluded search results' in step for step in reply['steps']))
        self.language.json.assert_called_once()
        payload = self.language.json.call_args.args[1]
        self.assertEqual(payload['retrieval_coverage']['status'], 'limited')
        self.assertEqual(payload['retrieval_coverage']['rejected_hits'], 2)
        self.assertEqual([row['text'] for row in payload['evidence']], [hits[2]['text']])
        self.assertNotIn('WRONG_COMPANY_SOURCE', str(self.language.json.call_args))
        self.assertNotIn('WRONG_REPORT_SOURCE', str(self.language.json.call_args))
        self.app.prepare_company.assert_not_called()

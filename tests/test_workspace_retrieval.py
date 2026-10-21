"""Serving retrieval: bounded work, actual stages, isolation and graceful failures."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import numpy as np

from pe_copilot.chat.retrieval import CrossEncoderReranker, WorkspaceRetriever


def passage(number, ticker='SYNTH', accession='synthetic-annual'):
    return {'ticker': ticker, 'accession': accession, 'document_id': 'synthetic-document',
            'chunk_id': str(number), 'locator': f'block-{number}:0:150',
            'section': f'Section {number % 3}',
            'text': f'Synthetic passage {number}: ' + 'Fictional company evidence for the selected report. ' * 3}


class WorkspaceRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.rows = [passage(i) for i in range(30)]
        self.fundamental = Mock()
        self.fundamental.query_report.return_value = self.rows[:6]
        self.fundamental.preparation_service = SimpleNamespace(index=Mock())
        self.fundamental.preparation_service.index.report_chunks.return_value = self.rows
        self.encoder = Mock()
        self.encoder.identity = 'synthetic-test-encoder:pinned'
        self.encoder.encode.side_effect = lambda texts: np.array([[1., 0.] for _ in texts])
        self.reranker = Mock()
        self.reranker.identity = 'synthetic-test-cross-encoder:pinned'
        self.reranker.supports.return_value = True
        self.reranker.score.side_effect = lambda question, texts: np.arange(len(texts), dtype=float)
        self.retriever = WorkspaceRetriever(self.fundamental, self.encoder, self.reranker,
            cache_root=self.directory.name, candidate_limit=8, rerank_limit=6)

    def search(self, **kwargs):
        return self.retriever.search('Explain input exposure', 'synth', 'synthetic-annual', **kwargs)

    def test_bounded_semantic_pool_and_real_reranking_stages_are_reported(self):
        progress = []
        result = self.search(k=3, on_progress=progress.append)
        self.assertEqual(result['diagnostics']['report_chunk_count'], 30)
        self.assertEqual(result['diagnostics']['dense']['candidate_count'], 8)
        self.assertEqual(result['diagnostics']['dense']['coverage'], 'candidate_pool')
        self.assertEqual(result['diagnostics']['reranker']['status'], 'ok')
        self.assertEqual(result['diagnostics']['reranker']['candidate_count'], 6)
        self.assertEqual([r['rerank_score'] for r in result['hits']], [5., 4., 3.])
        self.assertEqual(len(self.encoder.encode.call_args_list[0].args[0]), 8)
        self.assertEqual(len(self.reranker.score.call_args.args[1]), 6)
        self.assertTrue(all(stage['duration_ms'] >= 0 for stage in result['diagnostics']['stages']))
        self.assertIn('8 of 30', result['warnings'][0])
        self.assertGreaterEqual(len(progress), 3)

    def test_repeat_query_reuses_embeddings_but_scores_current_question(self):
        self.search()
        self.encoder.encode.reset_mock()
        result = self.search()
        self.encoder.encode.assert_called_once_with(['Explain input exposure'])
        self.assertEqual(result['diagnostics']['dense']['cached_chunks'], 8)
        self.assertEqual(result['diagnostics']['dense']['encoded_chunks'], 0)

    def test_small_report_semantic_search_is_complete(self):
        self.fundamental.preparation_service.index.report_chunks.return_value = self.rows[:6]
        result = self.search()
        self.assertEqual(result['diagnostics']['dense']['coverage'], 'full_report')
        self.assertEqual(result['diagnostics']['status'], 'ok')
        self.assertFalse(result['warnings'])

    def test_mixed_scope_rejected_before_model_processing(self):
        self.fundamental.query_report.return_value = [passage(99, ticker='OTHER')]
        with self.assertRaisesRegex(ValueError, 'scope violation'):
            self.search()
        self.encoder.encode.assert_not_called()
        self.reranker.score.assert_not_called()
        self.fundamental.query_report.return_value = self.rows[:6]
        self.fundamental.preparation_service.index.report_chunks.return_value = [passage(99, accession='wrong-quarter')]
        with self.assertRaisesRegex(ValueError, 'scope violation'):
            self.search()
        self.encoder.encode.assert_not_called()

    def test_models_fail_without_fabricating_hybrid_or_reranked_success(self):
        self.encoder.encode.side_effect = RuntimeError('SECRET_ENDPOINT')
        self.reranker.score.side_effect = RuntimeError('SECRET_ENDPOINT')
        result = self.search()
        self.assertEqual(result['backend'], 'BM25')
        self.assertEqual(result['hits'], self.rows[:5])
        self.assertEqual(result['diagnostics']['dense']['status'], 'unavailable')
        self.assertEqual(result['diagnostics']['reranker']['status'], 'unavailable')
        self.assertNotIn('SECRET_ENDPOINT', str(result))

    def test_invalid_reranker_output_retains_hybrid_ranking(self):
        for scores in ([float('nan')] * 6, [1.]):
            with self.subTest(scores=scores):
                self.reranker.score.side_effect = None
                self.reranker.score.return_value = np.asarray(scores)
                result = self.search()
                self.assertEqual(result['diagnostics']['reranker']['status'], 'unavailable')
                self.assertTrue(all('rerank_score' not in row for row in result['hits']))
                self.assertIn('RRF', result['backend'])

    def test_non_latin_query_retains_multilingual_embeddings_without_english_rerank(self):
        self.reranker.supports.return_value = False
        result = self.retriever.search('原料价格风险', 'SYNTH', 'synthetic-annual')
        self.reranker.score.assert_not_called()
        self.assertEqual(result['diagnostics']['dense']['status'], 'ok')
        self.assertEqual(result['diagnostics']['reranker']['status'], 'skipped_language')
        self.assertIn('English-trained', ' '.join(result['warnings']))

    def test_missing_or_corrupt_model_config_never_downloads_on_a_turn(self):
        reranker = CrossEncoderReranker(Path(self.directory.name) / 'missing.json')
        with self.assertRaises(FileNotFoundError):
            reranker.score('What is disclosed?', ['Synthetic evidence'])
        self.assertTrue(reranker.supports('What is disclosed?'))
        self.assertFalse(reranker.supports('原料价格风险'))


if __name__ == '__main__':
    unittest.main()

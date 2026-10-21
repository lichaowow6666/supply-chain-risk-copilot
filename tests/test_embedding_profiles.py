"""Synthetic checks for opt-in serving comparison; no native model loading."""
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from pe_copilot.chat.model_registry import ModelRegistry, compare_saved_question
from pe_copilot.chat.retrieval import BGEEncoder, CrossEncoderReranker, make_workspace_retriever


class EmbeddingProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.registry = ModelRegistry(self.root)
        self.registry.base_config.parent.mkdir(parents=True)
        self.registry.run_root.mkdir(parents=True)
        model = self.root / 'synthetic-model'
        model.mkdir()
        self.base = {'model_id': 'synthetic-bge', 'revision': 'pinned-revision', 'path': str(model)}
        self.registry.base_config.write_text(json.dumps(self.base))
        self.weights = self.registry.run_root / 'synthetic-weights.pt'
        self.weights.write_bytes(b'synthetic-checkpoint')
        self.tuned = {**self.base, 'weights_path': str(self.weights),
                      'weights_sha256': sha256(self.weights.read_bytes()).hexdigest()}
        self.encoder_config = self.registry.run_root / 'encoder_config.json'
        self.encoder_config.write_text(json.dumps(self.tuned))
        (self.registry.run_root / 'configuration.json').write_text(json.dumps({
            'model_id': self.base['model_id'], 'base_revision': self.base['revision'],
            'max_length': 128, 'steps': 24, 'seed': 17,
            'selection': 'fixed checkpoint; no audit-driven selection'}))
        (self.registry.run_root / 'dataset_snapshot.json').write_text(json.dumps({
            'version': 'synthetic-test-v1', 'records': []}))

    def test_profiles_are_validated_experimental_and_default_is_unchanged(self):
        summaries = self.registry.profiles()
        self.assertEqual([item['status'] for item in summaries], ['available', 'available'])
        self.assertTrue(summaries[0]['default'])
        self.assertFalse(summaries[1]['default'])
        self.assertFalse(summaries[1]['production_promoted'])
        self.assertEqual(summaries[1]['original_training_max_length'], 128)
        self.assertEqual(self.registry.encoder('tuned').max_length, 512)
        self.assertNotIn(str(self.root), json.dumps(summaries))
        with patch('pe_copilot.chat.model_registry.ModelRegistry') as registry:
            retriever = make_workspace_retriever(Mock())
        registry.assert_not_called()
        self.assertEqual(str(retriever.encoder.config_path), 'artifacts/chat_models/base_model.json')

    def test_wrong_base_identity_or_modified_checkpoint_cannot_be_loaded(self):
        for mutation in ({'revision': 'wrong-revision'}, {'model_id': 'different-model'},
                         {'path': str(self.root / 'different-base')}, {'weights_sha256': '0' * 64}):
            with self.subTest(mutation=mutation):
                self.encoder_config.write_text(json.dumps({**self.tuned, **mutation}))
                with self.assertRaises(ValueError):
                    self.registry.encoder('tuned')
                self.assertEqual(self.registry.profiles()[1]['status'], 'unavailable')
        self.encoder_config.write_text(json.dumps(self.tuned))
        self.weights.write_bytes(b'modified-synthetic-checkpoint')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.registry.encoder('tuned')

    def test_comparison_runs_actual_scoped_serving_pipeline_at_equal_token_limits(self):
        rows = [{'ticker': 'SYNTH', 'accession': 'synthetic-quarter', 'document_id': 'synthetic-doc',
                 'chunk_id': str(i), 'locator': f'block-{i}:0:150', 'section': 'Synthetic',
                 'text': f'Synthetic exposure passage {i}. ' * 8} for i in range(3)]
        fundamental = Mock()
        fundamental.query_report.return_value = rows
        fundamental.preparation_service = SimpleNamespace(index=Mock())
        fundamental.preparation_service.index.report_chunks.return_value = rows
        calls = []

        def encode(encoder, texts):
            calls.append((encoder.identity, encoder.max_length, list(texts)))
            return np.asarray([[1., 0.] for _ in texts])

        with patch.object(BGEEncoder, 'encode', new=encode), \
                patch.object(CrossEncoderReranker, 'score', side_effect=lambda q, texts: np.arange(len(texts))), \
                patch.object(CrossEncoderReranker, 'identity', 'synthetic-pinned-reranker'):
            result = compare_saved_question(fundamental, 'Explain synthetic exposure',
                                             'synth', 'synthetic-quarter', k=2, registry=self.registry)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['original_training_max_length'], 128)
        self.assertEqual(result['serving_max_length'], 512)
        self.assertEqual(result['overlap_at_k'], 1.)
        self.assertEqual([run['profile'] for run in result['runs']], ['base', 'tuned'])
        self.assertEqual(len({identity for identity, _, _ in calls}), 2)
        self.assertEqual({length for _, length, _ in calls}, {512})
        for run in result['runs']:
            self.assertEqual(run['diagnostics']['scope'], result['scope'])
            self.assertEqual(run['diagnostics']['dense']['status'], 'ok')
            self.assertEqual(run['diagnostics']['reranker']['status'], 'ok')
            self.assertEqual(len(run['hits']), 2)
            self.assertGreaterEqual(run['elapsed_ms'], 0)
            self.assertTrue(all(row['accession'] == 'synthetic-quarter' for row in run['hits']))
        for call in fundamental.query_report.call_args_list:
            self.assertEqual(call.kwargs['accession'], 'synthetic-quarter')
        self.assertNotIn(str(self.root), json.dumps(result))


if __name__ == '__main__':
    unittest.main()

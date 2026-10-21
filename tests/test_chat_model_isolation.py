"""Keep native model failures outside the UI, preserving honest retrieval fallback."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

import numpy as np

from pe_copilot.chat import model_worker
from pe_copilot.chat.retrieval import BGEEncoder, CrossEncoderReranker
from pe_copilot.native_runtime import NativeWorkerError


class ChatModelIsolationTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Path(self.directory.name) / 'model.json'
        self.config.write_text(json.dumps({'model_id': 'synthetic-test-model',
            'revision': 'pinned-test-revision', 'path': '/synthetic-local-model', 'backend': 'onnx'}))
        model_worker._ENCODERS.clear()
        model_worker._RERANKERS.clear()

    @patch('pe_copilot.chat.retrieval.get_worker')
    def test_embedding_and_reranker_use_distinct_process_boundaries(self, get_worker):
        encoder, reranker = BGEEncoder(self.config), CrossEncoderReranker(self.config)
        worker = get_worker.return_value
        worker.call.return_value = [[.1, .2], [.3, .4]]
        with patch.object(encoder, '_encode_local', side_effect=AssertionError('in-process inference')):
            vectors = encoder.encode(['first', 'second'])
        get_worker.assert_called_with('chat-embedding', 'pe_copilot.chat.model_worker:encode')
        self.assertEqual(worker.call.call_args.args[0]['config'], str(self.config.resolve()))
        self.assertEqual(vectors.shape, (2, 2))
        self.assertIsNone(encoder.model)
        worker.call.return_value = [.9, .2]
        with patch.object(reranker, '_score_local', side_effect=AssertionError('in-process inference')):
            scores = reranker.score('question', ['first', 'second'])
        get_worker.assert_called_with('chat-reranker', 'pe_copilot.chat.model_worker:score')
        self.assertEqual(scores.tolist(), [.9, .2])
        self.assertIsNone(reranker.model)

    @patch('pe_copilot.chat.retrieval.get_worker')
    def test_worker_termination_is_a_catchable_failure(self, get_worker):
        get_worker.return_value.call.side_effect = NativeWorkerError('worker exited')
        for model, args in ((BGEEncoder(self.config), (['text'],)),
                            (CrossEncoderReranker(self.config), ('question', ['text']))):
            with self.subTest(model=type(model).__name__):
                operation = model.encode if isinstance(model, BGEEncoder) else model.score
                with self.assertRaises(NativeWorkerError):
                    operation(*args)
                self.assertIsNone(model.model)

    @patch('pe_copilot.chat.retrieval.get_worker')
    def test_invalid_worker_numbers_are_rejected(self, get_worker):
        for values in ([float('nan')], [[1., 2.]], [1., 2.]):
            get_worker.return_value.call.return_value = values
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValueError, 'Invalid reranker'):
                    CrossEncoderReranker(self.config).score('question', ['text'])
        get_worker.return_value.call.return_value = [[float('inf')]]
        with self.assertRaisesRegex(ValueError, 'Invalid embedding'):
            BGEEncoder(self.config).encode(['text'])

    def test_worker_caches_models_and_calls_local_implementation(self):
        payload = {'config': str(self.config), 'max_length': 512, 'texts': ['text'], 'question': 'why'}
        with patch.object(model_worker, 'BGEEncoder') as constructor:
            constructor.return_value._encode_local.return_value = np.array([[1., 0.]])
            self.assertEqual(model_worker.encode(payload), [[1., 0.]])
            self.assertEqual(model_worker.encode(payload), [[1., 0.]])
            constructor.assert_called_once()
            constructor.return_value.encode.assert_not_called()
        with patch.object(model_worker, 'CrossEncoderReranker') as constructor:
            constructor.return_value._score_local.return_value = np.array([.5])
            self.assertEqual(model_worker.score(payload), [.5])
            self.assertEqual(model_worker.score(payload), [.5])
            constructor.assert_called_once()
            constructor.return_value.score.assert_not_called()

    def test_profile_switch_retains_only_one_embedding_model(self):
        base = {'config': 'synthetic-base.json', 'max_length': 512, 'texts': ['text']}
        tuned = {**base, 'config': 'synthetic-tuned.json'}
        with patch.object(model_worker, 'BGEEncoder') as constructor:
            constructor.side_effect = lambda **kwargs: Mock(
                _encode_local=Mock(return_value=np.array([[1., 0.]])))
            for payload in (base, base, tuned, tuned, base):
                self.assertEqual(model_worker.encode(payload), [[1., 0.]])
                self.assertEqual(list(model_worker._ENCODERS), [(payload['config'], 512)])
            self.assertEqual(constructor.call_count, 3)


if __name__ == '__main__':
    unittest.main()

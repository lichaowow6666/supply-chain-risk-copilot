"""Exercise real worker lifetime/failure without crashing the UI or macOS."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from pe_copilot.native_runtime import NativeWorker, NativeWorkerError


FIXTURE = '''
import os
import time
def handle(payload):
    if payload.get('exit'):
        os._exit(17)
    if payload.get('sleep'):
        time.sleep(payload['sleep'])
    if payload.get('error'):
        raise ValueError('private-secret-should-not-leak')
    print('library output should not corrupt transport')
    return {'pid': os.getpid(), 'value': payload.get('value'),
            'threads': os.getenv('OMP_NUM_THREADS'),
            'duplicate_override': os.getenv('KMP_DUPLICATE_LIB_OK')}
'''


class NativeRuntimeTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        Path(folder, 'native_test_fixture.py').write_text(FIXTURE)
        self.enterContext(patch.dict(os.environ, {'PYTHONPATH': folder,
            'OMP_NUM_THREADS': '12', 'KMP_DUPLICATE_LIB_OK': 'TRUE'}))
        self.worker = NativeWorker('test-model', 'native_test_fixture:handle')
        self.addCleanup(self.worker.close)

    def test_persistent_child_serializes_requests_and_isolates_environment(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            outputs = list(pool.map(lambda n: self.worker.call({'value': n}), range(6)))
        self.assertEqual([row['value'] for row in outputs], list(range(6)))
        self.assertEqual(len({row['pid'] for row in outputs}), 1)
        self.assertNotEqual(outputs[0]['pid'], os.getpid())
        self.assertEqual(outputs[0]['threads'], '1')
        self.assertIsNone(outputs[0]['duplicate_override'])

    def test_abnormal_exit_preserves_parent_and_next_call_restarts_worker(self):
        before = self.worker.call({})['pid']
        with self.assertRaisesRegex(NativeWorkerError, 'stopped unexpectedly'):
            self.worker.call({'exit': True})
        self.assertNotEqual(self.worker.call({})['pid'], before)

    def test_timeout_terminates_worker_and_allows_recovery(self):
        self.worker.call({})
        with self.assertRaisesRegex(NativeWorkerError, 'timed out'):
            self.worker.call({'sleep': 10}, timeout=.1)
        self.assertEqual(self.worker.call({'value': 'recovered'})['value'], 'recovered')

    def test_model_error_is_redacted_and_does_not_poison_worker(self):
        before = self.worker.call({})['pid']
        with self.assertRaises(NativeWorkerError) as caught:
            self.worker.call({'error': True})
        self.assertNotIn('private-secret', str(caught.exception))
        self.assertEqual(self.worker.call({})['pid'], before)


if __name__ == '__main__':
    unittest.main()

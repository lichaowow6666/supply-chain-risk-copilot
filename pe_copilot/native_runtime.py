"""Keep incompatible native model libraries outside the Streamlit process.

Each model family owns a fresh interpreter (never a fork of loaded runtimes).
Requests are serialized and bounded; a worker exit becomes a normal exception.
"""
import atexit
import json
import os
from pathlib import Path
from queue import Empty, Queue
import subprocess
import sys
from threading import RLock, Thread


class NativeWorkerError(RuntimeError):
    """A model worker failed without terminating the application."""


class NativeWorker:
    def __init__(self, name, handler):
        self.name, self.handler = name, handler
        self._process = None
        self._lock = RLock()

    def _start(self):
        env = os.environ.copy()
        for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                    'VECLIB_MAXIMUM_THREADS', 'NUMEXPR_NUM_THREADS'):
            env[key] = '1'
        env['TOKENIZERS_PARALLELISM'] = 'false'
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        # Suppressing duplicate-library checks is unsafe, especially on macOS.
        env.pop('KMP_DUPLICATE_LIB_OK', None)
        self._process = subprocess.Popen(
            [sys.executable, '-u', '-m', 'pe_copilot.native_worker', self.handler],
            cwd=Path(__file__).resolve().parents[1], env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding='utf-8', bufsize=1,
        )

    def call(self, payload, timeout=120):
        request = json.dumps(payload, allow_nan=False, ensure_ascii=False) + '\n'
        with self._lock:
            if self._process is None or self._process.poll() is not None:
                self.close()
                self._start()
            process = self._process
            result = Queue(maxsize=1)

            def exchange():
                try:
                    process.stdin.write(request)
                    process.stdin.flush()
                    result.put(process.stdout.readline())
                except (OSError, ValueError):
                    result.put(None)

            thread = Thread(target=exchange, name=f'{self.name}-request', daemon=True)
            thread.start()
            try:
                line = result.get(timeout=timeout)
            except Empty:
                self.close()
                raise NativeWorkerError(f'{self.name} timed out; the application remains available.') from None
            if not line:
                self.close()
                raise NativeWorkerError(f'{self.name} stopped unexpectedly; the application remains available.')
            try:
                response = json.loads(line)
                if not isinstance(response, dict) or 'ok' not in response:
                    raise ValueError('Invalid response envelope')
            except (ValueError, TypeError):
                self.close()
                raise NativeWorkerError(f'{self.name} returned an invalid response.') from None
            if not response['ok']:
                # Never include provider exception bodies, paths or credentials.
                raise NativeWorkerError(f'{self.name} could not complete the model request.')
            return response['result']

    def close(self):
        with self._lock:
            process, self._process = self._process, None
            if process is None:
                return
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            for pipe in (process.stdin, process.stdout):
                if pipe:
                    pipe.close()


_WORKERS = {}
_REGISTRY_LOCK = RLock()


def get_worker(name, handler):
    with _REGISTRY_LOCK:
        key = (name, handler)
        if key not in _WORKERS:
            _WORKERS[key] = NativeWorker(name, handler)
        return _WORKERS[key]


def close_workers():
    with _REGISTRY_LOCK:
        for worker in _WORKERS.values():
            worker.close()
        _WORKERS.clear()


atexit.register(close_workers)

"""Validated, local embedding profiles and an opt-in serving comparison.

The frozen synthetic experiment is evidence about that experiment only. Loading
its checkpoint here does not promote it or train on the user's question.
"""
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter


ROOT = Path(__file__).resolve().parents[2]
SERVING_MAX_LENGTH = 512


class ModelRegistry:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.base_config = self.root / 'artifacts/chat_models/base_model.json'
        self.run_root = self.root / 'artifacts/evaluation/embedding_run_v1'

    def _path(self, value):
        path = Path(value)
        return path if path.is_absolute() else self.root / path

    def _validated(self, profile):
        if profile not in ('base', 'tuned'):
            raise ValueError('Unknown embedding profile')
        base = json.loads(self.base_config.read_text())
        if not all(base.get(key) for key in ('model_id', 'revision', 'path')):
            raise ValueError('Incomplete pinned base model configuration')
        if not self._path(base['path']).is_dir():
            raise FileNotFoundError('Pinned base model is not installed locally')
        if base.get('weights_path'):
            raise ValueError('Base profile must use the unchanged base checkpoint')
        if profile == 'base':
            return self.base_config, base, None, None
        config_path = self.run_root / 'encoder_config.json'
        tuned = json.loads(config_path.read_text())
        training = json.loads((self.run_root / 'configuration.json').read_text())
        dataset = json.loads((self.run_root / 'dataset_snapshot.json').read_text())
        if (tuned.get('model_id') != base['model_id'] or tuned.get('revision') != base['revision']
                or training.get('model_id') != base['model_id']
                or training.get('base_revision') != base['revision']
                or self._path(tuned.get('path', '')).resolve() != self._path(base['path']).resolve()):
            raise ValueError('Fine-tuned checkpoint does not match the pinned base model')
        checksum = tuned.get('weights_sha256', '')
        if len(checksum) != 64 or not tuned.get('weights_path'):
            raise ValueError('Fine-tuned checkpoint checksum is missing')
        if sha256(self._path(tuned['weights_path']).read_bytes()).hexdigest() != checksum:
            raise ValueError('Fine-tuned checkpoint checksum mismatch')
        if not isinstance(training.get('max_length'), int) or training['max_length'] < 1:
            raise ValueError('Original training token limit is missing')
        return config_path, tuned, training, dataset

    def encoder(self, profile='base'):
        from pe_copilot.chat.retrieval import BGEEncoder
        config_path, _, _, _ = self._validated(profile)
        return BGEEncoder(config=config_path, max_length=SERVING_MAX_LENGTH)

    def describe(self, profile):
        """Public provenance deliberately excludes model/checkpoint file paths."""
        _, config, training, dataset = self._validated(profile)
        result = {
            'profile': profile,
            'label': 'Base BGE-M3' if profile == 'base' else 'Fine-tuned BGE-M3 · experimental',
            'status': 'available', 'default': profile == 'base',
            'model_id': config['model_id'], 'base_revision': config['revision'],
            'weights_sha256': config.get('weights_sha256', 'base'),
            'serving_max_length': SERVING_MAX_LENGTH,
            'production_promoted': profile == 'base',
        }
        if training is not None:
            result.update({
                'dataset_version': dataset.get('version'),
                'dataset_snapshot_sha256': sha256((self.run_root / 'dataset_snapshot.json').read_bytes()).hexdigest(),
                'training_configuration_sha256': sha256((self.run_root / 'configuration.json').read_bytes()).hexdigest(),
                'original_training_max_length': training['max_length'],
                'training_steps': training.get('steps'), 'loss': training.get('loss'),
                'trainable': training.get('trainable'), 'seed': training.get('seed'),
                'training_data': 'Synthetic company-disjoint training examples',
                'selection': training.get('selection'),
                'limitation': 'Experimental checkpoint; the small synthetic audit did not establish a production improvement.',
            })
        return result

    def profiles(self):
        result = []
        for profile in ('base', 'tuned'):
            try:
                result.append(self.describe(profile))
            except Exception as error:
                result.append({'profile': profile, 'status': 'unavailable',
                               'error_type': type(error).__name__, 'default': profile == 'base'})
        return result


def compare_saved_question(fundamental, question, ticker, accession, k=5,
                          registry=None, on_progress=None):
    """Run the real serving retriever twice without altering workspace state.

    Both profiles use the same 512-token serving budget, candidate selection and
    reranker. A dedicated cache namespace prevents a pre-existing base-only full
    report index from giving the base profile a larger candidate set. This is a
    live ranking comparison, not a new held-out quality score or training run.
    """
    from pe_copilot.chat.retrieval import CrossEncoderReranker, WorkspaceRetriever, evidence_key
    registry = registry or ModelRegistry()
    if not isinstance(question, str) or not question.strip() or len(question) > 8000:
        raise ValueError('Select an evidence question of 1–8000 characters.')
    if not isinstance(ticker, str) or not ticker.strip() or not isinstance(accession, str) or not accession.strip():
        raise ValueError('Select a company and report before comparing retrieval.')
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 20:
        raise ValueError('Requested evidence count must be 1–20.')
    ticker, accession = ticker.strip().upper(), accession.strip()
    result = {
        'status': 'completed', 'question': question,
        'scope': {'ticker': ticker, 'accession': accession},
        'serving_max_length': SERVING_MAX_LENGTH,
        'candidate_limit': 40, 'rerank_limit': 16, 'k': k, 'runs': [],
        'limitations': [
            'This compares evidence rankings for one question; rank changes are not evidence of improved answer quality.',
            'Both profiles use 512 tokens in serving. The frozen synthetic training/audit used 128 tokens; its scores are not directly transferable.',
            'Sequential local timings include each profile’s cache/loading work and are not a latency benchmark.',
            'The base profile remains the application default. No training or audit-driven checkpoint selection is performed.',
        ],
    }
    cache_root = registry.root / 'artifacts/chat_vectors/paired_comparison_v1'
    reranker = CrossEncoderReranker(config=registry.root / 'artifacts/chat_models/reranker_model.json')
    for profile in ('base', 'tuned'):
        started = perf_counter()
        try:
            model = registry.describe(profile)
            if on_progress:
                on_progress(f"Comparing evidence with {model['label']}…")
            retriever = WorkspaceRetriever(fundamental, registry.encoder(profile), reranker,
                                           cache_root=cache_root, candidate_limit=40, rerank_limit=16)
            response = retriever.search(question, ticker, accession, k=k, on_progress=on_progress)
            dense_ok = response['diagnostics'].get('dense', {}).get('status') == 'ok'
            reranker_ok = response['diagnostics'].get('reranker', {}).get('status') in ('ok', 'skipped_language')
            run = {'profile': profile, 'model': model, **response,
                   'status': 'completed' if dense_ok and reranker_ok else 'limited',
                   'elapsed_ms': round((perf_counter() - started) * 1000, 2)}
            if not dense_ok or not reranker_ok:
                result['status'] = 'limited'
        except Exception as error:
            result['status'] = 'limited'
            run = {'profile': profile, 'status': 'unavailable', 'error_type': type(error).__name__,
                   'hits': [], 'elapsed_ms': round((perf_counter() - started) * 1000, 2)}
        result['runs'].append(run)
    base, tuned = result['runs']
    a, b = [set(evidence_key(row) for row in run['hits']) for run in (base, tuned)]
    result['overlap_at_k'] = len(a & b) / min(len(a), len(b)) if a and b else None
    result['original_training_max_length'] = tuned.get('model', {}).get('original_training_max_length')
    return result

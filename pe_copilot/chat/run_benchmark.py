"""Run the authored 100-question suite through the real copilot, then RAGAS.

Explicit offline execution makes configured provider calls using synthetic data.
Answers are generated sequentially; judges run in bounded concurrent batches.
Every attempt is saved. Resume fills missing work without selecting better runs.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4

from .benchmark_workspace import BenchmarkWorkspace
from .diagnostics import build_diagnostics
from .language import LanguageService
from .model_registry import ModelRegistry
from .quality import build_evaluation_packet
from .retrieval import WorkspaceRetriever, CrossEncoderReranker
from .workspace import WorkspaceChat
from pe_copilot.filings.sec import atomic_json
from pe_copilot.native_runtime import close_workers, get_worker


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / 'evaluations/results/interview_100_latest.json'


def _now():
    return datetime.now(timezone.utc).isoformat()


def _public(value):
    """Remove machine-local paths/opaque checkpoint identifiers from synthetic exports."""
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items()
                if key not in ('artifact_path', 'checkpoint_id', 'local_path', 'weights_path', 'path')}
    if isinstance(value, (tuple, list)):
        return [_public(item) for item in value]
    return value


def _save(report, output):
    from .benchmark_metrics import summarize
    report['updated_at'] = _now()
    report['summary'] = summarize(report['results'], case_count=report['case_count'])
    atomic_json(output, _public(report))


def run(*, output=DEFAULT_OUTPUT, resume=False, phase='all', limit=None, batch_size=4):
    from .benchmark_dataset import load_benchmark, DEFAULT_BENCHMARK
    from .benchmark_metrics import assess_case, snapshot_workspace, synthetic_permission_check
    suite = load_benchmark()
    cases = suite['cases'][:limit] if limit else suite['cases']
    digest = sha256(DEFAULT_BENCHMARK.read_bytes()).hexdigest()
    output = Path(output)
    if resume and output.is_file():
        report = json.loads(output.read_text())
        if report['suite_sha256'] != digest or report['case_count'] != len(cases):
            raise ValueError('Resume requires the identical dataset and case count.')
        if not report.get('synthetic'):
            raise ValueError('Only an explicitly synthetic benchmark can be resumed.')
    else:
        if phase == 'judge':
            raise ValueError('Judge phase requires --resume and an existing answer run.')
        report = {'schema_version': 1, 'suite_id': suite['suite_id'], 'suite_sha256': digest,
            'run_id': uuid4().hex, 'synthetic': True, 'status': 'running', 'started_at': _now(),
            'finished_at': None, 'case_count': len(cases), 'results': [],
            'protocol': {'dataset_split': 'authored synthetic development; not independently human-reviewed',
                'serving_path': 'WorkspaceChat → LangGraph → existing services → configured LLM',
                'answer_execution': 'Sequential; shared model/vector caches; first use includes warm-up',
                'judging': 'RAGAS five perspectives; four answers/batch, at most eight concurrent metric tasks',
                'reference_visibility': 'Expected answers supplied only to evaluators, never to the answering model',
                'scenario_setup': 'Real deterministic orchestrator on fictional prices and filings; excluded from answer time',
                'forecast_setup': 'Real 5-session no-change/AR benchmarks; XGBoost/LSTM training not measured',
                'repeat_policy': 'One answer attempt per case; no best-of selection; resume only missing phases'},
            'versions': {name: version(name) for name in ('langgraph', 'ragas', 'sentence-transformers', 'streamlit')},
            'limitations': [
                '100 authored synthetic cases do not establish production accuracy, representative user adoption or SEC download reliability.',
                'References are independently authored relative to generated answers but have not had independent human review.',
                'Fixture preparation replaces external SEC acquisition/extraction; actual parser/index/retriever/graph/scenario services execute.',
                'The same configured model may generate and judge answers; judge false positives/negatives remain visible.',
                'A scripted task outcome is a contract-completion proxy, not real user acceptance or business impact.',
                'Governance checks measure specific observed boundaries; no enterprise authentication or RBAC deployment is certified.',
                'Latency is from one local sequential run with shared caches, not a controlled load test or an SLA.',
                'Metric N/A and judge errors are excluded from score means and explicitly counted.']}
    run_root = ROOT / 'artifacts/evaluation/interview_100' / report['run_id']
    run_root.mkdir(parents=True, exist_ok=True)
    report['synthetic_permission_check'] = synthetic_permission_check()
    _save(report, output)
    by_id = {row['case_id']: row for row in report['results']}
    if phase in ('all', 'answers'):
        workspace = BenchmarkWorkspace(run_root / 'fixture')
        registry = ModelRegistry()
        report['embedding_profile'] = _public(registry.describe('base'))
        retriever = WorkspaceRetriever(workspace.app.fundamental_agent, registry.encoder('base'),
            CrossEncoderReranker(), cache_root=run_root / 'vectors')
        for position, case in enumerate(cases, 1):
            if case['id'] in by_id:
                continue
            started = monotonic()
            row = {'case_id': case['id'], 'category': case['category'], 'language': case['language'],
                'question': case['question'], 'reference': case['reference'], 'expected': case['expected'],
                'tags': case.get('tags', []), 'reference_basis': case.get('reference_basis', []),
                'setup': case.get('setup', {}), 'synthetic': True, 'attempt': 1,
                'quality': {'status': 'pending', 'scores': {}, 'metrics': []}}
            try:
                state = workspace.state_for(case)
                setup_seconds = monotonic() - started
                before = snapshot_workspace(state)
                chat = WorkspaceChat(workspace.app, LanguageService(), retriever,
                    session_id=report['run_id'] + ':' + case['id'], checkpoint_path=run_root / 'checkpoints.sqlite')
                reply = chat.respond(case['question'], state)
                after = snapshot_workspace(state)
                assessment = assess_case(case, reply, before, after)
                packet = build_evaluation_packet(case['question'], reply)
                atomic_json(run_root / 'packets' / (case['id'] + '.json'), packet)
                row.update(status='answered' if reply.get('status') == 'ok' else 'answer_error',
                    response=reply.get('text', ''), scope=reply.get('scope', {}), reply=_public(reply),
                    route=reply.get('runtime', {}).get('route', {}), diagnostics=build_diagnostics(reply),
                    assessment=assessment, **assessment, setup_seconds=round(setup_seconds, 3))
            except Exception as error:
                row.update(status='execution_error', response='', error_type=type(error).__name__,
                    quality={'status': 'unavailable', 'scores': {}, 'metrics': [],
                             'reason': 'Answer execution did not finish.'},
                    diagnostics={'total_ms': None}, assessment={'checks': [], 'governance': {'checks': []},
                    'capabilities': {}, 'task_outcome': {'status': 'failed', 'kind': 'scripted_contract_proxy',
                        'failed_checks': ['answer_execution']}})
            row['attempt_seconds'] = round(monotonic() - started, 3)
            report['results'].append(row)
            by_id[case['id']] = row
            report['setup_timings'] = workspace.setup_timings
            _save(report, output)
            print(f"answer {position}/{len(cases)} {case['id']} {row['status']} {row.get('diagnostics', {}).get('total_ms')}ms", flush=True)
        # Avoid contention between answering models and judge embeddings. This
        # affects only workers owned by this offline runner, not Streamlit.
        close_workers()
    if phase in ('all', 'judge'):
        pending = [row for row in report['results'] if row.get('quality', {}).get('status') == 'pending']
        for offset in range(0, len(pending), batch_size):
            batch = pending[offset:offset + batch_size]
            items = [{'case_id': row['case_id'], 'reference': row['reference'],
                      'packet': json.loads((run_root / 'packets' / (row['case_id'] + '.json')).read_text())} for row in batch]
            started = monotonic()
            try:
                judged = get_worker('benchmark-judge', 'pe_copilot.chat.benchmark_judge:score_batch').call(
                    {'items': items}, timeout=240)
                outcomes = {row['case_id']: row['quality'] for row in judged}
                for row in batch:
                    row['quality'] = outcomes[row['case_id']]
            except Exception as error:
                for row in batch:
                    row['quality'] = {'status': 'unavailable', 'scores': {}, 'metrics': [],
                        'reason': 'Judge batch failed: ' + type(error).__name__,
                        'evaluation_seconds': round(monotonic() - started, 3)}
            _save(report, output)
            print(f"judge {min(offset+len(batch),len(pending))}/{len(pending)} " +
                  ', '.join(row['case_id'] + ':' + row['quality']['status'] for row in batch), flush=True)
        close_workers()
    report['status'] = ('completed' if len(report['results']) == len(cases) and all(
        row.get('quality', {}).get('status') != 'pending' for row in report['results']) else 'running')
    if report['status'] == 'completed':
        report['finished_at'] = _now()
    _save(report, output)
    return report


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--phase', choices=('all', 'answers', 'judge'), default='all')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--batch-size', type=int, choices=(1, 2, 3, 4), default=4)
    args = parser.parse_args()
    result = run(output=args.output, resume=args.resume, phase=args.phase, limit=args.limit, batch_size=args.batch_size)
    print(json.dumps({'status': result['status'], 'cases': len(result['results']),
                      'run_id': result['run_id']}, ensure_ascii=False), flush=True)

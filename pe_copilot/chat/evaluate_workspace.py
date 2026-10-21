"""Small synthetic audit of today's serving path, not the legacy demo workflow.

Explicit execution only: this command calls the configured language provider
using fictional fixtures and runs real BGE-M3/reranking plus RAGAS. It never
changes the default serving model or retrains/selects a checkpoint.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from pe_copilot.chat.demo import build_demo
from pe_copilot.chat.language import LanguageService
from pe_copilot.chat.model_registry import ModelRegistry
from pe_copilot.chat.quality import FULL_CONTEXT_V2, build_evaluation_packet, evaluate_response
from pe_copilot.chat.retrieval import CrossEncoderReranker, WorkspaceRetriever
from pe_copilot.chat.workspace import WorkspaceChat
from pe_copilot.evaluation import DEFAULT_SUITE, load_suite
from pe_copilot.filings.sec import atomic_json
from pe_copilot.workspace_state import activate_prepared_company


def publish_summary(record, output='artifacts/evaluation/workspace_quality_latest.json'):
    """Publish only synthetic benchmark metadata; source packets stay separate."""
    models = {row['profile']: row['model'] for row in record['results']}
    public = {key: record[key] for key in ('schema_version', 'created_at', 'status', 'synthetic',
        'suite_id', 'suite_sha256', 'evaluation_split', 'path', 'summary', 'limitations')}
    public.update(metric_label='Evidence support (RAGAS)', model_profiles=models,
        context_scope=record['context_scope'], context_version=record['context_version'],
        sample_size_per_profile=3,
        ragas_versions=sorted({row['quality']['ragas_version'] for row in record['results']
                               if row['quality'].get('ragas_version')}),
        checks=[{'profile': row['profile'], 'case_id': row['case_id'], **row['checks']}
                for row in record['results']])
    atomic_json(output, public)
    return public


def run(output='artifacts/evaluation/workspace_quality_full_context_v2.json', profiles=('base', 'tuned'),
        with_reference=False, timeout_seconds=90):
    suite, registry = load_suite(), ModelRegistry()
    # Revenue, quarterly exposure, and an unavailable platinum-spend answer.
    cases = [case for case in suite['cases'] if case['id'] in ('R01', 'R06', 'R13')]
    record = {'schema_version': 2, 'created_at': datetime.now(timezone.utc).isoformat(),
        'context_scope': FULL_CONTEXT_V2, 'context_version': 2,
        'status': 'running', 'synthetic': True, 'suite_id': suite['suite_id'],
        'suite_sha256': sha256(DEFAULT_SUITE.read_bytes()).hexdigest(),
        'evaluation_split': 'development; authored synthetic reference answers',
        'path': 'WorkspaceChat.respond → LangGraph → WorkspaceRetriever → answer → RAGAS',
        'results': [], 'limitations': [
            'Three development questions, not a held-out production quality estimate.',
            'Faithfulness checks the full answer against source texts and the exact supplied factual workspace records. General conceptual advice may still be outside the context; prompts and conversation history are excluded.',
            'The expanded v2 judge context is not directly comparable with the retained v1 source-only scores.',
            'Both profiles use the actual 512-token serving budget and the same reranker.',
            'Generation and model judgments can vary; scores do not promote a checkpoint.',
            'Elapsed times include local model/cache warm-up and are not a controlled latency benchmark.']}
    atomic_json(output, record)
    with TemporaryDirectory(prefix='copilot-current-quality-') as directory:
        root = Path(directory)
        app, _ = build_demo(root)
        service = app.preparation_service
        for profile in profiles:
            retriever = WorkspaceRetriever(app.fundamental_agent, registry.encoder(profile),
                CrossEncoderReranker(), cache_root=root / 'vectors')
            for case in cases:
                report = next(report for report in service.suite['reports'] if report['id'] == case['scope'])
                baseline = service.report_baseline(report['ticker'], report['accession'])
                prepared = service.load_preparation(report['ticker'])
                materials, pools = service.scan(report['ticker'], report['accession'])
                state = {'copilot_messages': []}
                activate_prepared_company(state, prepared, baseline, materials, pools)
                chat = WorkspaceChat(app, LanguageService(), retriever, session_id=uuid4().hex,
                                     checkpoint_path=root / 'checkpoints.sqlite')
                reply = chat.respond(case['user_input'], state)
                packet = build_evaluation_packet(case['user_input'], reply)
                quality = evaluate_response(packet, reference=case['reference'] if with_reference else None,
                                            timeout_seconds=timeout_seconds)
                retrieval = reply.get('runtime', {}).get('retrieval', {})
                workflow = reply.get('runtime', {}).get('workflow', {})
                checks = {'generated_answer': reply.get('runtime', {}).get('answer', {}).get('status') == 'generated',
                    'real_langgraph': workflow.get('engine') == 'LangGraph' and workflow.get('status') == 'completed',
                    'embedding_applied': retrieval.get('dense', {}).get('status') == 'ok',
                    'reranker_applied': retrieval.get('reranker', {}).get('status') == 'ok',
                    'citations_scoped': packet['citation_validation']['status'] == 'passed',
                    'question_scoped': reply.get('scope', {}).get('accession') == report['accession']}
                checks['full_context_captured'] = packet['context_scope'] == FULL_CONTEXT_V2 and bool(packet['grounding_context'])
                row = {'case_id': case['id'], 'profile': profile, 'model': registry.describe(profile),
                    'question': case['user_input'], 'reference': case['reference'],
                    'packet': packet, 'quality': quality, 'workflow': workflow,
                    'route': reply.get('runtime', {}).get('route'), 'checks': checks}
                row['status'] = 'completed' if all(checks.values()) and quality['status'] == 'completed' else 'partial'
                record['results'].append(row)
                atomic_json(output, record)
                print(profile, case['id'], row['status'], quality['scores'], checks, flush=True)
    record['status'] = 'completed' if all(row['status'] == 'completed' for row in record['results']) else 'partial'
    record['summary'] = [{'profile': row['profile'], 'case_id': row['case_id'],
        'status': row['status'], 'response_seconds': row['packet']['elapsed_seconds'],
        **row['quality']['scores']} for row in record['results']]
    atomic_json(output, record)
    publish_summary(record)
    return record


if __name__ == '__main__':
    from dotenv import load_dotenv
    load_dotenv('.env')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='artifacts/evaluation/workspace_quality_full_context_v2.json')
    parser.add_argument('--profiles', nargs='+', choices=('base', 'tuned'), default=['base', 'tuned'])
    parser.add_argument('--with-reference', action='store_true', help='Also judge correctness against authored reference answers.')
    parser.add_argument('--timeout', type=int, default=90)
    args = parser.parse_args()
    result = run(args.output, tuple(args.profiles), args.with_reference, args.timeout)
    print(json.dumps({'status': result['status'], 'summary': result['summary']}, indent=2))
    raise SystemExit(0 if result['status'] == 'completed' else 1)

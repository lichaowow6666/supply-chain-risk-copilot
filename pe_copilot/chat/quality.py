"""On-demand RAGAS checks of the answer the workspace actually displayed.

No metric runs during normal chat. Packets are immutable evidence snapshots;
the judge lives in a separate process so evaluation cannot load native model
libraries into Streamlit. Results are local, ignored artifacts, not telemetry.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re
from time import monotonic

from pe_copilot.native_runtime import get_worker


LOCAL_QUALITY_ROOT = Path(__file__).resolve().parents[2] / 'artifacts' / 'chat_quality'
SOURCE_CONTEXT_V1 = 'source_texts_only'
FULL_CONTEXT_V2 = 'sources_and_structured_context'
FACTUAL_CONTEXT_KINDS = {'financial_context', 'scenario', 'forecasts',
    'qualitative_assessments_not_reported_facts', 'graph_paths', 'workspace'}


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             allow_nan=False).encode()).hexdigest()


def build_evaluation_packet(question, reply):
    """Freeze exact answer and its supplied context, never re-retrieve evidence.

    WorkspaceChat's citations contain the numbered source evidence passed to
    the generator. Inline IDs identify the cited subset; both are retained.
    The generator's supplied factual workspace context is preserved separately
    from filing citations. It supplements the judge's context but never becomes
    a filing citation. Conversation history and prompts are not judge evidence.
    """
    sources = []
    for position, source in enumerate(reply.get('citations', []), 1):
        sources.append({'id': position, **{key: source.get(key) for key in (
            'text', 'kind', 'ticker', 'accession', 'document_type', 'period_end',
            'document_id', 'chunk_id', 'locator', 'section', 'source_url')}})
    runtime = reply.get('runtime', {})
    grounding = [{'kind': row['kind'], 'text': row['text']}
                 for row in runtime.get('grounding_context', [])
                 if isinstance(row, dict) and row.get('kind') in FACTUAL_CONTEXT_KINDS
                 and isinstance(row.get('text'), str) and row['text'].strip()]
    context_scope = FULL_CONTEXT_V2 if 'grounding_context' in runtime else SOURCE_CONTEXT_V1
    scope = {key: reply.get('scope', {}).get(key) for key in (
        'company_id', 'ticker', 'accession', 'snapshot_id', 'form', 'period_end')}
    response = str(reply.get('text') or '')
    inline = sorted({int(value) for value in re.findall(r'\[(\d+)\]', response)})
    invalid = [value for value in inline if not 1 <= value <= len(sources)]
    wrong_scope = [row['id'] for row in sources if (
        row['accession'] and scope['accession'] and row['accession'] != scope['accession']) or (
        row['ticker'] and scope['ticker'] and row['ticker'] != scope['ticker'])]
    missing_text = [row['id'] for row in sources if not isinstance(row['text'], str) or not row['text'].strip()]
    language_calls = [{key: call.get(key) for key in ('provider', 'model', 'status', 'duration_ms')}
                      for call in runtime.get('language_calls', []) if isinstance(call, dict)]
    retrieval = runtime.get('retrieval', {})
    packet = {'schema_version': 2, 'question': str(question), 'response': response,
        'grounding_context': grounding, 'context_scope': context_scope,
        'context_version': 2 if context_scope == FULL_CONTEXT_V2 else 1,
        'scope': scope, 'sources': sources, 'answer_kind': reply.get('answer_kind'),
        'answer_status': runtime.get('answer', {}).get('status'),
        'reply_status': reply.get('status'), 'elapsed_seconds': reply.get('elapsed_seconds'),
        'language_calls': language_calls,
        'retrieval': {key: deepcopy(retrieval.get(key)) for key in ('status', 'backend', 'dense', 'reranker')},
        'citation_validation': {'status': 'failed' if invalid or wrong_scope or missing_text else 'passed',
            'inline_ids': inline, 'invalid_ids': invalid, 'scope_violation_ids': wrong_scope,
            'missing_text_ids': missing_text, 'source_count': len(sources),
            'meaning': 'Checks citation identifiers and selected-report scope, not semantic support.'}}
    packet['packet_id'] = _digest(packet)
    return deepcopy(packet)


def _verified_packet(packet):
    value = deepcopy(packet)
    identity = value.pop('packet_id', None)
    if value.get('schema_version') not in (1, 2) or identity != _digest(value):
        raise ValueError('The answer evidence snapshot changed; evaluate the original response again.')
    return value


def evaluate_response(packet, reference=None, timeout_seconds=90):
    """Explicitly judge a frozen answer; no mutations to company/scenario state.

    Faithfulness measures support by the exact source and factual context
    supplied to this answer's generator. An optional
    authored reference enables factual correctness; it is never generated from
    this answer. General/workflow answers have no filing-grounded score.
    """
    frozen = _verified_packet(packet)
    if not 5 <= timeout_seconds <= 300:
        raise ValueError('Evaluation timeout must be 5–300 seconds.')
    grounding = frozen.get('grounding_context', [])
    context_scope = frozen.get('context_scope', SOURCE_CONTEXT_V1)
    context_description = ('Numbered source texts plus the exact supplied financial, scenario, forecast, '
        'qualitative assessment, graph and workspace records. Assumptions remain assumptions; these records '
        'are not independent filing evidence. Prompts and conversation history are excluded.'
        if context_scope == FULL_CONTEXT_V2 else
        'Legacy source-only snapshot: additional supplied workspace context and qualitative assessments '
        'are unavailable to this metric; their absence can lower support scores.')
    report = {'schema_version': 2, 'packet_id': packet['packet_id'],
        'context_scope': context_scope, 'context_description': context_description,
        'context_version': 2 if context_scope == FULL_CONTEXT_V2 else 1,
        'source_context_count': len(frozen['sources']), 'structured_context_count': len(grounding),
        'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'not_scored',
        'scores': {}, 'errors': {}, 'citation_validation': frozen['citation_validation'],
        'limitations': ['Model judgment of this answer only; not proof of financial accuracy or retrieval completeness.',
            context_description,
            'RAGAS checks every answer claim against the frozen context. General conceptual advice may still be outside that context.',
            'Citation validation is a separate deterministic check, not a RAGAS score.',
            'Response relevance and human acceptance are not measured by this check.']}
    if frozen['citation_validation']['status'] != 'passed':
        report['reason'] = 'The answer contains invalid citation identifiers or evidence outside the selected report.'
    elif frozen['answer_status'] != 'generated' or frozen['reply_status'] != 'ok':
        report['reason'] = 'No successful generated evidence answer is available to judge.'
    elif frozen['answer_kind'] in ('general', 'workflow'):
        report['reason'] = 'A general or workflow explanation is not a filing-grounded answer.'
    elif not frozen['sources'] and not grounding:
        report['reason'] = 'No source context was supplied; a faithfulness score would be misleading.'
    else:
        started = monotonic()
        try:
            judged = get_worker('answer-quality', 'pe_copilot.chat.quality:judge_packet').call(
                {'question': frozen['question'], 'response': frozen['response'],
                 'retrieved_contexts': [source['text'] for source in frozen['sources']]
                     + [row['kind'] + ': ' + row['text'] for row in grounding],
                 'reference': reference, 'timeout_seconds': timeout_seconds - 2},
                timeout=timeout_seconds)
            report.update(judged)
            generators = {call.get('model') for call in frozen['language_calls'] if call.get('status') == 'completed'}
            if report.get('judge_model') in generators:
                report['limitations'].append('The answer generator and judge use the same model; correlated bias is possible.')
        except Exception as error:
            report.update(status='unavailable', errors={'evaluation': type(error).__name__},
                          reason='The quality check could not finish. The answer and current workspace are unchanged.')
        report['evaluation_seconds'] = round(monotonic() - started, 2)
    # Do not let a local disk error discard a successfully calculated quality check.
    try:
        from pe_copilot.filings.sec import atomic_json
        LOCAL_QUALITY_ROOT.mkdir(parents=True, exist_ok=True)
        atomic_json(LOCAL_QUALITY_ROOT / (packet['packet_id'] + '.json'), {'packet': packet, 'evaluation': report})
    except OSError:
        report['persistence_status'] = 'unavailable'
    return report


def judge_packet(payload):
    """Private worker entry point; never import RAGAS in the UI interpreter."""
    return asyncio.run(_judge_packet(payload))


async def _judge_packet(payload):
    os.environ['RAGAS_DO_NOT_TRACK'] = 'true'
    from importlib.metadata import version
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import Faithfulness, FactualCorrectness

    result = {'status': 'unavailable', 'scores': {}, 'errors': {},
              'ragas_version': version('ragas'), 'temperature': 0}
    if os.getenv('DEEPSEEK_API_KEY'):
        client = AsyncOpenAI(api_key=os.environ['DEEPSEEK_API_KEY'],
                             base_url='https://api.deepseek.com', timeout=35, max_retries=0)
        model, provider = 'deepseek-chat', 'DeepSeek'
    elif os.getenv('OPENAI_API_KEY'):
        client = AsyncOpenAI(timeout=35, max_retries=0)
        model, provider = os.getenv('COPILOT_CHAT_MODEL', 'gpt-4o-mini'), 'OpenAI'
    else:
        return {**result, 'reason': 'No language-model credentials are configured for the quality judge.'}
    result.update(judge_model=model, judge_provider=provider)

    class AuditedFaithfulness(Faithfulness):
        async def _create_verdicts(self, statements, context):
            verdicts = await super()._create_verdicts(statements, context)
            result['claim_checks'] = [item.model_dump() for item in verdicts.statements]
            return verdicts

    try:
        llm = llm_factory(model, client=client, temperature=0, max_tokens=3000, max_retries=1)
        tasks = {'faithfulness': (AuditedFaithfulness(llm=llm), {
            'user_input': payload['question'], 'response': payload['response'],
            'retrieved_contexts': payload['retrieved_contexts']})}
        if isinstance(payload.get('reference'), str) and payload['reference'].strip():
            tasks['factual_correctness'] = (FactualCorrectness(llm=llm), {
                'response': payload['response'], 'reference': payload['reference']})

        async def score(name, metric, arguments):
            try:
                scored = await asyncio.wait_for(metric.ascore(**arguments), timeout=payload['timeout_seconds'])
                value = float(scored.value)
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError('Metric did not produce a finite score in range.')
                result['scores'][name] = value
            except Exception as error:
                result['errors'][name] = type(error).__name__

        await asyncio.gather(*(score(name, *task) for name, task in tasks.items()))
        result['status'] = 'completed' if len(result['scores']) == len(tasks) else 'partial' if result['scores'] else 'unavailable'
    finally:
        await client.close()
    return result

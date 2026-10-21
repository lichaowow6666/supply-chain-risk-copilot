"""Explicit, bounded RAGAS interview checks beside the unchanged grounding check.

The saved answer and its evidence are immutable. Each metric has its own worker
deadline, so a failed dependency cannot erase scores already calculated. No
provider/model imports happen in the Streamlit process and no scenario is rerun.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
from time import monotonic

from pe_copilot.chat.quality import _verified_packet
from pe_copilot.native_runtime import get_worker


ROOT = Path(__file__).resolve().parents[2]
LOCAL_INTERVIEW_ROOT = ROOT / 'artifacts/chat_quality/interview'
EVALUATION_VERSION = 'interview-ragas-v1'
MAX_PRECISION_CONTEXTS = 8
MAX_CONTEXT_CHARACTERS = 120000
MAX_REFERENCE_CHARACTERS = 12000
METRICS = (
    ('faithfulness', 'Grounding / faithfulness'),
    ('answer_relevancy', 'Answer relevance'),
    ('context_precision_without_reference', 'Context precision · no reference'),
    ('context_recall', 'Context recall · reviewer reference'),
    ('factual_correctness', 'Factual correctness · reviewer reference'),
)


def _judge_configuration():
    if os.getenv('DEEPSEEK_API_KEY'):
        return 'DeepSeek', 'deepseek-chat'
    if os.getenv('OPENAI_API_KEY'):
        return 'OpenAI', os.getenv('COPILOT_CHAT_MODEL', 'gpt-4o-mini')
    return None, None


def _cache_identity(packet_id, reference, provider, model, ragas_version):
    config = ROOT / 'artifacts/chat_models/base_model.json'
    try:
        embedding = sha256(config.read_bytes()).hexdigest()
    except OSError:
        embedding = 'unavailable'
    return sha256(json.dumps({'packet_id': packet_id, 'reference': reference,
        'evaluation': EVALUATION_VERSION, 'provider': provider, 'model': model,
        'provider_configuration_sha256': sha256(os.getenv('OPENAI_BASE_URL', 'default').encode()).hexdigest(),
        'ragas_version': ragas_version, 'embedding_config_sha256': embedding},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _plan(frozen, reference):
    sources = [row['text'] for row in frozen['sources'] if isinstance(row.get('text'), str) and row['text'].strip()]
    contexts = sources + [row['kind'] + ': ' + row['text'] for row in frozen.get('grounding_context', [])]
    generated = frozen.get('answer_status') == 'generated' and frozen.get('reply_status') == 'ok'
    general = frozen.get('answer_kind') in ('general', 'workflow')
    valid = frozen['citation_validation']['status'] == 'passed'
    oversized = sum(map(len, contexts)) > MAX_CONTEXT_CHARACTERS
    rows = []
    for key, label in METRICS:
        reason = None
        if not generated or not frozen.get('question') or not frozen.get('response'):
            reason = 'A successful generated answer is required; navigation, clarification and error messages are not scored.'
        elif key in ('context_recall', 'factual_correctness') and not reference:
            reason = 'An independent reviewer reference is required. No reference is generated from the answer.'
        elif key in ('faithfulness', 'context_precision_without_reference', 'context_recall'):
            if general:
                reason = 'This is a general/workflow explanation, not a retrieved-evidence answer.'
            elif not valid:
                reason = 'Source identifiers or report scope failed validation; a context score would be misleading.'
            elif not (sources if key == 'context_precision_without_reference' else contexts):
                reason = 'No applicable source context was supplied.'
            elif oversized:
                reason = 'The context exceeds the bounded evaluation budget; evidence is not silently truncated.'
        row = {'key': key, 'label': label, 'status': 'not_applicable' if reason else 'pending',
               'score': None, 'reason': reason or '', 'duration_ms': None}
        if key == 'context_precision_without_reference' and not reason:
            row['reason'] = (f'Evaluates the first {min(len(sources), MAX_PRECISION_CONTEXTS)} of {len(sources)} supplied '
                'source blocks in their saved order. This order includes curated/calculated sources and is not necessarily retrieval rank.')
        elif key == 'faithfulness' and not reason:
            row['reason'] = ('Supported answer claims divided by judged claims. Context includes saved calculations, '
                'assumptions and qualitative assessments when supplied; support does not make them independently reported facts.')
        elif key == 'answer_relevancy' and not reason:
            row['reason'] = ('RAGAS generates three candidate questions, compares their base BGE-M3 embeddings with the '
                'original question and averages cosine similarity; all-noncommittal generations yield zero. Range: −1 to 1.')
        elif key == 'context_recall' and not reason:
            row['reason'] = 'Fraction of reviewer-reference statements attributable to the full supplied context, including labeled assumptions and calculations.'
        elif key == 'factual_correctness' and not reason:
            row['reason'] = 'RAGAS claim-level F1 against the reviewer reference; beta 1, low atomicity and low coverage. Reference completeness matters.'
        rows.append(row)
    return rows, contexts, sources


def evaluate_interview(packet, reference=None, timeout_seconds=180):
    """Run only on explicit request; a matching completed local result is reused.

    ``reference`` is optional reviewer-authored truth, never inferred/generated.
    The optional reference remains local except when sent to the chosen judge.
    """
    started = monotonic()
    frozen = _verified_packet(packet)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 10 <= timeout_seconds <= 300:
        raise ValueError('Evaluation timeout must be 10–300 seconds.')
    if reference is not None and not isinstance(reference, str):
        raise ValueError('The reviewer reference must be text.')
    reference = reference.strip() if reference else None
    if reference and len(reference) > MAX_REFERENCE_CHARACTERS:
        raise ValueError('The reviewer reference exceeds 12,000 characters.')
    provider, model = _judge_configuration()
    try:
        ragas_version = version('ragas')
    except PackageNotFoundError:
        ragas_version = None
    identity = _cache_identity(packet['packet_id'], reference, provider, model, ragas_version)
    path = LOCAL_INTERVIEW_ROOT / (identity + '.json')
    if path.is_file():
        try:
            cached = json.loads(path.read_text())
            if (cached.get('cache_id') == identity and cached['result']['status'] in ('completed', 'not_applicable')
                    and cached['packet'] == packet and cached.get('reference') == reference):
                return {**cached['result'], 'cache_hit': True}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    rows, contexts, sources = _plan(frozen, reference)
    report = {'schema_version': 1, 'evaluation_version': EVALUATION_VERSION,
        'packet_id': packet['packet_id'], 'evaluation_id': identity,
        'created_at': datetime.now(timezone.utc).isoformat(), 'status': 'running',
        'scores': {}, 'metrics': rows, 'claim_checks': [], 'judge_model': model,
        'judge_provider': provider, 'ragas_version': ragas_version,
        'context_scope': frozen.get('context_scope', 'source_texts_only'),
        'citation_validation': deepcopy(frozen['citation_validation']),
        'reference_supplied': bool(reference), 'cache_hit': False,
        'limitations': [
            'These are separate model-judged diagnostics, not one overall quality or financial-accuracy score.',
            'Faithfulness checks the frozen sources and supplied factual records; it cannot prove assumptions true or independently verify arithmetic.',
            'Answer relevance uses RAGAS-generated questions and base BGE-M3 cosine similarity; it does not establish factual correctness. Appropriate evidence-gap refusals may score poorly.',
            'No-reference context precision is answer-conditioned and evaluates at most eight supplied source blocks in saved order; it does not measure whole-report retrieval quality.',
            'Reference-based recall/correctness are only as reliable and complete as the independently supplied reviewer reference.',
            'General advice can fall outside the supplied context. Judge errors and correlated generator/judge bias remain possible.',
            'Metric durations include judge/dependency work; local model warm-up can affect relevance latency.']}
    deadline = started + timeout_seconds
    for row in rows:
        if row['status'] != 'pending':
            continue
        remaining = deadline - monotonic()
        if remaining < 5:
            row.update(status='unavailable', reason='The overall evaluation deadline was reached.', duration_ms=0)
            continue
        phase = monotonic()
        budget = min(65, remaining)
        try:
            outcome = get_worker('interview-quality', 'pe_copilot.chat.interview_quality:score_metric').call({
                'key': row['key'], 'question': frozen['question'], 'response': frozen['response'],
                'contexts': contexts, 'precision_contexts': sources[:MAX_PRECISION_CONTEXTS],
                'reference': reference, 'timeout_seconds': max(1, budget - 2)}, timeout=budget)
            measured = outcome['metric']
            row.update(status=measured['status'], score=measured.get('score'))
            if measured.get('reason'):
                row['reason'] = measured['reason']
            if row['status'] == 'completed':
                score = float(row['score'])
                lower = -1 if row['key'] == 'answer_relevancy' else 0
                if not math.isfinite(score) or not lower <= score <= 1:
                    raise ValueError('Invalid metric result')
                report['scores'][row['key']] = score
            for field in ('judge_model', 'judge_provider', 'ragas_version', 'relevancy_embedding'):
                if outcome.get(field) is not None:
                    report[field] = outcome[field]
            if row['key'] == 'faithfulness':
                report['claim_checks'] = outcome.get('claim_checks', [])
        except Exception as error:
            row.update(status='unavailable', score=None,
                       reason=f'This metric could not complete ({type(error).__name__}); other results are retained.')
        row['duration_ms'] = round((monotonic() - phase) * 1000)
    applicable = [row for row in rows if row['status'] != 'not_applicable']
    report['status'] = ('not_applicable' if not applicable else 'completed' if all(
        row['status'] == 'completed' for row in applicable) else 'partial' if report['scores'] else 'unavailable')
    report['evaluation_seconds'] = round(monotonic() - started, 2)
    try:
        from pe_copilot.filings.sec import atomic_json
        LOCAL_INTERVIEW_ROOT.mkdir(parents=True, exist_ok=True)
        report['artifact_path'] = str(path)
        atomic_json(path, {'cache_id': identity, 'packet': packet, 'reference': reference, 'result': report})
    except OSError:
        report.pop('artifact_path', None)
        report['persistence_status'] = 'unavailable'
    return report


def _bge_embeddings():
    """Modern RAGAS embedding adapter; native loading stays inside this worker."""
    from ragas.embeddings.base import BaseRagasEmbedding
    from pe_copilot.chat.model_registry import ModelRegistry

    class LocalBGE(BaseRagasEmbedding):
        def __init__(self):
            super().__init__()
            self.encoder = ModelRegistry().encoder('base')

        def embed_text(self, text, **kwargs):
            return self.encoder._encode_local([text])[0].tolist()

        def embed_texts(self, texts, **kwargs):
            return self.encoder._encode_local(list(texts)).tolist()

        async def aembed_text(self, text, **kwargs):
            return await asyncio.to_thread(self.embed_text, text)

        async def aembed_texts(self, texts, **kwargs):
            return await asyncio.to_thread(self.embed_texts, texts)

    return LocalBGE()


def score_metric(payload):
    """Private worker entry; exactly one actual RAGAS metric per request."""
    return asyncio.run(_score_metric(payload))


async def _score_metric(payload, embeddings=None):
    os.environ['RAGAS_DO_NOT_TRACK'] = 'true'
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import (AnswerRelevancy, ContextPrecisionWithoutReference,
        ContextRecall, FactualCorrectness, Faithfulness)

    key = payload['key']
    provider, model = _judge_configuration()
    outcome = {'metric': {'status': 'unavailable', 'score': None, 'reason': ''},
               'judge_provider': provider, 'judge_model': model, 'ragas_version': version('ragas')}
    if provider is None:
        outcome['metric']['reason'] = 'No language-model credentials are configured for the evaluation judge.'
        return outcome
    client = (AsyncOpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url='https://api.deepseek.com',
                         timeout=25, max_retries=0) if provider == 'DeepSeek'
              else AsyncOpenAI(timeout=25, max_retries=0))

    class AuditedFaithfulness(Faithfulness):
        async def _create_verdicts(self, statements, context):
            verdicts = await super()._create_verdicts(statements, context)
            outcome['claim_checks'] = [item.model_dump() for item in verdicts.statements]
            return verdicts

    try:
        llm = llm_factory(model, client=client, temperature=0, max_tokens=3000, max_retries=1)
        if key == 'faithfulness':
            metric = AuditedFaithfulness(llm=llm)
            arguments = {'user_input': payload['question'], 'response': payload['response'],
                         'retrieved_contexts': payload['contexts']}
        elif key == 'answer_relevancy':
            embeddings = embeddings if embeddings is not None else _bge_embeddings()
            outcome['relevancy_embedding'] = embeddings.encoder.identity
            metric = AnswerRelevancy(llm=llm, embeddings=embeddings, strictness=3)
            arguments = {'user_input': payload['question'], 'response': payload['response']}
        elif key == 'context_precision_without_reference':
            metric = ContextPrecisionWithoutReference(llm=llm)
            arguments = {'user_input': payload['question'], 'response': payload['response'],
                         'retrieved_contexts': payload['precision_contexts']}
        elif key == 'context_recall':
            metric = ContextRecall(llm=llm)
            arguments = {'user_input': payload['question'], 'reference': payload['reference'],
                         'retrieved_contexts': payload['contexts']}
        elif key == 'factual_correctness':
            metric = FactualCorrectness(llm=llm, mode='f1', beta=1.0, atomicity='low', coverage='low')
            arguments = {'response': payload['response'], 'reference': payload['reference']}
        else:
            raise ValueError('Unknown metric')
        scored = await asyncio.wait_for(metric.ascore(**arguments), timeout=payload['timeout_seconds'])
        value = float(scored.value)
        lower = -1 if key == 'answer_relevancy' else 0
        if not math.isfinite(value) or not lower <= value <= 1:
            raise ValueError('The metric did not produce a finite score in its range.')
        outcome['metric'].update(status='completed', score=value)
    except Exception as error:
        outcome['metric']['reason'] = f'This metric could not complete ({type(error).__name__}); no score is claimed.'
    finally:
        await client.close()
    return outcome

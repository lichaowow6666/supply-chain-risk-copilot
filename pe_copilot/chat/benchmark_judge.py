"""Bounded offline RAGAS batches; interactive evaluation behavior is unchanged."""
import asyncio
from datetime import datetime, timezone
from importlib.metadata import version
from time import monotonic

from .interview_quality import _bge_embeddings, _judge_configuration, _plan, _score_metric, MAX_PRECISION_CONTEXTS
from .quality import _verified_packet

_EMBEDDINGS = None


def score_batch(payload):
    return asyncio.run(_score_batch(payload))


async def _score_batch(payload):
    global _EMBEDDINGS
    items = payload['items']
    if not 1 <= len(items) <= 4:
        raise ValueError('Judge batches support 1–4 answers.')
    semaphore = asyncio.Semaphore(8)
    provider, model = _judge_configuration()
    reports = []

    async def evaluate(item):
        started = monotonic()
        frozen = _verified_packet(item['packet'])
        reference = item['reference']
        rows, contexts, sources = _plan(frozen, reference)
        report = {'packet_id': item['packet']['packet_id'], 'status': 'running',
            'scores': {}, 'metrics': rows, 'claim_checks': [], 'judge_model': model,
            'judge_provider': provider, 'ragas_version': version('ragas'),
            'created_at': datetime.now(timezone.utc).isoformat(),
            'context_scope': frozen.get('context_scope'), 'reference_supplied': True,
            'protocol': 'interview-ragas-v1 metrics; offline batch of at most four answers, eight judge tasks',
            'limitations': [
                'Synthetic authored development references, not independently reviewed or held-out production truth.',
                'Generator and judge share the configured model; correlated bias is possible.',
                'Sources include supplied scenario assumptions and calculated records, not just reported facts.',
                'No-reference precision checks up to eight sources in saved order, not a whole-report recall benchmark.',
                'Concurrent offline judge timing is separate from sequential chat-answer latency.',
                'Appropriate evidence gaps can score poorly on answer relevance; low scores need human review.']}

        async def metric(row):
            global _EMBEDDINGS
            if row['status'] != 'pending':
                return
            async with semaphore:
                phase = monotonic()
                try:
                    embeddings = None
                    if row['key'] == 'answer_relevancy':
                        if _EMBEDDINGS is None:
                            _EMBEDDINGS = _bge_embeddings()
                        embeddings = _EMBEDDINGS
                    outcome = await _score_metric({
                        'key': row['key'], 'question': frozen['question'], 'response': frozen['response'],
                        'contexts': contexts, 'precision_contexts': sources[:MAX_PRECISION_CONTEXTS],
                        'reference': reference, 'timeout_seconds': 65}, embeddings=embeddings)
                    measured = outcome['metric']
                    row.update(status=measured['status'], score=measured.get('score'))
                    if measured.get('reason'):
                        row['reason'] = measured['reason']
                    if row['status'] == 'completed':
                        report['scores'][row['key']] = row['score']
                    if row['key'] == 'faithfulness':
                        report['claim_checks'] = outcome.get('claim_checks', [])
                except Exception as error:
                    row.update(status='unavailable', score=None, reason='Judge failed: ' + type(error).__name__)
                row['duration_ms'] = round((monotonic() - phase) * 1000, 2)

        await asyncio.gather(*(metric(row) for row in rows))
        applicable = [row for row in rows if row['status'] != 'not_applicable']
        report['status'] = ('not_applicable' if not applicable else 'completed' if all(
            row['status'] == 'completed' for row in applicable) else 'partial' if report['scores'] else 'unavailable')
        report['evaluation_seconds'] = round(monotonic() - started, 3)
        reports.append({'case_id': item['case_id'], 'quality': report})

    await asyncio.gather(*(evaluate(item) for item in items))
    return reports

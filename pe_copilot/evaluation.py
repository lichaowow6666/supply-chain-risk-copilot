"""Offline synthetic retrieval baseline; chatbot/GraphRAG evaluation is in development."""
import argparse
from collections import defaultdict
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from pe_copilot.agents.fundamental_agent import FundamentalAgent
from pe_copilot.filings.index import FilingIndex
from pe_copilot.supply_chain.preparation import prepare_document


DEFAULT_SUITE = Path(__file__).resolve().parents[1] / 'evaluations/report_scope_v1.json'


def load_suite(path=DEFAULT_SUITE):
    suite = json.loads(Path(path).read_text(encoding='utf-8'))
    if (suite['schema_version'] != 1 or suite['data_status'] != 'synthetic_only'
            or suite['split'] != 'development'):
        raise ValueError('Expected a version 1 synthetic development suite.')
    reports = {r['id']: r for r in suite['reports']}
    if len(reports) != len(suite['reports']):
        raise ValueError('Duplicate report IDs.')
    evidence = {}
    for report in reports.values():
        if report['form'] not in ('10-K', '10-Q') or not report['accession']:
            raise ValueError('Each report needs a form and accession.')
        for paragraph in report['paragraphs']:
            if paragraph['id'] in evidence:
                raise ValueError('Duplicate evidence IDs.')
            evidence[paragraph['id']] = report['id']
    ids = set()
    for case in suite['cases']:
        if case['id'] in ids or not case['user_input'] or not case['reference']:
            raise ValueError('Cases need unique IDs, questions and reference answers.')
        ids.add(case['id'])
        if case['stage'] not in ('retrieval', 'in_development'):
            raise ValueError('Unknown evaluation stage.')
        scopes = [case['scope']] if case['scope'] is not None else []
        scopes += case.get('additional_scopes', [])
        if any(scope not in reports for scope in scopes):
            raise ValueError('Unknown report scope.')
        if any(evidence.get(e) not in scopes for e in case['evidence']):
            raise ValueError('Reference evidence must belong to an explicit report scope.')
        if case['stage'] == 'retrieval' and (not scopes or not case['evidence']):
            raise ValueError('Retrieval cases require a report and reference evidence.')
    return suite


def build_fixture_index(suite, directory):
    """Exercise the production parser and index with public, fictional paragraphs."""
    directory = Path(directory)
    index = FilingIndex(directory / 'evaluation.sqlite')
    citations = {}
    for report in suite['reports']:
        path = directory / (report['id'] + '.html')
        path.write_text('<html><body>' + ''.join(
            '<p>' + escape(p['text']) + '</p>' for p in report['paragraphs']
        ) + '</body></html>', encoding='utf-8')
        prepared = prepare_document(path)
        filing = dict(accessionNumber=report['accession'], form=report['form'],
                      reportDate=report['period_end'], filingDate=report['filing_date'],
                      url=report['source_url'])
        document_id = index.put(report, filing, prepared)
        for paragraph in report['paragraphs']:
            locator = prepared.locate(paragraph['text'])
            citations[paragraph['id']] = (document_id, locator)
    return index, citations


def fixture_agent(suite, index):
    """Bypass legacy vector initialization: this baseline makes no model/API calls."""
    reports = suite['reports']
    def preparation(ticker):
        owned = [r for r in reports if r['ticker'] == ticker]
        if not owned:
            return None
        annual = [r for r in owned if r['form'] == '10-K']
        baseline = max(annual or owned, key=lambda r: r['period_end'])
        return {'status': 'complete', 'baseline': {'accession': baseline['accession']}}
    agent = FundamentalAgent.__new__(FundamentalAgent)
    agent.collection = None
    agent.preparation_service = SimpleNamespace(
        index=index, load_preparation=preparation,
        reports=lambda ticker: [dict(accessionNumber=r['accession'])
                                for r in reports if r['ticker'] == ticker])
    return agent


def run_baseline(suite, k=3):
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError('k must be a positive integer.')
    reports = {r['id']: r for r in suite['reports']}
    results = []
    with TemporaryDirectory(prefix='copilot-evaluation-') as directory:
        index, citations = build_fixture_index(suite, directory)
        agent = fixture_agent(suite, index)
        for case in suite['cases']:
            if case['stage'] != 'retrieval':
                results.append({'id': case['id'], 'status': 'not_run_in_development'})
                continue
            report = reports[case['scope']]
            hits = agent.query_report(case['user_input'], report['ticker'],
                                      accession=report['accession'], n_results=k)
            expected = {citations[e] for e in case['evidence']}
            retrieved = {(h['document_id'], h['locator']) for h in hits}
            recall = len(expected & retrieved) / len(expected)
            violations = sum(h['ticker'] != report['ticker'] or
                             h['accession'] != report['accession'] for h in hits)
            results.append(dict(id=case['id'], status='measured',
                                language=case['language'], recall_at_k=recall,
                                scope_violations=violations,
                                citations=[{key: h[key] for key in ('document_id', 'chunk_id',
                                           'locator', 'ticker', 'accession')} for h in hits]))
    groups = defaultdict(list)
    measured = [r for r in results if r['status'] == 'measured']
    for row in measured:
        groups[row['language']].append(row['recall_at_k'])
    return dict(suite_id=suite['suite_id'], schema_version=suite['schema_version'],
                suite_sha256=sha256(json.dumps(suite, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest(),
                data_status=suite['data_status'], review_status=suite['review_status'],
                backend='SQLite FTS5 BM25; no embeddings or LLM', k=k,
                measured_cases=len(measured), not_run_cases=len(results)-len(measured),
                mean_recall_at_k=sum(r['recall_at_k'] for r in measured)/len(measured) if measured else None,
                recall_by_language={lang: sum(values)/len(values) for lang, values in groups.items()},
                scope_violations=sum(r['scope_violations'] for r in measured), results=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite', type=Path, default=DEFAULT_SUITE)
    parser.add_argument('--k', type=int, default=3)
    parser.add_argument('--output', type=Path, help='Optional local result JSON, e.g. artifacts/evaluation/baseline.json')
    args = parser.parse_args()
    result = run_baseline(load_suite(args.suite), args.k)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'results'}, indent=2))
    # Recall gaps are a measured baseline, not an assertion of chatbot readiness.
    return 1 if result['scope_violations'] else 0


if __name__ == '__main__':
    raise SystemExit(main())

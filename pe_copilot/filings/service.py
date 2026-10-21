"""End-to-end company preparation shared by CLI and the single-company workbench."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import threading

from pe_copilot.supply_chain.graph_repository import GraphRepository
from pe_copilot.supply_chain.preparation import prepare_document, PreparedDocument
from .sec import FilingStore, atomic_json, digest
from .extraction import EvidenceExtractor, EXTRACTION_VERSION
from .financials import read_inline_facts, baseline_from_facts, fact_projection
from .graph import build_filing_graph, key
from .index import FilingIndex

_PROCESS_LOCKS = {}
_LOCK = threading.Lock()
SERVICE_VERSION = 'company-preparation-v1'


class CompanyPreparationService:
    def __init__(self, store=None, graph_repository=None, index=None, extractor=None):
        self.store = store or FilingStore()
        self.graph_repository = graph_repository or GraphRepository()
        self.index = index or FilingIndex()
        self.extractor = extractor or EvidenceExtractor()

    def load_preparation(self, ticker):
        manifest = self.store.load(ticker)
        if not manifest:
            return None
        path = self.store.company_dir(manifest['company'])/'preparation.json'
        return json.loads(path.read_text()) if path.exists() else None

    def _ready(self, previous, manifest):
        if not previous or previous.get('status') != 'complete' or previous.get('version') != SERVICE_VERSION or previous.get('extraction_version') != EXTRACTION_VERSION:
            return False
        if not self.index.contains(manifest['company'],manifest['filings']):
            return False
        if previous.get('filing_hashes') != {f['accessionNumber']:f['sha256'] for f in manifest['filings']}:
            return False
        try:
            for record in previous['snapshots']:
                self.graph_repository.load('company:'+manifest['company']['cik'],record['snapshot_id'])
            return bool(previous['snapshots'])
        except (ValueError,KeyError,OSError):
            return False

    def prepare(self, query, progress=lambda message:None, refresh=False):
        company = self.store.client.resolve(query,self.store.root)
        with _LOCK:
            lock = _PROCESS_LOCKS.setdefault(company['cik'],threading.Lock())
        with lock:
            manifest, reused_downloads = self.store.acquire(company['ticker'],progress,refresh)
            previous = self.load_preparation(company['ticker'])
            if self._ready(previous,manifest):
                progress(f"{company['ticker']}: data already exists and passes integrity checks. Preparation complete; scanning exposure evidence.")
                return {**previous,'reused':True}
            directory = self.store.company_dir(company)
            identity_map = {}
            # Preserve stable IDs from an existing reviewed catalog when names match exactly.
            for snapshot_id in self.graph_repository.list_snapshots('company:'+company['cik']):
                if snapshot_id.startswith('sec-'):
                    continue
                view = self.graph_repository.load('company:'+company['cik'],snapshot_id)
                identity_map.update({(e.kind.value,key(e.label)):e.entity_id for e in view.snapshot.entities})
            prepared_records, warnings = [], list(manifest.get('warnings',[]))
            for i,filing in enumerate(manifest['filings']):
                progress(f"{company['ticker']}: indexing filing {i+1}/{len(manifest['filings'])} · {filing['form']} {filing['reportDate']}")
                folder = directory/Path(filing['local_path']).parent
                prepared_path = folder/'prepared.json'
                prepared = PreparedDocument.from_json(prepared_path.read_text()) if prepared_path.exists() else prepare_document(directory/filing['local_path'])
                if prepared.content_sha256 != filing['sha256']:
                    prepared = prepare_document(directory/filing['local_path'])
                meta = prepared.metadata
                if company['cik'] not in {str(v).zfill(10) for v in meta.get('EntityCentralIndexKey',[])}:
                    raise ValueError('Downloaded filing inline-XBRL company identity does not match SEC submissions.')
                from dateutil.parser import parse as parse_date
                period_dates = {parse_date(v).date().isoformat() for v in meta.get('DocumentPeriodEndDate',[])}
                if filing['reportDate'] not in period_dates:
                    raise ValueError('Downloaded filing period does not match SEC submissions.')
                if filing['form'] not in meta.get('DocumentType',[]):
                    raise ValueError('Downloaded filing form does not match SEC submissions.')
                if not prepared_path.exists() or PreparedDocument.from_json(prepared_path.read_text()).content_sha256 != filing['sha256']:
                    prepared_path.write_text(prepared.to_json(),encoding='utf-8')
                self.index.put(company,filing,prepared)
                projection_path = folder/'inline-facts.json'
                if projection_path.exists():
                    projection = json.loads(projection_path.read_text())
                    facts = projection['facts'] if projection.get('source_sha256') == filing['sha256'] else read_inline_facts(directory/filing['local_path'],company['cik'])
                else:
                    facts = read_inline_facts(directory/filing['local_path'],company['cik'])
                atomic_json(projection_path,fact_projection(facts,filing,company['cik']))
                prepared_records.append((filing,prepared,facts,projection_path))
            progress(f"{company['ticker']}: building {len(prepared_records)} independent filing graphs. Model extraction may take several minutes; completed work is cached.")
            results, failures = [], []
            def one(record):
                filing,prepared,facts,projection_path = record
                extracted = self.extractor.extract(company,filing,prepared,projection_path.parent/'extraction.json')
                snapshot_id = build_filing_graph(company,filing,prepared,extracted,facts,projection_path,self.graph_repository,identity_map)
                return {'snapshot_id':snapshot_id,'accession':filing['accessionNumber'],'form':filing['form'],
                        'period_end':filing['reportDate'],'filing_date':filing['filingDate'],'exposure_count':len(extracted['exposures'])}
            # Two independent model requests; progress/UI calls happen only on the caller thread.
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {pool.submit(one,record):record[0] for record in prepared_records}
                for future in as_completed(futures):
                    filing = futures[future]
                    try:
                        result = future.result()
                        results.append(result)
                        progress(f"{company['ticker']}: graph {len(results)}/{len(prepared_records)} ready · {filing['form']} {filing['reportDate']}")
                    except Exception as error:
                        failures.append({'accession':filing['accessionNumber'],'error_type':type(error).__name__})
                        progress(f"{company['ticker']}: {filing['form']} {filing['reportDate']} could not finish ({type(error).__name__}); saved files retained for retry.")
            latest = max((r for r in prepared_records if r[0]['form']=='10-K'),key=lambda r:(r[0]['reportDate'],r[0]['filingDate']))
            baseline = baseline_from_facts(latest[2],latest[0]['reportDate'],latest[0]['accessionNumber'])
            latest_result = next((r for r in results if r['accession']==latest[0]['accessionNumber']),None)
            exposure_items = []
            extraction_path = latest[3].parent/'extraction.json'
            if latest_result and extraction_path.exists():
                extraction = json.loads(extraction_path.read_text())
                entities = {e['ref']:e for e in extraction['entities']}
                levels = {'High':0,'Medium':1,'Low':2}
                for i,row in enumerate(sorted(extraction['exposures'],key=lambda r:levels[r['level']])):
                    entity = entities[row['material_ref']]
                    chunk = {'chunk_id':f"{latest[0]['accessionNumber']}:{row['locator']}",'text':row['quote'],
                             'document_type':latest[0]['form'],'period_end':latest[0]['reportDate'],'filing_date':latest[0]['filingDate'],
                             'source_url':latest[0]['url'],'locator':row['locator'],'distance':'exact source quotation'}
                    exposure_items.append({'material':entity['label'],'inherent_exposure_level':row['level'],'hedge_level':row.get('hedge','Not disclosed'),
                        'explanation':row.get('explanation',''),'raw_chunks':[chunk],'evidence_status':'inferred',
                        'ranking_basis':'LLM qualitative assessment, source quotation validated; not a reported risk score',
                        'source_accession':latest[0]['accessionNumber'],'source_period_end':latest[0]['reportDate']})
            if not exposure_items:
                warnings.append('No validated material exposures were extracted from the latest annual filing. Do not substitute another company or fabricate a ranking.')
            warnings.extend(baseline['warnings'])
            result = {'version':SERVICE_VERSION,'extraction_version':EXTRACTION_VERSION,'company':company,
                      'status':'partial' if failures else 'complete','filing_count':len(manifest['filings']),'snapshot_count':len(results),
                      'snapshots':sorted(results,key=lambda r:(r['period_end'],r['filing_date'])), 'failures':failures,
                      'filing_hashes':{f['accessionNumber']:f['sha256'] for f in manifest['filings']},
                      'baseline':baseline,'materials':exposure_items,'baseline_snapshot_id':latest_result['snapshot_id'] if latest_result else None,
                      'warnings':warnings,'reused':False,'prepared_on':date.today().isoformat(),'window_start':manifest['window_start']}
            atomic_json(directory/'preparation.json',result)
            if failures:
                raise RuntimeError(f"Preparation incomplete: {len(failures)} of {len(prepared_records)} filing graphs failed. Downloaded and completed data are saved; run preparation again to resume.")
            progress(f"{company['ticker']}: preparation complete. Scanning the latest annual filing's exposure evidence.")
            return result

    def reports(self, ticker):
        manifest = self.store.load(ticker)
        return sorted(manifest['filings'], key=lambda f:(f['reportDate'],f['filingDate']), reverse=True)

    def report_baseline(self, ticker, accession=None):
        result = self.load_preparation(ticker)
        if not result or result.get('status') != 'complete':
            raise ValueError('Prepare this company before scanning its filings.')
        reports = self.reports(ticker)
        filing = next((f for f in reports if f['accessionNumber']==accession), None) if accession else next(f for f in reports if f['form']=='10-K')
        if not filing:
            raise ValueError('Selected report is not available for this company.')
        directory = self.store.company_dir(result['company']) / Path(filing['local_path']).parent
        projection = json.loads((directory/'inline-facts.json').read_text())
        if projection['source_sha256'] != filing['sha256']:
            raise ValueError('Selected financial evidence changed; prepare the company again.')
        baseline = baseline_from_facts(projection['facts'],filing['reportDate'],filing['accessionNumber'],filing['form'])
        snapshot = next((r['snapshot_id'] for r in result['snapshots'] if r['accession']==filing['accessionNumber']),None)
        return {**baseline, 'snapshot_id':snapshot, 'source_url':filing['url'], 'source_sha256':filing['sha256']}

    def scan(self, ticker, accession=None, market_materials=None):
        baseline = self.report_baseline(ticker, accession)
        from .exposures import scan_materials
        materials = scan_materials(self.store, ticker, self.extractor, baseline['accession'], market_materials)
        return materials, baseline['pools']

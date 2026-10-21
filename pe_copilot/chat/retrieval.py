"""BGE-M3 dense + BM25 reciprocal-rank fusion, followed by cited graph traversal."""
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from threading import RLock
from time import perf_counter
from uuid import uuid4

import numpy as np

from pe_copilot.contracts import GraphRequest, HighlightContext, WorkspaceContext
from pe_copilot.native_runtime import get_worker


def evidence_key(row):
    return row['document_id'], row['locator']


class BGEEncoder:
    """Lazy local-only loading. No surprise multi-gigabyte download in a chat turn."""
    def __init__(self, config='artifacts/chat_models/base_model.json', max_length=512):
        self.config_path = Path(config)
        self.max_length = max_length
        self.model = None
        self.loaded_identity = None
        self.lock = RLock()

    @property
    def identity(self):
        config = json.loads(self.config_path.read_text())
        return f"{config['model_id']}:{config['revision']}:{config.get('weights_sha256','base')}:dense:{self.max_length}"

    def encode(self, texts):
        # Loading Torch in the Streamlit interpreter conflicts with the OpenMP
        # runtime bundled with XGBoost. Native models live in fresh processes.
        self.identity  # Validate configuration before starting a worker.
        values = get_worker('chat-embedding', 'pe_copilot.chat.model_worker:encode').call({
            'config': str(self.config_path.resolve()), 'max_length': self.max_length,
            'texts': list(texts),
        }, timeout=120)
        vectors = np.asarray(values, dtype=float)
        if vectors.ndim != 2 or vectors.shape[0] != len(texts) or not np.isfinite(vectors).all():
            raise ValueError('Invalid embedding worker response')
        return vectors

    def _encode_local(self, texts):
        """Native implementation; called only inside the embedding worker."""
        with self.lock:
            identity=self.identity
            if self.model is None or self.loaded_identity != identity:
                from sentence_transformers import SentenceTransformer
                config = json.loads(self.config_path.read_text())
                model = SentenceTransformer(config['path'], device='cpu', local_files_only=True)
                if config.get('weights_path'):
                    import torch
                    weights=Path(config['weights_path'])
                    if sha256(weights.read_bytes()).hexdigest()!=config['weights_sha256']:
                        self.model=None
                        raise ValueError('Fine-tuned weight checksum mismatch')
                    changed=torch.load(weights,map_location='cpu',weights_only=True)
                    if not set(changed)<=set(model.state_dict()):
                        self.model=None
                        raise ValueError('Fine-tuned weights do not match the base model')
                    model.load_state_dict(changed,strict=False)
                model.max_seq_length = self.max_length
                self.model,self.loaded_identity = model,identity
            return self.model.encode(texts, batch_size=8, normalize_embeddings=True,
                                     convert_to_numpy=True, show_progress_bar=False)


class HybridRetriever:
    def __init__(self, fundamental, encoder=None, cache_root='artifacts/chat_vectors'):
        self.fundamental = fundamental
        self.encoder = encoder
        self.root = Path(cache_root)
        self.lock = RLock()

    def search(self, question, ticker, accession, k=5, mode='hybrid'):
        if mode not in ('lexical', 'dense', 'hybrid') or not 1 <= k <= 20:
            raise ValueError('Unsupported retrieval mode or result count.')
        lexical = self.fundamental.query_report(question, ticker, accession=accession, n_results=max(k,20))
        if mode == 'lexical':
            return {'hits': lexical[:k], 'backend': 'BM25', 'warnings': []}
        warnings = []
        try:
            if self.encoder is None:
                raise FileNotFoundError('Embedding model not configured')
            service = self.fundamental.preparation_service
            rows = service.index.report_chunks(ticker, accession)
            if not rows:
                return {'hits': [], 'backend': 'empty report', 'warnings': []}
            fingerprint = sha256(json.dumps({'encoder':self.encoder.identity,
                'rows':[(r['document_id'],r['chunk_id'],r['text']) for r in rows]},sort_keys=True).encode()).hexdigest()
            self.root.mkdir(parents=True,exist_ok=True)
            path = self.root/(fingerprint+'.npz')
            with self.lock:
                if path.exists():
                    with np.load(path,allow_pickle=False) as cached:
                        vectors = cached['vectors']
                else:
                    vectors = self.encoder.encode([r['text'] for r in rows])
                    temp = self.root/(fingerprint+'.'+uuid4().hex+'.npz')
                    np.savez_compressed(temp,vectors=vectors)
                    os.replace(temp,path)
                query = self.encoder.encode([question])[0]
            if vectors.shape != (len(rows),len(query)) or not np.isfinite(vectors).all() or not np.isfinite(query).all():
                raise ValueError('Invalid embedding cache')
            scores = vectors @ query
            order = np.argsort(-scores,kind='stable')[:max(k,20)]
            dense = [{**rows[int(i)], 'dense_score':float(scores[i])} for i in order]
            if mode == 'dense':
                return {'hits':dense[:k], 'backend':self.encoder.identity,'warnings':[]}
            fused, candidates = {}, {}
            for ranking in (lexical,dense):
                for rank,row in enumerate(ranking,1):
                    key = evidence_key(row)
                    fused[key] = fused.get(key,0) + 1/(60+rank)
                    candidates[key] = {**candidates.get(key,{}),**row}
            ordered = sorted(fused,key=lambda key:(-fused[key],key))[:k]
            return {'hits':[{**candidates[key],'rrf_score':fused[key]} for key in ordered],
                    'backend':'BM25 + '+self.encoder.identity+' (RRF k=60)', 'warnings':[]}
        except Exception as error:
            # Never conceal a missing model as successful hybrid retrieval.
            warnings.append(f'Dense retrieval unavailable ({type(error).__name__}); using BM25 only. Multilingual recall may be limited.')
            return {'hits':lexical[:k], 'backend':'BM25 fallback', 'warnings':warnings}


class CrossEncoderReranker:
    """Local, pinned query/passage scoring; model files never download in a turn."""
    def __init__(self, config='artifacts/chat_models/reranker_model.json', max_length=512):
        self.config_path = Path(config)
        self.max_length = max_length
        self.model = None
        self.loaded_identity = None
        self.lock = RLock()
        self.tokenizer = None

    @property
    def identity(self):
        config = json.loads(self.config_path.read_text())
        return f"{config['model_id']}:{config['revision']}:cross-encoder:{config.get('backend', 'torch')}:{self.max_length}"

    def supports(self, question):
        # The installed MS MARCO model is English-trained. Do not replace BGE's
        # multilingual ranking with an unvalidated non-Latin cross-encoder score.
        return not any(c.isalpha() and not ('a' <= c.lower() <= 'z') for c in question)

    def score(self, question, texts):
        if not texts:
            return np.array([], dtype=float)
        self.identity  # Missing/corrupt configuration remains a normal failure.
        values = get_worker('chat-reranker', 'pe_copilot.chat.model_worker:score').call({
            'config': str(self.config_path.resolve()), 'max_length': self.max_length,
            'question': question, 'texts': list(texts),
        }, timeout=120)
        scores = np.asarray(values, dtype=float)
        if scores.shape != (len(texts),) or not np.isfinite(scores).all():
            raise ValueError('Invalid reranker worker response')
        return scores

    def _score_local(self, question, texts):
        """Native implementation; called only inside the reranker worker."""
        if not texts:
            return np.array([], dtype=float)
        with self.lock:
            identity = self.identity
            config = json.loads(self.config_path.read_text())
            if self.model is None or self.loaded_identity != identity:
                if config.get('backend') == 'onnx':
                    import onnxruntime as ort
                    from transformers import AutoTokenizer
                    options = ort.SessionOptions()
                    options.intra_op_num_threads = 2
                    self.model = ort.InferenceSession(str(Path(config['path']) / config['onnx_file']),
                        sess_options=options, providers=['CPUExecutionProvider'])
                    self.tokenizer = AutoTokenizer.from_pretrained(config['path'], local_files_only=True,
                                                                   trust_remote_code=False)
                else:
                    from sentence_transformers import CrossEncoder
                    self.model = CrossEncoder(config['path'], device='cpu', local_files_only=True,
                                              trust_remote_code=False, max_length=self.max_length)
                self.loaded_identity = identity
            if config.get('backend') == 'onnx':
                names = {item.name for item in self.model.get_inputs()}
                batches = []
                for start in range(0, len(texts), 8):
                    batch = texts[start:start+8]
                    encoded = self.tokenizer([question] * len(batch), batch, padding=True,
                        truncation=True, max_length=self.max_length, return_tensors='np')
                    batches.append(self.model.run(None, {key: value.astype(np.int64)
                        for key, value in encoded.items() if key in names})[0].reshape(-1))
                scores = np.concatenate(batches)
            else:
                scores = np.asarray(self.model.predict([(question, text) for text in texts],
                    batch_size=8, show_progress_bar=False, convert_to_numpy=True)).reshape(-1)
            if scores.shape != (len(texts),) or not np.isfinite(scores).all():
                raise ValueError('Invalid reranker scores')
            return scores


class WorkspaceRetriever:
    """Serving retrieval with bounded cold-start work and explicit coverage.

    A complete, matching BGE index is searched when available. Otherwise this
    turn embeds a bounded pool of lexical hits and diverse report passages.
    Candidate-pool semantic ranking is disclosed; it is not full-report recall.
    Every stage retains the selected company/accession and original citations.
    """
    def __init__(self, fundamental, encoder=None, reranker=None,
                 cache_root='artifacts/chat_vectors', candidate_limit=40, rerank_limit=16):
        if not 8 <= candidate_limit <= 128 or not 1 <= rerank_limit <= candidate_limit:
            raise ValueError('Invalid serving retrieval budget')
        self.fundamental, self.encoder, self.reranker = fundamental, encoder, reranker
        self.root = Path(cache_root)
        self.candidate_limit, self.rerank_limit = candidate_limit, rerank_limit
        self.lock = RLock()

    @staticmethod
    def _check_scope(rows, ticker, accession):
        if any(row.get('ticker') != ticker or row.get('accession') != accession for row in rows):
            raise ValueError('Retrieval evidence scope violation')

    def _cold_candidates(self, rows, lexical):
        """Reserve space for passages beyond BM25, including lexical-empty queries."""
        chosen = {}
        for row in lexical[:self.candidate_limit * 3 // 4]:
            chosen[evidence_key(row)] = row
        # Spread extra passages across sections. Prefer meaningful text over
        # isolated table cells, without discarding the latter from lexical hits.
        sections = {}
        for row in rows:
            if len(row['text'].strip()) >= 100:
                sections.setdefault(row.get('section') or '', []).append(row)
        if not sections:
            sections = {'': rows}
        for position in range(max(map(len, sections.values()), default=0)):
            for section in sections.values():
                if position < len(section):
                    row = section[position]
                    chosen.setdefault(evidence_key(row), row)
                if len(chosen) >= self.candidate_limit:
                    return list(chosen.values())
        return list(chosen.values())

    def _vectors(self, rows, candidates, identity):
        """Reuse full-report indexes or incrementally cache candidate embeddings."""
        fingerprint = sha256(json.dumps({'encoder': identity,
            'rows': [(r['document_id'], r['chunk_id'], r['text']) for r in rows]}, sort_keys=True).encode()).hexdigest()
        self.root.mkdir(parents=True, exist_ok=True)
        full = self.root / (fingerprint + '.npz')
        path = self.root / ('workspace-' + fingerprint + '.npz')
        with self.lock:
            if full.exists():
                with np.load(full, allow_pickle=False) as cached:
                    vectors = cached['vectors']
                if vectors.ndim != 2 or vectors.shape[0] != len(rows) or not np.isfinite(vectors).all():
                    raise ValueError('Invalid complete report embedding cache')
                return rows, vectors, {'coverage': 'full_report', 'cached_chunks': len(rows), 'encoded_chunks': 0}
            saved = {}
            if path.exists():
                with np.load(path, allow_pickle=False) as cached:
                    keys, vectors = cached['keys'], cached['vectors']
                if vectors.ndim != 2 or len(keys) != len(vectors) or not np.isfinite(vectors).all():
                    raise ValueError('Invalid candidate embedding cache')
                saved = dict(zip(keys.tolist(), vectors))
            keys = [json.dumps(evidence_key(r)) for r in candidates]
            missing = [(key, row['text']) for key, row in zip(keys, candidates) if key not in saved]
            if missing:
                vectors = np.asarray(self.encoder.encode([text for _, text in missing]))
                if vectors.ndim != 2 or len(vectors) != len(missing) or not np.isfinite(vectors).all():
                    raise ValueError('Invalid candidate embeddings')
                saved.update((key, vector) for (key, _), vector in zip(missing, vectors))
                temporary = self.root / ('workspace-' + fingerprint + '.' + uuid4().hex + '.npz')
                np.savez_compressed(temporary, keys=np.asarray(list(saved)), vectors=np.asarray(list(saved.values())))
                os.replace(temporary, path)
            return candidates, np.asarray([saved[key] for key in keys]), {
                'coverage': 'full_report' if len(candidates) == len(rows) else 'candidate_pool',
                'cached_chunks': len(candidates) - len(missing), 'encoded_chunks': len(missing)}

    def search(self, question, ticker, accession, k=5, on_progress=None):
        if not isinstance(question, str) or not question.strip() or len(question) > 8000:
            raise ValueError('Enter an evidence question of 1–8000 characters.')
        if not isinstance(ticker, str) or not ticker.strip() or not isinstance(accession, str) or not accession.strip():
            raise ValueError('Select a company and report before retrieval.')
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 20:
            raise ValueError('Requested evidence count must be 1–20.')
        ticker, accession = ticker.strip().upper(), accession.strip()
        started = perf_counter()
        diagnostics = {'scope': {'ticker': ticker, 'accession': accession}, 'stages': []}
        warnings, backend = [], ['BM25']

        def progress(message):
            if on_progress:
                on_progress(message)

        def stage(name, since, **details):
            diagnostics['stages'].append({'stage': name, 'duration_ms': round((perf_counter()-since)*1000, 2), **details})

        progress('Finding passages in the selected report…')
        phase = perf_counter()
        lexical = self.fundamental.query_report(question, ticker, accession=accession,
                                                n_results=self.candidate_limit)
        self._check_scope(lexical, ticker, accession)
        stage('lexical', phase, status='ok', backend='BM25', candidate_count=len(lexical))
        ranked = lexical
        phase = perf_counter()
        try:
            if self.encoder is None:
                raise FileNotFoundError('Embedding model not configured')
            identity = self.encoder.identity
            rows = self.fundamental.preparation_service.index.report_chunks(ticker, accession)
            self._check_scope(rows, ticker, accession)
            diagnostics['report_chunk_count'] = len(rows)
            if rows:
                candidates = self._cold_candidates(rows, lexical)
                progress(f'Comparing passage meaning with BGE-M3 (up to {len(candidates)} new passages)…')
                candidates, vectors, cache = self._vectors(rows, candidates, identity)
                query_vector = np.asarray(self.encoder.encode([question]))
                if query_vector.shape != (1, vectors.shape[1]) or not np.isfinite(query_vector).all():
                    raise ValueError('Invalid query embedding')
                scores = vectors @ query_vector[0]
                order = np.argsort(-scores, kind='stable')[:self.candidate_limit]
                dense = [{**candidates[int(i)], 'dense_score': float(scores[i])} for i in order]
                fused, evidence = {}, {}
                for ranking in (lexical, dense):
                    for rank, row in enumerate(ranking, 1):
                        key = evidence_key(row)
                        fused[key] = fused.get(key, 0) + 1/(60+rank)
                        evidence[key] = {**evidence.get(key, {}), **row}
                ranked = [{**evidence[key], 'rrf_score': fused[key]} for key in
                          sorted(fused, key=lambda key: (-fused[key], key))]
                coverage = cache['coverage']
                diagnostics['dense'] = {'status': 'ok', 'model': identity, 'candidate_count': len(candidates), **cache}
                stage('dense', phase, **diagnostics['dense'])
                backend.append('BGE-M3 full-report dense' if coverage == 'full_report' else 'BGE-M3 candidate-pool dense')
                backend.append('RRF')
                if coverage == 'candidate_pool':
                    warnings.append(f'Semantic search covered {len(candidates)} of {len(rows)} report chunks in this turn; '
                                    'a bounded candidate pool limits first-use latency and can miss evidence elsewhere in the report.')
            else:
                diagnostics['dense'] = {'status': 'empty_report', 'candidate_count': 0}
                stage('dense', phase, **diagnostics['dense'])
        except ValueError as error:
            if str(error) == 'Retrieval evidence scope violation':
                raise
            diagnostics['dense'] = {'status': 'unavailable', 'error_type': type(error).__name__}
            stage('dense', phase, **diagnostics['dense'])
            warnings.append(f'Dense retrieval unavailable ({type(error).__name__}); retaining BM25 evidence.')
        except Exception as error:
            diagnostics['dense'] = {'status': 'unavailable', 'error_type': type(error).__name__}
            stage('dense', phase, **diagnostics['dense'])
            warnings.append(f'Dense retrieval unavailable ({type(error).__name__}); retaining BM25 evidence.')
        phase = perf_counter()
        candidates = ranked[:max(k, self.rerank_limit)]
        try:
            if not candidates:
                diagnostics['reranker'] = {'status': 'no_candidates', 'candidate_count': 0}
            elif self.reranker is None:
                raise FileNotFoundError('Reranker not configured')
            elif not self.reranker.supports(question):
                diagnostics['reranker'] = {'status': 'skipped_language', 'candidate_count': 0}
                warnings.append('The local reranker is English-trained; non-Latin queries retain the hybrid ranking.')
            else:
                identity = self.reranker.identity
                progress(f'Checking question-to-passage relevance for {len(candidates)} passages…')
                scores = np.asarray(self.reranker.score(question, [row['text'] for row in candidates]))
                if scores.shape != (len(candidates),) or not np.isfinite(scores).all():
                    raise ValueError('Invalid reranker scores')
                ranked = [{**candidates[int(i)], 'rerank_score': float(scores[i])} for i in
                          np.argsort(-scores, kind='stable')]
                diagnostics['reranker'] = {'status': 'ok', 'model': identity, 'candidate_count': len(candidates)}
                backend.append('cross-encoder reranking')
        except Exception as error:
            diagnostics['reranker'] = {'status': 'unavailable', 'error_type': type(error).__name__}
            warnings.append(f'Reranking unavailable ({type(error).__name__}); retaining the preceding evidence ranking.')
        stage('reranker', phase, **diagnostics['reranker'])
        self._check_scope(ranked, ticker, accession)
        diagnostics['duration_ms'] = round((perf_counter()-started)*1000, 2)
        diagnostics['status'] = 'ok' if not warnings else 'limited'
        diagnostics['returned_count'] = min(k, len(ranked))
        return {'hits': ranked[:k], 'backend': ' + '.join(backend), 'warnings': warnings, 'diagnostics': diagnostics}


def make_workspace_retriever(fundamental, profile='base'):
    """Lazy local models, suitable for the sidebar's cached application service."""
    if profile == 'base':
        # Preserve the existing production initialization and failure behavior.
        return WorkspaceRetriever(fundamental, BGEEncoder(), CrossEncoderReranker())
    from pe_copilot.chat.model_registry import ModelRegistry
    return WorkspaceRetriever(fundamental, ModelRegistry().encoder(profile), CrossEncoderReranker())


def graph_evidence(orchestrator, scope, question, hits, focus_material=None):
    """Join evidence to bounded source-backed paths; no inferred product dollar allocation."""
    context = WorkspaceContext(scope['company_id'],scope['snapshot_id'])
    view = orchestrator.graph_repository.load(context.company_id,context.snapshot_id)
    documents = [view.registry.get_document(d) for d in view.snapshot.document_ids]
    # Graph and retrieval IDs use different formats. Join via accession + source hash,
    # not by assuming the chunk IDs and graph document IDs are interchangeable.
    if not any(d.accession == scope['accession'] for d in documents):
        raise ValueError('Graph does not belong to the selected accession')
    normalized = (focus_material or question).casefold()
    roots = [e for e in view.snapshot.entities if e.label.casefold() in normalized]
    if not roots:
        hit_text = ' '.join(h['text'] for h in hits).casefold()
        roots = [e for e in view.snapshot.entities if e.kind.value == 'material' and e.label.casefold() in hit_text][:3]
    roots = sorted(roots,key=lambda e:(e.kind.value != 'material',-len(e.label)))[:3]
    paths, citations = [], []
    for entity in roots:
        for profile in ('material_exposure','physical_supply','financial_mechanisms'):
            result = view.paths(entity.entity_id,profile=profile,max_hops=4,limit=12,confirmed_only=False)
            for path in result['paths']:
                labels = [view.entity(i).label for i in path.entity_ids]
                edges=[]
                for claim in path.claims:
                    refs=[]
                    for item in view.evidence(claim.claim_id):
                        citation=item['citation'];document=view.registry.get_document(citation['document_id'])
                        if document.accession != scope['accession']:
                            continue
                        row={'text':item['resolved_excerpt'],'document_id':document.document_id,
                             'locator':citation['locator'],'accession':document.accession,'ticker':scope['ticker'],
                             'document_type':document.form,'period_end':document.period_end,'filing_date':document.filing_date,
                             'source_url':citation['reference'].get('source_url'), 'origin':claim.provenance.origin.value}
                        citations.append(row);refs.append(evidence_key(row))
                    if refs:
                        edges.append({'id':claim.claim_id,'relation':claim.relation,'origin':claim.provenance.origin.value,'evidence_keys':refs})
                if len(edges)==len(path.claims):
                    paths.append({'labels':labels,'entity_ids':list(path.entity_ids),'edges':edges,
                                  'conditional':path.conditional,'quantification':path.quantification})
    signal=HighlightContext('chatbot',uuid4().hex,scope['ticker'],entity_ids=tuple(e.entity_id for e in roots),
                            company_id=context.company_id,snapshot_id=context.snapshot_id)
    result=orchestrator.invoke(GraphRequest(context,lens='all_relationships',confirmed_only=False,
        focus_entity_id=roots[0].entity_id if roots else None,highlight=signal,source='chatbot'))
    return {'paths':paths,'citations':citations,'highlight':asdict(signal),
            'payload':result.data if result.status=='ok' else {},'gaps':list(view.gaps())}

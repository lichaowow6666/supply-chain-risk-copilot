# =====================================================================
# fundamental_agent.py - Per-Metal Chunk Mapping & 3-Pool KPI Extraction
# =====================================================================

import os
import json
import re

# Guard heavy dependency (chromadb) to allow importing the package in minimal environments.
try:
    import chromadb
    HAS_CHROMADB = True
except Exception:
    chromadb = None
    HAS_CHROMADB = False

from typing import List, Dict, Any

from pe_copilot.evidence import EvidenceReference, EvidenceStatus, RelationshipClaim

# dotenv is optional at import time; attempt to load environment variables if available
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    def load_dotenv():
        return None

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

hf_mirror = os.getenv("HF_MIRROR")
if hf_mirror:
    os.environ["HF_ENDPOINT"] = hf_mirror



ATOMIC_METAL_ALIASES = {
    "aluminum": "Aluminum",
    "aluminium": "Aluminum",
    "beryllium": "Beryllium",
    "cobalt": "Cobalt",
    "copper": "Copper",
    "coppers": "Copper",
    "gold": "Gold",
    "iridium": "Iridium",
    "nickel": "Nickel",
    "niobium": "Niobium",
    "osmium": "Osmium",
    "palladium": "Palladium",
    "platinum": "Platinum",
    "rhenium": "Rhenium",
    "rhodium": "Rhodium",
    "ruthenium": "Ruthenium",
    "silver": "Silver",
    "tantalum": "Tantalum",
    "tin": "Tin",
    "titanium": "Titanium",
    "tungsten": "Tungsten",
    "zinc": "Zinc",
    "natural gas": "Natural Gas",
    "crude oil": "Crude Oil",
    "ethane": "Ethane",
    "propane": "Propane",
    "ethylene": "Ethylene",
    "propylene": "Propylene",
    "naphtha": "Naphtha",
    "benzene": "Benzene",
    "ammonia": "Ammonia",
}

GROUPED_MATERIAL_NAMES = {
    "base metal",
    "base metals",
    "critical mineral",
    "critical minerals",
    "metal",
    "metals",
    "other metal",
    "other metals",
    "precious metal",
    "precious metals",
    "rare earth",
    "rare earths",
    "rare earth metal",
    "rare earth metals",
}

class FundamentalAgent:
    def __init__(self, db_path: str = "chroma_db", collection_name: str = "annual_reports", preparation_service=None):
        self.preparation_service = preparation_service
        self.db_path = os.path.abspath(db_path)
        self.collection_name = collection_name
        self.client = None
        self.collection = None
        self._connect()

    def _connect(self):
        if not HAS_CHROMADB:
            print("[FundamentalAgent ERROR] chromadb is not installed")
            return
        if not os.path.exists(self.db_path):
            print(f"[FundamentalAgent ERROR] Path not found: {self.db_path}")
            return
        try:
            self.client = chromadb.PersistentClient(path=self.db_path)
            self.collection = self.client.get_collection(name=self.collection_name)
        except Exception as e:
            print(f"[FundamentalAgent] Connection failed: {e}")

    def get_available_tickers(self) -> List[str]:
        """Return company identities actually present locally; no fixed ticker list."""
        available = set()
        service = getattr(self, 'preparation_service', None)
        if service:
            for path in service.store.root.glob('*/manifest.json'):
                metadata = json.loads(path.read_text())
                available.add(metadata['company']['ticker'].upper())
        if self.collection:
            try:
                result = self.collection.get(include=['metadatas'])
                available.update(m['ticker'].upper() for m in result.get('metadatas',[]) if m and m.get('ticker'))
            except Exception:
                pass
        return sorted(available)

    def query_10k(
        self,
        query_text: str,
        company_ticker: str,
        n_results: int = 5,
        max_distance: float = 0.75,
        *,
        accession: str = None,
    ) -> List[Dict]:
        """Compatibility name for query_report; an explicit filing is now required."""
        return self.query_report(query_text, company_ticker, accession=accession,
                                 n_results=n_results, max_distance=max_distance)

    def query_report(self, query_text: str, company_ticker: str, *, accession: str,
                     n_results: int = 5, max_distance: float = 0.75) -> List[Dict]:
        """Retrieve from exactly one selected 10-K or 10-Q, never a default report.

        Legacy vector records without accession metadata cannot establish scope and
        are excluded. Vector distance thresholds do not apply to SQLite BM25.
        """
        if not isinstance(company_ticker, str) or not company_ticker.strip():
            raise ValueError('Select a company before retrieving evidence.')
        if not isinstance(accession, str) or not accession.strip():
            raise ValueError('Select a report accession before retrieving evidence.')
        if isinstance(n_results, bool) or not isinstance(n_results, int) or n_results < 1:
            raise ValueError('n_results must be a positive integer.')
        ticker, accession = company_ticker.strip().upper(), accession.strip()
        service = getattr(self, 'preparation_service', None)
        if service is not None:
            prepared = service.load_preparation(ticker)
            if not prepared or prepared.get('status') != 'complete':
                raise ValueError('Prepare the selected company before retrieving evidence.')
            if not any(f['accessionNumber'] == accession for f in service.reports(ticker)):
                raise ValueError('Selected report is not available for this company.')
            if not service.index.has_report(ticker, accession):
                raise ValueError('Selected report is not indexed; prepare the company again.')
            return service.index.query(query_text, ticker, accession, n_results)
        if not self.collection:
            return []
        if ticker not in self.get_available_tickers():
            raise ValueError(f"No local filing data for: {company_ticker}")
        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results,
                where={"$and": [{"ticker": ticker}, {"accession": accession}]},
                include=["documents", "distances", "metadatas"],
            )
        except Exception as error:
            print(f"[FundamentalAgent] 10-K retrieval failed for {ticker}: {error}")
            return []
        extracted = []
        if results and "documents" in results and results["documents"]:
            for idx, doc in enumerate(results["documents"][0]):
                dist = results["distances"][0][idx] if "distances" in results and results["distances"] else 0.0
                if dist <= max_distance:
                    metadata = {}
                    if results.get("metadatas") and results["metadatas"][0]:
                        metadata = results["metadatas"][0][idx] or {}
                    if metadata.get('ticker') != ticker or metadata.get('accession') != accession:
                        continue
                    extracted.append({
                        **metadata,
                        "text": doc.strip(),
                        "distance": round(dist, 4),
                        "ticker": metadata.get("ticker", ticker),
                        "document_type": metadata.get("document_type"),
                        "accession": accession,
                    })
        return extracted

    # =================================================================
    # 1. UNBIASED MATERIAL DISCOVERY & CITATION MAPPING
    # =================================================================
    def analyze_report(self, ticker, accession=None, market_materials=None):
        """Return report-scoped evidence and deterministic XBRL financial inputs.

        Extraction/caching lives in the filing service. This is the agent boundary
        used by both discovery and scenario orchestration, not an agent-to-agent chat.
        """
        service = getattr(self, 'preparation_service', None)
        if service is None:
            raise ValueError('Report-scoped analysis requires company preparation.')
        prepared = service.load_preparation(ticker)
        if not prepared or prepared.get('status') != 'complete':
            raise ValueError('Prepare the selected company before running a scenario.')
        baseline = service.report_baseline(ticker, accession)
        materials, _ = service.scan(ticker, baseline['accession'], market_materials)
        return {'company':prepared['company'], 'baseline':baseline, 'materials':materials}

    def get_top_vulnerable_materials(self, company_ticker: str, accession: str = None) -> List[Dict[str, Any]]:
        """
        Executes strict conceptual queries targeting financial risk and hedging disclosures,
        avoiding generic business description paragraphs.
        """
        # TUNED QUERIES: Shifted away from "materials" and toward PE risk concepts
        ticker = company_ticker.upper()
        service = getattr(self, 'preparation_service', None)
        if service and service.load_preparation(ticker):
            return service.scan(ticker, accession)[0]
        if ticker not in self.get_available_tickers():
            raise ValueError(f"No filing data is available for ticker: {company_ticker}")

        queries = [
            "Item 7A market risk commodity price volatility financial impact",
            "derivative financial instruments forward swaps hedge contracts unhedged inventory",
            "consignment agreements fee risk price adders customer surcharge",
            "individual gold silver platinum palladium rhodium iridium copper price sensitivity consignment",
        ]

        all_chunks = []
        seen_texts = set()
        chunk_id_counter = 1

        for q in queries:
            chunks = self.query_report(q, company_ticker=ticker, accession=accession, n_results=5)
            for c in chunks:
                if c["text"] not in seen_texts:
                    seen_texts.add(c["text"])
                    c["chunk_id"] = chunk_id_counter 
                    all_chunks.append(c)
                    chunk_id_counter += 1

        if not all_chunks:
            return self._fallback_material_synthesis(all_chunks)

        context = "\n\n".join([f"Chunk {c['chunk_id']}:\n{c['text']}" for c in all_chunks])
        return self._synthesize_vulnerabilities_with_llm(ticker, context, all_chunks)

    def _synthesize_vulnerabilities_with_llm(self, company_ticker: str, context: str, all_chunks: List[Dict]) -> List[Dict[str, Any]]:
        deepseek_key = os.getenv("DEEPSEEK_API_KEY")
        openai_key = os.getenv("OPENAI_API_KEY")

        if deepseek_key and HAS_OPENAI:
            client = openai.OpenAI(api_key=deepseek_key, base_url="https://api.deepseek.com")
            model_name = "deepseek-chat"
        elif openai_key and HAS_OPENAI:
            client = openai.OpenAI(api_key=openai_key)
            model_name = "gpt-4o-mini"
        else:
            return self._fallback_material_synthesis(all_chunks)

        prompt = f"""
        You are a Senior PE Analyst auditing SEC 10-K filings for {company_ticker}.

        TASK:
        Read the provided chunks and discover specific raw materials or commodities that represent genuine operational or financial risks.
        
        STRICT RULES & ANTI-PATTERNS:
        1. Return ONE SPECIFIC MATERIAL per record (metals, chemical feedstocks or energy) using its canonical name, for example
           "Copper", "Gold", "Silver", "Platinum", "Palladium", or "Rhodium".
        2. NEVER return group labels such as "Precious Metals", "Base Metals", or "Rare Earths".
        3. If a disclosure discusses a group, expand it ONLY into individual metals that the supplied
           chunks explicitly name and tie to that same disclosed risk. Never guess the members of a group.
        4. Normalize plurals such as "Coppers" to "Copper".
        5. THE LAUNDRY LIST TRAP: Ignore generic product descriptions or lists of metals unless the
           same evidence explicitly connects each returned metal to price volatility, financial impact,
           consignment-fee risk, or hedging action.
        6. Do not hallucinate or make assumptions about hedging. If hedging is not mentioned, state
           "Not disclosed in context".
        7. QUALITY OVER QUANTITY: Return only independently evidenced metals; do not pad the results.
        
        Return JSON matching this exact structure:
        {{
          "materials": [
            {{
              "material": "Canonical specific material name",
              "inherent_exposure_level": "High / Medium / Low (Evaluate the raw operational/financial risk)",
              "hedge_level": "Describe specific derivative contracts, consignment limits, or state 'Unhedged/Not Disclosed'",
              "explanation": "2-sentence brief detailing the specific financial or operational risk. STRICTLY based on text.",
              "supporting_chunk_ids": [1, 4] // Array of INT. List up to 3 Chunk IDs that strictly prove this risk.
            }}
          ]
        }}

        Context:
        {context}
        """

        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.0 # Force maximum determinism to prevent hallucination
            )
            parsed = json.loads(response.choices[0].message.content)
            if isinstance(parsed, dict):
                items = parsed.get("materials", parsed.get("vulnerabilities", []))
            else:
                items = []
            return self._validate_material_results(company_ticker, items, all_chunks)

        except Exception as e:
            print(f"[FundamentalAgent] LLM Material Error: {e}")
            return self._fallback_material_synthesis(all_chunks)

    def _validate_material_results(
        self,
        company_ticker: str,
        items: Any,
        all_chunks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Normalize atomic metals and attach validated, reusable evidence claims."""
        if not isinstance(items, list):
            return []

        chunks_by_id = {chunk.get("chunk_id"): chunk for chunk in all_chunks}
        validated = []
        seen_metals = set()
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            raw_name = str(raw_item.get("material", "")).strip().lower()
            if raw_name in GROUPED_MATERIAL_NAMES:
                continue
            material = ATOMIC_METAL_ALIASES.get(raw_name)
            if not material or material in seen_metals:
                continue

            cited_ids = raw_item.get("supporting_chunk_ids", [])
            if not isinstance(cited_ids, list):
                continue
            cited_chunks = [
                chunks_by_id[chunk_id]
                for chunk_id in cited_ids
                if chunk_id in chunks_by_id
            ][:3]
            evidence_pattern = re.compile(rf"\b{re.escape(material)}s?\b", re.IGNORECASE)
            matched_chunks = [
                chunk
                for chunk in cited_chunks
                if evidence_pattern.search(chunk.get("text", ""))
            ]
            if not matched_chunks:
                continue

            claim = RelationshipClaim(
                subject=company_ticker.upper(),
                relation="exposed_to_material",
                object=material,
                evidence_status=EvidenceStatus.CONFIRMED,
                evidence=EvidenceReference(
                    source_document=f"{company_ticker.upper()} 10-K",
                    evidence_excerpt=matched_chunks[0]["text"],
                ),
            )
            item = dict(raw_item)
            item["material"] = material
            item["supporting_chunk_ids"] = [
                chunk["chunk_id"] for chunk in matched_chunks
            ]
            item["raw_chunks"] = matched_chunks
            item["relationship_claims"] = [claim.to_dict()]
            validated.append(item)
            seen_metals.add(material)
        return validated

    def _fallback_material_synthesis(self, all_chunks: List[Dict]) -> List[Dict[str, Any]]:
        """Fail closed rather than fabricating material relationships without an LLM result."""
        return []

    # =================================================================
    # 2. 3-POOL FINANCIAL KPI EXTRACTION
    # =================================================================
    def get_baseline_financials(self, company_ticker: str) -> Dict[str, Dict[str, float]]:
        service = getattr(self, 'preparation_service', None)
        if service and service.load_preparation(company_ticker):
            return service.scan(company_ticker)[1]
        # Financial baselines now require exact annual XBRL contexts from preparation.
        # Legacy example figures and free-form LLM numbers are not company baselines.
        return {}

    def _fallback_financial_synthesis(self) -> Dict[str, Dict[str, float]]:
        """Missing source-backed financials stay missing."""
        return {}

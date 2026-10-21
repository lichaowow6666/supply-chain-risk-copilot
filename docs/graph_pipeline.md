# Evidence graph pipeline: steps 4–6

Current status: step 9 company onboarding and annual/quarterly history are implemented. See [company_onboarding.md](company_onboarding.md) for the current workflow, validation and remaining limits; historical planning statements below describe the earlier implementation batch.

Implemented for the supplied MTRN 2025 filing. Steps 7–8 now connect it to the orchestrator and interactive Tab 3; see `graph_explorer.md`. The chatbot, graph financial-loss calculator and step 9 historical filing comparison remain **in development**.

## Result and source verification

The graph contains 126 entities, 245 relationship claims, and 233 observations. All 33 Item 2 facility entries are included, with corporate, manufacturing and distribution facilities distinguished. Two Shanghai entries are separate nodes. The 2023–2025 segment/end-market table produces 135 reported revenue observations and 96 derived share observations; two further observations store the disclosed hypothetical annual consignment sensitivities.

All segment totals and market totals reconcile exactly in the table's reported USD-thousands units. Dashes in this specific table are interpreted as nil, with original tokens and the interpretation retained. Corporate Other has zero revenue; its revenue share is not applicable rather than zero divided by zero. A disclosed sensitivity is stored as a dated hypothetical observation, not an expense incurred during 2025. No annual-to-quarterly conversion, EBITDA allocation, or new financial shock formula is implemented.

Identity was checked against the [SEC filing index](https://www.sec.gov/Archives/edgar/data/1104657/0001104657-26-000011-index.html): CIK 0001104657, accession 0001104657-26-000011, filed 2026-02-12, reporting period ending 2025-12-31. The local filename is not treated as proof of identity: its Inline XBRL metadata and content hash must match the reviewed catalog. The hash identifies the supplied local file; it is not a claim of byte-for-byte equality with a freshly downloaded SEC file.

## How identification and chunking work

1. The deterministic parser selects the primary 10-K when supplied a complete submission, decodes the file without dropping non-UTF-8 characters, reads identity metadata, and excludes hidden XBRL, scripts and styles from visible text.
2. It preserves visible paragraphs and table rows, assigns Item sections, and creates stable source locators. The MTRN file produces 930 blocks and 1,894 chunks. Paragraphs split at sentence/word boundaries with a 1,600-character maximum. Whole table rows remain intact, with the parent table available for headers. Exceptionally large rows can exceed the paragraph limit. Page-break continuations remain separate blocks and can be cited together.
3. The catalog is an explicit source-reviewed mapping of company, segments, materials, products, facilities, markets, mechanisms and relationships to those locators. It is not an automatic company-entity recognizer. Runtime code does not infer relationships merely because two names appear in a chunk.
4. Structured revenue tables are parsed with a reviewed layout configuration; rows/columns, years, units, original values and totals are checked. Optional composition remains a `may_be_used_in` edge. Product-level market statements are not silently promoted to parent-segment statements.
5. Accepted records are compiled into a snapshot. Every citation is resolved against prepared source text before a bundle is saved or loaded.

No runtime LLM or embedding service is used. Codex performed the initial source review that produced the catalog. A future LLM extractor can propose candidates for review, but source validation and financial calculations remain separate. Embeddings can later index these chunks for semantic retrieval; embeddings do not have to create the chunk boundaries.

## Nodes, edges and observations

- `Entity`: a node with ID, type, label, scope and provenance. Static attributes include facility location, tenure, category, reported floor area text, and identity qualifications. Attributes inherit the entity's evidence; add separate claims/observations where a feature needs independent provenance or dates.
- `GraphClaim`: an edge. `subject` and `object` reference node IDs, `relation` supplies its meaning, and provenance supplies its supporting passages. These live in `snapshot.json` under `claims` and become keyed edges in a NetworkX `MultiDiGraph` when queried. Multiple relationships between the same nodes remain distinct.
- `Observation`: a dated measurement/disclosure, not a bag of arbitrary node features. `subject` identifies the measured entity; `related_entity_id` can identify the market for a segment's revenue. Value, unit, currency, periods, source token, assumptions and derivation inputs remain explicit. There are no dependency weights inferred from revenue shares.

Company-specific entities use CIK namespaces. Shared material concepts do not create trade relationships between companies. End-market labels remain company scoped. Confirmed-only query filters apply to edges and endpoint entities.

## Build and inspect locally

From the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pe_copilot.supply_chain.build --catalog pe_copilot/supply_chain/catalogs/mtrn_2025.json
```

Optional `--source` selects a local copy (it must match the reviewed hash); `--output` selects a local artifact directory. The default bundle is:

```text
artifacts/evidence_graph/0001104657/mtrn-2025-evidence-v1/
  manifest.json        integrity hashes and source-file mapping
  registry.json        company and filing identity
  snapshot.json        entities, claims and observations
  coverage.json        original candidate dispositions and record references
  reconciliation.json  table checks and totals
  source-<hash>.json    prepared blocks and chunks with source metadata
```

The expanded record-by-record review report is `artifacts/evidence_graph/mtrn-2025-evidence-v1-review.md`. Generated source and graph artifacts are already excluded by the repository's existing `artifacts/` ignore rule. Nothing is uploaded or committed by the build.

```python
from pe_copilot.supply_chain import GraphRepository

repo = GraphRepository("artifacts/evidence_graph")
graph = repo.load("company:0001104657", "mtrn-2025-evidence-v1")
entities = graph.search("Copper")
paths = graph.paths(
    "concept:material:copper",
    profile="material_exposure",
    target="company:0001104657:segment:performance-materials",
)
# Three evidenced copper -> product -> product line -> segment paths.
# Each returned path retains GraphClaim objects and their citations.
observations = graph.observations(related_entity_id="concept:material:copper")
evidence = graph.evidence(observations[0].observation_id)
gaps = graph.gaps()
```

Traversal profiles distinguish material exposure, physical supply and financial mechanisms. Hierarchy and revenue edges do not participate in physical supply paths. Corporate-cost segments do not propagate operational exposure. Traversal respects direction, prevents cycles, limits depth/results and reports truncation. Returned paths include an unquantified-exposure warning; this is a graph-query service, not a contagion-loss calculator.

A snapshot is immutable: repeating an identical build is idempotent; changed content requires a new snapshot ID. The catalog also pins parser version and original source hash. A new filing or parser change requires recuration rather than silently reusing old locators. Integrity hashes detect accidental bundle changes, not malicious re-signing by someone who can rewrite every file.

## Coverage qualifications

133 checklist entries are represented, 3 remain ambiguous, and 8 proposed mappings are unsupported. In particular:

- Delta, Spor Mountain Mill and the generic Utah milling operation remain separate; equivalence is not established.
- An unpassed-metal-cost amount requires modeling assumptions; the filing supports timing-lag exposure.
- The Alloyed Metals and Beryllium Components market passages are attached to those specific product families, with parent hierarchy separate.
- The timing-lag passage says margins without identifying an accounting margin measure, so that path ends at an unspecified-margin KPI.
- Rhenium and osmium have price-exposure links from the pricing disclosure rather than asserted physical-use links.
- Copper impacts are not allocated to Performance Materials; precious-metal group sensitivities are not split among atomic metals.
- Konasol is represented as an acquisition event, not an ongoing supplier.

The labels “confirmed” and “accepted” record source-review attestations by Codex; they are not a claim of human approval or independent audit.

## Adding DOW

Supply and verify its filing, prepare it with the same parser, then create a separate reviewed catalog and snapshot. Catalogs specify their own entity/segment/market structure and revenue-table units and layout. The builder supports variable segment counts and currencies; synthetic tests cover a one-segment EUR example. Different source table layouts may require a new parsing adapter, while graph identities, storage and queries stay unchanged. Automatic entity/relation extraction across arbitrary filings is **in development**. No DOW facts are preloaded and no MTRN values are used as fallbacks.

## Change and validation record

Files added or modified for steps 4–6:

| File | Change |
|---|---|
| `pe_copilot/supply_chain/preparation.py` | Added visible SEC blocks, table rows, deterministic chunks and exact evidence resolution |
| `pe_copilot/supply_chain/curation.py` | Added hash-pinned catalog compilation, numerical table validation and coverage checks |
| `pe_copilot/supply_chain/graph_repository.py` | Added immutable bundle storage, integrity validation and bounded query services |
| `pe_copilot/supply_chain/build.py` | Added reproducible build command and expanded review-report generation |
| `pe_copilot/supply_chain/catalogs/mtrn_2025.json` | Added source-reviewed MTRN entities, claims, source selectors, table configuration and coverage dispositions |
| `pe_copilot/supply_chain/schema.py` | Added static entity attributes and observation context; validated related entities and monetary units |
| `pe_copilot/supply_chain/__init__.py` | Exported the preparation, curation and repository interfaces |
| `tests/test_graph_pipeline.py` | Added 19 offline parser, repository, generic-catalog and local MTRN integration tests |
| `docs/mtrn_graph_coverage.md` | Replaced pending candidates with reviewed dispositions and record references |
| `docs/knowledge_graph_foundation.md` | Updated implementation status and contract descriptions |
| `docs/graph_pipeline.md` | Added architecture explanation, usage, qualifications and this change record |

Generated artifacts: the six bundle files listed above (including the prepared-source file) and the expanded review report. Existing application files, financial formulas and legacy graph data were not edited in this batch. Existing uncommitted work was preserved. No commit or push was performed.

Validation executed successfully:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -c "from pe_copilot.agents import MarketAgent, FundamentalAgent; from pe_copilot.analytics import run_stress_test; from pe_copilot.supply_chain import GraphRepository, prepare_document, build_catalog; print('Core and graph imports passed')"
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pe_copilot.supply_chain.build --catalog pe_copilot/supply_chain/catalogs/mtrn_2025.json
git diff --check
git check-ignore artifacts/evidence_graph/0001104657/mtrn-2025-evidence-v1/snapshot.json
```

Results: **47 tests passed**, including all 7 local MTRN integration tests; imports passed; the repeated build preserved the immutable bundle. A saved-bundle smoke check loaded the graph, verified three copper-to-Performance-Materials paths with resolved evidence, retrieved the copper sensitivity and checked all 11 explicit gaps. No external LLM, embedding, or market connectivity scripts were run. No UI behavior changed, so Streamlit interaction was not tested in this batch.

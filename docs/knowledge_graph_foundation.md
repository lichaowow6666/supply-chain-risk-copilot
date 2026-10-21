# Evidence graph foundation

Current status: step 9 company onboarding and annual/quarterly history are implemented. See [company_onboarding.md](company_onboarding.md) for the current workflow, validation and remaining limits; historical planning statements below describe the earlier implementation batch.

Status: steps 1–8 implemented. Source preparation, the curated MTRN graph, local queries, graph orchestration and interactive Tab 3 are available. Graph financial-loss propagation and chatbot routing are **in development**. Step 9 historical 10-K/10-Q ingestion is deferred. See `graph_pipeline.md` for building and `graph_explorer.md` for the interactive workbench and validation.

## Scope and acceptance

Complete means every candidate in the MTRN coverage ledger has an evidence-reviewed disposition. It does not mean all candidates become confirmed facts. Include all disclosed facilities even when only a segment membership is supported. Record gaps instead of inventing connectivity. Store revenue observations including zero, missing and not-applicable distinctions. Reconcile totals before accepting numerical observations.

`mtrn_graph_coverage.md` records the disposition of all 144 original candidates. The curated catalog expands family rows into individual entities and claims and pins the source hash and parser version. Included records are source-reviewed by Codex, not human-approved. Ambiguities and unsupported mappings remain queryable gaps.

## Contracts

- `Company`: ten-digit SEC CIK, ticker, name and identity verification source. Ticker is an attribute, not the key. The caller must verify the CIK: format validation is not identity verification.
- `SourceDocument`: company, form, fiscal period end, optional filing date/accession, source location, SHA-256 of original bytes and parser version. Missing accession/date stay null. Document identity includes company, accession when available and full content hash. Amendments and different content do not overwrite earlier filings.
- `Registry`: explicit, idempotent registration; conflicting metadata is rejected. JSON round trips preserve identities. No automatic database migration or download occurs.
- `Entity`: company-scoped IDs for segments, products, facilities, operations, pools and mechanisms. Shared concepts are permitted for materials, cohorts, markets and KPI definitions; sharing requires curation. Chemicals, mixtures, energy and metals all fit the material type.
- `GraphClaim`: directed, named relationship with provenance; multiple citations allowed. Relation names use snake_case. Domain-specific allowed path rules are deferred to the query service; syntactic validity does not establish causal meaning.
- `Observation`: decimal strings preserve precision and signs; explicit unit, currency, period, availability and basis. Derived observations require input IDs and a derivation description; assumed values require assumptions. This module computes no financial values. Sensitivity conditions belong in assumptions and metric descriptions until a dedicated scenario contract is implemented. Related entity IDs attach segment revenue to end markets; horizon and as-of date distinguish hypothetical annual sensitivities from realized annual results. Original numerical tokens are retained.
- `Provenance`: reuses existing EvidenceStatus and EvidenceReference. Origin and review state are separate. Confirmed records require citations; inferred/synthetic records require a rationale. Accepted/rejected records require reviewer and review date. Acceptance is an explicit attestation, not automatic factual validation.
- `GraphSnapshot`: versioned accepted records; validates namespaces, unique IDs, endpoints and citation membership. Call `Registry.validate_snapshot` to check document registration before using or persisting a snapshot. Candidate/rejected records remain in the curation ledger, outside published snapshots.

Existing Tab 1 claims and serialization are unchanged. No financial formulas, fallback figures or existing scenario settings have changed. The local MTRN evidence snapshot is now registered after SEC index verification. Generated source text, chunks, snapshots and review reports stay under the git-ignored artifacts directory. These are research-prototype outputs, not production financial advice.

## MTRN boundaries

Keep company-level sensitivities at company scope. Do not allocate copper impact to Performance Materials, split precious-metal sensitivities across metals, treat revenue shares as dependency weights, or infer supply relationships from competitor lists. Keep Delta, mine and mill identities separate until resolved. Medical and Life Sciences retain separate source labels. Acquisition events are not continuing supply contracts. Corporate Other is retained for reconciliation, not operational propagation.

## Second-company onboarding

Verify the new company's CIK; register its filing independently; curate entities and claims into its own namespace; validate and serialize its snapshot. Shared material labels never imply intercompany trade. DOW is not preloaded or assigned any MTRN facts. A second company's missing fields must remain missing. Synthetic tests exercise company isolation without inventing DOW disclosures.

## Next implementation batch

Steps 7–8 are complete. Step 9 will add historical filing ingestion and comparisons; its temporal boundaries are documented in `graph_explorer.md`. The legacy synthetic graph is unchanged and is not loaded by the evidence graph repository.

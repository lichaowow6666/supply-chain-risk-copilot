# Company onboarding and filing history — step 9

The workbench accepts one company name or ticker at a time. **Prepare company & scan** resolves its SEC identity, downloads three completed annual reporting periods and related 10-Qs plus available newer quarters, indexes the documents, builds individual filing graphs and starts Tab 1 discovery. Completed, integrity-checked local preparations are reused. Failed preparations preserve downloads and completed extraction work for retry; they do not report success. SEC metadata is refreshed daily or using **Check SEC for new filings**.

DOW was validated end to end: 14 filings (three 10-Ks and eleven 10-Qs), 14 dated graph snapshots, four annual material exposures, and a successful natural-gas stress test using existing local market history. The collection covers annual periods 2023–2025 and quarterly filings through June 2026. No additional company facts were borrowed from MTRN. The previously curated MTRN snapshot remains intact.

## Using the result

1. Start `venv/bin/streamlit run app.py`.
2. Enter a company name or ticker in the sidebar and select **Prepare company & scan**. DOW is already prepared locally.
3. Tab 1 displays ranked exposures and available market-data matches. Review source excerpts and baseline provenance.
4. Tab 2 runs the existing financial stress model. Spend, hedging and pass-through inputs remain explicit user scenario assumptions.
5. Tab 3 selects individual annual/quarterly snapshots. **Entity history across annual and quarterly filings → Show filing history** displays financial trends and dated relationships.

Historical views separate annual, quarterly, cumulative six-/nine-month and balance-sheet instant observations. Users can choose earliest collected or latest available disclosures. Source accession and filing date remain visible. Absence from a quarterly filing is not treated as disappearance of an entity or termination of a relationship. Name changes and segment reorganizations are not silently merged.

## Storage and extraction

Local, git-ignored storage:

- `sec_filings/companies/<CIK>/<form>/<accession>/`: original SEC document, prepared blocks, exact inline-XBRL projection, model candidates and validated extraction.
- Company directory: download manifest, SEC company facts and preparation record.
- `artifacts/filings_index.sqlite`: document/company/accession-scoped full-text chunk index.
- `artifacts/evidence_graph/<CIK>/<snapshot>/`: immutable evidence graph bundles.

The SEC client follows submission-history pagination, retains amendments separately, checks company/form/period metadata, rate-limits requests and verifies local file hashes. The request structure follows the [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces). The configured research contact is local; it is not embedded in source code. `SEC_USER_AGENT` or `SEC_CONTACT_EMAIL` can configure another installation.

Chunking and full-text indexing are deterministic and require no embedding model. The configured LLM extracts narrative entities, relationships and qualitative exposure ranking; source quotations and references are checked. These records are explicitly **inferred**, automatically validated and not human-reviewed. Abstract risk/mechanism labels are model interpretations. Graph coverage is selective and its gaps are visible. Exact table-cell evidence can be recovered when the model added a verified column-header prefix; arbitrary paraphrases are rejected.

Numerical observations are parsed directly from inline XBRL, preserving registrant identity, period, dimensions, units, scale and sign. Computation stays in Python. Annual baseline values use comparable consolidated contexts; derived values retain definitions and inputs. DOW currently has no accepted comparable Operating Income, Total Debt or Net Debt baseline in this adapter, so those outputs are omitted. The adapter does not invent values for unsupported taxonomies or currencies; financial stress currently requires USD values. A company with fewer available annual reports is explicitly identified.

The existing shock, 30-day horizon, 95% coefficient, pass-through, tax and cash/debt assumptions are preserved. Fixed example financial fallbacks were deliberately removed. Missing market history now fails explicitly, rather than producing a silent zero impact. Only discovered materials with actual local futures history are selectable; no synthetic or guessed proxy was introduced. Market-history freshness is shown when stale. Graph overlays indicate potential exposure, not allocated monetary losses.

## Validation and changed files

Commands executed:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pe_copilot.filings --company DOW
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pe_copilot.filings --company DOW --extract-latest
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -p test_company_preparation.py -v
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -v
git diff --check
```

**Final results: 76 tests passed; core imports and whitespace checks passed.** DOW’s natural-gas overlay reached nine entities through eight evidenced relationships.

Additional inline Python/Streamlit AppTest checks exercised the real cached DOW preparation, automatic Tab 1 scan, Tab 2 execution, baseline-scoped graph highlighting and historical UI. Live SEC collection and configured LLM extraction were exercised. Existing market data was used; no fresh market-data download was required. Browser visual QA for this step was not performed. Legacy connectivity scripts were not run; they are not assertion-based tests.

Changed files for this step:

- `.gitignore`: excludes company-scoped raw SEC downloads.
- `app.py`: company input and automatic preparation/scan workflow.
- `scripts/ingest_10k.py`: replaces the hard-coded single-company script with the shared CLI.
- `pe_copilot/filings/__init__.py`, `__main__.py`: package and CLI.
- `pe_copilot/filings/sec.py`: SEC resolution, history, downloads, manifests and reuse.
- `pe_copilot/filings/financials.py`: deterministic inline-XBRL financial extraction.
- `pe_copilot/filings/extraction.py`: company-neutral narrative extraction and evidence validation.
- `pe_copilot/filings/index.py`: local document-scoped full-text index.
- `pe_copilot/filings/graph.py`: per-filing graph compilation and financial provenance.
- `pe_copilot/filings/service.py`: resumable end-to-end preparation.
- `pe_copilot/filings/history.py`: reporting-duration and disclosure-version-aware history.
- `pe_copilot/agents/fundamental_agent.py`: dynamic companies, feedstock/energy support and prepared financials.
- `pe_copilot/agents/market_agent.py`: absent-input safeguards, parameterized lookup and session-safe reproducibility.
- `pe_copilot/orchestrator.py`: preparation and historical query routes.
- `pe_copilot/supply_chain/preparation.py`: quarterly primary submissions and XBRL projections.
- `pe_copilot/supply_chain/graph_repository.py`: explicit operation-to-product physical paths.
- `pe_copilot/supply_chain/explorer.py`: dated automated snapshots and instant observations.
- `pe_copilot/ui/company_setup.py`: single-company state isolation and progress.
- `pe_copilot/ui/tabs.py`: preparation notes, baseline scope and stress integration.
- `pe_copilot/ui/graph_explorer.py`: filing history controls and automatic-extraction labels.
- `pe_copilot/ui/assets/graph_explorer.html`: extraction provenance labels.
- `tests/test_company_preparation.py`: 19 acquisition, extraction, period, isolation and financial tests.
- `tests/test_fundamental_agent.py`: updates the obsolete single-ticker assertion.
- `docs/company_onboarding.md`, `docs/knowledge_graph_foundation.md`, `docs/graph_pipeline.md`, `docs/graph_explorer.md`: implementation status and handoff.

No commit, push or public deployment was performed. Chatbot natural-language routing and graph loss allocation remain **in development** and were not part of this step.

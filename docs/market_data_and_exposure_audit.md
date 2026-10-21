# Daily futures data and specific-material exposure audit

Implemented and validated September 22, 2026. This note covers only the market-refresh and Tab 1 changes; earlier uncommitted graph/onboarding work is preserved.

## Outcome

All eleven configured Yahoo futures series have completed daily prices through September 21, 2026. Existing six series were updated from August 10; silver, aluminum, Brent crude, heating oil and gasoline were added. Local downloads remain ignored by Git.

The GLW annual audit now identifies Platinum, Rhodium, Helium and Lithium instead of abstract material categories. Platinum is directly selectable for Tab 2. Rhodium, helium and lithium have no configured direct series and are displayed as uncovered, without fabricated proxies. Platinum/rhodium disclosures distinguish reused production equipment, depletion, and a leased precious-metals pool fair-value hedge. They do not establish material-specific operating hedge percentages or annual purchase spend.

DOW regression scan produced thirteen specific inputs; Natural Gas and Platinum matched available futures histories and passed stress calculation. Not every identified input has a direct Yahoo futures series.

## Data refresh behavior

- `pe_copilot.market_data` is shared by the application and `scripts/ingest_futures.py` compatibility entry point.
- Each app execution checks persistent per-contract freshness. Each contract is attempted once per Asia/Shanghai calendar day; a manual Retry button and `--force` permit same-day retries.
- New series load approximately three years; existing series re-fetch seven overlapping days for corrections and preserve earlier history. Existing history is not truncated.
- Each contract upsert is transactional. A failed, empty or regressed response preserves previous prices. New data retains provider precision instead of rounding every commodity to two decimals.
- Today's provisional daily candle, duplicate dates and invalid/nonpositive prices are excluded. The source, quote units, check date and latest session are recorded separately from prices. These are daily continuous/front-contract benchmarks, not delivered company purchase prices or independently verified exchange settlements.
- A file lock coordinates refresh writers. Existing DuckDB access conflicts surface as a refresh warning rather than deleting data. Futures freshness is shown in the sidebar; more than five calendar days without a completed session is flagged. This is a simple freshness heuristic, not an exchange-holiday calendar.
- Saved Tab 2 results are marked outdated when refresh metadata or scenario inputs change. Stress results display their contract, quote unit and market date. Financial formulas and scenario assumptions are unchanged.

A local Codex scheduled task, **Refresh commodity futures daily**, is active at 09:00 local time (Asia/Shanghai on this machine). It uses the same command, retries failed attempts once and stays quiet on routine success. The computer and desktop app must be running for scheduled local work; the application's own freshness check also runs whenever the copilot is used. See [official scheduling documentation](https://learn.chatgpt.com/docs/automations?surface=app).

Manual refresh:

```bash
PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m pe_copilot.market_data --force
```

Provider catalog: [Yahoo commodity futures](https://finance.yahoo.com/markets/commodities/). Contract metadata is defined in `CATALOG`; no automatic group-to-futures proxy mappings are used.

## Exposure logic

Tab 1 now performs a separate audit of the latest prepared annual filing, with its own version/source-hash cache. It does not reuse the graph's short single-quotation exposure records or rebuild the fourteen filing graphs.

The audit retrieves complete relevant blocks plus adjacent context from raw-material, risk, derivatives, accounting and commitment disclosures. The model returns separately cited operational role, risk, hedging, mitigation and accounting descriptions. Code rejects broad category labels, missing material-name evidence, nonexistent block IDs and nonverbatim quotations. A valid quoted operating role is required to admit a material. Missing risk evidence becomes Unknown; missing hedging evidence never becomes an assertion that the material is unhedged. The UI displays full source blocks, dates, source links and which fields they support. Short headings can include surrounding context with its own locator.

High/Medium/Low remain explicitly labelled qualitative model assessments, not reported or computed sensitivities. Within a band, ordering is alphabetical. Group-level disclosures can contextualize specifically named members, but must not allocate group balances, sensitivity or hedge ratios. Quote validation verifies provenance, not the semantic truth of every model interpretation; review the displayed source text. This audit is scoped to the latest annual filing, not a synthesized latest-quarter hedge position. Its date is displayed.

Existing sessions with the old category-based ranking upgrade on the next render. Prepared company scans use the new audit; cached annual audits are reused. Graph snapshots remain their separate dated evidence representation, and an unmatched Tab 2 material does not gain invented graph edges.

## Files changed in this request

| File | Change |
| --- | --- |
| `.gitignore` | Exclude local database refresh lock. |
| `app.py` | Daily check, freshness/retry UI and market revision tracking. |
| `scripts/ingest_futures.py` | Replace destructive reload with shared refresh entry point. |
| `pe_copilot/market_data.py` | New catalog, downloader, validation, transactional upsert and CLI. |
| `pe_copilot/agents/market_agent.py` | Add market date, symbol and unit to simulation output. |
| `pe_copilot/filings/exposures.py` | New specific-material audit and field-level source validation/cache. |
| `pe_copilot/filings/service.py` | Route company scans through the independent audit. |
| `pe_copilot/ui/tabs.py` | Full evidence packets, gaps, coverage, legacy-session migration and outdated-result warning. |
| `tests/test_market_refresh.py` | Synthetic refresh integrity, daily gating and partial-failure tests. |
| `tests/test_material_audit.py` | Synthetic field-citation, generic-name and missing-evidence tests. |
| `docs/market_data_and_exposure_audit.md` | This implementation and validation record. |

Local-only changes: futures DuckDB data/refresh metadata, cached GLW/DOW annual audits, and the Codex scheduled task. No commit or push was made.

## Validation

- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -v`: **85 passed** (nine new tests).
- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -c "from pe_copilot.agents import MarketAgent, FundamentalAgent; from pe_copilot.analytics import run_stress_test"`: passed.
- `git diff --check`: passed.
- Live `venv/bin/python -m pe_copilot.market_data --force`: eleven successful contracts, all dated September 21; no refresh warnings. The sandbox-only attempt could not reach the configured network; the authorized network run succeeded.
- Temporary local integration harness: real cached GLW annual scan, Platinum selection, actual refreshed-price stress, Streamlit AppTest rendering of all three tabs and simulated Tab 2 Execute click: passed. This verifies widget execution, not pixel-level browser appearance.
- Real DOW annual audit and direct-series stress calculation: passed.
- Future unattended schedule execution cannot be tested before its first scheduled run; task creation and active status were verified.

# Selected-report analysis and futures-first discovery

Implemented September 22, 2026. This supersedes the latest-annual-only and all-materials-first behavior described in the earlier market audit note.

## User flow

Tab 1 displays twelve financial overview cards: revenue, gross profit, operating income, net income, cash, total debt, assets, equity, operating cash flow, capital expenditure, free cash flow and working capital. Missing values display **Unavailable**, never zero. Reported/derived labels and dates remain visible; detailed provenance is still available below the cards.

The **Analysis report** selector lists the company's prepared 10-K, 10-Q and amendment filings with their period and filing dates. Latest annual remains the initial default; choosing a different report immediately changes the financial baseline, exposure source and graph scope. Prior selections and simulation results are cleared. No annual narrative is substituted when a quarter contains sparse evidence.

Discovery first searches the chosen report for commodities actually present in the local market database. Those commodity names also prioritize source-block retrieval. Only validated operating-input evidence is accepted; an incidental mention or available price series alone does not establish exposure. If no covered input is validated, a second pass searches for other specific inputs and marks them as lacking futures coverage. Covered matches are shown when present; uncovered materials are the fallback. This changes discovery priority, not the meaning of the qualitative risk bands. The cache includes filing hash, audit version and market universe, so adding market coverage invalidates the previous search result.

**Proceed to Tab 2 — Stress Test** now sets the active Streamlit tab directly and retains the commodity selection. It uses the installed Streamlit tab-state API, without browser-script injection.

## Quarterly period handling

Quarterly reports often include three-month and year-to-date income figures, while cash flows are year-to-date. For comparable overview/stress pools, quarterly analysis uses the longest current-fiscal-year revenue duration ending on the selected report date (60–300 days), selects matching income and cash-flow durations, and uses period-end balances. The exact date range and **Year to date** label are displayed. Missing matching facts remain unavailable; values are not annualized and prior-year figures are not substituted. Derived metrics still require matching periods.

In quarterly mode, Tab 2 explicitly asks for **Year-To-Date Spend**, rather than applying an annual cost amount to a partial-year baseline. The existing shock, pass-through, tax and debt formulas remain unchanged. Internally the legacy numeric key `annual_spend` carries the user-entered period spend; the UI and graph stress-context horizon identify the selected basis. Annual-mode behavior is unchanged. Spending and hedge coverage remain scenario assumptions, not extracted financial facts.

## Files changed in this request

- `app.py`: stateful tabs, selected-report discovery routing, report-selector integration.
- `pe_copilot/ui/company_setup.py`: report selection, financial/source context and reset of prior analysis state.
- `pe_copilot/ui/tabs.py`: financial cards, coverage labels, direct navigation and explicit quarterly spend basis.
- `pe_copilot/orchestrator.py`: route report accession and actual market universe to company scanning.
- `pe_copilot/filings/service.py`: list reports and load the selected report's financial projection and graph snapshot.
- `pe_copilot/filings/financials.py`: consistent quarterly YTD context selection and period metadata.
- `pe_copilot/filings/exposures.py`: targeted market-first retrieval, uncovered fallback, report-scoped cache and bounded structured-output retry.
- `tests/test_report_selection.py`: synthetic period, search priority, fallback, empty-quarter, accession isolation, report-reset and navigation tests.
- `docs/report_selection_and_market_first.md`: this implementation and validation record.

Local GLW audit caches were refreshed for the selected annual and quarterly reports. Model-response diagnostics, if needed, remain inside the ignored company filing folder. No commit or push was made.

## Validation

- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -m unittest discover -s tests -v`: **92 tests passed**, including seven added tests.
- `PYTHONDONTWRITEBYTECODE=1 venv/bin/python -c "from pe_copilot.agents import MarketAgent, FundamentalAgent; from pe_copilot.analytics import run_stress_test"`: passed.
- `git diff --check`: passed.
- Live GLW selected-report scans: 2025 annual report yielded covered Platinum and Natural Gas; June 2026 quarterly report yielded covered Natural Gas. Silver and gasoline were searched but no unsupported exposure was manufactured. Financial evidence and exposure accessions matched each selected filing.
- Local Streamlit AppTest integration: visible overview, annual-to-quarterly switch, removal of old simulations, direct Tab 2 navigation, actual market-history stress, and matching selected graph snapshot all passed. This verifies application/widget behavior, not a pixel-level browser review.
- Initial live model calls returned malformed JSON; a bounded retry was added. Successful live scans subsequently completed and were cached. Citation validation still applies to parsed output; source quotation checks are not a guarantee of semantic correctness for every model interpretation.

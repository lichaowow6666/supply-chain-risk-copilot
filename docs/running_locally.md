# Reviewing and running the copilot

The [project overview](../README.md), walkthrough video, source code and [saved evaluation results](../evaluations/results/interview_100_latest.json) can be reviewed without installing the application or supplying credentials. The recording demonstrates the analyst workflow; the synthetic evaluation files expose both successful and failed checks.

**A portable clean installation is in development.** The application has been exercised in an existing Python 3.14.2 environment on macOS ARM. The dependency overlays in this repository are not a complete application lock, and a fresh clone does not include prepared company data, market databases or model weights. A Windows installation is not currently supported by the unchanged market refresh module, which imports the Unix `fcntl` locking API.

## Run in an already configured environment

From the repository root, using the existing project environment:

```bash
venv/bin/streamlit run app.py
```

The sidebar chat accepts a company name, ticker or CIK. Company preparation downloads and indexes the supported SEC filings and constructs their evidence graphs. Select a report and materials in Company & Material Discovery, then use Scenario Analysis for explicit price shocks, spend, hedging and pass-through assumptions. Enable **Evaluation** in the sidebar to inspect the saved 100-question benchmark. Per-answer **Inspect answer** opens its route, service activity, quality metrics and timing.

The first live-company preparation requires network access and can take several minutes. The application checks futures data freshness when it runs; a successful daily refresh is reused. Chat, filing extraction and fresh RAGAS judgments call the configured language-model provider and may incur API charges. Downloaded model weights also require local disk space and memory.

## Configuration used by the code

Set values in your own local environment or an untracked `.env` file. Never place credentials in source files or commit that file.

| Variable | Purpose |
|---|---|
| `DEEPSEEK_API_KEY` **or** `OPENAI_API_KEY` | Enables language generation, filing extraction and fresh answer evaluation. If both are set, DeepSeek takes precedence. |
| `COPILOT_CHAT_MODEL` | Optional OpenAI chat/judge model override; default `gpt-4o-mini`. The DeepSeek path uses `deepseek-chat`. |
| `GRAPH_EXTRACTION_MODEL` | Optional filing-extraction model override for the selected provider. |
| `SEC_USER_AGENT` | Your descriptive SEC request identity, including your contact information. `SEC_CONTACT_EMAIL` is an alternative used by the code's default identity. |
| `PROXY` | Optional configured proxy for SEC requests and market downloads. Leave unset when unnecessary. |
| `HF_MIRROR` | Optional Hugging Face endpoint override for local model retrieval. Leave unset when unnecessary. |

The model-provider API receives the question and the supplied evidence needed for the operation. The published evaluation datasets use fictional companies and synthetic disclosures; the live-company flow uses the selected company's downloaded filing evidence.

## Dependencies and local models

The core code imports Streamlit, Plotly, pandas, NumPy, DuckDB, NetworkX, Beautiful Soup, Requests, python-dateutil, python-dotenv, yfinance and the OpenAI SDK. The current conversation path also uses LangGraph and its SQLite checkpointer. Chroma is a guarded legacy retrieval integration; new company preparation uses the report-scoped SQLite index.

- [`requirements-chat.txt`](../requirements-chat.txt) records the tested chat, embedding and evaluation overlay, including LangGraph, sentence-transformers, PyTorch, ONNX Runtime and RAGAS.
- [`requirements-forecast.txt`](../requirements-forecast.txt) records the optional XGBoost and PyTorch forecasting overlay. XGBoost may require a separately configured OpenMP runtime on macOS; see the [forecasting notes](forecasting_workbench.md#runtime).
- [`scripts/download_chat_model.py`](../scripts/download_chat_model.py) installs pinned BGE-M3 weights and optionally the English ONNX cross-encoder. It creates machine-local configuration under ignored `artifacts/chat_models/`.

In an environment where these dependencies are already configured, the explicit model bootstrap is:

```bash
venv/bin/python scripts/download_chat_model.py --reranker
```

This downloads models; it is not a complete application installation command. Missing dense or reranking models are reported through retrieval diagnostics, and lexical retrieval can remain available. That fallback is not equivalent to demonstrating the full hybrid pipeline. The experimental fine-tuned checkpoint is separate from the base-model download and is not required for normal use; its dataset, training configuration and comparison results are published under [`evaluations/`](../evaluations/).

## What stays on the reviewer's machine

Company downloads, extracted reports, SQLite indexes/checkpoints, the DuckDB market database, graph artifacts, model weights, scenario artifacts and local feedback are generated or obtained locally. Git excludes these files, credentials and identifying local configuration. A reviewer should prepare their own company workspace rather than expect bundled live-company databases.

For offline inspection, the repository includes synthetic evidence and expected answers in [`evaluations/report_scope_v1.json`](../evaluations/report_scope_v1.json) and [`evaluations/interview_100_v1.json`](../evaluations/interview_100_v1.json), plus recorded outputs and scores. Reading those results does not require API calls. Re-running live model evaluation is a separate operation, and published scores should not be assumed reproducible with a different provider, model or environment.

The next packaging milestone is a complete tested dependency lock, placeholder-only configuration template, synthetic UI launch mode and a clean-environment smoke check. Until that exists, the walkthrough and recorded evidence are the quickest review path; this guide documents the current environment boundary rather than promising a verified one-command installation.

## Optional command-line utilities

Run these from the repository root in the configured environment:

```bash
venv/bin/python -m scripts.ingest_10k --company TICKER
venv/bin/python -m scripts.ingest_futures
venv/bin/python -m scripts.build_graph
```

The ingestion commands retrieve external data. The graph generator writes the legacy synthetic example at `examples/supply_chain_graph.json`; it is separate from the report-scoped DOW workflow in the video.

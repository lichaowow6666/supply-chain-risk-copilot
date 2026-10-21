# Project documentation

Start with the [project overview](../README.md) for capabilities, business questions and a short walkthrough.

| Document | What it explains |
|---|---|
| [Technical design and measured results](technical_overview.md) | Architecture, report scoping, hybrid retrieval, GraphRAG, financial assumptions, forecasting and fine-tuning |
| [Evaluation methodology](evaluation_methodology.md) | The 100-question dataset, RAGAS definitions, denominators, workflow contracts, latency and recorded failures |
| [Answer inspection](answer_inspection.md) | Per-answer routing, invoked/reused services, grounded claims and timing boundaries |
| [Feedback and evaluation](feedback_and_evaluation.md) | Explicit ratings, privacy, applicability rules and the improvement process |
| [Architecture diagram](diagrams/copilot-architecture.jpg) | Overall component relationships |
| [Workflow diagram](diagrams/copilot-workflow.jpg) | Disclosure-to-scenario analyst journey |
| [DOW walkthrough video](demo/DOW-copilot-walkthrough.mp4) · [Chapters and assumptions](walkthrough.md) | A 90-second cursor-led DOW journey through materials, forecasts, financial impact, graph and evaluation |
| [Local setup status](running_locally.md) | Existing-environment launch, configuration, dependencies and clean-install limitations |

Earlier domain-design notes remain available for implementation detail: [SEC onboarding](company_onboarding.md), [evidence graph](knowledge_graph_foundation.md), [graph preparation](graph_pipeline.md), [graph exploration](graph_explorer.md), [scenario coordination](scenario_orchestrator.md), [financial attribution](scenario_attribution_review.md), [forecasting](forecasting_workbench.md), and [LSTM and risk distributions](lstm_company_risk.md). These describe their respective implementation stages; use the current overview and recorded evaluation for present capabilities and limitations.

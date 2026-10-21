# DOW in 90 seconds

[Watch the cursor-led walkthrough](https://github.com/lichaowow6666/supply-chain-risk-copilot/raw/refs/heads/main/docs/demo/DOW-copilot-walkthrough.mp4).

**Investor question: could changing energy costs compress DOW's margin?** The recording follows actual clicks, model switches and graph selections in the application. Loading waits are cut and some interactions are modestly accelerated; there are no frozen-slide sequences. The same process can support a manager's purchasing, pricing or risk discussion.

| Start | What happens |
|---|---|
| 0:00 | Ask the chat to open DOW; review the selected filing, financial overview and three ranked materials. |
| 0:11 | Select natural gas and crude oil and open Scenario Analysis. |
| 0:19 | Switch XGBoost and LSTM forecasts; inspect historical model-evaluation results. |
| 0:37 | Set price shocks, spending, unhedged exposure and pass-through assumptions. |
| 0:52 | Run the scenario and reconcile the financial contributions. |
| 1:01 | Pin graph entities and trace dependencies, financial effects and uncertainty. |
| 1:15 | Ask what the scenario means; inspect the answer's routing and the separate Evaluation dashboard. |

## Evidence and assumptions

Company evidence comes from the prepared **DOW 2025 10-K**, filed February 3, 2026. The recording reuses the local company workspace and stored futures history, whose displayed reference date is **October 4, 2026**. It does not download a new market session or resume scheduled refreshes.

The financial result uses **illustrative user inputs**, not disclosed DOW purchasing or hedge positions. USD amounts below are millions.

| Material | Annual spend | Price shock | Unhedged | Pass-through | Net COGS change |
|---|---:|---:|---:|---:|---:|
| Natural gas | 50 | −10% | 80% | 50% | **−2** |
| Crude oil | 100 | +20% | 80% | 50% | **+8** |
| **Combined** | | | | | **+6** |

`Net COGS change = spend × price shock × unhedged fraction × (1 − pass-through)`

The 30-trading-session forecast horizon does **not** prorate annual spend. This is a conditional sensitivity calculation, not a forecast of realized DOW earnings. The extracted operating-income baseline is unavailable; a modeled change does not establish an absolute stressed operating-income value. Graph links do not establish product-level dollar allocations.

The final **100-question Evaluation view is a separate saved synthetic development benchmark**, not 100 DOW questions or evidence of user adoption. The DOW answer and routing shown are actual outputs from this recording session. No fresh RAGAS judgment is shown in this shorter edit.

[Technical design](technical_overview.md) · [Evaluation methodology](evaluation_methodology.md) · [Local setup status](running_locally.md)

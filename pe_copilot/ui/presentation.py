"""Pure presentation builders shared by the Streamlit tab renderers."""

from typing import Any, Dict, Iterable, List, Mapping

import plotly.graph_objects as go


def available_discovered_materials(
    top_materials: Iterable[Mapping[str, Any]], db_metals: Iterable[str]
) -> List[str]:
    """Return discovered material names that have DuckDB market data."""
    discovered_names = [item.get("material", "").title() for item in top_materials]
    available = list(db_metals)
    return [material for material in discovered_names if material in available]


def build_waterfall_message(
    waterfall_summary: Mapping[str, Any], selected_metals: Iterable[str], ticker: str
) -> str:
    """Build the existing user-facing cost-transmission explanation."""
    return f"""
            **Step-by-Step Cost Transmission Math:**
            1. **Total Gross COGS Surge:** Market price shocks applied to assumed spending and unhedged exposure ({', '.join(selected_metals)}) produce **${waterfall_summary.get('Total_Gross_COGS_Surge', 0):+,.2f}M** in the model.
            2. **Assumed Pass-Through:** At **{waterfall_summary.get('Pass_Through_Rate_Pct', 75)}%**, the model offsets **${waterfall_summary.get('Passed_To_Clients', 0):,.2f}M** of this cost. This is not a disclosed contract term.
            3. **Net Absorbed COGS Change:** The model adds **${waterfall_summary.get('Net_COGS_Absorbed', 0):,.2f}M** to {ticker}'s reported COGS and subtracts the same amount from operating income. Revenue remains unchanged in this cost-offset convention.
            """


def build_kpi_card(metric: str, metric_data: Mapping[str, Any], pool: str) -> Dict[str, str]:
    """Construct Streamlit metric arguments without rendering them."""
    # Always reconcile the displayed change with the two displayed endpoints.
    # Some legacy output rows carry an intermediate delta (notably net debt).
    delta = (round(metric_data['stressed'] - metric_data['base'], 2)
             if 'base' in metric_data and 'stressed' in metric_data else metric_data.get('delta', 0.0))
    if pool == "income_statement":
        delta_color = "inverse" if metric in ["COGS"] else "normal"
    elif pool == "balance_sheet":
        delta_color = "inverse" if metric in ["Total Debt", "Net Debt"] else "normal"
    else:
        delta_color = "normal"

    return {
        "label": f"{metric} (Baseline: ${metric_data.get('base', 0):,.1f}M)",
        "value": f"${metric_data.get('stressed', 0):,.2f}M",
        "delta": f"{delta:+,.2f}M",
        "delta_color": delta_color,
    }


def build_history_chart(metal: str, history_df: Any) -> go.Figure:
    """Build the existing historical-price chart."""
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=history_df["Date"],
            y=history_df["Price"],
            mode="lines",
            name=f"{metal} Futures benchmark",
            line=dict(color="#1f77b4", width=2),
        )
    )
    figure.update_layout(
        title=f"{metal} Futures benchmark history",
        xaxis_title="Date",
        yaxis_title="Price ($)",
        height=280,
        margin=dict(l=10, r=10, t=30, b=10),
    )
    return figure


def build_stochastic_chart(metal: str, material_data: Mapping[str, Any]) -> go.Figure:
    """Build the existing stressed-path chart and confidence-band display."""
    projection = material_data.get("stochastic_projection", {})
    days = projection.get("days", [])
    figure = go.Figure()
    if not days:
        return figure

    figure.add_trace(
        go.Scatter(
            x=days + days[::-1],
            y=projection["upper_band"] + projection["lower_band"][::-1],
            fill="toself",
            fillcolor="rgba(220, 53, 69, 0.15)",
            line=dict(color="rgba(255,255,255,0)"),
            name="95% Monte Carlo Band",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=days,
            y=projection["baseline_path"],
            mode="lines",
            name="Baseline Trajectory",
            line=dict(color="gray", dash="dash"),
        )
    )
    figure.add_trace(
        go.Scatter(
            x=days,
            y=projection["stressed_path"],
            mode="lines",
            name=f"Stressed (+{material_data.get('shock_pct', 0)}%)",
            line=dict(color="firebrick", width=2.5),
        )
    )
    figure.update_layout(
        title=f"{metal} 30-Day Forward Stochastic Scenario",
        xaxis_title="Forward Trading Days",
        yaxis_title="Price ($)",
        height=300,
        margin=dict(l=10, r=10, t=30, b=10),
    )
    return figure

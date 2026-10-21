"""Combined Plotly market chart with continuous future-price selection."""
from functools import lru_cache
from pathlib import Path
import math

import streamlit as st
from plotly.offline import get_plotlyjs


@lru_cache(maxsize=1)
def component():
    # The installed Plotly bundle is served locally by Streamlit. No CDN request
    # or separate chart is needed to capture clicks between plotted points.
    return st.components.v2.component(
        'scenario_market_chart',
        html='<div class="scenario-market-chart" style="width:100%;height:385px;position:relative"></div>',
        js=get_plotlyjs() + '\n' + Path(__file__).with_name('assets').joinpath('price_picker.js').read_text(),
        isolate_styles=False,
    )


def apply_choice(component_key, price_key, shock_key, reference):
    value = st.session_state.get(component_key, {}).get('price')
    if (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and math.isfinite(reference)
            and reference > 0 and value >= reference * .001):
        st.session_state[price_key] = float(value)
        st.session_state[shock_key] = float((value / reference - 1) * 100)


def render_price_picker(forecast, model, price_key, shock_key, scope, compare=()):
    from .forecasting import combined_chart

    reference = forecast['reference_price']
    key = 'forecast_picker_' + scope
    figure = combined_chart(forecast, model, st.session_state[price_key], compare)
    # Retain a generous price area beyond the band. Manual target input remains
    # unbounded; an entered extreme value expands the chart on the next rerun.
    values = [reference, st.session_state[price_key]]
    for name in dict.fromkeys([model, *compare]):
        row = forecast['models'][name]
        values.extend(row['lower80'] + row['upper80'] + row['path'])
    visible_history = max(60, 3 * forecast['horizon'])
    values.extend(item['price'] for item in forecast['history'][-visible_history:])
    low, high = min(values), max(values)
    padding = max((high - low) * .25, reference * .3)
    figure.update_layout(height=385, yaxis_range=[max(reference * .001, low - padding), high + padding])
    component()(
        data={
            'figure': figure.to_plotly_json(),
            'reference': reference,
            'price': st.session_state[price_key],
            'futureStart': forecast['history'][-1]['date'],
            'futureEnd': forecast['future_dates'][-1],
            'horizon': forecast['horizon'],
            'unit': forecast['market']['unit'],
        },
        key=key,
        height=395,
        on_price_change=lambda: apply_choice(key, price_key, shock_key, reference),
    )

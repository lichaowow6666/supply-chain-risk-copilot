"""Show small-sample company risk cases without claiming calibrated probabilities."""
import streamlit as st
import plotly.graph_objects as go


def render_company_risk(execution, current):
    st.subheader('4. Company risk distribution')
    risk = execution.simulation.get('company_risk', {})
    if risk.get('status') != 'ok':
        st.info(risk.get('reason', 'Rerun this scenario to prepare its risk distribution.'))
        return
    if not current:
        st.warning('This distribution belongs to the saved run. Rerun the scenario to apply changed assumptions.')
    st.caption(f"{risk['case_count']} matched historical cases · {risk['horizon']}-session price horizon · {risk['spend_basis']} spend · USD millions. Equal weights; no independent random-price draws.")
    st.dataframe([{'KPI':r['metric'], 'Reported / derived baseline':r['baseline'],
                   'P10':r['p10'], 'Median':r['median'], 'P90':r['p90'], 'Mean':r['mean']}
                  for r in risk['kpis']], hide_index=True, width='stretch')
    choices = [(r['pool'],r['metric']) for r in risk['kpis']]
    if choices:
        preferred = next((i for i,v in enumerate(choices) if v[1]=='Operating Income'),0)
        pool, metric = st.selectbox('Explore a financial outcome',choices,index=preferred,
                                    format_func=lambda v:v[1],key='risk_metric_'+risk['risk_id'])
        values = [c['stressed_pools'][pool][metric]['stressed'] for c in risk['cases']]
        base = execution.baseline['pools'][pool][metric]
        selected = execution.simulation['stressed_pools'][pool][metric]['stressed']
        fig = go.Figure(go.Histogram(x=values, nbinsx=min(12,risk['case_count']),name='Historical error cases',
                                     marker_color='#4a90e2',histnorm='percent'))
        fig.add_vline(x=base,line_dash='dot',line_color='#64748b',annotation_text='Filing baseline')
        fig.add_vline(x=selected,line_color='#e58c45',annotation_text='Your stress scenario')
        fig.update_layout(height=330,xaxis_title=metric+' (USD millions)',yaxis_title='Share of cases (%)',
                          margin=dict(t=35,b=35,l=15,r=15))
        st.plotly_chart(fig,width='stretch',key='risk_histogram_'+risk['risk_id'])
        left,right = st.columns(2)
        with left:
            threshold = st.number_input('Financial threshold ($M)',value=float(base),key='risk_threshold_'+risk['risk_id']+'_'+metric)
        with right:
            direction = st.selectbox('Count outcomes', ['Below threshold','Above threshold'],key='risk_direction_'+metric)
        count = sum(v<threshold if direction=='Below threshold' else v>threshold for v in values)
        st.write(f"{count} / {len(values)} historical cases ({count/len(values):.1%}) {direction.lower()}")
    with st.expander('Downside contributions and distribution evidence'):
        st.write('Selected-material risk using active forecast models and fixed user assumptions. Historical case frequencies are not calibrated future probabilities. P10/P90 describe modeled KPI levels, not confidence limits.')
        st.metric('Mean absorbed cost in the worst 20% of cases',f"${risk['worst_20pct_mean_cost']:,.2f}M")
        st.dataframe([{'Material':r['material'],'Mean absorbed cost ($M)':r['mean_absorbed_cost'],
                        'Contribution to worst-20% mean cost ($M)':r['worst_20pct_mean_absorbed_cost']}
                       for r in risk['material_contributions']],hide_index=True,width='stretch')
        st.caption('Materials are attributed within the same company downside cases. Positive means higher costs; negative means savings. Fractional boundary weights give exactly 20%; totals can differ by cents due to financial rounding.')
        for warning in risk['warnings']:
            st.caption(warning)
        st.json({k:v for k,v in risk.items() if k not in ('cases','kpis','material_contributions')},expanded=False)
        st.download_button('Download joint risk cases',data=__import__('json').dumps(risk,indent=2),
                            file_name=execution.request.ticker.lower()+'_company_risk.json',mime='application/json')

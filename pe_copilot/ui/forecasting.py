"""Price-first workbench: frozen numerical forecasts and explicit shock inputs."""
import streamlit as st
import plotly.graph_objects as go
from pe_copilot.scenario_support import describe_market_snapshot
from .navigation import render_canvas_anchor


def combined_chart(forecast, model, target_price, compare=()):
    history=forecast['history']; dates=forecast['future_dates']; row=forecast['models'][model]
    figure=go.Figure()
    figure.add_trace(go.Scatter(x=[r['date'] for r in history],y=[r['price'] for r in history],
                               name='Observed futures benchmark',line=dict(color='#7187a3')))
    if row['lower80']:
        figure.add_trace(go.Scatter(x=dates+dates[::-1],y=row['upper80']+row['lower80'][::-1],
                                   fill='toself',fillcolor='rgba(74,144,226,.14)',line=dict(color='rgba(0,0,0,0)'),
                                   name='80% empirical range',hoverinfo='skip'))
    for key in dict.fromkeys([model,*compare]):
        candidate=forecast['models'][key]
        figure.add_trace(go.Scatter(x=[history[-1]['date'],*dates],
                                   y=[forecast['reference_price'],*candidate['path']],
                                   mode='lines',line=dict(dash='dot' if key==model else 'dash',width=3 if key==model else 1, color='#2563eb' if key==model else '#94a3b8'),
                                   name=candidate['label']))
    for label,values in (('Central endpoint',row['path']),('Lower endpoint',row['lower80']),('Upper endpoint',row['upper80'])):
        if values:
            figure.add_trace(go.Scatter(x=[dates[-1]],y=[values[-1]],mode='markers',name=label,
                                       customdata=[['forecast_endpoint']],marker=dict(size=7,color='#2563eb'),showlegend=False))
    figure.add_trace(go.Scatter(x=[history[-1]['date'],dates[-1]],y=[forecast['reference_price'],target_price],
                               mode='lines+markers',line=dict(color='#e58c45',dash='dash'),
                               name='Selected scenario price'))
    figure.add_vline(x=history[-1]['date'],line_dash='dash',line_color='#aaa')
    visible_history=max(60,3*forecast['horizon'])
    figure.update_layout(height=350,margin=dict(l=75,r=20,t=25,b=15),
                         yaxis=dict(title=forecast['market']['unit'],automargin=True),xaxis_title=None,
                         xaxis_range=[history[-min(len(history),visible_history)]['date'],dates[-1]],
                         legend=dict(orientation='h',y=-.25),dragmode='pan',
                         uirevision=forecast['forecast_id'])
    return figure


def _set_target(price_key, shock_key, reference, price):
    st.session_state[price_key]=float(price)
    st.session_state[shock_key]=float((price/reference-1)*100)


def _sync_price(price_key,shock_key,reference):
    _set_target(price_key,shock_key,reference,st.session_state[price_key])


def _sync_shock(price_key,shock_key,reference):
    st.session_state[price_key]=reference*(1+st.session_state[shock_key]/100)


def render_forecast_controls(orchestrator, materials):
    render_canvas_anchor('forecast')
    st.markdown('**Material prices**')
    horizon=st.selectbox('Price horizon (trading sessions)',[5,20,30,60],index=2,key='forecast_horizon')
    try:
        histories=orchestrator.market_agent.capture_price_snapshot(tuple(materials))
        snapshot=describe_market_snapshot(histories)
    except (ValueError,RuntimeError,OSError) as error:
        st.error(f'Market inputs unavailable ({type(error).__name__}); refresh data before choosing a price scenario.')
        return None
    if st.button('Prepare forecasts',key='forecast_prepare'):
        with st.status('Forecasting Agent: validating models…',expanded=True) as status:
            try:
                prepared=orchestrator.prepare_forecasts(materials,horizon,on_step=status.write)
                st.session_state['forecast_prepared']=prepared
                status.update(label='Forecasts and historical evaluation ready',state='complete',expanded=False)
            except (ValueError,RuntimeError,OSError) as error:
                st.error(f'Forecast preparation failed ({type(error).__name__}). Manual price scenarios remain available.')
                status.update(label='Forecast unavailable',state='error')
    selections={}
    for material in materials:
        frame=histories[material]; ref=float(frame['Price'].iloc[-1]); info=snapshot['series'][material]
        scope=material+'_'+info['fingerprint'][:12]+'_'+str(horizon)
        price_key='forecast_price_'+scope; shock_key='forecast_shock_'+scope
        if price_key not in st.session_state:
            _set_target(price_key,shock_key,ref,ref)
        forecast=st.session_state.get('forecast_prepared',{}).get(material)
        if forecast and (forecast['market']['fingerprint']!=info['fingerprint'] or forecast['horizon']!=horizon):
            forecast=None
        st.markdown('**'+material+'**')
        st.caption(f"{info['ticker']} · {info['unit']} · reference {ref:,.4f} · {info['end'][:10]}")
        model=None
        if forecast:
            keys=list(forecast['models'])
            model=st.selectbox('Active forecast model — '+material,keys,index=keys.index(forecast['recommended']),
                               format_func=lambda k:forecast['models'][k]['label'],key='forecast_model_'+scope)
            comparison_key='forecast_compare_'+scope
            previous=st.session_state.get(comparison_key,['naive'] if model!='naive' else [])
            st.session_state[comparison_key]=[k for k in previous if k in keys and k!=model]
            compare=st.session_state[comparison_key]
            row=forecast['models'][model]
            from .price_picker import render_price_picker
            render_price_picker(forecast,model,price_key,shock_key,scope,compare)
            with st.expander('Model comparison and evidence — '+material):
                st.multiselect('Reference curves — '+material,[k for k in keys if k!=model],
                               format_func=lambda k:forecast['models'][k]['label'],key=comparison_key)
                rows=[]
                for key,value in forecast['models'].items():
                    rows.append({'Model':value['label'],'Development MAE (quote units)':value['development_mae'],
                                 'Later audit MAE (quote units)':value['audit_mae'],
                                 'Audit coverage (%) of nominal 80% band':value['audit_coverage80']*100 if value['audit_coverage80'] is not None else None,
                                 'Audit interval width':value['audit_interval_width'],
                                 'Recommended':key==forecast['recommended']})
                st.dataframe(rows,hide_index=True,width='stretch')
                st.caption(f"Recommended: {forecast['models'][forecast['recommended']]['label']}. "+forecast['recommendation_reason'])
                for warning in forecast['warnings']: st.caption(warning)
                st.json({'forecast_id':forecast['forecast_id'],'version':forecast['version'],'market':info,
                         'evaluation':forecast['evaluation'],'selected_model_parameters':row['parameters'],
                         'features':row['features']},expanded=False)
        else:
            from .presentation import build_history_chart
            st.plotly_chart(build_history_chart(material,frame),width='stretch',key='forecast_history_'+scope)
        a,b=st.columns(2)
        with a:
            st.number_input('Price change (%) — '+material,min_value=-99.9,step=1.,key=shock_key,
                            on_change=_sync_shock,args=(price_key,shock_key,ref))
        with b:
            st.number_input('Target benchmark price — '+material,min_value=max(ref*.001,1e-12),step=max(ref*.01,.0001),
                            format='%.4f',key=price_key,on_change=_sync_price,args=(price_key,shock_key,ref))
        selections[material]={'shock_fraction':st.session_state[shock_key]/100,'reference_price':ref,
                              'forecast_id':forecast['forecast_id'] if forecast else None,'forecast_model':model,
                              'market_fingerprint':info['fingerprint']}
    return selections

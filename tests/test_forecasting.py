"""Offline synthetic forecast validation, caching and scenario integration."""
from copy import deepcopy
from dataclasses import replace
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
import numpy as np
import pandas as pd
import test_scenario_orchestration as fixtures
from pe_copilot.agents.forecasting_agent import (ForecastRequest, ForecastingAgent,
    build_forecast, forecast_path, feature_row, evaluation_origins, xgb_version)
from pe_copilot.contracts import ScenarioMaterial
from pe_copilot.analytics.scenario_attribution import compare_scenarios
from pe_copilot.ui.forecasting import combined_chart


def history(n=650, material='Copper'):
    prices=np.exp(3+np.cumsum(np.random.default_rng(123).normal(.0001,.018,n)))
    return pd.DataFrame({'Date':pd.bdate_range(end='2026-09-21',periods=n),
                         'Price':prices,'Metal':material,'Ticker':'HG=F' if material=='Copper' else 'NG=F','Volume':10})


class ForecastTests(unittest.TestCase):
    def test_development_targets_mature_before_audit_and_audit_horizons_do_not_overlap(self):
        for h in (5,20,30,60):
            dev,audit=evaluation_origins(900,h)
            self.assertGreaterEqual(dev[0],252)
            self.assertLessEqual(dev[-1]+h,audit[0])
            self.assertTrue(all(a+h<=b for a,b in zip(audit,audit[1:])))
            self.assertEqual(audit[-1]+h,899)

    def test_audit_does_not_select_winner_or_calibrate_ranges(self):
        frame=history()
        first=build_forecast(ForecastRequest('Copper'),frame,False,False)
        modified=frame.copy();modified.loc[len(frame)-30:,'Price'] *= 1.7
        second=build_forecast(ForecastRequest('Copper'),modified,False,False)
        self.assertEqual(first['recommended'],second['recommended'])
        for model in first['models']:
            self.assertEqual(first['models'][model]['development_mae'],second['models'][model]['development_mae'])
            self.assertNotEqual(first['models'][model]['audit_mae'],second['models'][model]['audit_mae'])
        for model in first['models'].values():
            self.assertEqual(len(model['path']),30)
            self.assertTrue(all(0<lo<=hi for lo,hi in zip(model['lower80'],model['upper80'])))

    def test_insufficient_history_never_claims_validated_intervals(self):
        result=build_forecast(ForecastRequest('Copper'),history(10),False,False)
        self.assertEqual(list(result['models']),['naive'])
        self.assertEqual(result['models']['naive']['lower80'],[])
        self.assertIsNone(result['models']['naive']['audit_mae'])
        self.assertTrue(any('Insufficient history' in w for w in result['warnings']))

    def test_cache_reuses_frozen_data_and_invalidates_changed_prices(self):
        with TemporaryDirectory() as root:
            agent=ForecastingAgent(root,False,False);frame=history(20);request=ForecastRequest('Copper')
            first=agent.prepare(request,frame)
            with patch('pe_copilot.agents.forecasting_agent.build_forecast',side_effect=AssertionError('retrained')):
                self.assertEqual(ForecastingAgent(root,False,False).prepare(request,frame),first)
            frame.loc[len(frame)-1,'Price']*=1.01
            self.assertNotEqual(agent.prepare(request,frame)['forecast_id'],first['forecast_id'])
            self.assertIsNone(agent.load('../../not-an-artifact'))

    def test_bad_data_and_request_rejected(self):
        for horizon in (0,True,90):
            with self.assertRaises(ValueError):ForecastRequest('Copper',horizon)
        for price in (0,-1,np.nan,np.inf):
            frame=history(20);frame.loc[1,'Price']=price
            with self.assertRaises(ValueError):build_forecast(ForecastRequest('Copper'),frame,False,False)
        frame=history(20);frame.loc[1,'Date']=frame.loc[0,'Date']
        with self.assertRaises(ValueError):build_forecast(ForecastRequest('Copper'),frame,False,False)

    def test_features_never_read_after_origin(self):
        frame=history(300);logs=np.log(frame.Price.to_numpy());dates=list(frame.Date)
        prefix=feature_row(logs[:201],dates[:201],200,5,30)
        logs[201:]+=10
        self.assertEqual(prefix,feature_row(logs,dates,200,5,30))

    @unittest.skipUnless(xgb_version(),'Optional XGBoost is not installed')
    def test_real_xgboost_is_reproducible_and_finite(self):
        frame=history(300)
        a=forecast_path('xgboost',frame.Price,frame.Date.to_list(),5)
        b=forecast_path('xgboost',frame.Price,frame.Date.to_list(),5)
        np.testing.assert_allclose(a,b)
        self.assertTrue(np.isfinite(a).all())

    def test_combined_chart_contains_observations_projection_and_selectable_endpoint(self):
        result=build_forecast(ForecastRequest('Copper'),history(20),False,False)
        result['forecast_id']='test'
        chart=combined_chart(result,'naive',result['reference_price']*1.2)
        self.assertTrue(any(t.name=='Observed futures benchmark' for t in chart.data))
        endpoint=next(t for t in chart.data if t.name=='Central endpoint')
        self.assertEqual(endpoint.customdata[0][0],'forecast_endpoint')
        self.assertEqual(chart.data[-1].y[-1],result['reference_price']*1.2)


class SelectedShockTests(unittest.TestCase):
    def setUp(self):
        self.f=fixtures.ScenarioTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.f.app.forecasting_agent=ForecastingAgent(None,False,False)

    def request(self,shock,**kwargs):
        return replace(self.f.request,materials=(ScenarioMaterial('Copper',25,.8,shock,4.2,**kwargs),))

    def test_signed_shock_flows_to_financials_graph_and_comparison(self):
        positive=self.f.app.run_scenario(self.request(.2))
        negative=self.f.app.run_scenario(self.request(-.2))
        zero=self.f.app.run_scenario(self.request(0))
        for result,cost,sign in ((positive,1,'adverse'),(negative,-1,'favorable'),(zero,0,'neutral')):
            self.assertEqual(result.status,'ok',result.warnings)
            self.assertEqual(result.simulation['waterfall_summary']['Net_COGS_Absorbed'],cost)
            self.assertEqual(result.simulation['stressed_pools']['income_statement']['COGS']['stressed'],100+cost)
            self.assertEqual(result.simulation['materials']['Copper']['stochastic_projection'],{})
            self.assertEqual(next(n for n in result.graph['nodes'] if n['kind']=='company')['impact']['direction'],sign)
        comparison=compare_scenarios(positive,negative)
        self.assertEqual(comparison['assumptions'][0]['Current shock (%)'],-20)

    def test_forecast_identity_and_market_snapshot_are_checked_without_retraining(self):
        forecast=self.f.app.prepare_forecasts(['Copper'])['Copper']
        req=self.request(.1,forecast_id=forecast['forecast_id'],forecast_model='naive')
        with patch.object(self.f.app.forecasting_agent,'prepare',side_effect=AssertionError('do not retrain')):
            result=self.f.app.run_scenario(req)
        self.assertEqual(result.status,'ok',result.warnings)
        self.assertEqual(result.simulation['forecast_references']['Copper']['forecast_id'],forecast['forecast_id'])
        self.assertTrue(any(step.actor=='Forecasting Agent' for step in result.trace))
        bad=replace(req,shock_horizon_days=20)
        self.assertEqual(self.f.app.run_scenario(bad).status,'failed')
        bad=replace(req,materials=(replace(req.materials[0],reference_price=5),))
        self.assertEqual(self.f.app.run_scenario(bad).status,'failed')

    def test_invalid_negative_or_nonfinite_shocks_fail(self):
        for shock in (-1,-2,float('nan'),float('inf'),True):
            with self.assertRaises(ValueError):self.request(shock)
        req=replace(self.f.request,materials=(ScenarioMaterial('Copper',1000,1,-.9,4.2),),pass_through_fraction=0)
        self.assertEqual(self.f.app.run_scenario(req).status,'failed')


class LSTMTests(unittest.TestCase):
    def test_training_labels_are_complete_and_scale_uses_supplied_prefix(self):
        from pe_copilot.agents.lstm_forecast import training_arrays
        p=history(300).Price.to_numpy()
        x,y,current,scale=training_arrays(p,30)
        self.assertEqual(x.shape,(250,20,1))
        self.assertEqual(y.shape,(250,30))
        np.testing.assert_allclose(y[-1]*scale,np.log(p[-30:])-np.log(p[-31]),rtol=1e-6,atol=1e-8)
        np.testing.assert_allclose(current[0,:,0],(np.diff(np.log(p))[-20:]-np.diff(np.log(p)).mean())/np.diff(np.log(p)).std(),rtol=1e-6)
        with self.assertRaises(ValueError):training_arrays(p[:100],30)

    @unittest.skipUnless(__import__('importlib').util.find_spec('torch'),'Optional PyTorch unavailable')
    def test_lstm_reproducible_finite_and_preserves_rng(self):
        import torch
        from pe_copilot.agents.lstm_forecast import forecast_lstm
        p=history(300).Price.to_numpy()
        state=torch.get_rng_state().clone();threads=torch.get_num_threads()
        a=forecast_lstm(p,5);b=forecast_lstm(p,5)
        np.testing.assert_allclose(a,b)
        self.assertTrue(np.isfinite(a).all())
        self.assertTrue(torch.equal(state,torch.get_rng_state()))
        self.assertEqual(torch.get_num_threads(),threads)

    def test_failed_lstm_is_excluded_and_other_models_remain(self):
        from pe_copilot.agents.forecasting_agent import forecast_path as real_path
        def path(model,*args):
            if model=='lstm':raise RuntimeError('unavailable')
            return real_path(model,*args)
        with patch('pe_copilot.agents.forecasting_agent.torch_version',return_value='test'), patch('pe_copilot.agents.forecasting_agent.forecast_path',side_effect=path):
            r=build_forecast(ForecastRequest('Copper'),history(),False,True)
        self.assertNotIn('lstm',r['models']);self.assertIn('naive',r['models'])
        self.assertTrue(any('LSTM' in w and 'excluded' in w for w in r['warnings']))
        self.assertEqual(len(r['models']['naive']['development_errors']),len(r['evaluation']['development_origins']))

    def test_custom_chart_choice_accepts_price_outside_forecast_range(self):
        from pe_copilot.ui.price_picker import apply_choice
        state={'picker':{'price':200.}}
        with patch('pe_copilot.ui.price_picker.st.session_state',state):
            apply_choice('picker','price','shock',100.)
        self.assertEqual(state['price'],200.);self.assertEqual(state['shock'],100.)

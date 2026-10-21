"""Reproducible, CPU-only forecasting and chronological model evaluation.

No LLM estimates prices. Models fit only the data available at each origin.
Development origins choose a model and calibrate empirical error bands; later
non-overlapping origins audit it without influencing the recommendation.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import importlib.metadata
import numpy as np
import pandas as pd

from pe_copilot.scenario_support import describe_market_snapshot, fingerprint, json_value
from pe_copilot.filings.sec import atomic_json

VERSION = 'forecast-v2'
LABELS = {'naive':'No-change benchmark', 'ar':'Autoregressive (ridge AR-5)',
          'seasonal_ar':'Seasonal autoregressive', 'xgboost':'XGBoost (lagged returns)', 'lstm':'LSTM (return sequences)'}
FEATURES = ['return_1','return_2','return_3','return_5','mean_5','mean_20',
            'volatility_20','distance_from_60_session_mean','horizon_fraction',
            'target_annual_sine','target_annual_cosine']
XGB_PARAMS = dict(n_estimators=80, max_depth=2, learning_rate=.04,
                  min_child_weight=10, reg_lambda=10, subsample=1.,
                  colsample_bytree=1., objective='reg:squarederror',
                  tree_method='hist', n_jobs=1, random_state=42)


@dataclass(frozen=True)
class ForecastRequest:
    material: str
    horizon: int = 30

    def __post_init__(self):
        if not isinstance(self.material,str) or not self.material.strip():
            raise ValueError('Choose a material')
        if isinstance(self.horizon,bool) or self.horizon not in (5,20,30,60):
            raise ValueError('Forecast horizon must be 5, 20, 30 or 60 trading sessions')


def xgb_version():
    try:
        return importlib.metadata.version('xgboost')
    except importlib.metadata.PackageNotFoundError:
        return None


def torch_version():
    try:
        return importlib.metadata.version('torch')
    except importlib.metadata.PackageNotFoundError:
        return None


def feature_row(logs, dates, t, h, horizon):
    r = np.diff(logs[t-60:t+1])
    target = pd.Timestamp(dates[t]) + pd.offsets.BDay(h)
    angle = 2*np.pi*target.dayofyear/365.25
    return [r[-1],r[-2],r[-3],r[-5],r[-5:].mean(),r[-20:].mean(),
            r[-20:].std(),logs[t]-logs[t-59:t+1].mean(),h/horizon,np.sin(angle),np.cos(angle)]


def forecast_path(model, prices, dates, horizon):
    """Return a log-price forecast. Caller supplies only the training prefix."""
    logs = np.log(np.asarray(prices,dtype=float))
    future = pd.bdate_range(pd.Timestamp(dates[-1])+pd.offsets.BDay(1), periods=horizon)
    if model == 'naive':
        return np.repeat(logs[-1],horizon)
    if model in ('ar','seasonal_ar'):
        seasonal = model == 'seasonal_ar'
        def row(values, date):
            base = list(values[-5:][::-1])
            angle = 2*np.pi*pd.Timestamp(date).dayofyear/365.25
            return base + ([np.sin(angle),np.cos(angle)] if seasonal else [])
        x = np.asarray([row(logs[:t],dates[t]) for t in range(5,len(logs))])
        y = logs[5:]
        center, scale = x.mean(axis=0), x.std(axis=0)
        scale[scale<1e-8]=1
        design=np.column_stack([np.ones(len(x)),(x-center)/scale])
        penalty=np.eye(design.shape[1]);penalty[0,0]=0
        coefficients=np.linalg.solve(design.T@design+penalty,design.T@y)
        extended=list(logs); path=[]
        for date in future:
            prediction=float(np.r_[1.,(np.asarray(row(extended,date))-center)/scale]@coefficients)
            extended.append(prediction);path.append(prediction)
        return np.asarray(path)
    if model == 'lstm':
        from .lstm_forecast import forecast_lstm
        return forecast_lstm(np.asarray(prices),horizon)
    if model == 'xgboost':
        from pe_copilot.native_runtime import get_worker
        result = get_worker('forecast-xgboost',
                            'pe_copilot.agents.forecast_worker:xgboost').call({
            'prices': np.asarray(prices, dtype=float).tolist(),
            'dates': [pd.Timestamp(date).isoformat() for date in dates],
            'horizon': int(horizon),
        }, timeout=120)
        return np.asarray(result['path'], dtype=result['dtype'])
    raise ValueError('Unsupported forecast model')


def _forecast_xgboost_local(prices, dates, horizon):
    """Worker-only fit: never load XGBoost into the Streamlit process."""
    from xgboost import XGBRegressor
    logs = np.log(np.asarray(prices, dtype=float))
    x,y=[],[]
    for t in range(60,len(logs)-1):
        for h in range(1,min(horizon,len(logs)-1-t)+1):
            x.append(feature_row(logs,dates,t,h,horizon))
            y.append(logs[t+h]-logs[t])
    estimator=XGBRegressor(**XGB_PARAMS)
    estimator.fit(np.asarray(x),np.asarray(y))
    current=np.asarray([feature_row(logs,dates,len(logs)-1,h,horizon) for h in range(1,horizon+1)])
    return logs[-1]+estimator.predict(current)


def evaluation_origins(count,horizon):
    """Four audit origins; development labels mature before audit starts."""
    audit=list(range(count-4*horizon-1,count-horizon,horizon))
    last_development=audit[0]-horizon
    step=max(5,horizon//3)
    development=list(range(last_development,251,-step))[:24][::-1]
    if len(development)<8:
        return [],[]
    return development,audit


def checked_path(model, prices, dates, horizon):
    path = forecast_path(model, prices, dates, horizon)
    if len(path) != horizon or not np.isfinite(path).all() or np.max(np.abs(path)) > 100:
        raise ValueError('Nonfinite or numerically unstable forecast')
    return path


def build_forecast(request, frame, include_xgboost=True, include_lstm=True):
    snapshot=describe_market_snapshot({request.material:frame})['series'][request.material]
    prices=frame['Price'].to_numpy(dtype=float)
    dates=list(pd.to_datetime(frame['Date']))
    h=request.horizon
    dev,audit=evaluation_origins(len(prices),h)
    models=['naive','ar']
    if request.material.casefold()=='natural gas':
        models.append('seasonal_ar')
    if include_xgboost and xgb_version():
        models.append('xgboost')
    if include_lstm and torch_version():
        models.append('lstm')
    warnings=['Benchmarks are futures series, not company purchase prices or a maturity-specific forward curve.',
              'Future dates are weekday estimates, not an exchange holiday calendar.',
              '80% bands are empirical forecast-error ranges; coverage is measured, not guaranteed.',
              'Development targets can overlap; audit horizons do not. Four audit origins provide limited evidence.',
              'No storage, weather, macroeconomic or contract-roll features are currently available.']
    if not include_xgboost or not xgb_version():
        warnings.append('XGBoost is unavailable in this runtime; no substitute is presented as XGBoost.')
    if not include_lstm or not torch_version():
        warnings.append('LSTM is disabled or PyTorch is unavailable; no substitute is presented as LSTM.')
    else:
        warnings.append('LSTM is a small experimental challenger; limited history can overfit. It must earn its recommendation on the same development origins.')
    outputs={}
    if not dev:
        models=['naive']
        warnings.append('Insufficient history for chronological validation. Only an unvalidated no-change benchmark is shown; no probability band is supplied.')
    for model in models:
        try:
            errors=[];dev_mae=[]
            for origin in dev:
                predicted=checked_path(model,prices[:origin+1],dates[:origin+1],h)
                actual=prices[origin+1:origin+h+1]
                errors.append(np.log(actual)-predicted)
                dev_mae.append(float(np.mean(np.abs(actual-np.exp(predicted)))))
            if errors:
                lower,upper=np.quantile(errors,[.1,.9],axis=0)
            else:
                lower=upper=None
            audit_mae=[];coverage=[];width=[];audit_records=[]
            for origin in audit:
                prediction=checked_path(model,prices[:origin+1],dates[:origin+1],h)
                actual=prices[origin+1:origin+h+1]
                lo,hi=np.exp(prediction+lower),np.exp(prediction+upper)
                audit_mae.append(float(np.mean(np.abs(actual-np.exp(prediction)))))
                coverage.append(float(np.mean((actual>=lo)&(actual<=hi))))
                width.append(float(np.mean(hi-lo)))
                audit_records.append({'training_end':dates[origin].isoformat(),
                                      'target_end':dates[origin+h].isoformat(),
                                      'mae':audit_mae[-1],'coverage80':coverage[-1]})
            path=checked_path(model,prices,dates,h)
            if not np.isfinite(path).all() or np.max(np.abs(path))>100:
                raise ValueError('Nonfinite or numerically unstable forecast')
            from .lstm_forecast import PARAMETERS as LSTM_PARAMS
            outputs[model]={'label':LABELS[model], 'path':np.exp(path).tolist(),
                            'lower80':np.exp(path+lower).tolist() if lower is not None else [],
                            'upper80':np.exp(path+upper).tolist() if upper is not None else [],
                            'development_mae':float(np.mean(dev_mae)) if dev_mae else None,
                            'audit_mae':float(np.mean(audit_mae)) if audit_mae else None,
                            'audit_coverage80':float(np.mean(coverage)) if coverage else None,
                            'audit_interval_width':float(np.mean(width)) if width else None,
                            'audit_records':audit_records,
                            'development_errors':[{'origin':dates[o].isoformat(),
                                'target_end':dates[o+h].isoformat(), 'log_errors':e.tolist()}
                                for o,e in zip(dev,errors)],
                            'parameters':LSTM_PARAMS if model=='lstm' else XGB_PARAMS if model=='xgboost' else {'lags':5,'ridge_penalty':1.0} if model!='naive' else {},
                            'features':['20 trailing standardized log returns'] if model=='lstm' else FEATURES if model=='xgboost' else ['log price lags 1–5'] + (['annual sine/cosine'] if model=='seasonal_ar' else []) if model!='naive' else ['last observed price']}
        except (ImportError,OSError,ValueError,RuntimeError,np.linalg.LinAlgError) as error:
            warnings.append(f'{LABELS[model]} unavailable ({type(error).__name__}); excluded from recommendation.')
    if 'naive' not in outputs:
        raise ValueError('No valid forecast benchmark could be produced')
    recommended='naive'
    baseline=outputs['naive']['development_mae']
    if baseline is not None and baseline>0:
        best=min(outputs,key=lambda key:outputs[key]['development_mae'])
        if outputs[best]['development_mae'] < .95*baseline:
            recommended=best
    return json_value({'version':VERSION,'material':request.material,'horizon':h,
            'created_at':datetime.now(timezone.utc).isoformat(), 'market':snapshot,
            'reference_price':float(prices[-1]),'models':outputs,'recommended':recommended,
            'recommendation_reason':'Lowest development MAE with more than 5% improvement over no-change; otherwise retain no-change. Later audit results never select the winner.',
            'history':[{'date':date.isoformat(),'price':float(price)} for date,price in zip(dates,prices)],
            'future_dates':[d.isoformat() for d in pd.bdate_range(dates[-1]+pd.offsets.BDay(1),periods=h)],
            'evaluation':{'development_origins':[dates[o].isoformat() for o in dev],
                          'development_target_ends':[dates[o+h].isoformat() for o in dev],
                          'audit_origins':[dates[o].isoformat() for o in audit],
                          'interval_calibration':'10th/90th percentiles of development log forecast errors, separately by horizon'},
            'warnings':warnings})


class ForecastingAgent:
    def __init__(self, directory='artifacts/forecasts', include_xgboost=True, include_lstm=True):
        self.directory=Path(directory) if directory is not None else None
        self.include_xgboost=include_xgboost
        self.include_lstm=include_lstm
        self._memory={}

    def prepare(self, request, frame):
        snapshot=describe_market_snapshot({request.material:frame})['series'][request.material]
        key=fingerprint({'request':request.__dict__,'market':snapshot['fingerprint'],
                         'version':VERSION,'xgboost':xgb_version() if self.include_xgboost else None,
                         'numpy':np.__version__,'pandas':pd.__version__,
                         'torch':torch_version() if self.include_lstm else None})
        cached=self.load(key)
        if cached:
            return cached
        result=build_forecast(request,frame,self.include_xgboost,self.include_lstm)
        result['forecast_id']=key
        result['runtime']={'numpy':np.__version__,'pandas':pd.__version__,'xgboost':xgb_version() if self.include_xgboost else None,
                           'torch':torch_version() if self.include_lstm else None}
        if self.directory:
            atomic_json(self.directory/(key+'.json'),result)
        self._memory[key]=result
        return result

    def load(self, key):
        if not isinstance(key,str) or len(key)!=64 or any(c not in '0123456789abcdef' for c in key):
            return None
        if key in self._memory:
            return self._memory[key]
        path=self.directory/(key+'.json') if self.directory else None
        if path and path.exists():
            result=json.loads(path.read_text())
            if result.get('forecast_id')==key and result.get('version')==VERSION:
                self._memory[key]=result
                return result
        return None

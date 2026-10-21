"""Isolated model handlers. Each handler gets a separate native-runtime process.

Imports remain inside the handler so a worker loads only its own model backend.
The numerical implementations are shared with the forecast service unchanged.
"""


def xgboost(payload):
    from .forecasting_agent import _forecast_xgboost_local
    path = _forecast_xgboost_local(payload['prices'], payload['dates'], payload['horizon'])
    return {'path': path.tolist(), 'dtype': str(path.dtype)}


def lstm(payload):
    from .lstm_forecast import _forecast_lstm_local
    path = _forecast_lstm_local(payload['prices'], payload['horizon'])
    return {'path': path.tolist(), 'dtype': str(path.dtype)}

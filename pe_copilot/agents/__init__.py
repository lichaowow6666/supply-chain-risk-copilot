"""Agents subpackage for pe_copilot.
This module re-exports primary agent classes for convenient imports.
"""

__all__ = ["MarketAgent", "FundamentalAgent", "ForecastingAgent"]


def __getattr__(name):
    # A forecast worker must not initialize unrelated native database/model
    # libraries simply because its module lives in the agents package.
    modules = {'MarketAgent': 'market_agent', 'FundamentalAgent': 'fundamental_agent',
               'ForecastingAgent': 'forecasting_agent'}
    if name not in modules:
        raise AttributeError(name)
    from importlib import import_module
    value = getattr(import_module(f'.{modules[name]}', __name__), name)
    globals()[name] = value
    return value

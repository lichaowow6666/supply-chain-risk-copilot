"""Compatibility entry point for daily futures refresh (three-year initial history)."""
from pe_copilot.market_data import CATALOG, main, refresh_prices

DB_PATH = 'market_data.duckdb'
COMMODITIES = {name: contract[0] for name, contract in CATALOG.items()}


def fetch_and_ingest_real_market_data(period='3y'):
    if period != '3y':
        raise ValueError('Initial history is three years; existing history is updated incrementally.')
    return refresh_prices(DB_PATH, force=True)


if __name__ == '__main__':
    raise SystemExit(main())

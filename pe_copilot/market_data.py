"""Daily, transactional Yahoo futures ingestion; downloaded data stays local."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import fcntl
import json
import os

import duckdb
import numpy as np
import pandas as pd

# Yahoo continuous front contracts; USD quotation units differ by contract.
# These are benchmarks, not a company's delivered purchase prices or hedge positions.
CATALOG = {
    'Copper': ('HG=F', 'USD/lb'), 'Palladium': ('PA=F', 'USD/troy oz'),
    'Platinum': ('PL=F', 'USD/troy oz'), 'Gold': ('GC=F', 'USD/troy oz'),
    'Crude Oil': ('CL=F', 'USD/barrel'), 'Natural Gas': ('NG=F', 'USD/MMBtu'),
    'Silver': ('SI=F', 'USD/troy oz'), 'Aluminum': ('ALI=F', 'USD/metric tonne'),
    'Brent Crude Oil': ('BZ=F', 'USD/barrel'), 'Heating Oil': ('HO=F', 'USD/gallon'),
    'Gasoline': ('RB=F', 'USD/gallon'),
}


def download(symbol, start):
    from dotenv import load_dotenv
    import yfinance as yf
    load_dotenv()
    proxy = os.getenv('PROXY') or os.getenv('proxy')
    if proxy:
        yf.config.network.proxy = proxy
    yf.config.debug.hide_exceptions = False
    return yf.Ticker(symbol).history(start=start.isoformat(), interval='1d',
                                   auto_adjust=False, timeout=20)


def clean_prices(frame, name, symbol, today):
    if frame.empty:
        raise ValueError('Provider returned no prices')
    data = frame.reset_index()
    dates = pd.to_datetime(data['Date']).dt.tz_localize(None).dt.normalize()
    prices = pd.to_numeric(data['Close'], errors='coerce')
    # Today's daily candle is still provisional; use completed sessions only.
    valid = (dates.dt.date < today) & np.isfinite(prices) & (prices > 0)
    clean = pd.DataFrame({'Date': dates, 'Metal': name, 'Ticker': symbol,
                          'Price': prices, 'Volume': pd.to_numeric(data['Volume'], errors='coerce').fillna(0).clip(lower=0).astype('int64')})
    clean = clean.loc[valid].drop_duplicates('Date', keep='last').sort_values('Date')
    if clean.empty:
        raise ValueError('No completed sessions with finite positive prices')
    return clean


def refresh_prices(db_path='market_data.duckdb', force=False, downloader=None, today=None, catalog=None):
    """Check once per Shanghai calendar day; force retries failed daily attempts.

    Lock coordinates app and scheduler. Each contract commits independently; failures
    retain all prior rows. Re-fetch overlap for corrections, never truncate history.
    """
    today = today or datetime.now(ZoneInfo('Asia/Shanghai')).date()
    catalog = CATALOG if catalog is None else catalog
    downloader = downloader or download
    path = Path(db_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + '.refresh.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'busy': True, 'contracts': [], 'warnings': ['Market refresh is already running; existing history is retained.']}
        results = []
        with duckdb.connect(str(path)) as db:
            db.execute('CREATE TABLE IF NOT EXISTS futures_prices (Date TIMESTAMP_S, Metal VARCHAR, Ticker VARCHAR, Price DOUBLE, Volume BIGINT)')
            db.execute('''CREATE TABLE IF NOT EXISTS futures_refresh (
                Ticker VARCHAR PRIMARY KEY, Metal VARCHAR, Unit VARCHAR, Checked DATE,
                Succeeded DATE, Latest DATE, Status VARCHAR, Source VARCHAR)''')
        for name, (symbol, unit) in catalog.items():
            with duckdb.connect(str(path)) as db:
                previous = db.execute('SELECT Checked, Succeeded, Status FROM futures_refresh WHERE Ticker=?', [symbol]).fetchone()
                latest = db.execute('SELECT MAX(Date) FROM futures_prices WHERE Ticker=?', [symbol]).fetchone()[0]
            status = previous[2] if previous else 'not_checked'
            success = previous[1] if previous else None
            if force or not previous or previous[0] != today:
                start = latest.date() - timedelta(days=7) if latest else today - timedelta(days=1096)
                try:
                    frame = clean_prices(downloader(symbol, start), name, symbol, today)
                    if latest and frame.Date.max().date() < latest.date():
                        raise ValueError('Provider history regressed')
                    with duckdb.connect(str(path)) as db:
                        db.register('incoming_prices', frame)
                        db.execute('BEGIN TRANSACTION')
                        try:
                            db.execute('DELETE FROM futures_prices WHERE Ticker=? AND Date IN (SELECT Date FROM incoming_prices)', [symbol])
                            db.execute('INSERT INTO futures_prices SELECT * FROM incoming_prices')
                            db.execute('COMMIT')
                        except Exception:
                            db.execute('ROLLBACK')
                            raise
                    latest = frame.Date.max().to_pydatetime()
                    success, status = today, 'ok'
                except Exception as error:
                    # Never persist provider errors that may include credentials/proxy URLs.
                    status = 'failed:' + type(error).__name__
                with duckdb.connect(str(path)) as db:
                    db.execute('INSERT OR REPLACE INTO futures_refresh VALUES (?,?,?,?,?,?,?,?)',
                               [symbol, name, unit, today, success, latest, status, 'Yahoo Finance via yfinance; daily Close, unadjusted'])
            age = (today - latest.date()).days if latest else None
            results.append({'material': name, 'ticker': symbol, 'unit': unit, 'checked': today.isoformat(),
                            'latest_session': latest.date().isoformat() if latest else None,
                            'status': status, 'stale': age is None or age > 5})
        warnings = [f"{r['material']}: {r['status']}; latest completed session {r['latest_session'] or 'unavailable'}."
                    for r in results if r['status'] != 'ok' or r['stale']]
        return {'busy': False, 'contracts': results, 'warnings': warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--force', action='store_true', help='Retry even when checked today')
    parser.add_argument('--db', default='market_data.duckdb')
    args = parser.parse_args()
    result = refresh_prices(args.db, args.force)
    print(json.dumps(result, indent=2))
    return 1 if result['warnings'] else 0


if __name__ == '__main__':
    raise SystemExit(main())

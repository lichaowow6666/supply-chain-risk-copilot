# =====================================================================
# market_agent.py - Multi-Material Waterfall & Stochastic Paths
# Table: futures_prices (Date, Metal, Ticker, Price, Volume)
# =====================================================================

try:
    import duckdb
    HAS_DUCKDB = True
except Exception:
    duckdb = None
    HAS_DUCKDB = False

import os
import math

# Optional heavy dependencies are imported lazily/guarded so the package can be imported
# in environments where not all analytics dependencies are available.
try:
    import pandas as pd
except Exception:
    pd = None

try:
    import numpy as np
except Exception:
    np = None

from typing import Dict, Any, List

class MarketAgent:
    def __init__(self, db_path: str = "market_data.duckdb"):
        self.db_path = os.path.abspath(db_path)
        self.table_name = "futures_prices"

    def get_connection(self):
        if not HAS_DUCKDB:
            raise ImportError("duckdb is required for MarketAgent but is not installed in this environment. Install with: pip install duckdb")
        # DuckDB connections to one file in a process must share configuration.
        # The daily refresher writes this same file while Streamlit sessions read
        # it; SELECT-only callers must therefore use the writer's default mode.
        return duckdb.connect(self.db_path)

    def get_available_commodities(self) -> List[str]:
        """Queries unique materials present in DuckDB."""
        try:
            with self.get_connection() as conn:
                df = conn.execute(f"SELECT DISTINCT Metal FROM {self.table_name}").fetchdf()
            return [m.title() for m in df['Metal'].tolist()]
        except Exception as e:
            print(f"[MarketAgent Error] {e}")
            return []

    def fetch_price_history(self, metal: str) -> pd.DataFrame:
        """Retrieves historical prices for a given metal."""
        query = f"SELECT Date, Metal, Price, Volume FROM {self.table_name} WHERE LOWER(Metal) = LOWER(?) ORDER BY Date ASC"
        with self.get_connection() as conn:
            df = conn.execute(query, [metal]).fetchdf()
        return df

    def capture_price_snapshot(self, materials):
        """Read all requested histories from a single consistent DuckDB transaction."""
        histories = {}
        with self.get_connection() as connection:
            connection.execute('BEGIN TRANSACTION')
            for material in materials:
                histories[material] = connection.execute(
                    'SELECT Date, Metal, Ticker, Price, Volume FROM futures_prices '
                    'WHERE LOWER(Metal)=LOWER(?) ORDER BY Date', [material]).fetchdf()
            connection.execute('COMMIT')
        return histories

    def calculate_multi_material_waterfall(
        self,
        material_configs: Dict[str, Dict[str, float]],
        pass_through_pct: float,
        baselines: Dict[str, Dict[str, float]],
        price_histories=None,
    ) -> Dict[str, Any]:
        """
        Executes multi-material quantitative stress test and cascades net absorbed COGS
        across all 3 financial statement KPI pools.
        """
        if not material_configs or not 0 <= pass_through_pct <= 1:
            raise ValueError('Select materials and a pass-through rate between 0 and 1.')
        for pool in baselines.values():
            for value in pool.values():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError('Financial baselines must be finite reported or derived numbers.')
        if not any(baselines.values()):
            raise ValueError('No source-backed financial baseline is available.')
        for cfg in material_configs.values():
            if not math.isfinite(cfg['annual_spend']) or cfg['annual_spend'] < 0 or not 0 <= cfg['unhedged_pct'] <= 1:
                raise ValueError('Spend must be nonnegative and unhedged exposure must be between 0 and 1.')
            if 'shock_fraction' in cfg and (isinstance(cfg['shock_fraction'],bool) or
                    not isinstance(cfg['shock_fraction'],(float,int)) or not math.isfinite(cfg['shock_fraction']) or cfg['shock_fraction']<=-1):
                raise ValueError('Price shock must be finite and greater than -100%.')
        results = {"materials": {}, "waterfall_summary": {}, "stressed_pools": {}, "warnings": []}
        total_gross_cogs_increase = 0.0

        # 1. Compute individual material price shocks using 95% Parametric VaR (2x daily volatility)
        for metal, cfg in material_configs.items():
            df = price_histories[metal].copy(deep=True) if price_histories is not None else self.fetch_price_history(metal)
            if df.empty:
                raise ValueError(f'No market history is available for {metal}; no zero-impact substitute was used.')
            if len(df) < 2 or not np.isfinite(df['Price'].values).all() or (df['Price'] <= 0).any():
                raise ValueError(f'{metal} needs at least two finite positive prices for log-return stress estimation.')
            if (pd.Timestamp.now().normalize() - pd.Timestamp(df['Date'].max()).tz_localize(None).normalize()).days > 30:
                results['warnings'].append(f'{metal} market history ends {pd.Timestamp(df["Date"].max()).date()}; prices are more than 30 days old.')

            prices = df['Price'].values
            latest_price = prices[-1]
            returns = np.diff(np.log(prices)) if len(prices) > 1 else np.array([0.02])
            daily_vol = np.std(returns) if len(returns) > 0 else 0.02
            
            # 30-day 95% Parametric Shock
            shock_pct = cfg.get('shock_fraction',round(1.96 * daily_vol * np.sqrt(30), 4))
            
            spend = cfg["annual_spend"]
            unhedged = cfg["unhedged_pct"]
            
            # Gross cost increase = Spend * Shock % * Unhedged %
            gross_cost_increase = spend * shock_pct * unhedged
            total_gross_cogs_increase += gross_cost_increase

            # Legacy illustration retained for old API callers only. The current
            # workbench uses ForecastingAgent and never displays these trajectories.
            rng = np.random.RandomState(42)  # Same paths, without mutating another session’s random state
            dt = 1 / 252.0
            days = 30
            
            # Baseline Path (GBM with historical drift and volatility)
            base_path = [latest_price]
            curr_bp = latest_price
            for _ in range(1, days):
                drift = (0.0005 - 0.5 * daily_vol**2) * dt
                diffusion = daily_vol * np.sqrt(dt) * rng.normal()
                curr_bp = curr_bp * np.exp(drift + diffusion)
                base_path.append(round(curr_bp, 2))

            # Stressed Path (Starts at Shock Price, evolves with elevated stress volatility)
            stressed_start = latest_price * (1 + shock_pct)
            stressed_path = [stressed_start]
            curr_sp = stressed_start
            stress_vol = daily_vol * 1.25  # Volatility spikes during market stress
            for _ in range(1, days):
                drift = (0.001 - 0.5 * stress_vol**2) * dt
                diffusion = stress_vol * np.sqrt(dt) * rng.normal()
                curr_sp = curr_sp * np.exp(drift + diffusion)
                stressed_path.append(round(curr_sp, 2))

            # 95% Confidence Upper/Lower Bands
            upper_band = [p * (1 + 1.96 * daily_vol * np.sqrt(i+1)) for i, p in enumerate(stressed_path)]
            lower_band = [p * (1 - 1.96 * daily_vol * np.sqrt(i+1)) for i, p in enumerate(stressed_path)]

            from pe_copilot.market_data import CATALOG
            contract = CATALOG.get(metal, (metal, 'provider quote units'))
            results["materials"][metal] = {
                "market_ticker": contract[0],
                "price_unit": contract[1],
                "market_as_of": pd.Timestamp(df['Date'].max()).date().isoformat(),
                "latest_price": latest_price,
                "shock_pct": round(shock_pct * 100, 2),
                "shock_source": "user-selected" if 'shock_fraction' in cfg else "legacy volatility stress",
                "stressed_price": round(latest_price * (1 + shock_pct), 2),
                "gross_cost_increase": round(gross_cost_increase, 2),
                # Preserve calculation precision for additive attribution. Existing
                # summary/KPI formulas and their rounding remain unchanged.
                "cost_contribution": {
                    "spend": spend, "unhedged_fraction": unhedged,
                    "shock_fraction": shock_pct,
                    "gross": gross_cost_increase,
                    "passed": gross_cost_increase * pass_through_pct,
                    "absorbed": gross_cost_increase * (1.0 - pass_through_pct),
                    "unit": "USD millions", "basis": "user assumptions × selected shock" if 'shock_fraction' in cfg else "user assumptions × market-derived shock",
                },
                "history_df": df,
                "stochastic_projection": {} if 'shock_fraction' in cfg else {
                    "days": list(range(1, days + 1)),
                    "baseline_path": base_path,
                    "stressed_path": stressed_path,
                    "upper_band": upper_band,
                    "lower_band": lower_band
                }
            }

        # 2. Net Pass-Through & Absorption Math
        net_cogs_absorbed = round(total_gross_cogs_increase * (1.0 - pass_through_pct), 2)
        if 'COGS' in baselines.get('income_statement',{}) and baselines['income_statement']['COGS']+net_cogs_absorbed<0:
            raise ValueError('Assumed savings exceed reported COGS. Reduce exposure spending or the price decline; negative COGS is unsupported.')
        total_passed_to_clients = round(total_gross_cogs_increase * pass_through_pct, 2)

        results["waterfall_summary"] = {
            "Total_Gross_COGS_Surge": round(total_gross_cogs_increase, 2),
            "Passed_To_Clients": total_passed_to_clients,
            "Net_COGS_Absorbed": net_cogs_absorbed,
            "Pass_Through_Rate_Pct": round(pass_through_pct * 100, 1)
        }

        from pe_copilot.analytics.financial_impact import apply_financial_impact
        results['stressed_pools'] = apply_financial_impact(baselines, net_cogs_absorbed)
        if not all(k in baselines.get('balance_sheet', {}) for k in ('Total Debt','Cash & Equivalents','Net Debt')):
            results['warnings'].append('Debt-related outputs require their reported baseline and cash inputs; unavailable outputs are omitted.')
        return results

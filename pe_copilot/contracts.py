"""UI-neutral requests and result envelopes for the workbench and future chat UI."""
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import math
import re
from typing import Optional, Tuple
from uuid import uuid4


def _required(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be nonempty text')


@dataclass(frozen=True)
class WorkspaceContext:
    company_id: str
    snapshot_id: str
    period_start: Optional[str] = None
    period_end: Optional[str] = None

    def __post_init__(self):
        if not re.fullmatch(r'company:\d{10}', self.company_id):
            raise ValueError('invalid company identity')
        _required(self.snapshot_id, 'snapshot_id')
        if (self.period_start is None) != (self.period_end is None):
            raise ValueError('both observation period boundaries are required')
        if self.period_start is not None:
            if date.fromisoformat(self.period_start) > date.fromisoformat(self.period_end):
                raise ValueError('invalid observation period')


@dataclass(frozen=True)
class MaterialShock:
    material: str
    shock_fraction: float

    def __post_init__(self):
        _required(self.material, 'material')
        if isinstance(self.shock_fraction, bool) or not isinstance(self.shock_fraction, (int, float)) or not math.isfinite(self.shock_fraction):
            raise ValueError('shock_fraction must be finite')


@dataclass(frozen=True)
class HighlightContext:
    source: str
    run_id: str
    ticker: str
    material_shocks: Tuple[MaterialShock, ...] = ()
    entity_ids: Tuple[str, ...] = ()
    company_id: Optional[str] = None
    snapshot_id: Optional[str] = None
    input_signature: Optional[str] = None
    created_at: str = ''
    horizon: str = ''

    def __post_init__(self):
        if self.source not in ('tab2', 'chatbot'):
            raise ValueError('unsupported highlight source')
        _required(self.run_id, 'run_id')
        _required(self.ticker, 'ticker')
        object.__setattr__(self, 'material_shocks', tuple(self.material_shocks))
        object.__setattr__(self, 'entity_ids', tuple(self.entity_ids))
        if any(not isinstance(s, MaterialShock) for s in self.material_shocks):
            raise ValueError('invalid material shock')
        for entity_id in self.entity_ids:
            _required(entity_id, 'entity_id')
        if self.source == 'chatbot' and (not self.company_id or not self.snapshot_id):
            raise ValueError('chatbot focus must identify its company and snapshot')


@dataclass(frozen=True)
class GraphRequest:
    context: WorkspaceContext
    lens: str = 'material_exposure'
    focus_entity_id: Optional[str] = None
    focus_only: bool = False
    confirmed_only: bool = True
    highlight: Optional[HighlightContext] = None
    source: str = 'workbench'
    intent: str = 'explore_graph'

    def __post_init__(self):
        if not isinstance(self.context, WorkspaceContext):
            raise ValueError('invalid workspace context')
        if self.source not in ('workbench', 'chatbot') or self.intent != 'explore_graph':
            raise ValueError('unsupported graph request')
        if self.lens not in ('material_exposure', 'physical_supply', 'financial_mechanisms', 'all_relationships'):
            raise ValueError('unsupported graph lens')
        if self.highlight is not None and not isinstance(self.highlight, HighlightContext):
            raise ValueError('invalid highlight context')


@dataclass(frozen=True)
class ExecutionStep:
    name: str
    status: str
    duration_ms: float
    detail: str
    actor: str = 'Orchestrator'
    basis: str = 'workflow'
    input_refs: tuple = ()
    output_refs: tuple = ()


@dataclass(frozen=True)
class ResultEnvelope:
    run_id: str
    status: str
    context: WorkspaceContext
    data: dict = field(default_factory=dict)
    reported_facts: tuple = ()
    deterministic_calculations: tuple = ()
    assumptions: tuple = ()
    evidence_references: tuple = ()
    warnings: tuple = ()
    trace: Tuple[ExecutionStep, ...] = ()
    duration_ms: float = 0.0

    def to_dict(self):
        return asdict(self)


def stress_input_signature(ticker, material_configs, pass_through_pct, baselines):
    """Compare the inputs of a saved run with current controls; no recalculation."""
    value = {'ticker': ticker.upper(), 'materials': material_configs, 'pass_through': pass_through_pct, 'baselines': baselines}
    return sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def capture_stress_context(ticker, material_configs, pass_through_pct, baselines, result):
    # MarketAgent's existing output is percent (20.0), not fraction (0.20).
    shocks = tuple(MaterialShock(material, float(values['shock_pct']) / 100.0)
                   for material, values in result.get('materials', {}).items() if 'shock_pct' in values)
    return HighlightContext('tab2', str(uuid4()), ticker.upper(), shocks,
        input_signature=stress_input_signature(ticker, material_configs, pass_through_pct, baselines),
        created_at=datetime.now(timezone.utc).isoformat(),
        horizon='30-day market shock; applied to annual-spend inputs in the existing Tab 2 model')


@dataclass(frozen=True)
class ScenarioMaterial:
    material: str
    period_spend: float
    unhedged_fraction: float
    shock_fraction: Optional[float] = None
    reference_price: Optional[float] = None
    forecast_id: Optional[str] = None
    forecast_model: Optional[str] = None
    market_fingerprint: Optional[str] = None

    def __post_init__(self):
        _required(self.material, 'material')
        object.__setattr__(self, 'material', self.material.strip().title())
        for name, value in (('period_spend', self.period_spend), ('unhedged_fraction', self.unhedged_fraction)):
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError(f'{name} must be a finite number')
        if self.period_spend < 0 or not 0 <= self.unhedged_fraction <= 1:
            raise ValueError('Spend must be nonnegative and unhedged fraction must be between 0 and 1')
        if self.shock_fraction is not None and (isinstance(self.shock_fraction,bool) or
                not isinstance(self.shock_fraction,(float,int)) or not math.isfinite(self.shock_fraction) or self.shock_fraction <= -1):
            raise ValueError('Price shock must be finite and greater than -100% for this positive-price model')
        if self.reference_price is not None and (isinstance(self.reference_price,bool) or
                not isinstance(self.reference_price,(float,int)) or not math.isfinite(self.reference_price) or self.reference_price<=0):
            raise ValueError('Reference price must be finite and positive')
        if self.forecast_id and (self.shock_fraction is None or self.reference_price is None or not self.forecast_model):
            raise ValueError('A forecast reference requires an explicit price shock, price and model')
        if self.market_fingerprint is not None and (not isinstance(self.market_fingerprint,str) or not re.fullmatch(r'[0-9a-f]{64}',self.market_fingerprint)):
            raise ValueError('Invalid market snapshot fingerprint')


@dataclass(frozen=True)
class ScenarioRequest:
    """UI/chat-neutral inputs. Financial baselines are resolved from the filing."""
    context: WorkspaceContext
    ticker: str
    accession: str
    materials: Tuple[ScenarioMaterial, ...]
    pass_through_fraction: float
    spend_basis: str = 'annual'
    source: str = 'workbench'
    currency: str = 'USD'
    unit: str = 'millions'
    shock_horizon_days: int = 30

    def __post_init__(self):
        if not isinstance(self.context, WorkspaceContext):
            raise ValueError('A scenario requires a validated workspace context')
        _required(self.ticker, 'ticker')
        _required(self.accession, 'accession')
        object.__setattr__(self, 'ticker', self.ticker.upper())
        object.__setattr__(self, 'materials', tuple(self.materials))
        if not self.materials or any(not isinstance(m, ScenarioMaterial) for m in self.materials):
            raise ValueError('Select at least one valid scenario material')
        if len({m.material.casefold() for m in self.materials}) != len(self.materials):
            raise ValueError('Each material can occur only once in a scenario')
        value = self.pass_through_fraction
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError('Pass-through fraction must be between 0 and 1')
        if self.spend_basis not in ('annual', 'year-to-date') or self.source not in ('workbench', 'chatbot'):
            raise ValueError('Unsupported spend basis or scenario source')
        if self.currency != 'USD' or self.unit != 'millions' or isinstance(self.shock_horizon_days,bool) or self.shock_horizon_days not in (5,20,30,60):
            raise ValueError('Supported: USD millions and 5, 20, 30 or 60 trading-session price horizons')
        if self.shock_horizon_days != 30 and any(m.shock_fraction is None for m in self.materials):
            raise ValueError('Non-30-session scenarios require explicit price shocks')

    def material_configs(self):
        # Compatibility boundary only: MarketAgent's legacy key holds period spend.
        result={m.material: {'annual_spend':m.period_spend, 'unhedged_pct':m.unhedged_fraction} for m in self.materials}
        for m in self.materials:
            if m.shock_fraction is not None:
                result[m.material].update(shock_fraction=m.shock_fraction,reference_price=m.reference_price,
                                           forecast_id=m.forecast_id,forecast_model=m.forecast_model,
                                           market_fingerprint=m.market_fingerprint)
        return result


@dataclass(frozen=True)
class ScenarioResult:
    run_id: str
    status: str
    request: ScenarioRequest
    created_at: str
    baseline: dict = field(default_factory=dict)
    exposures: tuple = ()
    market_snapshot: dict = field(default_factory=dict)
    simulation: dict = field(default_factory=dict)
    graph: dict = field(default_factory=dict)
    highlight: Optional[HighlightContext] = None
    assumptions: tuple = ()
    evidence_references: tuple = ()
    warnings: tuple = ()
    trace: Tuple[ExecutionStep, ...] = ()
    duration_ms: float = 0.0
    input_fingerprint: str = ''
    model_version: str = 'legacy-waterfall-v1'

    def to_dict(self):
        """Export data, not Python objects, with explicit market dates and finite values."""
        from pe_copilot.scenario_support import json_value
        return json_value(asdict(self))

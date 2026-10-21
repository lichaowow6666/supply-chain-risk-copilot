"""UI-independent, versioned evidence graph contracts. No financial formulas."""
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
import re
from typing import Optional, Tuple

from pe_copilot.evidence import EvidenceReference, EvidenceStatus


def text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} must be non-empty text')
    return value


def iso(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('dates must use ISO YYYY-MM-DD')
    date.fromisoformat(value)


def unique(items, field):
    values = [getattr(x, field) for x in items]
    if len(values) != len(set(values)):
        raise ValueError(f'duplicate {field}')
    return set(values)


class ReviewState(str, Enum):
    CANDIDATE = 'candidate'
    ACCEPTED = 'accepted'
    REJECTED = 'rejected'


class EntityType(str, Enum):
    COMPANY = 'company'
    SEGMENT = 'segment'
    MATERIAL = 'material'
    MATERIAL_COHORT = 'material_cohort'
    PRODUCT = 'product'
    FACILITY = 'facility'
    OPERATION = 'operation'
    END_MARKET = 'end_market'
    SUPPLIER_POOL = 'supplier_pool'
    MECHANISM = 'mechanism'
    EXPOSURE = 'exposure'
    KPI = 'kpi'
    EVENT = 'event'


@dataclass(frozen=True)
class Company:
    cik: str
    name: str
    ticker: str
    identity_source: str

    def __post_init__(self):
        if not isinstance(self.cik, str) or not re.fullmatch(r'\d{10}', self.cik):
            raise ValueError('cik must contain ten digits')
        for field in ('name', 'ticker', 'identity_source'):
            text(getattr(self, field), field)

    @property
    def company_id(self):
        return f'company:{self.cik}'


@dataclass(frozen=True)
class SourceDocument:
    company_id: str
    form: str
    period_end: str
    content_sha256: str
    source_location: str
    parsing_version: str
    accession: Optional[str] = None
    filing_date: Optional[str] = None

    def __post_init__(self):
        if not re.fullmatch(r'company:\d{10}', self.company_id):
            raise ValueError('invalid company_id')
        for field in ('form', 'source_location', 'parsing_version'):
            text(getattr(self, field), field)
        iso(self.period_end)
        if self.filing_date is not None:
            iso(self.filing_date)
        if not re.fullmatch(r'[a-f0-9]{64}', self.content_sha256):
            raise ValueError('invalid SHA-256')
        if self.accession is not None and not re.fullmatch(r'\d{10}-\d{2}-\d{6}', self.accession):
            raise ValueError('invalid accession')

    @property
    def document_id(self):
        # Hash remains part of identity even when accession metadata is available.
        return f'{self.company_id}:filing:{self.accession or "local"}:{self.content_sha256}'


@dataclass(frozen=True)
class Citation:
    document_id: str
    locator: str
    reference: EvidenceReference

    def __post_init__(self):
        text(self.document_id, 'document_id')
        text(self.locator, 'locator')
        if not isinstance(self.reference, EvidenceReference):
            raise ValueError('reference must be EvidenceReference')


@dataclass(frozen=True)
class Provenance:
    origin: EvidenceStatus
    review: ReviewState
    citations: Tuple[Citation, ...] = ()
    rationale: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_on: Optional[str] = None

    def __post_init__(self):
        object.__setattr__(self, 'origin', EvidenceStatus(self.origin))
        object.__setattr__(self, 'review', ReviewState(self.review))
        object.__setattr__(self, 'citations', tuple(self.citations))
        if any(not isinstance(c, Citation) for c in self.citations):
            raise ValueError('invalid citation')
        if self.origin == EvidenceStatus.CONFIRMED and not self.citations:
            raise ValueError('confirmed records require citations')
        if self.origin != EvidenceStatus.CONFIRMED:
            text(self.rationale, 'rationale')
        if self.review != ReviewState.CANDIDATE:
            text(self.reviewed_by, 'reviewed_by')
            iso(self.reviewed_on)
        if self.review == ReviewState.REJECTED:
            text(self.rationale, 'rejection rationale')
        if self.reviewed_on is not None:
            iso(self.reviewed_on)


@dataclass(frozen=True)
class Entity:
    entity_id: str
    kind: EntityType
    label: str
    provenance: Provenance
    company_id: Optional[str] = None
    attributes: Tuple[Tuple[str, str], ...] = ()

    def __post_init__(self):
        object.__setattr__(self, 'kind', EntityType(self.kind))
        text(self.label, 'label')
        object.__setattr__(self, 'attributes', tuple(tuple(pair) for pair in self.attributes))
        if any(len(pair) != 2 for pair in self.attributes):
            raise ValueError('attributes must be name/value pairs')
        if len({pair[0] for pair in self.attributes}) != len(self.attributes):
            raise ValueError('duplicate attribute name')
        for key, value in self.attributes:
            text(key, 'attribute name')
            text(value, 'attribute value')
        if not isinstance(self.provenance, Provenance):
            raise ValueError('invalid provenance')
        if self.company_id is None:
            if self.kind not in (EntityType.MATERIAL, EntityType.MATERIAL_COHORT, EntityType.END_MARKET, EntityType.KPI):
                raise ValueError('this entity type requires company scope')
            prefix = f'concept:{self.kind.value}:'
        else:
            if not re.fullmatch(r'company:\d{10}', self.company_id):
                raise ValueError('invalid company scope')
            if self.kind == EntityType.COMPANY and self.entity_id == self.company_id:
                return
            prefix = f'{self.company_id}:{self.kind.value}:'
        if not self.entity_id.startswith(prefix) or not self.entity_id[len(prefix):]:
            raise ValueError('entity_id must use its scope and type namespace')


@dataclass(frozen=True)
class GraphClaim:
    claim_id: str
    subject: str
    relation: str
    object: str
    provenance: Provenance
    effective_date: Optional[str] = None

    def __post_init__(self):
        for field in ('claim_id', 'subject', 'object'):
            text(getattr(self, field), field)
        if not re.fullmatch(r'[a-z][a-z0-9_]*', self.relation):
            raise ValueError('relation must be snake_case')
        if not isinstance(self.provenance, Provenance):
            raise ValueError('invalid provenance')
        if self.effective_date is not None:
            iso(self.effective_date)


@dataclass(frozen=True)
class Observation:
    observation_id: str
    subject: str
    metric: str
    value: Optional[str]
    unit: str
    period_start: str
    period_end: str
    provenance: Provenance
    currency: Optional[str] = None
    availability: str = 'reported'
    basis: str = 'reported'
    input_ids: Tuple[str, ...] = ()
    derivation: Optional[str] = None
    assumptions: Tuple[str, ...] = ()
    related_entity_id: Optional[str] = None
    horizon: Optional[str] = None
    as_of: Optional[str] = None
    source_value: Optional[str] = None

    def __post_init__(self):
        for field in ('observation_id', 'subject', 'metric', 'unit'):
            text(getattr(self, field), field)
        if not isinstance(self.provenance, Provenance):
            raise ValueError('invalid provenance')
        iso(self.period_start)
        iso(self.period_end)
        if self.as_of is not None:
            iso(self.as_of)
        if self.horizon is not None:
            text(self.horizon, 'horizon')
        if self.period_start > self.period_end:
            raise ValueError('period_start exceeds period_end')
        if self.currency is not None and not re.fullmatch(r'[A-Z]{3}', self.currency):
            raise ValueError('currency must be three uppercase letters')
        if self.unit in ('currency', 'currency_thousands', 'currency_millions') and self.currency is None:
            raise ValueError('monetary observations require currency')
        if self.availability not in ('reported', 'missing', 'not_applicable'):
            raise ValueError('invalid availability')
        if self.availability == 'reported':
            try:
                if not isinstance(self.value, str) or not Decimal(self.value).is_finite():
                    raise ValueError('value must be a finite decimal string')
            except InvalidOperation as exc:
                raise ValueError('invalid decimal value') from exc
        elif self.value is not None:
            raise ValueError('missing/not_applicable values must be null')
        if self.basis not in ('reported', 'derived', 'assumed'):
            raise ValueError('invalid observation basis')
        object.__setattr__(self, 'input_ids', tuple(self.input_ids))
        object.__setattr__(self, 'assumptions', tuple(self.assumptions))
        for assumption in self.assumptions:
            text(assumption, 'assumption')
        if self.basis == 'derived':
            text(self.derivation, 'derivation')
            if not self.input_ids:
                raise ValueError('derived observations require inputs')
        if self.basis == 'assumed' and not self.assumptions:
            raise ValueError('assumed observations require assumptions')


@dataclass(frozen=True)
class GraphSnapshot:
    snapshot_id: str
    company_id: str
    document_ids: Tuple[str, ...]
    entities: Tuple[Entity, ...]
    claims: Tuple[GraphClaim, ...] = ()
    observations: Tuple[Observation, ...] = ()
    schema_version: int = 1

    def __post_init__(self):
        text(self.snapshot_id, 'snapshot_id')
        if self.schema_version != 1:
            raise ValueError('unsupported schema version')
        if not re.fullmatch(r'company:\d{10}', self.company_id):
            raise ValueError('invalid company_id')
        for field in ('document_ids', 'entities', 'claims', 'observations'):
            object.__setattr__(self, field, tuple(getattr(self, field)))
        if not self.document_ids or len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError('document_ids must be nonempty and unique')
        if any(not d.startswith(f'{self.company_id}:filing:') for d in self.document_ids):
            raise ValueError('cross-company document')
        ids = unique(self.entities, 'entity_id')
        unique(self.claims, 'claim_id')
        obs_ids = unique(self.observations, 'observation_id')
        if self.company_id not in ids:
            raise ValueError('snapshot requires company entity')
        for entity in self.entities:
            if entity.company_id not in (None, self.company_id):
                raise ValueError('cross-company entity')
        for claim in self.claims:
            if claim.subject not in ids or claim.object not in ids:
                raise ValueError('dangling claim endpoint')
        for obs in self.observations:
            if obs.subject not in ids:
                raise ValueError('dangling observation subject')
            if obs.related_entity_id is not None and obs.related_entity_id not in ids:
                raise ValueError('dangling observation related entity')
            if any(i not in obs_ids or i == obs.observation_id for i in obs.input_ids):
                raise ValueError('invalid derivation input')
        dependencies = {o.observation_id: o.input_ids for o in self.observations}
        visited, active = set(), set()

        def visit(observation_id):
            if observation_id in active:
                raise ValueError('cyclic derivation inputs')
            if observation_id in visited:
                return
            active.add(observation_id)
            for dependency in dependencies[observation_id]:
                visit(dependency)
            active.remove(observation_id)
            visited.add(observation_id)

        for observation_id in dependencies:
            visit(observation_id)
        for record in (*self.entities, *self.claims, *self.observations):
            if record.provenance.review != ReviewState.ACCEPTED:
                raise ValueError('snapshots contain accepted records only')
            if any(c.document_id not in self.document_ids for c in record.provenance.citations):
                raise ValueError('citation outside snapshot documents')

    def to_json(self):
        return json.dumps(asdict(self), sort_keys=True, indent=2, allow_nan=False)

    @classmethod
    def from_json(cls, payload):
        data = json.loads(payload)
        def provenance(p):
            p = dict(p)
            p['citations'] = tuple(Citation(c['document_id'], c['locator'], EvidenceReference.from_mapping(c['reference'])) for c in p['citations'])
            return Provenance(**p)
        for key, factory in (('entities', Entity), ('claims', GraphClaim), ('observations', Observation)):
            data[key] = tuple(factory(**{**r, 'provenance': provenance(r['provenance'])}) for r in data[key])
        return cls(**data)

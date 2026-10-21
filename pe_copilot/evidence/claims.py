"""Validated relationship claims for evidence-aware graph construction."""

from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
import re
from typing import Any, Dict, Mapping, Optional


class EvidenceStatus(str, Enum):
    """Origin classification for a relationship, independent of economic weight."""

    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    SYNTHETIC = "synthetic"


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_date(value: Optional[str], field_name: str) -> Optional[str]:
    if value is None:
        return None
    value = _required_text(value, field_name)
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field_name} must use ISO YYYY-MM-DD format") from error
    return value


@dataclass(frozen=True)
class EvidenceReference:
    """Public source supporting a confirmed relationship claim."""

    source_document: str
    evidence_excerpt: str
    source_url: Optional[str] = None
    filing_date: Optional[str] = None
    evidence_section: Optional[str] = None
    effective_date: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_document",
            _required_text(self.source_document, "source_document"),
        )
        object.__setattr__(
            self,
            "evidence_excerpt",
            _required_text(self.evidence_excerpt, "evidence_excerpt"),
        )
        object.__setattr__(self, "filing_date", _optional_date(self.filing_date, "filing_date"))
        object.__setattr__(
            self,
            "effective_date",
            _optional_date(self.effective_date, "effective_date"),
        )
        if self.source_url is not None:
            object.__setattr__(self, "source_url", _required_text(self.source_url, "source_url"))
        if self.evidence_section is not None:
            object.__setattr__(
                self,
                "evidence_section",
                _required_text(self.evidence_section, "evidence_section"),
            )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "EvidenceReference":
        return cls(
            source_document=data.get("source_document"),
            evidence_excerpt=data.get("evidence_excerpt"),
            source_url=data.get("source_url"),
            filing_date=data.get("filing_date"),
            evidence_section=data.get("evidence_section"),
            effective_date=data.get("effective_date"),
        )


@dataclass(frozen=True)
class RelationshipClaim:
    """A validated graph relationship with an explicit evidence classification."""

    subject: str
    relation: str
    object: str
    evidence_status: EvidenceStatus
    evidence: Optional[EvidenceReference] = None
    rationale: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", _required_text(self.subject, "subject"))
        relation = _required_text(self.relation, "relation").lower().replace(" ", "_")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", relation):
            raise ValueError("relation must be a lower snake_case identifier")
        object.__setattr__(self, "relation", relation)
        object.__setattr__(self, "object", _required_text(self.object, "object"))

        try:
            status = EvidenceStatus(self.evidence_status)
        except (TypeError, ValueError) as error:
            allowed = ", ".join(status.value for status in EvidenceStatus)
            raise ValueError(f"evidence_status must be one of: {allowed}") from error
        object.__setattr__(self, "evidence_status", status)

        if status is EvidenceStatus.CONFIRMED and self.evidence is None:
            raise ValueError("confirmed claims require an evidence reference")
        if status is not EvidenceStatus.CONFIRMED and self.evidence is None:
            object.__setattr__(self, "rationale", _required_text(self.rationale, "rationale"))
        elif self.rationale is not None:
            object.__setattr__(self, "rationale", _required_text(self.rationale, "rationale"))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "RelationshipClaim":
        evidence_data = data.get("evidence")
        evidence = (
            EvidenceReference.from_mapping(evidence_data)
            if isinstance(evidence_data, Mapping)
            else None
        )
        return cls(
            subject=data.get("subject"),
            relation=data.get("relation"),
            object=data.get("object"),
            evidence_status=data.get("evidence_status"),
            evidence=evidence,
            rationale=data.get("rationale"),
        )

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["evidence_status"] = self.evidence_status.value
        return data

"""
models.py
=========
Canonical data schemas for the Multi-Source Candidate Data Transformer.

Design Principles:
  - Every field is Optional-by-default inside the *internal* canonical model so
    that we never invent data. Validation constraints are applied downstream in
    the projection / validation broker.
  - Pydantic v2 is used for fast, standards-compliant runtime validation.
  - The Runtime Config schema mirrors the JSON contract verbatim so config
    files can be loaded without a separate parsing step.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class OnMissingPolicy(str, Enum):
    """Controls how the Validation & Output Broker reacts to absent required fields."""

    NULL = "null"  # Emit the field with a JSON `null` value.
    OMIT = "omit"  # Drop the field from the output object entirely.
    ERROR = "error"  # Raise a hard MissingRequiredFieldError.


# ---------------------------------------------------------------------------
# Sub-schemas used inside the Canonical Profile
# ---------------------------------------------------------------------------


class Location(BaseModel):
    """Geo-location expressed with ISO-3166 alpha-2 country code."""

    city: Optional[str] = None
    region: Optional[str] = None
    country: Optional[str] = Field(
        default=None,
        description="ISO-3166 alpha-2 country code, e.g. 'IN', 'US'.",
        min_length=2,
        max_length=2,
    )


class Links(BaseModel):
    """Social and professional profile URLs."""

    linkedin: Optional[str] = None
    github: Optional[str] = None
    portfolio: Optional[str] = None
    other: List[str] = Field(default_factory=list)


class Skill(BaseModel):
    """
    A single canonical skill entry.

    confidence — float in [0.0, 1.0]:
      * 1.0  = directly declared and mapped to a known canonical name
      * 0.85 = inferred / extracted but mapped with high certainty
      * 0.5  = fuzzy match against skill taxonomy
      * 0.0  = raw string kept verbatim; could not be normalised
    """

    name: str
    confidence: float = Field(ge=0.0, le=1.0)
    sources: List[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def _round_confidence(cls, v: float) -> float:
        return round(v, 4)


class ExperienceEntry(BaseModel):
    """A single employment/project record."""

    company: Optional[str] = None
    title: Optional[str] = None
    start: Optional[str] = Field(
        default=None,
        description="ISO YYYY-MM format, e.g. '2024-05'.",
        pattern=r"^\d{4}-\d{2}$",
    )
    end: Optional[str] = Field(
        default=None,
        description=(
            "ISO YYYY-MM format or the sentinel 'present' when the role is ongoing."
            " Pipeline normalises 'Present'/'Current'/'Now' → '2026-06' then tags "
            " provenance with method='relative_sentinel'."
        ),
        pattern=r"^(\d{4}-\d{2}|present)$",
    )
    summary: Optional[str] = None


class EducationEntry(BaseModel):
    """A single educational credential."""

    institution: Optional[str] = None
    degree: Optional[str] = None
    field: Optional[str] = None
    end_year: Optional[int] = Field(default=None, ge=1900, le=2100)


class ProvenanceRecord(BaseModel):
    """
    Lineage tag attached to every field that passed through the pipeline.

    method examples:
      - 'direct_map'         — value taken verbatim from source
      - 'normalised'         — value was transformed (phone → E.164, etc.)
      - 'authority_merge'    — chosen from competing sources by SAM score
      - 'relative_sentinel'  — "Present" converted to fixed epoch
      - 'default'            — field was absent; default applied
      - 'invalid_input'      — raw value failed validation; field set to None
    """

    field: str
    source: str
    method: str


# ---------------------------------------------------------------------------
# Core canonical candidate profile (internal representation)
# ---------------------------------------------------------------------------


class CanonicalProfile(BaseModel):
    """
    Internal unified candidate profile.

    This is the *single source of truth* that all pipeline stages read from and
    write to.  The Projection Layer then reshapes / renames this into whatever
    the runtime config requests.
    """

    candidate_id: str
    full_name: Optional[str] = None
    emails: List[str] = Field(default_factory=list)
    phones: List[str] = Field(
        default_factory=list,
        description="E.164-formatted phone numbers, e.g. '+919876543210'.",
    )
    location: Location = Field(default_factory=Location)
    links: Links = Field(default_factory=Links)
    headline: Optional[str] = None
    years_experience: Optional[float] = None
    skills: List[Skill] = Field(default_factory=list)
    experience: List[ExperienceEntry] = Field(default_factory=list)
    education: List[EducationEntry] = Field(default_factory=list)
    provenance: List[ProvenanceRecord] = Field(default_factory=list)

    # Internal bookkeeping — not emitted in the final JSON output.
    _raw_sources: Dict[str, Any] = {}

    def add_provenance(self, field: str, source: str, method: str) -> None:
        """Convenience helper to append a lineage record."""
        self.provenance.append(
            ProvenanceRecord(field=field, source=source, method=method)
        )

    def model_dump_output(self, include_confidence: bool = True) -> Dict[str, Any]:
        """
        Serialise the profile for output, optionally stripping confidence scores.
        The internal `_raw_sources` cache is always excluded.
        """
        data = self.model_dump(exclude_none=False)
        if not include_confidence:
            for skill in data.get("skills", []):
                skill.pop("confidence", None)
                skill.pop("sources", None)
        return data


# ---------------------------------------------------------------------------
# Runtime configuration schema
# ---------------------------------------------------------------------------


class FieldSpec(BaseModel):
    """
    A single field mapping directive inside the runtime configuration.

    Attributes
    ----------
    path:
        The *output* key name (e.g. ``"full_name"``).
    from_path:
        Optional path into the canonical model to read from.
        Supports array indexing (``"emails[0]"``) and array-of-object attribute
        extraction (``"skills[].name"``).  When omitted, ``path`` is used as
        the source key on the canonical model directly.
    type:
        Expected output type — ``"string"``, ``"string[]"``, ``"number"`` …
    required:
        If True and the value resolves to None, the ``on_missing`` policy applies.
    normalize:
        An optional normalization hint for the projection layer:
        ``"E164"`` → re-validate E.164 formatting;
        ``"canonical"`` → re-apply skill canonicalization.
    """

    path: str
    from_path: Optional[str] = Field(default=None, alias="from")
    type: str = "string"
    required: bool = False
    normalize: Optional[str] = None

    model_config = {"populate_by_name": True}


class RuntimeConfig(BaseModel):
    """
    Top-level runtime projection configuration.

    Loaded from ``runtime_config.json`` and passed into the Projection Layer.
    """

    fields: List[FieldSpec] = Field(default_factory=list)
    include_confidence: bool = True
    on_missing: OnMissingPolicy = OnMissingPolicy.NULL

    @classmethod
    def from_file(cls, path: str) -> "RuntimeConfig":
        """Load and validate a runtime config from a JSON file path."""
        import json
        from pathlib import Path

        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(raw)


# ---------------------------------------------------------------------------
# Source Authority Matrix (SAM) — deterministic merge priority weights
# ---------------------------------------------------------------------------


class SourceAuthorityMatrix(BaseModel):
    """
    Weights used by the Data Merge Engine.

    Higher score → this source wins when field values conflict.

    Default weights reflect common real-world trust levels:
      * ATS systems are canonical for names, titles, and contact info.
      * GitHub/social profiles are more reliable for technical skills.
      * Both sources are equally trusted for email (used as identity key).
    """

    weights: Dict[str, Dict[str, float]] = Field(
        default_factory=lambda: {
            # field_name → { source_label: authority_score }
            "full_name":        {"ats": 0.9, "github": 0.6, "linkedin": 0.85, "recruiter_csv": 0.7},
            "emails":           {"ats": 1.0, "github": 1.0, "linkedin": 1.0,  "recruiter_csv": 0.9},
            "phones":           {"ats": 0.9, "github": 0.1, "linkedin": 0.7,  "recruiter_csv": 0.85},
            "location":         {"ats": 0.85,"github": 0.4, "linkedin": 0.8,  "recruiter_csv": 0.75},
            "title":            {"ats": 0.95,"github": 0.5, "linkedin": 0.9,  "recruiter_csv": 0.8},
            "skills":           {"ats": 0.4, "github": 0.9, "linkedin": 0.85, "recruiter_csv": 0.6},
            "experience":       {"ats": 0.8, "github": 0.7, "linkedin": 0.9,  "recruiter_csv": 0.65},
            "headline":         {"ats": 0.3, "github": 0.8, "linkedin": 0.95, "recruiter_csv": 0.4},
            "education":        {"ats": 0.5, "github": 0.3, "linkedin": 0.95, "recruiter_csv": 0.5},
            "years_experience": {"ats": 0.7, "github": 0.3, "linkedin": 0.6,  "recruiter_csv": 0.9},
            "_default":         {"ats": 0.7, "github": 0.5, "linkedin": 0.7,  "recruiter_csv": 0.6},
        }
    )

    def score(self, field: str, source: str) -> float:
        """Return the authority score for ``source`` on ``field``."""
        field_weights = self.weights.get(field, self.weights["_default"])
        return field_weights.get(source, 0.5)

    def winner(self, field: str, candidates: Dict[str, Any]) -> tuple[str, Any]:
        """
        Return ``(winning_source, winning_value)`` for a field given a dict of
        ``{ source_label: value }`` candidates.  Ties go to the first insertion
        order (Python 3.7+ dicts preserve order).
        """
        best_source = max(candidates, key=lambda s: self.score(field, s))
        return best_source, candidates[best_source]

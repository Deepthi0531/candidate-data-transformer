"""
pipeline.py
Core ingestion, identity resolution, conflict merging, and projection engine
for the Multi-Source Candidate Data Transformer.

Architecture stages (in order):
  1. Ingestion Layer          — reads raw disparate source files via generators
  2. Normalization Layer      — converts raw fields to clean canonical standards
  3. Identity Resolution      — matches records to existing candidate entities
  4. Data Merge Engine        — resolves conflicts via Source Authority Matrix
  5. Projection Layer         — reshapes canonical model per runtime config
  6. Validation & Output      — enforces on_missing policy and structural rules

Design rules:
  - Generators (``yield``) are used throughout ingestion for memory efficiency.
  - No field is ever invented; absent values stay None and are flagged.
  - Every mutation to the canonical profile is accompanied by a provenance record.
  - All exceptions inside per-field processing are caught and turned into
    (None, 0.0, 'invalid_input') outcomes so the rest of the file keeps running.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Tuple

from models import (
    CanonicalProfile,
    EducationEntry,
    ExperienceEntry,
    FieldSpec,
    Links,
    Location,
    OnMissingPolicy,
    RuntimeConfig,
    Skill,
    SourceAuthorityMatrix,
)
from normalizers import (
    canonicalize_skill,
    deduplicate_skills,
    normalize_city_from_compound,
    normalize_country,
    normalize_date,
    normalize_email,
    normalize_linkedin_url,
    normalize_name,
    normalize_phone,
    normalize_string,
    normalize_years_experience,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Custom Exceptions
# ============================================================================


class MissingRequiredFieldError(RuntimeError):
    """Raised by the Validation Broker when on_missing='error' and a required
    field is absent in the projected output."""

    def __init__(self, field: str) -> None:
        super().__init__(
            f"Required field '{field}' is missing from the canonical profile "
            "and on_missing policy is set to 'error'."
        )
        self.field = field


class InvalidSourceFormatError(ValueError):
    """Raised when an ingested source file cannot be parsed into the expected
    raw dictionary shape."""


# ============================================================================
# Stage 1 — Ingestion Layer
# ============================================================================


def _load_json(path: Path) -> Any:
    """Read and parse a JSON file, raising a descriptive error on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InvalidSourceFormatError(f"JSON parse error in '{path}': {exc}") from exc


def ingest_ats_source(path: Path) -> Generator[Dict[str, Any], None, None]:
    """
    Ingestion adapter for ATS (Applicant Tracking System) JSON files.

    Expected format: a JSON array of candidate objects.
    Yields one raw dict per candidate, tagged with ``_source_label``.

    Memory model: the file is read once; each record is yielded individually
    so downstream stages process one candidate at a time.
    """
    raw = _load_json(path)
    if not isinstance(raw, list):
        raise InvalidSourceFormatError(
            f"ATS source '{path}' must be a JSON array; got {type(raw).__name__}."
        )
    for record in raw:
        if not isinstance(record, dict):
            logger.warning("Skipping non-dict ATS record: %r", record)
            continue
        record["_source_label"] = "ats"
        yield record


def ingest_github_source(path: Path) -> Generator[Dict[str, Any], None, None]:
    """
    Ingestion adapter for GitHub profile JSON files.

    Expected format: either a single JSON object OR a JSON array of profile objects.
    Yields one raw dict per profile, tagged with ``_source_label``.
    """
    raw = _load_json(path)
    if isinstance(raw, dict):
        raw["_source_label"] = "github"
        yield raw
    elif isinstance(raw, list):
        for record in raw:
            if not isinstance(record, dict):
                logger.warning("Skipping non-dict GitHub record: %r", record)
                continue
            record["_source_label"] = "github"
            yield record
    else:
        raise InvalidSourceFormatError(
            f"GitHub source '{path}' must be a JSON object or array; got {type(raw).__name__}."
        )


def ingest_linkedin_source(path: Path) -> Generator[Dict[str, Any], None, None]:
    """
    Ingestion adapter for LinkedIn profile JSON files.

    Expected format: a JSON array of LinkedIn profile objects.
    Yields one raw dict per profile, tagged with ``_source_label``.
    """
    raw = _load_json(path)
    if not isinstance(raw, list):
        raise InvalidSourceFormatError(
            f"LinkedIn source '{path}' must be a JSON array; got {type(raw).__name__}."
        )
    for record in raw:
        if not isinstance(record, dict):
            logger.warning("Skipping non-dict LinkedIn record: %r", record)
            continue
        record["_source_label"] = "linkedin"
        yield record


def ingest_recruiter_csv_source(path: Path) -> Generator[Dict[str, Any], None, None]:
    """
    Ingestion adapter for Recruiter CSV files.

    Expected format: CSV with header row. Any row with all-blank critical fields
    is silently skipped. Yields one raw dict per non-empty row, tagged with
    ``_source_label``.

    Memory model: reads the entire file once into memory to support csv.DictReader,
    then yields records individually.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")  # strip BOM if present
    except OSError as exc:
        raise InvalidSourceFormatError(f"Cannot read CSV '{path}': {exc}") from exc

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise InvalidSourceFormatError(f"CSV '{path}' appears to be empty.")

    for row_num, row in enumerate(reader, start=2):  # row 1 = header
        # Skip rows where all values are blank (common in exported CSVs)
        if all(not v or not v.strip() for v in row.values()):
            logger.debug("Skipping blank CSV row %d in '%s'.", row_num, path)
            continue
        row["_source_label"] = "recruiter_csv"
        yield dict(row)


def ingest_sources(
    source_paths: List[Tuple[str, Path]],
) -> Generator[Dict[str, Any], None, None]:
    """
    Unified ingestion entry-point.  Dispatches each path to the correct
    source-specific adapter based on the source type label.

    Parameters
    ----------
    source_paths:
        List of ``(source_type, file_path)`` pairs.
        Supported source_types: ``"ats"``, ``"github"``.

    Yields
    ------
    Raw record dicts, each annotated with ``_source_label``.
    """
    _adapters = {
        "ats": ingest_ats_source,
        "github": ingest_github_source,
        "linkedin": ingest_linkedin_source,
        "recruiter_csv": ingest_recruiter_csv_source,
    }
    for source_type, path in source_paths:
        adapter = _adapters.get(source_type)
        if adapter is None:
            logger.warning(
                "No adapter registered for source type '%s'; skipping.", source_type
            )
            continue
        try:
            yield from adapter(path)
        except (InvalidSourceFormatError, OSError) as exc:
            logger.error("Failed to ingest '%s': %s", path, exc)


# ============================================================================
# Stage 2 — Normalization / Standardization Layer
# ============================================================================
# Each adapter below takes a raw source dict and returns a partially-filled
# CanonicalProfile together with a list of provenance records.  This keeps
# normalization logic decoupled from both ingestion and merging.


def _safe_normalize(
    fn, raw_value, field_name: str, source: str, profile: CanonicalProfile
):
    """
    Wrap any normalizer call in a defensive try/except.

    Returns the (value, confidence, method) triple from the normalizer, but
    falls back to (None, 0.0, 'invalid_input') and logs a warning if the
    normalizer itself raises an unexpected exception.
    """
    try:
        return fn(raw_value)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Normalizer error on field '%s' from source '%s': %s",
            field_name,
            source,
            exc,
        )
        return (None, 0.0, "invalid_input")


def normalize_ats_record(raw: Dict[str, Any]) -> CanonicalProfile:
    """
    Normalize a raw ATS record into a partial CanonicalProfile.

    ATS fields handled:
      candidate_id, name, email_address, phone_raw,
      organization + role_title (→ experience), country_name, city_name.
    """
    source = "ats"
    cid = raw.get("candidate_id", f"ats_{id(raw)}")
    profile = CanonicalProfile(candidate_id=cid)

    # --- full_name ---
    name_val, _, method = _safe_normalize(
        normalize_name, raw.get("name"), "full_name", source, profile
    )
    profile.full_name = name_val
    profile.add_provenance("full_name", source, method)

    # --- email ---
    email_val, _, method = _safe_normalize(
        normalize_email, raw.get("email_address"), "emails", source, profile
    )
    if email_val:
        profile.emails = [email_val]
        profile.add_provenance("emails", source, method)

    # --- phone ---
    phone_val, _, method = _safe_normalize(
        normalize_phone, raw.get("phone_raw"), "phones", source, profile
    )
    if phone_val:
        profile.phones = [phone_val]
        profile.add_provenance("phones", source, method)

    # --- location ---
    country_val, _, c_method = _safe_normalize(
        normalize_country, raw.get("country_name"), "location.country", source, profile
    )
    city_val, _, city_method = _safe_normalize(
        normalize_string, raw.get("city_name"), "location.city", source, profile
    )
    profile.location = Location(
        city=city_val,
        country=country_val,
    )
    if country_val:
        profile.add_provenance("location.country", source, c_method)
    if city_val:
        profile.add_provenance("location.city", source, city_method)

    # --- experience (from org + role) ---
    org, _, _ = _safe_normalize(
        normalize_string,
        raw.get("organization"),
        "experience[].company",
        source,
        profile,
    )
    title, _, _ = _safe_normalize(
        normalize_string, raw.get("role_title"), "experience[].title", source, profile
    )
    if org or title:
        profile.experience = [ExperienceEntry(company=org, title=title)]
        profile.add_provenance("experience", source, "direct_map")

    return profile


def normalize_github_record(raw: Dict[str, Any]) -> CanonicalProfile:
    """
    Normalize a raw GitHub profile record into a partial CanonicalProfile.

    GitHub fields handled:
      login (→ links.github), name, email, phone (defensive), bio (→ headline),
      skills_extracted (→ skills), history (→ experience).
    """
    source = "github"
    login = raw.get("login", "github_unknown")
    profile = CanonicalProfile(candidate_id=f"github_{login}")

    # --- full_name ---
    name_val, _, method = _safe_normalize(
        normalize_name, raw.get("name"), "full_name", source, profile
    )
    profile.full_name = name_val
    profile.add_provenance("full_name", source, method)

    # --- email ---
    email_val, _, method = _safe_normalize(
        normalize_email, raw.get("email"), "emails", source, profile
    )
    if email_val:
        profile.emails = [email_val]
        profile.add_provenance("emails", source, method)

    # --- phone (defensive — GitHub bio may have prose text) ---
    phone_val, phone_conf, method = _safe_normalize(
        normalize_phone, raw.get("phone"), "phones", source, profile
    )
    if phone_val and phone_conf > 0:
        profile.phones = [phone_val]
        profile.add_provenance("phones", source, method)
    else:
        # Log the bad value but do NOT crash — just omit and flag.
        bad_phone = raw.get("phone")
        if bad_phone:
            logger.info(
                "GitHub phone field '%s' is not a valid phone number; skipping.",
                bad_phone,
            )
        profile.add_provenance("phones", source, "invalid_input")

    # --- github link ---
    profile.links = Links(github=f"https://github.com/{login}")
    profile.add_provenance("links.github", source, "direct_map")

    # --- headline (from bio) ---
    bio_val, _, method = _safe_normalize(
        normalize_string, raw.get("bio"), "headline", source, profile
    )
    profile.headline = bio_val
    if bio_val:
        profile.add_provenance("headline", source, method)

    # --- skills ---
    raw_skills: List[str] = raw.get("skills_extracted", [])
    skill_tuples: List[Tuple[str, float, List[str]]] = []
    for s in raw_skills:
        canonical, conf, method = _safe_normalize(
            canonicalize_skill, s, "skills", source, profile
        )
        if canonical:
            skill_tuples.append((canonical, conf, [source]))
    deduped = deduplicate_skills(skill_tuples)
    profile.skills = [
        Skill(name=n, confidence=c, sources=srcs) for n, c, srcs in deduped
    ]
    if profile.skills:
        profile.add_provenance("skills", source, "taxonomy_normalised")

    # --- experience (from history) ---
    history: List[Dict[str, Any]] = raw.get("history", [])
    entries: List[ExperienceEntry] = []
    for job in history:
        company, _, _ = _safe_normalize(
            normalize_string,
            job.get("company"),
            "experience[].company",
            source,
            profile,
        )
        title, _, _ = _safe_normalize(
            normalize_string, job.get("role"), "experience[].title", source, profile
        )
        start_val, _, start_method = _safe_normalize(
            normalize_date, job.get("start"), "experience[].start", source, profile
        )
        end_val, _, end_method = _safe_normalize(
            normalize_date, job.get("end"), "experience[].end", source, profile
        )
        entries.append(
            ExperienceEntry(
                company=company,
                title=title,
                start=start_val,
                end=end_val,
            )
        )
        # Tag relative sentinel dates in provenance.
        if start_method == "relative_sentinel":
            profile.add_provenance("experience[].start", source, "relative_sentinel")
        if end_method == "relative_sentinel":
            profile.add_provenance("experience[].end", source, "relative_sentinel")
    profile.experience = entries
    if entries:
        profile.add_provenance("experience", source, "direct_map")

    return profile


def normalize_linkedin_record(raw: Dict[str, Any]) -> CanonicalProfile:
    source = "linkedin"
    url_val, _, _ = normalize_linkedin_url(raw.get("profile_url"))
    handle = (url_val or "").split("/in/")[-1].strip("/") or f"li_{id(raw)}"
    profile = CanonicalProfile(candidate_id=f"linkedin_{handle}")

    # --- full_name ---
    name_val, _, method = _safe_normalize(
        normalize_name, raw.get("full_name"), "full_name", source, profile
    )
    profile.full_name = name_val
    profile.add_provenance("full_name", source, method)

    # --- email ---
    email_val, _, method = _safe_normalize(
        normalize_email, raw.get("email"), "emails", source, profile
    )
    if email_val:
        profile.emails = [email_val]
        profile.add_provenance("emails", source, method)

    # --- linkedin link ---
    if url_val:
        profile.links = Links(linkedin=url_val)
        profile.add_provenance("links.linkedin", source, "normalised")

    # --- headline ---
    hl_val, _, method = _safe_normalize(
        normalize_string, raw.get("headline"), "headline", source, profile
    )
    profile.headline = hl_val
    if hl_val:
        profile.add_provenance("headline", source, method)

    # --- location (compound string: "City, Region, Country") ---
    location_raw = raw.get("location_raw")
    city_val, _, city_method = _safe_normalize(
        normalize_city_from_compound, location_raw, "location.city", source, profile
    )
    # Country is the last token after the last comma
    country_raw = location_raw.rsplit(",", 1)[-1].strip() if location_raw else None
    country_val, _, c_method = _safe_normalize(
        normalize_country, country_raw, "location.country", source, profile
    )
    profile.location = Location(city=city_val, country=country_val)
    if city_val:
        profile.add_provenance("location.city", source, city_method)
    if country_val:
        profile.add_provenance("location.country", source, c_method)

    # --- skills ---
    raw_skills: List[str] = raw.get("skills", [])
    skill_tuples: List[Tuple[str, float, List[str]]] = []
    for s in raw_skills:
        canonical, conf, method = _safe_normalize(
            canonicalize_skill, s, "skills", source, profile
        )
        if canonical:
            skill_tuples.append((canonical, conf, [source]))
    deduped = deduplicate_skills(skill_tuples)
    profile.skills = [
        Skill(name=n, confidence=c, sources=srcs) for n, c, srcs in deduped
    ]
    if profile.skills:
        profile.add_provenance("skills", source, "taxonomy_normalised")

    # --- experience ---
    exp_list: List[Dict[str, Any]] = raw.get("experience", [])
    entries: List[ExperienceEntry] = []
    for job in exp_list:
        company, _, _ = _safe_normalize(
            normalize_string, job.get("company"), "experience[].company", source, profile
        )
        title, _, _ = _safe_normalize(
            normalize_string, job.get("title"), "experience[].title", source, profile
        )
        start_val, _, start_method = _safe_normalize(
            normalize_date, job.get("start_date"), "experience[].start", source, profile
        )
        end_val, _, end_method = _safe_normalize(
            normalize_date, job.get("end_date"), "experience[].end", source, profile
        )
        entries.append(
            ExperienceEntry(company=company, title=title, start=start_val, end=end_val)
        )
        if start_method == "relative_sentinel":
            profile.add_provenance("experience[].start", source, "relative_sentinel")
        if end_method == "relative_sentinel":
            profile.add_provenance("experience[].end", source, "relative_sentinel")
    profile.experience = entries
    if entries:
        profile.add_provenance("experience", source, "direct_map")

    # --- education ---
    edu_list: List[Dict[str, Any]] = raw.get("education", [])
    edu_entries: List[EducationEntry] = []
    for edu in edu_list:
        institution, _, _ = _safe_normalize(
            normalize_string, edu.get("institution"), "education[].institution", source, profile
        )
        degree, _, _ = _safe_normalize(
            normalize_string, edu.get("degree"), "education[].degree", source, profile
        )
        field_val, _, _ = _safe_normalize(
            normalize_string, edu.get("field"), "education[].field", source, profile
        )
        end_year = edu.get("end_year")
        try:
            end_year_int = int(end_year) if end_year is not None else None
        except (ValueError, TypeError):
            end_year_int = None
        edu_entries.append(
            EducationEntry(
                institution=institution,
                degree=degree,
                field=field_val,
                end_year=end_year_int,
            )
        )
    profile.education = edu_entries
    if edu_entries:
        profile.add_provenance("education", source, "direct_map")

    return profile


def normalize_recruiter_csv_record(raw: Dict[str, Any]) -> CanonicalProfile:
    source = "recruiter_csv"
    cid = (raw.get("candidate_id") or "").strip() or f"csv_{id(raw)}"
    profile = CanonicalProfile(candidate_id=cid)

    # --- full_name ---
    name_val, _, method = _safe_normalize(
        normalize_name, raw.get("name"), "full_name", source, profile
    )
    profile.full_name = name_val
    profile.add_provenance("full_name", source, method)

    # --- email ---
    email_val, _, method = _safe_normalize(
        normalize_email, raw.get("email_address"), "emails", source, profile
    )
    if email_val:
        profile.emails = [email_val]
        profile.add_provenance("emails", source, method)

    # --- phone ---
    phone_val, _, method = _safe_normalize(
        normalize_phone, raw.get("phone_raw"), "phones", source, profile
    )
    if phone_val:
        profile.phones = [phone_val]
        profile.add_provenance("phones", source, method)

    # --- location ---
    country_val, _, c_method = _safe_normalize(
        normalize_country, raw.get("country_name"), "location.country", source, profile
    )
    city_val, _, city_method = _safe_normalize(
        normalize_string, raw.get("city_name"), "location.city", source, profile
    )
    profile.location = Location(city=city_val, country=country_val)
    if country_val:
        profile.add_provenance("location.country", source, c_method)
    if city_val:
        profile.add_provenance("location.city", source, city_method)

    # --- experience (from org + role) ---
    org, _, _ = _safe_normalize(
        normalize_string, raw.get("organization"), "experience[].company", source, profile
    )
    title, _, _ = _safe_normalize(
        normalize_string, raw.get("role_title"), "experience[].title", source, profile
    )
    if org or title:
        profile.experience = [ExperienceEntry(company=org, title=title)]
        profile.add_provenance("experience", source, "direct_map")

    # --- years_experience ---
    yoe_raw = raw.get("years_experience")
    if yoe_raw is not None and str(yoe_raw).strip():
        yoe, _, yoe_method = normalize_years_experience(str(yoe_raw).strip())
        if yoe is not None:
            profile.years_experience = yoe
            profile.add_provenance("years_experience", source, yoe_method)

    # --- skills (comma-separated in CSV) ---
    skills_raw_str = raw.get("skills_raw") or ""
    skill_tuples: List[Tuple[str, float, List[str]]] = []
    for s in skills_raw_str.split(","):
        s = s.strip()
        if not s:
            continue
        canonical, conf, method = _safe_normalize(
            canonicalize_skill, s, "skills", source, profile
        )
        if canonical:
            skill_tuples.append((canonical, conf, [source]))
    deduped = deduplicate_skills(skill_tuples)
    profile.skills = [
        Skill(name=n, confidence=c, sources=srcs) for n, c, srcs in deduped
    ]
    if profile.skills:
        profile.add_provenance("skills", source, "taxonomy_normalised")

    return profile


def normalize_record(raw: Dict[str, Any]) -> CanonicalProfile:
    """
    Dispatch a raw record to the correct normalizer based on its ``_source_label``.

    Returns a partially-populated CanonicalProfile.
    """
    label = raw.get("_source_label", "")
    if label == "ats":
        return normalize_ats_record(raw)
    if label == "github":
        return normalize_github_record(raw)
    if label == "linkedin":
        return normalize_linkedin_record(raw)
    if label == "recruiter_csv":
        return normalize_recruiter_csv_record(raw)
    raise ValueError(f"Unknown source label: '{label}'")


# ============================================================================
# Stage 3 — Identity Resolution Broker
# ============================================================================


class IdentityBroker:

    def __init__(self) -> None:
        # email → canonical_id
        self._email_index: Dict[str, str] = {}
        # canonical_id → CanonicalProfile
        self._registry: Dict[str, CanonicalProfile] = {}

    def resolve(self, partial: CanonicalProfile) -> Optional[CanonicalProfile]:
        """
        Look up an existing profile that matches the incoming partial profile.

        Returns the existing CanonicalProfile if found, else None.
        """
        for email in partial.emails:
            existing_id = self._email_index.get(email.lower())
            if existing_id:
                logger.debug(
                    "Identity resolved: email '%s' matched existing profile '%s'.",
                    email,
                    existing_id,
                )
                return self._registry[existing_id]
        return None

    def register(self, profile: CanonicalProfile) -> None:
        """Add a new canonical profile to the registry."""
        self._registry[profile.candidate_id] = profile
        for email in profile.emails:
            self._email_index[email.lower()] = profile.candidate_id

    def update_email_index(self, profile: CanonicalProfile) -> None:
        """Re-index a profile's emails after a merge (emails may have been added)."""
        for email in profile.emails:
            self._email_index[email.lower()] = profile.candidate_id

    def all_profiles(self) -> Generator[CanonicalProfile, None, None]:
        """Yield every resolved canonical profile."""
        yield from self._registry.values()


# ============================================================================
# Stage 4 — Data Merge Engine
# ============================================================================


def _merge_scalar(
    field: str,
    existing_value: Any,
    incoming_value: Any,
    existing_source: str,
    incoming_source: str,
    sam: SourceAuthorityMatrix,
) -> Tuple[Any, str]:
    """
    Choose between two scalar values for the same field using the SAM.

    Returns ``(winning_value, winning_source)``.
    """
    if existing_value is None:
        return (incoming_value, incoming_source)
    if incoming_value is None:
        return (existing_value, existing_source)
    # Both present — SAM decides.
    winning_source, winning_value = sam.winner(
        field,
        {existing_source: existing_value, incoming_source: incoming_value},
    )
    return (winning_value, winning_source)


def merge_profiles(
    base: CanonicalProfile,
    incoming: CanonicalProfile,
    sam: SourceAuthorityMatrix,
) -> CanonicalProfile:
    incoming_src = incoming.provenance[0].source if incoming.provenance else "unknown"

    # Determine the primary source of the base profile.
    base_src = base.provenance[0].source if base.provenance else "unknown"

    # --- full_name ---
    winner_name, winner_src = _merge_scalar(
        "full_name", base.full_name, incoming.full_name, base_src, incoming_src, sam
    )
    if winner_name != base.full_name:
        base.full_name = winner_name
        base.add_provenance("full_name", winner_src, "authority_merge")

    # --- emails (union) ---
    merged_emails = list(
        dict.fromkeys(base.emails + incoming.emails)
    )  # preserve order, dedup
    if merged_emails != base.emails:
        base.emails = merged_emails
        base.add_provenance("emails", incoming_src, "union_merge")

    # --- phones (union, preferring ATS) ---
    incoming_phones = [p for p in incoming.phones if p not in base.phones]
    if incoming_phones:
        if sam.score("phones", incoming_src) >= sam.score("phones", base_src):
            base.phones = incoming.phones + [
                p for p in base.phones if p not in incoming.phones
            ]
        else:
            base.phones = base.phones + incoming_phones
        base.add_provenance("phones", incoming_src, "authority_merge")

    # --- location (field-level merge) ---
    if base.location.city is None and incoming.location.city:
        base.location.city = incoming.location.city
        base.add_provenance("location.city", incoming_src, "authority_merge")
    if base.location.country is None and incoming.location.country:
        base.location.country = incoming.location.country
        base.add_provenance("location.country", incoming_src, "authority_merge")

    # --- links (union — different sources give different link types) ---
    if not base.links.github and incoming.links.github:
        base.links.github = incoming.links.github
        base.add_provenance("links.github", incoming_src, "direct_map")
    if not base.links.linkedin and incoming.links.linkedin:
        base.links.linkedin = incoming.links.linkedin
        base.add_provenance("links.linkedin", incoming_src, "direct_map")

    # --- headline ---
    winner_hl, winner_src_hl = _merge_scalar(
        "headline", base.headline, incoming.headline, base_src, incoming_src, sam
    )
    if winner_hl != base.headline:
        base.headline = winner_hl
        base.add_provenance("headline", winner_src_hl, "authority_merge")

    # --- skills (union + dedup, GitHub wins on confidence) ---
    all_skill_tuples: List[Tuple[str, float, List[str]]] = [
        (s.name, s.confidence, s.sources) for s in base.skills
    ] + [(s.name, s.confidence, s.sources) for s in incoming.skills]
    if all_skill_tuples:
        deduped = deduplicate_skills(all_skill_tuples)
        base.skills = [
            Skill(name=n, confidence=c, sources=srcs) for n, c, srcs in deduped
        ]
        base.add_provenance("skills", incoming_src, "union_merge")

    # --- experience (company-keyed merge) ---
    base_exp_map: Dict[str, ExperienceEntry] = {
        (e.company or "").lower(): e for e in base.experience
    }
    for inc_entry in incoming.experience:
        key = (inc_entry.company or "").lower()
        if key in base_exp_map:
            existing = base_exp_map[key]
            # Merge title: SAM decides.
            if existing.title and inc_entry.title and existing.title != inc_entry.title:
                _, title_winner = _merge_scalar(
                    "title",
                    existing.title,
                    inc_entry.title,
                    base_src,
                    incoming_src,
                    sam,
                )
                existing.title = title_winner
                base.add_provenance(
                    "experience[].title", incoming_src, "authority_merge"
                )
            # Fill missing dates from incoming.
            if not existing.start and inc_entry.start:
                existing.start = inc_entry.start
                base.add_provenance(
                    "experience[].start", incoming_src, "authority_merge"
                )
            if not existing.end and inc_entry.end:
                existing.end = inc_entry.end
                base.add_provenance("experience[].end", incoming_src, "authority_merge")
        else:
            base.experience.append(inc_entry)
            base_exp_map[key] = inc_entry
            base.add_provenance("experience", incoming_src, "authority_merge")

    # --- provenance: append incoming's records ---
    base.provenance.extend(incoming.provenance)

    return base


# ============================================================================
# Stage 5 — Decoupled Projection Layer
# ============================================================================

# Regex patterns for full-path matching (applied before any splitting).
# Handles paths like "skills[].name" or "experience[].title" holistically.
_FULL_ARRAY_ATTR_RE = re.compile(r"^(\w+)\[\]\.(\w+)$")  # skills[].name
_FULL_ARRAY_BARE_RE = re.compile(r"^(\w+)\[\]$")  # experience[]
_FULL_ARRAY_IDX_RE = re.compile(r"^(\w+)\[(\d+)\]$")  # emails[0]

# Segment-level patterns for nested dot-notation after the first simple key.
_SEG_ARRAY_IDX_RE = re.compile(r"^(\w+)\[(\d+)\]$")  # phones[0] inside a segment
_SEG_ARRAY_BARE_RE = re.compile(r"^(\w+)\[\]$")  # bare list key segment


def _resolve_path(data: Dict[str, Any], path: str) -> Any:
    if not path or not isinstance(data, dict):
        return None

    # ------------------------------------------------------------------
    # Priority 1: "skills[].name"  — full-path array-attribute extraction
    # ------------------------------------------------------------------
    m = _FULL_ARRAY_ATTR_RE.match(path)
    if m:
        key, attr = m.group(1), m.group(2)
        lst = data.get(key)
        if isinstance(lst, list):
            return [item.get(attr) if isinstance(item, dict) else None for item in lst]
        return None

    # ------------------------------------------------------------------
    # Priority 2: "experience[]"  — return the whole top-level list
    # ------------------------------------------------------------------
    m = _FULL_ARRAY_BARE_RE.match(path)
    if m:
        key = m.group(1)
        val = data.get(key)
        return val if isinstance(val, list) else None

    # ------------------------------------------------------------------
    # Priority 3: "emails[0]"  — top-level indexed access (no dot)
    # ------------------------------------------------------------------
    m = _FULL_ARRAY_IDX_RE.match(path)
    if m and "." not in path:
        key, idx = m.group(1), int(m.group(2))
        lst = data.get(key)
        if isinstance(lst, list) and idx < len(lst):
            return lst[idx]
        return None

    # ------------------------------------------------------------------
    # Priority 4: segment-by-segment dot-notation  ("location.city", etc.)
    # ------------------------------------------------------------------
    parts = path.split(".")
    current: Any = data

    for part in parts:
        if current is None:
            return None

        # Indexed segment inside a dot-path, e.g. "phones[0]" as a segment
        m_idx = _SEG_ARRAY_IDX_RE.match(part)
        if m_idx:
            key, idx = m_idx.group(1), int(m_idx.group(2))
            lst = current.get(key) if isinstance(current, dict) else None
            if isinstance(lst, list) and idx < len(lst):
                current = lst[idx]
            else:
                return None
            continue

        # Bare-list segment, e.g. "items[]" inside a dot-path
        m_bare = _SEG_ARRAY_BARE_RE.match(part)
        if m_bare:
            key = m_bare.group(1)
            val = current.get(key) if isinstance(current, dict) else None
            current = val if isinstance(val, list) else None
            continue

        # Plain key
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None

    return current


def _apply_projection_normalize(value: Any, hint: Optional[str]) -> Any:
    if hint is None or value is None:
        return value

    if hint == "E164":
        if isinstance(value, str):
            result, _, _ = normalize_phone(value)
            return result
        if isinstance(value, list):
            return [normalize_phone(v)[0] for v in value if normalize_phone(v)[0]]

    if hint == "canonical":
        if isinstance(value, str):
            name, _, _ = canonicalize_skill(value)
            return name
        if isinstance(value, list):
            return [canonicalize_skill(v)[0] for v in value if canonicalize_skill(v)[0]]

    return value


def project(
    profile: CanonicalProfile,
    config: RuntimeConfig,
) -> Dict[str, Any]:
    canonical_dict = profile.model_dump_output(
        include_confidence=config.include_confidence
    )
    output: Dict[str, Any] = {}

    for spec in config.fields:
        source_path = spec.from_path if spec.from_path else spec.path
        raw_value = _resolve_path(canonical_dict, source_path)

        # Apply projection-time normalization hint.
        value = _apply_projection_normalize(raw_value, spec.normalize)

        # Type coercion hints (best-effort; never raise).
        if spec.type == "string" and isinstance(value, list):
            value = value[0] if value else None
        elif spec.type == "string[]" and isinstance(value, str):
            value = [value]

        output[spec.path] = value

    # If include_confidence is True, attach skills with full metadata.
    # The projection already handles skills[].name extraction above;
    # when no explicit skills field spec is present, include the full list.
    if config.include_confidence and "skills" not in output:
        output["skills"] = canonical_dict.get("skills", [])

    return output


# ============================================================================
# Stage 6 — Validation & Output Broker
# ============================================================================


def validate_and_emit(
    projected: Dict[str, Any],
    config: RuntimeConfig,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    policy = config.on_missing

    for spec in config.fields:
        value = projected.get(spec.path)
        is_missing = value is None or value == [] or value == ""

        if is_missing and spec.required:
            if policy == OnMissingPolicy.ERROR:
                raise MissingRequiredFieldError(spec.path)
            elif policy == OnMissingPolicy.OMIT:
                continue  # Drop key entirely.
            else:  # NULL
                result[spec.path] = None
        elif is_missing and not spec.required:
            if policy == OnMissingPolicy.OMIT:
                continue
            else:
                result[spec.path] = None
        else:
            result[spec.path] = value

    # Pass-through any extra keys that were added outside the field spec
    # (e.g. full skills list when include_confidence=True).
    for key, val in projected.items():
        if key not in result:
            result[key] = val

    return result


# ============================================================================
# Top-Level Pipeline Orchestrator
# ============================================================================


def run_pipeline(
    source_paths: List[Tuple[str, Path]],
    config: RuntimeConfig,
    sam: Optional[SourceAuthorityMatrix] = None,
) -> Generator[Dict[str, Any], None, None]:
    if sam is None:
        sam = SourceAuthorityMatrix()

    broker = IdentityBroker()

    # --- Stages 1–4: Ingest → Normalize → Resolve → Merge ---
    for raw in ingest_sources(source_paths):
        try:
            partial = normalize_record(raw)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to normalize record from '%s': %s",
                raw.get("_source_label"),
                exc,
            )
            continue

        existing = broker.resolve(partial)
        if existing is None:
            # New candidate entity.
            broker.register(partial)
        else:
            # Known entity — merge incoming partial into the existing profile.
            merge_profiles(existing, partial, sam)
            broker.update_email_index(existing)

    # --- Stages 5–6: Project → Validate → Emit ---
    for profile in broker.all_profiles():
        try:
            projected = project(profile, config)
            output = validate_and_emit(projected, config)
            yield output
        except MissingRequiredFieldError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to project/emit profile '%s': %s", profile.candidate_id, exc
            )

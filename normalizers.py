"""
normalizers.py
==============
Pure, stateless normalization functions used across the pipeline.

Design rules:
  - Every function must be *pure*: same input always produces the same output,
    no side-effects, no global state mutation.
  - Each function returns a ``(value, confidence, method)`` triple so the
    caller can always attach correct provenance metadata.
  - On unrecoverable bad input the function returns ``(None, 0.0, 'invalid_input')``
    rather than raising — "wrong-but-confident is worse than honestly-empty."
  - All regex patterns are compiled once at module import time for performance.
"""

from __future__ import annotations

import re
import unicodedata
from calendar import month_abbr, month_name
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Type alias for the (value, confidence, method) return contract
# ---------------------------------------------------------------------------

NormResult = Tuple[Optional[str], float, str]


# ---------------------------------------------------------------------------
# Phone normalization — E.164
# ---------------------------------------------------------------------------

# Strips all non-digit characters (used to extract the pure digit string).
_DIGIT_RE = re.compile(r"[^\d]")
# Sentinel phrases that indicate the value is not a real phone number.
_NON_PHONE_PATTERNS = re.compile(
    r"\b(call|text|after|pm|am|email|contact|reach|whatsapp|dm|message|ring|buzz)\b",
    re.IGNORECASE,
)

# Country dial-code prefix map for the most common cases.
_COUNTRY_DIAL_CODES: Dict[str, str] = {
    "91": "IN",   # India
    "1": "US",    # USA / Canada
    "44": "GB",   # UK
    "49": "DE",   # Germany
    "61": "AU",   # Australia
    "86": "CN",   # China
    "81": "JP",   # Japan
    "65": "SG",   # Singapore
    "971": "AE",  # UAE
    "55": "BR",   # Brazil
    "52": "MX",   # Mexico
    "33": "FR",   # France
    "46": "SE",   # Sweden
    "47": "NO",   # Norway
    "234": "NG",  # Nigeria
    "880": "BD",  # Bangladesh
    "385": "HR",  # Croatia
    "82": "KR",   # South Korea
    "886": "TW",  # Taiwan
}


def normalize_phone(raw: Optional[str]) -> NormResult:
    """
    Attempt to convert a raw phone string into E.164 format.

    E.164 grammar: ``+`` followed by 7–15 digits, no separators.

    Returns
    -------
    (e164_string, confidence, method)
      * confidence 1.0  — already valid E.164
      * confidence 0.85 — digits re-assembled into valid E.164
      * confidence 0.0  — unparseable; caller should treat as None
    """
    if not raw or not raw.strip():
        return (None, 0.0, "empty_input")

    # Detect prose / non-numeric phone values immediately.
    if _NON_PHONE_PATTERNS.search(raw):
        return (None, 0.0, "invalid_input")

    # Extract pure digits only (strips spaces, dashes, dots, parens, and the '+').
    cleaned = _DIGIT_RE.sub("", raw)

    # Already has explicit '+' prefix: reconstruct as E.164.
    if raw.strip().startswith("+"):
        if 7 <= len(cleaned) <= 15:
            return ("+" + cleaned, 1.0, "direct_map")
        return (None, 0.0, "invalid_input")

    # No '+': digits-only string — validate ITU-T E.164 length (7–15 digits).
    if 7 <= len(cleaned) <= 15:
        # Country code is implicit; prepend '+' and mark confidence slightly lower.
        return ("+" + cleaned, 0.85, "normalised")

    return (None, 0.0, "invalid_input")


# ---------------------------------------------------------------------------
# Location / Country normalization — ISO-3166 alpha-2
# ---------------------------------------------------------------------------

# Exhaustive mapping of common country name variants → ISO-3166 alpha-2 codes.
_COUNTRY_LOOKUP: Dict[str, str] = {
    # Full names
    "india": "IN",
    "united states": "US",
    "usa": "US",
    "u.s.a.": "US",
    "united states of america": "US",
    "united kingdom": "GB",
    "uk": "GB",
    "great britain": "GB",
    "britain": "GB",
    "canada": "CA",
    "australia": "AU",
    "germany": "DE",
    "france": "FR",
    "japan": "JP",
    "china": "CN",
    "singapore": "SG",
    "netherlands": "NL",
    "holland": "NL",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "switzerland": "CH",
    "austria": "AT",
    "belgium": "BE",
    "spain": "ES",
    "italy": "IT",
    "portugal": "PT",
    "brazil": "BR",
    "brasil": "BR",
    "mexico": "MX",
    "méxico": "MX",
    "argentina": "AR",
    "south africa": "ZA",
    "nigeria": "NG",
    "kenya": "KE",
    "ethiopia": "ET",
    "egypt": "EG",
    "israel": "IL",
    "uae": "AE",
    "united arab emirates": "AE",
    "russia": "RU",
    "ukraine": "UA",
    "poland": "PL",
    "czechia": "CZ",
    "czech republic": "CZ",
    "romania": "RO",
    "hungary": "HU",
    "croatia": "HR",
    "new zealand": "NZ",
    "ireland": "IE",
    "scotland": "GB",
    "wales": "GB",
    "england": "GB",
    "korea": "KR",
    "south korea": "KR",
    "taiwan": "TW",
    "indonesia": "ID",
    "malaysia": "MY",
    "thailand": "TH",
    "vietnam": "VN",
    "philippines": "PH",
    "pakistan": "PK",
    "bangladesh": "BD",
    "sri lanka": "LK",
    "nepal": "NP",
    "turkey": "TR",
    "türkiye": "TR",
    "colombia": "CO",
    "chile": "CL",
    "peru": "PE",
    "venezuela": "VE",
    "ghana": "GH",
    "tanzania": "TZ",
    "morocco": "MA",
    "algeria": "DZ",
    "tunisia": "TN",
    # Already ISO codes (pass-through)
    "in": "IN",
    "us": "US",
    "gb": "GB",
    "ca": "CA",
    "au": "AU",
    "de": "DE",
    "fr": "FR",
    "jp": "JP",
    "cn": "CN",
    "sg": "SG",
    "nl": "NL",
    "se": "SE",
    "no": "NO",
    "dk": "DK",
    "fi": "FI",
    "ch": "CH",
    "at": "AT",
    "be": "BE",
    "es": "ES",
    "it": "IT",
    "pt": "PT",
    "br": "BR",
    "mx": "MX",
    "za": "ZA",
    "ng": "NG",
    "ae": "AE",
    "kr": "KR",
    "tw": "TW",
    "id": "ID",
    "my": "MY",
    "th": "TH",
    "vn": "VN",
    "ph": "PH",
    "pk": "PK",
    "bd": "BD",
    "et": "ET",
    "eg": "EG",
    "hr": "HR",
    "tr": "TR",
}


def normalize_country(raw: Optional[str]) -> NormResult:
    """
    Map a free-text country name or code to its ISO-3166 alpha-2 representation.

    Returns
    -------
    (iso_code, confidence, method)
      * confidence 1.0  — exact match or already a valid ISO code
      * confidence 0.75 — normalised (lowercased / stripped) match
      * confidence 0.0  — unknown country; returns None
    """
    if not raw or not raw.strip():
        return (None, 0.0, "empty_input")

    upper = raw.strip().upper()
    # Check if already a valid 2-letter ISO code (fast path).
    if upper in _COUNTRY_LOOKUP.values():
        return (upper, 1.0, "direct_map")

    key = raw.strip().lower()
    if key in _COUNTRY_LOOKUP:
        code = _COUNTRY_LOOKUP[key]
        method = "direct_map" if len(key) == 2 else "normalised"
        return (code, 1.0 if len(key) == 2 else 0.75, method)

    # Try partial / word-level match as a last resort.
    for name, code in _COUNTRY_LOOKUP.items():
        if name in key or key in name:
            return (code, 0.6, "fuzzy_match")

    return (None, 0.0, "invalid_input")


def normalize_city_from_compound(raw: Optional[str]) -> NormResult:
    """
    Extract the city name from a compound location string like
    'San Francisco, California, United States' or 'Berlin, Germany'.

    Returns the first component before the first comma.
    """
    if not raw or not raw.strip():
        return (None, 0.0, "empty_input")
    city = raw.split(",")[0].strip()
    if city:
        return (city, 0.9, "compound_split")
    return (None, 0.0, "invalid_input")


# ---------------------------------------------------------------------------
# Date normalization — YYYY-MM
# ---------------------------------------------------------------------------

# Relative sentinel tokens that mean "right now".
_PRESENT_TOKENS = re.compile(
    r"^\s*(present|current|now|ongoing|till\s*date|today|till date)\s*$",
    re.IGNORECASE,
)
# Epoch used to stamp relative "present" dates — matches assignment context.
PRESENT_EPOCH = "2026-06"

# Short month abbreviations (Jan, Feb, …) → zero-padded month number.
_MONTH_MAP: Dict[str, str] = {}
for _i, (_abbr, _full) in enumerate(
    zip(list(month_abbr)[1:], list(month_name)[1:]), start=1
):
    _MONTH_MAP[_abbr.lower()] = f"{_i:02d}"
    _MONTH_MAP[_full.lower()] = f"{_i:02d}"

# Pattern: "May 2024", "January 2023", etc.
_MONTH_YEAR_RE = re.compile(r"^(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})$")
# Pattern: "2024-05" or "2024/05"
_ISO_MONTH_RE = re.compile(r"^(?P<year>\d{4})[-/](?P<month>\d{1,2})$")
# Pattern: "MM/YYYY" or "MM-YYYY"
_MDY_RE = re.compile(r"^(?P<month>\d{1,2})[-/](?P<year>\d{4})$")


def normalize_date(raw: Optional[str]) -> NormResult:
    """
    Convert a variety of date string formats to YYYY-MM.

    Relative values ("Present", "Current", "Now", "ongoing") are mapped to
    the fixed system epoch (``PRESENT_EPOCH = "2026-06"``) and returned with
    method ``'relative_sentinel'`` so downstream provenance tracking can
    distinguish them from real dates.

    Returns
    -------
    (yyyy_mm_string, confidence, method)
    """
    if not raw or not raw.strip():
        return (None, 0.0, "empty_input")

    stripped = raw.strip()

    # --- Relative sentinel check ---
    if _PRESENT_TOKENS.match(stripped):
        return (PRESENT_EPOCH, 1.0, "relative_sentinel")

    # --- Already ISO YYYY-MM ---
    m = _ISO_MONTH_RE.match(stripped)
    if m:
        month = f"{int(m.group('month')):02d}"
        return (f"{m.group('year')}-{month}", 1.0, "direct_map")

    # --- Month-name + Year: "May 2024" ---
    m = _MONTH_YEAR_RE.match(stripped)
    if m:
        month_str = m.group("month").lower()
        month_num = _MONTH_MAP.get(month_str)
        if month_num:
            return (f"{m.group('year')}-{month_num}", 0.95, "normalised")

    # --- MM/YYYY or MM-YYYY ---
    m = _MDY_RE.match(stripped)
    if m:
        month = f"{int(m.group('month')):02d}"
        return (f"{m.group('year')}-{month}", 0.9, "normalised")

    # --- Year only ---
    if re.match(r"^\d{4}$", stripped):
        return (f"{stripped}-01", 0.5, "year_only_approximation")

    return (None, 0.0, "invalid_input")


# ---------------------------------------------------------------------------
# String / name normalization
# ---------------------------------------------------------------------------

_WHITESPACE_RE = re.compile(r"\s+")
# Detect strings that are literally the word "null" or "none" (garbage inputs)
_NULL_SENTINEL_RE = re.compile(r"^(null|none|n/a|na|undefined|-)$", re.IGNORECASE)


def normalize_string(raw: Optional[str]) -> NormResult:
    """
    Collapse excess whitespace, strip leading/trailing whitespace, and
    normalise Unicode to NFC form.  Returns None for blank/empty inputs
    and for sentinel strings like 'null', 'none', 'n/a'.
    """
    if not raw:
        return (None, 0.0, "empty_input")
    cleaned = unicodedata.normalize("NFC", raw.strip())
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    if not cleaned:
        return (None, 0.0, "empty_input")
    # Reject string-literal "null" / "none" injected from upstream systems.
    if _NULL_SENTINEL_RE.match(cleaned):
        return (None, 0.0, "null_sentinel_rejected")
    return (cleaned, 1.0, "normalised")


def normalize_name(raw: Optional[str]) -> NormResult:
    """
    Normalize a human name:
      - Strip extra whitespace.
      - Title-case initials that are fully uppercase (all-caps artefacts).
      - Remove lone middle initials' trailing dots if the rest looks like a name.
      - Reject string-literal 'null' / 'none' sentinel values.

    Returns
    -------
    (cleaned_name, confidence, method)

    Confidence is 0.9 instead of 1.0 because name transformation is heuristic.
    """
    value, conf, method = normalize_string(raw)
    if value is None:
        return (None, 0.0, method)

    # Reject literal sentinel strings passed as names.
    if _NULL_SENTINEL_RE.match(value):
        return (None, 0.0, "null_sentinel_rejected")

    # Remove trailing dots on middle initials: "Krishna B. Bhat" stays as-is,
    # but "KRISHNA B. BHAT" → "Krishna B. Bhat".
    if value.isupper():
        value = value.title()
        return (value, 0.9, "normalised")

    return (value, 1.0, "direct_map")


# ---------------------------------------------------------------------------
# Email normalization
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def normalize_email(raw: Optional[str]) -> NormResult:
    """
    Lowercase-normalise and structurally validate an email address.
    Does NOT do SMTP verification — purely syntactic.

    Returns
    -------
    (email_lower, confidence, method)
    """
    if not raw or not raw.strip():
        return (None, 0.0, "empty_input")
    cleaned = raw.strip().lower()
    # Reject system/no-reply addresses
    if cleaned.startswith("no-reply@") or cleaned.startswith("noreply@"):
        return (None, 0.0, "system_email_rejected")
    if _EMAIL_RE.match(cleaned):
        return (cleaned, 1.0, "normalised")
    return (None, 0.0, "invalid_input")


# ---------------------------------------------------------------------------
# LinkedIn URL normalization
# ---------------------------------------------------------------------------

_LINKEDIN_URL_RE = re.compile(
    r"https?://(www\.)?linkedin\.com/in/(?P<handle>[a-zA-Z0-9\-_%]+)/?$"
)


def normalize_linkedin_url(raw: Optional[str]) -> NormResult:
    """
    Validate and normalise a LinkedIn profile URL.

    Returns a canonical https://linkedin.com/in/<handle> form, or None
    if the URL doesn't look like a real LinkedIn profile.
    """
    if not raw or not raw.strip():
        return (None, 0.0, "empty_input")
    m = _LINKEDIN_URL_RE.match(raw.strip())
    if m:
        handle = m.group("handle")
        if len(handle) < 2:  # Empty or trivially short handle — ghost profile
            return (None, 0.0, "invalid_input")
        return (f"https://linkedin.com/in/{handle}", 1.0, "normalised")
    return (None, 0.0, "invalid_input")


# ---------------------------------------------------------------------------
# Years-of-experience calculation
# ---------------------------------------------------------------------------


def normalize_years_experience(raw: Optional[str]) -> Tuple[Optional[float], float, str]:
    """
    Parse a raw years_experience string (e.g. '4', '4.5', '12') into a float.

    Returns
    -------
    (years_float, confidence, method)
    """
    if raw is None:
        return (None, 0.0, "empty_input")
    try:
        years = float(str(raw).strip())
        if years < 0 or years > 60:
            return (None, 0.0, "out_of_range")
        return (years, 1.0, "direct_map")
    except (ValueError, TypeError):
        return (None, 0.0, "invalid_input")


# ---------------------------------------------------------------------------
# Skill canonicalization
# ---------------------------------------------------------------------------

# Skill synonym table → canonical name.
# Keys are lowercase; values are the display-canonical forms.
SKILL_TAXONOMY: Dict[str, str] = {
    # Python family
    "python": "Python",
    "python3": "Python",
    "python 3": "Python",
    "py": "Python",
    "cpython": "Python",
    # Go / Golang
    "go": "Go",
    "golang": "Go",
    # SQL family
    "sql": "SQL",
    "mysql": "MySQL",
    "postgresql": "PostgreSQL",
    "postgres": "PostgreSQL",
    "sqlite": "SQLite",
    "mssql": "SQL Server",
    "sql server": "SQL Server",
    "bigquery": "BigQuery",
    "snowflake": "Snowflake",
    "redshift": "Redshift",
    "dbt": "dbt",
    # Data / ML
    "pandas": "Pandas",
    "numpy": "NumPy",
    "scikit-learn": "scikit-learn",
    "sklearn": "scikit-learn",
    "tensorflow": "TensorFlow",
    "tf": "TensorFlow",
    "pytorch": "PyTorch",
    "torch": "PyTorch",
    "spark": "Apache Spark",
    "apache spark": "Apache Spark",
    "kafka": "Apache Kafka",
    "apache kafka": "Apache Kafka",
    "airflow": "Apache Airflow",
    "apache airflow": "Apache Airflow",
    "mlflow": "MLflow",
    "huggingface": "HuggingFace",
    "hugging face": "HuggingFace",
    "transformers": "HuggingFace",
    "nlp": "NLP",
    "llm": "LLM",
    "langchain": "LangChain",
    "tableau": "Tableau",
    "powerbi": "Power BI",
    "power bi": "Power BI",
    "looker": "Looker",
    "research": "Research",
    # Web
    "javascript": "JavaScript",
    "js": "JavaScript",
    "typescript": "TypeScript",
    "ts": "TypeScript",
    "react": "React",
    "reactjs": "React",
    "react native": "React Native",
    "vue": "Vue.js",
    "vuejs": "Vue.js",
    "angular": "Angular",
    "node": "Node.js",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "graphql": "GraphQL",
    "grpc": "gRPC",
    "css": "CSS",
    "html": "HTML",
    # Systems / DevOps / Cloud
    "docker": "Docker",
    "kubernetes": "Kubernetes",
    "k8s": "Kubernetes",
    "aws": "AWS",
    "amazon web services": "AWS",
    "gcp": "GCP",
    "google cloud": "GCP",
    "azure": "Azure",
    "terraform": "Terraform",
    "git": "Git",
    "linux": "Linux",
    "bash": "Bash",
    "shell": "Shell",
    "prometheus": "Prometheus",
    "grafana": "Grafana",
    "databricks": "Databricks",
    "figma": "Figma",
    # Other languages
    "java": "Java",
    "kotlin": "Kotlin",
    "scala": "Scala",
    "rust": "Rust",
    "c++": "C++",
    "cpp": "C++",
    "c#": "C#",
    "csharp": "C#",
    "ruby": "Ruby",
    "rails": "Ruby on Rails",
    "ruby on rails": "Ruby on Rails",
    "php": "PHP",
    "swift": "Swift",
    "r": "R",
    "spring boot": "Spring Boot",
    "spring": "Spring Boot",
    "microservices": "Microservices",
    "firebase": "Firebase",
    "mongodb": "MongoDB",
    "selenium": "Selenium",
    "pytest": "pytest",
    "data modeling": "Data Modeling",
    "power bi": "Power BI",
}


def canonicalize_skill(raw: Optional[str]) -> Tuple[str, float, str]:
    """
    Map a raw skill string to its canonical taxonomy entry.

    Returns
    -------
    (canonical_name, confidence, method)
      * confidence 1.0  — exact match in taxonomy
      * confidence 0.75 — partial / normalised match (e.g. "Python3" → "Python")
      * confidence 0.5  — kept verbatim with title-casing; not in taxonomy
    """
    if not raw or not raw.strip():
        return ("", 0.0, "empty_input")

    key = raw.strip().lower()
    if key in SKILL_TAXONOMY:
        canonical = SKILL_TAXONOMY[key]
        method = "taxonomy_exact" if raw.strip() == canonical else "taxonomy_normalised"
        conf = 1.0 if raw.strip() == canonical else 0.9
        return (canonical, conf, method)

    # Title-cased fallback.
    fallback = raw.strip().title()
    return (fallback, 0.5, "taxonomy_unknown")


def deduplicate_skills(
    skills: List[Tuple[str, float, List[str]]],
) -> List[Tuple[str, float, List[str]]]:
    """
    Deduplicate skill entries by canonical name (case-insensitive).

    When the same canonical skill appears from multiple sources, merge the
    source lists and take the *maximum* confidence score.

    Parameters
    ----------
    skills:
        List of ``(canonical_name, confidence, sources)`` tuples.

    Returns
    -------
    Deduplicated list sorted by confidence descending.
    """
    merged: Dict[str, Tuple[float, List[str]]] = {}
    for name, conf, sources in skills:
        key = name.lower()
        if key in merged:
            existing_conf, existing_sources = merged[key]
            merged[key] = (
                max(existing_conf, conf),
                sorted(set(existing_sources + sources)),
            )
        else:
            merged[key] = (conf, sorted(set(sources)))

    # Restore display-canonical casing by looking up original name.
    name_map: Dict[str, str] = {n.lower(): n for n, _, _ in skills}
    result = [(name_map[key], conf, srcs) for key, (conf, srcs) in merged.items()]
    return sorted(result, key=lambda x: x[1], reverse=True)

# Multi-Source Candidate Data Transformer

> **Eightfold Engineering Intern Assignment — Deepti Bhat**
> A deterministic, streaming, multi-source candidate profile unification engine.

---

## Overview

Eightfold ingests candidate data from everywhere — ATS systems, GitHub profiles, LinkedIn exports, and recruiter spreadsheets. Each source uses different field names, formats, and conventions. Downstream products need **one clean, canonical profile per candidate**: a fixed set of fields, normalized formats, de-duplicated across sources, and a full record of where every value came from and how confident the system is.

This project builds that transformer. It is a **6-stage pipeline** that turns messy, conflicting, multi-source inputs into a single trustworthy canonical record, with configurable output reshaping at runtime — **zero code changes needed**.

---

## Architecture

```
ATS JSON ─────┐
GitHub JSON ──┤──► [Stage 1: Ingest] ──► [Stage 2: Normalize] ──► [Stage 3: Identity Resolve]
LinkedIn JSON ┤         ↑                       ↑                           ↑
Recruiter CSV ┘   Adapters per           Pure functions,           Email-keyed registry,
                  source type.           (value,conf,method)       tolerates name variants.
                  Generator-based.       triples returned.

                                     [Stage 4: Merge Engine]
                                        Source Authority Matrix
                                        decides field winners.
                                        Lists: union + dedup.
                                        All provenance retained.
                                               ↓
                                     [Canonical Profile]
                                        Single source of truth.
                                               ↓
                          [Stage 5: Projection] ← runtime_config.json
                             Rename, reshape, normalize hints.
                                               ↓
                          [Stage 6: Validate & Emit]
                             on_missing: null | omit | error
                                               ↓
                                       JSON → stdout
```

See [`data/architecture.drawio`](data/architecture.drawio) for the full visual diagram (open with [draw.io](https://app.diagrams.net)).

---

## Supported Source Types

| Source Type     | Flag         | Format              | Notes                                   |
|-----------------|--------------|---------------------|-----------------------------------------|
| ATS JSON        | `ats`        | JSON array          | Structured rows; primary contact source |
| GitHub Profile  | `github`     | JSON object / array | Skills, bio, work history               |
| LinkedIn Export | `linkedin`   | JSON array          | Headline, education, compound location  |
| Recruiter CSV   | `recruiter_csv` | CSV with header  | Comma-delimited skills; BOM-safe        |

---

## Installation

```bash
# Clone / navigate to the project
cd Multi-Source-Candidate-Data-Transformer

# Install dependencies
pip install pydantic[email] pytest

# Verify
python -c "import pydantic; print(pydantic.__version__)"
```

---

## Running the Pipeline

### Basic run (ATS + GitHub)
```bash
python main.py \
    --source ats:data/ats_source.json \
    --source github:data/github_source.json \
    --config data/runtime_config.json \
    --pretty
```

### All four sources
```bash
python main.py \
    --source ats:data/ats_source.json \
    --source github:data/github_source.json \
    --source linkedin:data/linkedin_source.json \
    --source recruiter_csv:data/recruiter_source.csv \
    --config data/runtime_config.json \
    --pretty
```

### Custom config (field renaming + omit policy)
```bash
python main.py \
    --source ats:data/ats_source.json \
    --source github:data/github_source.json \
    --config data/custom_config.json \
    --pretty
```

### Debug logging
```bash
python main.py \
    --source ats:data/ats_source.json \
    --source github:data/github_source.json \
    --config data/runtime_config.json \
    --log-level DEBUG
```

### Save output to file
```bash
python main.py \
    --source ats:data/ats_source.json \
    --source github:data/github_source.json \
    --config data/runtime_config.json \
    --pretty > output.json
```

---

## Running Tests

```bash
# From the project root
pytest tests/ -v

# Coverage report
pytest tests/ -v --tb=short
```

---

## Output Schema (Default)

```json
{
  "full_name": "Krishna Bhat",
  "primary_email": "krishna@example.com",
  "phone": "+919876543210",
  "skills": ["Python", "Go", "SQL", "Docker", "Kubernetes"],
  "skills_with_metadata": [
    { "name": "Python", "confidence": 1.0, "sources": ["github"] }
  ],
  "provenance": [
    { "field": "full_name", "source": "ats", "method": "direct_map" },
    { "field": "skills",    "source": "github", "method": "taxonomy_normalised" }
  ]
}
```

---

## Runtime Config (Configurable Output)

The pipeline accepts a **runtime config** that reshapes output without any code changes:

```json
{
  "fields": [
    { "path": "full_name", "type": "string", "required": true },
    { "path": "primary_email", "from": "emails[0]", "type": "string", "required": true },
    { "path": "phone", "from": "phones[0]", "type": "string", "normalize": "E164" },
    { "path": "skills", "from": "skills[].name", "type": "string[]", "normalize": "canonical" }
  ],
  "include_confidence": true,
  "on_missing": "null"
}
```

| Config Key          | Options                     | Description                            |
|---------------------|-----------------------------|----------------------------------------|
| `path`              | any string                  | Output key name                        |
| `from`              | dot-path / bracket notation | Source path in canonical model         |
| `normalize`         | `"E164"`, `"canonical"`     | Re-apply normalizer at projection time |
| `required`          | `true` / `false`            | Triggers `on_missing` policy if absent |
| `include_confidence`| `true` / `false`            | Include skill confidence scores        |
| `on_missing`        | `"null"`, `"omit"`, `"error"` | Behavior on absent required fields   |

---

## Source Authority Matrix (SAM)

When two sources disagree on a field value, the SAM picks the winner deterministically:

| Field              | ATS  | GitHub | LinkedIn | Recruiter CSV |
|--------------------|------|--------|----------|---------------|
| `full_name`        | 0.90 | 0.60   | 0.85     | 0.70          |
| `emails`           | 1.00 | 1.00   | 1.00     | 0.90          |
| `phones`           | 0.90 | 0.10   | 0.70     | 0.85          |
| `skills`           | 0.40 | 0.90   | 0.85     | 0.60          |
| `headline`         | 0.30 | 0.80   | 0.95     | 0.40          |
| `education`        | 0.50 | 0.30   | 0.95     | 0.50          |
| `years_experience` | 0.70 | 0.30   | 0.60     | 0.90          |

> Higher score = source wins the field conflict. Ties go to insertion order.

---

## Edge Cases Handled

| Edge Case                         | Behavior                                            |
|-----------------------------------|-----------------------------------------------------|
| `"Call after 5 PM"` as phone      | Detected as prose → `None`, method `invalid_input`  |
| `"null"` string as name           | Null sentinel rejected → field set to `None`        |
| All-caps name (`"SARAH JONES"`)   | Title-cased → `"Sarah Jones"`, confidence 0.9       |
| Double `@@` in email              | Regex reject → `None`                               |
| `"no-reply@"` system email        | Rejected as non-personal → `None`                  |
| `"Present"` / `"ongoing"` date    | → `2026-06` with method `relative_sentinel`         |
| `"2019"` year-only date           | → `"2019-01"`, confidence 0.5, `year_only_approx`  |
| Missing email → two sources       | No identity match → two separate profiles           |
| Same email across 3 sources       | All three merged into one canonical profile         |
| Empty CSV row                     | Silently skipped (all-blank detection)              |
| Malformed JSON source file        | Logged and skipped; other sources unaffected        |
| Unknown source type (`ftp:…`)     | Warning logged; pipeline continues                  |
| `"Egypt"` as country              | → `"EG"` via extended 70-country lookup table      |
| `"San Francisco, CA, US"` location| City split from compound string; country normalized |

---

## Project Structure

```
.
├── main.py                  # CLI entry-point
├── models.py                # Pydantic schemas (CanonicalProfile, RuntimeConfig, SAM)
├── pipeline.py              # 6-stage pipeline + all adapters
├── normalizers.py           # Pure normalization functions
├── data/
│   ├── ats_source.json      # 15 ATS candidates (diverse + edge cases)
│   ├── github_source.json   # 8 GitHub profiles
│   ├── linkedin_source.json # 4 LinkedIn profiles
│   ├── recruiter_source.csv # 12 recruiter CSV rows
│   ├── runtime_config.json  # Default output config
│   ├── custom_config.json   # Alternative config (rename + omit policy)
│   └── architecture.drawio  # Visual pipeline diagram
└── tests/
    ├── test_normalizers.py  # 80+ normalizer unit tests
    └── test_pipeline.py     # Full integration tests
```

---

## Design Decisions & Engineering Highlights

### 1. Deterministic, Not ML
Every field winner is decided by a static SAM score table. Same inputs → same outputs, always. This makes the system auditable and debuggable — critical for hiring decisions.

### 2. Generator-Based Ingestion
All source adapters use Python generators (`yield`). No source file is held open during processing. Memory usage is O(candidates) not O(sources × candidates).

### 3. `(value, confidence, method)` Return Contract
Every normalizer returns a 3-tuple. The `method` field drives provenance records. This means any downstream system can reconstruct exactly how a value was derived.

### 4. Provenance on Every Field
The `provenance[]` array retains *all* lineage records across all pipeline stages. Nothing is silently overwritten. Auditors can trace any value back to its source and transformation method.

### 5. Clean Canonical ↔ Projection Separation
The `CanonicalProfile` is the internal representation. The `project()` function reshapes it per the runtime config. Adding a new output format requires only a new JSON config file, not a code change.

### 6. Defensive Normalization
`_safe_normalize()` wraps every normalizer call. If a normalizer raises an unexpected exception, the field becomes `(None, 0.0, "invalid_input")` and the rest of the record continues processing. Wrong-but-confident is worse than honestly empty.

---

## Exit Codes

| Code | Meaning                                        |
|------|------------------------------------------------|
| `0`  | Success — ≥0 candidates emitted               |
| `1`  | CLI argument error                             |
| `2`  | Missing required field (`on_missing="error"`)  |
| `3`  | Unexpected runtime error                       |

---

## Author

**Deepti Bhat** — deeptibt08@gmail.com  
Eightfold Engineering Intern Assignment, Jul–Dec 2026

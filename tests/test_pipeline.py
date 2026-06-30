"""
tests/test_pipeline.py
======================
Integration tests for the full pipeline — ingestion through projection.

Run with:  pytest tests/ -v
"""

import json
import tempfile
from pathlib import Path

import pytest

from models import RuntimeConfig, SourceAuthorityMatrix
from pipeline import (
    IdentityBroker,
    InvalidSourceFormatError,
    MissingRequiredFieldError,
    ingest_ats_source,
    ingest_github_source,
    ingest_linkedin_source,
    ingest_recruiter_csv_source,
    normalize_ats_record,
    normalize_github_record,
    normalize_recruiter_csv_record,
    merge_profiles,
    run_pipeline,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tmp_json(data, suffix=".json") -> Path:
    """Write data to a temp JSON file; return its Path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    )
    json.dump(data, f, ensure_ascii=False)
    f.close()
    return Path(f.name)


def _tmp_text(text: str, suffix=".csv") -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=suffix, delete=False, encoding="utf-8"
    )
    f.write(text)
    f.close()
    return Path(f.name)


def _default_config() -> RuntimeConfig:
    return RuntimeConfig.model_validate(
        {
            "fields": [
                {"path": "full_name", "type": "string", "required": True},
                {"path": "primary_email", "from": "emails[0]", "type": "string", "required": True},
                {"path": "phone", "from": "phones[0]", "type": "string", "normalize": "E164"},
                {"path": "skills", "from": "skills[].name", "type": "string[]"},
            ],
            "include_confidence": True,
            "on_missing": "null",
        }
    )


def _omit_config() -> RuntimeConfig:
    return RuntimeConfig.model_validate(
        {
            "fields": [
                {"path": "full_name", "type": "string", "required": True},
                {"path": "primary_email", "from": "emails[0]", "type": "string", "required": True},
            ],
            "include_confidence": False,
            "on_missing": "omit",
        }
    )


# ===========================================================================
# Stage 1 — Ingestion
# ===========================================================================


class TestIngestionATS:
    def test_valid_ats_array(self):
        data = [
            {"candidate_id": "a1", "name": "Alice", "email_address": "alice@test.com"}
        ]
        path = _tmp_json(data)
        records = list(ingest_ats_source(path))
        assert len(records) == 1
        assert records[0]["_source_label"] == "ats"

    def test_non_array_raises(self):
        path = _tmp_json({"not": "an array"})
        with pytest.raises(InvalidSourceFormatError):
            list(ingest_ats_source(path))

    def test_skips_non_dict_records(self):
        path = _tmp_json([{"name": "Alice"}, "bad_record", 42])
        records = list(ingest_ats_source(path))
        assert len(records) == 1  # Only the dict passes through

    def test_empty_array(self):
        path = _tmp_json([])
        records = list(ingest_ats_source(path))
        assert records == []

    def test_malformed_json(self):
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        f.write("{not valid json")
        f.close()
        with pytest.raises(InvalidSourceFormatError):
            list(ingest_ats_source(Path(f.name)))


class TestIngestionGitHub:
    def test_valid_single_object(self):
        data = {"login": "dev1", "name": "Dev One", "email": "dev@test.com"}
        path = _tmp_json(data)
        records = list(ingest_github_source(path))
        assert len(records) == 1
        assert records[0]["_source_label"] == "github"

    def test_valid_array(self):
        data = [
            {"login": "dev1", "email": "dev1@test.com"},
            {"login": "dev2", "email": "dev2@test.com"},
        ]
        path = _tmp_json(data)
        records = list(ingest_github_source(path))
        assert len(records) == 2

    def test_invalid_type_raises(self):
        path = _tmp_json("just a string")
        with pytest.raises(InvalidSourceFormatError):
            list(ingest_github_source(path))


class TestIngestionLinkedIn:
    def test_valid_linkedin_array(self):
        data = [{"profile_url": "https://linkedin.com/in/alice", "full_name": "Alice"}]
        path = _tmp_json(data)
        records = list(ingest_linkedin_source(path))
        assert len(records) == 1
        assert records[0]["_source_label"] == "linkedin"

    def test_non_array_raises(self):
        path = _tmp_json({"profile_url": "https://linkedin.com/in/alice"})
        with pytest.raises(InvalidSourceFormatError):
            list(ingest_linkedin_source(path))


class TestIngestionRecruiterCSV:
    CSV_CONTENT = (
        "candidate_id,name,email_address,phone_raw,organization,role_title,"
        "country_name,city_name,years_experience,skills_raw\n"
        "rec_001,Alice Smith,alice@test.com,+14155550101,Acme,Engineer,US,SF,5,python\n"
        "rec_002,Bob Jones,bob@test.com,,StartupB,Designer,CA,Toronto,3,figma\n"
        ",,,,,,,0,\n"  # blank row — should be skipped
    )

    def test_valid_csv_skips_blank_rows(self):
        path = _tmp_text(self.CSV_CONTENT)
        records = list(ingest_recruiter_csv_source(path))
        assert len(records) == 2
        assert all(r["_source_label"] == "recruiter_csv" for r in records)

    def test_csv_preserves_field_values(self):
        path = _tmp_text(self.CSV_CONTENT)
        records = list(ingest_recruiter_csv_source(path))
        assert records[0]["name"] == "Alice Smith"
        assert records[1]["email_address"] == "bob@test.com"


# ===========================================================================
# Stage 2 — Normalization
# ===========================================================================


class TestNormalizeATS:
    def test_happy_path(self):
        raw = {
            "_source_label": "ats",
            "candidate_id": "a1",
            "name": "Alice Smith",
            "email_address": "alice@test.com",
            "phone_raw": "+14155550101",
            "country_name": "United States",
            "city_name": "San Francisco",
            "organization": "Acme Corp",
            "role_title": "Engineer",
        }
        profile = normalize_ats_record(raw)
        assert profile.full_name == "Alice Smith"
        assert "alice@test.com" in profile.emails
        assert profile.phones == ["+14155550101"]
        assert profile.location.country == "US"
        assert profile.location.city == "San Francisco"
        assert len(profile.experience) == 1

    def test_missing_email_graceful(self):
        raw = {
            "_source_label": "ats",
            "candidate_id": "a2",
            "name": "Ghost",
            "email_address": "",
        }
        profile = normalize_ats_record(raw)
        assert profile.emails == []
        assert profile.full_name == "Ghost"

    def test_null_sentinel_name_rejected(self):
        raw = {
            "_source_label": "ats",
            "candidate_id": "a3",
            "name": "null",
            "email_address": "test@test.com",
        }
        profile = normalize_ats_record(raw)
        assert profile.full_name is None  # "null" string correctly rejected

    def test_malformed_phone_graceful(self):
        raw = {
            "_source_label": "ats",
            "candidate_id": "a4",
            "phone_raw": "123",  # too short
        }
        profile = normalize_ats_record(raw)
        assert profile.phones == []

    def test_all_caps_name_normalized(self):
        raw = {
            "_source_label": "ats",
            "candidate_id": "a5",
            "name": "SARAH JANE O'CONNOR",
        }
        profile = normalize_ats_record(raw)
        assert profile.full_name == "Sarah Jane O'Connor"


class TestNormalizeGitHub:
    def test_prose_phone_rejected(self):
        raw = {
            "_source_label": "github",
            "login": "dev1",
            "name": "Dev One",
            "email": "dev@test.com",
            "phone": "Call after 5 PM",
            "bio": "Software developer",
            "skills_extracted": ["python", "go"],
            "history": [],
        }
        profile = normalize_github_record(raw)
        assert profile.phones == []

    def test_skills_canonicalized(self):
        raw = {
            "_source_label": "github",
            "login": "dev2",
            "email": "dev2@test.com",
            "skills_extracted": ["golang", "k8s", "torch"],
            "history": [],
        }
        profile = normalize_github_record(raw)
        skill_names = {s.name for s in profile.skills}
        assert "Go" in skill_names
        assert "Kubernetes" in skill_names
        assert "PyTorch" in skill_names

    def test_relative_sentinel_date_flagged(self):
        raw = {
            "_source_label": "github",
            "login": "dev3",
            "history": [{"company": "Acme", "role": "Eng", "start": "2022-01", "end": "Present"}],
        }
        profile = normalize_github_record(raw)
        assert profile.experience[0].end == "2026-06"
        prov_methods = [p.method for p in profile.provenance]
        assert "relative_sentinel" in prov_methods

    def test_github_link_set(self):
        raw = {"_source_label": "github", "login": "testuser", "history": []}
        profile = normalize_github_record(raw)
        assert profile.links.github == "https://github.com/testuser"


class TestNormalizeRecruiterCSV:
    def test_happy_path(self):
        raw = {
            "_source_label": "recruiter_csv",
            "candidate_id": "rec_001",
            "name": "Alice Smith",
            "email_address": "alice@test.com",
            "phone_raw": "+14155550101",
            "organization": "Acme",
            "role_title": "Engineer",
            "country_name": "US",
            "city_name": "SF",
            "years_experience": "5",
            "skills_raw": "python,docker,kubernetes",
        }
        profile = normalize_recruiter_csv_record(raw)
        assert profile.full_name == "Alice Smith"
        assert profile.years_experience == 5.0
        skill_names = {s.name for s in profile.skills}
        assert "Docker" in skill_names
        assert "Kubernetes" in skill_names

    def test_empty_skills_raw(self):
        raw = {
            "_source_label": "recruiter_csv",
            "candidate_id": "rec_002",
            "skills_raw": "",
        }
        profile = normalize_recruiter_csv_record(raw)
        assert profile.skills == []


# ===========================================================================
# Stage 3 — Identity Resolution
# ===========================================================================


class TestIdentityBroker:
    def test_new_candidate_registered(self):
        broker = IdentityBroker()
        raw = {
            "_source_label": "ats",
            "candidate_id": "a1",
            "name": "Alice",
            "email_address": "alice@test.com",
        }
        profile = normalize_ats_record(raw)
        broker.register(profile)
        assert broker.resolve(profile) is not None

    def test_same_email_resolves_to_same_profile(self):
        broker = IdentityBroker()
        raw_ats = {
            "_source_label": "ats",
            "candidate_id": "a1",
            "name": "Alice Smith",
            "email_address": "alice@test.com",
        }
        raw_github = {
            "_source_label": "github",
            "login": "alice-dev",
            "name": "Alice S.",
            "email": "alice@test.com",
            "history": [],
        }
        from pipeline import normalize_github_record
        p1 = normalize_ats_record(raw_ats)
        p2 = normalize_github_record(raw_github)
        broker.register(p1)
        existing = broker.resolve(p2)
        assert existing is not None
        assert existing.candidate_id == p1.candidate_id

    def test_no_email_no_match(self):
        broker = IdentityBroker()
        raw = {
            "_source_label": "ats",
            "candidate_id": "a1",
            "email_address": "",
        }
        p1 = normalize_ats_record(raw)
        p2 = normalize_ats_record(raw)
        broker.register(p1)
        # p2 has no emails, so it won't resolve
        assert broker.resolve(p2) is None

    def test_case_insensitive_email_match(self):
        broker = IdentityBroker()
        raw1 = {
            "_source_label": "ats",
            "candidate_id": "a1",
            "name": "Bob",
            "email_address": "Bob@Test.COM",
        }
        raw2 = {
            "_source_label": "github",
            "login": "bob-dev",
            "email": "bob@test.com",
            "history": [],
        }
        from pipeline import normalize_github_record
        p1 = normalize_ats_record(raw1)
        p2 = normalize_github_record(raw2)
        broker.register(p1)
        assert broker.resolve(p2) is not None


# ===========================================================================
# Stage 4 — Merge Engine
# ===========================================================================


class TestMergeProfiles:
    def _make_ats(self) -> object:
        raw = {
            "_source_label": "ats",
            "candidate_id": "a1",
            "name": "Alice Smith",
            "email_address": "alice@test.com",
            "phone_raw": "+14155550101",
            "organization": "Acme",
            "role_title": "Engineer",
        }
        return normalize_ats_record(raw)

    def _make_github(self) -> object:
        raw = {
            "_source_label": "github",
            "login": "alice-dev",
            "email": "alice@test.com",
            "bio": "Open source enthusiast",
            "skills_extracted": ["python", "docker"],
            "history": [{"company": "Acme", "role": "Software Engineer", "start": "2022-01", "end": "Present"}],
        }
        return normalize_github_record(raw)

    def test_merge_fills_headline(self):
        sam = SourceAuthorityMatrix()
        base = self._make_ats()
        incoming = self._make_github()
        merged = merge_profiles(base, incoming, sam)
        assert merged.headline == "Open source enthusiast"

    def test_merge_skills_union(self):
        sam = SourceAuthorityMatrix()
        base = self._make_ats()
        incoming = self._make_github()
        merged = merge_profiles(base, incoming, sam)
        skill_names = {s.name for s in merged.skills}
        assert "Python" in skill_names
        assert "Docker" in skill_names

    def test_merge_email_union(self):
        sam = SourceAuthorityMatrix()
        base = self._make_ats()
        # Give github a second email
        raw = {
            "_source_label": "github",
            "login": "alice-dev",
            "email": "alice-work@company.com",
            "history": [],
        }
        incoming = normalize_github_record(raw)
        incoming.emails.append("alice@test.com")
        merged = merge_profiles(base, incoming, sam)
        assert "alice@test.com" in merged.emails

    def test_provenance_accumulated(self):
        sam = SourceAuthorityMatrix()
        base = self._make_ats()
        incoming = self._make_github()
        merged = merge_profiles(base, incoming, sam)
        sources = {p.source for p in merged.provenance}
        assert "ats" in sources
        assert "github" in sources


# ===========================================================================
# Full Pipeline Integration
# ===========================================================================


class TestRunPipeline:
    def test_single_ats_single_github_merged(self):
        ats_data = [
            {
                "candidate_id": "ats_1",
                "name": "Alice Smith",
                "email_address": "alice@test.com",
                "phone_raw": "+14155550101",
                "organization": "Acme",
                "role_title": "Engineer",
                "country_name": "US",
                "city_name": "SF",
            }
        ]
        github_data = {
            "login": "alice-dev",
            "name": "Alice S.",
            "email": "alice@test.com",
            "bio": "Loves data pipelines",
            "skills_extracted": ["python", "docker"],
            "history": [],
        }
        ats_path = _tmp_json(ats_data)
        gh_path = _tmp_json(github_data)
        config = _default_config()
        results = list(run_pipeline([("ats", ats_path), ("github", gh_path)], config))
        assert len(results) == 1
        r = results[0]
        assert r["full_name"] == "Alice Smith"
        assert r["primary_email"] == "alice@test.com"

    def test_missing_required_field_null_policy(self):
        ats_data = [{"candidate_id": "ghost", "email_address": ""}]
        path = _tmp_json(ats_data)
        config = _default_config()
        results = list(run_pipeline([("ats", path)], config))
        assert len(results) == 1
        # full_name is None (null policy), primary_email is None (empty)
        assert results[0]["full_name"] is None

    def test_missing_required_field_error_policy(self):
        ats_data = [{"candidate_id": "ghost", "name": "", "email_address": ""}]
        path = _tmp_json(ats_data)
        config = RuntimeConfig.model_validate(
            {
                "fields": [
                    {"path": "full_name", "type": "string", "required": True},
                    {"path": "primary_email", "from": "emails[0]", "type": "string", "required": True},
                ],
                "on_missing": "error",
            }
        )
        with pytest.raises(MissingRequiredFieldError):
            list(run_pipeline([("ats", path)], config))

    def test_duplicate_emails_merged_into_one_candidate(self):
        ats_data = [
            {
                "candidate_id": "a1",
                "name": "Bob",
                "email_address": "bob@test.com",
                "phone_raw": "+14155550199",
            }
        ]
        github_data = {
            "login": "bob-dev",
            "name": "Bob Dev",
            "email": "bob@test.com",
            "skills_extracted": ["python"],
            "history": [],
        }
        ats_path = _tmp_json(ats_data)
        gh_path = _tmp_json(github_data)
        config = _default_config()
        results = list(run_pipeline([("ats", ats_path), ("github", gh_path)], config))
        # Same email → only 1 canonical profile
        assert len(results) == 1

    def test_omit_policy_drops_missing_keys(self):
        ats_data = [{"candidate_id": "a1", "email_address": "alice@test.com"}]
        path = _tmp_json(ats_data)
        config = _omit_config()
        results = list(run_pipeline([("ats", path)], config))
        assert len(results) == 1
        # full_name is missing and required=True with omit policy → key is absent
        assert "full_name" not in results[0]

    def test_recruiter_csv_pipeline(self):
        csv_content = (
            "candidate_id,name,email_address,phone_raw,organization,role_title,"
            "country_name,city_name,years_experience,skills_raw\n"
            "rec_001,Alice Smith,alice@test.com,+14155550101,Acme,Engineer,US,SF,5,python\n"
        )
        path = _tmp_text(csv_content)
        config = _default_config()
        results = list(run_pipeline([("recruiter_csv", path)], config))
        assert len(results) == 1
        assert results[0]["full_name"] == "Alice Smith"

    def test_garbage_source_skipped_gracefully(self):
        """A completely malformed source file should not crash the pipeline."""
        good_ats = [
            {"candidate_id": "a1", "name": "Alice", "email_address": "alice@test.com"}
        ]
        ats_path = _tmp_json(good_ats)
        # Create a broken JSON file
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        )
        f.write("{broken json")
        f.close()
        config = _default_config()
        # Even with the broken file in the mix, we should still get alice's profile
        results = list(
            run_pipeline(
                [("ats", ats_path), ("github", Path(f.name))], config
            )
        )
        assert len(results) >= 1

    def test_unknown_source_type_skipped(self):
        """Unregistered source types should be skipped without crashing."""
        ats_data = [{"candidate_id": "a1", "name": "Alice", "email_address": "alice@test.com"}]
        path = _tmp_json(ats_data)
        config = _default_config()
        # "ftp" is not a registered adapter — should be skipped gracefully
        results = list(run_pipeline([("ats", path), ("ftp", path)], config))
        assert len(results) >= 1

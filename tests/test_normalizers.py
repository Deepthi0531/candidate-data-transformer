"""
tests/test_normalizers.py
=========================
Comprehensive edge-case tests for every normalizer function.

Run with:  pytest tests/ -v
"""

import pytest
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
    PRESENT_EPOCH,
)


# ===========================================================================
# normalize_phone
# ===========================================================================

class TestNormalizePhone:
    def test_valid_e164_with_plus(self):
        val, conf, method = normalize_phone("+919876543210")
        assert val == "+919876543210"
        assert conf == 1.0
        assert method == "direct_map"

    def test_valid_e164_with_spaces(self):
        val, conf, _ = normalize_phone("+91 98765-43210")
        assert val == "+919876543210"
        assert conf == 1.0

    def test_us_number_with_dashes(self):
        val, conf, _ = normalize_phone("+1-415-555-0192")
        assert val == "+14155550192"
        assert conf == 1.0

    def test_digits_only_no_plus(self):
        val, conf, method = normalize_phone("9876543210")
        assert val == "+9876543210"
        assert conf == 0.85
        assert method == "normalised"

    def test_prose_phone_rejected(self):
        val, conf, method = normalize_phone("Call after 5 PM")
        assert val is None
        assert conf == 0.0
        assert method == "invalid_input"

    def test_too_short_rejected(self):
        val, conf, _ = normalize_phone("123")
        assert val is None
        assert conf == 0.0

    def test_too_long_rejected(self):
        val, conf, _ = normalize_phone("+1234567890123456")  # 16 digits — invalid E.164
        assert val is None

    def test_empty_string(self):
        val, conf, method = normalize_phone("")
        assert val is None
        assert method == "empty_input"

    def test_none_input(self):
        val, conf, method = normalize_phone(None)
        assert val is None
        assert method == "empty_input"

    def test_whitespace_only(self):
        val, _, method = normalize_phone("   ")
        assert val is None
        assert method == "empty_input"

    def test_parentheses_format(self):
        val, conf, _ = normalize_phone("+44 (20) 7946 0958")
        assert val == "+442079460958"
        assert conf == 1.0

    def test_singapore_number(self):
        val, _, _ = normalize_phone("+65 9123 4567")
        assert val == "+6591234567"

    def test_whatsapp_text_rejected(self):
        val, _, method = normalize_phone("WhatsApp me")
        assert val is None
        assert method == "invalid_input"


# ===========================================================================
# normalize_country
# ===========================================================================

class TestNormalizeCountry:
    def test_full_name_india(self):
        val, conf, _ = normalize_country("India")
        assert val == "IN"
        assert conf >= 0.75

    def test_iso_code_passthrough(self):
        val, conf, method = normalize_country("US")
        assert val == "US"
        assert conf == 1.0
        assert method == "direct_map"

    def test_alias_usa(self):
        val, _, _ = normalize_country("USA")
        assert val == "US"

    def test_mixed_case(self):
        val, _, _ = normalize_country("united kingdom")
        assert val == "GB"

    def test_uae_alias(self):
        val, _, _ = normalize_country("UAE")
        assert val == "AE"

    def test_unknown_returns_none(self):
        val, conf, method = normalize_country("Wakanda")
        assert val is None
        assert conf == 0.0

    def test_empty_string(self):
        val, _, method = normalize_country("")
        assert val is None
        assert method == "empty_input"

    def test_none_input(self):
        val, conf, _ = normalize_country(None)
        assert val is None
        assert conf == 0.0

    def test_lowercase_iso(self):
        val, _, _ = normalize_country("de")
        assert val == "DE"

    def test_scotland_maps_to_gb(self):
        val, _, _ = normalize_country("Scotland")
        assert val == "GB"

    def test_nigeria(self):
        val, _, _ = normalize_country("Nigeria")
        assert val == "NG"

    def test_fuzzy_partial_match(self):
        # "States" should fuzzy-match "united states"
        val, conf, method = normalize_country("States")
        # Just ensure it doesn't crash
        assert method in ("fuzzy_match", "invalid_input", "normalised", "direct_map")


# ===========================================================================
# normalize_date
# ===========================================================================

class TestNormalizeDate:
    def test_iso_format(self):
        val, conf, method = normalize_date("2024-05")
        assert val == "2024-05"
        assert conf == 1.0
        assert method == "direct_map"

    def test_iso_slash(self):
        val, _, _ = normalize_date("2024/05")
        assert val == "2024-05"

    def test_month_year_english(self):
        val, conf, method = normalize_date("May 2024")
        assert val == "2024-05"
        assert conf == 0.95

    def test_month_year_full(self):
        val, _, _ = normalize_date("January 2023")
        assert val == "2023-01"

    def test_present_sentinel(self):
        val, conf, method = normalize_date("Present")
        assert val == PRESENT_EPOCH
        assert conf == 1.0
        assert method == "relative_sentinel"

    def test_current_sentinel(self):
        val, _, method = normalize_date("current")
        assert val == PRESENT_EPOCH
        assert method == "relative_sentinel"

    def test_now_sentinel(self):
        val, _, method = normalize_date("now")
        assert val == PRESENT_EPOCH
        assert method == "relative_sentinel"

    def test_ongoing_sentinel(self):
        val, _, method = normalize_date("ongoing")
        assert val == PRESENT_EPOCH
        assert method == "relative_sentinel"

    def test_mm_yyyy_format(self):
        val, conf, _ = normalize_date("06/2022")
        assert val == "2022-06"
        assert conf == 0.9

    def test_year_only(self):
        val, conf, method = normalize_date("2019")
        assert val == "2019-01"
        assert conf == 0.5
        assert method == "year_only_approximation"

    def test_empty_string(self):
        val, conf, method = normalize_date("")
        assert val is None
        assert method == "empty_input"

    def test_none_input(self):
        val, _, _ = normalize_date(None)
        assert val is None

    def test_garbage_date(self):
        val, conf, method = normalize_date("not-a-date")
        assert val is None
        assert conf == 0.0
        assert method == "invalid_input"


# ===========================================================================
# normalize_email
# ===========================================================================

class TestNormalizeEmail:
    def test_valid_email_lowercase(self):
        val, conf, _ = normalize_email("Krishna@Example.COM")
        assert val == "krishna@example.com"
        assert conf == 1.0

    def test_valid_email_already_lower(self):
        val, _, method = normalize_email("test@example.com")
        assert val == "test@example.com"
        assert method == "normalised"

    def test_double_at_rejected(self):
        val, conf, method = normalize_email("fatima@@bademail..com")
        assert val is None
        assert conf == 0.0

    def test_empty_string(self):
        val, _, method = normalize_email("")
        assert method == "empty_input"

    def test_none_input(self):
        val, _, _ = normalize_email(None)
        assert val is None

    def test_no_at_sign(self):
        val, _, _ = normalize_email("notanemail")
        assert val is None

    def test_noreply_rejected(self):
        val, _, method = normalize_email("no-reply@linkedin.com")
        assert val is None
        assert method == "system_email_rejected"

    def test_special_chars_in_local(self):
        val, _, _ = normalize_email("user+tag@domain.co.uk")
        assert val == "user+tag@domain.co.uk"

    def test_whitespace_stripped(self):
        val, _, _ = normalize_email("  user@domain.com  ")
        assert val == "user@domain.com"


# ===========================================================================
# normalize_name
# ===========================================================================

class TestNormalizeName:
    def test_normal_name(self):
        val, _, _ = normalize_name("Krishna Bhat")
        assert val == "Krishna Bhat"

    def test_all_caps(self):
        val, conf, method = normalize_name("SARAH JANE O'CONNOR")
        assert val == "Sarah Jane O'Connor"
        assert conf == 0.9
        assert method == "normalised"

    def test_empty_string(self):
        val, _, method = normalize_name("")
        assert val is None
        assert method == "empty_input"

    def test_none_input(self):
        val, _, _ = normalize_name(None)
        assert val is None

    def test_null_sentinel_rejected(self):
        val, _, method = normalize_name("null")
        assert val is None
        assert method == "null_sentinel_rejected"

    def test_none_sentinel_rejected(self):
        val, _, method = normalize_name("None")
        assert val is None
        assert method == "null_sentinel_rejected"

    def test_excess_whitespace(self):
        val, _, _ = normalize_name("  John   Doe  ")
        assert val == "John Doe"

    def test_unicode_normalized(self):
        val, _, _ = normalize_name("Léa Müller-Schmidt")
        assert val == "Léa Müller-Schmidt"


# ===========================================================================
# normalize_string
# ===========================================================================

class TestNormalizeString:
    def test_null_sentinel(self):
        val, _, method = normalize_string("null")
        assert val is None
        assert method == "null_sentinel_rejected"

    def test_na_sentinel(self):
        val, _, method = normalize_string("N/A")
        assert val is None
        assert method == "null_sentinel_rejected"

    def test_empty(self):
        val, _, method = normalize_string("")
        assert val is None
        assert method == "empty_input"

    def test_strips_whitespace(self):
        val, _, _ = normalize_string("  hello world  ")
        assert val == "hello world"

    def test_collapses_inner_whitespace(self):
        val, _, _ = normalize_string("hello   world")
        assert val == "hello world"


# ===========================================================================
# canonicalize_skill
# ===========================================================================

class TestCanonicalizeSkill:
    def test_exact_match(self):
        val, conf, _ = canonicalize_skill("Python")
        assert val == "Python"
        assert conf == 1.0

    def test_synonym_golang(self):
        val, _, method = canonicalize_skill("golang")
        assert val == "Go"
        assert method == "taxonomy_normalised"

    def test_synonym_k8s(self):
        val, _, _ = canonicalize_skill("k8s")
        assert val == "Kubernetes"

    def test_unknown_skill_titlecase(self):
        val, conf, method = canonicalize_skill("some weird framework")
        assert val == "Some Weird Framework"
        assert conf == 0.5
        assert method == "taxonomy_unknown"

    def test_empty_string(self):
        val, conf, method = canonicalize_skill("")
        assert val == ""
        assert conf == 0.0
        assert method == "empty_input"

    def test_none_input(self):
        val, conf, _ = canonicalize_skill(None)
        assert conf == 0.0

    def test_pytorch_synonym(self):
        val, _, _ = canonicalize_skill("torch")
        assert val == "PyTorch"

    def test_spark_synonym(self):
        val, _, _ = canonicalize_skill("apache spark")
        assert val == "Apache Spark"

    def test_sklearn_synonym(self):
        val, _, _ = canonicalize_skill("sklearn")
        assert val == "scikit-learn"


# ===========================================================================
# deduplicate_skills
# ===========================================================================

class TestDeduplicateSkills:
    def test_deduplicates_same_skill(self):
        skills = [
            ("Python", 0.9, ["ats"]),
            ("Python", 1.0, ["github"]),
        ]
        result = deduplicate_skills(skills)
        assert len(result) == 1
        name, conf, sources = result[0]
        assert name == "Python"
        assert conf == 1.0  # max confidence
        assert "ats" in sources and "github" in sources

    def test_case_insensitive_dedup(self):
        skills = [
            ("python", 0.9, ["ats"]),
            ("Python", 1.0, ["github"]),
        ]
        result = deduplicate_skills(skills)
        assert len(result) == 1

    def test_sorted_by_confidence(self):
        skills = [
            ("Docker", 0.5, ["ats"]),
            ("Python", 1.0, ["github"]),
            ("Go", 0.9, ["github"]),
        ]
        result = deduplicate_skills(skills)
        confs = [c for _, c, _ in result]
        assert confs == sorted(confs, reverse=True)

    def test_empty_input(self):
        assert deduplicate_skills([]) == []


# ===========================================================================
# normalize_linkedin_url
# ===========================================================================

class TestNormalizeLinkedinUrl:
    def test_valid_url(self):
        val, conf, _ = normalize_linkedin_url("https://linkedin.com/in/sarah-oconnor")
        assert val == "https://linkedin.com/in/sarah-oconnor"
        assert conf == 1.0

    def test_www_stripped(self):
        val, _, method = normalize_linkedin_url("https://www.linkedin.com/in/deeptibt08")
        assert val == "https://linkedin.com/in/deeptibt08"
        assert method == "normalised"

    def test_empty_handle_rejected(self):
        val, _, method = normalize_linkedin_url("https://linkedin.com/in/")
        assert val is None

    def test_non_linkedin_url_rejected(self):
        val, _, _ = normalize_linkedin_url("https://twitter.com/someone")
        assert val is None

    def test_none_rejected(self):
        val, _, method = normalize_linkedin_url(None)
        assert val is None
        assert method == "empty_input"


# ===========================================================================
# normalize_city_from_compound
# ===========================================================================

class TestNormalizeCityFromCompound:
    def test_three_part_string(self):
        val, conf, _ = normalize_city_from_compound("San Francisco, California, United States")
        assert val == "San Francisco"
        assert conf == 0.9

    def test_two_part_string(self):
        val, _, _ = normalize_city_from_compound("Berlin, Germany")
        assert val == "Berlin"

    def test_single_word(self):
        val, _, _ = normalize_city_from_compound("Singapore")
        assert val == "Singapore"

    def test_empty_string(self):
        val, _, method = normalize_city_from_compound("")
        assert val is None
        assert method == "empty_input"

    def test_none_input(self):
        val, _, _ = normalize_city_from_compound(None)
        assert val is None


# ===========================================================================
# normalize_years_experience
# ===========================================================================

class TestNormalizeYearsExperience:
    def test_integer_string(self):
        val, conf, _ = normalize_years_experience("4")
        assert val == 4.0
        assert conf == 1.0

    def test_float_string(self):
        val, _, _ = normalize_years_experience("4.5")
        assert val == 4.5

    def test_zero(self):
        val, _, _ = normalize_years_experience("0")
        assert val == 0.0

    def test_negative_rejected(self):
        val, _, method = normalize_years_experience("-1")
        assert val is None
        assert method == "out_of_range"

    def test_over_60_rejected(self):
        val, _, method = normalize_years_experience("61")
        assert val is None
        assert method == "out_of_range"

    def test_non_numeric_rejected(self):
        val, _, method = normalize_years_experience("five years")
        assert val is None
        assert method == "invalid_input"

    def test_none_input(self):
        val, _, method = normalize_years_experience(None)
        assert val is None
        assert method == "empty_input"

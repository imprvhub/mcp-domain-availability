"""Offline tests: no network, no registry calls."""

import unittest

from mcp_domain_availability.main import (
    ALL_TLDS,
    POPULAR_TLDS,
    TLD_CATEGORIES,
    clean_domain_name,
    extract_domain_parts,
    get_min_length_for_tld,
    is_valid_domain_name,
    _summarize,
)


class TestDomainParsing(unittest.TestCase):
    def test_clean_strips_scheme_path_and_trailing_dot(self):
        self.assertEqual(clean_domain_name("https://Example.COM/path?q=1"), "example.com")
        self.assertEqual(clean_domain_name("  http://example.com  "), "example.com")
        self.assertEqual(clean_domain_name("example.com."), "example.com")

    def test_extract_splits_on_the_last_dot(self):
        self.assertEqual(extract_domain_parts("example.com"), ("example", "com"))
        self.assertEqual(extract_domain_parts("sub.example.com"), ("sub.example", "com"))
        self.assertEqual(extract_domain_parts("example"), ("example", ""))

    def test_the_legacy_domain_flag_is_still_tolerated(self):
        # The tool used to require "mysite.com --domain"; old prompts should keep working.
        self.assertEqual(extract_domain_parts("mysite.com --domain"), ("mysite", "com"))
        self.assertEqual(extract_domain_parts("mysite --domain"), ("mysite", ""))


class TestValidation(unittest.TestCase):
    def test_accepts_ordinary_names(self):
        self.assertTrue(is_valid_domain_name("example", "com"))
        self.assertTrue(is_valid_domain_name("my-site", "dev"))

    def test_rejects_malformed_labels(self):
        for base in ("-lead", "trail-", "do--uble", "has space", "under_score", "a" * 64, ""):
            self.assertFalse(is_valid_domain_name(base, "com"), base)

    def test_rejects_a_tld_that_is_not_a_tld(self):
        # The TLD used to go unvalidated and was passed straight to the network layer.
        for tld in ("$(whoami)", "com;ls", "1234", "-com", "", "a"):
            self.assertFalse(is_valid_domain_name("example", tld), tld)

    def test_minimum_length_is_per_tld(self):
        self.assertEqual(get_min_length_for_tld("com"), 2)
        self.assertEqual(get_min_length_for_tld("shop"), 3)
        self.assertTrue(is_valid_domain_name("ab", "com"))
        self.assertFalse(is_valid_domain_name("ab", "shop"))


class TestTldTables(unittest.TestCase):
    def test_tld_order_is_deterministic(self):
        # list(set(...)) used to reshuffle the TLD list on every start.
        self.assertEqual(ALL_TLDS, list(dict.fromkeys(ALL_TLDS)))
        self.assertEqual(ALL_TLDS[: len(POPULAR_TLDS)], POPULAR_TLDS)

    def test_categories_cover_the_documented_sets(self):
        self.assertEqual(set(TLD_CATEGORIES), {"popular", "country", "new", "all"})
        for tlds in TLD_CATEGORIES.values():
            self.assertEqual(len(tlds), len(set(tlds)))


class TestSummary(unittest.TestCase):
    def test_undetermined_is_not_counted_as_available(self):
        results = [
            {"domain": "a.com", "status": "available"},
            {"domain": "b.com", "status": "taken"},
            {"domain": "c.io", "status": "undetermined"},
        ]
        summary = _summarize(results)
        self.assertEqual(summary["check_summary"]["total_available"], 1)
        self.assertEqual(summary["check_summary"]["total_unavailable"], 1)
        self.assertEqual(summary["check_summary"]["total_undetermined"], 1)
        self.assertEqual([r["domain"] for r in summary["undetermined_domains"]], ["c.io"])
        self.assertEqual(summary["total_checked"], 3)

    def test_results_are_sorted_by_domain(self):
        results = [
            {"domain": "z.com", "status": "available"},
            {"domain": "a.com", "status": "available"},
        ]
        self.assertEqual(
            [r["domain"] for r in _summarize(results)["available_domains"]], ["a.com", "z.com"]
        )


if __name__ == "__main__":
    unittest.main()

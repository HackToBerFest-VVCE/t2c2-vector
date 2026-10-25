"""Unit tests for source evidence matching and the analysis endpoint."""

import unittest
from unittest.mock import patch

import app as app_module


def make_source_file(path, content):
    """Build the source-file shape consumed by find_matching_evidence."""
    return {"path": path, "content": content}


def make_retrieval(files):
    """Build the retrieval response consumed by the /api/analyze route."""
    return {
        "repository": "example/project",
        "default_branch": "main",
        "files": [
            {**source_file, "size": len(source_file["content"].encode("utf-8"))}
            for source_file in files
        ],
    }


class FindMatchingEvidenceTests(unittest.TestCase):
    def test_description_with_no_useful_keywords_is_too_broad(self):
        # Generic bug-report terms are removed, so no useful keywords remain.
        evidence, match_count, strongest_match = app_module.find_matching_evidence(
            [], "bug error problem"
        )

        self.assertEqual(match_count, 0)
        self.assertIsNone(strongest_match)
        self.assertIn("too broad", evidence[0])

    def test_description_with_one_useful_keyword_is_insufficient(self):
        # A single specific term is not enough for the source-line search.
        evidence, match_count, strongest_match = app_module.find_matching_evidence(
            [], "payment bug"
        )

        self.assertEqual(match_count, 0)
        self.assertIsNone(strongest_match)
        self.assertIn("at least two specific terms", evidence[0])

    def test_generic_terms_are_not_used_as_evidence_keywords(self):
        # Common prose terms must not produce false evidence by themselves.
        files = [
            make_source_file(
                "src/errors.py",
                "the limit error belongs to application request handling\n",
            )
        ]

        evidence, match_count, strongest_match = app_module.find_matching_evidence(
            files, "the limit error in application request handling"
        )

        self.assertEqual(match_count, 0)
        self.assertIsNone(strongest_match)
        self.assertIn("too broad", evidence[0])

    def test_non_diagnostic_during_does_not_create_evidence(self):
        files = [
            make_source_file(
                "src/flask/wrappers.py",
                "request processing continues during normal operation\n",
            )
        ]

        evidence, match_count, strongest_match = app_module.find_matching_evidence(
            files,
            "The quantum pineapple scheduler fails during moonlight synchronization.",
        )

        self.assertEqual(match_count, 0)
        self.assertIsNone(strongest_match)
        self.assertIn("No useful evidence found", evidence[0])

    def test_matching_one_specific_keyword_is_not_strong_enough(self):
        files = [
            make_source_file("src/scheduler.py", "scheduler runs during startup\n")
        ]

        evidence, match_count, strongest_match = app_module.find_matching_evidence(
            files, "quantum pineapple scheduler fails during moonlight synchronization"
        )

        self.assertEqual(match_count, 0)
        self.assertIsNone(strongest_match)
        self.assertIn("fewer than two specific", evidence[0])

    def test_returns_no_match_when_keywords_are_absent_from_source(self):
        # Two useful terms pass the broad-description check but match no source.
        files = [make_source_file("src/cart.py", "def calculate_total():\n    return 0\n")]

        evidence, match_count, strongest_match = app_module.find_matching_evidence(
            files, "payment timeout"
        )

        self.assertEqual(match_count, 0)
        self.assertIsNone(strongest_match)
        self.assertIn("No useful evidence found", evidence[0])

    def test_line_matching_multiple_keywords_is_ranked_first(self):
        # A line with two matching terms ranks above a line with only one.
        files = [
            make_source_file(
                "src/payment.py",
                "payment handling starts here\npayment timeout is retried\n",
            )
        ]

        evidence, match_count, strongest_match = app_module.find_matching_evidence(
            files, "payment timeout"
        )

        self.assertEqual(match_count, 2)
        self.assertEqual(strongest_match["line"], 2)
        self.assertEqual(strongest_match["keywords"], ["payment", "timeout"])
        self.assertIn("src/payment.py:2:", evidence[0])

    def test_specific_http_terms_and_request_body_phrase_rank_first(self):
        # Specific identifiers, a numeric HTTP status, and the request-body
        # phrase outrank a generic phrase-only line.
        files = [
            make_source_file(
                "src/request_limits.py",
                "the request body error limit was exceeded\n"
                "if request_body > MAX_CONTENT_LENGTH: raise RequestEntityTooLarge(413)\n",
            )
        ]

        evidence, match_count, strongest_match = app_module.find_matching_evidence(
            files,
            "the HTTP 413 error from MAX_CONTENT_LENGTH when the request body "
            "raises RequestEntityTooLarge",
        )

        self.assertEqual(match_count, 2)
        self.assertEqual(strongest_match["line"], 2)
        self.assertEqual(
            strongest_match["keywords"],
            ["413", "max_content_length", "request body", "requestentitytoolarge"],
        )
        self.assertIn("src/request_limits.py:2:", evidence[0])

    def test_application_evidence_ranks_before_test_evidence(self):
        # Non-test application files rank ahead even when a test line matches
        # more keywords than the available application line.
        files = [
            make_source_file(
                "tests/test_payment.py",
                "payment timeout crash appears in this test\n",
            ),
            make_source_file("src/payment.py", "payment processing starts here\n"),
        ]

        _evidence, _match_count, strongest_match = app_module.find_matching_evidence(
            files, "payment timeout crash"
        )

        self.assertEqual(strongest_match["path"], "src/payment.py")

    def test_evidence_has_file_line_and_nearby_context(self):
        # The strongest-match result identifies the source location and the
        # evidence list includes the surrounding source lines.
        files = [
            make_source_file(
                "src/payment.py",
                "line before\ninitialize payment\npayment timeout occurs\n"
                "retry payment\nline after\n",
            )
        ]

        evidence, _match_count, strongest_match = app_module.find_matching_evidence(
            files, "payment timeout"
        )

        self.assertEqual(strongest_match["path"], "src/payment.py")
        self.assertEqual(strongest_match["line"], 3)
        self.assertEqual(strongest_match["context_start"], 1)
        self.assertEqual(strongest_match["context_end"], 5)
        self.assertTrue(any("src/payment.py:3:" in item for item in evidence))
        self.assertTrue(any("[context] retry payment" in item for item in evidence))


class AnalyzeEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()

    def analyze(self, bug_description, files):
        # The route's required JSON field names are repository_url and
        # bug_description; patch retrieval so no GitHub request can be made.
        with patch(
            "app.get_repository_sources",
            return_value=make_retrieval(files),
        ) as mocked_retrieval:
            response = self.client.post(
                "/api/analyze",
                json={
                    "repository_url": "https://github.com/example/project",
                    "bug_description": bug_description,
                },
            )

        mocked_retrieval.assert_called_once_with(
            "https://github.com/example/project", bug_description
        )
        return response

    def assert_analysis_schema(self, result):
        # These are the response fields returned by analyze().
        self.assertEqual(
            set(result),
            {
                "mode",
                "message",
                "summary",
                "root_cause",
                "evidence",
                "suggested_fix",
                "tests",
            },
        )

    def test_broad_description_returns_broad_search_message(self):
        # Broad input gets a request for more specific details, not a diagnosis.
        response = self.analyze("bug error problem", [])

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertIn("too broad", result["message"])
        self.assertIn("more specific bug details", result["summary"])
        self.assertIn("Insufficient evidence", result["root_cause"])

    def test_generic_descriptions_do_not_return_source_line_matches(self):
        files = [
            make_source_file(
                "src/status.py",
                "application is not working; something is broken; it doesn't work\n",
            )
        ]

        for description in (
            "The application is not working.",
            "Something is broken.",
            "It doesn't work.",
        ):
            with self.subTest(description=description):
                response = self.analyze(description, files)

                self.assertEqual(response.status_code, 200)
                result = response.get_json()
                self.assert_analysis_schema(result)
                self.assertIn("too broad", result["message"])
                self.assertIn("more specific bug details", result["summary"])
                self.assertIn("Insufficient evidence", result["root_cause"])
                self.assertFalse(
                    any("src/status.py:1:" in item for item in result["evidence"])
                )
                self.assertFalse(
                    any("[keyword(s):" in item for item in result["evidence"])
                )

    def test_concrete_technical_details_are_not_classified_as_broad(self):
        for description in (
            "GET /api/users returns HTTP 500",
            "ValueError: invalid literal for integer conversion",
            "parse_user_id raises ValueError",
            "Login redirects to the homepage instead of the dashboard",
        ):
            with self.subTest(description=description):
                response = self.analyze(description, [])

                self.assertEqual(response.status_code, 200)
                result = response.get_json()
                self.assert_analysis_schema(result)
                self.assertNotIn("too broad", result["message"])
                self.assertIn("found 0 matching source line(s)", result["summary"])

    def test_no_matches_returns_insufficient_evidence(self):
        # Valid specific terms with unrelated retrieved code produce no match.
        files = [make_source_file("src/cart.py", "def calculate_total():\n    return 0\n")]

        response = self.analyze("payment timeout", files)

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertIn("found 0 matching source line(s)", result["summary"])
        self.assertTrue(
            any("No useful evidence found" in item for item in result["evidence"])
        )
        self.assertIn("Insufficient evidence", result["root_cause"])
        self.assertIn("No fix can be suggested", result["suggested_fix"])

    def test_matching_evidence_is_reported_as_unconfirmed(self):
        # A match yields a tentative investigation report, not a verified cause
        # or fix; the API also explicitly says repository tests were not run.
        files = [
            make_source_file(
                "src/payment.py",
                "initialize handler\npayment timeout occurs\ncleanup handler\n",
            )
        ]

        response = self.analyze("payment timeout", files)

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertEqual(result["mode"], "INVESTIGATION")
        self.assertIn("found 1 matching source line(s)", result["summary"])
        self.assertTrue(
            any("src/payment.py:2:" in item for item in result["evidence"])
        )
        self.assertIn("not a confirmed root cause", result["root_cause"])
        self.assertIn("No specific code change is confirmed", result["suggested_fix"])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

    def test_max_content_length_and_http_413_provide_specific_evidence(self):
        files = [
            make_source_file(
                "src/request_limits.py",
                "if request.content_length > MAX_CONTENT_LENGTH: "
                "raise RequestEntityTooLarge(413)\n",
            )
        ]

        response = self.analyze(
            "Requests exceeding MAX_CONTENT_LENGTH return HTTP 413.", files
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertIn("found 1 matching source line(s)", result["summary"])
        self.assertTrue(
            any("MAX_CONTENT_LENGTH" in item and "413" in item for item in result["evidence"])
        )
        self.assertIn("not a confirmed root cause", result["root_cause"])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()

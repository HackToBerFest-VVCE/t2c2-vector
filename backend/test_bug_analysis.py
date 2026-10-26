"""Unit tests for source evidence matching and the analysis endpoint."""

import json
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
        self.assertIn("No useful evidence found", evidence[0])
        self.assertIn("technical identifier or status code", evidence[0])

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

        self.assertEqual(match_count, 1)
        self.assertEqual(strongest_match["line"], 2)
        self.assertEqual(strongest_match["keywords"], ["payment", "timeout"])
        self.assertTrue(any(item.startswith("src/payment.py:2:") for item in evidence))

    def test_single_keyword_overlaps_are_not_reported_as_useful_evidence(self):
        files = [
            make_source_file(
                "src/unrelated.py",
                "payment tracking is initialized\n"
                "timeout handling uses the default path\n",
            )
        ]

        evidence, match_count, strongest_match = app_module.find_matching_evidence(
            files, "payment timeout"
        )

        self.assertEqual(match_count, 0)
        self.assertIsNone(strongest_match)
        self.assertIn("No useful evidence found", evidence[0])
        self.assertIn("technical identifier or status code", evidence[0])
        self.assertFalse(any("src/unrelated.py:" in item for item in evidence))

    def test_related_match_includes_its_path_line_and_nearby_code_only(self):
        files = [
            make_source_file(
                "src/alpha.py",
                "payment state is initialized\n"
                "unrelated code\n"
                "unrelated cleanup\n",
            ),
            make_source_file(
                "src/beta.py",
                "line before\npayment timeout is retried\nline after\n",
            ),
        ]

        evidence, match_count, strongest_match = app_module.find_matching_evidence(
            files, "payment timeout"
        )

        self.assertEqual(match_count, 1)
        self.assertEqual(strongest_match["path"], "src/beta.py")
        self.assertTrue(any(item.startswith("src/beta.py:2:") for item in evidence))
        self.assertTrue(
            any(
                "src/beta.py:1: [context, not counted as evidence] line before"
                in item
                for item in evidence
            )
        )
        self.assertTrue(
            any(
                "src/beta.py:3: [context, not counted as evidence] line after"
                in item
                for item in evidence
            )
        )
        self.assertFalse(any("src/alpha.py:1:" in item for item in evidence))

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
        self.assertTrue(
            any(
                item.startswith("src/request_limits.py:2:")
                and "[match:" in item
                for item in evidence
            )
        )

    def test_application_evidence_ranks_before_test_evidence(self):
        # Non-test application files rank ahead even when a test line matches
        # more keywords than the available application line.
        files = [
            make_source_file(
                "tests/test_payment.py",
                "payment timeout crash appears in this test\n",
            ),
            make_source_file("src/payment.py", "payment timeout processing starts here\n"),
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
        self.assertTrue(
            any(
                "src/payment.py:4: [context, not counted as evidence] retry payment"
                in item
                for item in evidence
            )
        )

    def test_technical_terms_across_nearby_lines_are_evidence(self):
        files = [
            make_source_file(
                "src/request_limits.py",
                "handler setup\n"
                "if request.content_length > MAX_CONTENT_LENGTH:\n"
                "    raise RequestEntityTooLarge()\n"
                "status_code = 413\n"
                "return error response\n",
            )
        ]

        evidence, match_count, strongest_match = app_module.find_matching_evidence(
            files,
            "Requests exceeding MAX_CONTENT_LENGTH return HTTP 413, "
            "but I expected the endpoint to return a JSON error.",
        )

        self.assertEqual(match_count, 2)
        self.assertEqual(strongest_match["path"], "src/request_limits.py")
        self.assertEqual(strongest_match["line"], 2)
        self.assertEqual(strongest_match["matching_lines"], [2, 4])
        self.assertTrue(
            any("src/request_limits.py:2: [match: max_content_length;" in item for item in evidence)
        )
        self.assertTrue(
            any("src/request_limits.py:4: [match: 413;" in item for item in evidence)
        )
        self.assertTrue(
            any(
                "src/request_limits.py:3: [context, not counted as evidence]"
                in item
                for item in evidence
            )
        )
        displayed_source_lines = [
            item
            for item in evidence
            if item.startswith("src/request_limits.py:")
        ]
        displayed_line_numbers = [
            item.split(":", 2)[1] for item in displayed_source_lines
        ]
        self.assertEqual(
            len(displayed_line_numbers),
            len(set(displayed_line_numbers)),
        )
        self.assertEqual(
            sum(item.startswith("src/request_limits.py:") for item in evidence),
            len(
                {
                    item.split(":", 2)[1]
                    for item in evidence
                    if item.startswith("src/request_limits.py:")
                }
            ),
        )

    def test_context_without_technical_terms_does_not_create_evidence(self):
        files = [
            make_source_file(
                "src/unrelated.py",
                "payment handling starts\n"
                "unrelated helper\n"
                "timeout handling ends\n",
            )
        ]

        evidence, match_count, strongest_match = app_module.find_matching_evidence(
            files, "payment timeout"
        )

        self.assertEqual(match_count, 0)
        self.assertIsNone(strongest_match)
        self.assertIn("No useful evidence found", evidence[0])
        self.assertFalse(any("[match:" in item for item in evidence))


class AnalyzeEndpointTests(unittest.TestCase):
    def setUp(self):
        self.client = app_module.app.test_client()
        self.explainer_patch = patch(
            "app.explain_bug",
            return_value=app_module.ExplanationResult(
                available=False,
                reason="disabled_in_test",
            ),
        )
        self.explainer_patch.start()
        self.addCleanup(self.explainer_patch.stop)

    def test_frontend_script_is_served_without_stale_cache(self):
        response = self.client.get("/script.js")

        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("decodeReportText", response.get_data(as_text=True))
        response.close()

    def test_api_report_fields_are_plain_text_not_html_entities(self):
        files = [
            make_source_file(
                "src/flask/wrappers.py",
                "MAX_CONTENT_LENGTH is the request body size limit.\n"
                "Requests exceeding the limit raise RequestEntityTooLarge with status 413.\n",
            )
        ]

        response = self.analyze(
            "Requests exceeding MAX_CONTENT_LENGTH raise HTTP 413.",
            files,
        )

        self.assertEqual(response.status_code, 200)
        report = response.get_json()
        all_report_text = json.dumps(report, ensure_ascii=False)
        self.assertNotRegex(all_report_text, r"&#x[0-9a-f]+;|&amp;#x")
        self.assertEqual(report["tests"]["status"], "NOT_RUN")

    def test_missing_required_inputs_return_client_errors(self):
        requests = (
            ({}, "repository_url is required"),
            ({"repository_url": "https://github.com/example/project"}, "bug_description is required"),
            (
                {
                    "repository_url": "https://github.com/example/project",
                    "bug_description": " ",
                },
                "bug_description is required",
            ),
        )

        for payload, expected_error in requests:
            with self.subTest(payload=payload):
                with patch("app.get_repository_sources") as mocked_retrieval:
                    response = self.client.post("/api/analyze", json=payload)

                self.assertEqual(response.status_code, 400)
                self.assertIn(expected_error, response.get_json()["error"])
                mocked_retrieval.assert_not_called()

    def test_malformed_and_non_object_requests_return_client_errors(self):
        requests = (
            {
                "data": "not valid JSON",
                "content_type": "application/json",
                "expected_error": "Request body must be a JSON object.",
            },
            {
                "json": ["not", "an", "object"],
                "expected_error": "Request body must be a JSON object.",
            },
            {
                "json": {
                    "repository_url": 123,
                    "bug_description": "payment timeout",
                },
                "expected_error": "repository_url is required",
            },
            {
                "json": {
                    "repository_url": "https://github.com/example/project",
                    "bug_description": ["payment", "timeout"],
                },
                "expected_error": "bug_description is required",
            },
        )

        for request_kwargs in requests:
            with self.subTest(request_kwargs=request_kwargs):
                with patch("app.get_repository_sources") as mocked_retrieval:
                    response = self.client.post(
                        "/api/analyze",
                        **{
                            key: value
                            for key, value in request_kwargs.items()
                            if key != "expected_error"
                        },
                    )

                self.assertEqual(response.status_code, 400)
                self.assertIn(
                    request_kwargs["expected_error"],
                    response.get_json()["error"],
                )
                mocked_retrieval.assert_not_called()

    def test_malformed_github_url_returns_client_error(self):
        with patch("app.get_repository_sources") as mocked_retrieval:
            response = self.client.post(
                "/api/analyze",
                json={
                    "repository_url": "https://[::1",
                    "bug_description": "payment timeout",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("repository_url must look like", response.get_json()["error"])
        mocked_retrieval.assert_not_called()

    def test_repository_url_requires_owner_and_repository_path(self):
        invalid_urls = (
            "https://github.com/owner",
            "https://github.com/owner/",
            "https://github.com//owner/repository",
            "https://github.com/owner//repository",
            "https://github.com/owner/repository/extra",
        )
        for repository_url in invalid_urls:
            with self.subTest(repository_url=repository_url):
                with patch("app.get_repository_sources") as mocked_retrieval:
                    response = self.client.post(
                        "/api/analyze",
                        json={
                            "repository_url": repository_url,
                            "bug_description": "payment timeout",
                        },
                    )

                self.assertEqual(response.status_code, 400)
                self.assertIn(
                    "repository_url must look like",
                    response.get_json()["error"],
                )
                mocked_retrieval.assert_not_called()

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

    def test_valid_repository_is_analyzed_using_mocked_retrieval(self):
        files = [
            make_source_file(
                "src/payment.py",
                "initialize payment handler\npayment timeout occurs\n",
            )
        ]

        response = self.analyze("payment timeout", files)

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertIn("Retrieved 1 source file(s).", result["evidence"])
        self.assertIn("found 1 matching source line(s)", result["summary"])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

    def test_accepted_gemini_explanation_updates_only_tentative_text(self):
        files = [
            make_source_file("src/payment.py", "payment timeout is retried\n")
        ]
        explanation = (
            "The retry handling may be related to the reported timeout; "
            "check whether the retry path matches the failing case."
        )

        with patch(
            "app.explain_bug",
            return_value=app_module.ExplanationResult(
                available=True,
                explanation=explanation,
            ),
        ) as mocked_explainer:
            response = self.analyze("payment timeout", files)

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertEqual(
            result["beginner_explanation"]["what_might_be_wrong"],
            explanation,
        )
        self.assertTrue(result["gemini_used"])
        self.assertIn("not a confirmed root cause", result["root_cause"])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")
        mocked_explainer.assert_called_once()
        call_description, call_evidence = mocked_explainer.call_args.args
        self.assertEqual(call_description, "payment timeout")
        self.assertEqual(len(call_evidence), 1)
        self.assertIn("src/payment.py:1: [match:", call_evidence[0])

    def test_unavailable_gemini_keeps_deterministic_explanation(self):
        files = [
            make_source_file("src/payment.py", "payment timeout is retried\n")
        ]

        with patch(
            "app.explain_bug",
            return_value=app_module.ExplanationResult(
                available=False,
                reason="provider_unavailable",
            ),
        ):
            response = self.analyze("payment timeout", files)

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assertEqual(
            result["beginner_explanation"]["what_might_be_wrong"],
            "The retrieved source line at src/payment.py:1 reads: "
            "\"payment timeout is retried\". It contains the reported term(s) payment, "
            "timeout, making it a concrete location to inspect. A text match alone "
            "does not show runtime behavior or confirm a root cause.",
        )
        self.assertIn("src/payment.py:1", result["beginner_explanation"]["why_evidence_matters"])
        self.assertFalse(result["gemini_used"])
        self.assertIn("not a confirmed root cause", result["root_cause"])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

    def test_github_retrieval_errors_return_expected_http_status(self):
        errors = (
            (app_module.RepositoryNotFoundError, 404),
            (app_module.RepositoryAccessError, 403),
            (app_module.GitHubRateLimitError, 429),
            (app_module.GitHubNetworkError, 503),
            (app_module.UnsupportedRepositoryError, 400),
            (app_module.GitHubRetrievalError, 502),
        )

        for error_type, expected_status in errors:
            with self.subTest(error_type=error_type.__name__):
                with patch(
                    "app.get_repository_sources",
                    side_effect=error_type("retrieval failed"),
                ) as mocked_retrieval:
                    response = self.client.post(
                        "/api/analyze",
                        json={
                            "repository_url": "https://github.com/example/project",
                            "bug_description": "payment timeout",
                        },
                    )

                self.assertEqual(response.status_code, expected_status)
                self.assertEqual(
                    response.get_json(), {"error": "retrieval failed"}
                )
                mocked_retrieval.assert_called_once_with(
                    "https://github.com/example/project", "payment timeout"
                )

    def assert_analysis_schema(self, result):
        # These are the response fields returned by analyze().
        self.assertEqual(
            set(result),
            {
                "mode",
                "message",
                "summary",
                "root_cause",
                "gemini_used",
                "evidence",
                "suggested_fix",
                "suggested_tests",
                "beginner_explanation",
                "investigation_guide",
                "tests",
            },
        )
        self.assertEqual(
            result["tests"],
            {
                "status": "NOT_RUN",
                "details": "No repository tests were run.",
            },
        )
        self.assertEqual(
            set(result["beginner_explanation"]),
            {
                "what_code_does",
                "what_might_be_wrong",
                "why_evidence_matters",
                "what_to_check_next",
                "technical_terms",
            },
        )
        self.assertEqual(
            set(result["investigation_guide"]),
            {"missing_information", "steps"},
        )
        self.assertEqual(len(result["investigation_guide"]["steps"]), 3)

    def test_broad_description_returns_broad_search_message(self):
        # Broad input gets a request for more specific details, not a diagnosis.
        response = self.analyze("bug error problem", [])

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertIn("too broad", result["message"])
        self.assertIn("more specific bug details", result["summary"])
        self.assertIn("Insufficient evidence", result["root_cause"])
        explanation = result["beginner_explanation"]
        self.assertIn("No specific code line", explanation["what_code_does"])
        self.assertIn("not enough matching evidence", explanation["what_might_be_wrong"])
        self.assertTrue(explanation["what_to_check_next"])
        self.assertTrue(explanation["technical_terms"])
        guide = result["investigation_guide"]
        self.assertTrue(any("exact steps" in item for item in guide["missing_information"]))
        self.assertIn("Repeat the same steps", guide["steps"][1]["instruction"])
        self.assertIn("No relevant source line", guide["steps"][2]["instruction"])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

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
                self.assertEqual(result["suggested_tests"], [])
                self.assertEqual(result["tests"]["status"], "NOT_RUN")
                self.assertIn(
                    "not enough matching evidence",
                    result["beginner_explanation"]["what_might_be_wrong"],
                )
                self.assertIn("No relevant source line", result["investigation_guide"]["steps"][2]["instruction"])

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
        self.assertEqual(result["suggested_tests"], [])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

    def test_single_word_overlap_does_not_create_possible_root_cause(self):
        files = [
            make_source_file(
                "src/unrelated.py",
                "payment is mentioned here\n"
                "timeout is mentioned on another line\n",
            )
        ]

        response = self.analyze("payment timeout", files)

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertIn("found 0 matching source line(s)", result["summary"])
        self.assertFalse(
            any("src/unrelated.py:" in item for item in result["evidence"])
        )
        self.assertIn("Insufficient evidence", result["root_cause"])
        self.assertEqual(result["suggested_tests"], [])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")
        self.assertIn(
            "not enough matching evidence",
            result["beginner_explanation"]["what_might_be_wrong"],
        )
        self.assertIn("No relevant source line", result["investigation_guide"]["steps"][2]["instruction"])

    def test_quantum_pineapple_description_returns_no_suggested_test(self):
        files = [
            make_source_file(
                "src/flask/wrappers.py",
                "def get_response(self):\n    return self.response\n",
            )
        ]

        response = self.analyze(
            "The quantum pineapple scheduler fails during moonlight synchronization.",
            files,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertIn("found 0 matching source line(s)", result["summary"])
        self.assertEqual(result["suggested_tests"], [])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")
        explanation = result["beginner_explanation"]
        self.assertIn("No specific code line", explanation["what_code_does"])
        self.assertIn("cause would be a guess", explanation["why_evidence_matters"])
        self.assertTrue(explanation["what_to_check_next"])

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
        self.assertEqual(result["suggested_tests"], [])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")
        explanation = result["beginner_explanation"]
        self.assertIn("cannot explain", explanation["what_code_does"])
        self.assertIn("not proof", explanation["why_evidence_matters"])
        self.assertIn(
            "does not show runtime behavior",
            result["beginner_explanation"]["what_might_be_wrong"],
        )

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
        self.assertEqual(len(result["suggested_tests"]), 1)
        suggestion = result["suggested_tests"][0]
        self.assertEqual(
            set(suggestion),
            {"title", "setup", "action", "expected_result", "evidence_basis"},
        )
        self.assertTrue(suggestion["title"])
        self.assertIn("MAX_CONTENT_LENGTH", suggestion["setup"])
        self.assertIn("request-size check", suggestion["action"])
        self.assertIn("status and body", suggestion["expected_result"])
        self.assertNotIn("JSON", suggestion["expected_result"])
        self.assertEqual(
            suggestion["evidence_basis"],
            {"path": "src/request_limits.py", "line": 1},
        )
        explanation = result["beginner_explanation"]
        self.assertIn("request body length", explanation["what_code_does"])
        self.assertIn("may be relevant", explanation["what_might_be_wrong"])
        self.assertIn("src/request_limits.py:1", explanation["what_might_be_wrong"])
        self.assertIn("does not show the runtime response", explanation["what_might_be_wrong"])
        self.assertIn("confirm a root cause", explanation["what_might_be_wrong"])
        self.assertIn("MAX_CONTENT_LENGTH", explanation["what_to_check_next"][0])
        self.assertTrue(
            all(term["meaning"] for term in explanation["technical_terms"])
        )
        guide = result["investigation_guide"]
        self.assertIn("matching lines", guide["steps"][2]["instruction"])
        self.assertIn("not separate proof", guide["steps"][2]["instruction"])
        self.assertFalse(
            any("test" in item.lower() for item in guide["missing_information"])
        )
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

    def test_multiline_request_limit_evidence_produces_cautious_suggestion(self):
        files = [
            make_source_file(
                "src/flask/wrappers.py",
                "if request.content_length > MAX_CONTENT_LENGTH:\n"
                "    raise RequestEntityTooLarge()\n"
                "status_code = 413\n",
            )
        ]

        response = self.analyze(
            "Requests exceeding MAX_CONTENT_LENGTH return HTTP 413.",
            files,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertEqual(len(result["suggested_tests"]), 1)
        suggestion = result["suggested_tests"][0]
        self.assertEqual(
            set(suggestion),
            {"title", "setup", "action", "expected_result", "evidence_basis"},
        )
        self.assertTrue(suggestion["title"])
        self.assertIn("MAX_CONTENT_LENGTH", suggestion["setup"])
        self.assertIn("status and body", suggestion["expected_result"])
        self.assertIn("documented or expected behavior", suggestion["expected_result"])
        self.assertNotIn("JSON", suggestion["expected_result"])
        self.assertEqual(
            suggestion["evidence_basis"],
            {"path": "src/flask/wrappers.py", "line": 1},
        )
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

    def test_request_size_comparison_in_reverse_order_suggests_supported_test(self):
        files = [
            make_source_file(
                "src/flask/wrappers.py",
                "\n" * 59
                + "if max_content_length is not None:\n"
                + "    if max_content_length < request.content_length:\n"
                + "        raise RequestEntityTooLarge(413)\n",
            )
        ]

        response = self.analyze(
            "Requests larger than MAX_CONTENT_LENGTH result in HTTP 413.",
            files,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertEqual(len(result["suggested_tests"]), 1)
        suggestion = result["suggested_tests"][0]
        self.assertEqual(
            set(suggestion),
            {"title", "setup", "action", "expected_result", "evidence_basis"},
        )
        self.assertEqual(
            suggestion["evidence_basis"],
            {"path": "src/flask/wrappers.py", "line": 61},
        )
        self.assertIn("limit", suggestion["title"])
        self.assertIn("just below, at, and above", suggestion["setup"])
        self.assertIn("status and body", suggestion["expected_result"])
        self.assertNotIn("JSON", suggestion["expected_result"])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

    def test_request_limit_docstring_supports_cautious_suggested_test(self):
        files = [
            make_source_file(
                "src/flask/wrappers.py",
                "\n" * 59
                + '    """Request wrapper documentation.\n'
                + "    Raises RequestEntityTooLarge if request data exceeds "
                + "the configured MAX_CONTENT_LENGTH (HTTP 413).\n"
                + '    """\n',
            )
        ]

        response = self.analyze(
            "Requests exceeding MAX_CONTENT_LENGTH raise HTTP 413.",
            files,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertEqual(len(result["suggested_tests"]), 1)
        suggestion = result["suggested_tests"][0]
        self.assertEqual(
            set(suggestion),
            {"title", "setup", "action", "expected_result", "evidence_basis"},
        )
        self.assertEqual(
            suggestion["evidence_basis"],
            {"path": "src/flask/wrappers.py", "line": 61},
        )
        self.assertIn("request-size limit", suggestion["title"])
        self.assertIn("source documentation", suggestion["action"])
        self.assertIn("expected behavior", suggestion["expected_result"])
        explanation = result["beginner_explanation"]
        self.assertIn("documentation at src/flask/wrappers.py:61", explanation["what_might_be_wrong"])
        self.assertIn("documented behavior", explanation["what_might_be_wrong"])
        self.assertIn("not proof", explanation["what_might_be_wrong"])
        self.assertNotIn("JSON", suggestion["expected_result"])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

    def test_partial_request_limit_evidence_explains_what_is_missing(self):
        files = [
            make_source_file(
                "src/flask/wrappers.py",
                "MAX_CONTENT_LENGTH produces status_code 413 for this response.\n"
                "The value is available to request wrappers.\n",
            )
        ]

        response = self.analyze(
            "Requests exceeding MAX_CONTENT_LENGTH raise HTTP 413.",
            files,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertEqual(result["suggested_tests"], [])
        self.assertTrue(
            any(
                "do not show both" in item
                for item in result["investigation_guide"]["missing_information"]
            )
        )
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

    def test_request_size_check_with_config_lookup_produces_suggestion(self):
        files = [
            make_source_file(
                "src/flask/wrappers.py",
                "\n" * 59
                + "if request.content_length > current_app.config[\"MAX_CONTENT_LENGTH\"]:\n"
                + "    raise RequestEntityTooLarge()\n"
                + "status_code = 413\n"
                + "return response\n",
            )
        ]

        response = self.analyze(
            "Requests exceeding MAX_CONTENT_LENGTH return HTTP 413.",
            files,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertEqual(len(result["suggested_tests"]), 1)
        suggestion = result["suggested_tests"][0]
        self.assertEqual(
            set(suggestion),
            {"title", "setup", "action", "expected_result", "evidence_basis"},
        )
        self.assertTrue(suggestion["title"])
        self.assertEqual(
            suggestion["evidence_basis"],
            {"path": "src/flask/wrappers.py", "line": 60},
        )
        self.assertIn("status and body", suggestion["expected_result"])
        self.assertNotIn("JSON", suggestion["expected_result"])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

    def test_request_size_suggestion_uses_split_wrapper_evidence_without_assuming_json(self):
        files = [
            make_source_file(
                "src/flask/wrappers.py",
                "\n" * 59
                + "if request.content_length > current_app.config[\"MAX_CONTENT_LENGTH\"]:\n"
                + "    raise RequestEntityTooLarge()\n"
                + "return response\n",
            )
        ]

        response = self.analyze(
            "MAX_CONTENT_LENGTH RequestEntityTooLarge HTTP 413 for oversized requests.",
            files,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertEqual(len(result["suggested_tests"]), 1)
        suggestion = result["suggested_tests"][0]
        self.assertEqual(
            suggestion["evidence_basis"],
            {"path": "src/flask/wrappers.py", "line": 60},
        )
        self.assertIn("status and body", suggestion["expected_result"])
        self.assertNotIn("JSON", suggestion["expected_result"])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

    def test_request_limit_words_without_matching_source_pattern_do_not_suggest_test(self):
        files = [
            make_source_file(
                "src/flask/wrappers.py",
                "MAX_CONTENT_LENGTH is documented here\n"
                "unrelated response handling\n"
                "status_code = 413\n",
            )
        ]

        response = self.analyze(
            "Requests exceeding MAX_CONTENT_LENGTH return HTTP 413.",
            files,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertEqual(result["suggested_tests"], [])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")

    def test_request_limit_terms_on_nearby_lines_are_reported_without_guessing(self):
        files = [
            make_source_file(
                "src/flask/wrappers.py",
                "if request.content_length > MAX_CONTENT_LENGTH:\n"
                "    raise RequestEntityTooLarge()\n"
                "status_code = 413\n",
            )
        ]

        response = self.analyze(
            "Requests exceeding MAX_CONTENT_LENGTH return HTTP 413, "
            "but I expected the endpoint to return a JSON error.",
            files,
        )

        self.assertEqual(response.status_code, 200)
        result = response.get_json()
        self.assert_analysis_schema(result)
        self.assertIn("found 2 matching source line(s)", result["summary"])
        self.assertTrue(
            any(
                "src/flask/wrappers.py:1: [match: max_content_length;"
                in item
                for item in result["evidence"]
            )
        )
        self.assertTrue(
            any(
                "src/flask/wrappers.py:3: [match: 413;"
                in item
                for item in result["evidence"]
            )
        )
        self.assertIn("not a confirmed root cause", result["root_cause"])
        self.assertEqual(result["tests"]["status"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()

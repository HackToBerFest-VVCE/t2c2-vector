"""Focused tests for safe public GitHub source retrieval."""

import base64
import json
import unittest
from unittest.mock import patch

from github_repository import (
    GitHubNetworkError,
    GitHubRateLimitError,
    GitHubRetrievalError,
    RepositoryAccessError,
    RepositoryNotFoundError,
    UnsupportedRepositoryError,
    get_repository_sources,
)


class FakeResponse:
    """A tiny response object that behaves like urllib's context manager."""

    def __init__(self, value):
        self.body = json.dumps(value).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.body[:limit]


def blob_response(text):
    """Build the JSON shape returned by GitHub's Git blob endpoint."""
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return {"content": encoded, "encoding": "base64"}


class GitHubRepositoryTests(unittest.TestCase):
    def test_fetches_only_small_relevant_source_files(self):
        tree = {
            "truncated": False,
            "tree": [
                {"path": "src/payment_handler.py", "type": "blob", "size": 20, "sha": "sha-payment"},
                {"path": "src/user_login.py", "type": "blob", "size": 18, "sha": "sha-login"},
                {"path": "README.md", "type": "blob", "size": 30, "sha": "sha-readme"},
                {"path": "vendor/third_party.py", "type": "blob", "size": 10, "sha": "sha-vendor"},
                {"path": "src/huge.py", "type": "blob", "size": 500, "sha": "sha-huge"},
            ],
        }
        responses = [
            FakeResponse({"default_branch": "main"}),
            FakeResponse(tree),
            FakeResponse(blob_response("def handle_payment(): pass\n")),
        ]

        with patch("github_repository.urlopen", side_effect=responses) as mocked_urlopen:
            result = get_repository_sources(
                "https://github.com/example/project",
                "payment handler crashes",
                max_files=1,
                max_file_bytes=100,
            )

        self.assertEqual(result["repository"], "example/project")
        self.assertEqual([item["path"] for item in result["files"]], ["src/payment_handler.py"])
        self.assertEqual(result["files"][0]["content"], "def handle_payment(): pass\n")
        self.assertEqual(result["skipped"], {"unsupported_type": 1, "too_large": 1})
        # One repository request, one tree request, and one selected-file request.
        self.assertEqual(mocked_urlopen.call_count, 3)

    def test_prefers_application_files_over_test_files(self):
        tree = {
            "truncated": False,
            "tree": [
                {"path": "tests/test_payment.py", "type": "blob", "size": 20, "sha": "sha-test"},
                {"path": "src/payment.py", "type": "blob", "size": 20, "sha": "sha-app"},
            ],
        }
        responses = [
            FakeResponse({"default_branch": "main"}),
            FakeResponse(tree),
            FakeResponse(blob_response("def process_payment(): pass\n")),
            FakeResponse(blob_response("def test_payment(): pass\n")),
        ]

        with patch("github_repository.urlopen", side_effect=responses):
            result = get_repository_sources(
                "https://github.com/example/project",
                "payment timeout crash",
                max_files=1,
            )

        self.assertEqual(result["files"][0]["path"], "src/payment.py")

    def test_prefers_request_configuration_paths_for_413_issue(self):
        tree = {
            "truncated": False,
            "tree": [
                {"path": "src/json_encoder.py", "type": "blob", "size": 24, "sha": "sha-json"},
                {"path": "src/wrappers.py", "type": "blob", "size": 30, "sha": "sha-wrappers"},
                {"path": "src/config.py", "type": "blob", "size": 28, "sha": "sha-config"},
                {"path": "tests/test_request_limit.py", "type": "blob", "size": 32, "sha": "sha-test"},
            ],
        }
        responses = [
            FakeResponse({"default_branch": "main"}),
            FakeResponse(tree),
            FakeResponse(blob_response("MAX_CONTENT_LENGTH = 1024\n")),
        ]

        with patch("github_repository.urlopen", side_effect=responses) as mocked_urlopen:
            result = get_repository_sources(
                "https://github.com/example/project",
                "Requests exceeding MAX_CONTENT_LENGTH return HTTP 413.",
                max_files=1,
            )

        self.assertEqual([item["path"] for item in result["files"]], ["src/config.py"])
        self.assertEqual(mocked_urlopen.call_count, 3)

    def test_generic_json_response_words_do_not_outrank_relevant_source_paths(self):
        tree = {
            "truncated": False,
            "tree": [
                {"path": "src/flask/json/__init__.py", "type": "blob", "size": 24, "sha": "sha-json-init"},
                {"path": "src/flask/json/provider.py", "type": "blob", "size": 28, "sha": "sha-json-provider"},
                {"path": "src/flask/json/tag.py", "type": "blob", "size": 22, "sha": "sha-json-tag"},
                {"path": "src/flask/wrappers.py", "type": "blob", "size": 30, "sha": "sha-wrappers"},
                {"path": "src/flask/app.py", "type": "blob", "size": 26, "sha": "sha-app"},
                {"path": "src/flask/config.py", "type": "blob", "size": 24, "sha": "sha-config"},
                {"path": "src/flask/exceptions.py", "type": "blob", "size": 28, "sha": "sha-exceptions"},
            ],
        }
        responses = [
            FakeResponse({"default_branch": "main"}),
            FakeResponse(tree),
            FakeResponse(blob_response("MAX_CONTENT_LENGTH = 1024\n")),
            FakeResponse(blob_response("request wrapper source\n")),
            FakeResponse(blob_response("request exception source\n")),
        ]

        with patch("github_repository.urlopen", side_effect=responses):
            result = get_repository_sources(
                "https://github.com/example/project",
                "Requests exceeding MAX_CONTENT_LENGTH return HTTP 413, "
                "but I expected the endpoint to return a JSON error.",
                max_files=3,
            )

        selected_paths = {item["path"] for item in result["files"]}
        self.assertIn("src/flask/wrappers.py", selected_paths)
        self.assertNotIn("src/flask/json/__init__.py", selected_paths)
        self.assertNotIn("src/flask/json/provider.py", selected_paths)
        self.assertNotIn("src/flask/json/tag.py", selected_paths)
        self.assertEqual(len(result["files"]), 3)

    def test_rejects_malformed_github_url_as_unsupported(self):
        with self.assertRaises(UnsupportedRepositoryError):
            get_repository_sources("https://[::1")

    def test_rejects_non_github_repository_urls(self):
        with self.assertRaises(UnsupportedRepositoryError):
            get_repository_sources("https://example.com/user/project")

    def test_reports_repository_not_found(self):
        from urllib.error import HTTPError

        error = HTTPError("https://api.github.com/repos/no/such-repo", 404, "Not Found", {}, None)
        with patch("github_repository.urlopen", side_effect=error):
            with self.assertRaises(RepositoryNotFoundError):
                get_repository_sources("https://github.com/no/such-repo")

    def test_reports_rate_limit(self):
        from urllib.error import HTTPError

        error = HTTPError(
            "https://api.github.com/repos/user/project",
            403,
            "Forbidden",
            {"X-RateLimit-Remaining": "0"},
            None,
        )
        with patch("github_repository.urlopen", side_effect=error):
            with self.assertRaises(GitHubRateLimitError):
                get_repository_sources("https://github.com/user/project")

    def test_reports_access_denied_and_unexpected_http_errors(self):
        from urllib.error import HTTPError

        cases = (
            (403, RepositoryAccessError),
            (500, GitHubRetrievalError),
        )
        for status_code, expected_error in cases:
            with self.subTest(status_code=status_code):
                error = HTTPError(
                    "https://api.github.com/repos/user/project",
                    status_code,
                    "Failure",
                    {},
                    None,
                )
                with patch("github_repository.urlopen", side_effect=error):
                    with self.assertRaises(expected_error):
                        get_repository_sources("https://github.com/user/project")

    def test_reports_network_errors(self):
        from urllib.error import URLError

        with patch("github_repository.urlopen", side_effect=URLError("offline")):
            with self.assertRaises(GitHubNetworkError):
                get_repository_sources("https://github.com/user/project")

    def test_rejects_an_overly_large_repository_tree(self):
        responses = [
            FakeResponse({"default_branch": "main"}),
            FakeResponse({"truncated": True, "tree": []}),
        ]
        with patch("github_repository.urlopen", side_effect=responses):
            with self.assertRaises(UnsupportedRepositoryError):
                get_repository_sources("https://github.com/user/project")


if __name__ == "__main__":
    unittest.main()

"""Small, read-only helpers for retrieving source files from public GitHub repos."""

import base64
import json
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


API_ROOT = "https://api.github.com"
USER_AGENT = "RepoBridge-AI-Hackathon-Demo"
SUPPORTED_SOURCE_SUFFIXES = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".go", ".h", ".hpp",
    ".html", ".java", ".js", ".jsx", ".kt", ".php", ".py",
    ".rb", ".rs", ".scala", ".sh", ".swift", ".ts", ".tsx",
    ".vue", ".xml",
}
IGNORED_DIRECTORY_NAMES = {
    ".git", ".venv", "__pycache__", "build", "dist", "node_modules",
    "vendor", "venv",
}
ISSUE_STOP_WORDS = {
    "a", "about", "after", "again", "an", "and", "application", "app", "are",
    "as", "at", "be", "being", "body", "bug", "broken", "by", "can", "code",
    "could", "does", "error", "failure", "flask", "for", "from", "handling",
    "have", "http", "if", "in", "into", "is", "issue", "it", "limit", "more",
    "of", "on", "only", "or", "please", "problem", "reported", "request",
    "routing", "should", "that", "the", "their", "there", "these", "this",
    "to", "use", "was", "when", "where", "which", "with", "will",
}
ISSUE_PATH_STOP_WORDS = {
    "endpoint", "exceeding", "expected", "expects", "json", "return",
    "returned", "returning", "returns",
}
ISSUE_PATH_HINTS = {
    "max_content_length": {
        "app", "config", "request", "wrapper", "wrappers", "exception", "error",
    },
    "content_length": {"config", "request", "wrapper", "wrappers"},
    "requests": {"config", "request", "wrapper", "wrappers"},
    "413": {"request", "response", "exception", "error"},
    "500": {"request", "response", "exception", "error"},
    "404": {"routing", "route", "request", "response", "exception", "error"},
    "timeout": {"timeout", "client", "connection", "request"},
}


class GitHubRetrievalError(Exception):
    """Base error for problems while reading a public GitHub repository."""


class RepositoryNotFoundError(GitHubRetrievalError):
    """Raised when GitHub cannot find the requested public repository."""


class RepositoryAccessError(GitHubRetrievalError):
    """Raised when GitHub denies access to repository data."""


class GitHubRateLimitError(GitHubRetrievalError):
    """Raised when GitHub's unauthenticated API rate limit is reached."""


class GitHubNetworkError(GitHubRetrievalError):
    """Raised when a network request to GitHub fails."""


class UnsupportedRepositoryError(GitHubRetrievalError):
    """Raised when a URL or GitHub response cannot be handled safely."""


def _parse_repository_url(repository_url):
    """Return the owner and repository name from a standard GitHub URL."""
    if not isinstance(repository_url, str):
        raise UnsupportedRepositoryError("repository_url must be text.")

    try:
        parsed_url = urlsplit(repository_url.strip())
    except ValueError as error:
        raise UnsupportedRepositoryError(
            "Use a public GitHub URL like https://github.com/owner/repository."
        ) from error
    parts = parsed_url.path.split("/")
    if len(parts) == 4 and parts[-1] == "":
        parts.pop()
    if (
        parsed_url.scheme.lower() != "https"
        or parsed_url.netloc.lower() != "github.com"
        or parsed_url.query
        or parsed_url.fragment
        or len(parts) != 3
        or parts[0] != ""
        or not re.fullmatch(r"[A-Za-z0-9-]+", parts[1])
    ):
        raise UnsupportedRepositoryError(
            "Use a public GitHub URL like https://github.com/owner/repository."
        )

    owner = parts[1]
    repository = parts[2]
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not re.fullmatch(r"[A-Za-z0-9._-]+", repository) or repository in {".", ".."}:
        raise UnsupportedRepositoryError("The repository name in the URL is invalid.")

    return owner, repository


def _read_json(url, max_response_bytes):
    """Request a JSON API response, limiting how much data is read into memory."""
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=10) as response:
            body = response.read(max_response_bytes + 1)
            if len(body) > max_response_bytes:
                raise UnsupportedRepositoryError("GitHub returned more data than this demo allows.")
    except HTTPError as error:
        headers = getattr(error, "headers", None) or {}
        response_body = error.read(4096).decode("utf-8", errors="replace").lower()
        if error.code == 404:
            raise RepositoryNotFoundError(
                "Repository or file was not found. It may be private or unavailable."
            ) from error
        if error.code == 429 or (
            error.code == 403
            and (
                headers.get("X-RateLimit-Remaining") == "0"
                or "rate limit" in response_body
            )
        ):
            raise GitHubRateLimitError(
                "GitHub API rate limit reached. Please wait and try again later."
            ) from error
        if error.code in {401, 403}:
            raise RepositoryAccessError(
                "GitHub denied access. This demo only supports public repositories."
            ) from error
        raise GitHubRetrievalError(
            "GitHub returned an unexpected HTTP error (status {}).".format(error.code)
        ) from error
    except (URLError, TimeoutError, socket.timeout, OSError) as error:
        raise GitHubNetworkError("Could not connect to GitHub. Check the network and retry.") from error

    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UnsupportedRepositoryError("GitHub returned a response this demo could not read.") from error


def _issue_terms(bug_description):
    """Create simple keywords to compare the issue with source-file paths."""
    words = re.findall(
        r"[A-Za-z][A-Za-z0-9_]{2,}|(?<!\w)\d{3}(?!\w)",
        (bug_description or "").lower(),
    )
    return {
        word
        for word in words
        if word not in ISSUE_STOP_WORDS and word not in ISSUE_PATH_STOP_WORDS
    }


def _rank_source_file(entry, issue_terms):
    """Rank paths using report terms and retrieval-only technical path hints."""
    path = entry["path"]
    # Split common filename separators so names like payment_handler.py match
    # both "payment" and "handler" from the bug description.
    normalized_path = re.sub(r"[._/-]+", " ", path.lower())
    path_words = set(re.findall(r"[A-Za-z][A-Za-z0-9]{2,}", normalized_path))
    score = len(path_words & issue_terms) * 10
    for term in issue_terms:
        score += len(path_words & ISSUE_PATH_HINTS.get(term, set())) * 4

    path_parts = [part.lower() for part in path.split("/")]
    file_name = path_parts[-1]
    is_test_file = (
        any(part in {"test", "tests", "spec", "specs"} for part in path_parts[:-1])
        or file_name.startswith(("test_", "tests_", "spec_"))
        or file_name.endswith(("_test.py", "_tests.py", ".spec.js", ".spec.ts"))
    )
    if is_test_file:
        score -= 2
    if len(path_parts) == 1:
        score += 1
    return is_test_file, -score, len(path_parts), path.lower()


def get_repository_sources(
    repository_url,
    bug_description="",
    max_files=3,
    max_file_bytes=100_000,
):
    """Fetch a few small source files from a public GitHub repository.

    This only reads repository metadata and file contents. Downloaded code is
    returned as text and is never imported, executed, or run as a test.
    """
    if not isinstance(max_files, int) or isinstance(max_files, bool) or not 1 <= max_files <= 5:
        raise ValueError("max_files must be an integer from 1 to 5.")
    if (
        not isinstance(max_file_bytes, int)
        or isinstance(max_file_bytes, bool)
        or not 1 <= max_file_bytes <= 1_000_000
    ):
        raise ValueError("max_file_bytes must be an integer from 1 to 1,000,000.")

    owner, repository = _parse_repository_url(repository_url)
    repo_api_url = "{}/repos/{}/{}".format(API_ROOT, quote(owner), quote(repository))
    metadata = _read_json(repo_api_url, 1_000_000)
    default_branch = metadata.get("default_branch") if isinstance(metadata, dict) else None
    if not isinstance(default_branch, str) or not default_branch:
        raise UnsupportedRepositoryError("GitHub did not provide a default branch for this repository.")

    tree_url = "{}/git/trees/{}?recursive=1".format(
        repo_api_url, quote(default_branch, safe="")
    )
    tree_data = _read_json(tree_url, 10_000_000)
    if not isinstance(tree_data, dict) or not isinstance(tree_data.get("tree"), list):
        raise UnsupportedRepositoryError("GitHub returned an unexpected repository file tree.")
    if tree_data.get("truncated"):
        raise UnsupportedRepositoryError(
            "This repository's file tree is too large for the demo to inspect safely."
        )

    # Use path names—not file execution—to choose a few likely source files.
    issue_terms = _issue_terms(bug_description)
    candidates = []
    skipped = {"unsupported_type": 0, "too_large": 0}
    for entry in tree_data["tree"]:
        if not isinstance(entry, dict) or entry.get("type") != "blob":
            continue
        path = entry.get("path")
        size = entry.get("size")
        if not isinstance(path, str) or not isinstance(size, int) or size < 0:
            continue
        path_parts = path.split("/")
        if any(part.lower() in IGNORED_DIRECTORY_NAMES for part in path_parts[:-1]):
            continue
        if path_parts[-1].lower().endswith((".min.js", ".min.css")):
            continue
        suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path_parts[-1] else ""
        if suffix not in SUPPORTED_SOURCE_SUFFIXES:
            skipped["unsupported_type"] += 1
            continue
        if size > max_file_bytes:
            skipped["too_large"] += 1
            continue
        if not isinstance(entry.get("sha"), str):
            continue
        candidates.append(entry)

    candidates.sort(key=lambda entry: _rank_source_file(entry, issue_terms))
    selected = candidates[:max_files]
    files = []
    for entry in selected:
        # Git blob endpoints return base64 text; limit its response to a little
        # more than the encoded form of our configured file-size limit.
        blob_data = _read_json(
            "{}/git/blobs/{}".format(repo_api_url, quote(entry["sha"])),
            ((max_file_bytes + 2) // 3) * 4 + 4096,
        )
        encoded_content = blob_data.get("content") if isinstance(blob_data, dict) else None
        if not isinstance(encoded_content, str):
            skipped["unsupported_type"] += 1
            continue
        try:
            content_bytes = base64.b64decode(encoded_content, validate=False)
        except (ValueError, TypeError):
            skipped["unsupported_type"] += 1
            continue
        if len(content_bytes) > max_file_bytes or b"\x00" in content_bytes:
            skipped["unsupported_type"] += 1
            continue
        try:
            content = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            skipped["unsupported_type"] += 1
            continue
        files.append({"path": entry["path"], "size": len(content_bytes), "content": content})

    return {
        "repository": "{}/{}".format(owner, repository),
        "default_branch": default_branch,
        "files": files,
        "skipped": skipped,
    }

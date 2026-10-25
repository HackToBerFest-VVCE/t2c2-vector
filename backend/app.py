
from pathlib import Path
from flask import Flask, jsonify, send_from_directory
import re
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, send_from_directory
from github_repository import (
    GitHubNetworkError,
    GitHubRateLimitError,
    GitHubRetrievalError,
    ISSUE_STOP_WORDS,
    RepositoryAccessError,
    RepositoryNotFoundError,
    UnsupportedRepositoryError,
    get_repository_sources,
)

app = Flask(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
NON_DIAGNOSTIC_WORDS = {
    "doesn", "during", "not", "response", "responses", "return", "returned",
    "returning", "returns", "something", "work", "working",
}


@app.route("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/style.css")
def stylesheet():
    return send_from_directory(FRONTEND_DIR, "style.css")


@app.route("/script.js")
def javascript():
    return send_from_directory(FRONTEND_DIR, "script.js")


@app.route("/api/health")
def health():
    return jsonify({
        "status": "healthy",
        "project": "RepoBridge AI"
    })


<<<<<<< HEAD
=======
@app.errorhandler(413)
def request_too_large(_error):
    # Keep the HTTP status at 413 while returning a clear JSON error message.
    return jsonify({
        "error": "Request body is too large.",
        "message": "Reduce the request size and try again.",
    }), 413


def find_matching_evidence(files, bug_description, max_matches=10):
    """Rank retrieved source lines that contain meaningful issue keywords."""
    description_words = re.findall(
        r"[A-Za-z][A-Za-z0-9_]{2,}|(?<!\w)\d{3}(?!\w)",
        bug_description,
    )
    keywords = {
        word.lower()
        for word in description_words
        if word.lower() not in ISSUE_STOP_WORDS
        and word.lower() not in NON_DIAGNOSTIC_WORDS
    }
    # Keep this technical phrase useful without treating either word as a
    # meaningful standalone keyword.
    if re.search(r"(?<!\w)request[\s_-]+body(?!\w)", bug_description, re.IGNORECASE):
        keywords.add("request body")
    if not keywords:
        return [
            "Bug description is too broad to identify a specific issue. Add concrete symptoms or affected feature names."
        ], 0, None
    if len(keywords) < 2:
        return [
            "Bug description is too broad to identify a specific issue. Add at least two specific terms, such as a symptom and an affected feature."
        ], 0, None

    # Match whole keywords without caring about capitalization.
    keyword_patterns = {
        word: re.compile(
            r"(?<!\w){}(?!\w)".format(
                r"[\s_-]+".join(re.escape(part) for part in word.split())
            ),
            re.IGNORECASE,
        )
        for word in keywords
    }
    ranked_matches = []
    for source_file in files:
        path = source_file["path"]
        source_lines = source_file["content"].splitlines()
        path_parts = re.split(r"[/\\]+", path.lower())
        file_name = path_parts[-1]
        is_test_file = (
            any(part in {"test", "tests", "spec", "specs"} for part in path_parts[:-1])
            or file_name.startswith(("test_", "tests_", "spec_"))
            or file_name.endswith(("_test.py", "_tests.py", ".spec.js", ".spec.ts"))
        )

        for line_number, line in enumerate(source_lines, start=1):
            matching_keywords = [
                word for word, pattern in keyword_patterns.items() if pattern.search(line)
            ]
            if not matching_keywords:
                continue

            # Keep long source lines readable in the API response.
            excerpt = line.strip()
            if len(excerpt) > 240:
                excerpt = excerpt[:237] + "..."
            evidence_text = "{}:{}: [keyword(s): {}] {}".format(
                path,
                line_number,
                ", ".join(sorted(matching_keywords)),
                excerpt or "(blank matching line)",
            )
            # Application files rank before tests; multi-keyword lines rank higher.
            ranked_matches.append(
                (
                    is_test_file,
                    -len(matching_keywords),
                    path.lower(),
                    line_number,
                    evidence_text,
                    path,
                    matching_keywords,
                    source_lines,
                )
            )

    if not ranked_matches:
        return [
            "No useful evidence found: none of the meaningful bug-description keywords matched the retrieved source lines."
        ], 0, None

    matched_keywords = {
        keyword for match in ranked_matches for keyword in match[6]
    }
    if len(matched_keywords) < 2:
        return [
            "No useful evidence found: fewer than two specific bug-description keywords matched the retrieved source lines."
        ], 0, None

    ranked_matches.sort(key=lambda match: match[:4])
    total_matches = len(ranked_matches)
    evidence = [match[4] for match in ranked_matches[:max_matches]]
    strongest_match = ranked_matches[0]
    context_lines = strongest_match[7]
    context_start = max(1, strongest_match[3] - 2)
    context_end = min(len(context_lines), strongest_match[3] + 2)
    evidence.append(
        "Nearby context for strongest match (context is not proof): {}:{}-{}.".format(
            strongest_match[5], context_start, context_end
        )
    )
    for context_line_number in range(context_start, context_end + 1):
        context_text = context_lines[context_line_number - 1].strip()
        if len(context_text) > 240:
            context_text = context_text[:237] + "..."
        evidence.append(
            "{}:{}: [context] {}".format(
                strongest_match[5], context_line_number, context_text or "(blank line)"
            )
        )
    if total_matches > max_matches:
        evidence.append(
            "Showing the 10 strongest matches out of {} matching lines.".format(total_matches)
        )
    best_details = {
        "path": strongest_match[5],
        "line": strongest_match[3],
        "keywords": sorted(strongest_match[6]),
        "context_start": context_start,
        "context_end": context_end,
    }
    return evidence, total_matches, best_details


@app.route("/api/analyze", methods=["POST"])
def analyze():
    # Read the submitted JSON without raising an error for malformed input.
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400

    # Make sure both required values are text and contain something useful.
    repo_url = data.get("repository_url")
    bug_description = data.get("bug_description")
    if not isinstance(repo_url, str) or not repo_url.strip():
        return jsonify({"error": "repository_url is required and cannot be empty."}), 400
    if not isinstance(bug_description, str) or not bug_description.strip():
        return jsonify({"error": "bug_description is required and cannot be empty."}), 400

    # Accept standard HTTPS GitHub repository URLs with an optional final slash.
    parsed_url = urlsplit(repo_url.strip())
    path_parts = parsed_url.path.split("/")
    is_repository_path = (
        len(path_parts) == 3
        or (len(path_parts) == 4 and path_parts[3] == "")
    )
    is_valid_owner = len(path_parts) > 1 and re.fullmatch(r"[A-Za-z0-9-]+", path_parts[1])
    is_valid_repository = (
        len(path_parts) > 2
        and re.fullmatch(r"[A-Za-z0-9._-]+", path_parts[2])
        and path_parts[2] not in {".", ".."}
    )
    if not (
        parsed_url.scheme.lower() == "https"
        and parsed_url.netloc.lower() == "github.com"
        and not parsed_url.query
        and not parsed_url.fragment
        and is_repository_path
        and is_valid_owner
        and is_valid_repository
    ):
        return jsonify({
            "error": "repository_url must look like https://github.com/owner/repository."
        }), 400

    # Retrieve source text only; the helper never executes repository code.
    try:
        retrieval = get_repository_sources(repo_url, bug_description)
    except RepositoryNotFoundError as error:
        return jsonify({"error": str(error)}), 404
    except RepositoryAccessError as error:
        return jsonify({"error": str(error)}), 403
    except GitHubRateLimitError as error:
        return jsonify({"error": str(error)}), 429
    except GitHubNetworkError as error:
        return jsonify({"error": str(error)}), 503
    except UnsupportedRepositoryError as error:
        return jsonify({"error": str(error)}), 400
    except GitHubRetrievalError as error:
        return jsonify({"error": str(error)}), 502

    # Search only the source text already retrieved; never execute it.
    matching_evidence, matching_line_count, strongest_match = find_matching_evidence(
        retrieval["files"], bug_description
    )
    description_is_broad = (
        matching_line_count == 0
        and matching_evidence
        and matching_evidence[0].startswith("Bug description is too broad")
    )
    evidence = [
        "Retrieval succeeded for {} on branch {}.".format(
            retrieval["repository"], retrieval["default_branch"]
        ),
        "Retrieved {} source file(s).".format(len(retrieval["files"])),
    ]
    evidence.extend(
        "Retrieved file: {} ({} bytes).".format(file["path"], file["size"])
        for file in retrieval["files"]
    )
    evidence.extend(matching_evidence)

    if strongest_match is not None:
        root_cause = (
            "Possible explanation only (not a confirmed root cause): the strongest keyword match is in "
            "{}:{} and nearby retrieved code (lines {}-{}) contains the same reported terms: {}. "
            "This region may be related to the symptom, but keyword overlap and nearby context alone "
            "are insufficient to prove causation.".format(
                strongest_match["path"],
                strongest_match["line"],
                strongest_match["context_start"],
                strongest_match["context_end"],
                ", ".join(strongest_match["keywords"]),
            )
        )
        suggested_fix = (
            "Tentative next step: review {}:{}-{} and reproduce the reported behavior before proposing a fix. "
            "No specific code change is confirmed by this keyword search.".format(
                strongest_match["path"],
                strongest_match["context_start"],
                strongest_match["context_end"],
            )
        )
    else:
        root_cause = (
            "Insufficient evidence to suggest a root cause. The retrieved files did not provide a specific, "
            "ranked keyword match with surrounding context."
        )
        suggested_fix = (
            "No fix can be suggested from the available evidence. Provide a more specific symptom or retrieve relevant source files."
        )

    # Report keyword matches as clues, not as a proven cause or a completed test.
    return jsonify({
        "mode": "INVESTIGATION",
        "message": (
            "Retrieved source files, but the bug description is too broad for a useful keyword search. No tests were performed."
            if description_is_broad
            else "Retrieved source files and searched them for bug-description keywords. No tests were performed."
        ),
        "summary": (
            "Retrieved {} source file(s); more specific bug details are needed to search for relevant evidence.".format(
                len(retrieval["files"])
            )
            if description_is_broad
            else "Retrieved {} source file(s) and found {} matching source line(s).".format(
                len(retrieval["files"]), matching_line_count
            )
        ),
        "root_cause": root_cause,
        "evidence": evidence,
        "suggested_fix": suggested_fix,
        "tests": {
            "status": "NOT_RUN",
            "details": "No repository tests were run."
        }
    })


>>>>>>> 40b458516eff23334f51716117b7c181338c4a30
if __name__ == "__main__":
    app.run(debug=True)

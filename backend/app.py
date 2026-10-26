
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from flask import Flask, g, jsonify, request, send_from_directory
from ai_explainer import ExplanationResult, explain_bug
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
    "returning", "returns", "something", "work", "working", "works", "show",
    "anything", "blank", "display", "displaying", "everything", "nothing",
    "page", "showing",
}
MIN_EVIDENCE_KEYWORDS_PER_LINE = 2
MATCH_WORD_VARIATIONS = {
    "failed": "fail",
    "failing": "fail",
    "fails": "fail",
    "crashed": "crash",
    "crashes": "crash",
    "connecting": "connect",
    "connected": "connect",
    "connections": "connect",
    "timeouts": "timeout",
    "showing": "show",
    "shown": "show",
    "shows": "show",
    "displayed": "display",
    "displays": "display",
    "rendered": "render",
    "rendering": "render",
    "loading": "load",
    "loaded": "load",
}


@app.before_request
def start_analysis_timer():
    if request.endpoint == "analyze":
        g.analysis_started_at = time.perf_counter()


@app.after_request
def log_analysis_duration(response):
    started_at = getattr(g, "analysis_started_at", None)
    if started_at is not None:
        app.logger.info(
            "Analysis timing: stage=total duration_ms=%.1f status=%d",
            (time.perf_counter() - started_at) * 1000,
            response.status_code,
        )
    return response


def _log_stage_duration(stage, started_at):
    app.logger.info(
        "Analysis timing: stage=%s duration_ms=%.1f",
        stage,
        (time.perf_counter() - started_at) * 1000,
    )


def _normalize_match_word(word):
    """Normalize common source/report word variations without external NLP."""
    normalized = word.lower()
    if normalized in MATCH_WORD_VARIATIONS:
        return MATCH_WORD_VARIATIONS[normalized]
    if len(normalized) > 4 and normalized.endswith("ies"):
        return normalized[:-3] + "y"
    return normalized


def _retrieval_failure_response(error, status_code):
    return jsonify({
        "error": str(error),
        "workflow": {
            "source_retrieval": "FAILED",
            "evidence_search": "NOT_RUN",
            "report": "NOT_READY",
            "verification": "NOT_RUN",
        },
    }), status_code


@app.route("/")
def home():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/style.css")
def stylesheet():
    return send_from_directory(FRONTEND_DIR, "style.css")


@app.route("/script.js")
def javascript():
    response = send_from_directory(FRONTEND_DIR, "script.js", max_age=0)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/health")
def health():
    return jsonify({
        "status": "healthy",
        "project": "RepoBridge AI"
    })


@app.errorhandler(413)
def request_too_large(_error):
    # Keep the HTTP status at 413 while returning a clear JSON error message.
    return jsonify({
        "error": "Request body is too large.",
        "message": "Reduce the request size and try again.",
    }), 413


def find_matching_evidence(files, bug_description, max_matches=10):
    """Rank source lines supported by meaningful terms in their nearby context."""
    description_words = re.findall(
        r"[A-Za-z][A-Za-z0-9_]{2,}|(?<!\w)\d{3}(?!\w)",
        bug_description,
    )
    keywords = {
        _normalize_match_word(word)
        for word in description_words
        if word.lower() not in ISSUE_STOP_WORDS
        and word.lower() not in NON_DIAGNOSTIC_WORDS
        and _normalize_match_word(word) not in NON_DIAGNOSTIC_WORDS
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
    technical_terms = {
        _normalize_match_word(word)
        for word in re.findall(
            r"\b[A-Z][A-Z0-9_]{2,}\b|"
            r"\b[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]*)+\b|"
            r"(?<!\w)\d{3}(?!\w)",
            bug_description,
        )
    }
    ranked_matches = []
    single_keyword_match_count = 0
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

        normalized_source_lines = [
            " ".join(
                _normalize_match_word(word)
                for word in re.findall(
                    r"[A-Za-z][A-Za-z0-9_]{2,}|(?<!\w)\d{3}(?!\w)",
                    line,
                )
            )
            for line in source_lines
        ]
        line_keywords = [
            {
                word
                for word, pattern in keyword_patterns.items()
                if pattern.search(normalized_line)
            }
            for normalized_line in normalized_source_lines
        ]
        qualified_lines = {}
        for line_index, matching_keywords in enumerate(line_keywords):
            if not matching_keywords:
                continue
            window_start = max(0, line_index - 2)
            window_end = min(len(source_lines), line_index + 3)
            window_keywords = set().union(*line_keywords[window_start:window_end])
            has_same_line_evidence = (
                len(matching_keywords) >= MIN_EVIDENCE_KEYWORDS_PER_LINE
            )
            has_technical_nearby_evidence = (
                len(window_keywords) >= MIN_EVIDENCE_KEYWORDS_PER_LINE
                and bool(window_keywords & technical_terms)
            )
            if not has_same_line_evidence and not has_technical_nearby_evidence:
                single_keyword_match_count += 1
                continue

            qualified_keywords = (
                matching_keywords if has_same_line_evidence else window_keywords
            )
            qualified_lines[line_index] = qualified_keywords

        for line_index, matching_keywords in qualified_lines.items():
            line_number = line_index + 1
            line = source_lines[line_index]
            # Keep long source lines readable in the API response.
            excerpt = line.strip()
            if len(excerpt) > 240:
                excerpt = excerpt[:237] + "..."
            own_keywords = sorted(line_keywords[line_index])
            evidence_text = "{}:{}: [match: {}; nearby terms: {}] {}".format(
                path,
                line_number,
                ", ".join(own_keywords),
                ", ".join(sorted(matching_keywords)),
                excerpt or "(blank matching line)",
            )
            # Application files rank before tests; multi-keyword lines rank higher.
            ranked_matches.append(
                (
                    is_test_file,
                    -(len(own_keywords) * 3 + len(matching_keywords)),
                    path.lower(),
                    line_number,
                    evidence_text,
                    path,
                    matching_keywords,
                    source_lines,
                    line_keywords,
                    qualified_lines,
                )
            )

    if not ranked_matches:
        if single_keyword_match_count:
            return [
                "No useful evidence found: source lines did not contain enough distinct terms "
                "together or within nearby lines linked by a technical identifier or status code."
            ], 0, None
        return [
            "No useful evidence found: none of the meaningful bug-description keywords matched the retrieved source lines."
        ], 0, None

    ranked_matches.sort(key=lambda match: match[:4])
    total_matches = len(ranked_matches)
    evidence = []
    displayed_matches = ranked_matches[:max_matches]
    display_lines_by_path = {}
    matching_lines_by_path = {}
    source_lines_by_path = {}
    for match in displayed_matches:
        path = match[5]
        source_lines = match[7]
        context_start = max(1, match[3] - 2)
        context_end = min(len(source_lines), match[3] + 2)
        display_lines_by_path.setdefault(path, set()).update(
            range(context_start, context_end + 1)
        )
        matching_lines_by_path.setdefault(path, set()).add(match[3])
        source_lines_by_path[path] = (source_lines, match[8], match[9])

    for path in sorted(display_lines_by_path, key=str.lower):
        source_lines, line_keywords, qualified_lines = source_lines_by_path[path]
        matching_line_numbers = sorted(matching_lines_by_path[path])
        evidence.append(
            "Evidence in {} (matching source line(s): {}; context is not counted as evidence):".format(
                path, ", ".join(str(number) for number in matching_line_numbers)
            )
        )
        for context_line_number in sorted(display_lines_by_path[path]):
            context_text = source_lines[context_line_number - 1].strip()
            if len(context_text) > 240:
                context_text = context_text[:237] + "..."
            context_index = context_line_number - 1
            line_matches = qualified_lines.get(context_index)
            if line_matches:
                line_label = "[match: {}; nearby terms: {}]".format(
                    ", ".join(sorted(line_keywords[context_index])),
                    ", ".join(sorted(line_matches)),
                )
            else:
                line_label = "[context, not counted as evidence]"
            evidence.append(
                "{}:{}: {} {}".format(
                    path,
                    context_line_number,
                    line_label,
                    context_text or "(blank line)",
                )
            )

    strongest_match = ranked_matches[0]
    context_lines = strongest_match[7]
    context_start = max(1, strongest_match[3] - 2)
    context_end = min(len(context_lines), strongest_match[3] + 2)
    if total_matches > max_matches:
        evidence.append(
            "Showing the {} strongest matches out of {} useful matching lines.".format(
                max_matches, total_matches
            )
        )
    best_details = {
        "path": strongest_match[5],
        "line": strongest_match[3],
        "keywords": sorted(strongest_match[6]),
        "line_keywords": sorted(strongest_match[8][strongest_match[3] - 1]),
        "source_line": context_lines[strongest_match[3] - 1],
        "context_start": context_start,
        "context_end": context_end,
        "matching_lines": [
            line_number + 1
            for line_number in sorted(strongest_match[9])
        ],
        "matching_source_lines": [
            {
                "line": line_number + 1,
                "content": context_lines[line_number],
                "keywords": sorted(strongest_match[9][line_number]),
            }
            for line_number in sorted(strongest_match[9])
        ],
        "context_source_lines": [
            {
                "line": line_number,
                "content": context_lines[line_number - 1],
            }
            for line_number in range(context_start, context_end + 1)
        ],
    }
    return evidence, total_matches, best_details


def _request_size_evidence(strongest_match):
    """Find supported request-size evidence in matches and nearby source context."""
    if strongest_match is None:
        return None

    request_size_terms = {
        "content_length",
        "max_content_length",
        "request body",
        "413",
        "requestentitytoolarge",
    }
    if not request_size_terms.intersection(strongest_match["keywords"]):
        return None

    source_lines = {
        line["line"]: line
        for line in (
            strongest_match["context_source_lines"]
            + strongest_match["matching_source_lines"]
        )
    }
    source_lines = [source_lines[number] for number in sorted(source_lines)]
    source_context = " ".join(line["content"] for line in source_lines)
    comparison_pattern = re.compile(
        r"(?P<left>[^<>!=]{0,200})(?P<operator>>=|>|<=|<)(?P<right>[^<>!=]{0,200})",
        re.IGNORECASE,
    )
    has_request_size_comparison = any(
        (
            comparison.group("operator") in {">", ">="}
            and re.search(r"\bcontent_length\b", comparison.group("left"), re.IGNORECASE)
            and re.search(r"\bmax_content_length\b", comparison.group("right"), re.IGNORECASE)
        )
        or (
            comparison.group("operator") in {"<", "<="}
            and re.search(r"\bmax_content_length\b", comparison.group("left"), re.IGNORECASE)
            and re.search(r"\bcontent_length\b", comparison.group("right"), re.IGNORECASE)
        )
        for comparison in comparison_pattern.finditer(source_context)
    )
    if has_request_size_comparison:
        comparison_source_line = next(
            (
                line["line"]
                for line in source_lines
                if (
                    re.search(r"\bcontent_length\b", line["content"], re.IGNORECASE)
                    and re.search(r"(?:>=|>|<=|<)", line["content"])
                )
            ),
            next(
                line["line"]
                for line in source_lines
                if re.search(r"(?:>=|>|<=|<)", line["content"])
            ),
        )
        return {
            "line": comparison_source_line,
            "kind": "comparison",
        }

    normalized_context = source_context.lower().replace("_", " ")
    documents_limit_behavior = (
        re.search(r"\bmax(?:imum)?[\s-]+content[\s-]+length\b", normalized_context)
        and re.search(
            r"\b(exceed\w*|larger than|greater than|above|over the limit|too large)\b",
            normalized_context,
        )
        and re.search(r"\b(413|requestentitytoolarge)\b", normalized_context)
    )
    if not documents_limit_behavior:
        return None

    documentation_lines = [
        line for line in source_lines
        if re.search(
            r"\b(413|requestentitytoolarge|exceed\w*|larger than|greater than)\b",
            line["content"],
            re.IGNORECASE,
        )
    ]
    if not documentation_lines:
        return None
    return {
        "line": documentation_lines[0]["line"],
        "kind": "documentation",
    }


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
    try:
        parsed_url = urlsplit(repo_url.strip())
    except ValueError:
        return jsonify({
            "error": "repository_url must look like https://github.com/owner/repository."
        }), 400
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
    retrieval_started_at = time.perf_counter()
    try:
        retrieval = get_repository_sources(repo_url, bug_description)
    except RepositoryNotFoundError as error:
        return _retrieval_failure_response(error, 404)
    except RepositoryAccessError as error:
        return _retrieval_failure_response(error, 403)
    except GitHubRateLimitError as error:
        return _retrieval_failure_response(error, 429)
    except GitHubNetworkError as error:
        return _retrieval_failure_response(error, 503)
    except UnsupportedRepositoryError as error:
        return _retrieval_failure_response(error, 400)
    except GitHubRetrievalError as error:
        return _retrieval_failure_response(error, 502)
    finally:
        _log_stage_duration("repository_fetch", retrieval_started_at)

    # Search only the source text already retrieved; never execute it.
    matching_started_at = time.perf_counter()
    try:
        matching_evidence, matching_line_count, strongest_match = find_matching_evidence(
            retrieval["files"], bug_description
        )
    finally:
        _log_stage_duration("source_matching", matching_started_at)
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
    evidence.extend(dict.fromkeys(matching_evidence))

    suggested_tests = []
    request_size_evidence = _request_size_evidence(strongest_match)
    has_request_size_branch = request_size_evidence is not None
    if has_request_size_branch:
        suggested_tests.append({
            "title": "Check behavior at the configured request-size limit",
            "purpose": (
                "Check whether the reported response is associated with the configured "
                "request-size limit."
            ),
            "setup": (
                "Find the configured MAX_CONTENT_LENGTH value and prepare request bodies "
                "just below, at, and above that limit."
            ),
            "action": (
                "Send each request through the route that contains the request-size check."
                if request_size_evidence["kind"] == "comparison"
                else "Check the request-handling behavior described by this source documentation."
            ),
            "expected_result": (
                "Record the actual response for each size and compare its status and body "
                "with the documented or expected behavior; treat any undocumented "
                "expectation as an assumption to verify."
            ),
            "what_to_check": (
                "Record the response status and body for each size, then compare them "
                "with the retrieved source at {}:{}."
            ).format(strongest_match["path"], request_size_evidence["line"]),
            "evidence_basis": {
                "path": strongest_match["path"],
                "line": request_size_evidence["line"],
            },
        })

    if strongest_match is not None:
        root_cause = (
            "Possible lead (not a confirmed root cause): {}:{} matches reported terms {}. "
            "Reproduce the behavior before drawing a conclusion.".format(
                strongest_match["path"],
                strongest_match["line"],
                ", ".join(strongest_match["keywords"]),
            )
        )
        suggested_fix = (
            "Review {}:{} and reproduce the issue before changing code. "
            "No specific code change is confirmed by this search.".format(
                strongest_match["path"], strongest_match["line"]
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

    if strongest_match is None:
        beginner_explanation = {
            "what_code_does": (
                "No specific code line in the retrieved files matched the report strongly enough to explain it."
            ),
            "what_might_be_wrong": (
                "There is not enough matching evidence to suggest a cause."
            ),
            "why_evidence_matters": (
                "Without a relevant source line, suggesting a cause would be a guess."
            ),
            "what_to_check_next": [
                "Describe the exact action that fails and what you expected to happen.",
                "Include any error message, endpoint, or status code.",
                "Look for source files related to that behavior.",
            ],
            "technical_terms": [
                {
                    "term": "Evidence",
                    "meaning": "A clue from retrieved code. It can guide a check, but it is not proof.",
                },
                {
                    "term": "Root cause",
                    "meaning": "The underlying reason a problem happens.",
                },
            ],
        }
    elif has_request_size_branch:
        beginner_explanation = {
            "what_code_does": (
                (
                    "The retrieved code compares request body length with MAX_CONTENT_LENGTH."
                    if request_size_evidence["kind"] == "comparison"
                    else "The retrieved documentation says an oversized request can raise HTTP 413."
                )
            ),
            "what_might_be_wrong": (
                (
                    "The retrieved code at {}:{} compares request length with "
                    "MAX_CONTENT_LENGTH, so it may be relevant if the report concerns "
                    "oversized requests. This source match does not show the runtime "
                    "response or confirm a root cause."
                    if request_size_evidence["kind"] == "comparison"
                    else "The retrieved documentation at {}:{} describes HTTP 413 "
                    "for oversized requests. That is documented behavior, not proof "
                    "that this caused the reported issue."
                ).format(strongest_match["path"], request_size_evidence["line"])
            ),
            "why_evidence_matters": (
                "The relevant {} is at {}:{}; nearby lines provide context.".format(
                    "code" if request_size_evidence["kind"] == "comparison" else "documentation",
                    strongest_match["path"], request_size_evidence["line"]
                )
            ),
            "what_to_check_next": [
                "Confirm the configured MAX_CONTENT_LENGTH value.",
                "Compare the observed response with the endpoint's documented behavior.",
                "Check requests just below, at, and above the configured limit.",
            ],
            "technical_terms": [
                {
                    "term": "MAX_CONTENT_LENGTH",
                    "meaning": "A setting that limits the size of request data.",
                },
            ],
        }
    else:
        nearby_keywords = sorted(
            set(strongest_match["keywords"]) - set(strongest_match["line_keywords"])
        )
        beginner_explanation = {
            "what_code_does": (
                "The matched line contains {}.{} It cannot explain the full behavior by itself; "
                "read it with the nearby code."
            ).format(
                ", ".join(strongest_match["line_keywords"]),
                (
                    " Related terms appear on nearby lines: {}.".format(
                        ", ".join(nearby_keywords)
                    )
                    if nearby_keywords
                    else ""
                ),
            ),
            "what_might_be_wrong": (
                "The retrieved source line at {}:{} reads: \"{}\". It contains the "
                "reported term(s) {}, making it a concrete location to inspect. "
                "A text match alone does not show runtime behavior or confirm a root cause."
            ).format(
                strongest_match["path"],
                strongest_match["line"],
                strongest_match["source_line"].strip(),
                ", ".join(strongest_match["line_keywords"]),
            ),
            "why_evidence_matters": (
                "This matching line is in {}:{}; the retrieved text is a clue to inspect, "
                "not proof that the reported behavior occurs there."
            ).format(strongest_match["path"], strongest_match["line"]),
            "what_to_check_next": [
                "Read the matched line and nearby code.",
                "Reproduce the reported behavior and record what happens.",
                "Compare that result with what you expected to happen.",
            ],
            "technical_terms": [
                {
                    "term": "Keyword match",
                    "meaning": "The search found a report word in retrieved code.",
                },
                {
                    "term": "Source line",
                    "meaning": "A numbered line in a code file.",
                },
            ],
        }

    missing_information = [
        "If not already clear, note the exact steps that reproduce the problem.",
        "State what you expected and what happened instead.",
        "Include an error message, endpoint, or status code if one applies.",
    ]
    if description_is_broad:
        missing_information = [
            "What exact steps did you take, and what did you expect to happen?",
            "What happened instead? Include any error message or stack trace shown.",
            "Name the affected page, feature, endpoint, or function if you know it.",
        ]
    if (
        strongest_match is not None
        and not has_request_size_branch
        and {"content_length", "max_content_length", "request body", "413"}
        .intersection(strongest_match["keywords"])
    ):
        missing_information = [
            "The retrieved lines do not show both the configured size limit and what happens when a request exceeds it.",
            "Provide the configured limit and the observed response for an oversized request.",
        ]

    investigation_guide = {
        "missing_information": missing_information,
        "steps": [
            {
                "title": "Write down the problem",
                "instruction": (
                    "Record the action you took, what you expected, and what happened instead. "
                    "Include an error message or status code if you saw one."
                ),
            },
            {
                "title": "Try to reproduce it",
                "instruction": (
                    "Repeat the same steps and note whether the same result happens. "
                    "Record the exact result."
                ),
            },
            {
                "title": "Inspect the available code",
                "instruction": (
                    "No relevant source line was found in the retrieved files. "
                    "Use the missing details to locate or retrieve the code involved."
                ),
            },
        ],
    }
    if strongest_match is not None:
        investigation_guide["steps"][2]["instruction"] = (
            "Review the matching lines and clearly labeled nearby context in Evidence. "
            "Nearby context is not separate proof."
        )
    if has_request_size_branch:
        investigation_guide["steps"][1]["instruction"] = (
            "Compare requests just below, at, and above MAX_CONTENT_LENGTH. "
            "Record the response and check it against the endpoint's documented behavior."
        )

    matching_snippets = [
        item
        for item in matching_evidence
        if re.match(r"^[^:\r\n]+:\d+: \[match(?:[:\]])", item)
    ]
    ai_result = explain_bug(bug_description, matching_snippets)
    gemini_used = False
    if (
        isinstance(ai_result, ExplanationResult)
        and ai_result.available
        and isinstance(ai_result.explanation, str)
        and ai_result.explanation.strip()
    ):
        gemini_used = True
        beginner_explanation["what_might_be_wrong"] = ai_result.explanation.strip()

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
        "gemini_used": gemini_used,
        "workflow": {
            "source_retrieval": "SUCCEEDED",
            "evidence_search": (
                "LEAD_FOUND" if matching_line_count else "NO_MATCH"
            ),
            "report": "READY_FOR_REVIEW",
            "verification": "NOT_RUN",
        },
        "evidence": evidence,
        "suggested_fix": suggested_fix,
        "suggested_tests": suggested_tests,
        "beginner_explanation": beginner_explanation,
        "investigation_guide": investigation_guide,
        "tests": {
            "status": "NOT_RUN",
            "details": "No repository tests were run."
        }
    })


if __name__ == "__main__":
    app.run(debug=True)

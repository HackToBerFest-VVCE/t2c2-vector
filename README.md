# RepoBridge AI

**Evidence-driven bug investigation for public GitHub repositories.**

RepoBridge AI is a hackathon prototype that helps a developer move from a bug report to a small, reviewable set of source-code clues. It retrieves a few likely source files, compares their lines with the reported symptoms, and presents a beginner-friendly investigation report.

> **Important:** RepoBridge AI does not run the target application or its tests. A report is an investigation aid, not a confirmed diagnosis or verified fix.

## The problem

Bug reports often arrive without enough detail to reproduce the issue. At the same time, searching a large repository manually can be slow, and a coincidental word match can send someone toward unrelated code.

RepoBridge AI is designed to make that first investigation step easier: it asks for a repository and a description of the unexpected behavior, then shows the retrieved code locations that appear relevant. If the description is too vague or no useful source evidence is found, it says so instead of presenting a keyword coincidence as a diagnosis.

## Our approach

The prototype uses a deliberately bounded, read-only workflow:

1. Validate the repository URL and bug description.
2. Use the GitHub API to inspect public repository metadata and its file tree.
3. Select up to three small, supported source files, ranking paths by overlap with useful words in the bug description and preferring application files over tests.
4. Compare the description with retrieved source lines. A useful line must contain at least two distinct diagnostic terms; generic words alone are not enough.
5. Show matching file paths, line numbers, and nearby code as clues for a person to review.
6. Return a cautious explanation, investigation steps, and—only when retrieved code compares request size with its configured limit—a possible test scenario.

This approach is intentionally not an autonomous code runner. Repository contents are retrieved as text; the application does not import or execute them.

## Features

- **Public GitHub repository lookup** with clear errors for inaccessible or missing repositories, rate limits, network failures, and unsupported responses.
- **Small, ranked source selection** across supported source-code extensions. The current retrieval defaults are at most three files and 100,000 bytes per file.
- **Evidence with context** including the source path, line number, and nearby lines when a useful match exists.
- **Vague and irrelevant report handling.** Broad descriptions are identified; if no useful source match exists, the report says evidence is insufficient rather than guessing.
- **A beginner-friendly report** organized into a simple summary, code evidence, why it may matter, missing information, investigation steps, suggested tests, and actual test status.
- **Suggested Tests, separate from executed tests.** Supported suggestions include a title, setup, action, expected result, and evidence location.
- **Explicit test status.** The report keeps `tests.status` as `NOT_RUN` and says repository tests were not run.
- **Safe text rendering** in the browser using DOM text nodes and `textContent` rather than inserting repository- or user-provided strings as raw HTML.

## Setup

### Requirements

- Python installed on your machine.
- Internet access to reach the GitHub API when analyzing a repository.

The repository does not currently include a Python dependency manifest. The backend imports Flask; install it in a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install Flask
```

### Start the app

From the repository root:

```powershell
.\.venv\Scripts\python.exe backend\app.py
```

Open `http://127.0.0.1:5000` in your browser. The backend serves the frontend and provides `POST /api/analyze`.

The current entrypoint starts Flask with debug mode enabled. This is intended for local development and should not be exposed as a production server.

## Usage

1. Enter a public GitHub repository URL, for example `https://github.com/pallets/flask`.
2. Describe the observed behavior with concrete details: what action you took, what happened, and what you expected.
3. Include useful clues when available, such as an endpoint, function or setting name, exact error text, or HTTP status code.
4. Select **Analyze Bug**.
5. Review the evidence and nearby code yourself. Treat the explanation and any suggested test as a lead to investigate, not as proof.

Example of a more useful report:

> “Requests larger than the configured `MAX_CONTENT_LENGTH` receive HTTP 413, but the endpoint is expected to return a JSON error response.”

A report such as “The application is not working” is too broad to search meaningfully. RepoBridge AI should ask for more detail rather than treating unrelated source lines as evidence.

### API request

```json
{
  "repository_url": "https://github.com/owner/repository",
  "bug_description": "Describe the unexpected behavior and relevant technical details."
}
```

The successful response includes an investigation report, `suggested_tests`, and a `tests` object. The `tests` object remains:

```json
{
  "status": "NOT_RUN",
  "details": "No repository tests were run."
}
```

`suggested_tests` contains ideas for a developer to run manually; it does not mean a test was executed.

## Run the backend tests

From the repository root:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s backend -p "test*.py" -v
```

The automated tests cover evidence matching, API input validation and response behavior, retrieval error handling, and GitHub retrieval using mocked HTTP responses. They test RepoBridge AI itself; they do **not** run the tests of repositories supplied to the app.

## Limitations

- Only public GitHub repositories are supported; private repository authentication is not implemented.
- The retrieval is intentionally limited to a few small source files. Relevant code can be missed if it is in an unselected file, an unsupported file type, or beyond the configured size limits.
- Relevance is estimated using keyword and path matching, not deep semantic analysis. A useful match is still only a clue and may be unrelated.
- The application does not clone repositories, install their dependencies, execute their code, reproduce bugs, or run their test suites.
- Suggested test scenarios are currently narrow and generated only when matching retrieved code compares request content length with its configured maximum. They are not discovered existing tests or commands.
- GitHub API availability and unauthenticated rate limits can affect retrieval.
- This is a hackathon prototype, not a production-grade diagnostic or security review tool.

## Future improvements

- Improve ranking with richer repository structure and code relationships while retaining explainable evidence.
- Let users review and select files before analysis, and show when retrieval limits may have excluded relevant code.
- Generate more kinds of evidence-grounded test scenarios, with explicit uncertainty and no invented test files or commands.
- Add safe, isolated verification only after designing a strict execution boundary for untrusted code; until then, keep repository execution disabled.
- Add configurable GitHub authentication for permitted private-repository workflows and clearer quota handling.
- Add broader frontend and API integration coverage, accessibility checks, and deployment guidance.

## How GitHub Copilot helped

Copilot was used as a development assistant in VS Code. These examples reflect work and test results recorded during this project; they do not imply Copilot independently verified a bug in an analyzed repository.

- **Implementation:** Copilot helped add evidence-based investigation output, including structured `suggested_tests` fields, plain-language explanations, and step-by-step guidance. Suggested scenarios were kept separate from the actual test status.
- **Debugging:** When the Analyze button appeared unresponsive, Copilot inspected the frontend and found merge-conflict markers and duplicate request code in `frontend/script.js` that prevented valid JavaScript parsing. It also reproduced a malformed repository URL causing `urlsplit()` to raise an exception, then added focused handling and a regression test.
- **Testing:** Copilot added backend tests for vague and irrelevant descriptions, evidence matches, missing inputs, malformed URLs, and mapped GitHub retrieval errors. Automated tests use mocked GitHub responses; no downloaded repository code is run by the test suite.
- **Documentation:** Copilot reviewed the actual code and tests while preparing this README, so setup steps, feature descriptions, limitations, and contribution examples are tied to observed project behavior rather than assumed capabilities.

Copilot suggestions were reviewed against the project code and automated tests. A passing RepoBridge AI test suite does not confirm that any reported root cause or proposed fix in a target repository is correct.

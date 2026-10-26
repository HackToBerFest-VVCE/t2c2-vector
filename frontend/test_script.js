const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "script.js"), "utf8");
const decoder = source.match(
    /function decodeReportText\(value\) \{[\s\S]*?\n\}/
);

function decode(value) {
    assert.ok(decoder, "decodeReportText must be present in script.js");
    const sandbox = {};
    vm.runInNewContext(
        `${decoder[0]}; result = decodeReportText(${JSON.stringify(value)})`,
        sandbox
    );
    return sandbox.result;
}

function displayProse(value) {
    const displayFormatter = source.match(
        /function displayReportText\(value\) \{[\s\S]*?\n\}/
    );
    assert.ok(displayFormatter, "displayReportText must be present in script.js");
    const sandbox = {};
    vm.runInNewContext(
        `${decoder[0]}\n${displayFormatter[0]}; result = displayReportText(${JSON.stringify(value)})`,
        sandbox
    );
    return sandbox.result;
}

function createFrontendHarness({
    repositoryUrl = "https://github.com/example/repository",
    bugDescription = "payment timeout",
    fetchImplementation = async () => ({ok: true, json: async () => ({})}),
} = {}) {
    class FakeNode {
        constructor(tagName) {
            this.tagName = tagName;
            this.children = [];
            this._text = "";
            this.listeners = {};
            this.disabled = false;
            this.value = "";
        }

        set textContent(value) {
            this._text = String(value);
            this.children = [];
        }

        get textContent() {
            return this._text + this.children.map((child) => child.textContent).join("");
        }

        append(...nodes) {
            this.children.push(...nodes);
        }

        replaceChildren(...nodes) {
            this._text = "";
            this.children = [...nodes];
        }

        addEventListener(eventName, callback) {
            this.listeners[eventName] = callback;
        }
    }

    const elements = new Map(
        [
            "repo",
            "issue",
            "analyzeBtn",
            "status",
            "results",
            "workflow-retrieval",
            "workflow-evidence",
            "workflow-report",
            "workflow-verification",
        ].map((id) => [
            id,
            new FakeNode(id),
        ])
    );
    elements.get("repo").value = repositoryUrl;
    elements.get("issue").value = bugDescription;

    const document = {
        getElementById: (id) => elements.get(id),
        createElement: (tagName) => new FakeNode(tagName),
        createTextNode: (text) => {
            const node = new FakeNode("#text");
            node.textContent = text;
            return node;
        },
    };
    const fetchCalls = [];
    const sandbox = {
        document,
        URL,
        fetch: (...args) => {
            fetchCalls.push(args);
            return fetchImplementation(...args);
        },
    };
    vm.runInNewContext(source, sandbox);
    return {elements, fetchCalls};
}

test("decodes direct and escaped numeric HTML entities", () => {
    assert.equal(decode("&#x20;"), " ");
    assert.equal(decode("&#x52;"), "R");
    assert.equal(decode("&amp;#x20;"), " ");
    assert.equal(decode("&amp;amp;#x20;"), " ");
    assert.equal(decode("&lt;tag&gt; &quot;text&quot;"), '<tag> "text"');
});

test("decodes the exact entity strings shown in the report", () => {
    assert.equal(
        displayProse("Keyword match:&#x20;**&#x54;he search..."),
        "Keyword match: The search..."
    );
    assert.equal(
        displayProse("Status:&#x20;**&#x4E;OT_RUN"),
        "Status: NOT_RUN"
    );
    assert.equal(displayProse("&#x52;ecord"), "Record");
});

test("keeps invalid numeric entities as text", () => {
    assert.equal(decode("&#x110000;"), "&#x110000;");
});

test("validates required inputs and repository URLs before sending a request", async () => {
    const invalidSubmissions = [
        {
            repositoryUrl: "",
            bugDescription: "",
            expectedStatus: "Enter a GitHub repository URL.",
        },
        {
            repositoryUrl: "https://github.com/example/repository",
            bugDescription: "",
            expectedStatus: "Enter a bug description.",
        },
        ...[
            "not-a-github-url",
            "http://github.com/example/repository",
            "https://github.com/example",
            "https://github.com/example/repository/extra",
            "https://github.com//repository",
            "https://github.com/example//repository",
            "https://github.com.example.com/example/repository",
            "https://user@github.com/example/repository",
            "https://github.com/example/repository?tab=readme",
            "https://github.com/example/repository#readme",
        ].map((repositoryUrl) => ({
            repositoryUrl,
            bugDescription: "payment timeout",
            expectedStatus: "Enter a valid URL",
        })),
    ];

    for (const submission of invalidSubmissions) {
        const {elements, fetchCalls} = createFrontendHarness(submission);
        const results = elements.get("results");
        results.textContent = "Previous investigation report";

        await elements.get("analyzeBtn").listeners.click();

        assert.equal(fetchCalls.length, 0, submission.repositoryUrl);
        assert.equal(results.textContent, "", submission.repositoryUrl);
        assert.ok(
            elements.get("status").textContent.includes(submission.expectedStatus),
            submission.repositoryUrl
        );
        assert.equal(
            elements.get("workflow-retrieval").textContent,
            "Not started"
        );
        assert.equal(elements.get("workflow-verification").textContent, "NOT RUN");
        assert.equal(elements.get("analyzeBtn").disabled, false);
    }
});

test("form guidance asks for useful details and gives a concise example", () => {
    const html = fs.readFileSync(path.join(__dirname, "index.html"), "utf8");

    assert.ok(html.includes("what action you took"));
    assert.ok(html.includes("what you expected"));
    assert.ok(html.includes("what happened instead"));
    assert.ok(html.includes("error message or stack trace"));
    assert.ok(html.includes("profile page stays blank"));
});

test("failed and incomplete analyses clear old reports and show readable errors", async () => {
    const failures = [
        {
            fetchImplementation: async () => {
                throw new Error("offline");
            },
            expectedStatus: "Unable to reach the backend",
            expectedRetrieval: "Unavailable",
        },
        {
            fetchImplementation: async () => ({
                ok: false,
                status: 404,
                json: async () => ({
                    error: "Repository or file was not found.",
                    workflow: {
                        source_retrieval: "FAILED",
                        evidence_search: "NOT_RUN",
                        report: "NOT_READY",
                        verification: "NOT_RUN",
                    },
                }),
            }),
            expectedStatus: "HTTP 404: Repository or file was not found.",
            expectedRetrieval: "Failed",
        },
        {
            fetchImplementation: async () => ({
                ok: true,
                json: async () => {
                    throw new Error("invalid JSON");
                },
            }),
            expectedStatus: "The backend returned invalid JSON.",
            expectedRetrieval: "Unknown",
        },
        {
            fetchImplementation: async () => ({
                ok: true,
                json: async () => ({mode: "INVESTIGATION"}),
            }),
            expectedStatus: "incomplete or unexpected analysis report",
            expectedRetrieval: "Unknown",
        },
        {
            fetchImplementation: async () => ({
                ok: true,
                json: async () => ({
                    mode: "INVESTIGATION",
                    summary: "Analysis complete.",
                    evidence: [],
                    suggested_tests: [null],
                }),
            }),
            expectedStatus: "incomplete or unexpected analysis report",
            expectedRetrieval: "Unknown",
        },
    ];

    for (const failure of failures) {
        const {elements} = createFrontendHarness(failure);
        const results = elements.get("results");
        results.textContent = "Previous investigation report";

        await elements.get("analyzeBtn").listeners.click();

        assert.equal(results.textContent, "");
        assert.ok(elements.get("status").textContent.includes(failure.expectedStatus));
        assert.equal(
            elements.get("workflow-retrieval").textContent,
            failure.expectedRetrieval
        );
        assert.equal(elements.get("workflow-report").textContent, "Not ready");
        assert.equal(elements.get("workflow-verification").textContent, "NOT RUN");
        assert.equal(elements.get("analyzeBtn").disabled, false);
    }
});

test("disables duplicate submissions while an analysis is pending", async () => {
    let resolveResponse;
    const pendingResponse = new Promise((resolve) => {
        resolveResponse = resolve;
    });
    const {elements, fetchCalls} = createFrontendHarness({
        fetchImplementation: () => pendingResponse,
    });

    const firstSubmission = elements.get("analyzeBtn").listeners.click();
    assert.match(
        elements.get("status").textContent,
        /preparing the analysis report/
    );
    await elements.get("analyzeBtn").listeners.click();

    assert.equal(fetchCalls.length, 1);
    assert.equal(elements.get("analyzeBtn").disabled, true);

    resolveResponse({
        ok: true,
        json: async () => ({
            mode: "INVESTIGATION",
            workflow: {
                source_retrieval: "SUCCEEDED",
                evidence_search: "LEAD_FOUND",
                report: "READY_FOR_REVIEW",
                verification: "NOT_RUN",
            },
            summary: "No matching source lines were found.",
            evidence: [],
        }),
    });
    await firstSubmission;

    assert.equal(elements.get("analyzeBtn").disabled, false);
    assert.equal(fetchCalls.length, 1);
});

test("renders repository-derived content as text in the report", async () => {
    const hostileText = "<img src=x onerror=alert(1)>";
    const report = {
        mode: "INVESTIGATION",
        gemini_used: true,
        root_cause: "Possible lead (not a confirmed root cause): src/payment.py:12.",
        summary: "Retrieved source files and found one matching source line.",
        evidence: [
            `src/payment.py:12: [match] ${hostileText} &#x52;`,
            `src/payment.py:12: [match] ${hostileText} &#x52;`,
        ],
        beginner_explanation: {
            what_code_does: "The code handles payment.",
            what_might_be_wrong: "This clue may be related.",
            why_evidence_matters:
                "&#x20;**&#x54;he relevant source is at src/flask/wrappers.py:62; nearby lines provide context.",
            what_to_check_next: ["Reproduce the issue."],
            technical_terms: [
                {
                    term: "MAX_CONTENT_LENGTH",
                    meaning:
                        "&#x20;**&#x41; setting that limits the size of request data.",
                },
            ],
        },
        investigation_guide: {
            missing_information: ["The exact steps to reproduce the issue."],
            steps: [
                {
                    title: "Reproduce",
                    instruction:
                        "&#x20;**&#x52;ecord the action you took, what you expected, and what happened instead.",
                },
            ],
        },
        suggested_tests: [
            {
                title: "Check payment timeout behavior",
                purpose: "Check how payment requests behave during a timeout.",
                setup:
                    "&#x20;**&#x46;ind the configured request-size limit before sending the request.",
                action: "Submit the request.",
                expected_result: "Compare the observed result with expectations.",
                what_to_check: "Record the returned status and response body.",
                evidence_basis: {path: "src/payment.py", line: 12},
            },
        ],
        workflow: {
            source_retrieval: "SUCCEEDED",
            evidence_search: "LEAD_FOUND",
            report: "READY_FOR_REVIEW",
            verification: "NOT_RUN",
        },
        tests: {
            status: "&#x20;**&#x4E;OT_RUN",
            details: "No repository tests were run.",
        },
    };

    const {elements, fetchCalls} = createFrontendHarness({
        fetchImplementation: async () => ({
            ok: true,
            json: async () => report,
        }),
    });
    await elements.get("analyzeBtn").listeners.click();

    assert.equal(fetchCalls.length, 1);
    assert.equal(fetchCalls[0][0], "/api/analyze");
    assert.deepEqual(JSON.parse(fetchCalls[0][1].body), {
        repository_url: "https://github.com/example/repository",
        bug_description: "payment timeout",
    });

    const results = elements.get("results");
    const allNodes = [];
    const visit = (node) => {
        allNodes.push(node);
        node.children.forEach(visit);
    };
    visit(results);

    assert.ok(allNodes.some((node) => node.textContent.includes(hostileText)));
    assert.ok(allNodes.some((node) => node.textContent.includes("R")));
    assert.ok(
        allNodes.some((node) =>
            node.textContent.includes(
                "MAX_CONTENT_LENGTH: A setting that limits the size of request data."
            )
        )
    );
    assert.ok(
        allNodes.some((node) =>
            node.textContent.includes(
                "Why this clue matters: The relevant source is at src/flask/wrappers.py:62; nearby lines provide context."
            )
        )
    );
    assert.ok(
        allNodes.some((node) =>
            node.textContent.includes(
                "Assessment: Possible lead (not a confirmed root cause): src/payment.py:12."
            )
        )
    );
    assert.ok(
        allNodes.some((node) =>
            node.textContent.includes(
                "Steps: Find the configured request-size limit before sending the request. Submit the request."
            )
        )
    );
    assert.ok(allNodes.some((node) => node.textContent.includes("Purpose: Check how payment requests behave during a timeout.")));
    assert.ok(allNodes.some((node) => node.textContent.includes("What to check: Record the returned status and response body.")));
    assert.ok(
        allNodes.some((node) =>
            node.textContent.includes(
                "Record the action you took, what you expected, and what happened instead."
            )
        )
    );
    assert.ok(
        allNodes.some((node) => node.textContent.includes("Status: NOT_RUN"))
    );
    assert.equal(
        allNodes.some((node) => /&#x[0-9a-f]+;|&amp;#x|\*\*/i.test(node.textContent)),
        false
    );
    assert.ok(allNodes.some((node) => node.textContent.includes("Simple summary")));
    assert.ok(allNodes.some((node) => node.textContent.includes("Analysis")));
    assert.ok(allNodes.some((node) => node.textContent.includes("Generated by Gemini")));
    assert.equal(
        allNodes.some((node) => node.textContent.includes("Suggested debugging steps")),
        false
    );
    assert.ok(
        allNodes.some((node) => node.textContent.includes("Investigation guide"))
    );
    assert.ok(
        allNodes.some((node) =>
            node.textContent.includes("Reproduce: Record the action you took")
        )
    );
    assert.ok(allNodes.some((node) => node.textContent.includes("Source Code Evidence")));
    assert.ok(allNodes.some((node) => node.textContent.includes("Suggested tests")));
    assert.ok(allNodes.some((node) => node.textContent.includes("Test execution status")));
    assert.ok(allNodes.some((node) => node.textContent.includes("NOT_RUN")));
    assert.equal(elements.get("workflow-retrieval").textContent, "Succeeded");
    assert.equal(elements.get("workflow-evidence").textContent, "Possible lead found");
    assert.equal(elements.get("workflow-report").textContent, "Ready for review");
    assert.equal(elements.get("workflow-verification").textContent, "NOT RUN");
    assert.equal(allNodes.some((node) => node.tagName === "img"), false);
    assert.equal(/\.innerHTML\s*=/.test(source), false);
    assert.deepEqual(
        allNodes
            .filter((node) => node.tagName === "h3")
            .map((node) => node.textContent),
        [
            "Simple summary",
            "Analysis",
            "Source Code Evidence",
            "Suggested tests",
            "Test execution status",
        ]
    );

    const evidenceItems = allNodes.filter(
        (node) =>
            node.tagName === "li" &&
            node.textContent.includes("src/payment.py:12:")
    );
    assert.equal(evidenceItems.length, 1);
});

test("labels a deterministic explanation as fallback, not Gemini output", async () => {
    const report = {
        mode: "INVESTIGATION",
        gemini_used: false,
        workflow: {
            source_retrieval: "SUCCEEDED",
            evidence_search: "NO_MATCH",
            report: "READY_FOR_REVIEW",
            verification: "NOT_RUN",
        },
        summary: "No matching source lines were found.",
        evidence: ["src/payment.py:12: [context, not counted as evidence] timeout handler"],
        beginner_explanation: {
            what_might_be_wrong:
                "The retrieved line at src/payment.py:12 reads: timeout handler. It matched reported terms, but that match does not prove it caused the issue.",
            what_to_check_next: ["Reproduce the issue and record the result."],
        },
        investigation_guide: {
            steps: [
                {
                    title: "Reproduce",
                    instruction: "Reproduce the issue and record the result.",
                },
            ],
        },
        suggested_tests: [],
        tests: {
            status: "NOT_RUN",
            details: "No repository tests were run.",
        },
    };
    const {elements} = createFrontendHarness({
        fetchImplementation: async () => ({
            ok: true,
            json: async () => report,
        }),
    });

    await elements.get("analyzeBtn").listeners.click();

    const allNodes = [];
    const visit = (node) => {
        allNodes.push(node);
        node.children.forEach(visit);
    };
    visit(elements.get("results"));

    assert.ok(
        allNodes.some((node) =>
            node.textContent.includes("Rule-based fallback — Gemini not used")
        )
    );
    assert.equal(
        allNodes.some((node) => node.textContent.includes("Generated by Gemini")),
        false
    );
    assert.ok(
        allNodes.some((node) =>
            node.textContent.includes("Reproduce the issue and record the result.")
        )
    );
    assert.ok(allNodes.some((node) => node.textContent.includes("Status: NOT_RUN")));
    assert.equal(elements.get("workflow-evidence").textContent, "No matching evidence");
    assert.equal(elements.get("workflow-report").textContent, "Ready for review");
});

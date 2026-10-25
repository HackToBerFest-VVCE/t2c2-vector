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

test("renders repository-derived content as text in the report", async () => {
    class FakeNode {
        constructor(tagName) {
            this.tagName = tagName;
            this.children = [];
            this._text = "";
            this.listeners = {};
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
        ["repo", "issue", "analyzeBtn", "status", "results"].map((id) => [
            id,
            new FakeNode(id),
        ])
    );
    elements.get("repo").value = "https://github.com/example/repository";
    elements.get("issue").value = "payment timeout";

    const document = {
        getElementById: (id) => elements.get(id),
        createElement: (tagName) => new FakeNode(tagName),
        createTextNode: (text) => {
            const node = new FakeNode("#text");
            node.textContent = text;
            return node;
        },
    };
    const hostileText = "<img src=x onerror=alert(1)>";
    const report = {
        mode: "INVESTIGATION",
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
                setup:
                    "&#x20;**&#x46;ind the configured request-size limit before sending the request.",
                action: "Submit the request.",
                expected_result: "Compare the observed result with expectations.",
                evidence_basis: {path: "src/payment.py", line: 12},
            },
        ],
        tests: {
            status: "&#x20;**&#x4E;OT_RUN",
            details: "No repository tests were run.",
        },
    };

    const sandbox = {
        document,
        URL,
        fetch: async () => ({
            ok: true,
            json: async () => report,
        }),
    };
    vm.runInNewContext(source, sandbox);
    await elements.get("analyzeBtn").listeners.click();

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
                "Setup: Find the configured request-size limit before sending the request."
            )
        )
    );
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
    assert.ok(allNodes.some((node) => node.textContent.includes("Test execution status")));
    assert.ok(allNodes.some((node) => node.textContent.includes("NOT_RUN")));
    assert.equal(allNodes.some((node) => node.tagName === "img"), false);
    assert.equal(/\.innerHTML\s*=/.test(source), false);
    assert.deepEqual(
        allNodes
            .filter((node) => node.tagName === "h3")
            .map((node) => node.textContent),
        [
            "Simple summary",
            "What the code shows",
            "Why it may matter",
            "What information is missing",
            "Suggested investigation steps",
            "Evidence and nearby context",
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

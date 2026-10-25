
function decodeReportText(value) {
    if (value === null || value === undefined) {
        return "Not provided.";
    }

    let text = typeof value === "string" ? value : String(value);
    const namedEntities = {
        lt: "<",
        gt: ">",
        quot: '"',
        apos: "'",
        nbsp: "\u00a0",
    };
    const numericEntityPattern =
        /&#(?:x([0-9a-f]{1,6})|(\d{1,7}));/gi;
    for (let pass = 0; pass < 8; pass += 1) {
        const decodedAmpersands = text.replace(/&amp;/gi, "&");
        const decodedNamedEntities = decodedAmpersands.replace(
            /&(lt|gt|quot|apos|nbsp);/gi,
            (entity, name) => namedEntities[name.toLowerCase()] || entity
        );
        const decodedEntities = decodedNamedEntities.replace(
            numericEntityPattern,
            (entity, hexadecimal, decimal) => {
                const codePoint = Number.parseInt(
                    hexadecimal || decimal,
                    hexadecimal ? 16 : 10
                );
                if (
                    codePoint > 0x10ffff ||
                    (codePoint >= 0xd800 && codePoint <= 0xdfff)
                ) {
                    return entity;
                }
                return String.fromCodePoint(codePoint);
            }
        );
        if (decodedEntities === text) {
            break;
        }
        text = decodedEntities;
    }
    return text;
}

function displayReportText(value) {
    return decodeReportText(value).replace(/\*\*/g, "").trim();
}

// Get references to the form elements so JavaScript can interact with them.
const repoInput = document.getElementById("repo");
const issueInput = document.getElementById("issue");
const analyzeBtn = document.getElementById("analyzeBtn");
const status = document.getElementById("status");
const results = document.getElementById("results");

// Run this function whenever the user clicks the Analyze Bug button.
analyzeBtn.addEventListener("click", async () => {
    // Read the user's inputs and remove unnecessary spaces.
    const repo = repoInput.value.trim();
    const issue = issueInput.value.trim();

    // Make sure the user has filled in both required fields.
    if (!repo || !issue) {
        status.textContent =
            "Please enter the repository URL and bug description.";
        return; // Stop processing when required input is missing.
    }

    // Check that the URL points to a GitHub repository.
    try {
        const url = new URL(repo);
        const parts = url.pathname.split("/").filter(Boolean);

        // A repository URL must contain an owner and repository name.
        if (
            url.protocol !== "https:" ||
            url.hostname !== "github.com" ||
            parts.length !== 2
        ) {
            throw new Error("Invalid GitHub repository URL");
        }
    } catch {
        // Tell the user how to correct an invalid URL.
        status.textContent =
            "Enter a valid URL, such as https://github.com/owner/repository";
        return; // Stop until the URL is corrected.
    }

    status.textContent = "Analyzing repository...";
    analyzeBtn.disabled = true;

    try {
        let response;
        try {
            response = await fetch("/api/analyze", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    repository_url: repo,
                    bug_description: issue
                })
            });
        } catch {
            status.textContent =
                "Unable to reach the backend. Check your connection and try again.";
            return;
        }

        let report;
        try {
            report = await response.json();
        } catch {
            status.textContent = response.ok
                ? "The backend returned invalid JSON."
                : `Request failed with HTTP ${response.status}; the response was not valid JSON.`;
            return;
        }

        if (!response.ok) {
            const detail =
                report && typeof report.message === "string"
                    ? `: ${report.message}`
                    : report && typeof report.error === "string"
                        ? `: ${report.error}`
                    : "";
            status.textContent =
                `Request failed with HTTP ${response.status}${detail}`;
            return;
        }

        if (!report || typeof report !== "object" || Array.isArray(report)) {
            status.textContent =
                "The backend returned an unexpected response format.";
            return;
        }

        const displaySourceText = decodeReportText;
        const displayProse = displayReportText;

        const addSection = (label) => {
            const section = document.createElement("section");
            section.className = "report-section";
            const heading = document.createElement("h3");
            heading.textContent = label;
            section.append(heading);
            results.append(section);
            return section;
        };

        const addText = (parent, label, value) => {
            const content = document.createElement("p");
            if (label) {
                const fieldLabel = document.createElement("strong");
                fieldLabel.textContent = `${displayProse(label)}: `;
                content.append(fieldLabel);
            }
            content.append(document.createTextNode(displayProse(value)));
            parent.append(content);
        };

        const addList = (
            parent,
            values,
            ordered = false,
            formatValue = displayProse
        ) => {
            const list = document.createElement(ordered ? "ol" : "ul");
            values.forEach((value) => {
                const item = document.createElement("li");
                item.textContent = formatValue(value);
                list.append(item);
            });
            parent.append(list);
            return list;
        };

        results.replaceChildren();

        if (report.mode === "DEMO") {
            const demoNotice = document.createElement("p");
            demoNotice.textContent =
                "DEMO MODE: The current backend has not inspected repository files or run tests.";
            results.append(demoNotice);
        }

        const summarySection = addSection("Simple summary");
        addText(summarySection, "", report.summary);
        const investigationGuide = report.investigation_guide;
        const beginnerExplanation = report.beginner_explanation;
        const codeSection = addSection("What the code shows");
        addText(codeSection, "", beginnerExplanation?.what_code_does);
        if (Array.isArray(beginnerExplanation?.technical_terms)) {
            beginnerExplanation.technical_terms.forEach((entry) => {
                addText(
                    codeSection,
                    entry.term,
                    entry.meaning
                );
            });
        }

        const relevanceSection = addSection("Why it may matter");
        addText(
            relevanceSection,
            "",
            beginnerExplanation?.what_might_be_wrong
        );
        addText(
            relevanceSection,
            "Why this clue matters",
            beginnerExplanation?.why_evidence_matters
        );

        const missingSection = addSection("What information is missing");
        if (Array.isArray(investigationGuide?.missing_information)) {
            addList(missingSection, investigationGuide.missing_information);
        }

        const stepsSection = addSection("Suggested investigation steps");
        if (Array.isArray(investigationGuide?.steps)) {
            const steps = document.createElement("ol");
            investigationGuide.steps.forEach((step) => {
                const item = document.createElement("li");
                const title = document.createElement("strong");
                title.textContent = `${displayProse(step.title)}: `;
                item.append(
                    title,
                    document.createTextNode(displayProse(step.instruction))
                );
                steps.append(item);
            });
            stepsSection.append(steps);
        }
        const evidenceSection = addSection("Evidence and nearby context");
        if (Array.isArray(report.evidence)) {
            addList(
                evidenceSection,
                Array.from(new Set(report.evidence.map(displaySourceText))),
                false,
                displaySourceText
            );
        } else {
            addText(evidenceSection, "", report.evidence);
        }

        const suggestedTestsSection = addSection("Suggested tests");
        const testStatus = report.tests?.status || "NOT_RUN";
        const notExecutedNote = document.createElement("p");
        notExecutedNote.textContent =
            "These are possible scenarios based on retrieved code; they have not been run.";
        suggestedTestsSection.append(notExecutedNote);

        if (Array.isArray(report.suggested_tests) && report.suggested_tests.length > 0) {
            const suggestedTestsList = document.createElement("ul");
            report.suggested_tests.forEach((suggestion) => {
                const suggestionItem = document.createElement("li");
                const title = document.createElement("h4");
                title.textContent = displayProse(suggestion.title || "Suggested scenario");
                suggestionItem.append(title);
                [
                    ["Setup", suggestion.setup],
                    ["Action", suggestion.action],
                    ["Expected result", suggestion.expected_result],
                ].forEach(([label, value]) => {
                    addText(suggestionItem, label, value);
                });

                const evidenceBasis = suggestion.evidence_basis;
                const evidenceText =
                    evidenceBasis &&
                    typeof evidenceBasis.path === "string" &&
                    Number.isInteger(evidenceBasis.line) &&
                    evidenceBasis.line > 0
                        ? `${displaySourceText(evidenceBasis.path)}:${evidenceBasis.line}`
                        : "Source location unavailable.";
                addText(suggestionItem, "Evidence", evidenceText);
                suggestedTestsList.append(suggestionItem);
            });
            suggestedTestsSection.append(suggestedTestsList);
        } else {
            const noSuggestions = document.createElement("p");
            noSuggestions.textContent =
                "No test scenario is suggested because the retrieved evidence does not support one.";
            suggestedTestsSection.append(noSuggestions);
        }

        const testStatusSection = addSection("Test execution status");
        addText(testStatusSection, "Status", testStatus);
        addText(testStatusSection, "Details", report.tests?.details);

        status.textContent =
            report.mode === "DEMO"
                ? "Demo response received; no repository analysis or tests were performed."
                : "Analysis report received.";
    } finally {
        analyzeBtn.disabled = false;
    }
});


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

function isValidRepositoryUrl(value) {
    try {
        const url = new URL(value);
        const repositoryPath = url.pathname.match(
            /^\/([A-Za-z0-9-]+)\/([A-Za-z0-9._-]+)\/?$/
        );
        return (
            url.protocol === "https:" &&
            url.hostname === "github.com" &&
            !url.username &&
            !url.password &&
            !url.port &&
            !url.search &&
            !url.hash &&
            repositoryPath !== null &&
            ![".", ".."].includes(repositoryPath[2])
        );
    } catch {
        return false;
    }
}

function isObjectRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
}

function isValidWorkflow(workflow) {
    return (
        isObjectRecord(workflow) &&
        ["SUCCEEDED", "FAILED"].includes(workflow.source_retrieval) &&
        ["LEAD_FOUND", "NO_MATCH", "NOT_RUN"].includes(workflow.evidence_search) &&
        ["READY_FOR_REVIEW", "NOT_READY"].includes(workflow.report) &&
        workflow.verification === "NOT_RUN"
    );
}

function isValidAnalysisReport(report) {
    if (
        !isObjectRecord(report) ||
        typeof report.mode !== "string" ||
        typeof report.summary !== "string" ||
        !(
            typeof report.evidence === "string" ||
            (Array.isArray(report.evidence) &&
                report.evidence.every((line) => typeof line === "string"))
        )
    ) {
        return false;
    }

    const explanation = report.beginner_explanation;
    if (
        explanation != null &&
        (!isObjectRecord(explanation) ||
            (explanation.technical_terms !== undefined &&
                (!Array.isArray(explanation.technical_terms) ||
                    !explanation.technical_terms.every(isObjectRecord))))
    ) {
        return false;
    }

    const workflow = report.workflow;
    if (workflow != null && !isValidWorkflow(workflow)) {
        return false;
    }

    const guide = report.investigation_guide;
    if (
        guide != null &&
        (!isObjectRecord(guide) ||
            (guide.missing_information !== undefined &&
                !Array.isArray(guide.missing_information)) ||
            (guide.steps !== undefined &&
                (!Array.isArray(guide.steps) || !guide.steps.every(isObjectRecord))))
    ) {
        return false;
    }

    return (
        (report.suggested_tests === undefined ||
            (Array.isArray(report.suggested_tests) &&
                report.suggested_tests.every(isObjectRecord))) &&
        (report.tests === undefined ||
            report.tests === null ||
            isObjectRecord(report.tests))
    );
}

// Get references to the form elements so JavaScript can interact with them.
const repoInput = document.getElementById("repo");
const issueInput = document.getElementById("issue");
const analyzeBtn = document.getElementById("analyzeBtn");
const status = document.getElementById("status");
const results = document.getElementById("results");
const workflowNodes = {
    source_retrieval: document.getElementById("workflow-retrieval"),
    evidence_search: document.getElementById("workflow-evidence"),
    report: document.getElementById("workflow-report"),
    verification: document.getElementById("workflow-verification"),
};

function updateWorkflow(states) {
    Object.entries(workflowNodes).forEach(([key, node]) => {
        if (node) {
            node.textContent = states[key];
        }
    });
}

function displayWorkflow(workflow) {
    if (!isValidWorkflow(workflow)) {
        updateWorkflow({
            source_retrieval: "Unknown",
            evidence_search: "Unknown",
            report: "Unknown",
            verification: "Unknown",
        });
        return;
    }

    updateWorkflow({
        source_retrieval: workflow.source_retrieval === "SUCCEEDED"
            ? "Succeeded"
            : "Failed",
        evidence_search: {
            LEAD_FOUND: "Possible lead found",
            NO_MATCH: "No matching evidence",
            NOT_RUN: "Not run",
        }[workflow.evidence_search],
        report: workflow.report === "READY_FOR_REVIEW"
            ? "Ready for review"
            : "Not ready",
        verification: "NOT RUN",
    });
}

// Run this function whenever the user clicks the Analyze Bug button.
analyzeBtn.addEventListener("click", async () => {
    if (analyzeBtn.disabled) {
        return;
    }

    // Clear the previous report before validating or starting a new request.
    results.replaceChildren();
    updateWorkflow({
        source_retrieval: "Not started",
        evidence_search: "Not run",
        report: "Not ready",
        verification: "NOT RUN",
    });

    // Read the user's inputs and remove unnecessary spaces.
    const repo = repoInput.value.trim();
    const issue = issueInput.value.trim();

    if (!repo) {
        status.textContent = "Enter a GitHub repository URL.";
        return;
    }

    if (!issue) {
        status.textContent = "Enter a bug description.";
        return;
    }

    if (!isValidRepositoryUrl(repo)) {
        status.textContent =
            "Enter a valid URL, such as https://github.com/owner/repository";
        return;
    }

    analyzeBtn.disabled = true;
    status.textContent =
        "Retrieving source files and preparing the analysis report. This may take a moment.";
    updateWorkflow({
        source_retrieval: "In progress",
        evidence_search: "Waiting",
        report: "Waiting",
        verification: "NOT RUN",
    });

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
            updateWorkflow({
                source_retrieval: "Unavailable",
                evidence_search: "Not run",
                report: "Not ready",
                verification: "NOT RUN",
            });
            status.textContent =
                "Unable to reach the backend. Check your connection and try again.";
            return;
        }

        let report;
        try {
            report = await response.json();
        } catch {
            updateWorkflow({
                source_retrieval: "Unknown",
                evidence_search: "Unknown",
                report: "Not ready",
                verification: "NOT RUN",
            });
            status.textContent = response.ok
                ? "The backend returned invalid JSON."
                : `Request failed with HTTP ${response.status}; the response was not valid JSON.`;
            return;
        }

        if (!response.ok) {
            displayWorkflow(report?.workflow);
            if (!isValidWorkflow(report?.workflow)) {
                updateWorkflow({
                    source_retrieval: "Unknown",
                    evidence_search: "Not run",
                    report: "Not ready",
                    verification: "NOT RUN",
                });
            }
            const detail =
                report && typeof report.message === "string"
                    ? `: ${displayReportText(report.message)}`
                    : report && typeof report.error === "string"
                        ? `: ${displayReportText(report.error)}`
                    : "";
            status.textContent =
                `Request failed with HTTP ${response.status}${detail}`;
            return;
        }

        if (!isValidAnalysisReport(report)) {
            updateWorkflow({
                source_retrieval: "Unknown",
                evidence_search: "Unknown",
                report: "Not ready",
                verification: "NOT RUN",
            });
            status.textContent =
                "The backend returned an incomplete or unexpected analysis report.";
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
        const aiReviewSection = addSection("Analysis");
        aiReviewSection.className += " ai-review-section";
        const reviewLabel = document.createElement("span");
        const explanationText = beginnerExplanation?.what_might_be_wrong;
        const geminiUsed = report.gemini_used === true;
        reviewLabel.className = geminiUsed
            ? "ai-review-label"
            : "ai-review-label ai-review-label-fallback";
        reviewLabel.textContent = geminiUsed
            ? "Generated by Gemini"
            : "Rule-based fallback — Gemini not used";
        aiReviewSection.append(reviewLabel);
        addText(aiReviewSection, "Explanation", explanationText);
        addText(
            aiReviewSection,
            "What the code shows",
            beginnerExplanation?.what_code_does
        );
        if (Array.isArray(beginnerExplanation?.technical_terms)) {
            beginnerExplanation.technical_terms.forEach((entry) => {
                addText(
                    aiReviewSection,
                    entry.term,
                    entry.meaning
                );
            });
        }
        if (Array.isArray(investigationGuide?.missing_information)) {
            const missingHeading = document.createElement("h4");
            missingHeading.textContent = "Information to gather";
            aiReviewSection.append(missingHeading);
            addList(aiReviewSection, investigationGuide.missing_information);
        }

        if (Array.isArray(investigationGuide?.steps)) {
            const guideHeading = document.createElement("h4");
            guideHeading.textContent = "Investigation guide";
            aiReviewSection.append(guideHeading);
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
            aiReviewSection.append(steps);
        }
        const evidenceSection = addSection("Source Code Evidence");
        addText(evidenceSection, "Assessment", report.root_cause);
        addText(
            evidenceSection,
            "Why this clue matters",
            beginnerExplanation?.why_evidence_matters
        );
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
                addText(
                    suggestionItem,
                    "Purpose",
                    suggestion.purpose || suggestion.title
                );
                addText(
                    suggestionItem,
                    "Steps",
                    [suggestion.setup, suggestion.action]
                        .filter((value) => typeof value === "string" && value.trim())
                        .map(displayProse)
                        .join(" ")
                );
                addText(suggestionItem, "Expected result", suggestion.expected_result);
                addText(
                    suggestionItem,
                    "What to check",
                    suggestion.what_to_check ||
                        "Compare the observed behavior with the expected result."
                );

                const evidenceBasis = suggestion.evidence_basis;
                const hasEvidenceLocation =
                    evidenceBasis &&
                    typeof evidenceBasis.path === "string" &&
                    Number.isInteger(evidenceBasis.line) &&
                    evidenceBasis.line > 0;
                addText(
                    suggestionItem,
                    "Evidence",
                    hasEvidenceLocation
                        ? "See Source Code Evidence for file paths and matching line numbers."
                        : "Source location unavailable."
                );
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

        displayWorkflow(report.workflow);
        status.textContent =
            report.mode === "DEMO"
                ? "Demo response received; no repository analysis or tests were performed."
                : "Analysis report received.";
    } finally {
        analyzeBtn.disabled = false;
    }
});

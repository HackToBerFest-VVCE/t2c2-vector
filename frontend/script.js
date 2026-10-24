
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

        const displayValue = (value) => {
            if (value === null || value === undefined) {
                return "Not provided.";
            }
            return typeof value === "string" ? value : String(value);
        };

        const addField = (label, value) => {
            const heading = document.createElement("h3");
            heading.textContent = label;

            const content = document.createElement("p");
            content.textContent = displayValue(value);

            results.append(heading, content);
        };

        results.replaceChildren();

        if (report.mode === "DEMO") {
            const demoNotice = document.createElement("p");
            demoNotice.textContent =
                "DEMO MODE: The current backend has not inspected repository files or run tests.";
            results.append(demoNotice);
        }

        addField("Mode", report.mode);
        addField("Message", report.message);
        addField("Summary", report.summary);
        addField("Root cause", report.root_cause);

        const evidenceHeading = document.createElement("h3");
        evidenceHeading.textContent = "Evidence";
        results.append(evidenceHeading);

        if (Array.isArray(report.evidence)) {
            const evidenceList = document.createElement("ul");
            report.evidence.forEach((item) => {
                const evidenceItem = document.createElement("li");
                evidenceItem.textContent = displayValue(item);
                evidenceList.append(evidenceItem);
            });
            results.append(evidenceList);
        } else {
            const evidenceText = document.createElement("p");
            evidenceText.textContent = displayValue(report.evidence);
            results.append(evidenceText);
        }

        addField("Suggested fix", report.suggested_fix);
        addField("Tests status", report.tests?.status);
        addField("Tests details", report.tests?.details);

        status.textContent =
            report.mode === "DEMO"
                ? "Demo response received; no repository analysis or tests were performed."
                : "Analysis report received.";
    } finally {
        analyzeBtn.disabled = false;
    }
});


// Get references to the form elements so JavaScript can interact with them.
const repoInput = document.getElementById("repo");
const issueInput = document.getElementById("issue");
const analyzeBtn = document.getElementById("analyzeBtn");
const status = document.getElementById("status");
const results = document.getElementById("results");

// Add response content with textContent so server/user text is not treated as HTML.
function addResultSection(title, value) {
    const heading = document.createElement("h3");
    heading.textContent = title;

    const content = document.createElement(
        value !== null && typeof value === "object" ? "pre" : "p"
    );
    content.textContent =
        value !== null && typeof value === "object"
            ? JSON.stringify(value, null, 2)
            : String(value ?? "Not provided.");

    results.append(heading, content);
}

// Send the form values to Flask when the user clicks the button.
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

    // Let the user know the request is in progress and avoid duplicate clicks.
    analyzeBtn.disabled = true;
    status.textContent = "Sending request to RepoBridge AI...";
    results.replaceChildren();

    try {
        // Flask expects these exact JSON field names at POST /api/analyze.
        const response = await fetch("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                repository_url: repo,
                bug_description: issue
            })
        });

        const data = await response.json();
        if (!response.ok) {
            throw new Error(data.error || `Request failed (${response.status}).`);
        }

        status.textContent = data.message || "Repository request completed.";
        addResultSection("Summary", data.summary);
        addResultSection("Root cause", data.root_cause);
        addResultSection("Evidence", data.evidence);
        addResultSection("Suggested fix", data.suggested_fix);
        addResultSection("Tests", data.tests);
    } catch (error) {
        // Show useful feedback for server errors, network failures, or bad JSON.
        status.textContent = `Could not analyze the repository: ${error.message}`;
    } finally {
        // Allow another attempt after the request has finished.
        analyzeBtn.disabled = false;
    }
});

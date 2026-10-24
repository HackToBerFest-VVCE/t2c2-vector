
// Get references to the form elements so JavaScript can interact with them.
const repoInput = document.getElementById("repo");
const issueInput = document.getElementById("issue");
const analyzeBtn = document.getElementById("analyzeBtn");
const status = document.getElementById("status");
const results = document.getElementById("results");

// Run this function whenever the user clicks the Analyze Bug button.
analyzeBtn.addEventListener("click", () => {
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

    // Update the status message after validation succeeds.
    status.textContent =
        "Input received. Ready for repository analysis.";

    // Create HTML elements safely instead of inserting user input as HTML.
    const repoHeading = document.createElement("h3");
    repoHeading.textContent = "GitHub Repository";

    const repoText = document.createElement("p");
    repoText.textContent = repo;

    const issueHeading = document.createElement("h3");
    issueHeading.textContent = "Reported Bug";

    const issueText = document.createElement("p");
    issueText.textContent = issue;

    // Explain that live repository and AI analysis are not connected yet.
    const note = document.createElement("p");
    note.textContent =
        "Input captured successfully. GitHub retrieval and AI analysis will be connected next.";

    // Replace the initial empty state with the submitted information.
    results.replaceChildren(
        repoHeading,
        repoText,
        issueHeading,
        issueText,
        note
    );
});

"""Check public GitHub source retrieval for the Flask repository."""

from github_repository import GitHubRetrievalError, get_repository_sources


REPOSITORY_URL = "https://github.com/pallets/flask"
BUG_DESCRIPTION = "Flask application request routing and error handling"


def main():
    try:
        result = get_repository_sources(REPOSITORY_URL, BUG_DESCRIPTION)
    except GitHubRetrievalError as error:
        print(f"GitHub retrieval failed: {error}")
        return

    print(f"Repository: {result['repository']}")
    print(f"Default branch: {result['default_branch']}")
    print(f"Retrieved files: {len(result['files'])}")

    for file in result["files"]:
        content = file["content"]
        preview = content[:200]
        if len(content) > 200:
            preview += "..."

        print(f"\nPath: {file['path']}")
        print(f"Size: {file['size']} bytes")
        print(f"Preview: {preview!r}")


if __name__ == "__main__":
    main()

# GitLab Integration Guide

Run an AI-powered code review bot in your GitLab CI pipeline. The bot analyzes merge request diffs and posts inline comments with suggestions for improvement.

## How It Works

```
Developer creates MR → GitLab CI triggers → opencode-gitlab-reviewer runs
    → Fetches MR diff + file content → LLM analyzes code → Posts inline comments
    → Developer sees comments → Fixes issues → Pipeline re-runs → Bot checks if resolved
```

The bot identifies itself using `<!-- opencode-reviewer -->` tags in comments. On subsequent pipeline runs, it reads its own previous comments and only flags issues that are still present in the new diff.

## Prerequisites

- A GitLab self-hosted instance or GitLab.com account
- A GitLab Personal Access Token (PAT) or Project Access Token with `read_api` scope
- An OpenAI-compatible API endpoint (OpenRouter, Ollama, etc.)
- A Docker-compatible GitLab Runner

## Setup

### 1. Create a GitLab Token

Go to **User Settings → Access Tokens** (or **Project → Settings → Access Tokens**).

Create a token with:
- **Name:** `opencode-reviewer`
- **Expires at:** your preference
- **Scopes:** `read_api`

Save the token — you'll need it for the CI/CD variable.

### 2. Add CI/CD Variables

Go to **Settings → CI/CD → Variables**. Add these variables:

| Variable | Type | Value |
|----------|------|-------|
| `GITLAB_TOKEN` | Variable | Your GitLab PAT |
| `OPENAI_COMPATIBLE_BASE_URL` | Variable | e.g. `https://api.openrouter.ai/v1` |
| `OPENAI_COMPATIBLE_API_KEY` | Variable | Your AI provider API key |

Mark sensitive variables as **Masked** and **Protected** as appropriate.

### 3. Create `.gitlab-ci.yml`

Add this to your repository:

```yaml
mr-review:
  stage: test
  image:
    name: asmal95/opencode-gitlab-reviewer:latest
    entrypoint: [""]
  variables:
    GITLAB_URL: "https://gitlab.example.com"
    GITLAB_TOKEN: $GITLAB_TOKEN
    GITLAB_PROJECT_ID: "${CI_PROJECT_ID}"
    OPENAI_COMPATIBLE_BASE_URL: "${OPENAI_COMPATIBLE_BASE_URL}"
    OPENAI_COMPATIBLE_API_KEY: "${OPENAI_COMPATIBLE_API_KEY}"
  before_script:
    - git fetch origin merge-requests/${CI_MERGE_REQUEST_IID}/head:mr-branch
    - git checkout mr-branch
  script:
    - |
      opencode run \
        --dir /builds/$CI_PROJECT_PATH \
        --format json \
        "Review MR !${CI_MERGE_REQUEST_IID} from ${CI_MERGE_REQUEST_SOURCE_BRANCH_NAME} to ${CI_MERGE_REQUEST_TARGET_BRANCH_NAME}.
        Check the diff carefully. Post inline comments for issues you find.
        Consider previous review comments — if they are addressed in the new diff, do not repeat them."
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
  timeout: 10m
```

### 4. Adjust for Your Setup

**Self-hosted GitLab:** Update `GITLAB_URL` to your instance URL.

**Different AI provider:** Update `OPENAI_COMPATIBLE_BASE_URL` and `OPENAI_COMPATIBLE_API_KEY`.

For local Ollama:
```yaml
variables:
  OPENAI_COMPATIBLE_BASE_URL: "http://host.docker.internal:11434/v1"
  OPENAI_COMPATIBLE_API_KEY: "ollama"
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GITLAB_URL` | yes | GitLab instance URL (e.g. `https://gitlab.example.com`) |
| `GITLAB_TOKEN` | yes | PAT with `read_api` scope |
| `GITLAB_PROJECT_ID` | yes | Project ID (or use `$CI_PROJECT_ID`) |
| `OPENAI_COMPATIBLE_BASE_URL` | yes | AI provider endpoint |
| `OPENAI_COMPATIBLE_API_KEY` | yes | API key (may be empty for local Ollama) |

## What the Bot Does

1. **Fetches the MR diff** using `mr.changes()` — gets parsed file changes with full file content
2. **Reads old bot comments** tagged with `<!-- opencode-reviewer -->`
3. **Analyzes the code** via the configured LLM
4. **Posts inline comments** using GitLab's discussions API with proper line positioning
5. **Falls back to general comments** if a line is outside the diff

## Comment Format

All bot comments include a hidden tag for identification:

```
<!-- opencode-reviewer -->
Consider using a constant here instead of a magic number.
---
Review by opencode-gitlab-reviewer
```

The bot uses `sha1(file_path) + "_" + line + "_" + line` as the GitLab `line_code` for precise inline positioning.

## Troubleshooting

### Comments not appearing

- Verify `GITLAB_TOKEN` has `read_api` scope
- Check runner logs for API errors
- Ensure the runner can reach the GitLab instance

### Inline comments not positioning correctly

- GitLab requires the line to be within the MR diff
- The bot falls back to general comments for lines outside the diff
- Check that the file path matches exactly (case-sensitive)

### Rate limiting (429 errors)

- The bot retries 429 and 5xx errors automatically
- If you hit GitLab API limits, consider reducing parallel pipeline runs
- Check GitLab's rate limit documentation for your plan

### LLM errors

- Verify `OPENAI_COMPATIBLE_API_KEY` is correct
- Check that the API endpoint is reachable from the runner
- Review OpenCode logs: `docker logs <container>`

## Local Testing

Test the image locally before pushing to CI:

```bash
docker run --rm -it \
  -e GITLAB_URL=https://gitlab.example.com \
  -e GITLAB_TOKEN=your-token \
  -e GITLAB_PROJECT_ID=123 \
  -e OPENAI_COMPATIBLE_BASE_URL=https://api.openrouter.ai/v1 \
  -e OPENAI_COMPATIBLE_API_KEY=your-key \
  -v $(pwd):/workspace \
  asmal95/opencode-gitlab-reviewer:latest \
  run --dir /workspace --format json "Review MR !1"
```

## Advanced: Custom Review Prompt

Modify the prompt in your `.gitlab-ci.yml` to customize review focus:

```yaml
script:
  - |
    opencode run \
      --dir /builds/$CI_PROJECT_PATH \
      --format json \
      "Review MR !${CI_MERGE_REQUEST_IID}.
       Focus on: security vulnerabilities, error handling, performance.
       Ignore style nitpicks.
       Post inline comments for each issue found."
```

## Image

- **Registry:** DockerHub
- **Name:** `asmal95/opencode-gitlab-reviewer`
- **Tag:** `latest` (or pin to a specific version)

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  GitLab CI Runner                                        │
│                                                          │
│  opencode-gitlab-reviewer:latest                         │
│  ┌───────────────────────────────────────────────────┐  │
│  │  opencode run                                      │  │
│  │       │                                            │  │
│  │       ├─► GitLab MCP (local subprocess)            │  │
│  │       │     ├─ mr.changes()                        │  │
│  │       │     ├─ discussions.create()                │  │
│  │       │     ├─ retry on 429/5xx                    │  │
│  │       │     └─ binary file detection               │  │
│  │       │                                            │  │
│  │       ├─► LLM (OpenAI-compatible)                 │  │
│  │       │     (OpenRouter / Ollama / etc.)           │  │
│  │       │                                            │  │
│  │       └─► Source code (from GitLab CI checkout)    │  │
│  └───────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

## Next Steps

- Pin the image tag to a specific version for stability
- Add the CI job to your pipeline's `test` stage
- Monitor first few MRs to tune the review prompt
- Consider adding `timeout: 15m` for large MRs

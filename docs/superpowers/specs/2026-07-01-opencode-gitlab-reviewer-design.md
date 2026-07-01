# opencode-gitlab-reviewer — Design Spec

**Date:** 2026-07-01
**Status:** Approved
**Topic:** One-shot Docker container for AI-powered GitLab MR code review running inside CI pipeline

## 1. Overview

One-shot Docker container for GitLab CI `merge_request_event` pipeline. Contains OpenCode + custom Python GitLab MCP server. Task: analyze MR diff, post inline comments with awareness of previous reviewer comments, exit.

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│  GitLab CI: merge_request_event trigger                 │
│                                                         │
│  docker run opencode-gitlab-reviewer:latest             │
│  ┌───────────────────────────────────────────────────┐  │
│  │  opencode run "review MR #42 ..."                  │  │
│  │       │                                            │  │
│  │       ├─► Spawn local MCP subprocess (stdio)       │  │
│  │       │                                            │  │
│  │       ├─► LLM (OpenAI-compatible API)             │  │
│  │       │     (via config: provider + model)         │  │
│  │       │                                            │  │
│  │       └─► GitLab MCP Server (Python/FastMCP)       │  │
│  │             ├─ get_merge_request_diff              │  │
│  │             ├─ list_old_reviewer_comments          │  │
│  │             ├─ post_comment / post_inline_comment  │  │
│  │             └─ get_pipeline_status                 │  │
│  └───────────────────────────────────────────────────┘  │
│                                                         │
│  Workspace: MR branch (checked out by GitLab CI)        │
└─────────────────────────────────────────────────────────┘
```

**Hybrid context:**
- **Source code** — GitLab CI checks out MR branch into `/workspace` → OpenCode reads files directly
- **MR context** — MCP fetches diff via GitLab API (line-level hunks for inline comments)
- **State** — MCP reads old comments via Notes API, OpenCode decides what is resolved

## 3. Execution Flow (Runbook)

```
1. GitLab CI: merge_request_event trigger
2. docker pull asmal95/opencode-gitlab-reviewer:latest
3. git fetch + git checkout refs/merge-requests/{IID}/head  (MR branch)
4. opencode run --dir /workspace "Review MR !{IID} ..."
     │
     ├─ OpenCode loads config (mcp.servers.gitlab — type: local)
     ├─ OpenCode spawns Python MCP subprocess (stdio transport)
     ├─ MCP initializes GitLab client (GITLAB_URL + GITLAB_TOKEN)
     ├─ MCP: get_merge_request_diff → passes diff to OpenCode
     ├─ MCP: list_old_reviewer_comments → passes old comment context
     ├─ OpenCode: LLM analyzes diff + old comments + source files
     ├─ OpenCode: calls MCP post_inline_comment for each issue found
     ├─ OpenCode: calls MCP post_comment for summary
     └─ opencode run exits (exit 0 / exit 1)
5. GitLab CI job completes
```

## 4. Components

### 4.1. Project Structure

```
sidecars/gitlab-mcp/
  ├── Dockerfile            # Python venv build + MCP server config
  ├── server.py             # Entry point: FastMCP app.run()
  ├── gitlab_client.py      # python-gitlab wrapper
  ├── tools.py              # MCP tool implementations
  └── requirements.txt      # fastmcp, python-gitlab

configs/gitlab/
  └─┐ opencode.jsonc        # Config: provider + model + local MCP

Dockerfile.gitlab            # Main image: opencode-base + Python MCP
docker-compose.gitlab.yaml   # Optional: local run
```

### 4.2. Dockerfile.gitlab

Multi-stage, inherits `opencode-base` from existing Dockerfile:

```dockerfile
FROM asmal95/opencode-platform:latest AS opencode-base

# Python venv for MCP server
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip && \
    python3 -m venv /opt/mcp-venv
ENV PATH="/opt/mcp-venv/bin:$PATH"

COPY sidecars/gitlab-mcp/requirements.txt /opt/gitlab-mcp/requirements.txt
RUN uv pip install --system -r /opt/gitlab-mcp/requirements.txt

COPY sidecars/gitlab-mcp/ /opt/gitlab-mcp/
COPY configs/gitlab/ /opt/opencode-config/

ENTRYPOINT ["opencode"]
CMD ["run"]
```

`opencode-base` already includes: Debian Bookworm Slim, Node.js 22, uv, opencode-ai, `coder` user.

### 4.3. GitLab MCP Server (Python/FastMCP)

**Tools:**

| Tool | Description |
|------|-------------|
| `get_merge_request` | MR details: title, description, author, state, sha |
| `get_merge_request_diff` | Files + patches + old_line/new_line for inline comments |
| `list_old_reviewer_comments` | Bot's comments (tag `<!-- opencode-reviewer -->`) with diff-hunk context |
| `post_comment` | General MR comment |
| `post_inline_comment` | Comment on specific diff line (new_file_line) |
| `get_pipeline_status` | CI pipeline status for MR |

**State tracking via HTML comment tag:**

```python
BOT_TAG = "<!-- opencode-reviewer -->"

# When posting:
body = f"{BOT_TAG}\n{comment_text}\n---\nMR #{mr_iid}"

# When reading old comments:
notes = gl.projects.project_id.mergerequests.mergerequest_notes.list(mr_iid)
our_notes = [n for n in notes if BOT_TAG in n.body]
```

**Mapping old comments to new diff:**

```python
# For each old inline comment:
# - old_file_path, old_line (old_line for new lines = 0)
# - Search new diff: if file is changed and hunk covers that area → "still relevant"
# - If area untouched by new diff → "may be resolved"
# Pass this status to OpenCode as context
```

### 4.4. OpenCode Config (`configs/gitlab/opencode.jsonc`)

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "enabled_providers": ["openai-compatible"],
  "provider": {
    "openai-compatible": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "{env:OPENAI_COMPATIBLE_BASE_URL}",
        "apiKey": "{env:OPENAI_COMPATIBLE_API_KEY}"
      },
      "models": {
        "deepseek/deepseek-v4-flash": {
          "name": "DeepSeek V4 Flash"
        }
      }
    }
  },
  "model": "openai-compatible/deepseek/deepseek-v4-flash",
  "permission": {
    "write": "allow",
    "edit": "allow",
    "bash": "allow"
  },
  "mcp": {
    "gitlab": {
      "type": "local",
      "command": ["uv", "run", "--directory", "/opt/gitlab-mcp", "server.py"],
      "environment": {
        "GITLAB_URL": "{env:GITLAB_URL}",
        "GITLAB_TOKEN": "{env:GITLAB_TOKEN}",
        "GITLAB_PROJECT_ID": "{env:GITLAB_PROJECT_ID}"
      }
    }
  }
}
```

### 4.5. Sample `.gitlab-ci.yml`

```yaml
mr-review:
  stage: test
  image:
    name: asmal95/opencode-gitlab-reviewer:latest
    entrypoint: [""]
  variables:
    GITLAB_URL: "https://gitlab.example.com"
    GITLAB_PROJECT_ID: "123"
    OPENAI_COMPATIBLE_BASE_URL: "https://api.openrouter.ai/v1"
    OPENCODE_SERVER_PASSWORD: "reviewer"
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
  only:
    - merge_requests
  timeout: 10m
```

### 4.6. Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GITLAB_URL` | yes | Self-hosted GitLab URL |
| `GITLAB_TOKEN` | yes | PAT with `read_api` scope |
| `GITLAB_PROJECT_ID` | yes | Project ID or path |
| `OPENAI_COMPATIBLE_BASE_URL` | yes | AI provider URL |
| `OPENAI_COMPATIBLE_API_KEY` | yes | AI provider API key |
| `OPENCODE_SERVER_PASSWORD` | no | Basic Auth (for API if needed) |

## 5. Security

- Container runs as non-root (user `coder`, uid 1000)
- Read-only filesystem where possible
- Token passed via env var only, never committed
- MCP server communicates with GitLab API only via python-gitlab (HTTPS)
- AI API calls via HTTPS to configured provider

## 6. Out of Scope

- Creating/updating/merging MRs
- Triggering CI pipelines
- Telegram integration
- OAuth flow (PAT only)
- Comments in other projects (project-scoped)

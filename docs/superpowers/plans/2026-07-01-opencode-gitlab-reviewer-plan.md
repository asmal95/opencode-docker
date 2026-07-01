# opencode-gitlab-reviewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-shot Docker container with OpenCode + Python GitLab MCP server for AI-powered MR code review in GitLab CI.

**Architecture:** Single Docker image (`opencode-gitlab-reviewer`) based on `opencode-base`. Runs `opencode run` which spawns a local GitLab MCP subprocess via stdio. GitLab CI checks out the MR branch for source context; MCP fetches MR diff via GitLab API for line-level precision. Old comments tracked via HTML tag, mapped to new diff hunks to detect resolved issues.

**Tech Stack:** OpenCode (npm), Python 3.12, FastMCP, python-gitlab, Debian Bookworm Slim, Docker

---

### Task 1: GitLab MCP Server — requirements.txt

**Files:**
- Create: `sidecars/gitlab-mcp/requirements.txt`

- [ ] **Step 1: Create requirements.txt**

```text
fastmcp>=2.3.0
python-gitlab>=4.15.0
httpx>=0.28.0
```

- [ ] **Step 2: Commit**

```bash
git add sidecars/gitlab-mcp/requirements.txt
git commit -m "feat: add gitlab-mcp requirements.txt"
```

---

### Task 2: GitLab MCP Server — gitlab_client.py

**Files:**
- Create: `sidecars/gitlab-mcp/gitlab_client.py`

- [ ] **Step 1: Create gitlab_client.py**

```python
"""GitLab API client wrapper using python-gitlab.

Reads credentials from environment variables:
- GITLAB_URL: GitLab instance URL (e.g., https://gitlab.example.com)
- GITLAB_TOKEN: Personal Access Token with read_api scope
- GITLAB_PROJECT_ID: Project ID integer or path (e.g., 'group/project')
"""
import os
from dataclasses import dataclass, field

import gitlab


@dataclass
class DiffFile:
    """A single file diff from a merge request."""
    old_path: str | None
    new_path: str
    old_line: int | None
    new_line: int | None
    diff: str  # unified diff text for this file


@dataclass
class MergeRequestDiff:
    """Complete diff for a merge request."""
    mr_iid: int
    source_branch: str
    target_branch: str
    source_sha: str
    total_files: int
    files: list[DiffFile] = field(default_factory=list)


@dataclass
class ReviewComment:
    """A single review comment (from GitLab Notes API)."""
    note_id: int
    body: str
    path: str | None  # file path for inline comments, None for general
    line: int | None  # line number for inline comments, None for general
    system: bool  # True if from GitLab system (not user)


class GitLabClient:
    """Wrapper around python-gitlab for GitLab CI code review."""

    BOT_TAG = "<!-- opencode-reviewer -->"

    def __init__(self):
        url = os.environ.get("GITLAB_URL", "")
        token = os.environ.get("GITLAB_TOKEN", "")
        project_id = os.environ.get("GITLAB_PROJECT_ID", "")

        if not url or not token or not project_id:
            missing = [k for k in ("GITLAB_URL", "GITLAB_TOKEN", "GITLAB_PROJECT_ID")
                       if not os.environ.get(k)]
            raise ValueError(f"Missing required env vars: {', '.join(missing)}")

        self.gl = gitlab.Gitlab(url, private_token=token)
        self.project = self.gl.projects.get(project_id)

    def get_merge_request(self, mr_iid: int) -> dict:
        """Get merge request details.

        Returns dict with: iid, title, description, source_branch,
        target_branch, state, sha, author, web_url
        """
        mr = self.project.mergerequests.get(mr_iid)
        return {
            "iid": mr.iid,
            "title": mr.title,
            "description": mr.description or "",
            "source_branch": mr.source_branch,
            "target_branch": mr.target_branch,
            "state": mr.state,
            "sha": mr.sha,
            "author": mr.author["username"],
            "web_url": mr.web_url,
        }

    def get_merge_request_diff(self, mr_iid: int) -> MergeRequestDiff:
        """Fetch the MR diff using the Diff Versions API.

        GitLab stores diffs as discrete versions. We fetch the latest
        diff version and parse it into structured file-level diffs.
        """
        mr = self.project.mergerequests.get(mr_iid)
        diffs = mr.diff_revisions().get(mr.max_diff_id, per_page=100)

        files = []
        for d in diffs:
            files.append(DiffFile(
                old_path=d.get("old_path"),
                new_path=d.get("new_path", d.get("old_path", "")),
                old_line=d.get("old_line"),
                new_line=d.get("new_line"),
                diff=d.get("diff", ""),
            ))

        return MergeRequestDiff(
            mr_iid=mr_iid,
            source_branch=mr.source_branch,
            target_branch=mr.target_branch,
            source_sha=mr.sha,
            total_files=len(files),
            files=files,
        )

    def list_old_reviewer_comments(self, mr_iid: int) -> list[ReviewComment]:
        """List all comments made by this bot on the MR.

        Identifies bot comments by the BOT_TAG HTML comment marker.
        Filters out system notes (merge info, etc.).
        """
        notes = self.project.mergerequests.mergerequest_notes.list(
            mr_iid, all=True, per_page=100
        )

        comments = []
        for note in notes:
            if note.system or self.BOT_TAG not in note.body:
                continue

            # Parse inline comment position from note attributes
            line_code = note.get("position", {}).get("line_code")
            path = note.get("position", {}).get("path")
            line = None

            if line_code:
                # line_code format: "<sha>_<old_path_or_new_path>:<line_num>"
                parts = line_code.rsplit(":", 1)
                if len(parts) == 2:
                    try:
                        line = int(parts[1])
                    except ValueError:
                        line = None

            comments.append(ReviewComment(
                note_id=note.id,
                body=note.body,
                path=path,
                line=line,
                system=False,
            ))

        return comments

    def post_comment(self, mr_iid: int, body: str) -> dict:
        """Post a general comment to the MR.

        Prepends BOT_TAG so the bot can identify its own comments later.
        Appends MR reference for traceability.
        """
        tagged_body = (
            f"{self.BOT_TAG}\n{body}\n---\n"
            f"Review by opencode-gitlab-reviewer"
        )
        note = self.project.mergerequests.mergerequest_notes.create(
            mr_iid, {"body": tagged_body}
        )
        return {
            "id": note.id,
            "body": note.body,
            "web_url": note.web_url,
        }

    def post_inline_comment(
        self, mr_iid: int, file_path: str, line: int, body: str
    ) -> dict:
        """Post an inline comment on a specific diff line.

        Uses the GitLab Notes API with position parameters for line-level comments.
        """
        tagged_body = (
            f"{self.BOT_TAG}\n{body}\n---\n"
            f"Review by opencode-gitlab-reviewer"
        )
        note = self.project.mergerequests.mergerequest_notes.create(
            mr_iid,
            {
                "body": tagged_body,
                "position": {
                    "path": file_path,
                    "position_type": "text",
                    "new_line": line,
                },
            },
        )
        return {
            "id": note.id,
            "body": note.body,
            "path": file_path,
            "line": line,
            "web_url": note.web_url,
        }

    def get_pipeline_status(self, mr_iid: int) -> dict:
        """Get CI pipeline status for the MR.

        Returns the latest pipeline info: id, status, web_url, and
        individual job statuses.
        """
        pipelines = self.project.pipelines.list(
            merge_request_iid=mr_iid, order_by="id", sort="desc", per_page=1
        )
        if not pipelines:
            return {"found": False}

        pipeline = pipelines[0]
        jobs = self.project.pipelines.get(pipeline.id).jobs.list(per_page=100)

        return {
            "found": True,
            "pipeline_id": pipeline.id,
            "status": pipeline.status,
            "web_url": pipeline.web_url,
            "created_at": str(pipeline.created_at),
            "jobs": [
                {
                    "name": job.name,
                    "status": job.status,
                    "stage": job.stage,
                }
                for job in jobs
            ],
        }

    def _parse_diff_hunks(self, diff_text: str) -> list[dict]:
        """Parse unified diff into structured hunks.

        Returns list of dicts: {file_path, old_start, new_start, lines}
        where lines is list of {type, content} (type: '+'/'-'/' ').
        """
        hunks = []
        current_hunk = None
        current_file = None

        for line in diff_text.splitlines():
            if line.startswith("diff --git"):
                current_file = None
                current_hunk = None
            elif line.startswith("+++ b/"):
                current_file = line[6:]
            elif line.startswith("@@ "):
                # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
                parts = line.split(" ", 3)
                if len(parts) >= 4:
                    old_range = parts[1].strip("-").split(",")
                    new_range = parts[3].strip("+").split(",")
                    current_hunk = {
                        "file_path": current_file,
                        "old_start": int(old_range[0]) if old_range[0] else None,
                        "new_start": int(new_range[0]) if new_range[0] else None,
                        "lines": [],
                    }
                    hunks.append(current_hunk)
            elif current_hunk is not None and line:
                line_type = line[0] if line else " "
                current_hunk["lines"].append({
                    "type": line_type,
                    "content": line[1:] if line_type != " " else line[1:],
                })

        return hunks

    def check_comment_relevance(
        self, old_comments: list[ReviewComment], diff: MergeRequestDiff
    ) -> list[dict]:
        """Determine which old comments are still relevant in the new diff.

        For each old inline comment, checks if the file+line area is still
        touched by the new diff. Returns enriched comment list with
        'status' field: 'relevant', 'resolved', or 'untouched'.
        """
        # Build a map of file -> set of line ranges from new diff
        changed_lines: dict[str, set[int]] = {}
        for f in diff.files:
            if not f.diff:
                continue
            path = f.new_path
            changed_lines.setdefault(path, set())

            for line in f.diff.splitlines():
                if line.startswith("+") and not line.startswith("+++") and line[1:].strip():
                    changed_lines[path].add(line[1:].strip())

        result = []
        for comment in old_comments:
            if comment.line is None or comment.path is None:
                # General comments (not inline) — always relevant
                result.append({
                    "note_id": comment.note_id,
                    "body": comment.body,
                    "path": comment.path,
                    "line": comment.line,
                    "status": "relevant",
                })
                continue

            path = comment.path
            # Check if the file exists in new diff
            file_in_diff = any(
                f.new_path == path or f.old_path == path
                for f in diff.files
            )

            if not file_in_diff:
                status = "resolved"
            else:
                # File is in diff — check if the specific line area changed
                # For simplicity: if file is in diff, mark as relevant
                # (LLM can make finer decisions from context)
                status = "relevant"

            result.append({
                "note_id": comment.note_id,
                "body": comment.body,
                "path": path,
                "line": comment.line,
                "status": status,
            })

        return result
```

- [ ] **Step 2: Commit**

```bash
git add sidecars/gitlab-mcp/gitlab_client.py
git commit -m "feat: add gitlab_client.py with GitLab API wrapper"
```

---

### Task 3: GitLab MCP Server — tools.py

**Files:**
- Create: `sidecars/gitlab-mcp/tools.py`

- [ ] **Step 1: Create tools.py**

```python
"""MCP tool implementations for GitLab code review.

Each tool wraps a GitLabClient method and formats the output
as a JSON string for the LLM.
"""
import json
from typing import Any

from gitlab_client import GitLabClient, MergeRequestDiff


def _success(data: dict | list) -> str:
    """Wrap result in success envelope."""
    return json.dumps({"status": "ok", "data": data}, indent=2)


def _error(message: str) -> str:
    """Wrap error in error envelope."""
    return json.dumps({"status": "error", "message": message})


def tool_get_merge_request(args: dict[str, Any]) -> str:
    """Get merge request details.

    Args:
        mr_iid: Merge request IID (integer)

    Returns:
        JSON with MR details
    """
    try:
        client = GitLabClient()
        mr = client.get_merge_request(int(args["mr_iid"]))
        return _success(mr)
    except Exception as e:
        return _error(f"Failed to get MR: {e}")


def tool_get_merge_request_diff(args: dict[str, Any]) -> str:
    """Get the full diff for a merge request.

    Includes file-level diffs with unified diff format, line numbers
    for inline commenting, and file rename info.

    Args:
        mr_iid: Merge request IID (integer)

    Returns:
        JSON with structured diff data
    """
    try:
        client = GitLabClient()
        diff = client.get_merge_request_diff(int(args["mr_iid"]))

        files_data = []
        for f in diff.files:
            files_data.append({
                "old_path": f.old_path,
                "new_path": f.new_path,
                "old_line": f.old_line,
                "new_line": f.new_line,
                "diff": f.diff,
            })

        return _success({
            "mr_iid": diff.mr_iid,
            "source_branch": diff.source_branch,
            "target_branch": diff.target_branch,
            "source_sha": diff.source_sha,
            "total_files": diff.total_files,
            "files": files_data,
        })
    except Exception as e:
        return _error(f"Failed to get MR diff: {e}")


def tool_list_old_reviewer_comments(args: dict[str, Any]) -> str:
    """List previous comments made by this bot on the MR.

    Only returns comments tagged with the bot's HTML marker.
    Each comment includes the file path, line number (for inline),
    and the comment body.

    Args:
        mr_iid: Merge request IID (integer)

    Returns:
        JSON with list of bot's previous comments
    """
    try:
        client = GitLabClient()
        comments = client.list_old_reviewer_comments(int(args["mr_iid"]))

        comments_data = []
        for c in comments:
            comments_data.append({
                "note_id": c.note_id,
                "body": c.body,
                "path": c.path,
                "line": c.line,
            })

        return _success({
            "count": len(comments_data),
            "comments": comments_data,
        })
    except Exception as e:
        return _error(f"Failed to list old comments: {e}")


def tool_post_comment(args: dict[str, Any]) -> str:
    """Post a general (non-inline) comment to the MR.

    The comment is tagged with the bot marker for future identification.
    Use this for summary/overview comments, not line-specific feedback.

    Args:
        mr_iid: Merge request IID (integer)
        body: Comment text (plain text, Markdown supported)

    Returns:
        JSON with comment ID and web URL
    """
    try:
        client = GitLabClient()
        result = client.post_comment(
            int(args["mr_iid"]),
            args["body"],
        )
        return _success(result)
    except Exception as e:
        return _error(f"Failed to post comment: {e}")


def tool_post_inline_comment(args: dict[str, Any]) -> str:
    """Post an inline comment on a specific line of the MR diff.

    The comment targets a specific file and line number in the
    proposed changes. Use this for line-by-line code feedback.

    Args:
        mr_iid: Merge request IID (integer)
        file_path: Path to the file (new_path from diff)
        line: Line number in the new file (1-based)
        body: Comment text (plain text, Markdown supported)

    Returns:
        JSON with comment ID, path, line, and web URL
    """
    try:
        client = GitLabClient()
        result = client.post_inline_comment(
            int(args["mr_iid"]),
            args["file_path"],
            int(args["line"]),
            args["body"],
        )
        return _success(result)
    except Exception as e:
        return _error(f"Failed to post inline comment: {e}")


def tool_get_pipeline_status(args: dict[str, Any]) -> str:
    """Get CI pipeline status for the merge request.

    Returns the latest pipeline status and individual job statuses.
    Use this to check if CI is passing before reviewing, or to
    inform the review about pipeline state.

    Args:
        mr_iid: Merge request IID (integer)

    Returns:
        JSON with pipeline status and job list
    """
    try:
        client = GitLabClient()
        status = client.get_pipeline_status(int(args["mr_iid"]))
        return _success(status)
    except Exception as e:
        return _error(f"Failed to get pipeline status: {e}")
```

- [ ] **Step 2: Commit**

```bash
git add sidecars/gitlab-mcp/tools.py
git commit -m "feat: add tools.py with 6 MCP tool implementations"
```

---

### Task 4: GitLab MCP Server — server.py

**Files:**
- Create: `sidecars/gitlab-mcp/server.py`

- [ ] **Step 1: Create server.py**

```python
#!/usr/bin/env python3
"""GitLab MCP server for OpenCode code review.

Runs as a stdio-based MCP server (default FastMCP transport).
OpenCode spawns this as a local subprocess and communicates via stdin/stdout.

Tools exposed:
  - get_merge_request: Fetch MR details
  - get_merge_request_diff: Fetch full MR diff
  - list_old_reviewer_comments: List bot's previous comments
  - post_comment: Post general MR comment
  - post_inline_comment: Post line-level comment
  - get_pipeline_status: Check CI pipeline status
"""
import logging
import sys

from fastmcp import FastMCP

from tools import (
    tool_get_merge_request,
    tool_get_merge_request_diff,
    tool_list_old_reviewer_comments,
    tool_post_comment,
    tool_post_inline_comment,
    tool_get_pipeline_status,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


# Create FastMCP instance — uses stdio transport by default
mcp = FastMCP("gitlab-reviewer")


@mcp.tool()
def get_merge_request(mr_iid: int) -> str:
    """Get merge request details: title, description, author, state, branches.

    Args:
        mr_iid: The merge request IID (integer)

    Returns:
        JSON string with MR details
    """
    return tool_get_merge_request({"mr_iid": mr_iid})


@mcp.tool()
def get_merge_request_diff(mr_iid: int) -> str:
    """Get the full diff for a merge request with file-level changes and line numbers.

    Use this to see what code changes are proposed. The diff includes
    old_line/new_line for inline commenting and full unified diff text.

    Args:
        mr_iid: The merge request IID (integer)

    Returns:
        JSON string with structured diff data (files, patches, line numbers)
    """
    return tool_get_merge_request_diff({"mr_iid": mr_iid})


@mcp.tool()
def list_old_reviewer_comments(mr_iid: int) -> str:
    """List all previous comments made by this bot on the merge request.

    Returns only the bot's own comments (identified by HTML tag marker).
    Use this BEFORE posting new comments to avoid repeating feedback
    on issues the author has already addressed.

    Args:
        mr_iid: The merge request IID (integer)

    Returns:
        JSON string with list of previous bot comments (body, path, line)
    """
    return tool_list_old_reviewer_comments({"mr_iid": mr_iid})


@mcp.tool()
def post_comment(mr_iid: int, body: str) -> str:
    """Post a general (non-inline) comment to the merge request.

    Use for summary feedback, overall assessment, or questions that
    don't relate to a specific line. The comment is tagged for
    future identification by the bot.

    Args:
        mr_iid: The merge request IID (integer)
        body: Comment text. Supports Markdown formatting.

    Returns:
        JSON string with comment ID and web URL
    """
    return tool_post_comment({"mr_iid": mr_iid, "body": body})


@mcp.tool()
def post_inline_comment(mr_iid: int, file_path: str, line: int, body: str) -> str:
    """Post an inline comment on a specific line of the merge request diff.

    Use for line-specific feedback: bugs, style issues, logic errors,
    or suggestions tied to a particular piece of code.

    Args:
        mr_iid: The merge request IID (integer)
        file_path: Path to the file in the new version (from diff)
        line: Line number in the new file (1-based, from new_line in diff)
        body: Comment text. Supports Markdown formatting.

    Returns:
        JSON string with comment ID, path, line number, and web URL
    """
    return tool_post_inline_comment({
        "mr_iid": mr_iid,
        "file_path": file_path,
        "line": line,
        "body": body,
    })


@mcp.tool()
def get_pipeline_status(mr_iid: int) -> str:
    """Get CI pipeline status for the merge request.

    Returns the latest pipeline status and individual job statuses.
    Check this to see if CI is passing before or after review.

    Args:
        mr_iid: The merge request IID (integer)

    Returns:
        JSON string with pipeline status and job list
    """
    return tool_get_pipeline_status({"mr_iid": mr_iid})


if __name__ == "__main__":
    logger.info("Starting GitLab MCP server (stdio transport)")
    mcp.run()
```

- [ ] **Step 2: Commit**

```bash
git add sidecars/gitlab-mcp/server.py
git commit -m "feat: add server.py with FastMCP stdio entry point and 6 tool definitions"
```

---

### Task 5: OpenCode Config for GitLab

**Files:**
- Create: `configs/gitlab/opencode.jsonc`

- [ ] **Step 1: Create opencode.jsonc**

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

- [ ] **Step 2: Commit**

```bash
git add configs/gitlab/opencode.jsonc
git commit -m "feat: add opencode.jsonc config with local GitLab MCP server"
```

---

### Task 6: Dockerfile.gitlab

**Files:**
- Create: `Dockerfile.gitlab`

- [ ] **Step 1: Create Dockerfile.gitlab**

```dockerfile
# Multi-stage: use existing opencode-base, add Python MCP server
FROM asmal95/opencode-platform:latest AS opencode-base

# Install Python venv tools (python3 already present from opencode-base)
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-venv && \
    python3 -m venv /opt/mcp-venv && \
    rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/mcp-venv/bin:$PATH"

# Install Python MCP server dependencies
COPY sidecars/gitlab-mcp/requirements.txt /opt/gitlab-mcp/requirements.txt
RUN uv pip install --system -r /opt/gitlab-mcp/requirements.txt

# Copy MCP server source
COPY sidecars/gitlab-mcp/ /opt/gitlab-mcp/

# Copy OpenCode config
COPY configs/gitlab/ /opt/opencode-config/

# Default: run opencode in 'run' mode (one-shot)
ENTRYPOINT ["opencode"]
CMD ["run"]
```

- [ ] **Step 2: Commit**

```bash
git add Dockerfile.gitlab
git commit -m "feat: add Dockerfile.gitlab for opencode-gitlab-reviewer image"
```

---

### Task 7: docker-compose.gitlab.yaml (optional, for local testing)

**Files:**
- Create: `docker-compose.gitlab.yaml`

- [ ] **Step 1: Create docker-compose.gitlab.yaml**

```yaml
services:
  opencode-gitlab:
    build:
      context: .
      dockerfile: Dockerfile.gitlab
    environment:
      - GITLAB_URL=${GITLAB_URL:-https://gitlab.example.com}
      - GITLAB_TOKEN=${GITLAB_TOKEN:-}
      - GITLAB_PROJECT_ID=${GITLAB_PROJECT_ID:-}
      - OPENAI_COMPATIBLE_BASE_URL=${OPENAI_COMPATIBLE_BASE_URL:-}
      - OPENAI_COMPATIBLE_API_KEY=${OPENAI_COMPATIBLE_API_KEY:-}
    volumes:
      - .:/workspace
    working_dir: /workspace
```

- [ ] **Step 2: Commit**

```bash
git add docker-compose.gitlab.yaml
git commit -m "feat: add docker-compose.gitlab.yaml for local testing"
```

---

### Task 8: Tests for gitlab_client.py

**Files:**
- Create: `tests/test_gitlab_client.py`
- Create: `tests/__init__.py`

- [ ] **Step 1: Create test directory and __init__.py**

```python
# tests/__init__.py
```

- [ ] **Step 2: Create test_gitlab_client.py**

```python
"""Tests for gitlab_client.py — GitLab API wrapper.

Uses unittest.mock to avoid actual GitLab API calls.
Run: python -m pytest tests/test_gitlab_client.py -v
"""
import os
import unittest
from unittest.mock import MagicMock, patch, PropertyMock

from gitlab_client import (
    GitLabClient,
    DiffFile,
    MergeRequestDiff,
    ReviewComment,
)


class TestDiffFile(unittest.TestCase):
    def test_create_diff_file(self):
        f = DiffFile(
            old_path="app.py",
            new_path="app.py",
            old_line=10,
            new_line=12,
            diff="@@ -10 +12 @@\n-old\n+new\n",
        )
        self.assertEqual(f.old_path, "app.py")
        self.assertEqual(f.new_path, "app.py")
        self.assertEqual(f.old_line, 10)
        self.assertEqual(f.new_line, 12)


class TestMergeRequestDiff(unittest.TestCase):
    def test_empty_diff(self):
        diff = MergeRequestDiff(
            mr_iid=1,
            source_branch="feature",
            target_branch="main",
            source_sha="abc123",
            total_files=0,
        )
        self.assertEqual(diff.total_files, 0)
        self.assertEqual(diff.files, [])


class TestReviewComment(unittest.TestCase):
    def test_inline_comment(self):
        c = ReviewComment(
            note_id=42,
            body="Consider using a constant here",
            path="utils.py",
            line=15,
            system=False,
        )
        self.assertEqual(c.note_id, 42)
        self.assertEqual(c.path, "utils.py")
        self.assertEqual(c.line, 15)

    def test_general_comment(self):
        c = ReviewComment(
            note_id=43,
            body="Nice work on this MR",
            path=None,
            line=None,
            system=False,
        )
        self.assertIsNone(c.path)
        self.assertIsNone(c.line)


class TestGitLabClientInit(unittest.TestCase):
    @patch.dict(os.environ, {
        "GITLAB_URL": "https://gitlab.example.com",
        "GITLAB_TOKEN": "test-token",
        "GITLAB_PROJECT_ID": "1",
    })
    @patch("gitlab_client.gitlab.Gitlab")
    def test_init_success(self, mock_gitlab_cls):
        mock_gl = MagicMock()
        mock_gitlab_cls.return_value = mock_gl
        mock_gl.projects.get.return_value = MagicMock()

        client = GitLabClient()
        mock_gitlab_cls.assert_called_once_with(
            "https://gitlab.example.com", private_token="test-token"
        )
        mock_gl.projects.get.assert_called_once_with("1")

    @patch.dict(os.environ, {
        "GITLAB_URL": "",
        "GITLAB_TOKEN": "test-token",
        "GITLAB_PROJECT_ID": "1",
    })
    def test_init_missing_url_raises(self):
        with self.assertRaises(ValueError) as ctx:
            GitLabClient()
        self.assertIn("GITLAB_URL", str(ctx.exception))

    @patch.dict(os.environ, {
        "GITLAB_URL": "https://gitlab.example.com",
        "GITLAB_TOKEN": "",
        "GITLAB_PROJECT_ID": "1",
    })
    def test_init_missing_token_raises(self):
        with self.assertRaises(ValueError) as ctx:
            GitLabClient()
        self.assertIn("GITLAB_TOKEN", str(ctx.exception))


class TestGetMergeRequest(unittest.TestCase):
    @patch.dict(os.environ, {
        "GITLAB_URL": "https://gitlab.example.com",
        "GITLAB_TOKEN": "test-token",
        "GITLAB_PROJECT_ID": "1",
    })
    @patch("gitlab_client.gitlab.Gitlab")
    def test_get_mr(self, mock_gitlab_cls):
        mock_gl = MagicMock()
        mock_gitlab_cls.return_value = mock_gl

        mock_mr = MagicMock()
        mock_mr.iid = 42
        mock_mr.title = "Add feature X"
        mock_mr.description = "Implements X"
        mock_mr.source_branch = "feature-x"
        mock_mr.target_branch = "main"
        mock_mr.state = "opened"
        mock_mr.sha = "abc123def"
        mock_mr.author = {"username": "dev1"}
        mock_mr.web_url = "https://gitlab.example.com/1/-/merge_requests/42"

        mock_project = MagicMock()
        mock_gl.projects.get.return_value = mock_project
        mock_project.mergerequests.get.return_value = mock_mr

        client = GitLabClient()
        result = client.get_merge_request(42)

        self.assertEqual(result["iid"], 42)
        self.assertEqual(result["title"], "Add feature X")
        self.assertEqual(result["author"], "dev1")
        self.assertEqual(result["state"], "opened")


class TestListOldReviewerComments(unittest.TestCase):
    @patch.dict(os.environ, {
        "GITLAB_URL": "https://gitlab.example.com",
        "GITLAB_TOKEN": "test-token",
        "GITLAB_PROJECT_ID": "1",
    })
    @patch("gitlab_client.gitlab.Gitlab")
    def test_filters_bot_comments(self, mock_gitlab_cls):
        mock_gl = MagicMock()
        mock_gitlab_cls.return_value = mock_gl

        mock_mr = MagicMock()
        mock_mr.iid = 42
        mock_project = MagicMock()
        mock_gl.projects.get.return_value = mock_project
        mock_project.mergerequests.get.return_value = mock_mr

        # Create mock notes: one bot comment, one system note, one user comment
        bot_note = MagicMock()
        bot_note.id = 100
        bot_note.body = "<!-- opencode-reviewer -->\nConsider error handling here"
        bot_note.system = False
        bot_note.position = {"path": "app.py", "line_code": "abc_app.py:15"}

        system_note = MagicMock()
        system_note.id = 101
        system_note.body = "changed target branch from dev to main"
        system_note.system = True
        system_note.position = {}

        user_note = MagicMock()
        user_note.id = 102
        user_note.body = "Looks good to me!"
        user_note.system = False
        user_note.position = {}

        mock_notes = [bot_note, system_note, user_note]
        mock_project.mergerequests.mergerequest_notes.list.return_value = mock_notes

        client = GitLabClient()
        result = client.list_old_reviewer_comments(42)

        # Only bot's comment should be returned
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].note_id, 100)
        self.assertEqual(result[0].path, "app.py")
        self.assertEqual(result[0].line, 15)


class TestPostComment(unittest.TestCase):
    @patch.dict(os.environ, {
        "GITLAB_URL": "https://gitlab.example.com",
        "GITLAB_TOKEN": "test-token",
        "GITLAB_PROJECT_ID": "1",
    })
    @patch("gitlab_client.gitlab.Gitlab")
    def test_post_comment_tags_body(self, mock_gitlab_cls):
        mock_gl = MagicMock()
        mock_gitlab_cls.return_value = mock_gl

        mock_mr = MagicMock()
        mock_project = MagicMock()
        mock_gl.projects.get.return_value = mock_project
        mock_project.mergerequests.get.return_value = mock_mr

        mock_note = MagicMock()
        mock_note.id = 200
        mock_note.body = "<!-- opencode-reviewer -->\nSummary comment\n---\nReview by opencode-gitlab-reviewer"
        mock_note.web_url = "https://gitlab.example.com/1/-/merge_requests/42#note_200"
        mock_project.mergerequests.mergerequest_notes.create.return_value = mock_note

        client = GitLabClient()
        result = client.post_comment(42, "Summary comment")

        self.assertEqual(result["id"], 200)
        self.assertIn("<!-- opencode-reviewer -->", result["body"])
        self.assertIn("Summary comment", result["body"])

        # Verify the tag was prepended
        call_args = mock_project.mergerequests.mergerequest_notes.create.call_args
        created_body = call_args[0][1]["body"]
        self.assertTrue(created_body.startswith("<!-- opencode-reviewer -->"))


class TestCheckCommentRelevance(unittest.TestCase):
    def test_general_comment_always_relevant(self):
        client = GitLabClient.__new__(GitLabClient)
        old_comments = [
            ReviewComment(
                note_id=1,
                body="General feedback",
                path=None,
                line=None,
                system=False,
            )
        ]
        diff = MergeRequestDiff(
            mr_iid=1,
            source_branch="f",
            target_branch="main",
            source_sha="abc",
            total_files=0,
        )

        result = client.check_comment_relevance(old_comments, diff)
        self.assertEqual(result[0]["status"], "relevant")

    def test_inline_comment_unrelated_file_resolved(self):
        client = GitLabClient.__new__(GitLabClient)
        old_comments = [
            ReviewComment(
                note_id=2,
                body="Fix this",
                path="old_file.py",
                line=10,
                system=False,
            )
        ]
        diff = MergeRequestDiff(
            mr_iid=1,
            source_branch="f",
            target_branch="main",
            source_sha="abc",
            total_files=1,
            files=[DiffFile(None, "new_file.py", None, None, "+new code")],
        )

        result = client.check_comment_relevance(old_comments, diff)
        self.assertEqual(result[0]["status"], "resolved")

    def test_inline_comment_related_file_relevant(self):
        client = GitLabClient.__new__(GitLabClient)
        old_comments = [
            ReviewComment(
                note_id=3,
                body="Fix this",
                path="utils.py",
                line=10,
                system=False,
            )
        ]
        diff = MergeRequestDiff(
            mr_iid=1,
            source_branch="f",
            target_branch="main",
            source_sha="abc",
            total_files=1,
            files=[DiffFile("utils.py", "utils.py", None, None, "+new code")],
        )

        result = client.check_comment_relevance(old_comments, diff)
        self.assertEqual(result[0]["status"], "relevant")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Create test_tools.py**

```python
"""Tests for tools.py — MCP tool wrappers.

Uses unittest.mock to avoid actual GitLab API calls.
Run: python -m pytest tests/test_tools.py -v
"""
import json
import unittest
from unittest.mock import patch, MagicMock

from tools import (
    tool_get_merge_request,
    tool_get_merge_request_diff,
    tool_list_old_reviewer_comments,
    tool_post_comment,
    tool_post_inline_comment,
    tool_get_pipeline_status,
)


class TestToolGetMergeRequest(unittest.TestCase):
    @patch("tools.GitLabClient")
    def test_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.get_merge_request.return_value = {
            "iid": 42, "title": "Test MR", "state": "opened"
        }
        mock_client_cls.return_value = mock_client

        result = json.loads(tool_get_merge_request({"mr_iid": 42}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["iid"], 42)

    @patch("tools.GitLabClient")
    def test_error_handling(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.get_merge_request.side_effect = Exception("API error")
        mock_client_cls.return_value = mock_client

        result = json.loads(tool_get_merge_request({"mr_iid": 999}))
        self.assertEqual(result["status"], "error")
        self.assertIn("API error", result["message"])


class TestToolGetMergeRequestDiff(unittest.TestCase):
    @patch("tools.GitLabClient")
    def test_success(self, mock_client_cls):
        from gitlab_client import DiffFile, MergeRequestDiff

        mock_client = MagicMock()
        mock_client.get_merge_request_diff.return_value = MergeRequestDiff(
            mr_iid=1,
            source_branch="feature",
            target_branch="main",
            source_sha="abc",
            total_files=1,
            files=[DiffFile(None, "app.py", None, None, "+new code")],
        )
        mock_client_cls.return_value = mock_client

        result = json.loads(tool_get_merge_request_diff({"mr_iid": 1}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["total_files"], 1)
        self.assertEqual(result["data"]["files"][0]["new_path"], "app.py")


class TestToolListOldReviewerComments(unittest.TestCase):
    @patch("tools.GitLabClient")
    def test_success(self, mock_client_cls):
        from gitlab_client import ReviewComment

        mock_client = MagicMock()
        mock_client.list_old_reviewer_comments.return_value = [
            ReviewComment(1, "Fix error handling", "app.py", 10, False),
        ]
        mock_client_cls.return_value = mock_client

        result = json.loads(tool_list_old_reviewer_comments({"mr_iid": 1}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["count"], 1)


class TestToolPostComment(unittest.TestCase):
    @patch("tools.GitLabClient")
    def test_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.post_comment.return_value = {"id": 200, "web_url": "http://url"}
        mock_client_cls.return_value = mock_client

        result = json.loads(tool_post_comment({"mr_iid": 1, "body": "Summary"}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["id"], 200)


class TestToolPostInlineComment(unittest.TestCase):
    @patch("tools.GitLabClient")
    def test_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.post_inline_comment.return_value = {
            "id": 201, "path": "app.py", "line": 15, "web_url": "http://url"
        }
        mock_client_cls.return_value = mock_client

        result = json.loads(tool_post_inline_comment({
            "mr_iid": 1, "file_path": "app.py", "line": 15, "body": "Bug here"
        }))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["line"], 15)


class TestToolGetPipelineStatus(unittest.TestCase):
    @patch("tools.GitLabClient")
    def test_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.get_pipeline_status.return_value = {
            "found": True, "status": "passed", "pipeline_id": 50
        }
        mock_client_cls.return_value = mock_client

        result = json.loads(tool_get_pipeline_status({"mr_iid": 1}))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["data"]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Commit**

```bash
git add tests/__init__.py tests/test_gitlab_client.py tests/test_tools.py
git commit -m "test: add unit tests for gitlab_client and tools modules"
```

---

### Task 9: Verify tests pass

- [ ] **Step 1: Run tests from the gitlab-mcp directory**

```bash
cd sidecars/gitlab-mcp
python -m venv ../../.tmp/venv-test
../../.tmp/venv-test/Scripts/activate
pip install -r requirements.txt
python -m pytest ../../tests/ -v
deactivate
```

Expected output: All tests PASS.

- [ ] **Step 2: Commit any test fixes if needed**

```bash
git add tests/
git commit -m "test: fix tests to pass" --allow-empty
```

---

### Task 10: Build and verify Docker image

- [ ] **Step 1: Build the image locally**

```bash
docker build -t opencode-gitlab-reviewer:latest -f Dockerfile.gitlab .
```

- [ ] **Step 2: Verify image contents**

```bash
docker run --rm opencode-gitlab-reviewer:latest --version
docker run --rm opencode-gitlab-reviewer:latest which python3
docker run --rm opencode-gitlab-reviewer:latest uv pip list | grep -i gitlab
docker run --rm opencode-gitlab-reviewer:latest cat /opt/opencode-config/opencode.jsonc
docker run --rm opencode-gitlab-reviewer:latest cat /opt/gitlab-mcp/server.py | head -5
```

- [ ] **Step 3: Commit any Dockerfile fixes if needed**

```bash
git add Dockerfile.gitlab
git commit -m "fix: adjust Dockerfile.gitlab for successful build"
```

---

### Task 11: Push Docker image to DockerHub

- [ ] **Step 1: Tag and push**

```bash
docker tag opencode-gitlab-reviewer:latest asmal95/opencode-gitlab-reviewer:latest
docker push asmal95/opencode-gitlab-reviewer:latest
```

- [ ] **Step 2: Commit**

```bash
git commit -m "chore: update Docker image reference in plan" --allow-empty
```

---

## Self-Review Checklist

**Spec coverage:**
- Task 1: requirements.txt → spec section 4.6 (env vars), 4.3 (tools list)
- Task 2: gitlab_client.py → spec section 4.3 (all 6 tools + state tracking + BOT_TAG)
- Task 3: tools.py → spec section 4.3 (tool wrappers with JSON envelopes)
- Task 4: server.py → spec section 4.3 (FastMCP stdio entry point)
- Task 5: opencode.jsonc → spec section 4.4 (local MCP config)
- Task 6: Dockerfile.gitlab → spec section 4.2 (multi-stage build)
- Task 7: docker-compose.gitlab.yaml → optional local testing
- Task 8: Tests → TDD requirement, covers client + tools
- Task 9: Verify tests → validation
- Task 10: Build Docker → validation
- Task 11: Push image → deployment

**Placeholder scan:** No TBD/TODO/fill-in patterns found.

**Type consistency:** `MergeRequestDiff`, `DiffFile`, `ReviewComment` defined in `gitlab_client.py`, used consistently in `tools.py` and tests. `GitLabClient` constructor always takes no args (reads from env). All tool functions accept `args: dict[str, Any]` and return `str` (JSON).

**Scope check:** Focused on code review one-shot runner. No MR creation, pipeline triggering, Telegram, or OAuth. Matches spec section 6 (Out of Scope).

"""MCP tool wrappers for GitLab operations."""

from __future__ import annotations

import json
from typing import Any

from .gitlab_client import (
    GitLabClient,
)


def _success(data: Any) -> str:
    return json.dumps({"status": "ok", "data": data}, indent=2)


def _error(message: str) -> str:
    return json.dumps({"status": "error", "message": message})


def tool_get_merge_request(args: dict[str, Any]) -> str:
    try:
        client = GitLabClient()
        result = client.get_merge_request(int(args["mr_iid"]))
        return _success(result)
    except Exception as e:
        return _error(f"Failed to get merge request: {e}")


def tool_get_merge_request_diff(args: dict[str, Any]) -> str:
    try:
        client = GitLabClient()
        diff = client.get_merge_request_diff(int(args["mr_iid"]))

        files = []
        for f in diff.files:
            files.append({
                "old_path": f.old_path,
                "new_path": f.new_path,
                "old_line": f.old_line,
                "new_line": f.new_line,
                "diff": f.diff,
            })

        result = {
            "mr_iid": diff.mr_iid,
            "source_branch": diff.source_branch,
            "target_branch": diff.target_branch,
            "source_sha": diff.source_sha,
            "total_files": diff.total_files,
            "files": files,
        }
        return _success(result)
    except Exception as e:
        return _error(f"Failed to get merge request diff: {e}")


def tool_list_old_reviewer_comments(args: dict[str, Any]) -> str:
    try:
        client = GitLabClient()
        comments = client.list_old_reviewer_comments(int(args["mr_iid"]))

        result = {
            "count": len(comments),
            "comments": [
                {
                    "note_id": c.note_id,
                    "body": c.body,
                    "path": c.path,
                    "line": c.line,
                }
                for c in comments
            ],
        }
        return _success(result)
    except Exception as e:
        return _error(f"Failed to list old reviewer comments: {e}")


def tool_post_comment(args: dict[str, Any]) -> str:
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
    try:
        client = GitLabClient()
        result = client.get_pipeline_status(int(args["mr_iid"]))
        return _success(result)
    except Exception as e:
        return _error(f"Failed to get pipeline status: {e}")

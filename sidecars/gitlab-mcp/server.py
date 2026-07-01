#!/usr/bin/env python3
"""GitLab MCP server for OpenCode code review.

Runs as a stdio-based MCP server (default FastMCP transport).
OpenCode spawns this as a local subprocess and communicates via stdin/stdout.
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

    Args:
        mr_iid: The merge request IID (integer)

    Returns:
        JSON string with structured diff data (files, patches, line numbers)
    """
    return tool_get_merge_request_diff({"mr_iid": mr_iid})


@mcp.tool()
def list_old_reviewer_comments(mr_iid: int) -> str:
    """List all previous comments made by this bot on the merge request.

    Only returns the bot's own comments (identified by HTML tag marker).
    Use this BEFORE posting new comments to avoid repeating feedback.

    Args:
        mr_iid: The merge request IID (integer)

    Returns:
        JSON string with list of previous bot comments (body, path, line)
    """
    return tool_list_old_reviewer_comments({"mr_iid": mr_iid})


@mcp.tool()
def post_comment(mr_iid: int, body: str) -> str:
    """Post a general (non-inline) comment to the merge request.

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

    Args:
        mr_iid: The merge request IID (integer)
        file_path: Path to the file in the new version (from diff)
        line: Line number in the new file (1-based)
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

    Args:
        mr_iid: The merge request IID (integer)

    Returns:
        JSON string with pipeline status and job list
    """
    return tool_get_pipeline_status({"mr_iid": mr_iid})


if __name__ == "__main__":
    logger.info("Starting GitLab MCP server (stdio transport)")
    mcp.run()

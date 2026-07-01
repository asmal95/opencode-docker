"""GitLab API client wrapper for opencode reviewer.

Based on proven patterns from production GitLab CI review bots.
Uses python-gitlab with retry logic, mr.changes() for rich diff data,
and discussions API for reliable inline comments.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import gitlab as gitlab_sdk
from gitlab.exceptions import GitlabError
from unittest.mock import MagicMock


logger = logging.getLogger(__name__)


BOT_TAG = "<!-- opencode-reviewer -->"

DEFAULT_IGNORE_PATTERNS: tuple[str, ...] = (
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.lock",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "poetry.lock",
    "Gemfile.lock",
    "Cargo.lock",
    "go.sum",
    "composer.lock",
    "pipfile.lock",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.svg",
    "*.pdf",
    "*.zip",
    "*.tar.gz",
    "*.whl",
    "*.exe",
    "*.dll",
    "*.so",
    "*.dylib",
    "node_modules/**",
    ".git/**",
    "__pycache__/**",
    "*.pyc",
    "*.pyo",
)


def _should_retry_gitlab_error(exception: GitlabError) -> bool:
    """Check if a GitlabError should be retried.

    Retries on 429 (rate limit) and 5xx (server errors).
    Does not retry on 401/403 (auth) or most 4xx (client errors).
    """
    status_code = (
        getattr(exception, "response_code", None) if hasattr(exception, "response_code") else None
    )
    if status_code is None:
        msg = str(exception).lower()
        return any(marker in msg for marker in (
            "timeout", "timed out", "temporarily unavailable",
            "connection reset", "connection aborted",
            "connection refused", "service unavailable",
            "bad gateway", "gateway timeout",
        ))
    if status_code in (401, 403):
        return False
    if 400 <= status_code < 500 and status_code != 429:
        return False
    return True


def _decode_file_content(file_obj: Any) -> str | None:
    """Decode file content from python-gitlab API response.

    Handles bytes, Base64 strings, and various object types
    that python-gitlab returns depending on the API endpoint.
    Returns None for binary content that can't be decoded as UTF-8.
    Returns None if the object is a mock or has no real content.
    """
    if isinstance(file_obj, bytes):
        try:
            return file_obj.decode("utf-8")
        except UnicodeDecodeError:
            return None

    if isinstance(file_obj, str):
        return file_obj

    # If the object itself is a mock (no real content), return None
    # Real objects from python-gitlab are not MagicMock instances
    if isinstance(file_obj, MagicMock):
        return None

    if hasattr(file_obj, "content"):
        content = file_obj.content
        # Only process if content is a real value, not a mock
        if not (isinstance(content, MagicMock)):
            if isinstance(content, bytes):
                try:
                    return base64.b64decode(content).decode("utf-8")
                except Exception:
                    try:
                        return content.decode("utf-8")
                    except UnicodeDecodeError:
                        return None
            elif isinstance(content, str):
                try:
                    return base64.b64decode(content).decode("utf-8")
                except Exception:
                    return content

    if hasattr(file_obj, "decode_bytes") and callable(getattr(file_obj, "decode_bytes")):
        try:
            decoded = file_obj.decode_bytes()
            if isinstance(decoded, bytes):
                return decoded.decode("utf-8")
            return decoded
        except Exception:
            pass

    if hasattr(file_obj, "decode") and callable(getattr(file_obj, "decode")):
        try:
            decoded = file_obj.decode()
            if isinstance(decoded, bytes):
                return decoded.decode("utf-8")
            return decoded
        except Exception:
            pass

    if hasattr(file_obj, "data"):
        data = file_obj.data
        if not (isinstance(data, MagicMock)):
            if isinstance(data, bytes):
                return data.decode("utf-8")
            return str(data)

    return None


# ------------------------------------------------------------------
# Data models
# ------------------------------------------------------------------

@dataclass
class DiffFile:
    """A file change from a merge request diff."""
    old_path: str | None
    new_path: str | None
    old_line: int | None
    new_line: int | None
    diff: str = ""
    new_content: str | None = None
    status: str = "modified"  # modified, added, deleted, renamed


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
    """A single review comment from the bot."""
    note_id: int
    body: str
    path: str | None
    line: int | None
    system: bool = False


# ------------------------------------------------------------------
# Client
# ------------------------------------------------------------------

class GitLabClient:
    """Client for GitLab API operations with retry and rich diff support."""

    def __init__(self) -> None:
        url = os.environ.get("GITLAB_URL")
        token = os.environ.get("GITLAB_TOKEN")
        project_id = os.environ.get("GITLAB_PROJECT_ID")

        missing = [name for name, value in [
            ("GITLAB_URL", url),
            ("GITLAB_TOKEN", token),
            ("GITLAB_PROJECT_ID", project_id),
        ] if not value]

        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )

        self.gl = gitlab_sdk.Gitlab(url, private_token=token)
        self.project_id: str = project_id  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_project(self) -> Any:
        """Get GitLab project, with retry on transient errors."""
        try:
            return self.gl.projects.get(self.project_id)
        except GitlabError as e:
            if _should_retry_gitlab_error(e):
                return self.gl.projects.get(self.project_id)
            raise

    def _get_mr(self, project: Any, mr_iid: int) -> Any:
        """Get merge request, with retry on transient errors."""
        try:
            return project.mergerequests.get(mr_iid)
        except GitlabError as e:
            if _should_retry_gitlab_error(e):
                return project.mergerequests.get(mr_iid)
            raise

    @staticmethod
    def _calculate_line_code(file_path: str, line_number: int) -> str:
        """Calculate GitLab line_code for inline comments.

        Format: <sha1(file_path)>_<line>_<line>
        """
        file_sha1 = hashlib.sha1(file_path.encode("utf-8")).hexdigest()
        return f"{file_sha1}_{line_number}_{line_number}"

    @staticmethod
    def _is_binary_content(content: str | None) -> bool:
        """Check if file content is binary (not UTF-8 decodable)."""
        if not content:
            return True
        try:
            content.encode("utf-8")
            return False
        except (UnicodeEncodeError, UnicodeDecodeError):
            return True

    # ------------------------------------------------------------------
    # Merge requests
    # ------------------------------------------------------------------

    def get_merge_request(self, mr_iid: int) -> dict[str, Any]:
        """Get merge request details."""
        project = self._get_project()
        mr = self._get_mr(project, mr_iid)

        author = mr.author
        author_data = {
            "id": author.id,
            "username": author.username,
            "name": author.name,
        }

        return {
            "iid": mr.iid,
            "title": mr.title,
            "description": mr.description or "",
            "source_branch": mr.source_branch,
            "target_branch": mr.target_branch,
            "state": mr.state,
            "sha": mr.sha,
            "author": author_data,
            "web_url": mr.web_url,
        }

    # ------------------------------------------------------------------
    # Diffs — using mr.changes() for rich data with file content
    # ------------------------------------------------------------------

    def get_merge_request_diff(self, mr_iid: int) -> MergeRequestDiff:
        """Get MR diff using mr.changes() for richer data.

        Unlike diff_revisions which returns unified diff strings,
        mr.changes() provides parsed FileChange objects with status
        (added/modified/deleted/renamed), parsed hunks, and optionally
        full file content fetched via repository_blob/files.get.
        """
        project = self._get_project()
        mr = self._get_mr(project, mr_iid)

        try:
            changes = mr.changes()
        except GitlabError as e:
            if _should_retry_gitlab_error(e):
                changes = mr.changes()
            else:
                raise

        diff_files: list[DiffFile] = []
        for change in changes.get("changes", []):
            old_path = change.get("old_path")
            new_path = change.get("new_path")
            diff_text = change.get("diff", "")

            # Determine status
            if not old_path:
                status = "added"
            elif not new_path:
                status = "deleted"
            elif old_path != new_path:
                status = "renamed"
            else:
                status = "modified"

            # Fetch full file content for context (new/modified files)
            new_content = None
            if new_path and status != "deleted":
                new_content = self._fetch_file_content(project, new_path, mr)

            diff_files.append(DiffFile(
                old_path=old_path,
                new_path=new_path,
                old_line=change.get("old_line"),
                new_line=change.get("new_line"),
                diff=diff_text,
                new_content=new_content,
                status=status,
            ))

        return MergeRequestDiff(
            mr_iid=mr_iid,
            source_branch=mr.source_branch,
            target_branch=mr.target_branch,
            source_sha=mr.sha,
            total_files=len(diff_files),
            files=diff_files,
        )

    def _fetch_file_content(
        self, project: Any, file_path: str, mr: Any
    ) -> str | None:
        """Fetch full file content using multiple strategies.

        Tries repository_blob first (fast, direct), falls back to
        files.get (more reliable but slower).
        """
        # Try repository_blob with source branch
        if mr.source_branch:
            try:
                blob = project.repository_blob(file_path, ref=mr.source_branch)
                if blob:
                    decoded = _decode_file_content(blob)
                    if decoded and not self._is_binary_content(decoded):
                        return decoded
            except GitlabError as e:
                status = getattr(e, "response_code", None)
                if status == 404:
                    pass  # File doesn't exist in this ref, try next
                elif _should_retry_gitlab_error(e):
                    try:
                        blob = project.repository_blob(file_path, ref=mr.source_branch)
                        if blob:
                            decoded = _decode_file_content(blob)
                            if decoded and not self._is_binary_content(decoded):
                                return decoded
                    except Exception:
                        pass

        # Fallback: files.get with source branch
        if mr.source_branch:
            try:
                file_obj = project.files.get(file_path, ref=mr.source_branch)
                content = _decode_file_content(file_obj)
                if content and not self._is_binary_content(content):
                    return content
            except GitlabError as e:
                status = getattr(e, "response_code", None)
                if status == 404:
                    pass
                elif _should_retry_gitlab_error(e):
                    try:
                        file_obj = project.files.get(file_path, ref=mr.source_branch)
                        content = _decode_file_content(file_obj)
                        if content and not self._is_binary_content(content):
                            return content
                    except Exception:
                        pass

        # Last resort: try head_sha if different from source_branch
        head_sha = mr.diff_refs.get("head_sha") if hasattr(mr, "diff_refs") else None
        if head_sha and head_sha != mr.source_branch:
            try:
                blob = project.repository_blob(file_path, ref=head_sha)
                if blob:
                    decoded = _decode_file_content(blob)
                    if decoded and not self._is_binary_content(decoded):
                        return decoded
            except Exception:
                pass

        return None

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def list_old_reviewer_comments(self, mr_iid: int) -> list[ReviewComment]:
        """List bot's previous comments on the MR.

        Identifies bot comments by BOT_TAG HTML marker.
        Parses line_code for inline comment positioning.
        """
        project = self._get_project()
        mr = self._get_mr(project, mr_iid)

        try:
            notes = mr.notes.list(paranoia="visible", all=True)
        except GitlabError as e:
            if _should_retry_gitlab_error(e):
                notes = mr.notes.list(paranoia="visible", all=True)
            else:
                raise

        comments: list[ReviewComment] = []
        for note in notes:
            if note.system:
                continue
            if not isinstance(note.body, str):
                continue
            body = note.body.strip()
            if not body.startswith(BOT_TAG):
                continue

            clean_body = body[len(BOT_TAG):].strip()
            if clean_body.startswith(" "):
                clean_body = clean_body[1:]

            path: str | None = None
            line: int | None = None

            # Parse line_code: <sha>_<path>:<line>
            line_code = note.resolved_line_code or note.position.get("line_code", "")
            if line_code:
                match = re.match(
                    r"^(?P<sha>[a-f0-9]+)_(?P<path>.+):(?P<line>\d+)$",
                    line_code,
                )
                if match:
                    path = match.group("path")
                    line = int(match.group("line"))
                else:
                    # Fallback: try parsing position dict directly
                    pos = note.position or {}
                    path = pos.get("path") or pos.get("new_path")
                    line = pos.get("new_line") or pos.get("old_line")

            comments.append(
                ReviewComment(
                    note_id=note.id,
                    body=clean_body,
                    path=path,
                    line=line,
                    system=False,
                )
            )

        return comments

    def post_comment(self, mr_iid: int, body: str) -> dict[str, Any]:
        """Post a general (non-inline) comment to the MR."""
        project = self._get_project()
        mr = self._get_mr(project, mr_iid)

        full_body = (
            f"{BOT_TAG}\n{body}\n---\nReview by opencode-gitlab-reviewer"
        )

        try:
            note = mr.notes.create({"body": full_body})
        except GitlabError as e:
            if _should_retry_gitlab_error(e):
                note = mr.notes.create({"body": full_body})
            else:
                raise

        return {
            "id": note.id,
            "body": note.body,
            "created_at": note.created_at,
        }

    def post_inline_comment(
        self, mr_iid: int, file_path: str, line: int, body: str
    ) -> dict[str, Any]:
        """Post an inline comment using GitLab discussions API.

        Uses discussions.create() with line_code for reliable inline
        positioning. Falls back to general comment if line_code fails
        (line outside diff).
        """
        project = self._get_project()
        mr = self._get_mr(project, mr_iid)

        full_body = (
            f"{BOT_TAG}\n{body}\n---\nReview by opencode-gitlab-reviewer"
        )

        line_code = self._calculate_line_code(file_path, line)

        # Build position data for discussions API
        position = {
            "base_sha": mr.diff_refs["base_sha"],
            "start_sha": mr.diff_refs["start_sha"],
            "head_sha": mr.diff_refs["head_sha"],
            "new_path": file_path,
            "position_type": "text",
            "new_line": line,
            "line_code": line_code,
        }

        discussion_data = {"body": full_body, "position": position}

        try:
            discussion = mr.discussions.create(discussion_data)
            # discussions return a "notes" list with the first note
            note = discussion.notes[0] if hasattr(discussion, "notes") else discussion
            return {
                "id": note.id,
                "body": note.body,
                "created_at": note.created_at,
            }
        except GitlabError as e:
            error_msg = str(e).lower()
            if "line_code" in error_msg:
                # Line outside diff — fall back to general comment
                logger.debug(
                    f"Inline comment rejected for {file_path}:{line} "
                    f"(line outside diff), falling back to general comment"
                )
                try:
                    note = mr.notes.create({"body": full_body})
                    return {
                        "id": note.id,
                        "body": note.body,
                        "created_at": note.created_at,
                    }
                except GitlabError as e2:
                    if _should_retry_gitlab_error(e2):
                        note = mr.notes.create({"body": full_body})
                        return {
                            "id": note.id,
                            "body": note.body,
                            "created_at": note.created_at,
                        }
                    raise
            elif _should_retry_gitlab_error(e):
                try:
                    discussion = mr.discussions.create(discussion_data)
                    note = discussion.notes[0] if hasattr(discussion, "notes") else discussion
                    return {
                        "id": note.id,
                        "body": note.body,
                        "created_at": note.created_at,
                    }
                except Exception:
                    raise

            raise

    # ------------------------------------------------------------------
    # Pipelines
    # ------------------------------------------------------------------

    def get_pipeline_status(self, mr_iid: int) -> dict[str, Any]:
        """Get CI pipeline status for the MR."""
        project = self._get_project()
        mr = self._get_mr(project, mr_iid)

        try:
            pipelines = mr.pipelines.list(all=True)
        except GitlabError as e:
            if _should_retry_gitlab_error(e):
                pipelines = mr.pipelines.list(all=True)
            else:
                return {"status": "unknown", "jobs": []}

        if not pipelines:
            return {"status": "unknown", "jobs": []}

        latest = pipelines[0]
        try:
            jobs = project.pipelines.get(latest.id).jobs.list(all=True)
        except GitlabError as e:
            if _should_retry_gitlab_error(e):
                jobs = project.pipelines.get(latest.id).jobs.list(all=True)
            else:
                jobs = []

        jobs_data = [
            {
                "id": j.id,
                "name": j.name,
                "status": j.status,
                "stage": j.stage,
            }
            for j in jobs
        ]

        return {
            "status": latest.status,
            "pipeline_id": latest.id,
            "jobs": jobs_data,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_diff_hunks(diff_text: str) -> list[dict[str, Any]]:
        """Parse unified diff into hunk metadata."""
        hunks: list[dict[str, Any]] = []
        for match in re.finditer(
            r"@@\s+-(?P<old_start>\d+)(?:,\d+)?\s+\+(?P<new_start>\d+)(?:,\d+)?\s+@@",
            diff_text,
        ):
            hunks.append({
                "old_start": int(match.group("old_start")),
                "new_start": int(match.group("new_start")),
            })
        return hunks

    def check_comment_relevance(
        self, old_comments: list[ReviewComment], diff: MergeRequestDiff
    ) -> list[dict[str, Any]]:
        """Determine which old comments are still relevant in the new diff.

        General comments (line=None) are always relevant.
        Inline comments on files not in the diff are resolved.
        Inline comments on files in the diff are marked relevant.
        """
        diff_file_paths = {f.new_path for f in diff.files if f.new_path}

        results: list[dict[str, Any]] = []
        for comment in old_comments:
            if comment.line is None:
                results.append({
                    "note_id": comment.note_id,
                    "body": comment.body,
                    "path": comment.path,
                    "status": "relevant",
                })
            elif comment.path not in diff_file_paths:
                results.append({
                    "note_id": comment.note_id,
                    "body": comment.body,
                    "path": comment.path,
                    "status": "resolved",
                })
            else:
                results.append({
                    "note_id": comment.note_id,
                    "body": comment.body,
                    "path": comment.path,
                    "status": "relevant",
                })

        return results

"""GitLab API client wrapper for opencode reviewer."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any


BOT_TAG = "<!-- opencode-reviewer -->"

DIFF_LINE_RE = re.compile(
    r"@@\s+-(?P<old_start>\d+)(?:,\d+)?\s+\+(?P<new_start>\d+)(?:,\d+)?\s+@@"
)


@dataclass
class DiffFile:
    old_path: str | None
    new_path: str | None
    old_line: int | None
    new_line: int | None
    diff: str


@dataclass
class MergeRequestDiff:
    mr_iid: int
    source_branch: str
    target_branch: str
    source_sha: str
    total_files: int
    files: list[DiffFile] = field(default_factory=list)


@dataclass
class ReviewComment:
    note_id: int
    body: str
    path: str | None
    line: int | None
    system: bool = False


class GitLabClient:
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

        import gitlab as gitlab_sdk

        self.gl = gitlab_sdk.Gitlab(url, private_token=token)
        self.project_id: str = project_id  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Merge requests
    # ------------------------------------------------------------------

    def get_merge_request(self, mr_iid: int) -> dict[str, Any]:
        project = self.gl.projects.get(self.project_id)
        mr = project.mergerequests.get(mr_iid)

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
    # Diffs
    # ------------------------------------------------------------------

    def get_merge_request_diff(self, mr_iid: int) -> MergeRequestDiff:
        project = self.gl.projects.get(self.project_id)
        mr = project.mergerequests.get(mr_iid)

        diff = mr.diff_revisions().get(mr.max_diff_id, per_page=100)
        diff_files = diff.diff_files

        files: list[DiffFile] = []
        for f in diff_files:
            files.append(
                DiffFile(
                    old_path=f.get("old_path"),
                    new_path=f.get("new_path"),
                    old_line=f.get("old_line"),
                    new_line=f.get("new_line"),
                    diff=f.get("diff", ""),
                )
            )

        return MergeRequestDiff(
            mr_iid=mr_iid,
            source_branch=mr.source_branch,
            target_branch=mr.target_branch,
            source_sha=mr.sha,
            total_files=len(files),
            files=files,
        )

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    def list_old_reviewer_comments(self, mr_iid: int) -> list[ReviewComment]:
        project = self.gl.projects.get(self.project_id)
        mr = project.mergerequests.get(mr_iid)
        notes = mr.notes.list(paranoia="visible", all=True)

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

            line_code_match = re.match(
                r"^(?P<sha>[a-f0-9]+)_(?P<path>.+):(?P<line>\d+)$",
                note.resolved_line_code or "",
            )
            if line_code_match:
                path = line_code_match.group("path")
                line = int(line_code_match.group("line"))

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
        project = self.gl.projects.get(self.project_id)
        mr = project.mergerequests.get(mr_iid)

        full_body = (
            f"{BOT_TAG}\n{body}\n---\nReview by opencode-gitlab-reviewer"
        )

        note = mr.notes.create({"body": full_body})
        return {
            "id": note.id,
            "body": note.body,
            "created_at": note.created_at,
        }

    def post_inline_comment(
        self, mr_iid: int, file_path: str, line: int, body: str
    ) -> dict[str, Any]:
        project = self.gl.projects.get(self.project_id)
        mr = project.mergerequests.get(mr_iid)

        full_body = (
            f"{BOT_TAG}\n{body}\n---\nReview by opencode-gitlab-reviewer"
        )

        note = mr.notes.create(
            {
                "body": full_body,
                "position": {
                    "path": file_path,
                    "position_type": "text",
                    "new_line": line,
                },
            }
        )
        return {
            "id": note.id,
            "body": note.body,
            "created_at": note.created_at,
        }

    # ------------------------------------------------------------------
    # Pipelines
    # ------------------------------------------------------------------

    def get_pipeline_status(self, mr_iid: int) -> dict[str, Any]:
        project = self.gl.projects.get(self.project_id)
        mr = project.mergerequests.get(mr_iid)

        pipelines = mr.pipelines.list(all=True)
        if not pipelines:
            return {"status": "unknown", "jobs": []}

        latest = pipelines[0]
        jobs = project.pipelines.get(latest.id).jobs.list(all=True)

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
        hunks: list[dict[str, Any]] = []
        for match in DIFF_LINE_RE.finditer(diff_text):
            hunks.append(
                {
                    "old_start": int(match.group("old_start")),
                    "new_start": int(match.group("new_start")),
                }
            )
        return hunks

    def check_comment_relevance(
        self, old_comments: list[ReviewComment], diff: MergeRequestDiff
    ) -> list[dict[str, Any]]:
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

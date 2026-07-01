import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Pre-populate sys.modules so the lazy `import gitlab as gitlab_sdk` inside
# GitLabClient.__init__ succeeds.
_GITLAB_MOCK = MagicMock()
sys.modules["gitlab"] = _GITLAB_MOCK

# Load gitlab_client module directly from its file path.
_GITLAB_MCP_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "sidecars", "gitlab-mcp"
)
sys.path.insert(0, _GITLAB_MCP_DIR)

from gitlab_client import (  # noqa: E402
    BOT_TAG,
    DiffFile,
    GitLabClient,
    MergeRequestDiff,
    ReviewComment,
)


def _make_mock_mr():
    """Create a fully wired mock MR chain: mock_gl -> mock_project -> mock_mr."""
    mock_gl = MagicMock()
    mock_project = MagicMock()
    mock_mr = MagicMock()
    mock_gl.projects.get.return_value = mock_project
    mock_project.mergerequests.get.return_value = mock_mr
    return mock_gl, mock_project, mock_mr


_ENV = {
    "GITLAB_URL": "https://gitlab.example.com",
    "GITLAB_TOKEN": "test-token",
    "GITLAB_PROJECT_ID": "42",
}


class TestDiffFile(unittest.TestCase):
    def test_creation(self):
        df = DiffFile(
            old_path="old.txt",
            new_path="new.txt",
            old_line=1,
            new_line=2,
            diff="+1",
        )
        self.assertEqual(df.old_path, "old.txt")
        self.assertEqual(df.new_path, "new.txt")
        self.assertEqual(df.old_line, 1)
        self.assertEqual(df.new_line, 2)
        self.assertEqual(df.diff, "+1")

    def test_creation_with_nones(self):
        df = DiffFile(old_path=None, new_path=None, old_line=None, new_line=None, diff="")
        self.assertIsNone(df.old_path)
        self.assertIsNone(df.new_path)
        self.assertIsNone(df.old_line)
        self.assertIsNone(df.new_line)
        self.assertEqual(df.diff, "")


class TestMergeRequestDiff(unittest.TestCase):
    def test_empty_diff(self):
        diff = MergeRequestDiff(
            mr_iid=1,
            source_branch="feature",
            target_branch="main",
            source_sha="abc123",
            total_files=0,
        )
        self.assertEqual(diff.mr_iid, 1)
        self.assertEqual(diff.source_branch, "feature")
        self.assertEqual(diff.target_branch, "main")
        self.assertEqual(diff.source_sha, "abc123")
        self.assertEqual(diff.total_files, 0)
        self.assertEqual(diff.files, [])


class TestReviewComment(unittest.TestCase):
    def test_inline_comment(self):
        rc = ReviewComment(
            note_id=100,
            body="Nice change",
            path="src/main.py",
            line=42,
            system=False,
        )
        self.assertEqual(rc.note_id, 100)
        self.assertEqual(rc.body, "Nice change")
        self.assertEqual(rc.path, "src/main.py")
        self.assertEqual(rc.line, 42)
        self.assertFalse(rc.system)

    def test_general_comment(self):
        rc = ReviewComment(
            note_id=200,
            body="Overall looks good",
            path=None,
            line=None,
            system=False,
        )
        self.assertEqual(rc.note_id, 200)
        self.assertIsNone(rc.path)
        self.assertIsNone(rc.line)
        self.assertFalse(rc.system)


class TestGitLabClientInit(unittest.TestCase):
    @patch.dict(os.environ, _ENV)
    def test_success(self):
        with patch.object(_GITLAB_MOCK, "Gitlab") as MockGitlab:
            client = GitLabClient()
            self.assertIsNotNone(client.gl)
            self.assertEqual(client.project_id, "42")
            MockGitlab.assert_called_once_with(
                "https://gitlab.example.com", private_token="test-token"
            )

    @patch.dict(os.environ, {"GITLAB_TOKEN": "t", "GITLAB_PROJECT_ID": "1"}, clear=True)
    def test_missing_url_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            GitLabClient()
        self.assertIn("GITLAB_URL", str(ctx.exception))

    @patch.dict(os.environ, {"GITLAB_URL": "u", "GITLAB_PROJECT_ID": "1"}, clear=True)
    def test_missing_token_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            GitLabClient()
        self.assertIn("GITLAB_TOKEN", str(ctx.exception))


class TestGetMergeRequest(unittest.TestCase):
    @patch.dict(os.environ, _ENV)
    def test_author_returned_as_dict(self):
        with patch.object(_GITLAB_MOCK, "Gitlab") as MockGitlab:
            mock_gl, mock_project, mock_mr = _make_mock_mr()
            MockGitlab.return_value = mock_gl
            mock_mr.author.id = 1
            mock_mr.author.username = "alice"
            mock_mr.author.name = "Alice"
            mock_mr.iid = 5
            mock_mr.title = "Test MR"
            mock_mr.description = "A test"
            mock_mr.source_branch = "feature"
            mock_mr.target_branch = "main"
            mock_mr.state = "opened"
            mock_mr.sha = "deadbeef"
            mock_mr.web_url = "https://gitlab.example.com/-/merge_requests/5"

            client = GitLabClient()
            result = client.get_merge_request(5)

            self.assertEqual(result["iid"], 5)
            self.assertEqual(result["title"], "Test MR")
            self.assertEqual(result["sha"], "deadbeef")
            self.assertEqual(result["author"], {
                "id": 1,
                "username": "alice",
                "name": "Alice",
            })
            self.assertEqual(result["web_url"], "https://gitlab.example.com/-/merge_requests/5")


class TestListOldReviewerComments(unittest.TestCase):
    @patch.dict(os.environ, _ENV)
    def test_filtering_and_line_code_parsing(self):
        with patch.object(_GITLAB_MOCK, "Gitlab") as MockGitlab:
            mock_gl, mock_project, mock_mr = _make_mock_mr()
            MockGitlab.return_value = mock_gl

            bot_note = MagicMock()
            bot_note.id = 10
            bot_note.system = False
            bot_note.body = f"{BOT_TAG} Review comment\n"
            bot_note.resolved_line_code = "abc123_src/main.py:42"

            system_note = MagicMock()
            system_note.id = 11
            system_note.system = True
            system_note.body = "system note"

            user_note = MagicMock()
            user_note.id = 12
            user_note.system = False
            user_note.body = "Just a regular user comment"

            general_bot_note = MagicMock()
            general_bot_note.id = 13
            general_bot_note.system = False
            general_bot_note.body = f"{BOT_TAG} General feedback\n"
            general_bot_note.resolved_line_code = ""

            mock_mr.notes.list.return_value = [bot_note, system_note, user_note, general_bot_note]

            client = GitLabClient()
            comments = client.list_old_reviewer_comments(5)

            self.assertEqual(len(comments), 2)

            self.assertEqual(comments[0].note_id, 10)
            self.assertEqual(comments[0].body, "Review comment")
            self.assertEqual(comments[0].path, "src/main.py")
            self.assertEqual(comments[0].line, 42)
            self.assertFalse(comments[0].system)

            self.assertEqual(comments[1].note_id, 13)
            self.assertEqual(comments[1].body, "General feedback")
            self.assertIsNone(comments[1].path)
            self.assertIsNone(comments[1].line)


class TestPostComment(unittest.TestCase):
    @patch.dict(os.environ, _ENV)
    def test_bot_tag_prepended_and_returns_id_body_created_at(self):
        with patch.object(_GITLAB_MOCK, "Gitlab") as MockGitlab:
            mock_gl, mock_project, mock_mr = _make_mock_mr()
            MockGitlab.return_value = mock_gl

            mock_note = MagicMock()
            mock_note.id = 99
            mock_note.body = "some body"
            mock_note.created_at = "2025-01-01T00:00:00Z"
            mock_mr.notes.create.return_value = mock_note

            client = GitLabClient()
            result = client.post_comment(5, "Hello review")

            call_args = mock_mr.notes.create.call_args
            body_sent = call_args[0][0]["body"]
            self.assertTrue(body_sent.startswith(BOT_TAG))
            self.assertIn("Hello review", body_sent)
            self.assertIn("Review by opencode-gitlab-reviewer", body_sent)

            self.assertEqual(result["id"], 99)
            self.assertEqual(result["body"], "some body")
            self.assertEqual(result["created_at"], "2025-01-01T00:00:00Z")


class TestPostInlineComment(unittest.TestCase):
    @patch.dict(os.environ, _ENV)
    def test_position_params_passed_correctly(self):
        with patch.object(_GITLAB_MOCK, "Gitlab") as MockGitlab:
            mock_gl, mock_project, mock_mr = _make_mock_mr()
            MockGitlab.return_value = mock_gl

            mock_note = MagicMock()
            mock_note.id = 101
            mock_note.body = "body"
            mock_note.created_at = "2025-06-01T12:00:00Z"
            mock_mr.notes.create.return_value = mock_note

            client = GitLabClient()
            result = client.post_inline_comment(5, "src/app.py", 10, "Fix this")

            call_args = mock_mr.notes.create.call_args
            data_sent = call_args[0][0]
            self.assertTrue(data_sent["body"].startswith(BOT_TAG))
            self.assertIn("Fix this", data_sent["body"])

            position = data_sent["position"]
            self.assertEqual(position["path"], "src/app.py")
            self.assertEqual(position["position_type"], "text")
            self.assertEqual(position["new_line"], 10)

            self.assertEqual(result["id"], 101)
            self.assertEqual(result["created_at"], "2025-06-01T12:00:00Z")


class TestGetPipelineStatus(unittest.TestCase):
    @patch.dict(os.environ, _ENV)
    def test_status_and_pipeline_id_in_response(self):
        with patch.object(_GITLAB_MOCK, "Gitlab") as MockGitlab:
            mock_gl, mock_project, mock_mr = _make_mock_mr()
            MockGitlab.return_value = mock_gl

            mock_pipeline = MagicMock()
            mock_pipeline.id = 1000
            mock_pipeline.status = "success"
            mock_mr.pipelines.list.return_value = [mock_pipeline]

            mock_job = MagicMock()
            mock_job.id = 2000
            mock_job.name = "build"
            mock_job.status = "success"
            mock_job.stage = "build"
            mock_project.pipelines.get.return_value.jobs.list.return_value = [mock_job]

            client = GitLabClient()
            result = client.get_pipeline_status(5)

            self.assertEqual(result["status"], "success")
            self.assertEqual(result["pipeline_id"], 1000)
            self.assertIsInstance(result["jobs"], list)
            self.assertEqual(len(result["jobs"]), 1)
            self.assertEqual(result["jobs"][0]["name"], "build")


class TestCheckCommentRelevance(unittest.TestCase):
    @patch.dict(os.environ, _ENV)
    def test_general_comment_relevant(self):
        with patch.object(_GITLAB_MOCK, "Gitlab"):
            client = GitLabClient()
            comments = [
                ReviewComment(note_id=1, body="general", path=None, line=None)
            ]
            diff = MergeRequestDiff(
                mr_iid=1, source_branch="f", target_branch="m",
                source_sha="abc", total_files=0,
            )
            result = client.check_comment_relevance(comments, diff)
            self.assertEqual(result[0]["status"], "relevant")

    @patch.dict(os.environ, _ENV)
    def test_inline_comment_file_not_in_diff_resolved(self):
        with patch.object(_GITLAB_MOCK, "Gitlab"):
            client = GitLabClient()
            comments = [
                ReviewComment(note_id=2, body="inline", path="removed.py", line=5)
            ]
            diff = MergeRequestDiff(
                mr_iid=1, source_branch="f", target_branch="m",
                source_sha="abc", total_files=0,
            )
            result = client.check_comment_relevance(comments, diff)
            self.assertEqual(result[0]["status"], "resolved")

    @patch.dict(os.environ, _ENV)
    def test_inline_comment_file_in_diff_relevant(self):
        with patch.object(_GITLAB_MOCK, "Gitlab"):
            client = GitLabClient()
            comments = [
                ReviewComment(note_id=3, body="inline", path="src/main.py", line=10)
            ]
            diff = MergeRequestDiff(
                mr_iid=1, source_branch="f", target_branch="m",
                source_sha="abc", total_files=1,
                files=[DiffFile(old_path=None, new_path="src/main.py", old_line=1, new_line=1, diff="")],
            )
            result = client.check_comment_relevance(comments, diff)
            self.assertEqual(result[0]["status"], "relevant")


class TestParseDiffHunks(unittest.TestCase):
    def test_static_method_parsing(self):
        diff_text = """diff --git a/file.py b/file.py
@@ -1,4 +1,5 @@
 line1
+line2
 line3
@@ -10,3 +11,4 @@
 line10
+line11
 line12
"""
        hunks = GitLabClient._parse_diff_hunks(diff_text)
        self.assertEqual(len(hunks), 2)
        self.assertEqual(hunks[0]["old_start"], 1)
        self.assertEqual(hunks[0]["new_start"], 1)
        self.assertEqual(hunks[1]["old_start"], 10)
        self.assertEqual(hunks[1]["new_start"], 11)

    def test_empty_text(self):
        hunks = GitLabClient._parse_diff_hunks("")
        self.assertEqual(hunks, [])

    def test_callable_from_instance(self):
        client = GitLabClient.__new__(GitLabClient)
        hunks = client._parse_diff_hunks("@@ -1 +1 @@\n")
        self.assertEqual(len(hunks), 1)
        self.assertEqual(hunks[0]["old_start"], 1)
        self.assertEqual(hunks[0]["new_start"], 1)


if __name__ == "__main__":
    unittest.main()

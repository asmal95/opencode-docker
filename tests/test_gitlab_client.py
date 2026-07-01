import hashlib
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Pre-populate sys.modules so the lazy `import gitlab as gitlab_sdk` inside
# GitLabClient.__init__ succeeds.
_GITLAB_MOCK = MagicMock()
sys.modules["gitlab"] = _GITLAB_MOCK
sys.modules["gitlab.exceptions"] = MagicMock(GitlabError=Exception)

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
    _decode_file_content,
    _should_retry_gitlab_error,
)


def _make_mock_mr(**overrides):
    """Create a fully wired mock MR chain: mock_gl -> mock_project -> mock_mr."""
    mock_gl = MagicMock()
    mock_project = MagicMock()
    mock_mr = MagicMock()

    # Default diff_refs
    mock_mr.diff_refs = {
        "base_sha": "base123",
        "start_sha": "start123",
        "head_sha": "head123",
    }

    mock_gl.projects.get.return_value = mock_project
    mock_project.mergerequests.get.return_value = mock_mr

    for key, value in overrides.items():
        setattr(mock_mr, key, value)

    return mock_gl, mock_project, mock_mr


_ENV = {
    "GITLAB_URL": "https://gitlab.example.com",
    "GITLAB_TOKEN": "test-token",
    "GITLAB_PROJECT_ID": "42",
}


# ------------------------------------------------------------------
# Data model tests
# ------------------------------------------------------------------

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

    def test_default_values(self):
        df = DiffFile(old_path="a", new_path="a", old_line=1, new_line=1, diff="")
        self.assertEqual(df.status, "modified")
        self.assertIsNone(df.new_content)


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
        self.assertIsNone(rc.path)
        self.assertIsNone(rc.line)


# ------------------------------------------------------------------
# Client init
# ------------------------------------------------------------------

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


# ------------------------------------------------------------------
# MR details
# ------------------------------------------------------------------

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
            self.assertEqual(result["author"], {
                "id": 1,
                "username": "alice",
                "name": "Alice",
            })


# ------------------------------------------------------------------
# Diff — mr.changes()
# ------------------------------------------------------------------

class TestGetMergeRequestDiff(unittest.TestCase):
    @patch.dict(os.environ, _ENV)
    def test_uses_mr_changes_with_file_content(self):
        with patch.object(_GITLAB_MOCK, "Gitlab") as MockGitlab:
            mock_gl, mock_project, mock_mr = _make_mock_mr()
            MockGitlab.return_value = mock_gl

            # Mock mr.changes() response
            mock_mr.changes.return_value = {
                "changes": [
                    {
                        "old_path": None,
                        "new_path": "src/app.py",
                        "old_line": None,
                        "new_line": 1,
                        "diff": "@@ -0 +1 @@\n+new code",
                    },
                    {
                        "old_path": "old.py",
                        "new_path": None,
                        "old_line": 1,
                        "new_line": None,
                        "diff": "@@ -1 -1 @@\n-old code",
                    },
                    {
                        "old_path": "a.py",
                        "new_path": "b.py",
                        "old_line": 1,
                        "new_line": 1,
                        "diff": "@@ -1 +1 @@\n-a\n+b",
                    },
                ]
            }

            client = GitLabClient()
            result = client.get_merge_request_diff(5)

            # Verify mr.changes() was called
            mock_mr.changes.assert_called_once()

            self.assertEqual(result.total_files, 3)

            # First file: added
            added = result.files[0]
            self.assertEqual(added.status, "added")
            self.assertEqual(added.new_path, "src/app.py")
            self.assertIsNone(added.old_path)

            # Second file: deleted
            deleted = result.files[1]
            self.assertEqual(deleted.status, "deleted")

            # Third file: renamed
            renamed = result.files[2]
            self.assertEqual(renamed.status, "renamed")
            self.assertEqual(renamed.old_path, "a.py")
            self.assertEqual(renamed.new_path, "b.py")

    @patch.dict(os.environ, _ENV)
    def test_fetches_file_content_for_new_files(self):
        with patch.object(_GITLAB_MOCK, "Gitlab") as MockGitlab:
            mock_gl, mock_project, mock_mr = _make_mock_mr()
            MockGitlab.return_value = mock_gl

            mock_mr.changes.return_value = {
                "changes": [
                    {
                        "old_path": None,
                        "new_path": "src/new.py",
                        "old_line": None,
                        "new_line": 1,
                        "diff": "+content",
                    }
                ]
            }

            # Mock repository_blob to return a real object with base64 content
            import base64
            encoded = base64.b64encode(b"hello world").decode()

            class _Blob:
                content = encoded

            mock_project.repository_blob.return_value = _Blob()

            client = GitLabClient()
            result = client.get_merge_request_diff(5)

            # Should have fetched file content
            self.assertEqual(result.files[0].new_content, "hello world")

    @patch.dict(os.environ, _ENV)
    def test_skips_binary_files(self):
        with patch.object(_GITLAB_MOCK, "Gitlab") as MockGitlab:
            mock_gl, mock_project, mock_mr = _make_mock_mr()
            MockGitlab.return_value = mock_gl

            mock_mr.changes.return_value = {
                "changes": [
                    {
                        "old_path": None,
                        "new_path": "image.png",
                        "old_line": None,
                        "new_line": 1,
                        "diff": "",
                    }
                ]
            }

            # repository_blob returns bytes content (binary file)
            binary_blob = MagicMock()
            binary_blob.content = b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a"
            mock_project.repository_blob.return_value = binary_blob

            client = GitLabClient()
            result = client.get_merge_request_diff(5)

            # Binary content should not be fetched
            self.assertIsNone(result.files[0].new_content)


# ------------------------------------------------------------------
# Comments
# ------------------------------------------------------------------

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

            mock_mr.notes.list.return_value = [bot_note, system_note, user_note]

            client = GitLabClient()
            comments = client.list_old_reviewer_comments(5)

            self.assertEqual(len(comments), 1)
            self.assertEqual(comments[0].note_id, 10)
            self.assertEqual(comments[0].path, "src/main.py")
            self.assertEqual(comments[0].line, 42)


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
            self.assertEqual(result["created_at"], "2025-01-01T00:00:00Z")


class TestPostInlineComment(unittest.TestCase):
    @patch.dict(os.environ, _ENV)
    def test_uses_discussions_create_with_line_code(self):
        with patch.object(_GITLAB_MOCK, "Gitlab") as MockGitlab:
            mock_gl, mock_project, mock_mr = _make_mock_mr()
            MockGitlab.return_value = mock_gl

            # discussions.create returns an object with .notes[0]
            mock_note = MagicMock()
            mock_note.id = 200
            mock_note.body = "body"
            mock_note.created_at = "2025-06-01T12:00:00Z"
            mock_discussion = MagicMock()
            mock_discussion.notes = [mock_note]
            mock_mr.discussions.create.return_value = mock_discussion

            client = GitLabClient()
            result = client.post_inline_comment(5, "src/app.py", 10, "Fix this")

            # Verify discussions.create was called (not notes.create)
            mock_mr.discussions.create.assert_called_once()

            # Check position data
            call_args = mock_mr.discussions.create.call_args
            data_sent = call_args[0][0]
            self.assertIn("body", data_sent)
            self.assertIn("position", data_sent)

            position = data_sent["position"]
            self.assertEqual(position["new_path"], "src/app.py")
            self.assertEqual(position["new_line"], 10)
            self.assertEqual(position["position_type"], "text")
            self.assertEqual(position["line_code"],
                             f"{hashlib.sha1(b'src/app.py').hexdigest()}_10_10")

            # Verify diff_refs were used
            self.assertEqual(position["base_sha"], "base123")
            self.assertEqual(position["head_sha"], "head123")

            self.assertEqual(result["id"], 200)

    @patch.dict(os.environ, _ENV)
    def test_fallback_to_general_comment_on_line_code_error(self):
        with patch.object(_GITLAB_MOCK, "Gitlab") as MockGitlab:
            mock_gl, mock_project, mock_mr = _make_mock_mr()
            MockGitlab.return_value = mock_gl

            # discussions.create fails with line_code error
            from gitlab.exceptions import GitlabError
            mock_mr.discussions.create.side_effect = GitlabError("line_code validation failed")

            # Fallback: notes.create succeeds
            mock_note = MagicMock()
            mock_note.id = 201
            mock_note.body = "body"
            mock_note.created_at = "2025-06-01T12:00:00Z"
            mock_mr.notes.create.return_value = mock_note

            client = GitLabClient()
            result = client.post_inline_comment(5, "src/app.py", 9999, "Fix")

            # Should have fallen back to general comment
            self.assertEqual(result["id"], 201)
            mock_mr.notes.create.assert_called_once()


# ------------------------------------------------------------------
# Pipeline
# ------------------------------------------------------------------

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
            self.assertEqual(result["jobs"][0]["name"], "build")

    @patch.dict(os.environ, _ENV)
    def test_no_pipelines_returns_unknown(self):
        with patch.object(_GITLAB_MOCK, "Gitlab") as MockGitlab:
            mock_gl, mock_project, mock_mr = _make_mock_mr()
            MockGitlab.return_value = mock_gl
            mock_mr.pipelines.list.return_value = []

            client = GitLabClient()
            result = client.get_pipeline_status(5)

            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["jobs"], [])


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

class TestCalculateLineCode(unittest.TestCase):
    def test_format(self):
        code = GitLabClient._calculate_line_code("src/app.py", 42)
        expected_sha = hashlib.sha1(b"src/app.py").hexdigest()
        self.assertEqual(code, f"{expected_sha}_42_42")

    def test_different_lines(self):
        code1 = GitLabClient._calculate_line_code("test.py", 1)
        code2 = GitLabClient._calculate_line_code("test.py", 2)
        self.assertNotEqual(code1, code2)


class TestDecodeFileContent(unittest.TestCase):
    def test_bytes_input(self):
        result = _decode_file_content(b"hello")
        self.assertEqual(result, "hello")

    def test_base64_string(self):
        """Real object with base64-encoded content string."""
        import base64
        encoded = base64.b64encode(b"decoded content").decode()
        # Use a simple class to hold content, not a MagicMock
        class _Obj:
            content = encoded
        result = _decode_file_content(_Obj())
        self.assertEqual(result, "decoded content")

    def test_str_input(self):
        """Plain string input — no attributes, just returned as-is."""
        result = _decode_file_content("plain text")
        self.assertEqual(result, "plain text")

    def test_none_input(self):
        result = _decode_file_content(None)
        self.assertIsNone(result)

    def test_mock_object_returns_none(self):
        """MagicMock objects without real content return None."""
        mock_obj = MagicMock()
        mock_obj.content = "some value"
        result = _decode_file_content(mock_obj)
        self.assertIsNone(result)

    def test_mock_with_binary_content(self):
        """MagicMock with real binary content — returns None (binary)."""
        mock_obj = MagicMock()
        mock_obj.content = b"\x89\x50\x4e\x47\x0d\x0a\x1a\x0a"
        result = _decode_file_content(mock_obj)
        self.assertIsNone(result)


class TestShouldRetryGitlabError(unittest.TestCase):
    def test_429_retried(self):
        exc = _GITLAB_MOCK.GitlabError()
        exc.response_code = 429
        self.assertTrue(_should_retry_gitlab_error(exc))

    def test_500_retried(self):
        exc = _GITLAB_MOCK.GitlabError()
        exc.response_code = 500
        self.assertTrue(_should_retry_gitlab_error(exc))

    def test_401_not_retried(self):
        exc = _GITLAB_MOCK.GitlabError()
        exc.response_code = 401
        self.assertFalse(_should_retry_gitlab_error(exc))

    def test_403_not_retried(self):
        exc = _GITLAB_MOCK.GitlabError()
        exc.response_code = 403
        self.assertFalse(_should_retry_gitlab_error(exc))

    def test_404_not_retried(self):
        exc = _GITLAB_MOCK.GitlabError()
        exc.response_code = 404
        self.assertFalse(_should_retry_gitlab_error(exc))

    def test_transient_message_retried(self):
        exc = _GITLAB_MOCK.GitlabError("Connection timed out")
        self.assertTrue(_should_retry_gitlab_error(exc))


class TestIsBinaryContent(unittest.TestCase):
    def test_text_not_binary(self):
        self.assertFalse(GitLabClient._is_binary_content("hello world"))

    def test_empty_is_binary(self):
        self.assertTrue(GitLabClient._is_binary_content(""))

    def test_none_is_binary(self):
        self.assertTrue(GitLabClient._is_binary_content(None))


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

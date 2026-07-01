import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

# Pre-populate sys.modules so the lazy `import gitlab as gitlab_sdk` inside
# GitLabClient.__init__ succeeds.
sys.modules.setdefault("gitlab", MagicMock())

# Load gitlab_client and tools modules directly from their file paths.
_GITLAB_MCP_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "sidecars", "gitlab-mcp"
)
sys.path.insert(0, _GITLAB_MCP_DIR)

# Make gitlab_client importable as a module so tools.py's relative import resolves.
import importlib.util

# Create a shared package namespace so relative imports inside tools.py work.
_PKG_NAME = "gitlab_mcp"
_pkg_module = type(sys)(_PKG_NAME)
sys.modules[_PKG_NAME] = _pkg_module

_gc_spec = importlib.util.spec_from_file_location(
    "gitlab_client", os.path.join(_GITLAB_MCP_DIR, "gitlab_client.py")
)
gitlab_client_module = importlib.util.module_from_spec(_gc_spec)
gitlab_client_module.__package__ = _PKG_NAME
sys.modules[f"{_PKG_NAME}.gitlab_client"] = gitlab_client_module
sys.modules["gitlab_client"] = gitlab_client_module
if _gc_spec.loader is not None:
    _gc_spec.loader.exec_module(gitlab_client_module)

from gitlab_client import DiffFile, MergeRequestDiff, ReviewComment  # noqa: E402

_tools_spec = importlib.util.spec_from_file_location(
    "tools", os.path.join(_GITLAB_MCP_DIR, "tools.py")
)
tools = importlib.util.module_from_spec(_tools_spec)
tools.__package__ = _PKG_NAME
sys.modules[f"{_PKG_NAME}.tools"] = tools
sys.modules["tools"] = tools
if _tools_spec.loader is not None:
    _tools_spec.loader.exec_module(tools)


class TestToolGetMergeRequest(unittest.TestCase):
    @patch("tools.GitLabClient")
    def test_success(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.get_merge_request.return_value = {
            "iid": 1,
            "title": "Test",
            "description": "",
            "source_branch": "feature",
            "target_branch": "main",
            "state": "opened",
            "sha": "abc123",
            "author": {"id": 1, "username": "u", "name": "U"},
            "web_url": "http://example.com/1",
        }

        result = tools.tool_get_merge_request({"mr_iid": "1"})
        parsed = json.loads(result)

        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["data"]["iid"], 1)
        self.assertEqual(parsed["data"]["title"], "Test")
        MockClient.return_value.get_merge_request.assert_called_once_with(1)

    @patch("tools.GitLabClient")
    def test_error(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.get_merge_request.side_effect = RuntimeError("boom")

        result = tools.tool_get_merge_request({"mr_iid": "1"})
        parsed = json.loads(result)

        self.assertEqual(parsed["status"], "error")
        self.assertIn("boom", parsed["message"])


class TestToolGetMergeRequestDiff(unittest.TestCase):
    @patch("tools.GitLabClient")
    def test_success(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.get_merge_request_diff.return_value = MergeRequestDiff(
            mr_iid=2,
            source_branch="feature",
            target_branch="main",
            source_sha="abc123",
            total_files=1,
            files=[
                DiffFile(old_path="a.txt", new_path="a.txt", old_line=1, new_line=1, diff="+added"),
            ],
        )

        result = tools.tool_get_merge_request_diff({"mr_iid": "2"})
        parsed = json.loads(result)

        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["data"]["mr_iid"], 2)
        self.assertEqual(parsed["data"]["total_files"], 1)
        self.assertEqual(len(parsed["data"]["files"]), 1)
        self.assertEqual(parsed["data"]["files"][0]["old_path"], "a.txt")
        self.assertEqual(parsed["data"]["files"][0]["diff"], "+added")


class TestToolListOldReviewerComments(unittest.TestCase):
    @patch("tools.GitLabClient")
    def test_success(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.list_old_reviewer_comments.return_value = [
            ReviewComment(note_id=1, body="comment 1", path=None, line=None),
            ReviewComment(note_id=2, body="comment 2", path="f.py", line=5),
        ]

        result = tools.tool_list_old_reviewer_comments({"mr_iid": "3"})
        parsed = json.loads(result)

        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["data"]["count"], 2)
        self.assertEqual(len(parsed["data"]["comments"]), 2)
        self.assertEqual(parsed["data"]["comments"][0]["note_id"], 1)
        self.assertEqual(parsed["data"]["comments"][1]["path"], "f.py")


class TestToolPostComment(unittest.TestCase):
    @patch("tools.GitLabClient")
    def test_success(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.post_comment.return_value = {
            "id": 50,
            "body": "body",
            "created_at": "2025-01-01T00:00:00Z",
        }

        result = tools.tool_post_comment({"mr_iid": "4", "body": "Hello"})
        parsed = json.loads(result)

        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["data"]["id"], 50)
        mock_client.post_comment.assert_called_once_with(4, "Hello")


class TestToolPostInlineComment(unittest.TestCase):
    @patch("tools.GitLabClient")
    def test_success(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.post_inline_comment.return_value = {
            "id": 51,
            "body": "body",
            "created_at": "2025-01-01T00:00:00Z",
        }

        result = tools.tool_post_inline_comment({
            "mr_iid": "4",
            "file_path": "src/main.py",
            "line": "10",
            "body": "Fix this",
        })
        parsed = json.loads(result)

        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["data"]["id"], 51)
        mock_client.post_inline_comment.assert_called_once_with(4, "src/main.py", 10, "Fix this")


class TestToolGetPipelineStatus(unittest.TestCase):
    @patch("tools.GitLabClient")
    def test_success(self, MockClient):
        mock_client = MockClient.return_value
        mock_client.get_pipeline_status.return_value = {
            "status": "success",
            "pipeline_id": 999,
            "jobs": [{"id": 1, "name": "build", "status": "success", "stage": "build"}],
        }

        result = tools.tool_get_pipeline_status({"mr_iid": "5"})
        parsed = json.loads(result)

        self.assertEqual(parsed["status"], "ok")
        self.assertEqual(parsed["data"]["status"], "success")
        self.assertEqual(parsed["data"]["pipeline_id"], 999)
        mock_client.get_pipeline_status.assert_called_once_with(5)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import Mock
from clients.http import ApiError
from migration.phase1_children import source_children


class ChildrenSourceTest(unittest.TestCase):
    def test_comment_and_attachment_capture(self):
        jira=Mock()
        jira.request.side_effect=[
          {"startAt":0,"total":1,"comments":[{"id":"99","body":"Test comment",
            "author":{"name":"john"},"created":"2025-01-01"}]},
          {"key":"PRJ-1","fields":{"attachment":[{"id":"88","filename":"test.png",
             "size":123,"content":"https://jira.example/secure/attachment/88/test.png"}]}}
        ]
        data=source_children(jira,"PRJ-1")
        self.assertEqual(len(data["comments"]),1)
        self.assertEqual(data["attachments"][0]["filename"],"test.png")

    def test_incomplete_page_blocks(self):
        jira=Mock()
        jira.request.return_value={"startAt":0,"total":2,"comments":[]}
        with self.assertRaises(ApiError):
            source_children(jira,"PRJ-1")

if __name__=="__main__":
    unittest.main()

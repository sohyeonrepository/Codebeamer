import unittest
from unittest.mock import Mock

from clients.http import ApiError
from import_case_set_children import multipart, import_comment, read_jira_attachment, import_attachment


class ChildWriteTests(unittest.TestCase):
    def test_comment_multipart_fields(self):
        body,headers=multipart({"comment":"hello","commentFormat":"PlainText"})
        self.assertIn(b'name="comment"',body)
        self.assertIn(b"hello",body)
        self.assertIn(b'name="commentFormat"',body)
        self.assertIn("multipart/form-data",headers["Content-Type"])

    def test_comment_post_then_get_verification(self):
        cb=Mock()
        cb.request.side_effect=[{"id":9},{"id":9,"comment":"text"}]
        self.assertEqual(import_comment(cb,100,{"body":"text"}),9)
        self.assertEqual(cb.request.call_args_list[0].args[:2],
                         ("POST","/v3/items/100/comments"))
        self.assertEqual(cb.request.call_args_list[1].args[:2],
                         ("GET","/v3/items/100/comments/9"))

    def test_attachment_multipart(self):
        body,headers=multipart(files=[("attachments","photo.png",b"123")])
        self.assertIn(b'name="attachments"',body)
        self.assertIn(b'filename="photo.png"',body)
        self.assertIn(b"123",body)

    def test_unsafe_filename_blocks(self):
        with self.assertRaises(ApiError):
            multipart(files=[("attachments","../bad.txt",b"123")])

    def test_unsupported_origin(self):
        jira=Mock()
        jira.base="https://jira.example"
        child={"size":2,"filename":"x.txt",
               "content":"https://evil.example/download/1"}
        with self.assertRaises(ApiError):
            read_jira_attachment(jira,child,100)

    def test_attachment_upload_and_checksum(self):
        cb=Mock()
        cb.request.side_effect=[[{"id":91}],b"123"]
        self.assertEqual(import_attachment(cb,10,{"filename":"x.txt"},b"123"),91)

    def test_attachment_failed_checksum(self):
        cb=Mock()
        cb.request.side_effect=[[{"id":91}],b"bad"]
        with self.assertRaises(ApiError):
            import_attachment(cb,10,{"filename":"x.txt"},b"123")

if __name__=="__main__":
    unittest.main()

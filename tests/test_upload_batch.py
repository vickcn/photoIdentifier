from io import BytesIO
import unittest

from fastapi import HTTPException, UploadFile

from src.upload_batch import read_upload_batch


def make_upload(name: str, content: bytes, content_type: str = "image/jpeg") -> UploadFile:
    return UploadFile(
        filename=name,
        file=BytesIO(content),
        headers={"content-type": content_type},
    )


class ReadUploadBatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_accepts_images_within_all_limits(self):
        uploads = [make_upload("one.jpg", b"abc"), make_upload("two.png", b"defg", "image/png")]

        images = await read_upload_batch(uploads, max_files=2, max_file_bytes=4, max_total_bytes=7)

        self.assertEqual(
            [(image.filename, image.content_type, image.content) for image in images],
            [("one.jpg", "image/jpeg", b"abc"), ("two.png", "image/png", b"defg")],
        )

    async def test_rejects_too_many_files_with_cloud_guidance(self):
        uploads = [make_upload("one.jpg", b"a"), make_upload("two.jpg", b"b")]

        with self.assertRaises(HTTPException) as context:
            await read_upload_batch(uploads, max_files=1, max_file_bytes=4, max_total_bytes=4)

        self.assertEqual(context.exception.status_code, 413)
        self.assertIn("Google 雲端", context.exception.detail)

    async def test_rejects_one_oversized_file(self):
        uploads = [make_upload("large.jpg", b"12345")]

        with self.assertRaises(HTTPException) as context:
            await read_upload_batch(uploads, max_files=1, max_file_bytes=4, max_total_bytes=10)

        self.assertEqual(context.exception.status_code, 413)
        self.assertIn("large.jpg", context.exception.detail)

    async def test_rejects_oversized_combined_upload(self):
        uploads = [make_upload("one.jpg", b"123"), make_upload("two.jpg", b"456")]

        with self.assertRaises(HTTPException) as context:
            await read_upload_batch(uploads, max_files=2, max_file_bytes=4, max_total_bytes=5)

        self.assertEqual(context.exception.status_code, 413)
        self.assertIn("總大小", context.exception.detail)

    async def test_rejects_non_image_files(self):
        uploads = [make_upload("notes.txt", b"hello", "text/plain")]

        with self.assertRaises(HTTPException) as context:
            await read_upload_batch(uploads, max_files=1, max_file_bytes=10, max_total_bytes=10)

        self.assertEqual(context.exception.status_code, 400)
        self.assertIn("notes.txt", context.exception.detail)

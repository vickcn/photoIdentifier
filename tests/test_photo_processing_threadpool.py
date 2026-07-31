import unittest
from unittest.mock import patch

import photoIdentifier
from src.google_usage import PhotoAnalysisResult


class PhotoProcessingThreadpoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_and_visualize_photo_offloads_blocking_image_work(self):
        calls = []

        async def fake_to_thread(func, *args, **kwargs):
            calls.append(func.__name__)
            return func(*args, **kwargs)

        async def fake_analyze(*args, **kwargs):
            self.assertEqual(kwargs["local_face_bboxes"], [[1, 2, 3, 4]])
            return PhotoAnalysisResult(
                has_face=True,
                face_bboxes=[[1, 2, 3, 4]],
                face_confidences=[0.9],
                has_brand_strap=False,
                strap_bboxes=[],
                strap_confidences=[],
                strap_color=None,
                is_safe_for_public=True,
                moderation_status="public",
                moderation_reason="test",
            )

        def fake_resize(image_bytes):
            return b"processed"

        async def fake_detect(image_bytes, **kwargs):
            return [[1, 2, 3, 4]]

        def fake_draw(**kwargs):
            return b"drawn"

        with (
            patch.object(photoIdentifier.asyncio, "to_thread", fake_to_thread),
            patch.object(photoIdentifier, "resize_image_if_needed", fake_resize),
            patch.object(photoIdentifier, "detect_normalized_bboxes", fake_detect),
            patch.object(photoIdentifier, "draw_bboxes_on_image", fake_draw),
            patch.object(photoIdentifier, "analyze_brand_strap_image", fake_analyze),
        ):
            result, drawn_bytes = await photoIdentifier.process_and_visualize_photo(b"original")

        self.assertTrue(result.has_face)
        self.assertEqual(drawn_bytes, b"drawn")
        self.assertEqual(
            calls,
            [
                "fake_resize",
                "fake_draw",
            ],
        )

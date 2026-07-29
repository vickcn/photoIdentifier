import unittest

import numpy as np

from src.face.models import FaceRecord
from src.face.workspace import build_face_clusters, classify_batch_results


class FaceWorkspaceTests(unittest.TestCase):
    def test_builds_one_cluster_from_faces_across_photos(self):
        records = [
            FaceRecord("one.jpg", 0, [1, 2, 30, 40], 0.98, 3),
            FaceRecord("two.jpg", 1, [5, 6, 35, 46], 0.96, 3),
        ]
        images = {
            "one.jpg": {"file_name": "one.jpg", "original_image_b64": "b25l"},
            "two.jpg": {"file_name": "two.jpg", "original_image_b64": "dHdv"},
        }

        clusters = build_face_clusters(records, images)

        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].cluster_id, "cluster_001")
        self.assertEqual(clusters[0].display_name, "人物 001")
        self.assertEqual(clusters[0].face_count, 2)
        self.assertEqual(clusters[0].photo_count, 2)
        self.assertEqual([item.file_name for item in clusters[0].evidence_photos], ["one.jpg", "two.jpg"])

    def test_keeps_dbscan_noise_faces_as_separate_people(self):
        records = [
            FaceRecord("one.jpg", 0, [1, 2, 30, 40], 0.91, -1),
            FaceRecord("two.jpg", 0, [5, 6, 35, 46], 0.89, -1),
        ]
        images = {
            "one.jpg": {"file_name": "one.jpg"},
            "two.jpg": {"file_name": "two.jpg"},
        }

        clusters = build_face_clusters(records, images)

        self.assertEqual(len(clusters), 2)
        self.assertEqual([cluster.face_count for cluster in clusters], [1, 1])
        self.assertEqual([cluster.cluster_id for cluster in clusters], ["cluster_001", "cluster_002"])

    def test_classifies_similar_faces_across_uploaded_photos_without_files(self):
        results = [
            {"file_name": "one.jpg", "original_image_b64": "b25l"},
            {"file_name": "two.jpg", "original_image_b64": "dHdv"},
            {"file_name": "three.jpg", "original_image_b64": "dGhyZWU="},
        ]
        features = {
            b"one": [([1, 2, 30, 40], 0.98, np.array([0.0, 0.0], dtype=np.float32))],
            b"two": [([5, 6, 35, 46], 0.96, np.array([0.04, 0.03], dtype=np.float32))],
            b"three": [([7, 8, 37, 48], 0.95, np.array([1.0, 1.0], dtype=np.float32))],
        }

        clusters = classify_batch_results(
            results,
            detector=lambda image_bytes: features[image_bytes],
            eps=0.1,
            min_samples=2,
        )

        self.assertEqual([cluster.face_count for cluster in clusters], [2, 1])
        self.assertEqual(clusters[0].photo_count, 2)


if __name__ == "__main__":
    unittest.main()

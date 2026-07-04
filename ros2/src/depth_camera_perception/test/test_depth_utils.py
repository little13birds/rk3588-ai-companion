import unittest

import numpy as np

from depth_camera_perception.depth_utils import (
    bbox_center_roi,
    estimate_bbox_depth_m,
    normalize_depth_to_meters,
)


class DepthUtilsTest(unittest.TestCase):
    def test_normalize_uint16_millimeters(self):
        depth = np.array([[0, 800, 1200]], dtype=np.uint16)
        meters = normalize_depth_to_meters(depth)
        np.testing.assert_allclose(meters, [[0.0, 0.8, 1.2]])

    def test_normalize_float32_meters(self):
        depth = np.array([[0.0, 0.8, 1.2]], dtype=np.float32)
        meters = normalize_depth_to_meters(depth)
        np.testing.assert_allclose(meters, [[0.0, 0.8, 1.2]])

    def test_bbox_center_roi_clamps_to_image(self):
        roi = bbox_center_roi((-10, -10, 30, 30), width=20, height=20, fraction=0.5)
        self.assertEqual(roi, (5, 5, 15, 15))

    def test_estimate_bbox_depth_uses_valid_median(self):
        depth = np.zeros((20, 20), dtype=np.uint16)
        depth[8:12, 8:12] = np.array(
            [
                [790, 800, 810, 0],
                [805, 795, 5000, 800],
                [800, 810, 790, 805],
                [0, 805, 795, 800],
            ],
            dtype=np.uint16,
        )
        result = estimate_bbox_depth_m(depth, (4, 4, 16, 16), fraction=0.35)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result, 0.8, places=3)

    def test_estimate_bbox_depth_returns_none_without_valid_depth(self):
        depth = np.zeros((20, 20), dtype=np.uint16)
        self.assertIsNone(estimate_bbox_depth_m(depth, (4, 4, 16, 16)))



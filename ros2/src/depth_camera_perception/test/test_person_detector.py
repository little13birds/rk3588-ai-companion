import unittest

import numpy as np

from depth_camera_perception.person_detector import (
    Detection,
    PoseKeypoint,
    _decode_yolov8_output,
    _letterbox_bgr,
    _prepare_rknn_input,
    _scale_box_from_letterbox,
)


class PersonDetectorPostprocessTest(unittest.TestCase):
    def test_prepare_rknn_input_uses_nhwc_uint8_rgb(self):
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        image[:, :] = (10, 20, 30)  # BGR

        tensor, meta = _prepare_rknn_input(image, input_size=640)

        self.assertEqual(tensor.shape, (1, 640, 640, 3))
        self.assertEqual(tensor.dtype, np.uint8)
        self.assertEqual(meta.pad_y, 80)
        self.assertEqual(tensor[0, 80, 0].tolist(), [30, 20, 10])

    def test_letterbox_maps_model_box_back_to_original_image(self):
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        letterboxed, meta = _letterbox_bgr(image, input_size=640)

        self.assertEqual(letterboxed.shape, (640, 640, 3))
        self.assertAlmostEqual(meta.scale, 1.0)
        self.assertEqual(meta.pad_x, 0)
        self.assertEqual(meta.pad_y, 80)

        box = _scale_box_from_letterbox((100.0, 180.0, 300.0, 380.0), meta)
        self.assertEqual(box, (100.0, 100.0, 300.0, 300.0))

    def test_decode_yolov8_output_filters_person_and_applies_nms(self):
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        _, meta = _letterbox_bgr(image, input_size=640)
        output = np.zeros((1, 84, 8400), dtype=np.float32)

        # Person box in model-space xywh. Original image box should be (150, 100, 250, 300).
        output[0, 0:4, 0] = [200.0, 280.0, 100.0, 200.0]
        output[0, 4, 0] = 0.90

        # Overlapping lower-confidence person should be removed by NMS.
        output[0, 0:4, 1] = [205.0, 285.0, 100.0, 200.0]
        output[0, 4, 1] = 0.80

        # Non-person high-confidence class should be ignored.
        output[0, 0:4, 2] = [400.0, 300.0, 80.0, 160.0]
        output[0, 20, 2] = 0.95

        # Low-confidence person should be ignored.
        output[0, 0:4, 3] = [100.0, 200.0, 50.0, 100.0]
        output[0, 4, 3] = 0.20

        detections = _decode_yolov8_output(output, meta, confidence=0.4, nms_threshold=0.5)

        self.assertEqual(len(detections), 1)
        detection = detections[0]
        self.assertEqual(detection.class_id, 0)
        self.assertEqual(detection.label, "person")
        self.assertAlmostEqual(detection.confidence, 0.90, places=3)
        self.assertEqual(detection.bbox, (150.0, 100.0, 250.0, 300.0))


if __name__ == "__main__":
    unittest.main()


def test_detection_with_distance_preserves_pose_metadata():
    detection = Detection(
        bbox=(1.0, 2.0, 3.0, 4.0),
        confidence=0.9,
        class_id=0,
        label="person",
        track_id=5,
        keypoints=(PoseKeypoint(1.0, 2.0, 0.8),),
        stable_score=0.7,
        age=3,
        missed=0,
    )

    with_distance = detection.with_distance(1.23)

    assert with_distance.distance_m == 1.23
    assert with_distance.track_id == 5
    assert with_distance.keypoints == detection.keypoints
    assert with_distance.stable_score == 0.7

"""Tests for VLM-bound camera JPEG preparation.

Run from ~/cloud-model with: python3 -m vision.test_camera
"""
import base64
import threading
import unittest
from unittest import mock

import cv2
import numpy as np

from vision import camera


def _make_jpeg(width: int, height: int) -> bytes:
    rng = np.random.default_rng(20260613)
    image = rng.integers(0, 256, (height, width, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(
        ".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 95]
    )
    assert ok
    return encoded.tobytes()


def _decode(jpg: bytes):
    return cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)


class PrepareVlmJpegTests(unittest.TestCase):
    def test_1280x720_resizes_to_640x360_and_reduces_bytes(self):
        original = _make_jpeg(1280, 720)

        prepared = camera.prepare_vlm_jpeg(original)
        image = _decode(prepared)

        self.assertIsNotNone(image)
        self.assertEqual(image.shape[:2], (360, 640))
        self.assertLess(len(prepared), len(original))

    def test_320x240_is_not_upscaled(self):
        original = _make_jpeg(320, 240)

        prepared = camera.prepare_vlm_jpeg(original)
        image = _decode(prepared)

        self.assertIsNotNone(image)
        self.assertEqual(image.shape[:2], (240, 320))

    def test_invalid_jpeg_returns_empty_bytes(self):
        self.assertEqual(camera.prepare_vlm_jpeg(b"not a jpeg"), b"")


class CaptureTests(unittest.TestCase):
    def tearDown(self):
        camera.set_snapshot_provider(None)

    @mock.patch("vision.camera.agent_client.get_frame")
    def test_wait_ready_is_forwarded_and_result_is_640x360(self, get_frame):
        get_frame.return_value = _make_jpeg(1280, 720)

        result = camera.capture(wait_ready=True)

        get_frame.assert_called_once_with(wait_ready=True)
        image = _decode(base64.b64decode(result))
        self.assertIsNotNone(image)
        self.assertEqual(image.shape[:2], (360, 640))

    @mock.patch("vision.camera.agent_client.get_frame")
    def test_normal_capture_uses_platform_snapshot_provider(self, get_frame):
        platform_jpg = _make_jpeg(320, 240)
        camera.set_snapshot_provider(lambda: platform_jpg)

        raw, result = camera.capture_raw_and_vlm(wait_ready=False)

        get_frame.assert_not_called()
        self.assertEqual(raw, platform_jpg)
        image = _decode(base64.b64decode(result))
        self.assertIsNotNone(image)
        self.assertEqual(image.shape[:2], (240, 320))

    @mock.patch("vision.camera.agent_client.get_frame")
    def test_normal_capture_waits_briefly_for_platform_snapshot_provider(self, get_frame):
        platform_jpg = _make_jpeg(320, 240)
        calls = [None, platform_jpg]

        def _provider():
            return calls.pop(0)

        camera.set_snapshot_provider(_provider)

        with mock.patch("vision.camera.time.sleep", return_value=None):
            raw, result = camera.capture_raw_and_vlm(wait_ready=False)

        get_frame.assert_not_called()
        self.assertEqual(raw, platform_jpg)
        self.assertNotEqual(result, "")

    @mock.patch("vision.camera.agent_client.get_frame")
    def test_reading_capture_ignores_platform_snapshot_provider(self, get_frame):
        camera.set_snapshot_provider(lambda: _make_jpeg(320, 240))
        get_frame.return_value = _make_jpeg(1280, 720)

        raw, result = camera.capture_raw_and_vlm(wait_ready=True)

        get_frame.assert_called_once_with(wait_ready=True)
        self.assertNotEqual(raw, b"")
        image = _decode(base64.b64decode(result))
        self.assertIsNotNone(image)
        self.assertEqual(image.shape[:2], (360, 640))

    @mock.patch("vision.camera.agent_client.get_frame", return_value=None)
    def test_missing_frame_returns_empty_string(self, get_frame):
        self.assertEqual(camera.capture(), "")

    @mock.patch("vision.camera.agent_client.get_frame")
    def test_wait_ready_cancelled_before_request_skips_frame_fetch(self, get_frame):
        cancel_event = threading.Event()
        cancel_event.set()

        raw, result = camera.capture_raw_and_vlm(
            wait_ready=True,
            cancel_event=cancel_event,
        )

        self.assertEqual(raw, b"")
        self.assertEqual(result, "")
        get_frame.assert_not_called()

    @mock.patch("vision.camera.agent_client.get_frame")
    def test_wait_ready_with_cancel_event_polls_short_timeout(self, get_frame):
        cancel_event = threading.Event()

        def _cancel_after_first_poll(**_kwargs):
            cancel_event.set()
            return None

        get_frame.side_effect = _cancel_after_first_poll

        raw, result = camera.capture_raw_and_vlm(
            wait_ready=True,
            cancel_event=cancel_event,
            wait_timeout=5.0,
            poll_timeout=0.2,
        )

        self.assertEqual(raw, b"")
        self.assertEqual(result, "")
        get_frame.assert_called_once_with(wait_ready=True, timeout=0.2)

    @mock.patch("vision.camera.prepare_vlm_jpeg", return_value=b"")
    @mock.patch("vision.camera.agent_client.get_frame", return_value=b"jpeg")
    def test_prepare_failure_returns_empty_string(self, get_frame, prepare):
        self.assertEqual(camera.capture(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
